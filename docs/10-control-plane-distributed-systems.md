# Control plane и distributed systems: как проектировать операции, которые переживают сбои

Предыдущая глава показала механику Kubernetes reconciliation: как intent проходит через API, scheduler и kubelet до работающего процесса. Но даже если каждый компонент настроен правильно, между ними остаётся сеть, а сеть не даёт контроллеру простой гарантии: «запрос не выполнен» или «запрос выполнен». Иногда единственный честный ответ - **результат неизвестен**.

Именно с этого начинается инженерия control plane. AX и Substrate - самостоятельные распределённые системы поверх Kubernetes. Они принимают intent, сохраняют состояние, запускают длительные операции и наблюдают за результатом. Процесс может завершиться между side effect и записью status; ответ может потеряться; два экземпляра контроллера могут одновременно увидеть одну работу; старое событие может прийти после нового. Надёжность появляется не из одной удачной библиотеки, а из согласованного протокола восстановления.

## Как складываются главы 8-10

| Глава | Главный вопрос | Инженерное решение |
| --- | --- | --- |
| 8. Runtime isolation | от чего и насколько сильно изолировать workload? | process, container, gVisor или microVM по threat model |
| 9. Kubernetes | где и как разместить выбранный runtime? | Pod, controller, scheduling, сеть, storage и RuntimeClass |
| 10. Control plane | что произойдёт при retry, delay, restart и частичном side effect? | status, idempotency, operation ID, versioning, deadline и recovery |

Эти вопросы нельзя переставить местами. Kubernetes не усиливает слабую isolation boundary, а microVM не делает control-plane operation идемпотентной. Граница безопасности отвечает на вопрос «что сможет повредить скомпрометированный workload». Control plane отвечает на другой вопрос: «сойдётся ли система к правильному состоянию, если любой шаг задержится, повторится или оборвётся посередине».

## Что читатель должен унести из главы

После этой главы инженер должен уметь:

- отделить принятие intent от фактической готовности ресурса;
- описать операцию как конечный автомат и reconciliation loop, а не как длинную цепочку RPC;
- выбрать idempotency key, operation ID и место для durable journal;
- понять, когда нужен optimistic concurrency, lock, lease или fencing token;
- настроить retry с deadline, backoff, jitter и retry budget;
- объяснить роли PostgreSQL, Redis Streams и gRPC без идеи «один инструмент решит всё»;
- провести восстановление после неизвестного результата внешнего side effect.

## Control plane - не обычный request handler

Обычный HTTP handler часто мыслится линейно: получил запрос, проверил данные, вызвал зависимость, вернул ответ. Для короткой операции в одном процессе этого иногда достаточно. Control plane управляет ресурсами, которые живут дольше одного запроса: Actor, Worker, snapshot, task, route, volume или sandbox. Поэтому полезнее видеть в нём три части:

- **API** принимает intent и выдаёт стабильную identity ресурса или операции;
- **durable state** хранит spec, текущий status, версии и историю значимых переходов;
- **reconciler** многократно сравнивает desired и observed state и делает один безопасный следующий шаг.

Пользователь просит «создать Actor». API может быстро проверить контракт, записать ресурс и ответить `accepted`. Затем отдельный reconciler выбирает Worker, резервирует capacity, создаёт sandbox, восстанавливает snapshot, ждёт readiness и публикует route. Если controller перезапустится после любого шага, новая replica должна продолжить не из памяти умершего процесса, а из durable state и наблюдаемого мира.

### Команда, событие, состояние и status - не одно и то же

| Сущность | Смысл | Пример | Главный риск |
| --- | --- | --- | --- |
| Command | просьба выполнить действие | `ResumeActor(A)` | может быть доставлена повторно |
| Event | утверждение о свершившемся факте | `SnapshotRestored` | может опоздать или прийти не по порядку |
| Desired state | устойчивый intent владельца | `actor.spec.phase = Running` | может измениться во время операции |
| Observed status | последнее подтверждённое наблюдение | `Ready=False, reason=Restoring` | всегда немного отстаёт от реальности |

Если очередь содержит только команды, а durable state отсутствует, после сбоя трудно ответить, что система уже сделала и чего всё ещё хочет пользователь. Если хранить только события, но не определить правила переходов, журнал превращается в поток фактов без ясного текущего контракта. Практичный control plane обычно сочетает декларативный spec, вычисляемый status и поток работ, который будит reconciler.

## Модель частичного отказа: шесть неприятных вариантов

«Сервис упал» - слишком грубое описание. Для проектирования операции полезно перечислить, что именно наблюдает вызывающая сторона.

1. **Задержка.** Ответ ещё не пришёл. Операция может продолжаться штатно.
2. **Потеря ответа.** Side effect выполнен, но клиент увидел timeout. Это состояние unknown, а не failure.
3. **Повтор.** Клиент, брокер или controller доставил ту же команду снова.
4. **Переупорядочивание.** Событие о старой generation пришло после события о новой.
5. **Crash/restart.** Процесс потерял volatile context между двумя durable записями.
6. **Partition и split view.** Компоненты доступны по отдельности, но не могут согласовать актуальное состояние.

К этому добавляются clock skew и паузы процесса. Поэтому wall-clock timestamp не годится как единственный арбитр владения: часы могут расходиться, а holder lease может «замереть» на долгой паузе и вернуться уже после выдачи нового lease. Версия ресурса, monotonic sequence и fencing token обычно надёжнее предположения «у кого время новее, тот прав».

## Accepted, ready и completed

Успешный API response отвечает только на вопрос своего уровня. Например, `202 Accepted` означает, что intent принят в обработку, но не что Actor уже доступен. Даже синхронный `200` от внутреннего RPC может означать лишь успешную запись в локальную базу. У ресурса полезно различать как минимум:

- **accepted** - intent проверен и durable сохранён;
- **progressing** - система выполняет шаги и знает, что осталось;
- **ready** - ресурс может обслуживать целевой трафик сейчас;
- **completed** - конечная операция завершена и повторять её не требуется;
- **failed** - достигнуто терминальное состояние с понятной причиной;
- **unknown** - свежего подтверждения нет; это не синоним `false`.

Клиенту нужен resource ID или operation ID, endpoint чтения status и, при необходимости, watch/stream. Таймаут ожидания клиента не должен отменять сам intent по умолчанию: он только прекращает ожидание. Отмена - отдельная команда с собственным статусом и конфликтами.

### Conditions, generation и наблюдаемая версия

Одного поля `phase` быстро становится мало. Actor может одновременно быть `Progressing=True`, `Ready=False` и `Degraded=True`. Condition должна не только нести boolean, но и отвечать «почему»: type, status, reason, message, время перехода и generation, которую оценивал controller.

`observedGeneration` защищает клиента от ложной уверенности. Если spec уже generation 12, а status вычислен для generation 11, зелёный `Ready=True` относится к старому intent. Аналогично, version/resourceVersion позволяет controller обнаружить конкурентное изменение и повторить read-modify-write вместо молчаливого перетирания данных.

Watch является оптимизацией доставки изменений, а не единственным источником истины. Соединение обрывается, история может быть сокращена, cursor устаревает. Надёжный consumer умеет заново выполнить list/read, построить актуальный snapshot и продолжить watch с новой версии.

## Reconciliation loop: один безопасный шаг за раз

Reconciler не обязан завершать весь lifecycle за один вызов. Напротив, короткий и повторяемый проход проще тестировать и восстанавливать. Его форма примерно такая:

```
reconcile(resource_id):
    desired = store.read_spec(resource_id)
    observed = inspect_runtime(resource_id)

    if desired.generation is older than observed.intent_generation:
        return  # устаревшая работа

    next_step = plan(desired, observed)

    if next_step is none:
        update_conditions(resource_id, Ready=True)
        return

    operation_id = stable_id(resource_id, desired.generation, next_step)
    result = execute_idempotently(next_step, operation_id)
    persist_result_and_status(result)
    enqueue_recheck(resource_id)
```

Ключевые слова здесь - **inspect**, **stable\_id** и **persist**. Reconciler сначала выясняет наблюдаемую реальность, затем связывает действие со стабильной identity и только после этого обновляет status. Если шаг долгий, его лучше представить отдельной operation record со своим lifecycle, а не держать lock и RPC connection несколько минут.

### Удаление тоже требует reconciliation

Удалить строку из API store легко, но тогда исчезает место, где отмечено, что надо удалить sandbox, route, snapshot lease и external credential. Поэтому deletion обычно двухфазный: пользователь ставит deletion intent, finalizer удерживает ресурс, controller освобождает зависимые объекты и лишь затем удаляет finalizer. Finalizer не делает cleanup безошибочным; он делает обязанность cleanup видимой и повторяемой.

Для каждого finalizer нужен аварийный runbook. Если внешняя система недоступна навсегда, ресурс может зависнуть в terminating. Ручное снятие finalizer допустимо только после понимания, какие orphaned resources и расходы останутся.

## Idempotency: повтор не должен умножать эффект

Идемпотентная операция при повторе имеет тот же *intended effect*, что и один вызов. Это не означает, что ответ, latency или audit log будут одинаковыми. `EnsureActorRunning(A)` естественно ближе к идемпотентности, чем `CreateAnotherActor()`. Хороший API формулирует intent через стабильную identity: «обеспечь Actor A в состоянии Running», а не «ещё раз запусти что-нибудь».

Для команды с side effect клиент генерирует idempotency key или operation ID и повторяет запрос с тем же ключом. Сервер атомарно связывает ключ с fingerprint запроса и результатом:

- тот же ключ и тот же payload возвращают прежний result или текущий status;
- тот же ключ и другой payload дают conflict, а не новую операцию;
- новый ключ означает новый намеренный effect.

Запись deduplication должна жить не меньше максимального окна повторов. Если сервер забывает ключ через час, а очередь может повторить сообщение через сутки, гарантия исчезает. Полезны unique constraint на `(tenant_id, operation_id)` и сохранённый request hash. Проверка «сначала SELECT, потом INSERT» без транзакции оставляет race; уникальность должна обеспечиваться storage.

### Миф об exactly-once

В распределённой системе нельзя просто объявить «exactly once» между независимыми хранилищами и внешним миром. Если controller выполнил side effect, но упал до записи `done`, следующий процесс видит незавершённую запись и не знает, повторять ли действие. Практическая стратегия обычно состоит из:

- at-least-once delivery;
- idempotent consumer или deduplication key;
- durable operation journal;
- запроса фактического состояния внешней системы;
- компенсирующего действия, когда истинная атомарность невозможна.

Даже «exactly-once processing» конкретного брокера не распространяется автоматически на email, cloud API, платёжный шлюз или файловую систему. Всегда спрашивайте: exactly once в какой границе и относительно какого durable state?

## Retry - управляемая нагрузка, а не рефлекс

Retry полезен только при временной ошибке и безопасной повторяемости. Он опасен при validation error, permanent permission denial и неизвестной семантике side effect. Перед включением автоматического retry операция должна ответить на четыре вопроса:

1. Есть ли общий deadline, после которого результат уже не нужен?
2. Какие коды действительно transient для этого метода?
3. Безопасен ли повтор или есть устойчивый idempotency key?
4. Сколько дополнительной нагрузки допустимо создать во время инцидента?

Exponential backoff разводит повторы во времени, jitter не даёт тысячам клиентов проснуться одновременно, attempt limit ограничивает один запрос, а retry budget ограничивает систему целиком. Без budget деградация зависимости вызывает retry storm: каждый timeout создаёт несколько новых вызовов, очередь растёт, latency увеличивается и порождает ещё больше timeout.

### Deadline и cancellation

Deadline должен передаваться по цепочке как оставшийся budget, а не обнуляться на каждом hop. Иначе три сервиса с локальным timeout по 30 секунд превращают пользовательский deadline в полторы минуты. Компонент, получивший cancellation, должен прекратить бессмысленную работу, но это не откатывает уже совершённый side effect. Cancellation - сигнал управления вычислением, не распределённая транзакция.

Код `UNAVAILABLE` может быть retryable, но только если method semantics позволяют. `DEADLINE_EXCEEDED` также не доказывает, что сервер ничего не сделал. Для non-idempotent метода эти статусы переводят результат в unknown и запускают проверку по operation ID.

## Конкуренция: version, lock, lease и fencing

Несколько replicas полезны для availability, но они могут одновременно прочитать один status. Универсального «distributed lock» нет; сначала выбирается тип конфликта.

| Механизм | Когда подходит | Что не гарантирует |
| --- | --- | --- |
| Optimistic concurrency | короткое read-modify-write, конфликты редки | не удерживает владение во время долгого side effect |
| Row/advisory lock в БД | координация внутри одной database boundary | не блокирует внешний API после commit или потери сессии |
| Lease | временное лидерство или assignment с renewal | старый holder может продолжить работу после expiry |
| Fencing token | защита downstream от устаревшего holder | работает только если downstream сравнивает token |

Fencing token - монотонно растущий номер владения. Каждый новый holder получает большее значение; storage или Worker отвергает команды со старым token. Это закрывает опасный сценарий: controller A завис, его lease истёк, controller B получил ресурс, а A «ожил» и попытался записать старый результат.

Lock не заменяет idempotency. Holder может выполнить внешний side effect и умереть до фиксации результата. После expiry новый holder повторит операцию. Поэтому lock уменьшает параллелизм, а operation ID и recovery protocol отвечают за корректность повторов.

## Redis Streams: очередь работ с явным подтверждением

Redis Streams полезен, когда control plane нужен упорядоченный log сообщений и распределение работы по consumer group. Consumer читает запись, она попадает в Pending Entries List (PEL), а после успешной обработки consumer отправляет `XACK`. Если consumer умер до ack, запись остаётся pending и может быть изучена через `XPENDING` и передана другому consumer через `XCLAIM` или `XAUTOCLAIM`.

Это естественно даёт at-least-once processing: сообщение может быть обработано, но ack потерян, после чего оно будет обработано снова. Следовательно, handler всё равно обязан быть идемпотентным. Для production важны не только длина stream, но и:

- возраст самой старой pending записи;
- delivery count и число повторных claims;
- lag consumer group;
- consumers, которые давно не подают признаков жизни;
- политика poison message и dead-letter stream;
- retention/trim, не удаляющий нужную для recovery историю.

Poison message нельзя бесконечно гонять между consumers. После ограниченного числа попыток запись переносится в quarantine/dead-letter path вместе с причиной, payload reference и operation ID. Оператор должен иметь безопасный способ исправить данные и переиграть сообщение, не создавая новую identity операции.

### Очередь будит reconciler, но не владеет истиной

Stream хорошо доставляет сигнал «ресурс нужно проверить». Однако durable spec/status обычно остаётся в system of record. Если сообщение потерялось или было trim'нуто, периодический resync всё равно должен найти незавершённый ресурс. Если одно сообщение пришло десять раз, каждый проход читает свежую версию ресурса и быстро понимает, нужен ли следующий шаг.

## PostgreSQL: транзакционная граница и source of truth

PostgreSQL подходит для resource records, operation journal, unique constraints и атомарных переходов между связанными строками. Row lock защищает конкурирующее обновление; advisory lock удобен для application-defined identity, но его область и lifetime нужно выбрать осознанно. Session-level advisory lock переживает rollback и требует явного release или завершения session; transaction-level освобождается на commit/rollback.

Транзакция PostgreSQL не охватывает Redis, object store и внешний cloud API. Если в одной функции записать БД, а затем отправить событие, процесс может умереть между шагами. Outbox pattern решает локальную часть задачи: изменение domain state и запись события в outbox совершаются одной транзакцией; отдельный publisher доставляет outbox в stream и помечает публикацию. Доставка всё равно может повториться, поэтому consumer сохраняет deduplication.

### Redis и PostgreSQL в одной архитектуре

| Задача | Предпочтительный центр | Почему |
| --- | --- | --- |
| durable resource state и инварианты | PostgreSQL | transactions, constraints, versioned updates |
| быстрая доставка работы и fan-out | Redis Streams | consumer groups, pending tracking, низкая latency |
| snapshot blob | object storage | размер, lifecycle policy, дешёвое долговременное хранение |
| cluster workload placement | Kubernetes API | scheduler/controllers и node-level execution |

Это не строгий закон, а полезная граница ответственности. В текущем контексте AX использует Redis в task control-plane path, а Substrate хранит dynamic Actor/Worker state в PostgreSQL. Их backup, retention и failure domain различаются. Восстановление только Redis не восстанавливает состояние Substrate; восстановление PostgreSQL не доказывает, что AX stream и desired tasks согласованы. Нужен runbook сквозной сверки.

## gRPC/protobuf: контракт, время и давление потока

Protobuf задаёт typed schema, а gRPC - transport contract. Совместимость требует дисциплины: не переиспользовать удалённые field numbers, резервировать их, добавлять поля как optional/evolvable и не рассчитывать, что все компоненты обновятся одновременно. Новый сервер должен корректно принимать старого клиента, а rollout должен учитывать mixed versions.

Для каждого метода проектируются:

- deadline и propagation оставшегося budget;
- idempotency и допустимые retryable status codes;
- максимальный размер message и способ передачи больших blobs по reference;
- TLS/mTLS, identity и authorization;
- limits на concurrent streams;
- flow control и backpressure;
- observability по method, code, latency и attempt.

Streaming не отменяет backpressure. Если producer пишет быстрее, чем consumer читает и downstream подтверждает работу, buffers растут до memory pressure или cancellation. Flow control ограничивает объём данных в полёте, но application всё равно должно ограничивать собственную очередь и не держать lock во время медленной отправки.

## Внешний side effect: сначала журнал, потом действие

Самая трудная граница - система, которую нельзя включить в локальную транзакцию. Перед вызовом создаётся durable operation record:

```
operation_id: actor-42/gen-7/publish-route
kind: PublishRoute
request_fingerprint: sha256(...)
state: Planned
attempt: 0
external_id: null
last_error: null
```

Затем controller вызывает внешний API с operation ID как idempotency key, если API это поддерживает, и сохраняет внешний ID. После timeout он не создаёт «ещё один route», а сначала ищет существующий объект по idempotency key, tag или external ID. Если внешний API не поддерживает поиск и deduplication, нужно честно выбрать один из плохих вариантов: риск duplicate, ручная сверка или compensating cleanup.

State machine операции может выглядеть так: `Planned -> InFlight -> Confirmed`, а при неопределённости - `Unknown -> Verifying`. Состояние `Failed` допустимо только когда известно, что effect не произошёл или корректно компенсирован. Timeout сам по себе не даёт этого знания.

## Практический инцидент: Actor создан, ответ потерян

Рассмотрим последовательность, которая в happy-path тесте почти не видна:

1. AX создаёт operation `op-73` для Actor `A` generation 4.
2. Substrate принимает запрос и durable создаёт Actor.
3. Сеть рвётся до ответа. AX видит deadline exceeded.
4. AX повторяет запрос с тем же `op-73`.
5. Substrate находит прежнюю operation record и возвращает identity существующего Actor, а не создаёт второго.
6. Controller AX читает status: Actor существует, но ещё `Ready=False`.
7. Через watch или повторную проверку приходит `Ready=True` для generation 4.
8. AX отмечает свой шаг confirmed и продолжает task lifecycle.

Если повтор отправлен с новым operation ID, сервер не может отличить intentional second Actor от retry. Если AX после timeout сразу пишет `Failed`, оператор получит ложный failure при реально созданном ресурсе. Если status не содержит generation, поздний ответ от generation 3 может ошибочно завершить generation 4. Одна короткая авария проявляет необходимость почти всех механизмов главы.

## Наблюдаемость: видеть протокол, а не только процессы

CPU и memory важны, но они не объясняют, где застряла операция. Logs, traces и metrics должны связываться полями:

- tenant, resource ID, generation и operation ID;
- controller instance, lease/fencing token и attempt;
- queue/stream, message ID, pending age и delivery count;
- RPC service/method, status code, deadline и remaining budget;
- предыдущий и новый status, reason перехода;
- external system и external object ID;
- duration каждого шага и время с момента принятия intent.

Полезные SLO строятся вокруг пользовательского результата: time-to-accepted, time-to-ready, доля операций в unknown, возраст старейшей незавершённой operation, reconciliation lag. Метрика «controller process up» ничего не говорит о том, сходится ли desired state.

Operation ID должен проходить через AX, Substrate, Kubernetes events и внешние вызовы настолько далеко, насколько это безопасно. Но trace ID не заменяет business identity: sampling может удалить trace, а operation record обязана пережить рестарт и retention observability stack.

## Проверка отказоустойчивости

Unit test happy path не доказывает корректность recovery. Нужны тесты на точки разрыва:

- crash после side effect, но до status update;
- crash после записи outbox, но до публикации;
- потеря ack и повторная доставка stream message;
- два reconciler одновременно читают одну version;
- lease истекает во время долгого RPC, старый holder возвращается;
- ответы generation N приходят после N+1;
- watch cursor устарел и требует полного resync;
- внешний API отвечает timeout, хотя объект создан;
- poison message превышает attempt limit;
- object store доступен, а database нет, и наоборот.

Fault injection должен проверять инварианты, а не только отсутствие panic. Например: на один operation ID существует не более одного внешнего объекта; Ready никогда не относится к старой generation; stale fencing token не меняет Worker; удаляемый API resource не исчезает до завершения finalizer cleanup.

## Антипаттерны, которые выглядят проще

- **«У нас internal network, timeout не нужен».** Зависший RPC постепенно занимает все workers.
- **«Повторим любой 5xx».** Код ошибки не доказывает безопасность повторного side effect.
- **«Redis Stream гарантирует exactly once».** Потерянный ack закономерно создаёт повторную обработку.
- **«Возьмём lock на всю операцию».** Долгий lock снижает availability и всё равно не откатывает внешний effect.
- **«Status обновим в конце».** После crash не видно, какой шаг выполнялся и что проверять.
- **«Очередь и есть наша база».** Retention, trim и порядок delivery редко выражают все resource invariants.
- **«Timeout означает, что ничего не произошло».** Это одна из самых дорогих ошибок в control plane.
- **«Удалим запись, cleanup сделаем потом».** После удаления теряется durable обязанность освободить зависимые ресурсы.

## Практический чек-лист новой control-plane операции

1. Какой durable intent и какая стабильная resource identity?
2. Что означает API success: accepted, ready или completed?
3. Как клиент узнаёт status и к какой generation он относится?
4. Как формируется operation ID и сколько хранится deduplication record?
5. Какие шаги идемпотентны, а какие требуют journal или compensation?
6. Что происходит при crash до и после каждого side effect?
7. Какие ошибки retryable, каковы deadline, backoff, jitter и budget?
8. Как разрешается concurrent update: version, transaction, lease, fencing?
9. Где source of truth, где stream, где blob и где cluster state?
10. Как обрабатываются pending, poison и устаревшие сообщения?
11. Какие IDs связывают logs, traces, metrics и external resources?
12. Как выглядят resync, deletion и ручное аварийное восстановление?

## Итог связки

- Глава 8 выбирает physical security boundary по threat model.
- Глава 9 выражает placement, resources, identity, network и lifecycle в Kubernetes.
- Глава 10 превращает операции над этими ресурсами в повторяемый протокол восстановления.
- Надёжный control plane не обещает отсутствие сбоев. Он хранит достаточно identity и state, чтобы после сбоя понять, что произошло, и безопасно продолжить.
- Ни один слой не компенсирует автоматически ошибку другого: сильный sandbox не исправляет duplicate side effect, а идемпотентный reconciler не делает privileged container безопасным.

**Дальше.** Теперь у нас есть physical boundary, orchestration substrate и правила поведения control plane при отказах. Следующая глава вводит Actor model - способ отделить логически долгоживущую identity агента от конкретного Worker, Pod и периода активного выполнения. На этой базе слова suspend, resume, placement и snapshot перестают быть набором API методов и складываются в lifecycle одной сущности.

### Источники и дальнейшее чтение

- [RFC 9110: HTTP Semantics](https://www.rfc-editor.org/rfc/rfc9110.html) - определение idempotent methods и ограничения автоматического retry.
- [Kubernetes Controllers](https://kubernetes.io/docs/concepts/architecture/controller/) - reconciliation как управление desired и current state.
- [Kubernetes API Concepts](https://kubernetes.io/docs/reference/using-api/api-concepts/) - resourceVersion, list/watch и восстановление после устаревшего cursor.
- [Kubernetes Finalizers](https://kubernetes.io/docs/concepts/overview/working-with-objects/finalizers/) - двухфазное удаление и cleanup.
- [Redis Streams](https://redis.io/docs/latest/develop/data-types/streams/) - consumer groups, PEL, XACK, XPENDING и claim pending entries.
- [PostgreSQL Explicit Locking](https://www.postgresql.org/docs/current/explicit-locking.html) - row, table и advisory locks.
- [gRPC Deadlines](https://grpc.io/docs/guides/deadlines/), [Cancellation](https://grpc.io/docs/guides/cancellation/), [Retry](https://grpc.io/docs/guides/retry/) и [Flow Control](https://grpc.io/docs/guides/flow-control/) - временной budget, retry policy и backpressure.
- [AX Core Concepts](https://github.com/google/ax/blob/main/docs/concepts.md) - Task и declarative control-plane model AX.
- [Agent Substrate](https://github.com/agent-substrate/substrate) - Actor/Worker runtime и current project documentation.
