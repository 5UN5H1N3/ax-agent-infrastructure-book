# Lifecycle stateful Actor: pause, suspend, resume и восстановление

**Статус:** `STATE MACHINE` · `DURABILITY BOUNDARY` · `FAILURE SEMANTICS`

В главе 13 мы собрали ресурсную модель Substrate: Actor получает identity, ActorTemplate задаёт исполняемую среду, WorkerPool предоставляет capacity, а snapshot всегда имеет конкретного владельца. Теперь разберём главное следствие этой модели: что происходит с Actor между запросами и где проходит граница долговечности.

Фраза «поставить процесс на паузу и потом продолжить» слишком проста для production. Нужно различать checkpoint на локальном диске node, durable snapshot в object storage, cold boot из image, восстановление process memory и откат к последней подтверждённой точке. Эти пути имеют разную стоимость и разные последствия при отказе.

## Что читатель должен унести из главы

- чем `PauseActor` отличается от `SuspendActor` и когда выбирать каждый путь;
- как `Full` и `Data` scopes меняют resume semantics;
- как выбирается источник resume: local, external, golden snapshot или cold boot;
- почему checkpoint не делает TCP sessions, leases и external volumes транзакционными;
- как `RevertActor` восстанавливает доступность ценой отката;
- какие метрики и fault-injection tests нужны до aggressive hibernation.

## Lifecycle - это state machine, а не набор команд

Control plane фиксирует переходное состояние до дорогой операции. Благодаря этому повторный вызов может продолжить workflow после timeout или restart. Клиенту важно перечитывать `Actor.status`, а не считать сетевой ответ единственной правдой.

| Состояние | Смысл | Обычное действие |
| --- | --- | --- |
| `SUSPENDED` | Actor не выполняется; durable snapshot готов или Actor ещё не запускался | Resume, update, delete |
| `RESUMING` | Выбирается Worker, подключаются volumes, выполняется restore/boot | Ждать или повторить resume |
| `RUNNING` | Sandbox активен и обслуживает трафик | Работа, pause, suspend, revert |
| `PAUSING` | Создаётся node-local checkpoint | Ждать или повторить pause |
| `PAUSED` | Checkpoint хранится на node, worker assignment освобождён | Resume на той же node или suspend |
| `SUSPENDING` | Checkpoint создаётся или загружается в object storage | Ждать или повторить suspend |
| `CRASHED` | Текущая execution потеряна или wake workflow завершился ошибкой | Revert, затем resume |
| `REVERTING` | Уничтожается текущий sandbox и partial state | Ждать или повторить revert |
| `DELETING` | Удаляются runtime, volumes и owned snapshots | Повторить delete с guards |

Стабильные состояния - `SUSPENDED`, `PAUSED`, `RUNNING` и `CRASHED`. Остальные описывают workflow. Если Actor долго остаётся в переходе, не редактируйте status вручную: определите незавершённый шаг и повторите публичную операцию либо выполните контролируемый revert.

### Как выбрать операцию

| Задача | Операция | Состояние хранится | Компромисс |
| --- | --- | --- | --- |
| Коротко освободить Worker и быстро вернуться | `PauseActor` | На node | Быстро, но не durable |
| Пережить потерю Worker/node | `SuspendActor` | В object storage | Upload/download и I/O |
| Запустить из последней точки | `ResumeActor` | Local, external, golden или none | Путь зависит от scope |
| Отбросить ошибочную execution | `RevertActor` | Последний completed external snapshot | Изменения после него теряются |
| Удержать точку дольше следующего suspend | `CreateTag` | Отдельная tag-owned копия | Хранение и отдельный owner |

## Что именно попадает в snapshot

Scope отвечает за состав checkpoint, а операция - за место хранения. Pause оставляет файлы на node, suspend загружает их в object storage.

| Scope | Что сохраняется | Resume | Когда выбирать |
| --- | --- | --- | --- |
| `Full` | Process memory, writable rootfs delta и `DurableDir` | Продолжение memory state | Дорогая инициализация, in-memory progress |
| `Data` | Snapshot-capable volumes, прежде всего `DurableDir` | Fresh containers + восстановленные данные | Restartable process и меньший snapshot |

`snapshotConfig.onPause` задаёт scope локального checkpoint, `onCommit` - durable snapshot. `onCommit` обязан быть подмножеством `onPause`:

```
onPause=Full, onCommit=Full  -> hot local и hot durable resume
onPause=Full, onCommit=Data  -> hot pause, дешёвый durable suspend
onPause=Data, onCommit=Data  -> process стартует заново после lifecycle
```

`onPause=Data, onCommit=Full` невозможен: после Data pause память уже не сохранена. Если Actor paused с Data checkpoint, Full commit отклоняется до изменения состояния, чтобы Actor оставался resumable.

### Full или Data

Full оправдан, если память содержит дорогое состояние: загруженную модель, compiled graph, interpreter session или agent loop. Но он больше, сильнее связан с runtime и template UID и создаёт I/O burst. Data лучше, когда entrypoint умеет реконструировать execution из файлов и durable records. Такой контракт проще тестировать.

> **Правило.** Начинайте с Data, если есть хороший restart protocol. Выбирайте Full, когда измеренная цена повторной инициализации или потери in-memory progress оправдывает более тяжёлый snapshot.

## Golden snapshot: оптимизация первого запуска

Golden snapshot - published Tag, созданный один раз для ActorTemplate. Controller запускает временный Actor в `ate-golden`, ждёт готовности, делает Full suspend, копирует snapshot в tag-owned storage и записывает ссылку в template status.

1. Cold boot digest-pinned image с выбранным SandboxConfig.
2. Общая инициализация: libraries, read-only assets, runtime caches.
3. Ожидание `wakeupProbe`; если probe есть не у всех containers, применяется warm-up interval.
4. Full checkpoint независимо от обычного `onCommit`.
5. Создание published Tag, удаление временного Actor, Ready status.

Actor, созданный до готовности golden tag, может получить пустой `externalSnapshot` и впервые пойти через cold boot. Позднее появление golden snapshot не перепривязывает его автоматически. Поэтому Ready должен быть deployment gate.

В golden snapshot безопасно помещать общие libraries и read-only assets. Per-Actor secrets, tenant identity, уникальные leases и живые внешние соединения должны появляться после clone/resume. Иначе все клоны получат одну замороженную identity или протухший credential.

## Resume: четыре источника одного перехода

![Resume on demand: запрос может разбудить suspended actor](.gitbook/assets/diagrams/14-14.png)  
*Resume on demand: запрос может разбудить suspended actor*

`ResumeActor` переводит Actor в `RUNNING`, но внутри это разные пути. Control plane читает status/template, создаёт и подключает external volumes, выбирает Worker, просит atelet восстановить workload и только после готовности фиксирует assignment.

| Источник | Когда | Фактический запуск |
| --- | --- | --- |
| Node-local snapshot | `PAUSED`, local checkpoint имеет приоритет | Restore на node snapshot |
| Actor external snapshot | Есть собственный suspend | Full restore или Data + fresh boot |
| Borrowed Tag/golden | Actor создан из Tag или template golden | Restore общей immutable точки |
| Snapshot отсутствует | Golden не был готов или source не задан | Cold boot из image |

### Пошаговый resume

1. Если Actor уже `RUNNING`, вызов является no-op и возвращает `resumed=false`.
2. Lease сериализует lifecycle, чтобы два запроса не назначили два Workers.
3. Выбираются source, scope и совместимость template UID.
4. Пересекаются sandbox class, selectors и свободные CPU/RAM/slots.
5. Создаются/подключаются external volumes.
6. Atelet выполняет restore либо cold boot.
7. Все объявленные wakeup probes должны вернуть HTTP 200.
8. Status становится `RUNNING` с `workerAssignment`.

Если snapshot создан под другим ActorTemplate UID, memory image нельзя считать совместимой. При разрешённом update Substrate может восстановить durable data, но запустить guest заново. Это migration path, а не hot resume; SLO должен различать эти случаи.

### Wakeup probe определяет смысл «готов»

Без probe успешный resume означает, что container process запущен, но application может ещё не слушать порт. С probe RPC ждёт HTTP 200 от каждого container с probe. Default timeout - 30 секунд. Ошибка переводит Actor в `CRASHED`; перед следующим resume нужен revert.

Probe является one-shot gate, а не постоянной readiness check. Он должен проверять локальную готовность, не весь downstream graph. Иначе сбой внешней базы превратит готовые Actors в crashed.

### Request parking скрывает wake latency, но не отменяет её

Router может удерживать первый запрос и объединять несколько конкурентных запросов вокруг одного in-flight resume. Но assignment, download, restore, probe и tunnel всё равно входят в latency budget. Подробности routing будут в следующей главе.

## Pause: быстрый checkpoint с node affinity

`PauseActor` создаёт checkpoint локально на node. После завершения assignment очищается, volumes отсоединяются, а status хранит `localSnapshot` с node и snapshot name. Следующий resume возвращается к этой node.

Pause экономит object-storage upload и полезен для короткого idle interval. Но это performance tier, не durability tier. Потеря node или очистка локального диска уничтожают точку. Recovery тогда опирается на предыдущий external snapshot через revert, а всё после него теряется.

| Свойство | Pause | Suspend |
| --- | --- | --- |
| Хранение | Node-local | Object storage |
| Placement | Snapshot node | Любой совместимый Worker |
| Потеря node | Не переживает | Переживает |
| Цена | Локальный checkpoint | Checkpoint + upload/download |
| Idle horizon | Короткий | Долгий/неопределённый |

### Pause, затем suspend

Paused Actor можно suspend'ить без повторного запуска. Control plane загружает local snapshot и при необходимости сужает Full до Data:

```
RUNNING
  -> short idle: PauseActor (Full, local)
  -> idle continues: SuspendActor (Data, external)
  -> later ResumeActor: fresh process + restored DurableDir
```

Такой tiered policy даёт быстрый возврат в первые минуты, но не хранит memory image для сессий, которые не вернулись.

## Suspend: durable commit и освобождение Worker

`SuspendActor` принимается из `RUNNING` и `PAUSED`. В первом случае Worker создаёт checkpoint и отправляет его в actor-owned URI. Во втором готовый local checkpoint загружается в object storage.

1. Зафиксировать `SUSPENDING` и `inProgressSnapshotUri`.
2. Создать checkpoint или взять local snapshot.
3. Записать manifest и objects в storage.
4. Отсоединить volumes и освободить Worker.
5. Сделать новый snapshot текущим, очистить in-progress fields, перейти в `SUSPENDED`.
6. Освободить предыдущий actor-owned snapshot; borrowed Tag не удалять.

Actor владеет одним текущим external snapshot. Следующий успешный suspend заменяет его. Если точка должна жить дольше, пока Actor suspended создают Tag.

### Зачем status показывает in-progress URI

Upload может оборваться после записи части objects. Persisted destination делает retry re-entrant: workflow продолжает тот же URI, а revert удаляет partial snapshot. Предыдущий `externalSnapshot` остаётся последней подтверждённой точкой, пока commit не завершён. После timeout перечитайте Actor: операция могла успешно завершиться или остаться в переходе.

## Revert: доступность ценой отката

`RevertActor` принимается из `RUNNING`, `PAUSED` и `CRASHED`. Он уничтожает текущий sandbox, освобождает Worker, отбрасывает local и partial snapshots, сохраняя последний completed external snapshot. Итог - `SUSPENDED`.

```
CRASHED / RUNNING / PAUSED
  -> RevertActor
  -> discard current execution and partial checkpoint
  -> keep last completed external snapshot
  -> SUSPENDED -> ResumeActor
```

Revert - controlled rollback. Если durable commit был час назад, час изменений process memory/rootfs потеряется. Automation может делать revert автоматически только при явно принятом RPO.

### External volumes не откатываются

External CSI volumes не входят в Actor snapshot. После revert память/rootfs могут вернуться в прошлое, а volume или database остаться в будущем. Нужны idempotency keys, versioned records, transactional outbox или reconciliation. Substrate checkpoint не является распределённой транзакцией.

## Что snapshot не обещает сохранить

### TCP sessions

Memory image может содержать file descriptor, но remote endpoint не обязан сохранять соединение. NAT, load balancer и server timeout могли истечь. После resume приложение должно переподключиться.

### Credentials, DNS и leases

Token может протухнуть, DNS answer измениться, lease перейти другому владельцу. После resume обновляйте credentials и перепроверяйте ownership.

### External side effects

Crash между внешним действием и checkpoint создаёт неопределённость. Повтор после revert способен выполнить действие дважды. Нужны idempotency key, deduplication record и reconcile по external operation ID.

## Eviction и окно потери данных

При удалении Worker Pod текущий graceful path посылает Actor `SIGTERM` и оставляет до 30 минут для suspend. Если commit не завершён, Actor становится `CRASHED`, а всё после предыдущего external snapshot теряется. Это grace period, не гарантия: node может исчезнуть сразу.

RPO определяется максимальным временем между успешными durable snapshots. Actor, running несколько часов, имеет несколько часов потенциальной потери, даже если обычно suspend'ится в конце задачи.

| Workload | Подход | Почему |
| --- | --- | --- |
| Короткая сессия | Suspend в конце | Повтор работы приемлем |
| Долгий agent job | Application checkpoints + lifecycle commits | Ограничивает RPO |
| Финансовое действие | External log + idempotency | Snapshot не доказывает external commit |
| Warmed model | Golden Full + Data commits | Общий warm state, меньше durable memory |

## AX nuance: workspace durable, process reconstructable

В AX runner восстановление `/workspace` не гарантирует продолжение того же process tree. Task должен иметь явные checkpoints: plan, tool results, patch state и finalization marker. Хороший agent пишет durable intent до внешнего действия и result после него.

```
step-042.intent.json -> operation_id и ожидаемое действие
step-042.result.json -> подтверждённый результат/reference
checkpoint.json      -> последний завершённый step

resume: read checkpoint -> reconcile dangling intents -> continue
```

Этот protocol полезен и при Full snapshot: memory ускоряет happy path, журнал защищает от eviction, migration и revert.

## Сквозной сценарий: coding Actor

1. Golden snapshot содержит language server и общие tool binaries.
2. Первый request делает Full resume из golden.
3. После короткого idle Actor уходит в Full pause на node.
4. Если idle продолжается, paused Actor suspend'ится с Data scope: workspace durable, memory отброшена.
5. Через сутки fresh process восстанавливает workspace и читает journal.
6. После crash revert возвращает последний Data snapshot, journal помогает reconcile tool calls.

Golden решает цену первого boot, pause - короткий idle, suspend - durability, journal - business correctness, revert - recovery.

## Observability: измерять путь, а не одну latency

| Сигнал | Что показывает |
| --- | --- |
| Actors по state и возрасту | Застрявшие transitions и crash rate |
| Resume latency по source/scope | Local, Full, Data и cold boot |
| Snapshot bytes/upload duration | I/O cost и storage pressure |
| Probe timeout | Startup failure или плохой readiness contract |
| Revert count/rollback age | Recovery frequency и реальный RPO |
| Cold boot despite golden | Проблемы Ready gate или IAM |

```
wake_latency = parking + lease_wait + assignment + volume_attach
             + download_or_boot + restore + probe + tunnel
```

## Fault-injection plan

1. Оборвать RPC во время resume и доказать отсутствие второго assignment.
2. Убить control plane после записи in-progress URI и повторить suspend.
3. Прервать upload, затем revert и проверить cleanup.
4. Потерять node с paused Actor и восстановиться из external snapshot.
5. Дать probe timeout, увидеть `CRASHED`, выполнить revert/resume.
6. Сменить совместимый template и проверить Data fallback вместо memory restore.
7. Проверить external volume после revert на temporal mismatch.

## Антипаттерны

- **Pause считать durable.** Node-local checkpoint не переживает потерю node.
- **Full считать транзакцией.** External systems остаются снаружи.
- **Retry без чтения status.** Timeout мог скрыть успех.
- **Probe всей системы.** Downstream outage создаёт crashed Actors.
- **Долгий RUNNING без commits.** Eviction grace не задаёт RPO.
- **Обновлять template и ждать hot restore.** Другой UID меняет compatibility path.
- **Хранить per-Actor secret в golden.** Клоны разделят credential.

## Production checklist lifecycle

- Для template выбраны `onPause`/`onCommit` и объяснён выбор.
- Измерены size, upload, restore и cold boot.
- Определены idle horizons RUNNING -> PAUSED -> SUSPENDED.
- Template Ready является gate для CreateActor.
- Probe проверяет локальную готовность.
- Приложение переподключает clients и обновляет credentials.
- External side effects имеют idempotency/reconciliation.
- RPO задаётся частотой durable checkpoints.
- Dashboards разделяют local, Full, Data, golden и cold boot.
- Fault injection покрывает restart, потерю node и partial upload.

## Итог

Pause быстро освобождает Worker, но привязывает Actor к node-local checkpoint. Suspend создаёт переносимую durable точку. Resume выбирает local, actor-owned, borrowed golden snapshot или cold boot. Revert возвращает последнюю подтверждённую точку, но не откатывает внешний мир.

Инженер выбирает consistency model: что можно потерять, как долго хранить memory, где проходит idempotency boundary и какой latency budget доступен первому запросу. В следующей главе проследим этот запрос через routing, request parking, mTLS tunnel и egress.

### Источники и дальнейшее чтение

- [Substrate API guide](https://github.com/agent-substrate/substrate/blob/main/docs/api-guide.md)
- [Agent Substrate architecture](https://github.com/agent-substrate/substrate/blob/main/docs/architecture.md)
- [Agent Substrate glossary](https://github.com/agent-substrate/substrate/blob/main/docs/glossary.md)
- [Request parking design](https://github.com/agent-substrate/substrate/blob/main/docs/request-parking.md)
- [Rolling upgrade runbook](https://github.com/agent-substrate/substrate/blob/main/docs/upgrade.md)
- [ate API lifecycle definitions](https://github.com/agent-substrate/substrate/blob/main/pkg/proto/ateapipb/ateapi.proto)
