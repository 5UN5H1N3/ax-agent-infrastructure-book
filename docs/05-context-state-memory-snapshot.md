# Context, state, memory и snapshot: не смешивать

![Состояние агента: семь разных сущностей, которые часто ошибочно называют «памятью»](.gitbook/assets/diagrams/05-06.png)  
*Состояние агента: семь разных сущностей, которые часто ошибочно называют «памятью»*

Глава 4 ввела state, journal, checkpoint и context assembler как компоненты harness. Теперь определим их семантические гарантии: какие данные производны, какие авторитетны, что переживает restart и что можно безопасно восстановить.

Представим, что агент расследует сетевой инцидент. Он прочитал alert, проверил DNS, запросил логи и подготовил изменение. Затем процесс перезапустился.

Что именно должно вернуться после запуска?

- исходная цель;
- подтверждённые факты;
- список уже выполненных tool calls;
- ожидающий approval;
- открытые файлы;
- разговор с пользователем;
- найденные в прошлых задачах рекомендации;
- TCP connection к log server;
- содержимое Python heap;
- фактическое состояние DNS и load balancer.

Это десять разных видов информации с разными владельцами, сроками жизни и гарантиями. Называть их одним словом `memory` удобно только до первого сбоя.

Главный принцип главы:

**Context - то, что модель видит сейчас. State - то, что приложение считает текущим progression. Memory - выбранная информация для будущих обращений. Snapshot - технический снимок части runtime. Ни одна из этих сущностей не заменяет external source of truth.**

Различие не академическое. От него зависит, повторит ли агент write после restart, примет ли устаревшее summary за факт и сможет ли оператор объяснить, почему было принято решение.

## Карта сущностей

Полезно различать как минимум восемь слоёв.

| Сущность | Главный вопрос | Типичный срок жизни | Пример |
| --- | --- | --- | --- |
| Request context | Что модель увидит в этом inference? | один model call | system instructions, goal, selected evidence |
| Working state | Где находится Task сейчас? | один run или Task | phase, verified facts, budgets, pending operation |
| Session history | Что происходило в диалоге? | thread/session | user messages, assistant responses, tool items |
| Persistent memory | Что стоит использовать в будущих задачах? | несколько runs | preference, reusable lesson, known environment fact |
| Workspace/artifacts | Какие файлы и evidence созданы? | Task/project | logs, reports, repository checkout, raw tool output |
| Process RAM | Что нужно живому процессу прямо сейчас? | жизнь процесса | objects, stacks, caches, open descriptors |
| Checkpoint/snapshot | Как ускорить или обеспечить resume? | до expiry/замены | serialized state, graph checkpoint, VM/process image |
| External source of truth | Каково реальное состояние мира? | задаёт внешняя система | Git branch, ticket status, DNS record, database row |

Одна физическая технология может хранить несколько слоёв. PostgreSQL может содержать working state, session history и persistent memory. Это не превращает их в одну семантическую сущность. И наоборот, одна сущность может быть распределена: request context состоит из instructions, выбранных memory items, state projection и tool schemas.

### Семь вопросов к любому фрагменту данных

Перед тем как решить «сохраним это в memory», ответьте:

1. **Scope:** к чему относится запись - request, run, thread, user, agent, project или organization?
2. **Lifetime:** когда она должна исчезнуть?
3. **Authority:** является ли это фактом, гипотезой, preference или производным summary?
4. **Provenance:** откуда информация получена и когда?
5. **Mutability:** кто может её изменить и как разрешаются конфликты?
6. **Rebuildability:** можно ли восстановить запись из другого источника?
7. **Sensitivity:** содержит ли она secrets, personal data или privileged instructions?

Если хотя бы scope и authority неизвестны, автоматическая retrieval такой записи опасна.

## Context: конечный вход одного model call

LLM не читает database, filesystem или предыдущий run напрямую. Она получает конечную последовательность tokens в конкретном request. Это и есть request context.

В него могут входить:

- platform и agent instructions;
- objective пользователя;
- projection текущего state;
- recent conversation items;
- retrieved memory;
- tool descriptions;
- selected files и search results;
- observations текущего шага;
- examples и output schema.

Context существует только для inference. После получения response его содержимое не становится автоматически durable state. Если важный факт присутствовал только в prompt, после compaction или смены provider он может исчезнуть.

### Context window - бюджет, а не хранилище

Большое context window создаёт иллюзию, что можно передавать всю историю. Но проблема не только в жёстком token limit. Шумные и противоречивые данные уменьшают полезность внимания, повышают стоимость и затрудняют поиск нужного evidence.

Хороший context assembler отвечает не на вопрос «что у нас есть?», а на вопрос «какой минимальный набор данных нужен для текущего decision point?».

Упрощённо:

```
policy + objective + state projection + selected evidence
+ relevant history + tool catalog + output reserve <= context budget
```

При нехватке бюджета нельзя просто удалять самые старые tokens. Старое approval или исходное constraint может быть важнее свежего многословного tool output.

### Context является производным представлением

Request context лучше собирать заново из durable components. Тогда его можно проверить и воспроизвести.

Manifest context assembly может содержать:

```
context_manifest:
  request_id: mr-203
  state_version: 18
  prompt_bundle: incident-agent@7
  objective_ref: artifact://task/input
  included_observations: [obs-41, obs-44, obs-45]
  memory_items: [mem-user-12, mem-runbook-8]
  tool_catalog: tools-readonly@5
  compaction: incident-summary@3
  input_tokens: 32740
```

Manifest не обязан копировать весь prompt, но должен объяснять его состав. Это помогает отличить ошибку reasoning от ошибки retrieval или compaction.

### Instruction и evidence нельзя смешивать

Файл, web page, log line и memory item по умолчанию являются данными. Они не получают право менять policy только потому, что попали в context.

Assembler должен сохранять:

- тип источника;
- trust level;
- timestamp и version;
- допустимое использование;
- ссылку на raw artifact.

Строка `ignore previous instructions` из retrieved document остаётся содержимым документа. Техническое enforcement всё равно находится в harness policy.

## Working state: восстановимая модель progression

Working state отвечает на вопрос: что уже известно, что ожидается и какой следующий transition допустим?

Он должен быть структурированным и достаточно компактным, чтобы application code мог проверять invariants без участия модели.

```
run_id: run-118
state_version: 23
phase: diagnosing
objective:
  kind: restore_service
  target: payments-api
constraints:
  writes_require_approval: true
verified_facts:
  - id: fact-31
    claim: origin health endpoint returns 200
    source: probe-771
    observed_at: 2026-10-05T02:10:18Z
hypotheses:
  - id: hyp-9
    text: edge certificate bundle is stale
    status: testing
pending:
  kind: tool
  operation_id: op-202
budgets:
  model_steps_left: 8
  tool_calls_left: 17
terminal:
  status: running
```

### State хранит raw facts, а prompt форматируется позднее

State не должен состоять из текста «агент думает, что проблема, вероятно, в сертификате». Разделяйте observation, interpretation и decision.

- Observation: probe вернул serial `41:A9` в 02:10 UTC.
- External fact: certificate inventory связывает `41:A9` с expired bundle.
- Hypothesis: edge использует stale bundle.
- Decision: сравнить config revision edge nodes.

Так разные nodes и модели могут использовать данные по-разному, а verifier способен проверить claim.

Производные presentation strings не нужно сохранять, если их легко собрать из raw fields. Это уменьшает расхождение между state и отображением.

### State version защищает от старых событий

Каждый mutation получает монотонную версию. Model proposal, approval и tool result ссылаются на версию, для которой были созданы.

Если approval относится к `state_version=17`, а target config уже изменился на версии 20, harness не должен выполнять старый plan. Он запрашивает новое решение или повторную проверку.

При нескольких workers нужен concurrency control: compare-and-swap, lease, transaction или single-writer discipline. Last-write-wins опасен, потому что поздний, но устаревший worker может стереть свежие evidence.

### State и journal дополняют друг друга

Current state удобен для быстрого продолжения. Append-only journal удобен для аудита и восстановления причинной цепочки.

```
ObservationRecorded(obs-41)
HypothesisAdded(hyp-9)
OperationPrepared(op-202)
ApprovalRequested(ap-17)
ApprovalGranted(ap-17, state_version=23)
OperationStarted(op-202)
```

Необязательно восстанавливать state replay всех events, как в полном event sourcing. Практичный вариант - current state, operation ledger и журнал существенных transitions.

## Session history: transcript не равен state

Session history хранит последовательность conversation items. Она полезна для continuity: помнить уточнение пользователя, предыдущий ответ и ожидающий tool result.

Но transcript плохо подходит как единственный working state:

- факты смешаны с гипотезами и исправлениями;
- одно утверждение может быть отменено позже;
- model-generated text выглядит так же убедительно, как observation;
- трудно атомарно обновить budget или operation status;
- история растёт без границ;
- compaction теряет детали;
- application code не может надёжно проверить invariant.

Поэтому session может помочь собрать context, но controller восстанавливается из structured state и ledger.

Официальные agent SDK часто называют session «memory», потому что она автоматически подставляет history в следующий run. Это удобный API-термин, но инженер должен помнить: сохранённая история разговора не становится ни verified knowledge, ни checkpoint внешних side effects.

## Persistent memory: информация для будущих обращений

Persistent memory существует за пределами одного текущего run. Её цель - сократить повторное исследование или сохранить полезную continuity.

Полезны четыре класса.

### Semantic memory

Устойчивые факты и понятия: имя сервиса, topology, внутренние термины, проверенный mapping между team и repository.

### Episodic memory

Сведения о прошлом episode: «в инциденте INC-42 похожая ошибка была вызвана stale edge config». Episode помогает сформировать гипотезу, но не доказывает причину нового инцидента.

### Procedural memory

Проверенная последовательность действий: runbook, diagnostic checklist, правила использования tool. Лучше хранить её как versioned skill или документ, а не как случайный prose fragment.

### Preference memory

Предпочтения пользователя или команды: формат отчёта, язык, рабочие часы, допустимый уровень подробности. Preference не должна расширять authorization.

| Класс | Может подсказать | Не может доказать |
| --- | --- | --- |
| Semantic | где искать service owner | кто владеет сервисом прямо сейчас без проверки |
| Episodic | какую гипотезу проверить первой | что новый incident имеет ту же причину |
| Procedural | какие steps обычно безопасны | что step разрешён текущей policy |
| Preference | как оформить результат | право выполнить внешнее действие |

Memory - не источник абсолютной истины. Это indexed collection кандидатов на включение в context.

## Запись memory - отдельный pipeline

Опасно позволять агенту сохранять любое собственное утверждение «на будущее». Ошибка превращается в устойчивое self-reinforcing знание.

Write pipeline включает:

1. извлечение candidate из завершённого episode;
2. классификацию scope и memory type;
3. удаление transient details;
4. проверку provenance;
5. проверку на secrets и personal data;
6. conflict detection с существующими записями;
7. назначение confidence, TTL и owner;
8. human или rule-based validation для high-impact facts;
9. indexing;
10. audit записи и последующего изменения.

Пример memory record:

```
memory_id: mem-service-82
scope: project:payments
kind: episodic
claim: edge config can lag after certificate rotation
source_episode: incident:INC-42
supporting_artifacts: [artifact://inc-42/postmortem]
created_at: 2026-08-19T11:00:00Z
valid_until: 2026-11-19T11:00:00Z
confidence: medium
owner: team:edge-platform
status: active
```

Фраза сформулирована как возможность, а не как вечный факт. Она содержит источник, expiry и owner.

### Что не следует запоминать автоматически

- secrets и access tokens;
- неподтверждённые догадки;
- tool output без provenance;
- временные resource identifiers без TTL;
- personal data без цели и retention policy;
- полные transcripts «на всякий случай»;
- instructions из недоверенного документа;
- сведения, которые дешевле и надёжнее получить из authoritative API.

## Чтение memory: retrieval не заканчивается vector search

Similarity search отвечает только на вопрос «какие записи лексически или семантически похожи на query?». Для безопасного context нужны дополнительные filters.

Read pipeline:

1. сформировать query из текущего decision point, а не из всей истории;
2. ограничить tenant, user, project и agent scope;
3. отфильтровать expired, revoked и incompatible records;
4. получить candidates через keyword, metadata и semantic search;
5. rerank по relevance, authority, freshness и diversity;
6. обнаружить conflicts;
7. выбрать записи под token budget;
8. добавить provenance и trust labels;
9. сохранить retrieval manifest;
10. предложить revalidation, если память влияет на side effect.

Упрощённый score может учитывать:

```
score = relevance * authority * freshness * scope_match
        - conflict_penalty - redundancy_penalty
```

Это не универсальная формула, а напоминание: высокая cosine similarity не делает запись свежей или авторитетной.

### Конфликтующие memories

Удалять старую запись сразу после появления новой не всегда правильно. Конфликт может отражать изменение мира, разные environments или ошибку retrieval.

Полезный record lifecycle:

- `candidate` - извлечено, но не проверено;
- `active` - допустимо для retrieval;
- `superseded` - заменено более свежей записью;
- `disputed` - есть противоречие;
- `revoked` - признано ошибочным или небезопасным;
- `expired` - TTL закончился;
- `deleted` - удалено по retention/privacy policy.

Модель должна видеть не только claim, но и status. `Disputed` memory используется как повод проверить источник, а не как готовый факт.

### Progressive disclosure

Не нужно помещать каждую найденную запись в prompt. Полезный pattern:

1. короткий index или summary говорит, какие memories существуют;
2. модель или deterministic router выбирает релевантную тему;
3. harness загружает полный record;
4. raw artifact читается только при необходимости.

Так memory помогает, не заполняя context старыми деталями.

## Workspace и artifacts: материальный след работы

Workspace - файловая среда Task или project. В ней могут находиться source checkout, промежуточные файлы, tool configuration и generated artifacts.

Artifacts - адресуемые результаты: report, patch, raw log, screenshot, test output, context manifest. Artifact должен иметь identity, content type, size, hash, producer и retention class.

Workspace не является memory автоматически. Файл становится полезным для агента только после discovery, чтения и включения релевантной части в context. И наоборот, удаление файла из context не удаляет его из workspace.

### Файл, state и artifact играют разные роли

Допустим, агент выполнил diagnostic command.

- Полный stdout сохранён как artifact.
- В state записано: command завершился, exit code 0, artifact ID такой-то.
- В context следующего шага попали только 30 строк вокруг ошибки.
- В persistent memory после завершения может попасть проверенный lesson, но не весь log.

Так сохраняется audit без переполнения prompt.

### Workspace требует lifecycle

Нужны правила:

- namespace и ownership;
- quota;
- allowed paths;
- encryption и secret handling;
- cleanup после Task;
- promotion нужных artifacts в долгоживущее хранилище;
- защита от path traversal и symlink surprises;
- version/hash при чтении файлов моделью.

Путь `/workspace/result.txt` без Task identity и hash слаб для аудита: файл мог измениться между решением и выполнением.

## Process RAM: удобное, но недолговечное состояние

Heap, stack, in-memory cache, open file descriptors и активные coroutines нужны живому process. Они быстрые, но не должны быть единственной копией критического progression.

В RAM допустимо хранить:

- parsed config;
- connection pools;
- short-lived caches;
- buffered telemetry с лимитом;
- current call objects;
- derived prompt representation.

В RAM опасно хранить как единственную копию:

- approval;
- idempotency ledger;
- external operation ID;
- оставшийся budget;
- verified fact;
- terminal evidence;
- единственный экземпляр user-provided artifact.

Process может исчезнуть из-за OOMKill, node failure, deploy, scale-down или обычного restart. Production recovery начинается с предположения, что RAM потеряна.

## Checkpoint и snapshot: логический и технический уровни

Термины используются по-разному в frameworks, поэтому contract важнее названия.

**Logical checkpoint** сериализует application state в безопасной границе: current node, structured state, pending operations, budgets и identifiers. Обычно он не содержит весь heap.

**Runtime snapshot** фиксирует техническое состояние execution environment: filesystem, memory pages, process tree и иногда virtual device state. Его цель - быстро продолжить работу, но точный состав зависит от runtime.

| Свойство | Logical checkpoint | Runtime snapshot |
| --- | --- | --- |
| Уровень | приложение | process/container/VM/runtime |
| Формат | schema, records, journal | memory/filesystem image, runtime metadata |
| Переносимость | выше при versioned schema | зависит от kernel, image, template, architecture |
| Размер | обычно небольшой | может быть большим |
| Понимает domain state | да | обычно нет |
| Восстанавливает sockets | явно reconnect | технически возможно не всегда надёжно |
| Доказывает external outcome | нет | нет |
| Подходит для audit | да, если есть provenance | ограниченно |

Оба механизма полезны. Ошибка - считать runtime snapshot заменой логического checkpoint.

### Snapshot фиксирует момент, но не распределённую транзакцию

В момент snapshot:

- external API мог уже применить write;
- response ещё мог не попасть в process;
- database transaction могла быть open;
- filesystem buffers могли не flush-иться;
- remote lease могла продолжать истекать;
- credential могла стать недействительной через секунду;
- DNS и routing могли измениться.

Resume не возвращает весь внешний мир к прошлому моменту. Поэтому snapshot не является undo и не обеспечивает exactly-once side effects.

### Consistency boundary должна быть явной

Перед logical checkpoint harness стремится достичь состояния, которое можно интерпретировать после restart:

- все completed observations durably записаны;
- незавершённая operation имеет ID и статус;
- approval связан со state version;
- context можно пересобрать;
- artifacts flush-нуты и адресуемы;
- remaining budgets сохранены;
- active lease или heartbeat известен.

Перед runtime snapshot дополнительно полезно:

- остановить создание новых operations;
- дождаться или классифицировать active calls;
- flush durable files;
- закрыть или пометить connections для reconnect;
- сохранить snapshot metadata и compatibility identity.

Полностью quiescent state получить не всегда возможно. Тогда snapshot metadata должна перечислить unknown operations.

## External source of truth нельзя заменить памятью

Для network agent реальный route находится на устройстве или в authoritative controller. Для coding agent реальный commit находится в Git. Для support agent статус case находится в ticket system.

Local state и memory могут содержать projection external world, но projection устаревает.

Каждая запись должна отвечать:

- какой источник authoritative;
- когда projection получена;
- какая freshness нужна для решения;
- как перепроверить;
- что делать при недоступности источника.

Например:

```
projection:
  claim: api.example.com -> 203.0.113.25
  source: authoritative-dns/ns1
  observed_at: 2026-10-05T02:13:22Z
  freshness_slo: 30s
  raw_observation: artifact://dns/probe-91
```

Через пять минут эта запись остаётся полезной историей, но может быть недостаточно свежей для production write.

### Cache, projection и memory

Cache оптимизирует повторное получение и может быть отброшен. Projection представляет external state на конкретный момент. Memory выбирает информацию для будущего reasoning. Один record способен играть несколько ролей физически, но invalidation policy различается.

Если service inventory API дешёв и надёжен, лучше перечитать его, чем хранить вечную semantic memory о владельце сервиса. Memory полезна, когда помогает найти правильный authoritative query.

## Resume contract: восстановить работу, а не ощущение непрерывности

После restart новый process не обязан «помнить», что чувствовал старый. Он обязан безопасно продолжить contract.

Минимальный resume protocol:

1. Захватить lease run и убедиться, что старый worker больше не authoritative.
2. Загрузить schema/version agent specification.
3. Загрузить latest valid logical checkpoint.
4. Проверить integrity state и artifacts.
5. Восстановить budget и cancellation state.
6. Найти operations со статусом `prepared`, `running` или `unknown`.
7. Выполнить reconciliation с external systems.
8. Проверить approval freshness.
9. Обновить projections authoritative state.
10. Пересобрать context из актуальных данных.
11. Только после этого разрешить новый model step.

```
restart
  -> claim lease
  -> load checkpoint
  -> validate schema
  -> reconcile operations
  -> refresh external facts
  -> rebuild context
  -> continue transition
```

Автоматически отправлять в модель последний transcript сразу после restart опасно: там может быть старый plan, а external write уже выполнен.

### Schema evolution

Long-running Task может пережить upgrade agent code. State и memory formats должны иметь version.

Варианты:

- backward-compatible reader;
- explicit migration;
- pin Task к старой agent version;
- fail с human-readable migration requirement.

Тихо игнорировать неизвестные fields или заменять их default значениями опасно для approval и operation ledger.

Snapshot compatibility ещё строже: process image может зависеть от runtime image, kernel features, architecture и library versions. При несовместимости правильнее выполнить fresh process start и восстановить logical state.

## Compaction и summarization

Когда history растёт, harness создаёт summary или compacted state. Это преобразование с потерями.

Хорошая compaction сохраняет:

- objective и constraints;
- verified facts с provenance;
- rejected hypotheses и почему они отклонены;
- принятые decisions;
- open questions;
- pending approvals/operations;
- external identifiers;
- budgets;
- ссылки на raw artifacts.

Плохая compaction сохраняет только плавный narrative. Narrative легко скрывает uncertainty и превращает предположение в факт.

### Summary не переписывает историю

Сохраняйте relation:

```
summary:
  id: sum-19
  covers_events: [evt-100, evt-184]
  generated_by: summarizer@3
  created_at: 2026-10-05T02:30:00Z
  source_hash: sha256:...
  claims:
    - text: edge nodes disagree on certificate serial
      evidence: [obs-41, obs-44]
```

Если summary используется для risky decision, harness может открыть исходные observations.

## Privacy, security и retention

Долгоживущая memory увеличивает пользу и одновременно blast radius утечки.

Для каждого слоя нужна отдельная policy:

| Слой | Типичный риск | Контроль |
| --- | --- | --- |
| Request context | отправка данных provider | redaction, provider/data policy |
| Session history | накопление sensitive dialog | encryption, TTL, access control |
| Working state | подмена progression | integrity, versioning, least privilege |
| Persistent memory | долговременная ошибка или утечка | validation, scope, expiry, deletion |
| Workspace/artifacts | secrets и большие raw payloads | sandbox, quota, malware scan, retention |
| Snapshot | plaintext RAM и credentials | encryption, isolation, compatibility policy |
| Trace | копия prompts/tool results | content controls, sampling, redaction before export |

Нужно поддерживать deletion не только основной записи. Memory item может иметь embedding, cache entry, search index, backup и trace reference. Privacy delete должен знать все производные копии или иметь документированный срок их исчезновения.

### Memory poisoning

Атакующий пытается сохранить инструкцию или ложный факт так, чтобы будущий run автоматически доверял ему.

Защита:

- недоверенный content не повышает свой trust level;
- write pipeline отделён от текущего model response;
- sensitive scopes требуют validation;
- provenance всегда доступен при retrieval;
- memory не расширяет tool authority;
- есть revoke и conflict workflow;
- evals включают malicious memory records.

## Multi-agent boundaries

Не вся память должна быть общей. Shared memory облегчает coordination, но распространяет ошибки и секреты.

Разделяйте:

- private scratch state одного agent;
- Task-shared verified facts;
- team/project memory;
- user preferences;
- organization knowledge;
- public knowledge.

Child agent получает только необходимую projection parent state. Его свободный transcript не должен автоматически становиться parent truth. Parent принимает structured result с evidence и сам решает, что добавить в shared state.

## Что сохраняет AX, а что обязан сохранить агент

Для AX особенно важно не путать durable Workspace и живой process. Runner может стартовать заново, даже если файлы `/workspace` сохранились. Agent harness должен уметь reconstruct progression из durable state.

Практичный contract:

- AX Task хранит инфраструктурный lifecycle и execution metadata;
- Workspace хранит files, artifacts и сериализованный agent state;
- harness хранит state schema, operation ledger и resume logic;
- Model/Gateway предоставляет inference, но не является authoritative memory Task;
- Substrate snapshot может ускорять runtime resume, но не заменяет application recovery;
- external systems остаются источниками истины для своих resources.

Если процесс восстановился из snapshot, harness всё равно проверяет leases, credentials, connections и незавершённые operations. Если snapshot несовместим, fresh process должен продолжить работу из logical checkpoint.

## Куда положить новую информацию: decision table

| Если информация... | Основное место | Дополнительное действие |
| --- | --- | --- |
| нужна только для текущего model decision | Request context | не сохранять без причины |
| определяет допустимый следующий transition | Working state | versioned update |
| является сообщением пользователя | Session history | извлечь structured update при необходимости |
| полезна будущим Tasks | Persistent memory candidate | validation, scope, TTL, provenance |
| большая или бинарная | Artifact store/workspace | в state сохранить reference и hash |
| дешёво получается из authoritative API | Cache/projection | задать freshness, не считать вечной memory |
| нужна только живому adapter | Process RAM | предусмотреть rebuild/reconnect |
| ускоряет восстановление runtime | Snapshot | сохранить compatibility и retention metadata |
| подтверждает внешний side effect | External system + operation ledger | выполнить независимый read-back |

Короткое правило: сохраняйте semantic minimum в state, evidence в artifacts, reusable lesson в memory, а current truth перепроверяйте во внешней системе.

## Reference data model

Ниже не готовая schema, а пример явного разделения.

```
task:
  id: task-908
  objective_ref: artifact://task-908/input
  agent_spec: network-diagnostician@5

state:
  schema_version: 4
  state_version: 27
  phase: verifying
  verified_fact_refs: [fact-19, fact-22]
  pending_operation: op-741
  approval_ref: null
  budgets_ref: budget-908

session:
  id: thread-311
  latest_item_seq: 84
  compaction_ref: summary-12

memory_query:
  scope: project:edge
  retrieved: [mem-71, mem-90]
  manifest_ref: retrieval-551

workspace:
  root: workspace://task-908
  artifacts:
    - id: tls-probe-203
      sha256: 8c1...
      media_type: application/json

checkpoint:
  id: cp-27
  state_version: 27
  operation_ledger_offset: 119
  created_at: 2026-10-05T02:44:00Z

runtime_snapshot:
  id: snap-18
  checkpoint_id: cp-27
  runtime_identity: runner-image@sha256:91e...
  status: compatible

external_projections:
  - resource: dns://example.com/api
    observation_ref: obs-22
    observed_at: 2026-10-05T02:43:41Z
    freshness_slo: 30s
```

Связь snapshot с logical checkpoint особенно полезна: после resume harness знает, какое application state ожидалось в снимке и с какого journal offset проверять новые события.

## Как тестировать state и memory layer

### State invariants

Автоматические тесты проверяют:

- state version растёт монотонно;
- terminal state не возвращается в running;
- один active lease имеет одного owner;
- pending operation существует в ledger;
- approval ссылается на текущий proposal и state version;
- budget не увеличивается после restart;
- verified fact содержит provenance;
- context можно пересобрать из referenced data.

### Recovery matrix

Проверяйте crash в каждой границе:

| Момент сбоя | Что должно произойти после restart |
| --- | --- |
| До записи model response | безопасный новый model call в оставшемся budget |
| После response, до state update | duplicate response игнорируется или применяется один раз |
| После operation prepare | operation reconciled до execution |
| После external write | read-back определяет outcome, blind retry запрещён |
| После artifact upload, до state reference | orphan artifact обнаруживается cleanup/reconciliation |
| Во время compaction | прежний valid summary остаётся active |
| После memory index, до metadata commit | incomplete candidate не участвует в retrieval |
| После snapshot, до suspend status | snapshot lineage проверяется, active process fencing сохраняется |

### Memory evals

Memory оценивается не количеством сохранённых записей. Полезны метрики:

- retrieval precision и recall на реальных задачах;
- доля retrieved items, фактически использованных;
- stale/expired retrieval rate;
- conflict detection rate;
- число corrections пользователем;
- доля записей без provenance;
- memory poisoning success rate;
- token cost retrieved memory;
- improvement verified task success;
- deletion completion time.

Memory, которая увеличивает token usage и уверенность, но не улучшает verified outcome, не приносит пользы.

### Counterfactual tests

Меняйте один слой независимо:

- тот же state, другая memory;
- та же memory, более свежая external projection;
- тот же context, новый state version;
- тот же checkpoint, несовместимый runtime snapshot;
- тот же episode, отозванный memory record.

Правильная система предпочитает свежий authoritative факт старой memory, отклоняет stale approval и умеет продолжить без runtime snapshot.

## Практикум: восстановление агента после неизвестного результата

Сценарий: агент должен заменить backend в load balancer после approval. Tool request получил timeout, затем process завершился.

### До выполнения

Working state:

```
phase: executing
state_version: 14
proposal:
  id: change-51
  target: lb/payment-api/backend-a
  desired: backend-b
approval:
  id: approval-88
  proposal_id: change-51
  state_version: 14
operation:
  id: op-771
  idempotency_key: payment-api-change-51
  status: prepared
```

В session history есть объяснение пользователю. В workspace лежат diff и rollback plan. Persistent memory содержит старый episode о проблемах этого load balancer. External source of truth - API самого load balancer.

### Во время сбоя

Executor отправил operation, но не получил response. В ledger сохраняется `outcome=unknown`. Runtime snapshot отсутствует.

Важно: отсутствие snapshot не является потерей Task, потому что logical checkpoint уже содержит proposal, approval и operation identity.

### После restart

Новый process:

1. получает lease;
2. загружает checkpoint;
3. не подставляет старый transcript модели;
4. видит `op-771` со статусом unknown;
5. читает current backend из load balancer API;
6. находит `backend-b` и change ID, связанный с operation;
7. записывает observation;
8. переводит operation в `applied_unverified`;
9. запускает health probes;
10. только после успешной проверки ставит `verified` и завершает Task.

Если API показывает `backend-a`, harness проверяет operation status по external ID. Только затем policy решает, допустим ли retry с тем же idempotency key.

### Что здесь делает каждый слой

| Слой | Роль |
| --- | --- |
| Context | после reconciliation показать модели актуальные evidence |
| Working state | сохранить phase, proposal, approval и pending operation |
| Session history | сохранить человеческий диалог, но не определять outcome |
| Memory | подсказать прошлый failure mode, не объявлять его причиной |
| Workspace/artifacts | хранить diff, raw responses и probe results |
| Process RAM | новый process строит заново |
| Logical checkpoint | дать точку безопасного application resume |
| Runtime snapshot | мог бы ускорить start, но не обязателен |
| External source of truth | показать, применилось ли изменение реально |

Это и есть правильная mental model: continuity создаётся не иллюзией непрерывной памяти, а согласованными identifiers, evidence и recovery protocol.

## Типичные антипаттерны

**Everything is memory.** Transcript, vector store, state JSON и workspace называют одним термином, поэтому невозможно определить гарантию.

**Transcript as database.** Controller ищет текущий budget или approval в тексте сообщений.

**Context as archive.** В каждый request отправляется вся история «чтобы ничего не потерять».

**Summary as truth.** Model-generated compaction заменяет raw observations и удаляет uncertainty.

**Vector similarity as authority.** Наиболее похожая запись считается правильной без проверки scope, freshness и source.

**Agent writes its own truth.** Любой final answer автоматически становится persistent memory.

**No expiry.** Resource locations, team ownership и procedures живут в memory бесконечно.

**Shared memory by default.** Все agents и tenants читают один store без namespace и capability checks.

**Snapshot equals transaction.** Восстановление RAM считается откатом external side effects.

**Snapshot equals backup.** Единственный runtime image используется как DR strategy.

**Process RAM as checkpoint.** Важные operation IDs существуют только в objects живого процесса.

**Fresh process means new operation.** После Task retry создаётся другой idempotency key и повторяется уже выполненный write.

**Delete only the row.** Основная memory запись удалена, но embedding, cache, snapshot и trace продолжают хранить content.

## Итоги главы

- Context - конечный набор tokens одного model call; его нужно собирать, а не путать с хранилищем.
- Working state - структурированная и versioned модель progression, пригодная для programmatic checks и recovery.
- Session history обеспечивает диалоговую continuity, но не является authoritative state.
- Persistent memory хранит кандидаты для будущего reasoning: semantic, episodic, procedural и preference records.
- Memory write требует validation, scope, provenance, TTL и privacy policy; retrieval требует больше, чем similarity search.
- Workspace хранит файлы и evidence; state хранит ссылки и смысл; context получает только нужную projection.
- Process RAM удобна, но критический progression обязан переживать её потерю.
- Logical checkpoint и runtime snapshot решают разные задачи. Snapshot ускоряет resume, но не откатывает внешний мир.
- External system остаётся source of truth; локальные записи являются timestamped projections.
- Resume начинается с lease, checkpoint и reconciliation, а не с повторной отправки transcript модели.
- AX может сохранять Workspace и управлять lifecycle, но harness отвечает за state schema, ledger и application recovery.
- Качество memory измеряется улучшением verified outcomes, а не количеством записей.

**Дальше.** State описывает progression, но сам по себе не наблюдает среду и не создаёт side effects. В следующей главе разберём interfaces, через которые agent получает capabilities: Tools, MCP и Skills.

### Источники и дальнейшее чтение

- [Anthropic: Effective context engineering for AI agents](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents) - context как конечный набор tokens, compaction и structured note-taking.
- [Anthropic: Effective harnesses for long-running agents](https://www.anthropic.com/engineering/effective-harnesses-for-long-running-agents) - continuity между ограниченными context windows через явные artifacts и progress records.
- [OpenAI Agents SDK: Sessions](https://openai.github.io/openai-agents-python/sessions/) - conversation history, session backends и resume interrupted runs.
- [OpenAI Agents SDK: Agent memory](https://openai.github.io/openai-agents-python/sandbox/memory/) - различие session memory и distilled reusable memory.
- [LangGraph: Persistence](https://docs.langchain.com/oss/python/langgraph/persistence) - checkpoints для thread state и stores для cross-thread memory.
- [LangGraph: Thinking in LangGraph](https://docs.langchain.com/oss/javascript/langgraph/thinking-in-langgraph) - raw state, node boundaries, checkpoint frequency и human interrupts.
- [Anthropic: Contextual Retrieval](https://www.anthropic.com/engineering/contextual-retrieval) - влияние surrounding context на качество retrieval.
- Главы 14 и 39 этой книги - platform-specific semantics suspend, snapshot ownership и compatibility.
