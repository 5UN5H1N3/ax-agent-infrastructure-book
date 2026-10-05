# Actor model для агентных workload: identity, state и lifecycle

В конце предыдущей главы мы пришли к устойчивой identity, operation ID и reconciliation. Actor model отвечает на следующий вопрос: **вокруг какой сущности собрать состояние и последовательность решений, если физический процесс может исчезать и появляться снова?**

Для агента такой сущностью часто становится разговор, рабочая сессия, пользовательский помощник или автономный исполнитель. Он живёт логически дольше одного HTTP-запроса и дольше одного Pod. Но слово *Actor* перегружено: в классической модели это вычислительная сущность с mailbox; в virtual actor runtime - логическая identity с управляемой activation; в Agent Substrate - stateful workload, который можно suspend и resume на разных Workers. Эти идеи родственны, но не взаимозаменяемы.

Если не провести границу, легко приписать платформе несуществующие гарантии: решить, что Substrate автоматически сериализует сообщения, что snapshot является очередью, а Actor обязательно имеет supervision tree. Эта глава сначала разбирает исходную модель, затем virtual actors и только после этого показывает, что именно переносится в инфраструктуру AX/Substrate.

## Что читатель должен унести из главы

После этой главы инженер должен уметь:

- объяснить Actor через identity, private state, behavior и messages, а не через «маленький сервис»;
- отличить logical Actor от activation, process, Worker и Pod;
- выбрать правильную границу Actor для agent session или автономной задачи;
- понять, какие гарантии даёт mailbox и какие приходится строить отдельно;
- спроектировать suspend/resume без потери in-flight work и внешних side effects;
- увидеть hot Actor, unbounded queue, reentrancy и snapshot coupling до production;
- решить, когда Actor model упрощает систему, а когда становится лишним слоем.

## Три значения слова Actor

| Контекст | Что называется Actor | Что обычно предоставляет runtime |
| --- | --- | --- |
| Классическая actor model | identity + private state + behavior + mailbox | message dispatch, turns, references, lifecycle, иногда supervision |
| Virtual actor | логическая сущность, адресуемая даже без активного процесса | activation/deactivation, placement, directory, persistence hooks |
| Agent Substrate | экземпляр stateful sandboxed workload | Worker assignment, routing, memory/disk snapshot, suspend/resume |

Общий мотив - отделить **кто это** от **где он сейчас выполняется**. Различие - уровень абстракции. Классический Actor определяет программную модель обработки сообщений. Substrate управляет целым окружением процесса. Внутри Substrate Actor может работать приложение на Python, Go или Rust, и оно не становится классическим message actor автоматически.

## Классическая модель: маленький владелец состояния

Actor model появилась как способ рассуждать о конкурентных вычислениях без общего изменяемого состояния. Actor получает сообщение и в ответ может:

1. отправить конечное число сообщений известным ему Actors;
2. создать новые Actors;
3. выбрать behavior для обработки следующего сообщения.

Полезная бытовая аналогия - отдельный кабинет с одним ответственным сотрудником. Снаружи нельзя открыть его ящики и поправить документы. Можно только положить запрос во входящую корзину. Сотрудник разбирает запрос, изменяет свои записи и отправляет новые поручения. Адрес кабинета остаётся известным, даже если конкретного сотрудника временно заменили.

### Identity и Actor reference

Внешний код обращается не к объекту в памяти, а к reference или logical address. Эта indirection позволяет переместить activation, перезапустить процесс или восстановить state, не раздавая всем новый физический адрес. Ссылка отвечает на вопрос «какому Actor предназначено сообщение», но не обязана раскрывать host, thread или текущий lifecycle state.

Для agent platform identity должна переживать restart. Хороший ключ выводится из domain boundary: `tenant/session`, `workspace/agent` или `job/executor`. IP, Pod name и process ID для этого не подходят: это адреса временного воплощения.

### Private state и behavior

Actor владеет своим изменяемым state. Другие Actors не читают его напрямую, а запрашивают операцию сообщением. Благодаря этому инварианты сосредоточены в одном месте. Например, Session Actor может гарантировать, что номер следующего шага монотонно растёт и одновременно существует не более одного active tool call.

Behavior - функция реакции на сообщение в текущем состоянии. Один и тот же `SubmitInput` может приниматься в состоянии `WaitingForUser`, отклоняться в `RunningTool` и превращаться в no-op после `Completed`. Поэтому protocol Actor удобнее описывать как state machine, а не как набор не связанных RPC methods.

### Mailbox: очередь не равна гарантии

Mailbox отделяет отправителя от получателя во времени. Отправитель помещает сообщение и не получает право войти во внутренний state Actor. Многие реализации обрабатывают по одному message turn на Actor, что уменьшает потребность в locks.

Но слово mailbox ничего не обещает само по себе. Нужно отдельно выяснить:

- очередь in-memory или durable;
- она bounded или может расти до исчерпания памяти;
- каков порядок для разных senders;
- что происходит при restart, relocation и termination;
- какая delivery semantics: at-most-once или at-least-once;
- есть ли acknowledgement на уровне бизнес-результата.

Например, Akka core по умолчанию формулирует базовую гарантию как at-most-once delivery и ordering для пары sender-receiver. Усиление до retry требует message ID, acknowledgement и deduplication. Это тот же вывод, к которому мы пришли в главе 10: transport не может придумать бизнес-значение «успешно обработано».

### Один turn за раз - не магическая защита

Последовательная обработка упрощает state, но не делает систему полностью свободной от concurrency. Пока Actor ждёт внешний API, runtime может запрещать interleaving, тогда длинный вызов блокирует mailbox. Либо runtime разрешает reentrancy, тогда continuation одного запроса может выполниться после части другого, и инварианты снова требуют внимания.

Показательный пример: Actor прочитал `balance=100`, ушёл в `await`, другой turn изменил balance до 40, а первый продолжил работу с устаревшим предположением. «Один thread» не спасает, если turns перемежаются. Правило простое: после await нужно заново проверить state/version или явно запретить interleaving для критического protocol.

![Actor ≠ Worker: logical population can be larger than active capacity](.gitbook/assets/diagrams/11-11.png)  
*Actor ≠ Worker: logical population can be larger than active capacity*

## Actor, activation, Worker и Pod

| Сущность | Срок жизни | Что в ней устойчиво | Можно заменить без смены Actor identity? |
| --- | --- | --- | --- |
| Actor | domain lifecycle | logical ID и lineage state | нет, это сама identity |
| Activation | период активного выполнения | in-memory embodiment | да |
| Worker | период доступной capacity | sandbox slot/runtime environment | да |
| Pod | Kubernetes workload lifecycle | контейнеры и network namespace | да |

Главная мысль рисунка: logical population может быть намного больше active capacity. Тысяча зарегистрированных agent sessions не требует тысячу постоянно работающих Pods, если одновременно активны только десятки. Runtime держит warm Workers, а Actors получают физическое воплощение по требованию.

## Virtual actor: identity существует дольше activation

Классическая модель обычно заставляет явно создавать и останавливать Actors. Virtual actor runtime делает ещё один шаг: logical Actor считается всегда адресуемым, а runtime сам создаёт activation при обращении и удаляет её после периода бездействия. В Orleans такая сущность называется grain: у неё есть user-defined key, behavior и state, а runtime отвечает за placement, location и activation lifecycle.

Это похоже на виртуальную память. Программа обращается к логическому адресу, не решая, находится ли нужная страница сейчас в RAM. Клиент обращается к logical Actor ID, не решая, на каком host живёт activation и существует ли она в памяти прямо сейчас.

### Directory и location transparency

Чтобы reference оставалась стабильной, runtime нужен directory: отображение `Actor ID -> current activation location`. При activation запись создаётся, при relocation меняется, при failure восстанавливается. Directory находится на критическом пути и определяет, насколько строго система предотвращает две одновременные activations одной identity.

Абсолютная location transparency удобна, но не отменяет физику. Remote call медленнее local call, serialization имеет цену, partition существует, а state может быть расположен далеко. Программный интерфейс может скрывать адрес, но observability и placement policy должны видеть topology.

### Activation и deactivation

Activation - временное воплощение Actor на вычислительном узле. При первом обращении runtime загружает state, создаёт instance и направляет туда сообщения. После idle period activation можно убрать из памяти. Следующий вызов снова активирует ту же logical identity, возможно на другом узле.

Deactivation не должна восприниматься как domain deletion. Пользовательская сессия всё ещё существует; исчезает только её горячее представление. И наоборот, удаление Actor требует отдельного решения о retention, audit и dependent resources.

## Что именно является Actor в Agent Substrate

Agent Substrate переносит идею virtual identity на более низкий уровень. Его Actor - это не объект класса внутри runtime, а экземпляр sandboxed application environment, связанный с ActorTemplate и snapshot lineage. Когда Actor активен, он назначен Worker и имеет работающие процессы. Когда suspended, durable snapshot остаётся, а Worker освобождается.

Текущая архитектура описывает следующий путь:

1. создаётся Actor record со стабильной identity и ссылкой на ActorTemplate;
2. первоначально Actor может находиться в suspended state и ссылаться на golden snapshot;
3. входящий запрос или явный вызов инициирует resume;
4. control plane атомарно выбирает свободный Worker;
5. node-side компоненты восстанавливают memory и disk state в sandbox;
6. routing layer направляет запрос на текущий Worker;
7. при suspend создаётся новый snapshot, assignment освобождается, Worker возвращается в pool.

Это очень похоже на virtual actor activation, но есть принципиальная оговорка: Substrate управляет **жизнью окружения**, а не обязательно mailbox и turn scheduling приложения внутри. Если HTTP server внутри Actor одновременно принимает 50 requests, Substrate сам по себе не превращает их в 50 последовательных turns.

### Что Substrate даёт, а что остаётся приложению

| Задача | Substrate/runtime layer | Agent application layer |
| --- | --- | --- |
| stable Actor identity | resource ID и control-plane record | domain mapping session/user/job |
| physical placement | Worker assignment и routing | не должен хранить Worker IP как identity |
| state capture | memory + writable disk snapshot | quiescence и consistency внешних данных |
| request concurrency | transport до workload | mailbox, locks, turn model или собственный scheduler |
| business retry | может восстановить workload | idempotency, operation journal, deduplication |
| failure policy | crash detection, revert/resume primitives | что повторить, отменить, компенсировать или показать человеку |

Эта таблица не обесценивает runtime. Наоборот, она делает контракт честным. Substrate решает тяжёлую инфраструктурную задачу - быстрое физическое воплощение stateful workload. Приложение решает semantic задачу - какой запрос можно выполнять, в каком порядке и что считать завершением.

## Как выбрать границу Actor для агента

Actor должен владеть набором state и инвариантов, которые естественно изменяются последовательно. Слишком крупная граница создаёт hot spot; слишком мелкая превращает простое решение в распределённую транзакцию.

| Кандидат | Когда хорош | Главный риск |
| --- | --- | --- |
| один Actor на user | персональный assistant с единым долгим контекстом | все сессии пользователя конкурируют за один Actor |
| один Actor на conversation/session | диалоги изолированы и имеют собственную memory | общие user preferences требуют отдельного store |
| один Actor на task | автономная работа с ясным началом и завершением | много коротких Actors и дорогая activation |
| один Actor на workspace/project | общий mutable environment и files | hot key, длинный mailbox, большая snapshot |
| Actor на tool/resource | нужно сериализовать доступ к конкретному устройству или account | cross-actor workflow и возможные циклы ожидания |

Практический тест границы:

- можно ли сформулировать один набор инвариантов, которым владеет Actor;
- можно ли обрабатывать большинство операций без синхронного вызова другого Actor;
- помещается ли working set в разумный snapshot и activation budget;
- есть ли естественная стабильная identity;
- допустима ли последовательная пропускная способность одной activation;
- понятно ли, когда Actor idle, suspended и deleted.

### Пример: conversation Actor

Пусть Actor представляет одну беседу. Он владеет текущим plan, краткой memory, списком разрешённых tools, последним подтверждённым step и ссылками на artifacts. Команды могут выглядеть так:

```
UserMessage(text, request_id)
ToolResult(call_id, payload_ref)
Approve(step_id, approver)
Cancel(reason, request_id)
IdleTimeout(generation)
```

Behavior зависит от state: `WaitingForUser`, `Planning`, `WaitingForTool`, `WaitingForApproval`, `Completed`. `request_id` делает повтор безопасным, а `step_id` не позволяет позднему approval подтвердить уже заменённый plan. Большие tool outputs хранятся по reference, чтобы не раздувать mailbox и snapshot.

## State: что именно должно пережить activation

У агента нет одного монолитного «состояния». Полезно разделить как минимум четыре слоя:

1. **Definition state.** Image, dependencies, sandbox policy и resources из ActorTemplate. Это версия кода и окружения.
2. **Execution state.** RAM, stack, локальные процессы и writable filesystem, которые может захватить snapshot.
3. **Domain state.** Conversation history, approvals, operation journal, artifact metadata. Часто ему лучше жить в явной durable schema.
4. **External state.** Объекты в SaaS, cloud, GitHub, email или payment system. Snapshot ими не владеет.

Snapshot особенно удобен для сложного execution state: прогретая model library, interpreter, process tree, cache, локальный checkout. Но snapshot не заменяет domain model. Через месяц инженер должен уметь узнать, почему задача ждёт approval, не поднимая старую память процесса и не исследуя heap.

### Snapshot - не распределённая транзакция

Представим, что agent отправил внешний запрос, получил success, но ещё не записал его в локальный journal. Snapshot в этот момент сохранит неполную картину. После resume agent может повторить side effect. Обратный сценарий тоже возможен: journal говорит `in-flight`, а сетевой ответ уже потерян.

Поэтому перед suspend приложение должно перейти в согласованную точку:

- перестать принимать новую работу или пометить её для следующей activation;
- дождаться безопасных in-flight turns либо явно их отменить;
- сбросить buffers и durable записать operation state;
- закрыть или подготовить к пересозданию connections;
- зафиксировать snapshot generation/version;
- только затем разрешить platform checkpoint.

Для операций, которые нельзя спокойно остановить, Actor должен оставаться running или использовать protocol, способный продолжить работу после неизвестного результата. Freeze процесса не превращает внешний мир в часть checkpoint.

### Что обычно не переживает resume

После relocation нельзя рассчитывать на прежний IP, TCP connection, PID namespace, kernel timer или mounted credential lifetime. DNS caches, OAuth tokens и database pools могут протухнуть, а remote peer уже закрыл connection. Хороший workload имеет resume hook или lazy reinitialization:

```
on_resume(snapshot_generation):
    refresh_identity_if_needed()
    recreate_network_pools()
    validate_external_leases()
    recheck_inflight_operations()
    publish_ready_only_after_validation()
```

Readiness после restore должна означать не «процесс разморожен», а «application dependencies проверены и новые requests безопасны».

## Concurrency: где действительно выполняется сериализация

Actor model часто выбирают, чтобы избежать fine-grained locks. Это работает только при явном месте сериализации. Возможны три варианта:

- **Runtime mailbox.** Framework выдаёт приложению один message turn за раз.
- **Application queue.** HTTP/gRPC handlers складывают команды во внутренний bounded channel, который читает один state owner.
- **Storage serialization.** Несколько handlers работают параллельно, но обновляют state через version/transaction.

Substrate Actor совместим со всеми тремя, но не выбирает вариант за приложение. Если agent server многопоточный, snapshot одного sandbox не означает single-threaded semantics. Архитектурная документация должна прямо назвать owner state и точку сериализации.

### Reentrancy, cycles и deadlock

Два Actors могут синхронно вызвать друг друга и оба ждать ответа. При строгой обработке «запрос до конца» получается deadlock. Reentrancy позволяет обработать новый turn во время await, но возвращает риск логической гонки. Часто безопаснее изменить protocol:

- использовать asynchronous reply message вместо вложенного blocking call;
- разбить долгую операцию на state transitions;
- хранить correlation ID и продолжать после отдельного result message;
- не строить циклические цепочки синхронных Actor calls;
- после каждого await валидировать generation и current state.

## Delivery semantics и protocol messages

Actor reference создаёт удобную адресацию, но сообщение остаётся распределённой операцией. Оно может потеряться до mailbox, обработаться до потери reply или повториться из-за retry. Поэтому protocol проектируется так же строго, как control-plane API:

- command имеет message ID и expected generation;
- receiver сохраняет deduplication record или делает effect идемпотентным;
- business acknowledgement сообщает не «байты дошли», а «переход state выполнен»;
- deadline ограничивает ожидание sender;
- late reply проверяется по correlation ID и current state;
- dead letters служат диагностике, а не гарантии доставки.

Ordering обычно гарантируется только в узкой границе. Даже если сообщения одного sender приходят по порядку, сообщения от разных senders могут перемежаться. Посредник, retry или relocation также меняют наблюдаемую последовательность. Если порядок важен, protocol несёт sequence/generation и отклоняет stale message.

## Supervision: восстановить процесс недостаточно

В Erlang/OTP и Akka Actor hierarchy связывает child lifecycle с supervisor strategy. Supervisor решает, перезапускать ли один child, зависимую группу или останавливать ветку. Это воплощает принцип «let it crash» только при важном условии: state можно безопасно пересоздать или восстановить.

На agent platform существует несколько уровней supervision:

| Уровень | Что обнаруживает | Что способен восстановить |
| --- | --- | --- |
| application actor runtime | exception/invalid behavior | behavior instance, mailbox strategy, child hierarchy |
| process supervisor | process exit | process и локальную конфигурацию |
| Substrate | Actor/Worker lifecycle failure | sandbox из последнего завершённого snapshot |
| Kubernetes | container/Pod/node failure | Worker infrastructure и platform components |
| AX control plane | Task не достиг desired state | workflow step через reconciliation |

Каждый верхний слой должен понимать семантику нижнего recovery. Если Kubernetes перезапустил Worker, это не доказывает, что Actor side effect можно повторить. Если Substrate восстановил snapshot, это не означает, что потерянные после snapshot messages появятся снова. Infrastructure recovery возвращает вычислительную способность; business recovery возвращает корректность.

## Backpressure и hot Actors

Последовательный owner state имеет конечную пропускную способность. Если входящий поток быстрее обработки, mailbox растёт. Unbounded mailbox превращает временную перегрузку в memory exhaustion с большой задержкой, когда пользователю уже не нужен результат.

Нужны явные controls:

- bounded queue и понятное поведение при заполнении;
- per-tenant и per-Actor admission limits;
- deadline/TTL сообщения, чтобы не исполнять просроченную работу;
- метрики queue depth, oldest age и service time;
- отдельные limits для user commands и background events;
- приоритет control messages, но без starvation обычной работы.

Hot Actor нельзя бесконечно ускорять добавлением Workers: одна logical identity по определению остаётся одной точкой владения state. Варианты - уменьшить границу Actor, вынести read-only work, разделить state на независимые partitions или использовать несколько aggregator stages. Если инвариант требует глобальной последовательности, предел throughput является ценой этого инварианта.

## Security и multi-tenancy

Actor boundary полезна для reasoning, но не является security boundary автоматически. Два Actors в одном process могут иметь логически раздельный state и всё же разделять memory address space. На уровне Substrate изоляцию обеспечивает sandbox class и platform configuration из главы 8.

Особого внимания требуют snapshots. Они могут содержать prompts, credentials, tool outputs, source code и остатки process memory. Поэтому нужны encryption, tenant-scoped authorization, integrity verification, lifecycle retention и audit. Route также является security-sensitive mapping: запрос Actor A не должен попасть в activation Actor B после быстрого reassignment Worker.

Identity внутри восстановленного snapshot требует проверки. Клонирование golden state не должно переносить уникальный actor token, telemetry identity или lease прежнего экземпляра. После resume runtime/application должны привязать окружение к текущим Actor ID, generation и short-lived credentials.

## Наблюдаемость Actor lifecycle

Логи только по Pod name теряют связь после relocation. Основная correlation identity - Actor ID, дополненная activation ID, Worker ID и snapshot generation. Минимальный набор telemetry:

- Actor lifecycle state и время последнего перехода;
- activation/resume latency по этапам: lookup, assignment, download, restore, readiness;
- suspend latency, snapshot size и upload throughput;
- Actor ID, activation ID, Worker/Pod и template version;
- request/message ID, mailbox depth, oldest message age и processing time;
- число retries, duplicates, stale messages и dead letters;
- частота crashes, reverts и повторных activations;
- доля warm Workers и ожидание capacity.

Следует различать **actor unavailable** и **actor cold**. Cold Actor может быть здоров, но требовать resume. SLO полезно разложить на warm-request latency, cold-resume latency и долю requests, которые встретили activation.

## Когда Actor model подходит

Модель особенно сильна, когда система состоит из большого числа независимо адресуемых stateful entities:

- chat/conversation sessions;
- autonomous tasks с долгим ожиданием;
- user assistants с персональной memory;
- digital twins, game entities, devices;
- workflow coordinators, владеющие небольшой state machine;
- изолированные development workspaces, которые большую часть времени idle.

Общий признак: state естественно partitioned по identity, requests к одной identity удобно сериализовать, а разные identities работают независимо. Тогда Actor boundary уменьшает область locks и failure, а virtual activation экономит capacity.

## Когда лучше выбрать другой инструмент

| Задача | Почему Actor может мешать | Что рассмотреть |
| --- | --- | --- |
| stateless transformation | identity и lifecycle ничего не добавляют | обычный service/worker pool |
| массовая аналитика по всем entities | fan-out по миллионам Actors дорог | warehouse, stream processor, batch engine |
| сложные cross-entity transactions | много coordination messages и partial failures | relational transaction boundary |
| общий большой mutable dataset | неясно, какой Actor владеет state | database/service с явной concurrency model |
| очень короткие независимые jobs | activation и snapshot overhead больше работы | queue + ephemeral workers |
| одна глобальная identity с огромным QPS | Actor становится serial bottleneck | partitioning, CRDT, sharded data model |

Не следует превращать каждую таблицу БД в Actor. Модель ценна там, где behavior и lifecycle принадлежат entity, а не просто потому, что у строки есть primary key.

## Антипаттерны

- **Actor = Pod.** Logical identity оказывается привязана к расходному infrastructure object.
- **Actor = thread.** Теряется multiplexing; тысячи idle entities требуют тысячи threads.
- **Snapshot = database.** Domain state становится непрозрачным и плохо мигрируемым.
- **Mailbox без limit.** Перегрузка маскируется до OOM и многоминутной latency.
- **Синхронные циклы Actor calls.** Последовательная обработка превращается в deadlock.
- **Resume сразу Ready.** Приложение получает трафик до пересоздания connections и проверки leases.
- **Worker IP как identity.** Relocation ломает references и направляет запросы устаревшей activation.
- **«Один Actor на всё».** Глобальная state machine становится hot spot и общей failure domain.
- **Вера в автоматическую exactly-once.** Message delivery, snapshot и внешний side effect остаются разными границами.

## Практический чек-лист Actor design

1. Какую domain entity представляет Actor и как выглядит стабильный ID?
2. Какими инвариантами Actor владеет единолично?
3. Где находится точка сериализации: mailbox, application queue или storage?
4. Разрешена ли reentrancy и что происходит после await?
5. Mailbox bounded? Что происходит при overflow и expired message?
6. Каковы delivery, ordering, acknowledgement и deduplication semantics?
7. Какой state живёт в snapshot, database, object store и внешних системах?
8. Как workload достигает quiescent point перед suspend?
9. Что пересоздаётся при resume и когда публикуется readiness?
10. Как предотвращаются stale route и duplicate activation?
11. Как разделены application supervision, Substrate recovery и Kubernetes restart?
12. Как обнаружить hot Actor и когда менять partitioning?
13. Какие данные snapshot содержит и кто имеет право их восстановить?
14. Как Actor version/template обновляется без несовместимого state?
15. Когда Actor удаляется и какой retention остаётся после него?

## Итог

- Actor - это прежде всего logical identity и владелец state/behavior, а не process или Pod.
- Классическая actor model добавляет message protocol и mailbox; virtual actor добавляет автоматическую activation и location management.
- Agent Substrate применяет virtual-identity идею к sandboxed workload, но не навязывает приложению классическую mailbox semantics.
- Suspend/resume экономит capacity только при явном quiescence protocol и разделении snapshot, domain state и external state.
- Последовательная обработка упрощает инварианты, но создаёт throughput ceiling, hot-key risk и вопросы reentrancy.
- Infrastructure recovery возвращает Actor к вычислению; бизнес-корректность возвращают idempotency, journal и protocol из главы 10.

**Дальше.** Теперь Actor, activation и Worker разведены концептуально. Следующая глава рассматривает конкретную operational architecture Agent Substrate: ateapi, atecontroller, atelet, ateom, atenet, PostgreSQL, object storage и путь запроса от gateway до восстановленного sandbox.

### Источники и дальнейшее чтение

- [Carl Hewitt: Actor Model of Computation](https://arxiv.org/abs/1008.1459) - обзор Actor model автором исходной идеи.
- [Akka: What is an Actor?](https://doc.akka.io/libraries/akka-core/current/general/actors.html) - state, behavior, reference, mailbox и supervision.
- [Akka: Message Delivery Reliability](https://doc.akka.io/libraries/akka-core/current/general/message-delivery-reliability.html) - at-most-once, ordering, ACK/retry и deduplication.
- [Microsoft Orleans Overview](https://learn.microsoft.com/en-us/dotnet/orleans/overview) - virtual actors, stable identity и managed activation.
- [Orleans Request Scheduling](https://learn.microsoft.com/en-us/dotnet/orleans/grains/request-scheduling) - single-threaded turns, interleaving и reentrancy.
- [Orleans Grain Directory](https://learn.microsoft.com/en-us/dotnet/orleans/host/grain-directory) - mapping logical identity на activation location.
- [Erlang/OTP Supervisor Behaviour](https://www.erlang.org/doc/system/sup_princ.html) - supervision trees и restart strategies.
- [Agent Substrate Architecture](https://github.com/agent-substrate/substrate/blob/main/docs/architecture.md) - Actor/Worker lifecycle, routing и snapshot state.
- [Agent Substrate Security](https://github.com/agent-substrate/substrate/security) - cross-Actor isolation, routing, identity и snapshot risks.
