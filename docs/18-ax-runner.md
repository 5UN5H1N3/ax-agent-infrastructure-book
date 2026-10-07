# Runner: PID 1 как платформенный контракт, а не просто entrypoint

**Статус:** `CURRENT MAIN VERIFIED` · `PROCESS LIFECYCLE` · `READINESS SEMANTICS`

В главе 17 мы разделили Task, Workspace и Model и увидели, что декларация сама по себе ничего не исполняет. Между manifest и пользовательским агентом нужен компонент, который превратит данные control plane в работающий процесс внутри sandbox. В AX эту роль выполняет runner.

Runner легко принять за тонкую shell-обёртку: прочитать команду, запустить её и ждать. Но в AX он одновременно является PID 1 контейнера, подготавливает Workspace, держит metadata surface, сообщает readiness, включает debug services и завершает process tree при suspend. Ошибка в этой точке искажает состояние сразу для трёх наблюдателей: Substrate, AX и самого agent harness.

Поэтому runner полезно рассматривать не как вспомогательный binary, а как платформенный адаптер. Сверху он получает декларативные контракты Task и Workspace, снизу работает с Unix processes и durable filesystem, а наружу выдаёт минимальный протокол здоровья и управления.

## Что читатель должен унести из главы

- зачем платформе отдельный runner, если у container image уже есть ENTRYPOINT;
- почему AX всегда запускает `/usr/local/bin/ax-task-runner`, а `spec.command` становится child process;
- какие обязанности возникают у PID 1 и какие из них default runner не решает как полноценная init-system;
- чем различаются container health, workspace readiness и liveness самого agent process;
- почему успешный выход child не завершает Task и не попадает в AX status;
- как runner обрабатывает `SIGTERM`, process group и 10-секундный grace period;
- что именно переживает suspend/resume, а что должно быть восстановлено harness;
- когда достаточно расширить default image, а когда нужен embedded или custom runner;
- как проверить runner до запуска production workload.

![Runner contract: граница между AX, sandbox и agent process](.gitbook/assets/diagrams/18-16.png)
*Runner contract: граница между AX, sandbox и agent process*

## Зачем runner существует отдельно от harness

Agent harness отвечает за model loop, tools, memory, budgets и прикладную остановку. Runner решает другой класс задач: получить launch configuration от платформы, подготовить среду, запустить harness как процесс и удерживать management surface sandbox. Смешивать эти роли можно, но тогда обновление agent logic начинает менять платформенный lifecycle, а изменение probes или signal handling требует пересборки всего приложения.

Отдельный runner даёт AX устойчивый внутренний protocol. Control plane не обязан знать, написан агент на Python, Go или TypeScript, является ли он ReAct loop, supervisor или обычным shell script. Для платформы важны фиксированный executable, port 80, readiness и корректное завершение.

| Слой | Главная обязанность | Чего он не должен угадывать |
| --- | --- | --- |
| AX/Substrate | Создать sandbox, передать specs, volume и probes | Как работает agent loop и что означает бизнес-успех |
| Runner | Материализовать Workspace, запустить и остановить process tree, держать management API | Правильность решения задачи агентом |
| Harness | Выполнять agent logic, сохранять checkpoints и публиковать application state | Как control plane создаёт Actor и восстанавливает filesystem |

## Launch envelope: что AX передаёт в container

В current main reconciler строит ActorTemplate так, чтобы container всегда стартовал с командой `/usr/local/bin/ax-task-runner`. Пользовательский `spec.command` не становится OCI command. Он сериализуется внутри `AX_TASK_YAML`, а runner позднее запускает его через `os/exec`.

| Вход | Значение | Практическое следствие |
| --- | --- | --- |
| Image | `spec.image` или default runner image | Custom image обязан содержать совместимый executable по фиксированному пути |
| Container command | `/usr/local/bin/ax-task-runner` | ENTRYPOINT исходного image не определяет lifecycle AX |
| `AX_TASK_YAML` | Task без status | Runner получает launch spec, но не authoritative control-plane state |
| `AX_WORKSPACES_YAML` | Найденные Workspace resources в binding order | Отсутствующий resource может не попасть в stream |
| Task env | Literal `spec.env` плюс platform-provided values | Переменные доступны и runner, и child process |
| Volume | Durable directory at `/workspace` | Files переживают suspend/resume; process tree не переживает |
| Ready probe | `GET /readyz` on port 80 | Custom runner обязан занять этот port и реализовать protocol |

> **Главный compatibility test.** Custom image считается AX-compatible не потому, что внутри есть ваш agent binary. Она совместима, если platform entrypoint существует и соблюдает runner contract.

## Boot sequence по шагам

Порядок операций важен для диагностики. Одни failures происходят до появления health endpoint, другие оставляют container живым, но неготовым, третьи вообще не отражаются в AX status.

1. **Parse configuration.** Binary читает Task из `AX_TASK_YAML`, Workspaces из multi-document `AX_WORKSPACES_YAML` и подписывается на `SIGTERM`/`Interrupt`. Invalid YAML является fatal error: runner завершается до старта server.
2. **Resolve mounts.** Bindings сопоставляются с Workspace по name. Если resource не передан, binding всё равно превращается в mount с пустым Workspace.
3. **Start metadata server.** HTTP server начинает слушать до Workspace setup. С этого момента `/healthz` отвечает 200, а `/readyz` ещё 503.
4. **Prepare Workspaces.** Setup выполняется последовательно в declaration order. При отсутствии bindings создаётся default `/workspace`.
5. **Mark ready.** Только если каждый вызов `SetupWorkspace` вернул nil, metadata server переключает workspace readiness в true.
6. **Start command.** Runner выбирает первый Workspace как working directory, собирает environment и запускает `spec.command` в отдельной process group.
7. **Supervise until stop.** Runner ждёт либо завершения direct child, либо отмены context. После выхода child он продолжает обслуживать server до внешнего stop/suspend.

Неочевидный момент: ошибка Workspace setup выставляет внутренний `ready=false`, но current `runner.Run` всё равно переходит к запуску command. Это может быть полезно для debug или self-repair, но application не должна считать наличие процесса доказательством готовой среды.

```
parse specs
  -> start metadata server       health=200, ready=503
  -> setup workspaces in order
       -> all returned nil       ready=200
       -> any returned error     ready stays 503
  -> start child command         happens in both cases
  -> child exits                 runner stays alive
  -> SIGTERM to runner           terminate process group, then exit
```

## PID 1: особая роль, но не магия

В Linux PID 1 внутри PID namespace определяет lifetime container: пока этот process жив, container считается работающим. Он получает stop signal от runtime и становится родителем для orphaned descendants. Поэтому обычное приложение, случайно поставленное PID 1, часто неправильно обрабатывает signals или накапливает zombie processes.

Default AX runner решает основную часть lifecycle для одного foreground command. Он подписывается на `SIGTERM`, запускает direct child через `exec.Cmd`, помещает его в отдельную process group и обязательно вызывает `Wait` для этого child. Но в inspected code нет общего `SIGCHLD`/`wait4` loop или subreaper behavior для произвольно осиротевших grandchildren.

Отсюда инженерное ограничение: agent command должен оставаться foreground supervisor своего дерева. Если библиотека делает double-fork, вызывает `setsid` и отдаёт процессы PID 1, default runner может не вести их как полноценная init-system. Для сложного service tree используйте собственный supervisor внутри command либо custom runner с явным reaping.

| Обязанность PID 1 | Default runner | Ограничение |
| --- | --- | --- |
| Получить stop signal | Да, `signal.NotifyContext` | Обрабатываются Interrupt и SIGTERM |
| Остановить process tree | Signal всей исходной process group | Process, ушедший в другую session/group, может избежать сигнала |
| Дождаться direct child | Да, через `cmd.Wait()` | Это не универсальный reaper всех orphaned descendants |
| Держать container после child exit | Да | Container alive больше не означает workload alive |
| Сообщить exit code control plane | Только log/callback | AX status его сейчас не получает |

## Три сигнала здоровья, которые нельзя смешивать

Слово ready звучит однозначно, но в этой системе у него несколько уровней. Если dashboard показывает только один зелёный индикатор, оператор не понимает, какой именно факт подтверждён.

| Сигнал | Что он доказывает | Чего он не доказывает |
| --- | --- | --- |
| `/healthz = 200` | Metadata server жив и принимает HTTP | Workspace готов, child запущен или agent полезен |
| `/readyz = 200` | Все вызовы Workspace setup завершились без возвращённой ошибки | Goal postconditions, toolchain и business dependencies реально работают |
| Task phase `Running` | Actor resumed и получил Worker | WorkspaceReady или живой child process |
| `WorkspaceReady=True` | AX однажды увидел runner readiness | Текущий child после resume выполняется успешно |
| Application heartbeat | Harness выполняет ожидаемый loop/checkpoint | Полная готовность внешних tools без отдельных probes |

AX опрашивает `/readyz` примерно каждые 500 ms, но только в ограниченном окне, по умолчанию до 15 секунд. Если setup дольше, Task остаётся `Running` с `WorkspaceReady=False`; следующий lifecycle RPC может снова запустить reconciliation. После того как условие стало True, current reconciler сохраняет его через suspend/resume и больше не опрашивает runner для этого condition.

С другой стороны, Agent Substrate использует container readiness probe на том же endpoint. Значит, HTTP readiness остаётся полезной runtime guard, даже если AX status уже содержит старое True. Но ни один из этих механизмов не наблюдает child process напрямую.

## Metadata server и debug surface

Runner поднимает один listener. Обычные HTTP/1.1 requests обслуживают health, readiness и metadata. Когда `spec.debug=true`, незашифрованный HTTP/2 traffic с `Content-Type: application/grpc` передаётся guest gRPC server через h2c.

| Endpoint/service | Назначение | Risk |
| --- | --- | --- |
| `/metadata/v1alpha1/ax/task` | Task launch configuration как YAML | Включает literal env из spec; не считайте endpoint public |
| `/metadata/v1alpha1/ax/workspaces` | Найденные Workspace resources в binding order | Это configuration, а не доказательство materialization |
| Guest process service | Запуск, inspection, output и kill процессов | Arbitrary process execution внутри sandbox |
| Guest filesystem service | Streaming read/write файлов | Доступ к durable Workspace и его artifacts |

`AX_METADATA_URL` указывает child на loopback address runner, обычно `http://127.0.0.1:80`. Это удобный self-introspection contract без SDK. Но metadata не должна становиться скрытым secret delivery channel: Task env возвращается почти в исходном виде.

Debug выключен по умолчанию правильно. Его включение меняет не удобство логирования, а attack surface sandbox. Production policy должна ограничивать, кто может создать Task с debug, кто может вызвать `ax ssh` и как такой доступ попадает в audit log.

## Child process: что означает exit

Runner запускает command с inherited stdin/stdout/stderr и environment, где есть runner env, `AX_METADATA_URL` и `spec.env`. Working directory берётся из первого Workspace binding. Это делает порядок bindings частью runtime contract, а не косметикой manifest.

После `cmd.Start()` отдельная goroutine вызывает `cmd.Wait()`. Exit code записывается в log и передаётся optional `OnCommandExit` callback, если runner package встроен в custom binary. Default `ax-task-runner` callback не задаёт. Control plane не читает этот результат и не переводит Task в Completed или Failed.

Для long-running agent service такое поведение удобно: после crash можно зайти через debug surface, прочитать files и решить, перезапускать ли workload. Для one-shot automation оно опасно: exit 0 выглядит почти так же, как exit 2, если оператор смотрит только на Task phase.

| Workload | Рекомендуемый completion contract |
| --- | --- |
| Long-running agent | Heartbeat + last successful loop + restart policy вне одного Task status |
| One-shot analysis | Atomic result file/status record и explicit completion event до выхода |
| Code-changing agent | Commit/PR identity, test result и durable operation ledger |
| Infrastructure action | Approved operation id, before/after evidence и verification result |

> **Task не равен Job.** Пока AX не отражает child exit в resource status, терминальный успех должен жить в application protocol, а не выводиться из container health.

## SIGTERM, process group и 10 секунд на остановку

Когда sandbox останавливается или suspend инициирует shutdown, runtime доставляет `SIGTERM` PID 1. Runner отменяет context и отправляет `SIGTERM` отрицательному PID child, то есть всей process group. Затем ждёт до 10 секунд. Если direct child не завершился, runner посылает group `SIGKILL` и всё равно ждёт `cmd.Wait()`.

```
Substrate stop/suspend
  -> SIGTERM to runner (PID 1)
  -> SIGTERM to child process group
  -> wait up to 10 seconds
       -> child exits: record result
       -> timeout: SIGKILL process group, then wait
  -> stop metadata server
  -> runner exits
  -> container stops
```

Эти 10 секунд жёстко заданы в current package. Task manifest не содержит termination grace setting. Harness должен либо укладываться в этот budget, либо заранее continuously сохранять state, а не пытаться записать всё при shutdown.

Process group лучше сигнала одному PID: shell, tool process и их обычные descendants получают одинаковый request на завершение. Но это не security boundary. Процесс может создать новую session/group, зависнуть в uninterruptible kernel wait или уже выполнить внешний side effect. `SIGKILL` останавливает вычисление, но не откатывает Git push, ticket update или network change.

## Что переживает suspend/resume

С точки зрения runner resume является новым boot. Substrate восстанавливает durable Workspace files, затем запускает новый container и новый PID 1. Старые goroutines, sockets, in-memory model context и child processes не продолжаются.

| State | После resume | Кто отвечает |
| --- | --- | --- |
| Files под durable Workspace | Должны быть восстановлены | Substrate snapshot/storage |
| Workspace initialization marker | Позволяет пропустить повторный setup | Runner/setup logic |
| Process memory и goroutines | Создаются заново | Harness rehydration |
| Open sockets и sessions | Потеряны | Client reconnect logic |
| Tool operation in progress | Неизвестен без operation ledger | Application protocol |
| AX `WorkspaceReady` condition | Может остаться True | Control-plane stored status |

Marker защищает agent-modified checkout от повторной materialization, но не восстанавливает semantic progress. Надёжный harness при boot читает checkpoint, сверяет незавершённые operation IDs с внешними системами и только потом продолжает loop.

## Как выбирать уровень customization

Документация AX предлагает три уровня. Правильный выбор определяется не языком agent framework, а тем, насколько вы хотите менять lifecycle contract.

| Вариант | Когда выбирать | Что вы поддерживаете |
| --- | --- | --- |
| Extend default image | Нужны дополнительные compilers, CLI, SDK или agent binary | Toolchain и supply chain image; runner остаётся upstream |
| Embed Go `runner` package | Нужны hooks, например artifact upload через `OnCommandExit` | Свой binary и version compatibility package |
| Custom runner | Нужен другой supervisor, workspace layout, init/reaping или protocol extension | Весь platform contract: specs, server, probes, signals, debug и tests |

Для большинства команд первый вариант дешевле и безопаснее. Если проблема только в отсутствии Node.js или вашего CLI, не переписывайте PID 1. Embedded package оправдан, когда требуется небольшая интеграция на границе exit/setup. Полный runner нужен лишь тогда, когда стандартный lifecycle принципиально не подходит.

## Локальная и end-to-end проверка runner

Default binary умеет читать specs из files, поэтому basic contract проверяется без кластера:

```
ax-task-runner \
  --task-file task.yaml \
  --workspace-file code.yaml \
  --port 8080

curl -i http://127.0.0.1:8080/healthz
curl -i http://127.0.0.1:8080/readyz
curl -s http://127.0.0.1:8080/metadata/v1alpha1/ax/task
```

Но local test не проверяет Substrate signal delivery, restored volume и routing. Минимальная acceptance matrix должна включать:

| Сценарий | Ожидаемый результат |
| --- | --- |
| Slow Workspace setup | health=200, ready=503, command behavior соответствует вашему policy |
| Command exit 0/не 0 | Runner остаётся жив, exit виден в application telemetry |
| Child с descendants | SIGTERM доходит всей foreground process group |
| Child игнорирует SIGTERM | После 10 секунд применяется SIGKILL |
| Suspend during tool call | После resume operation ledger предотвращает duplicate effect |
| Resume from changed Workspace | Проверена marker/version strategy, нет скрытой повторной initialization |
| Debug off/on | `ax ssh` запрещён/разрешён ожидаемо и audit-visible |

## Антипаттерны

- **Считать runner обычным ENTRYPOINT.** Он реализует protocol с control plane и Substrate.
- **Считать Running доказательством живого агента.** Runner может жить после exit child.
- **Перезапускать container из-за каждого exit.** Сначала решите, является workload Job, service или inspectable session.
- **Писать весь state только на SIGTERM.** Hardcoded grace period равен 10 секундам.
- **Daemonize agent process.** Foreground supervisor лучше согласуется с process-group shutdown и observability.
- **Считать readyz application probe.** Он отражает Workspace setup, а не model/tools/business readiness.
- **Включать debug постоянно.** Guest services дают process и filesystem access.
- **Переписывать runner ради пакета или CLI.** Чаще достаточно расширить default image.

## Production checklist runner

- Custom image содержит executable `/usr/local/bin/ax-task-runner`.
- Runner contract и image закреплены digest, а не mutable tag.
- Invalid specs, bind failure port 80 и command start failure наблюдаемы отдельно.
- Health, Workspace readiness и application heartbeat имеют разные metrics.
- Command работает foreground и не теряет descendants за новой process session.
- Exit code и completion result сохраняются вне runner logs.
- Checkpoint interval короче ожидаемого shutdown budget.
- Tool side effects используют idempotency key и durable operation ledger.
- Resume path протестирован как новый process с restored filesystem.

## Итог

Runner делает AX Task управляемой sandbox session: принимает specs, подготавливает filesystem, запускает command, отвечает на probes и корректно завершает process group. Его позиция PID 1 нужна не для «агентной магии», а потому, что именно этот process связывает lifetime container с lifecycle workload.

Но default runner сознательно не превращает Task в batch Job или полноценную init-system. Он не публикует child exit в AX status, не доказывает application readiness и не восстанавливает in-memory progress. Эти обязанности остаются у harness и platform design вокруг него.

В следующей главе мы проследим полный lifecycle Task: от `ax apply` и Redis record до Actor resume, runner readiness, suspend и delete. После этой главы уже можно будет чётко отделить control-plane state transition от того, что реально происходит с процессом внутри sandbox.

### Источники и дальнейшее чтение

- [AX runner contract](https://github.com/google/ax/blob/ac2332829f22360ff97b0ba34d94dd0dd782f17e/docs/runner.md)
- [Default runner implementation](https://github.com/google/ax/blob/ac2332829f22360ff97b0ba34d94dd0dd782f17e/runner/runner.go)
- [ax-task-runner entrypoint and signal context](https://github.com/google/ax/blob/ac2332829f22360ff97b0ba34d94dd0dd782f17e/cmd/ax-task-runner/main.go)
- [Metadata, readiness and guest service server](https://github.com/google/ax/blob/ac2332829f22360ff97b0ba34d94dd0dd782f17e/internal/metadata/server.go)
- [Task reconciliation and WorkspaceReady polling](https://github.com/google/ax/blob/ac2332829f22360ff97b0ba34d94dd0dd782f17e/internal/controller/reconciler.go)
- [Inside the AX sandbox](https://github.com/google/ax/blob/ac2332829f22360ff97b0ba34d94dd0dd782f17e/docs/sandbox.md)
- [Linux PID namespaces and PID 1 behavior](https://man7.org/linux/man-pages/man7/pid_namespaces.7.html)
- [Linux wait and zombie process semantics](https://man7.org/linux/man-pages/man2/waitpid.2.html)
