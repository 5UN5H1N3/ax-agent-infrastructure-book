# Agent harness: система вокруг LLM

![Agent harness: control loop, adapters, policy и evidence](.gitbook/assets/diagrams/04-05.png)  
*Agent harness: control loop, adapters, policy и evidence*

В предыдущей главе агент был описан как ограниченный control loop: получить observation, выбрать действие, выполнить его, проверить результат и решить, продолжать ли работу. Но эта схема не отвечает на главный практический вопрос: **какой код обеспечивает ограничения, сохраняет состояние, вызывает модель, проверяет tool call и переживает сбой процесса?**

Этот код и образует agent harness.

Представим маленького агента для анализа incident logs. В демонстрации он выглядит просто: system prompt, список tools и цикл из нескольких вызовов модели. Затем появляются production-требования:

- выбрать модель с нужными capabilities;
- не потерять Task после restart;
- ограничить стоимость и число шагов;
- не передать модели лишние secrets;
- проверить arguments до tool execution;
- запросить approval перед production write;
- отличить provider timeout от неправильного решения модели;
- повторить безопасную операцию, но не продублировать side effect;
- связать model request, tool call и итоговый artifact в trace;
- остановить всё дерево работы по cancel.

Если добавлять эти обязанности прямо в `while`-цикл, он быстро превращается в неявный framework со случайными контрактами. Harness делает эти контракты явными.

**Agent harness - это runtime одного агентного execution, который связывает model, state, context, tools, policy и lifecycle в управляемый процесс.**

Слово `harness` буквально означает оснастку или систему крепления. Модель остаётся вычислительным компонентом, а harness удерживает её внутри инженерных границ. Он не делает модель детерминированной, но делает детерминированными условия, при которых её предложение может стать действием.

## Harness не равен агенту, framework или orchestrator

Эти понятия часто смешивают, потому что один продукт может реализовывать сразу несколько ролей.

| Сущность | Главный вопрос | Пример ответственности |
| --- | --- | --- |
| Model | Как получить следующее вероятностное решение? | inference, structured output, tool proposal |
| Agent | Какое поведение решает задачу? | goal, loop, strategy, termination semantics |
| Harness | Как безопасно выполнить один agent run? | context, state, validation, tools, budgets, checkpoint |
| Framework/SDK | Какими abstractions это программировать? | Agent, Tool, Runner, graph node, middleware |
| Orchestrator | Где и когда запустить workload? | scheduling, Task lifecycle, Worker, restart, resource placement |
| Sandbox | Где физически выполняется недоверенный код? | process isolation, filesystem, network boundary |
| Model gateway | Как унифицировать доступ к providers? | routing, credentials, quotas, provider telemetry |
| Workflow engine | Как надёжно провести известный процесс? | durable timers, retries, signals, state-machine history |

Framework может предоставить готовый harness. Но наличие класса `Agent` ещё не означает, что определены recovery, cancellation, idempotency и audit. И наоборот, небольшой собственный harness может состоять из нескольких модулей без отдельного framework.

Полезная граница такова:

- **agent design** определяет, какие решения допустимо делегировать модели;
- **harness** исполняет эти решения внутри одного логического run;
- **orchestrator** обеспечивает жизнь процесса и инфраструктурных ресурсов;
- **platform policy** определяет внешние полномочия и организационные правила.

## Главный контракт: proposal не является action

Модель не должна напрямую вызывать shell, HTTP API или MCP server. Она формирует предложение в структурированном формате. Harness превращает его в действие только после последовательности проверок.

```
model proposal
  -> parse
  -> schema validation
  -> capability lookup
  -> policy decision
  -> approval check
  -> budget reservation
  -> idempotency registration
  -> execution
  -> output normalization
  -> postcondition verification
  -> durable observation
```

Эта цепочка важнее конкретного SDK. Если framework скрывает её за одним методом, инженер всё равно должен понимать, где проходит каждый gate и что произойдёт при сбое между ними.

Например, модель предложила:

```
{
  "action": "dns.update_record",
  "arguments": {
    "zone": "example.com",
    "name": "api",
    "value": "203.0.113.25"
  },
  "reason": "authoritative record differs from desired state"
}
```

Harness обязан ответить на вопросы, которые модель не может решать единолично:

1. Существует ли tool с таким именем и версией?
2. Соответствуют ли arguments schema?
3. Разрешена ли зона текущему identity и Task scope?
4. Требует ли действие approval?
5. Не устарел ли plan после получения approval?
6. Есть ли remaining budget?
7. Не выполнялась ли эта operation раньше?
8. Как проверить фактический результат?
9. Какие данные можно сохранить в trace?

Prompt с фразой «будь осторожен» не заменяет ни одну из этих проверок.

## Внутренняя архитектура harness

Удобно разделить harness на control path и data path.

**Control path** решает, что должно произойти: создаёт run, переводит state machine, проверяет policy, учитывает budget, управляет pause/resume и termination.

**Data path** переносит содержимое: собирает model context, отправляет запрос provider, передаёт arguments tool executor, нормализует observation и сохраняет artifacts.

Такое разделение не требует отдельных сервисов. В маленьком приложении оба пути живут в одном процессе. Важно, чтобы их обязанности не смешивались логически.

Минимальный набор компонентов:

| Компонент | Вход | Выход | Что он не должен решать |
| --- | --- | --- | --- |
| Run controller | событие и durable state | следующий transition | domain-факты без evidence |
| Context assembler | state, instructions, observations | model request | policy на side effects |
| Model adapter | normalized request | normalized response | выполнение tool call |
| Decision parser | raw/structured response | typed proposal | authorization |
| Tool registry | identity, scope, capabilities | доступные tool descriptors | фактическое выполнение |
| Policy engine | proposal, identity, state | allow, deny, require approval | убеждать модель текстом |
| Tool executor | approved operation | raw result | объявлять Task успешной |
| Observation normalizer | raw result | typed observation | скрывать partial failure |
| State store/journal | events и checkpoints | восстановимый progression | быть единственной копией external state |
| Budget controller | reservation/usage events | remaining limits | оценивать бизнес-ценность |
| Trace/audit sink | correlation events | searchable evidence | хранить secrets без фильтрации |
| Result builder | verified state и artifacts | terminal result | придумывать отсутствующие evidence |

Далее разберём не названия классов, а инженерные контракты между ними.

## Run controller: место, где живёт lifecycle

Run controller реализует state machine из предыдущей главы. Он принимает события и решает, какой transition выполнить. Событием может быть model response, tool result, approval, timeout, cancel или recovery signal.

Хороший controller не состоит из рекурсивных вызовов `run_agent()` и не держит всё состояние только в stack. Его логику можно представить функцией:

```
new_state, effects = transition(current_state, event)
```

`effects` - не уже выполненные внешние операции, а команды harness: вызвать model, запросить approval, запустить tool, сохранить artifact или завершить Task. Благодаря этому transition можно тестировать без сети и модели.

Основные обязанности controller:

- назначать монотонный `state_version`;
- проверять terminal conditions до нового шага;
- резервировать budget до вызова внешней зависимости;
- связывать event с ожидающим operation;
- игнорировать duplicate и stale events;
- распространять cancellation;
- checkpoint-ить состояние перед паузой;
- строить terminal result только из verified evidence.

Если controller не умеет выразить `WaitingForApproval` или `WaitingForTool`, приложение начинает удерживать process и надеяться, что ответ когда-нибудь придёт. Это дорого и плохо восстанавливается.

## Context assembler: compiler, а не конкатенация строк

Context assembly часто реализуют как `system_prompt + history + latest_message`. Для короткого demo этого достаточно. В production assembler больше похож на compiler pipeline.

Он должен:

1. принять цель, текущее состояние и тип следующего решения;
2. выбрать инструкции с явными версиями;
3. получить только релевантные observations и artifacts;
4. отметить provenance и trust level каждого фрагмента;
5. добавить доступные tools и их schema;
6. применить redaction и data policy;
7. оценить token budget;
8. выполнить truncation, summarization или retrieval;
9. сформировать provider-neutral request;
10. сохранить manifest того, что действительно ушло модели.

Manifest нужен для воспроизводимости. Запись «модель увидела контекст задачи» недостаточна. Нужны identifiers и версии: prompt bundle, tool catalog, memory snapshot, included artifact hashes, compaction strategy и model settings.

### Инструкции и данные должны различаться

Любой текст из issue, log, web page или tool output потенциально недоверенный. Harness не обязан понимать все prompt injection, но должен сохранять границы происхождения.

Практичная классификация:

| Класс | Пример | Допустимое использование |
| --- | --- | --- |
| Platform instruction | policy о запрете production write | authoritative instruction |
| Agent instruction | роль и procedure конкретного агента | instruction внутри platform policy |
| User objective | «исследовать ошибку TLS» | цель, но не новые полномочия |
| Trusted state | approved scope, identity, budget | control data |
| Retrieved evidence | runbook из контролируемого repo | данные с provenance |
| Untrusted content | issue body, log line, web page | только evidence, не инструкция |
| Tool metadata | schema, side-effect class | contract для proposal |

Если все категории превращаются в один текстовый блок, модель может принять строку из лога за системную команду. Даже если provider поддерживает роли сообщений, harness должен дополнительно ограничивать authority на уровне tools и policy.

### Context window - не база данных

Assembler не должен пытаться поместить всю историю. Его задача - собрать достаточный контекст для текущего decision point.

Полезный порядок при переполнении:

1. сохранить immutable platform и safety instructions;
2. сохранить objective, current state и open decision;
3. сохранить последние verified observations;
4. сохранить schemas только доступных сейчас tools;
5. заменить старые подробности ссылками и summaries;
6. убрать дубли и нерелевантные transcripts;
7. остановиться с `NeedsInput` или `Blocked`, если безопасное сокращение невозможно.

Summarization является lossy transformation. Summary нельзя считать authoritative evidence; оно должно ссылаться на исходные artifacts.

## Model adapter: единый интерфейс без иллюзии одинаковых моделей

Model adapter переводит normalized request в API конкретного provider и возвращает normalized response. Его цель - изолировать транспортные различия, но не скрыть смысловые capabilities.

Минимальный request contract может включать:

```
model_request:
  request_id: mr-0182
  model_profile: reasoning-medium
  messages_ref: ctx-09a1
  tools_ref: catalog-v17
  required_output_schema: decision-v3
  max_output_tokens: 1800
  deadline: 2026-10-05T01:30:00Z
  cancellation_token: cancel-task-412
  trace_parent: 00-abcd-1234-01
```

Ответ должен нормализовать:

- provider request/response IDs;
- text и structured parts;
- tool proposals;
- finish reason;
- usage, включая cached и reasoning tokens, если доступны;
- latency и retry attempts;
- safety/refusal signals;
- provider-specific warnings;
- raw response reference для отладки.

### Абстракция всегда протекает

Разные providers и модели по-разному поддерживают parallel tool calls, strict JSON schema, streaming, reasoning controls, prompt caching, image/audio input и server-managed conversation state. Нельзя свести всё к наименьшему общему знаменателю и затем ожидать одинакового поведения.

Вместо одного списка model names лучше использовать capability profile:

| Capability | Зачем знать harness |
| --- | --- |
| Structured output strictness | можно ли доверять provider-side schema enforcement |
| Tool-call concurrency | может ли ответ содержать несколько независимых proposals |
| Context limit | какой compaction нужен до запроса |
| Streaming semantics | какие partial events считаются только UI, а какие durable |
| Cancellation support | можно ли остановить inference или только игнорировать результат |
| Usage reporting | когда и насколько точно доступна стоимость |
| Data residency/retention | разрешён ли provider для данного Task |
| Server-managed state | где находится часть conversation history |

Routing должен происходить до отправки чувствительных данных. Если policy запрещает внешний provider, fallback после ошибки не может молча выбрать его ради доступности.

### Ошибки model adapter

`ModelError` слишком широк. Harness должен различать хотя бы:

- authentication/authorization failure;
- quota или rate limit;
- provider unavailable;
- request timeout;
- context overflow;
- unsupported capability;
- malformed structured output;
- safety refusal;
- cancelled request;
- unknown/ambiguous outcome.

Только часть этих ошибок допускает автоматический retry. `context overflow` требует нового context assembly, а malformed decision - repair или новый model step. Authorization failure обычно требует оператора. Timeout после provider acceptance может оставить неизвестность по usage, но не создаёт внешний domain side effect, пока proposal не прошёл executor.

## Decision parser: недоверенный компиляторный front end

Даже при strict structured output ответ модели остаётся недоверенным входом. Parser должен:

- проверять JSON/schema;
- запрещать неизвестные action types;
- нормализовать identifiers, paths и URLs;
- устанавливать size limits;
- отклонять смешение final answer и несовместимого action;
- сохранять raw response отдельно от typed proposal;
- возвращать диагностируемую ошибку, а не `None`.

Repair полезен только для синтаксических проблем. Если модель выбрала запрещённый tool, нельзя автоматически «починить» решение на похожий разрешённый tool: это уже новое semantic decision и новый model step.

## Tool registry и executor: каталог отдельно, выполнение отдельно

Tool registry отвечает на вопрос «что доступно этому run сейчас?». Tool executor отвечает на вопрос «как выполнить уже разрешённую operation?». Разделение позволяет менять discovery, не смешивая его с credentials и side effects.

Descriptor полезного tool содержит больше, чем имя и JSON Schema:

```
tool:
  name: dns.read_record
  version: 2.1.0
  input_schema_ref: sha256:8f2...
  output_schema_ref: sha256:91b...
  side_effect: read_only
  authority: dns:zone/read
  timeout: 8s
  retry_class: safe_read
  concurrency_key: zone:{zone}
  data_classification: internal
  verifier: dns.verify_read
```

Для write tool добавляются approval class, idempotency support, compensation/rollback metadata и postcondition verifier.

MCP может дать discovery и transport для tools, resources и prompts, но протокол не определяет, как приложение управляет model context или agent loop. Именно harness агрегирует discovered capabilities, применяет local policy и решает, какие descriptors показать модели. Наличие tool в MCP server не означает автоматического разрешения на его вызов.

### Полный путь tool call

Production executor обычно выполняет такую последовательность:

1. Получает typed proposal и immutable operation identity.
2. Повторно загружает актуальный state.
3. Проверяет, что proposal создан для текущего `state_version`.
4. Валидирует arguments и canonical form.
5. Разрешает identity, scope и capability.
6. Вычисляет risk/approval class.
7. Проверяет approval, expiry и exact arguments.
8. Резервирует budget и concurrency slot.
9. Регистрирует operation как `prepared`.
10. Выполняет call с deadline и cancellation propagation.
11. Сохраняет raw result или error.
12. Нормализует result в observation.
13. Проверяет postcondition отдельным read.
14. Помечает operation как `verified`, `failed` или `unknown`.
15. Публикует event для run controller.

Особенно опасен промежуток между пунктами 10 и 11. Внешняя система могла применить write, а процесс упал до записи результата. После recovery нельзя считать операцию ни успешной, ни неуспешной. Статус `unknown` должен запускать reconciliation.

### Error и observation не одно и то же

Tool может вернуть HTTP 200 и domain failure внутри payload. Может вернуть timeout после фактически выполненного write. Может завершиться частично. Поэтому normalized result должен явно представлять:

- transport outcome;
- provider/application outcome;
- side-effect certainty;
- retryability;
- raw artifact reference;
- observed external identifiers;
- suggested reconciliation action.

```
operation_result:
  operation_id: op-441
  transport: timeout
  application: unknown
  side_effect: unknown
  retryable: after_reconcile
  external_id: change-9841
  raw_artifact: artifact://tool/op-441-response
```

Фраза tool exception не содержит достаточной информации для безопасного retry.

### Output normalization защищает и контекст

Tool result способен быть огромным, бинарным, секретным или вредоносным для model context. Executor не должен автоматически возвращать модели весь stdout.

Нормализатор:

- ограничивает размер;
- отделяет structured result от human-readable text;
- удаляет или маскирует secrets;
- сохраняет полный payload как artifact при допустимой policy;
- добавляет provenance и timestamps;
- помечает недоверенный контент;
- возвращает модели digest и ссылки вместо мегабайтного вывода.

Tool design подробно рассматривается в главе 6, а безопасность исполнения - в главе 37. Здесь важно увидеть роль harness: он является точкой, где model proposal превращается в контролируемую operation и обратно в observation.

## Policy и approval: решения вне prompt

Policy engine получает факты, а не свободный текст:

```
subject + task scope + action + canonical arguments + state + environment
    -> allow | deny | require_approval | constrain
```

`constrain` может сузить действие: ограничить path, maximum rows, destination host, duration или набор fields. Лучше предоставить model уже attenuated tool, чем показывать широкую capability и каждый раз надеяться на правильные arguments.

Policy должна применяться как минимум дважды:

1. когда формируется доступный tool catalog;
2. непосредственно перед execution.

Первая проверка уменьшает число невозможных proposals. Вторая защищает от stale context, изменения identity и подмены arguments.

Approval не заменяет policy. Человек не должен получать просьбу «разрешить shell». Нужен конкретный proposal:

- что изменится;
- где именно;
- какими arguments;
- какие side effects ожидаются;
- чем подтверждена необходимость;
- как проверить результат;
- как откатить изменение;
- до какого времени и для какой `state_version` действует решение.

После approval harness повторяет policy и input validation. Между паузой и нажатием кнопки среда могла измениться.

### Fail-open и fail-closed

| Сбой компонента | Read-only действие | Необратимый write |
| --- | --- | --- |
| Policy service недоступен | возможно ограниченное local rule | deny/pause |
| Audit sink недоступен | buffer с жёстким лимитом | обычно pause |
| Budget store недоступен | conservative local remainder | deny нового expensive step |
| Approval store недоступен | продолжить без write | deny/pause |
| Verifier недоступен | вернуть incomplete evidence | не объявлять success |

Решение fail-open должно быть явным и привязано к risk class. Глобальная настройка `continue_on_error` почти всегда слишком груба.

## State, journal и checkpoint

Глава 5 подробно разделяет context, working state, memory и snapshot. Для harness сейчас достаточно трёх правил.

**Первое:** logical state должен переживать restart процесса.

**Второе:** transition и намерение выполнить side effect должны записываться до внешнего действия.

**Третье:** checkpoint не доказывает, что external world соответствует локальному state.

Event journal полезен для расследования:

```
RunCreated
ContextAssembled
ModelRequestStarted
ModelResponseReceived
ProposalValidated
ApprovalRequested
ApprovalGranted
OperationPrepared
OperationStarted
OperationOutcomeUnknown
ReconciliationObserved
OperationVerified
RunSucceeded
```

Необязательно строить полноценный event-sourced system. Можно хранить current state и append-only operation ledger. Но инженер должен иметь возможность ответить: что harness собирался сделать, что начал, что увидел после recovery и на основании чего объявил успех.

Checkpoint создаётся:

- после значимого observation;
- перед `WaitingForApproval`;
- перед долгим tool execution;
- после регистрации operation result;
- перед terminal transition;
- периодически в длинном read-only исследовании.

Checkpoint слишком часто увеличивает latency и write amplification. Слишком редко - увеличивает объём повторной работы и риск потерять provenance. Выбор интервала является частью SLO, а не вкусовой настройкой.

## Budgets, deadlines и cancellation

Budget controller учитывает несколько ограничений одновременно:

- model requests и tokens;
- wall-clock deadline;
- tool calls;
- денежную стоимость;
- объём artifacts и retrieved data;
- parallel operations;
- write proposals;
- nested/delegated runs.

Budget должен резервироваться до операции, а фактическое usage сверяться после. Иначе несколько параллельных steps могут одновременно увидеть один остаток и совместно превысить лимит.

```
remaining = limit - committed_usage - active_reservations
```

Если точная стоимость неизвестна до завершения, резервируется upper bound. Неиспользованный остаток освобождается.

### Deadline не равен timeout

- **Run deadline** ограничивает всю работу.
- **Step timeout** ограничивает одну model/tool operation.
- **Connect/read timeout** описывает транспорт.
- **Approval expiry** ограничивает актуальность решения человека.
- **Lease timeout** определяет, когда другой worker может подобрать abandoned run.

Один параметр `timeout=60` не способен выразить эти semantics.

Cancellation должна быть cooperative и observable. Controller отмечает run как cancelling, прекращает новые proposals, передаёт token активным adapters/executors, ждёт bounded grace period и затем завершает оставшиеся процессы инфраструктурным способом. External writes, которые нельзя отменить, переходят в reconciliation queue.

Terminal `Cancelled` означает не «мы послали сигнал», а «harness больше не инициирует действия, локальные executions остановлены, незавершённые external operations перечислены».

## Concurrency и backpressure

Параллельные tool calls сокращают latency, но усложняют state.

Перед параллельным запуском harness должен знать:

- независимы ли operations;
- читают ли они один mutable resource;
- есть ли ordering requirement;
- как объединяются observations;
- можно ли отменить оставшиеся calls после первого достаточного результата;
- какой общий и per-tool concurrency limit;
- как зарезервирован budget.

Полезны `concurrency_key` и conflict policy. Два read для разных hosts могут выполняться одновременно. Два write в одну DNS zone должны сериализоваться или отклоняться. Read после write часто требует ordering barrier.

Backpressure нужен на трёх уровнях:

1. model request queue;
2. tool/provider-specific queue;
3. artifact and trace pipeline.

Если trace exporter замедлился, harness не должен бесконечно накапливать payload в RAM. Если model provider отвечает 429, тысяча runs не должна одновременно выполнить retry. Нужны bounded queues, jitter, global rate limiter и понятный overload result.

## Retry: только после классификации

Harness должен различать три класса ошибок.

**Transport/infrastructure error** - connection reset, rate limit, temporary unavailable. Часто подходит bounded retry.

**Contract error** - invalid schema, unsupported capability, tool not found. Retry без изменения request бесполезен.

**Semantic error** - модель выбрала не тот шаг, observation опровергла гипотезу, verifier не подтвердил результат. Нужен новый decision, а не повтор байтов.

Retry policy включает:

- error class;
- maximum attempts;
- backoff и jitter;
- deadline awareness;
- idempotency requirement;
- whether retry creates a new model step;
- what telemetry links attempts into one logical operation.

Важно различать logical call и physical attempt. Пользователь просит один model decision, но adapter может выполнить три HTTP attempts. Trace должен показывать оба уровня, а budget policy - явно решать, что считается платным request.

Circuit breaker защищает provider от лавины повторов, но сам по себе не является fallback strategy. Fallback model может отличаться capabilities, data policy, cost и behavior. Переключение допускается только на совместимый profile и должно фиксироваться в state.

## Tracing, audit и privacy

Harness находится в лучшей точке для correlation, потому что видит весь путь:

```
Task -> run -> state version -> model request -> proposal
     -> policy decision -> operation -> observation -> transition
```

Для каждого run нужны стабильные identifiers. Для каждого внешнего call - собственный span/operation ID. OpenTelemetry уже определяет GenAI operations вроде `invoke_agent`, `chat` и `execute_tool`; их можно использовать как основу, добавив domain attributes AX.

Но полный prompt и tool output часто содержат secrets, personal data или proprietary code. Поэтому tracing pipeline должен иметь режимы:

- metadata only;
- redacted content;
- encrypted restricted payload;
- sampled full content для специально разрешённой среды.

Нельзя сначала отправить полный prompt внешнему trace backend, а затем пытаться отредактировать dashboard. Redaction выполняется до export. Raw payload, если он вообще сохраняется, получает отдельную retention и access policy.

Audit event и debug log служат разным целям. Debug log можно sampling-овать. Audit trail для high-risk write должен быть complete, append-only и связывать identity, approval, exact arguments и verified outcome.

## Harness и orchestrator: где проходит recovery

Harness управляет логическим run. Orchestrator управляет workload, в котором этот run исполняется.

| Ситуация | Основной владелец | Действие |
| --- | --- | --- |
| Provider вернул 429 | Harness/model gateway | backoff, quota-aware retry |
| Model response не прошёл schema | Harness | repair/new decision/fail |
| Tool timeout с unknown side effect | Harness | reconciliation |
| Process получил OOMKill | Orchestrator + harness | restart workload, load checkpoint |
| Worker исчез | Orchestrator | reschedule/recover Task |
| Workspace mount не доступен | Runtime/orchestrator | infrastructure failure |
| Approval ожидается сутки | Harness сохраняет pause; orchestrator освобождает compute | resume по событию |
| Пользователь отменил Task | Оба | logical cancel + process cleanup |

Граница проявляется при restart. Orchestrator способен снова запустить runner. Но только harness знает, какой logical state загрузить и какую незавершённую operation reconcile.

Не следует позволять обоим уровням независимо повторять один и тот же side effect. Task-level retry перезапускает runner с прежним operation ledger. Он не создаёт новую identity для write только потому, что process новый.

## Граница с AX

AX предоставляет инфраструктурные primitives и execution substrate: Task lifecycle, Workspace, Model/Gateway, runner contract, размещение и наблюдаемость workload. Конкретный harness может быть Python-процессом, Go binary, Node application, graph framework или CLI-agent.

Для интеграции harness с AX достаточно явного runner contract:

- где получить Task input и identity;
- где находится durable Workspace;
- как вызвать Model/Gateway;
- как публиковать heartbeat и status;
- как получить cancel/approval event;
- куда записать artifacts;
- как вернуть terminal result;
- что должно сохраниться перед exit.

AX не должен угадывать внутренний plan агента. Harness не должен реализовывать собственный cluster scheduler. Такая независимость позволяет менять agent framework без миграции control plane и менять инфраструктуру без переписывания domain loop.

### Pause и suspend - разные операции

`WaitingForApproval` - логическое состояние run. `suspend` - инфраструктурная оптимизация workload. Сначала harness сохраняет checkpoint и сообщает, что способен быть возобновлён по событию. Затем AX может освободить compute или snapshot-ить workload.

Если workload был suspended без логического checkpoint, resume может вернуть процесс в устаревшее соединение или середину операции. Если run логически paused, но workload продолжает занимать GPU, корректность сохранена, однако ресурсы используются плохо. Production-система должна согласовать оба слоя.

## Как выбирать готовый framework или писать свой harness

Выбор не начинается со списка популярных библиотек. Сначала фиксируется required contract.

Спросите:

1. Есть ли typed state и явные transitions?
2. Можно ли durable pause/resume без удержания процесса?
3. Где хранятся operation IDs и approvals?
4. Какие hooks существуют до и после каждого tool call?
5. Можно ли гарантировать blocking guardrail до side effect?
6. Как распространяется cancellation?
7. Различаются ли logical call и retry attempts?
8. Можно ли заменить model provider без потери нужных capabilities?
9. Как тестировать controller без реальной модели?
10. Можно ли экспортировать traces без sensitive content?
11. Что происходит после process crash между tool execution и checkpoint?
12. Можно ли встроить собственные identity и policy services?

Если framework не даёт ответа, это не всегда причина отказаться. Но недостающую semantics придётся реализовать вокруг него, а не прятать в prompt.

### Когда достаточно маленького собственного harness

Собственный минимальный runtime разумен, когда:

- один агент и небольшой набор tools;
- короткий run;
- нет долгих human pauses;
- state легко сериализовать;
- поведение нужно жёстко контролировать;
- команда готова владеть lifecycle и тестами.

Framework полезнее, когда нужны готовые graph transitions, sessions, tool middleware, streaming, handoffs, tracing integrations или multi-agent patterns. Workflow engine полезен, когда главная сложность - durable timers, signals и recovery на дни, а agentic decisions являются отдельными activities.

Нередко лучший вариант - framework внутри activity, durable workflow снаружи и AX как execution platform. Важно назначить один authoritative owner для каждого retry, timeout и state transition.

## Референсный run contract

Конфигурация не обязана выглядеть именно так, но все поля должны иметь владельца и semantics.

```
run:
  id: run-412
  task_id: task-981
  agent_spec: incident-investigator@4
  objective_ref: artifact://task/input.json
  state_schema: incident-state@3
  state_store: workspace://state/run-412.json
  operation_ledger: workspace://ledger/run-412.ndjson

model:
  profile: reasoning-medium
  allowed_providers: [local, approved-cloud]
  required_capabilities: [structured-output, tool-calling]
  context_policy: incident-context@7

policy:
  identity: actor://oncall-agent
  scope: service:payments-api
  tool_catalog: incident-read-tools@9
  write_mode: approval-required

limits:
  deadline: 15m
  model_steps: 20
  input_tokens: 180000
  output_tokens: 24000
  tool_calls: 40
  parallel_tools: 4
  artifact_bytes: 50000000
  cost_usd: 4.00

recovery:
  checkpoint: after-observation
  lease: 60s
  unknown_operation: reconcile
  max_model_attempts: 3
  max_tool_attempts: 2

telemetry:
  content_mode: redacted
  trace_sample: always
  audit_writes: always
```

Такой contract полезен даже для single-process приложения. Он заставляет ответить, где хранится state, какие providers допустимы и кто отвечает за unknown operation.

### Упрощённый skeleton runtime

```
on_event(run_id, event):
    state = store.load(run_id)
    state = controller.apply_event(state, event)

    while state.is_runnable():
        controller.check_terminal(state)
        budgets.reserve_next_step(state)

        request, manifest = context.build(state)
        store.record(ContextAssembled(manifest))

        response = models.generate(request, state.deadline)
        proposal = decisions.parse(response)

        decision = policy.evaluate(proposal, state)
        if decision.requires_approval:
            store.checkpoint(state.waiting_for(decision))
            return PAUSED

        operation = ledger.prepare(decision, state.version)
        raw_result = tools.execute(operation)
        observation = observations.normalize(raw_result)
        verified = verifier.check(operation, observation)

        state = controller.apply_observation(state, verified)
        store.checkpoint(state)

    return results.build(state)
```

В реальной реализации каждая строка может завершиться ошибкой или получить cancel. Скелет показывает порядок ответственности: context manifest записан до model call; operation создаётся после policy; verification предшествует success; state сохраняется после observation.

## Как развивать harness по этапам

Не каждому проекту сразу нужен распределённый runtime. Полезно расти по мере появления требований.

### Этап 1. Управляемый single-process loop

Минимум:

- typed proposals;
- фиксированный tool registry;
- schema validation;
- maximum steps и deadline;
- structured logs;
- read-only tools или ручное approval;
- terminal result с evidence.

Этого достаточно для коротких внутренних экспериментов.

### Этап 2. Восстанавливаемый run

Добавляются:

- durable state;
- operation ledger;
- idempotency keys;
- pause/resume;
- cancellation;
- error taxonomy;
- simulated tools и replay tests.

Это граница, после которой agent можно безопасно оставлять без живого terminal session.

### Этап 3. Production control

Появляются:

- identity-aware policy;
- attenuated capabilities;
- versioned approvals;
- cost and concurrency reservations;
- reconciliation workers;
- redacted distributed tracing;
- provider routing с data policy;
- SLO, overload behavior и runbooks.

### Этап 4. Platform capability

Общие части выносятся в reusable services: model gateway, tool registry, policy service, artifact store, trace pipeline и SDK для runner. На этом этапе важно не заставлять все агенты использовать один универсальный prompt или state schema. Платформа стандартизирует contracts, а не domain reasoning.

## Как тестировать harness

Тесты model behavior и тесты harness - разные наборы. Harness в значительной части детерминирован и должен проверяться без настоящего provider.

### Contract tests компонентов

Model adapter получает scripted responses: text, tool proposal, refusal, malformed JSON, stream interruption, timeout и rate limit. Tool adapter получает success, partial result, timeout-after-write и duplicate request. State store симулирует conflict и lost lease.

Проверяются:

- нормализация provider response;
- schema и size limits;
- policy order;
- budget reservation/release;
- correlation IDs;
- retry classification;
- redaction до trace export;
- terminal result без недоказанных утверждений.

### Transition tests

Каждый event проверяется против каждого допустимого state. Особенно важны race conditions:

- cancel одновременно с tool completion;
- approval одновременно с expiry;
- duplicate model response после retry;
- старый worker пишет state после lease transfer;
- два parallel calls завершаются в обратном порядке;
- process падает после external write до local commit.

Ожидаемый результат теста - конкретный state, ledger и набор effects, а не строка ответа модели.

### Failure injection

Harness должен переживать искусственные сбои в каждой границе:

1. до model request;
2. после отправки, до response;
3. после response, до parsing;
4. после approval request;
5. после operation registration;
6. после external side effect;
7. во время verification;
8. после checkpoint, до status publish.

Для каждого места задаётся recovery invariant. Например: после повторного запуска существует не более одной domain operation с данным idempotency key; `Succeeded` невозможен без verifier evidence.

### Replay и golden trajectories

Replay запускает controller и adapters на сохранённой последовательности events. Он помогает проверить upgrade harness без повторного вызова внешних systems.

Golden trajectory не должна фиксировать каждую формулировку модели. Она фиксирует существенные свойства:

- использованы только разрешённые tools;
- write не выполнен без approval;
- budget не превышен;
- найдено evidence нужного типа;
- terminal state корректен;
- sensitive payload не попал в telemetry.

Официальные testing utilities некоторых SDK позволяют подставлять scripted model и записывать normalized interactions. Даже если выбран другой framework, такой interface стоит реализовать самостоятельно.

## Практикум: harness для агента проверки change request

Спроектируем harness для задачи: «Проанализируй change request, проверь repository и CI, затем подготовь recommendation. Ничего не merge и не изменяй без отдельного approval».

### 1. Определить границы

Agent может:

- читать change request и diff;
- искать по repository;
- запускать tests в sandbox;
- читать CI logs;
- создавать локальный report artifact.

Agent не может:

- push-ить commits;
- изменять branch protection;
- публиковать review comment;
- merge-ить change request;
- читать secrets вне test environment.

Это не текстовая просьба к модели, а tool catalog и policy.

### 2. Описать state

```
phase: collecting
change_ref: repo/pr/184
state_version: 1
verified:
  diff_loaded: false
  tests_run: false
findings: []
operations: {}
budgets:
  model_steps_left: 12
  tool_calls_left: 25
terminal:
  status: running
  evidence: []
```

### 3. Собрать контекст для конкретного решения

На первом step не нужно отправлять модели весь repository. Context assembler передаёт objective, diff summary, changed paths, available read-only tools и policy. Когда model просит конкретный файл, tool возвращает содержимое с path, commit SHA и line ranges.

CI log сохраняется artifact, а в context попадает нормализованный fragment с job ID и timestamp. Строка из log не получает instruction authority.

### 4. Выполнить tools через gates

Proposal `repo.read_file` проходит schema и path scope. Proposal `shell.run_tests` дополнительно проходит command policy и запускается в sandbox с deadline. Proposal `github.merge` отсутствует в catalog, поэтому parser возвращает capability error, а не пытается найти похожую функцию.

### 5. Построить результат из evidence

Recommendation содержит:

- verified findings со ссылками на diff или log artifacts;
- tests и их exit status;
- непроверенные assumptions;
- residual risks;
- точное предложение следующего действия.

Если tests не запустились из-за отсутствующей dependency, результат `Blocked` честнее, чем модельное «изменение выглядит безопасно».

### 6. Проверить recovery

В тесте process завершается после запуска tests, но до записи результата. После restart harness находит operation ID, проверяет sandbox job status и присоединяет существующий result. Он не запускает второй дорогостоящий job автоматически.

Это упражнение показывает назначение harness лучше абстрактного списка компонентов: он сохраняет границу между тем, что model предложила, тем, что система разрешила, тем, что действительно произошло, и тем, что удалось доказать.

## Типичные антипаттерны

**Prompt-only safety.** Все правила записаны в system prompt, а executor исполняет любой syntactically valid call.

**God Runner.** Один класс собирает context, вызывает provider, исполняет shell, пишет state и решает policy. Его невозможно тестировать по частям.

**Provider-shaped domain model.** Вся система хранит raw messages одного API; смена provider или версии ломает recovery.

**Retry everything.** Одинаковый decorator повторяет model calls, reads и non-idempotent writes.

**Success by narration.** Финальный текст модели автоматически становится `Succeeded` без verifier.

**Transcript as state.** Progression восстанавливается только из conversation history.

**Unbounded tool output.** Полный log, binary или database dump возвращается в model context и trace.

**Approval without version.** Оператор подтверждает старый plan, а harness выполняет его после изменения target state.

**Double orchestration.** Workflow engine и agent framework независимо retry-ят одну operation.

**Invisible fallback.** При provider error harness переключается на модель с другой data policy или capabilities, не фиксируя переход.

**Tracing all content by default.** Debug удобство превращается в неконтролируемую копию secrets и пользовательских данных.

## Итоги главы

- Harness - runtime одного agent execution, а не синоним модели или orchestration platform.
- Его базовый invariant: model proposal никогда не является action напрямую.
- Run controller владеет state transitions; context assembler строит воспроизводимый request; adapters нормализуют внешние systems.
- Tool execution требует schema, policy, approval, budget, idempotency, normalization и verification.
- Logical state, operation ledger и reconciliation позволяют переживать crash в опасной точке между side effect и checkpoint.
- Budgets резервируются до параллельной работы; deadlines, timeouts, leases и approval expiry имеют разные semantics.
- Retry применяется к классифицированной ошибке, а semantic failure запускает новое решение.
- Tracing связывает Task, model и tool operations, но sensitive content редактируется до export.
- AX управляет workload и инфраструктурным lifecycle; harness управляет внутренним run и восстанавливает его из durable state.
- Framework выбирают по recovery и enforcement contracts, а не по количеству готовых agent abstractions.
- Начинать можно с маленького loop, но production-уровень требует явных identity, policy, audit и failure injection tests.

### Источники и дальнейшее чтение

- [OpenAI Agents SDK: Running agents](https://openai.github.io/openai-agents-python/running_agents/) - run configuration, tool execution, sessions, approvals и tracing.
- [OpenAI Agents SDK: Guardrails](https://openai.github.io/openai-agents-python/guardrails/) - границы input, output и tool guardrails, включая blocking execution.
- [OpenAI Agents SDK: Testing](https://openai.github.io/openai-agents-python/testing/) - provider-neutral scripted testing orchestration.
- [Anthropic: Writing effective tools for agents](https://www.anthropic.com/engineering/writing-tools-for-agents) - tools как контракт между детерминированной системой и вероятностным агентом.
- [Model Context Protocol: Architecture overview](https://modelcontextprotocol.io/docs/learn/architecture) - границы protocol, host/client/server и discovery tools/resources/prompts.
- [Temporal: Activity Definition](https://docs.temporal.io/activity-definition) - retries, timeouts, heartbeats и требования к idempotent external operations.
- [OpenTelemetry GenAI semantic conventions](https://opentelemetry.io/docs/specs/semconv/registry/attributes/gen-ai/) - общие attributes и operations для agent/model/tool telemetry.
