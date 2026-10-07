# Task lifecycle в AX: synchronous reconciliation, состояния и recovery

![AX Task current main: synchronous lifecycle под per-resource lock](.gitbook/assets/diagrams/19-17.png)  
*AX Task current main: synchronous lifecycle под per-resource lock*

В предыдущей главе мы разобрали жизнь процесса внутри sandbox. Теперь поднимемся на уровень выше и проследим, что происходит с самим `Task`: от первого RPC до Actor, suspend, повторного запуска и удаления.

Здесь особенно опасна устаревшая mental model. В старых версиях и ранних материалах AX фигурировали Redis Streams и отдельный `ax-controller`. В current main на commit `ac23328` эта очередь и отдельный controller удалены. `ax-server` сам выполняет reconciliation синхронно в обработчиках `CreateTask`, `ResumeTask`, `SuspendTask` и `DeleteTask`, а Redis используется как resource store, канал watch-уведомлений и backend распределённых блокировок.

Это не косметическая перестановка компонентов. Она меняет latency API, failure windows, способ повторять операции и то, что инженер должен считать доказательством успеха.

## Сначала разделите три вида правды

У Task нет одной магической записи, которая всегда описывает всё происходящее. В работе участвуют как минимум три источника состояния.

| Слой | Что он знает | Чего он не доказывает |
| --- | --- | --- |
| Redis AX | Task spec, phase, conditions, actor name, worker IP | Что Actor действительно существует и процесс внутри жив |
| Agent Substrate | Actor, Worker assignment, suspend/resume и snapshot lifecycle | Что agent command достиг цели или корректно завершился |
| Runner и harness | Workspace bootstrap, child process, checkpoint и application progress | Что AX успел сохранить актуальный status |

Reconciliation пытается сблизить эти представления, но не превращает Redis и Substrate в одну транзакционную базу. Между любыми двумя удалёнными вызовами процесс может завершиться, сеть может оборваться, deadline клиента может истечь, а одна система уже успеет принять изменение.

Поэтому lifecycle полезно читать не как красивую последовательность стрелок, а как цепочку отдельно подтверждаемых side effects.

## Что значит synchronous reconciliation

Запрос к Task проходит примерно так:

1. CLI вызывает gRPC API `ax-server`.
2. `ax-server` валидирует объект и захватывает lock для конкретного `kind/atespace/name`.
3. Обработчик читает или записывает AX resource в Redis.
4. Тот же обработчик напрямую вызывает Agent Substrate.
5. Для resume он может ждать Workspace readiness до 15 секунд.
6. Итоговый status записывается в Redis, lock освобождается, RPC возвращается клиенту.

Отдельной durable queue между пунктами 3 и 4 нет. Отдельный worker не подберёт незавершённое событие после рестарта `ax-server`. Плюс такого дизайна - простой request path и немедленная обратная связь об ошибке. Цена - более длинные RPC, сильная зависимость от client deadline и необходимость явно проектировать recovery после частично выполненной операции.

> **Важно:** Redis Pub/Sub в current main обслуживает `WatchTask` и ожидание освобождения lock. Это не прежняя очередь reconciliation и не журнал событий с повторной доставкой.

## Create: создать означает подготовить suspended Task

`CreateTask` не запускает агента. Его нормальный результат - зарегистрированный Task и подготовленный Actor в состоянии `Suspended`.

### 1. Admission и immutable identity

Сервер проверяет schema, DNS-подобные имена и коллизии путей Workspace bindings. Пустой `atespace` становится `default`. Затем захватывается lock `task/<atespace>/<name>` и проверяется отсутствие Task с таким именем.

Task в current API immutable: повторный `CreateTask` для существующего имени возвращает `FailedPrecondition`, даже если предыдущая попытка provisioning закончилась `Failed`. Это защищает identity Actor и launch envelope от неявной подмены, но означает, что retry create не является идемпотентным в привычном HTTP-смысле.

Для автоматизации используйте правило: после неоднозначного timeout сначала сделайте `GetTask`; не отправляйте create вслепую. Если объект уже существует, выбирайте осознанно между `ResumeTask` после устранения причины и delete/recreate.

### 2. Первая запись в Redis

Сервер добавляет creation timestamp, создаёт status и устанавливает `phase=Suspended`. Запись попадает в Redis до обращения к Substrate. Store одновременно обновляет индексы и публикует новое значение в task-specific Pub/Sub channel.

Это важная граница. Если всё последующее сломается, Task уже существует и его имя занято.

### 3. Формирование launch envelope

Reconciler гарантирует существование Atespace, назначает Actor имя Task и формирует окружение контейнера:

- переменные из `Task.spec.env`;
- `AX_TASK_YAML` без status;
- `AX_WORKSPACES_YAML` с найденными Workspace specs;
- credential для Gemini, если он разрешён через Kubernetes Secret или окружение сервера.

По image и environment вычисляется короткий digest, который входит в имя per-task `ActorTemplate`. Status намеренно исключён из digest: смена `Ready` не должна создавать новый template и новый golden state.

`fetchWorkspaces` добавляет только те references, которые реально нашлись в store. Текущая validation проверяет форму bindings и коллизии paths, но не превращает отсутствие Workspace в отдельную admission error. Поэтому platform policy должна заранее проверять существование всех ссылок, иначе runner может получить неполный `AX_WORKSPACES_YAML`.

### 4. Actor создаётся и сразу suspends

Reconciler вызывает `EnsureActor`. Если Actor уже существует, helper возвращает его; crashed Actor сначала пытается revert к последнему snapshot, а Actor в `Deleting` коротко ожидается перед повторным create.

Так как desired phase равен `Suspended`, reconciler вызывает `SuspendActor`, очищает `workerIp`, выставляет `Ready=False` с reason `TaskSuspended` и возвращает status серверу. После успешной записи status RPC завершается.

Есть тонкость текущей реализации: ошибка `SuspendActor` на create/suspend только записывается в log как warning, после чего Task всё равно получает `phase=Suspended`. Следовательно, один только AX status не доказывает, что Actor действительно перестал исполняться. Для строгого контроля стоимости или security boundary проверяйте состояние Actor в Substrate.

## Resume: Running появляется раньше Ready

`ResumeTask` снова захватывает task lock, читает immutable spec, меняет phase в памяти на `Running` и вызывает тот же `Reconcile`.

Reconciler повторно обеспечивает Atespace, template и Actor, после чего вызывает `ResumeActor`. Substrate назначает Worker, а AX сохраняет его адрес в `status.workerIp`. На Worker запускается sandbox, затем runner как PID 1, Workspace bootstrap и agent command.

После успешного `ResumeActor` phase уже становится `Running`. Дальше AX проверяет `/readyz?check=workspace` напрямую по Worker IP или через atenet-router. Poll interval равен 500 миллисекундам, default timeout - 15 секунд.

Результаты принципиально разные:

| Наблюдение | Phase | Conditions | Что это означает |
| --- | --- | --- | --- |
| Actor resume не удался | `Failed` | `Ready=False`, reason `ActorResumeFailed` | Runtime не подтверждён |
| Actor работает, Workspace ещё готовится | `Running` | `WorkspaceReady=False`, `Ready=False` | Sandbox запущен, но рабочая среда не готова |
| Workspace probe вернул 200 | `Running` | `WorkspaceReady=True`, `Ready=True` | Workspace bootstrap завершён |

`Running` поэтому не следует использовать как сигнал для выдачи новой работы. И даже `Ready=True` не доказывает, что child process жив, model endpoint доступен или агент достиг application goal: эти границы мы разобрали в главе о runner.

### Что происходит после 15 секунд

Timeout readiness не переводит Task в `Failed`. RPC возвращает Task в `Running` с `WorkspaceReady=False` и reason `Initializing`.

В current architecture нет фонового controller loop, который обязательно проверит probe позднее. Если Workspace станет готов на шестнадцатой секунде, сохранённый status может остаться `Initializing` до следующей lifecycle-операции, вызывающей reconcile. Это не значит, что runner не готов; это значит, что control-plane observation устарела.

Для production нужны один из трёх подходов:

- внешний observer периодически сверяет AX status с runner probe и инициирует repair;
- platform extension добавляет background reconciliation;
- вызывающая система считает AX condition snapshot-ом и проверяет фактическую readiness перед назначением работы.

### Почему WorkspaceReady остаётся True

После первого успешного bootstrap condition `WorkspaceReady=True` считается sticky и не опрашивается повторно при последующих suspend/resume. Идея разумна: setup выполняется один раз, а durable filesystem возвращается из snapshot.

Но sticky condition - утверждение о прошлом успешном setup, а не криптографическая проверка текущего filesystem. Повреждение snapshot, смена bootstrap contract или ручное изменение volume требуют отдельной version/health policy. Иначе control plane мгновенно выставит `Ready=True`, хотя application-level проверка ещё не выполнена.

## Suspend: остановить вычисление, сохранить логическую сущность

`SuspendTask` сериализуется тем же task lock. Сервер читает Task, ставит phase `Suspended` в памяти и вызывает reconciler.

Reconciler снова проходит ensure-шаги, затем просит Substrate suspend Actor. Успешный suspend должен остановить sandbox и запустить предусмотренный Substrate snapshot lifecycle. AX очищает `workerIp`, ставит `Ready=False/TaskSuspended`, но сохраняет прежний `WorkspaceReady=True`, если bootstrap уже когда-то завершался.

Практический смысл suspend:

- AX resource и identity Task остаются;
- Actor и durable workspace остаются частью runtime lifecycle;
- Worker capacity может быть освобождена;
- process memory, sockets и текущий child tree не сохраняются;
- внешний side effect не откатывается;
- после resume harness начинает новый process и восстанавливает semantic progress из durable state.

Не используйте ответ `SuspendTask` как единственное подтверждение остановки расхода ресурсов. Из-за warning-only обработки ошибки `SuspendActor` строгий automation должен после вызова проверить Actor state и отсутствие Worker assignment.

## Delete: сначала Terminating, затем Actor, потом Redis

Удаление построено осторожнее, чем suspend:

1. Task lock захвачен.
2. В Redis сохраняется `phase=Terminating`.
3. `ReconcileDelete` удаляет Actor в Substrate.
4. Client опрашивает Actor до ответа `NotFound`.
5. Reconciler пытается удалить все per-task ActorTemplates.
6. Только после этого AX удаляет Task record и индексы из Redis.

Если удаление Actor завершается ошибкой или deadline истекает, Task остаётся в Redis как `Terminating`. Это полезный recovery marker: оператор видит незавершённую очистку и может повторить delete. Если Actor уже исчез, повторная очистка трактует `NotFound` как успех.

Удаление templates является best effort. Ошибки после нескольких попыток записываются как warning, но Task всё равно может исчезнуть из Redis. Поэтому при расследовании утечки image/template artifacts проверяйте Substrate отдельно.

И, конечно, delete Task не равен business rollback. Git commit, ticket, письмо, изменение устройства или cloud resource, созданные агентом, остаются во внешней системе.

## Per-resource lock: что он гарантирует и где заканчивается

Production server использует Redis lock с ключом `lock:<kind>:<atespace>:<name>`. Захват выполняется через `SET NX` с уникальным token, освобождение - Lua script, который удаляет только lock с тем же token. Ожидающие клиенты просыпаются через Pub/Sub, а периодический retry страхует пропущенное уведомление.

Это даёт полезное свойство: два обычных `ResumeTask`/`SuspendTask` для одного Task не должны одновременно менять его lifecycle. Операции с разными Task могут идти параллельно.

Но lock не является глобальной транзакцией:

- default TTL равен 30 секундам;
- renewal/heartbeat lease в current implementation отсутствует;
- readiness wait сам может занять 15 секунд, а медленные Substrate calls добавляют время;
- Task lock не блокирует referenced Workspace или Model;
- side effect в Substrate и запись Redis не коммитятся атомарно.

Если обработчик работает дольше TTL, другой запрос способен получить тот же lock, пока первый ещё выполняется. Инженеру нужно либо держать RPC и dependencies заметно быстрее lease, либо добавить renewal/fencing token, либо вынести долгие операции в durable state machine.

Отдельно тестируйте race между resume Task и обновлением Workspace/Model. Reconciler собирает launch envelope из snapshot-а прочитанных объектов, но не захватывает их locks вместе с Task.

## Failure windows и осмысленный retry

| Где произошёл сбой | Возможное состояние | Безопасное действие |
| --- | --- | --- |
| До первой записи Redis | Task отсутствует | Повторить create |
| После SaveTask, до Actor | Task существует как Suspended/Failed, Actor может отсутствовать | `GetTask`, устранить причину, resume или delete/recreate |
| Actor создан, status не записан | Substrate впереди Redis | Сверить Actor по имени Task, затем повторить lifecycle operation |
| ResumeActor успешен, RPC оборвался | Actor может работать, клиент видит timeout | Не делать create; проверить Task, Actor и runner probe |
| Readiness timeout | `Running`, `Ready=False`; runner может стать готов позже | Проверить фактический probe, затем repair/reconcile policy |
| SuspendActor вернул ошибку | AX всё равно может показать `Suspended` | Проверить Actor state и Worker assignment |
| Actor удалён, Redis delete не выполнен | Task остаётся `Terminating` | Повторить delete; `NotFound` Actor считается успехом |
| Template cleanup не завершён | Task удалён, templates остались | Периодический orphan audit и garbage collection |

Главное правило retry: сначала выясните, какой side effect уже состоялся. Повтор без чтения двух систем безопасен только там, где API явно гарантирует idempotency.

## WatchTask - уведомление о status, а не доказательство progress

`WatchTask` сначала читает текущий Task и отправляет событие `INITIAL`, затем подписывается на task-specific Redis Pub/Sub channel и пересылает `MODIFIED` при сохранении status. Канал не является durable event log: отключившийся consumer не воспроизведёт все промежуточные переходы, но после reconnect получит актуальный snapshot через `INITIAL`.

Server завершает stream автоматически для `Failed` или `Completed`. Однако default runner не переводит Task в `Completed` по exit child process. Следовательно, долгоживущий watch не является ожиданием результата Job. Для application completion нужен отдельный durable protocol harness-а.

## Как читать status без самообмана

| Поле | Полезная интерпретация | Опасная интерпретация |
| --- | --- | --- |
| `phase=Suspended` | Desired/control-plane lifecycle остановлен | «Actor точно не потребляет Worker» |
| `phase=Running` | ResumeActor прошёл и назначение Worker было получено | «Агент жив и выполняет полезную работу» |
| `phase=Failed` | Последний synchronous reconcile вернул ошибку | «Никаких runtime side effects не осталось» |
| `phase=Terminating` | Delete начат, record сохранён для recovery | «Удаление обязательно завершится само» |
| `WorkspaceReady=True` | Workspace setup когда-то успешно завершился | «Текущий restored filesystem полностью исправен» |
| `Ready=True` | Actor running и AX считает Workspace готовым | «Model, tools и business outcome готовы» |
| `workerIp` | Последний сохранённый routing hint | «Worker assignment гарантированно актуален» |

Phase `Completed` распознаётся watch API, но current Task reconciler и runner не устанавливают его после завершения команды. Не проектируйте batch semantics вокруг поля, которое никто не обновляет.

## Минимальные operational playbooks

### Create вернул ошибку

1. Выполните `GetTask` по тому же atespace/name.
2. Если Task отсутствует, retry create допустим.
3. Если Task есть, проверьте phase/conditions и Actor с тем же именем.
4. Не меняйте manifest под тем же именем: Task immutable.
5. После исправления dependency выберите resume или контролируемый delete/recreate.

### Task долго остаётся Initializing

1. Сверьте `workerIp` с реальным Actor assignment.
2. Проверьте runner `/healthz` и `/readyz?check=workspace` отдельно.
3. Посмотрите workspace setup logs и доступность Git/files sources.
4. Помните, что status сам может не обновиться после 15-секундного окна.
5. Не объявляйте инцидент model serving, пока не доказана готовность workspace.

### Suspend не освобождает Worker

1. Не доверяйте только `phase=Suspended`.
2. Проверьте Actor state и Worker assignment в Substrate.
3. Ищите warning `could not suspend actor on Substrate` в `ax-server`.
4. Убедитесь, что snapshot backend доступен и suspend не завис на runtime-слое.

### Delete застрял в Terminating

1. Проверьте, существует ли Actor.
2. Если Actor ещё удаляется, не удаляйте Redis key вручную.
3. После `NotFound` повторите штатный delete, чтобы завершить очистку record.
4. Отдельно проверьте per-task templates и внешние side effects.

## Acceptance matrix для платформенной команды

| Сценарий | Что должно быть доказано |
| --- | --- |
| Два одновременных resume одного Task | Операции сериализуются до истечения lock lease |
| Reconcile дольше 30 секунд | Нет overlap или внедрены renewal/fencing |
| `ax-server` падает после SaveTask | Recovery находит persisted Task и состояние Actor |
| Client timeout после ResumeActor | Повтор не создаёт второй Actor и не теряет первый |
| Workspace готов после 15 секунд | Observer обнаруживает readiness и исправляет stale status |
| SuspendActor возвращает ошибку | Monitoring не принимает `Suspended` за доказательство остановки |
| Delete прерывается после удаления Actor | Повтор завершает Redis cleanup |
| Redis Pub/Sub сообщение потеряно | Reconnect watch возвращает актуальный `INITIAL` snapshot |
| Runner child завершился | Application protocol фиксирует outcome независимо от AX phase |

## Антипаттерны

- **Искать отдельный `ax-controller` в current main.** Lifecycle выполняет `ax-server`.
- **Называть Redis Pub/Sub очередью reconciliation.** Это transient notification, а не durable work log.
- **Повторять create после timeout без Get.** Task мог уже занять immutable имя.
- **Считать synchronous RPC транзакцией Redis + Substrate.** Между side effects остаются failure windows.
- **Назначать работу по `phase=Running`.** Сначала проверьте `Ready`, затем application dependencies.
- **Считать `WorkspaceReady=True` вечной проверкой данных.** Condition sticky и не валидирует restored state повторно.
- **Считать lock абсолютной защитой.** Lease истекает, а cross-resource transaction отсутствует.
- **Удалять застрявший Redis record вручную.** Так легко потерять ownership Actor и templates.
- **Считать delete откатом действий агента.** Внешние side effects живут отдельно.

## Итог

Task lifecycle в current AX - это synchronous orchestration внутри `ax-server`: resource lock, Redis state, прямые Substrate calls и короткое окно проверки runner readiness. Такой дизайн легче проследить, чем прежнюю очередь с отдельным controller, но он требует дисциплины на границах систем.

Инженер должен уметь ответить на четыре вопроса: какой side effect уже произошёл, где хранится последняя observation, истёк ли lock lease и какое действие действительно безопасно повторить. Тогда `Suspended`, `Running`, `Ready` и `Terminating` становятся полезными сигналами, а не успокаивающими ярлыками.

В следующей главе мы перейдём к model endpoint. Теперь связь ясна: даже идеально reconciled и ready Task бесполезен, если inference service не выдерживает контекст, parallelism и latency agent workload.

### Источники и дальнейшее чтение

- [AX current-main architecture](https://github.com/google/ax/blob/ac2332829f22360ff97b0ba34d94dd0dd782f17e/DESIGN.md)
- [Task API and synchronous lifecycle handlers](https://github.com/google/ax/blob/ac2332829f22360ff97b0ba34d94dd0dd782f17e/internal/server/server.go)
- [Task reconciliation and Workspace readiness](https://github.com/google/ax/blob/ac2332829f22360ff97b0ba34d94dd0dd782f17e/internal/controller/reconciler.go)
- [Redis resource store and WatchTask Pub/Sub](https://github.com/google/ax/blob/ac2332829f22360ff97b0ba34d94dd0dd782f17e/internal/store/redis/store.go)
- [Per-resource Redis locks](https://github.com/google/ax/blob/ac2332829f22360ff97b0ba34d94dd0dd782f17e/internal/lock/lock.go)
- [Substrate Actor operations used by AX](https://github.com/google/ax/blob/ac2332829f22360ff97b0ba34d94dd0dd782f17e/internal/substrate/client.go)
- [Change removing Redis Streams and ax-controller](https://github.com/google/ax/commit/ac2332829f22360ff97b0ba34d94dd0dd782f17e)
