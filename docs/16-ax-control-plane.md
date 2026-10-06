# AX control plane: от декларации к Actor и граница с Substrate

**Статус:** `CURRENT MAIN VERIFIED` · `DIRECT RECONCILIATION` · `ROADMAP SEPARATED`

В главах 12-15 мы смотрели на Agent Substrate снизу: как Actor получает Worker, как сохраняется его состояние и как трафик находит Actor после suspend/resume. Теперь поднимемся на продуктовый уровень. Инженеру редко хочется вручную создавать ActorTemplate, передавать в него описание workspace, управлять Actor и переводить его runtime states. Ему нужна более понятная декларация: вот задача, вот рабочая среда, вот модель, запусти это безопасно и покажи состояние.

Именно эту роль выполняет AX. Он не заменяет Substrate и не является ещё одним sandbox runtime. AX принимает продуктовый intent в виде `Task`, `Workspace` и `Model`, а затем переводит его в lifecycle operations Substrate. Полезная аналогия: AX отвечает за язык платформы и orchestration конкретной agent workload, Substrate - за надёжное выполнение stateful Actor на кластере.

Но аналогия с Kubernetes легко вводит в заблуждение. AX использует Kubernetes-подобные manifests и команды `apply`, `get`, `watch`, `delete`, однако его ресурсы не являются Kubernetes CRD. Они обслуживаются собственным gRPC API и хранятся в Redis. Более того, на проверенной версии current main reconciliation выполняется прямо внутри запроса `ax-server`. Отдельного работающего `ax-controller` в этой версии нет.

## Что читатель должен унести из главы

- какую проблему решает AX поверх Substrate и почему эти слои нельзя смешивать;
- где находятся API contract, desired state, observed status и runtime state;
- как create, resume, suspend, delete и watch проходят через `ax-server`;
- почему package `internal/controller` не означает отдельный controller process;
- зачем AX выбрал Redis вместо Task CRD и какую цену платит за этот выбор;
- какие сбои оставляют Redis и Substrate в разных состояниях;
- почему текущая direct-execution схема удобна для ранней версии, но ограничивает масштабирование;
- что уже реализовано, а что пока существует только в roadmap.

![AX Task: фактический путь current main](.gitbook/assets/diagrams/16-15.png)

*AX Task: фактический путь current main*

## Сначала разделим три контракта

Сложность этой архитектуры возникает не из количества компонентов, а из того, что одно слово «задача» означает разные сущности на разных слоях.

| Контракт | Что он описывает | Кто владеет |
| --- | --- | --- |
| AX resource contract | `Task`, ссылки на `Workspace`, image, command, env, compute requests, debug и status | AX API и Redis |
| Translation contract | Как Task превращается в Atespace, ActorTemplate, Actor и lifecycle calls | `TaskReconciler` внутри AX |
| Runtime contract | Placement, Worker assignment, sandbox, durable volume, snapshot, suspend/resume и network path | Agent Substrate |

AX может сказать: «эта Task должна использовать такой image и такие workspaces». Он не выбирает Worker и не восстанавливает snapshot самостоятельно. Substrate умеет запустить Actor, но не знает, что для продукта означают `WorkspaceReady`, цель workspace или пользовательская команда `ax watch`. Граница проходит не между двумя наборами процессов, а между двумя моделями ответственности.

> **Практическое правило.** Если решение связано с продуктовым intent агента, его API и статусом - это AX. Если решение связано с безопасным размещением, остановкой, восстановлением и адресацией sandbox - это Substrate.

## Компоненты current main

| Компонент | Фактическая роль | Чего он не делает |
| --- | --- | --- |
| `ax` | Читает YAML, преобразует документы в typed protobuf calls, выполняет get/watch/lifecycle команды и настраивает доступ к cluster endpoint | Не пишет CRD и не создаёт Actor напрямую |
| `ax-server` | Обслуживает gRPC и `/healthz`, валидирует ресурсы, берёт locks, пишет Redis и синхронно вызывает reconciler | Не выполняет agent command |
| Redis | Хранит AX resources, индексы, distributed locks и Pub/Sub notifications для watch | Не является источником runtime placement и snapshots |
| `internal/controller` | Go package с `TaskReconciler`, включённый в процесс `ax-server` | Не отдельный deployment и не непрерывный queue consumer |
| Substrate Control API | Создаёт Atespace, ActorTemplate и Actor, выполняет resume/suspend/delete и возвращает Worker assignment | Не интерпретирует AX Workspace как продуктовую сущность |
| `ax-task-runner` | Работает внутри Actor, материализует workspaces, публикует readiness и запускает workload | Не является control plane |

Название package `controller` здесь описывает паттерн кода, а не topology. Это важная инженерная привычка: архитектуру нельзя выводить из имени каталога. Нужно проследить wiring в `cmd/ax-server/main.go`. Именно там создаются Redis store, Redis locker, Substrate client и `TaskReconciler`, после чего reconciler передаётся API server как обычная зависимость.

## Что происходит при `ax apply`

CLI разбирает manifest локально. Raw YAML не отправляется в server: клиент вызывает typed RPC для соответствующего kind. Для Task это `CreateTask`, потому что Task в текущем API immutable после создания. Workspace и Model используют update semantics.

1. **Validation.** Server проверяет имя и atespace как RFC 1123 labels, а также корректность workspace bindings и mount paths.
2. **Serialization per object.** Для ключа `task/<atespace>/<name>` берётся distributed lock. Параллельные create/resume/delete одной Task не должны менять lifecycle одновременно.
3. **Existence check.** Если Task уже есть, create завершается `FailedPrecondition`. Изменить immutable Task повторным apply нельзя.
4. **Первичная запись.** Task получает timestamp, status и phase `Suspended`, затем сохраняется в Redis.
5. **Разрешение ссылок.** Server читает из Redis перечисленные в Task объекты Workspace и передаёт их reconciler.
6. **Direct reconcile.** В том же RPC context AX вызывает Substrate, создаёт необходимые runtime resources и приводит Actor в suspended state.
7. **Status update.** Полученный status снова записывается в Redis и публикуется подписчикам watch.

Неожиданный, но принципиальный вывод: успешный `CreateTask` означает не только «spec принят». Request уже прошёл часть runtime provisioning. Поэтому latency и availability Substrate входят в latency и availability AX API. Это проще понимать и отлаживать, но оно связывает два control planes во времени.

## Внутри TaskReconciler

Reconciler сначала гарантирует существование Substrate Atespace. Имя Actor всегда равно имени Task; это упрощает маршрутизацию и диагностику. Затем он формирует launch configuration: image, пользовательские env, сериализованный Task в `AX_TASK_YAML` и multi-document Workspace YAML в `AX_WORKSPACES_YAML`.

Для отличающейся конфигурации создаётся per-task ActorTemplate. Его имя включает короткий digest image и environment. Status исключён из digest, иначе каждое изменение phase создавало бы новую template и новый golden snapshot. После этого AX выполняет idempotent `EnsureActor`. Если Actor уже существует, client получает его; crashed Actor пытается revert к snapshot, а Actor в deleting state получает короткое окно на завершение удаления.

Для новой Task desired phase равен `Suspended`, поэтому созданный Actor сразу переводится в suspended state. Команда `ax resume` меняет desired phase на `Running` и снова вызывает тот же reconciler. Substrate назначает Worker и возвращает его address, а AX временно опрашивает `/readyz?check=workspace` внутри Actor. Сначала используется прямой Worker address, при наличии настройки возможен путь через atenet router.

По умолчанию readiness polling ограничен 15 секундами. Если workspace готов, AX устанавливает `WorkspaceReady=True` и `Ready=True`. Если нет, Task остаётся в phase `Running`, но обе conditions объясняют, что initialization продолжается. Это важнее простой строки phase: запущенный Actor ещё не обязательно готов выполнять пользовательскую работу.

### Где заканчивается reconciliation

В проверенной версии нет фонового worker, который постоянно сравнивает Redis с Substrate. Polling выполняется только во время конкретного lifecycle RPC. Если workspace стал готов после 15-секундного окна, сам по себе Redis status не обязан обновиться немедленно: нужен следующий вызов, который снова запустит reconciliation, либо будущий механизм постоянного наблюдения.

Поэтому слово reconcile здесь следует понимать узко: это idempotent translation procedure, вызванная API operation, а не бесконечный Kubernetes-style loop. Для пользователя разница проявляется в status freshness, а для оператора - в recovery после частичного сбоя.

## Desired, observed и runtime state

| Данные | Где живут | Что потеря означает |
| --- | --- | --- |
| Task/Workspace/Model spec | Redis | AX теряет продуктовый intent и ссылки, даже если Actor ещё существует |
| Task phase/conditions/actor/workerIP | Redis | CLI видит устаревшую или отсутствующую картину |
| Actor/ActorTemplate/Atespace | Substrate control plane | Нельзя управлять runtime по одному Redis spec |
| Placement и Worker assignment | Substrate | AX не должен считать cached workerIP авторитетным |
| Durable filesystem и snapshot | Substrate storage path | Spec сохраняется, но продолжить работу с прежнего состояния нельзя |
| Workspace setup result | Actor volume плюс AX condition | Filesystem и отображаемый status могут временно расходиться |

Redis здесь не является единственным source of truth для всей системы. Он авторитетен для AX resource record. Substrate авторитетен для runtime existence и placement. Durable storage авторитетен для содержимого state. Production runbook обязан проверять все три слоя, иначе «Task Running» легко принять за доказательство, которого оно не даёт.

## Почему Redis, а не CRD на каждую Task

DESIGN.md объясняет выбор ожидаемыми миллионами короткоживущих Tasks. Kubernetes API и etcd превосходны как control plane для относительно небольшого количества декларативных infrastructure objects. Высокочастотный application state с быстрым churn создаёт другой профиль: много записей status, большие индексы, watchers, compaction и давление на общий cluster control plane.

Redis позволяет AX выбрать собственный key model, TTL для Tasks, sorted-set indexes и дешёвый Pub/Sub. В текущей реализации Task хранится отдельным JSON value, глобальный и per-atespace indexes - в sorted sets, а status update перезаписывает Task и публикует notification. Workspace и Model хранятся без TTL; Task TTL настраивается опционально и по умолчанию не задан.

Цена решения тоже реальна. CRD бесплатно получили бы Kubernetes authentication, RBAC, admission, resourceVersion, durable watch semantics, audit integration и зрелые backup procedures. Собственный API обязан реализовать или компенсировать это сам. Выбор Redis оправдан не потому, что он «быстрее вообще», а когда churn и требуемая cardinality действительно делают Kubernetes API неподходящим data path.

## Watch не равен durable event log

`WatchTask` подписывается на Redis Pub/Sub channel конкретной Task, затем отправляет клиенту initial snapshot из Redis и последующие `MODIFIED` messages. Pub/Sub удобен для live UI, но сообщения не сохраняются. При разрыве соединения события между disconnect и reconnect будут пропущены; новый initial snapshot восстанавливает текущее состояние, но не историю переходов.

Следовательно, watch подходит для «покажи, где Task сейчас», но не заменяет audit log. Если важны доказательство approvals, расход токенов, lifecycle history или расследование инцидента, события нужно отдельно писать в durable journal с idempotency key и retention policy.

## Locks, idempotency и граница транзакции

Redis lock сериализует операции одной AX resource. Substrate methods стараются быть idempotent: Atespace и ActorTemplate допускают AlreadyExists, EnsureActor возвращает существующий Actor, delete отсутствующего Actor считается успехом. Эти механизмы уменьшают риск duplicate resources при retry.

Но distributed lock не превращает Redis и Substrate в одну транзакцию. Между сохранением Task и созданием Actor процесс может упасть. Actor может быть создан, а status update в Redis - не выполниться. Delete может удалить Actor, но не успеть удалить Redis record. Это нормальная проблема dual-write, и её нельзя исправить более длинным timeout.

```
possible partial states:
  Redis Task exists, Substrate Actor missing
  Redis says Suspended, Actor is Running
  Actor exists, Redis Task missing
  Actor deleted, Task remains Terminating
  WorkspaceReady=False, workspace already initialized
```

В зрелой схеме recovery выполняет постоянный reconciler: он перечисляет desired resources, сверяет observed runtime, повторяет idempotent steps и обрабатывает orphans по явной policy. Текущий direct path частично восстанавливается при повторной lifecycle operation, но не гарантирует автоматическую convergence без внешнего сигнала.

## Failure matrix

| Сбой | Что увидит клиент | Что проверить | Безопасное восстановление |
| --- | --- | --- | --- |
| `ax-server` недоступен | Все gRPC operations недоступны; Actors могут продолжать работать | Pod readiness, listener, Redis и Substrate connectivity | Перезапустить server, не удаляя Actors |
| Redis недоступен | Create/get/watch и locks падают | Persistence, replication, memory pressure, latency | Восстановить Redis, затем inventory diff с Substrate |
| Substrate API недоступен | Lifecycle RPC завершается error после записи или чтения Redis | TLS, token, service endpoint, Substrate health | Восстановить API и повторить idempotent operation |
| Server упал после CreateActor | Client видит error, Actor может существовать | Task record и Actor с тем же atespace/name | Не создавать вручную второй Actor; повторить reconcile path |
| Workspace readiness timeout | Phase Running, Ready False | Runner logs, Worker route, `/readyz?check=workspace` | Исправить setup и инициировать новый lifecycle check |
| Delete прерван | Task может остаться Terminating | Actor, templates и Redis record отдельно | Продолжить idempotent cleanup по UID/name policy |

## Масштабирование direct-execution архитектуры

API replica одновременно является lifecycle worker. Slow Substrate call, workspace readiness polling и delete wait удерживают request и goroutine. Добавление replicas увеличивает concurrency, но каждый replica должен иметь одинаковый доступ к Redis locks, Kubernetes Secrets и Substrate Control API. Балансировщик не решает contention одной Task: lock намеренно оставляет один active lifecycle operation.

Пропускную способность нужно считать не только в requests per second. Для AX важны create latency, concurrent readiness polls, длительность suspend/resume, количество locks в ожидании, Substrate API saturation и Redis write latency. Если средний resume занимает 5 секунд, сотня HTTP workers не превращает один Substrate cluster в бесконечную capacity.

Переход к отдельному asynchronous controller имеет смысл, когда требуется durable work queue, controlled concurrency, retries после падения API process и rate limiting к Substrate. Но он приносит eventual consistency, queue lag, deduplication и отдельную эксплуатацию. Выбирать его нужно по измеренному demand и recovery objectives, а не потому, что «так делает Kubernetes».

## Security boundary и текущие ограничения

Substrate client поддерживает TLS, отдельный authority, CA bundle и bearer token file. Это защищает AX -> Substrate path при корректной конфигурации. На стороне пользовательского API current server создаёт обычный gRPC server без видимых authentication/RBAC interceptors и может обслуживать unencrypted HTTP/2. Следовательно, endpoint нельзя публиковать в недоверенную сеть без внешнего authenticated gateway, mTLS или эквивалентного enforcement layer.

Есть и более тонкая граница. Current reconciler помещает Gemini API key из Kubernetes Secret или server environment в ActorTemplate env. Для прототипа это работает, но secret доступен Actor и может попасть в runtime state. Production target главы 15 остаётся прежним: выдавать credential на egress gateway, не помещая его в sandbox. Roadmap AX также обещает least-privilege policies и более строгие security profiles.

Ещё один компромисс: если создание custom ActorTemplate не удалось, reconciler логирует warning и может перейти на default template. В production silent degradation опасна: Task способна стартовать с неожиданным image или environment. Платформе нужен alert на такой fallback, а для security-sensitive workloads лучше fail closed.

## Что реализовано, а что пока обещано

| Возможность | Статус в проверенном current main |
| --- | --- |
| Typed gRPC API, Redis store, Redis locks | Реализовано |
| Direct Task reconciliation с Substrate | Реализовано внутри `ax-server` |
| Task create/suspend/resume/delete/watch | Реализовано |
| Workspace readiness conditions | Реализован bounded poll во время reconcile |
| Отдельный `ax-controller` и durable queue | Не обнаружены в проверенной tree |
| Постоянный background convergence loop | Не обнаружен |
| Разделение workspace setup и task runtime на разные Actors | Roadmap |
| Автоматический suspend по idleness | Roadmap |
| Task branching, SPIFFE identity, telemetry trajectories | Roadmap |
| Стабильные specs и production governance | Roadmap; API имеет `v1alpha1` |

Книга фиксирует source snapshot, а не брендовый narrative. AX прямо предупреждает, что проект активно меняется и до stable release возможны breaking changes. Перед внедрением следует повторить проверку wiring, proto и roadmap на выбранном commit.

## Как диагностировать Task по слоям

1. **API.** Доступен ли `/healthz`, проходит ли gRPC, какой exact error code вернулся?
2. **AX record.** Есть ли Task в Redis, каковы phase, conditions, actor и workerIP?
3. **Lock.** Не зависла ли конкурирующая lifecycle operation для того же atespace/name?
4. **Translation.** Какую ActorTemplate выбрал reconciler, не было ли fallback на default?
5. **Substrate.** Существуют ли Atespace, ActorTemplate и Actor, каков Actor state и assignment?
6. **Runner.** Отвечает ли workspace ready probe, что говорят setup logs?
7. **Storage.** Существует ли durable volume/snapshot, если Task должна пережить suspend?
8. **Network.** Работает ли actor-aware route, не используется ли устаревший workerIP?

Такой порядок не даёт перескочить сразу к Worker logs, когда на самом деле Task не прошла validation, и не заставляет чинить Redis, когда проблема в runner readiness.

## Антипаттерны

- **Называть package отдельным service.** Deployment topology проверяется по entrypoint и manifests.
- **Считать успешную запись в Redis запущенной Task.** Runtime provisioning может ещё не завершиться.
- **Считать phase Running готовностью.** Для работы нужно `Ready=True`, а не только назначенный Worker.
- **Использовать watch как audit log.** Redis Pub/Sub не хранит пропущенные события.
- **Backup только Redis.** Spec без Actor state и snapshot не восстанавливает работу.
- **Backup только Substrate.** Runtime без AX intent превращается в orphan.
- **Retry create новым именем.** Сначала нужно проверить, не создался ли Actor при partial failure.
- **Публиковать gRPC endpoint напрямую.** Внешний authentication и authorization должны быть явными.
- **Принимать roadmap за текущую гарантию.** План не является implementation contract.

## Production checklist control plane

- Версия AX закреплена commit или release, а расхождения с docs задокументированы.
- Проверено, direct или asynchronous reconciliation используется именно в этой версии.
- Redis имеет persistence, backup, restore test, memory limits и alerting.
- Есть inventory reconciliation между Redis Tasks и Substrate Actors.
- Partial create/delete scenarios проверены fault injection.
- gRPC endpoint защищён authentication, authorization и transport security.
- Substrate TLS authority, CA и token rotation протестированы.
- Readiness SLO отделён от API acceptance и Actor phase.
- Watch дополняется durable audit trail, если история является требованием.
- Fallback на default ActorTemplate наблюдаем или запрещён.
- Secrets не передаются в Actor env для недоверенного workload.
- Capacity plan учитывает длительные lifecycle RPC и readiness polling.
- DR exercise восстанавливает Redis, Substrate metadata и snapshots как разные слои.

## Итог

AX превращает удобный для инженера agent intent в конкретный runtime lifecycle. Его ценность не в том, что он скрывает Substrate, а в том, что он добавляет продуктовый API и переводит Task, Workspace и Model в Atespace, ActorTemplate и Actor. На проверенной версии этот перевод выполняется синхронно внутри `ax-server`; Redis хранит AX records и live notifications, а Substrate остаётся владельцем placement, sandbox, suspend/resume и snapshot lifecycle.

Эта граница позволяет правильно выбирать инструмент. Если нужна изоляция и stateful runtime - смотрим на Substrate. Если нужен декларативный интерфейс для agent workloads и их product semantics - добавляем AX. Если нужны durable retries, автоматическая convergence и большая независимая controller capacity - проверяем, реализованы ли они в выбранной версии, либо проектируем этот слой отдельно.

В следующей главе мы разберём сами AX primitives: почему Task специально остаётся маленькой, что Workspace материализует внутри sandbox, какую роль играет Model и как не превратить эти ресурсы в неуправляемый универсальный manifest.

### Источники и дальнейшее чтение

- [AX README and operational overview](https://github.com/google/ax/blob/ac2332829f22360ff97b0ba34d94dd0dd782f17e/README.md)
- [AX design and API reference](https://github.com/google/ax/blob/ac2332829f22360ff97b0ba34d94dd0dd782f17e/DESIGN.md)
- [ax-server dependency wiring](https://github.com/google/ax/blob/ac2332829f22360ff97b0ba34d94dd0dd782f17e/cmd/ax-server/main.go)
- [AX gRPC lifecycle implementation](https://github.com/google/ax/blob/ac2332829f22360ff97b0ba34d94dd0dd782f17e/internal/server/server.go)
- [TaskReconciler implementation](https://github.com/google/ax/blob/ac2332829f22360ff97b0ba34d94dd0dd782f17e/internal/controller/reconciler.go)
- [Redis resource and watch store](https://github.com/google/ax/blob/ac2332829f22360ff97b0ba34d94dd0dd782f17e/internal/store/redis/store.go)
- [AX to Substrate client](https://github.com/google/ax/blob/ac2332829f22360ff97b0ba34d94dd0dd782f17e/internal/substrate/client.go)
- [AX roadmap](https://github.com/google/ax/blob/ac2332829f22360ff97b0ba34d94dd0dd782f17e/docs/roadmap.md)
