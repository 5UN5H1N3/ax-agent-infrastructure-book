# Tools, MCP и Skills

После главы 5 понятно, где хранится progression и как отличать его от context и memory. Теперь разберём interfaces, через которые agent наблюдает среду и действует в ней, не передавая модели неограниченную власть.

Инженер подключает к агенту Git, Kubernetes, базу данных и систему тикетов. Через несколько дней в каталоге уже пятьдесят операций. Модель путает `get_issue` и `search_issues`, отправляет лишние параметры, а при сетевой ошибке повторяет создание комментария. Команда добавляет MCP server и ожидает, что проблема исчезнет. Она не исчезает: появился стандартный способ доставки тех же плохо спроектированных операций.

Затем создают skill с инструкцией «сначала проверь состояние, потом меняй». Это улучшает поведение на демонстрации, но не запрещает вызов опасного tool и не заменяет approval. Получается система, в которой три полезных механизма выполняют не свои роли.

Главный принцип главы:

**Tool отвечает на вопрос «что система умеет сделать?», MCP - «как capability обнаружить и вызвать через общий протокол?», Skill - «как правильно выполнить класс задач?». Полномочия, policy и подтверждение результата находятся ещё одним слоем выше.**

## Четыре сущности, которые чаще всего смешивают

| Сущность | Что это | Что она не гарантирует | Пример |
| --- | --- | --- | --- |
| API, CLI, library | интерфейс конкретной системы или код реализации | понятность для модели, безопасный retry, единый discovery | Kubernetes API, `git`, Python SDK |
| Tool | формализованная операция, доступная agent loop | транспорт, авторизацию и правильный порядок применения | `dns.lookup`, `issue.create_comment` |
| MCP | протокол обмена context и capabilities между host, client и server | доверие к server, least privilege и безопасность действия | MCP server для GitHub или filesystem |
| Skill | пакет процедурных инструкций, scripts и references | новые полномочия и технический запрет опасных действий | skill расследования TLS incident |

Tool может быть обёрткой над API, локальной функцией, shell command или вызовом другого сервиса. MCP tool является tool, доступным через MCP. Skill может вызывать несколько tools, но сам по себе не обязан быть executable operation.

Есть и пятая сущность - workflow. Это фиксированный или параметризованный граф шагов с явными transitions. Skill советует агенту, как действовать; workflow исполняет заранее определённую структуру. Если порядок обязан быть детерминированным по требованиям compliance, одной инструкции в skill недостаточно.

## Tool как интерфейс между вероятностной моделью и детерминированной системой

Для человека UI снижает вероятность ошибки кнопками, подписями и disabled states. Для агента такую роль играет tool contract. Модель выбирает operation и генерирует arguments вероятностно; executor должен интерпретировать их однозначно.

Хороший tool contract содержит больше, чем JSON Schema:

| Поле | Зачем оно нужно |
| --- | --- |
| Stable name | помогает выбору и сохраняет совместимость traces |
| Purpose | объясняет, когда применять tool и когда не применять |
| Input schema | ограничивает форму аргументов, типы, enum и required fields |
| Preconditions | описывает, что должно быть истинно до вызова |
| Side-effect class | отличает read, reversible write и irreversible action |
| Authority scope | показывает требуемые resource, tenant, role и environment |
| Idempotency | определяет, можно ли повторить request и с каким key |
| Result schema | отделяет data, status, warnings, references и evidence |
| Error model | различает invalid input, denied, retryable, partial и unknown outcome |
| Limits | фиксирует pagination, timeout, payload size и rate limits |
| Version | позволяет менять contract без скрытой поломки agents |

Минимальное описание операции может выглядеть так:

```
name: dns.lookup
description: >
  Читает текущие DNS records для полного domain name.
  Используй для проверки фактического состояния. Не меняет DNS.
input:
  type: object
  required: [fqdn, record_type]
  properties:
    fqdn:
      type: string
      format: hostname
    record_type:
      type: string
      enum: [A, AAAA, CNAME, TXT]
    resolver:
      type: string
      enum: [authoritative, public]
side_effect: none
timeout_ms: 5000
output:
  records: array
  observed_at: date-time
  resolver_identity: string
  ttl_seconds: integer
```

Фраза «работает с DNS» почти бесполезна. Она не сообщает, читает tool или пишет, какой resolver используется и является ли результат authoritative. Description участвует в выборе операции так же, как type system участвует в исполнении.

### Не прячьте важную семантику в свободном тексте

Плохой contract:

```
{
  "name": "manage_cluster",
  "arguments": {
    "request": "Сделай всё необходимое, чтобы починить production"
  }
}
```

Executor вынужден повторно интерпретировать естественный язык. Невозможно заранее проверить target, действие, budget и approval scope.

Лучше разделить операции:

```
{
  "name": "deployment.propose_scale",
  "arguments": {
    "cluster": "prod-eu-1",
    "namespace": "payments",
    "deployment": "api",
    "from_replicas": 8,
    "to_replicas": 12,
    "reason": "queue lag above SLO for 10m"
  }
}
```

Proposal ещё не меняет cluster. Отдельный `deployment.apply_scale` принимает proposal ID, state version, approval ID и idempotency key. Это делает границу между reasoning и side effect видимой.

## Granularity: не micro-tool и не god-tool

Две крайности одинаково неудобны.

**Micro-tools** повторяют каждую низкоуровневую API operation. Каталог разрастается, названия пересекаются, а модель расходует шаги на механическую оркестрацию.

**God-tool** принимает универсальный command или произвольный URL. Он уменьшает каталог, но лишает harness возможности понять intent, проверить arguments и применить точную policy.

Полезная граница tool обычно соответствует осмысленному действию домена:

- имеет одну основную цель;
- возвращает достаточный для следующего решения результат;
- допускает ясную side-effect classification;
- может быть разрешена или запрещена отдельно;
- не требует модели знать внутреннюю пагинацию и служебные API calls.

Например, `logs.search` может сам пройти несколько страниц до заданного `max_events`, но не должен одновременно менять retention policy. `pull_request.prepare` может создать branch, commit и draft PR как одну контролируемую transaction-like operation, если эти шаги всегда образуют единый unit и имеют общий rollback plan.

### Названия должны помогать выбору

Namespace и verb снимают неоднозначность:

```
ticket.search
ticket.get
ticket.create
ticket.add_comment
ticket.transition
```

`get_ticket_data`, `fetch_issue`, `find_task` и `manage_ticket` в одном каталоге заставляют модель угадывать различия. Если два tools трудно различить человеку по name и description, модель тоже будет путаться.

Разделяйте read и write. Не делайте параметр `dry_run=true` единственной границей безопасности в одном универсальном tool: модель может пропустить flag, а policy сложнее проверить intent.

### Эволюция contract без скрытой поломки

Agent-facing contract меняется опаснее обычного internal API. Кодовый client не начнёт внезапно использовать новый endpoint, а модель способна выбрать новый tool сразу после того, как увидела его в catalog. Даже «уточнение» description меняет routing поведения.

Совместимые изменения обычно добавляют optional result field, расширяют документацию или исправляют implementation без изменения semantics. Несовместимые изменения включают:

- новое значение по умолчанию с другим side effect;
- переименование argument;
- изменение единиц измерения;
- превращение полного результата в paginated;
- изменение error из retryable в unknown;
- расширение resource scope;
- перенос read operation в read-and-write.

Для таких изменений создавайте новую contract version или новое имя operation, поддерживайте период миграции и повторяйте selection evals. Version должна описывать не только server package, но и набор schemas, который видел agent.

```
tool_catalog:
  id: observability@18
  protocol: mcp/2026-07-28
  server_digest: sha256:4c1...
  tools:
    logs.search: 3.1
    metrics.query_range: 2.0
  generated_at: 2026-10-05T08:00:00Z
```

При несовместимости лучше явно вернуть `unsupported_contract`, чем попытаться угадать старый meaning. Silent coercion создаёт правдоподобный, но неверный результат.

## Полный жизненный цикл tool call

Model response - только предложение вызвать tool. Production harness проводит его через последовательность проверок:

```
catalog -> model proposal -> parse -> schema validation
-> policy/authority -> approval if required -> executor
-> result normalization -> verification -> observation
```

| Стадия | Ответственность |
| --- | --- |
| Catalog assembly | показать только релевантные и разрешённые capabilities |
| Proposal | выбрать operation и arguments |
| Parsing | получить строго структурированный request |
| Validation | проверить schema, ranges, target и current state version |
| Policy | решить, разрешено ли действие этому agent и этому Task |
| Approval | получить human decision для конкретного proposal |
| Execution | вызвать real system с scoped credentials |
| Normalization | привести provider-specific response к стабильной форме |
| Verification | прочитать source of truth и подтвердить effect |
| Observation | сохранить факт, provenance и uncertainty в state |

Skill может рекомендовать этот порядок. MCP может передать call. Но только harness способен не допустить переход к executor.

### Catalog - часть policy и context budget

Не нужно показывать модели все operations организации. Каталог собирается по пересечению:

```
installed capabilities
AND agent role
AND Task scope
AND environment policy
AND current phase
```

Если Task только анализирует incident, write-tools можно вообще не включать. Это уменьшает и риск, и количество похожих descriptions в context.

Изменение каталога должно иметь version. Записав `tool_catalog=ops-readonly@12` в trace, команда сможет воспроизвести, из чего выбирала модель. При динамическом discovery нельзя считать старый список вечным: server может уведомить об изменениях, а client должен invalidated cache получить заново.

## Результат важен не меньше запроса

Tool result становится evidence для следующего шага. Свалка stdout или HTML на несколько мегабайт перегружает context и скрывает provenance.

Хороший result разделяет машинные поля и ссылки на raw artifact:

```
{
  "status": "succeeded",
  "data": {
    "fqdn": "api.example.com",
    "records": ["203.0.113.7"],
    "ttl_seconds": 60
  },
  "evidence": {
    "observed_at": "2026-10-05T08:15:02Z",
    "source": "authoritative-ns1",
    "artifact_ref": "artifact://runs/81/dns-response.txt"
  },
  "warnings": []
}
```

Большие logs и query results сохраняются как artifacts. В context возвращаются summary, cursor, counts и ссылка. При этом summary не должен уничтожать raw evidence.

### Ошибка - не одна строка

`success=false` недостаточно. Harness должен понимать следующий допустимый переход.

| Outcome | Значение | Типичное действие |
| --- | --- | --- |
| `invalid` | request не соответствует contract | исправить arguments без retry того же request |
| `denied` | policy или authority запретили действие | не обходить; запросить другой scope или завершить |
| `not_found` | resource достоверно отсутствует | проверить target или принять как observation |
| `conflict` | state/version изменилась | перечитать source of truth и построить новый proposal |
| `retryable` | operation не началась или безопасно повторяема | retry по policy и backoff |
| `partial` | выполнена только часть | зафиксировать completed units и reconcile |
| `unknown` | side effect мог произойти, response потерян | не повторять вслепую; проверить operation ID и внешний state |
| `succeeded` | executor сообщил success | всё равно verify критический effect |

Особенно опасно превращать timeout в `failed`. После отправки write timeout означает неизвестный outcome. Повтор с новым idempotency key способен создать второй комментарий, платёж или deployment.

## MCP: общий протокол, а не магический слой интеграции

Model Context Protocol стандартизует обмен между AI application и поставщиками context/capabilities. В архитектуре есть три роли:

- **MCP host** - приложение, управляющее agents и соединениями;
- **MCP client** - компонент host для связи с конкретным server;
- **MCP server** - программа, предоставляющая capabilities и context.

Один host обычно создаёт отдельный client для каждого server. Server может работать локально или удалённо. Локальность не означает доверие: локальный process всё равно может читать files, использовать credentials и выполнять code.

MCP разделён на data layer и transport layer. Data layer использует JSON-RPC и описывает discovery, calls и core primitives. Transport layer отвечает за framing, соединение и transport-specific authentication.

### Три server primitives

| Primitive | Назначение | Пример |
| --- | --- | --- |
| Tools | исполняемые operations | запрос к API, запись в issue tracker |
| Resources | читаемый context | schema, file, database record |
| Prompts | reusable interaction templates | шаблон review или investigation |

Это классификация интерфейсов, а не security model. Resource способен содержать malicious instruction. Tool может иметь разрушительный side effect. Prompt может конфликтовать с policy host.

Resources полезны, когда context нужно читать без маскировки под действие. Schema базы, runbook или содержимое файла не обязаны быть tools вида `get_schema()`: resource сохраняет семантику «данные для чтения». Prompts дают reusable starting template, но host решает, показывать ли его пользователю и как объединять с собственными instructions.

Не превращайте каждый primitive в другой ради удобства client. Если resource вызывает дорогое вычисление или раскрывает чувствительные данные, к нему всё равно применяются access control, limits и audit. Если prompt содержит instruction, он всё равно ниже platform policy. Чёткая классификация помогает host правильно отображать intent и применять разные правила.

### Что изменилось в MCP 2026-07-28

Актуальное ядро MCP stateless: старый protocol-level session lifecycle и `initialize/initialized` больше не являются основой протокола. Каждый request несёт protocol version и относящиеся к нему capabilities в `_meta`. Server предоставляет `server/discover`, через который client получает identity, поддерживаемые versions и capabilities. Списки primitives могут кешироваться и обновляться при изменениях.

Практический вывод: не проектируйте новый client по ранним примерам 2024-2025 годов без проверки версии specification. Но и не путайте stateless protocol с stateless application: OAuth flow, job execution, pagination и business operation могут иметь собственное durable state.

### STDIO или Streamable HTTP

| Вариант | Когда уместен | Риски и обязанности |
| --- | --- | --- |
| STDIO | локальный adapter, один host, controlled package | process launch, filesystem access, inherited environment, package supply chain |
| Streamable HTTP | shared remote service, централизованное обновление, много clients | network identity, TLS, OAuth/token scope, tenancy, rate limits, availability |

STDIO убирает сеть между client и server, но не делает server sandbox. Remote HTTP упрощает централизованное управление, но добавляет отдельную trust boundary и необходимость связывать user, client, tenant и downstream credentials.

## MCP не выдаёт доверие автоматически

![MCP trust boundary: протокол не заменяет авторизацию](.gitbook/assets/diagrams/06-07.png)  
*MCP trust boundary: протокол не заменяет авторизацию*

На схеме ниже protocol заканчивается раньше реального side effect. Policy gateway может быть частью host, отдельным proxy или server, но его свойства должны быть явными.

MCP server сообщает metadata о себе и tools, однако self-reported identity не является доказательством происхождения. Установленный package, endpoint, publisher и версия должны проходить обычный supply-chain review.

Credentials следует связывать с минимальным scope:

```
binding:
  server: git-prod@2.4.1
  agent_role: reviewer
  task_scope: repo:payments-api
  credentials: github-app-installation:381
  allowed_tools:
    - pull_request.read
    - checks.read
  denied_tools:
    - repository.delete
    - branch.force_push
```

Модель не должна видеть raw token. MCP server тоже не обязан получать широкие user credentials, если gateway может обменять identity на short-lived scoped token.

Подробные механизмы idempotency, approvals, containment и authority attenuation рассматриваются в главе 37. Здесь важно удержать границу ответственности: transport authentication подтверждает сторону соединения, но business authorization всё ещё проверяет конкретное действие над конкретным resource.

## Skills: переносимая процедурная экспертиза

Agent Skill - директория, в которой обязательно есть `SKILL.md` с metadata и инструкциями. Дополнительно она может содержать scripts, references, assets и другие ресурсы.

```
tls-incident/
|- SKILL.md
|- scripts/
|  `- compare_cert_chain.py
|- references/
|  |- decision-tree.md
|  `- provider-notes.md
`- assets/
   `- incident-report-template.md
```

В `SKILL.md` обязательны `name` и `description`. Description должна объяснять не только действие, но и условия применения: именно эти metadata помогают агенту решить, активировать ли skill.

```
---
name: tls-incident
description: >
  Расследует TLS handshake, certificate chain и routing incidents.
  Используй при certificate errors, hostname mismatch, expiry alerts
  или расхождении сертификатов между edge nodes.
compatibility: Requires openssl and read-only network access
metadata:
  owner: sre-platform
  version: "3.2"
---
```

### Progressive disclosure

Skill не должен целиком занимать context с начала Task. Open specification предполагает три уровня:

1. startup metadata - name и description всех доступных skills;
2. полный `SKILL.md` после активации;
3. scripts, references и assets только по необходимости.

Это позволяет хранить глубокую экспертизу, не оплачивая её tokens в каждом request. Но progressive disclosure работает только при хорошей навигации. `SKILL.md` должен явно говорить, какой reference читать при каком condition. Цепочка из десяти ссылок превращает skill в лабиринт.

### Skill не является permission

Инструкция «никогда не изменяй production без approval» полезна, но остаётся инструкцией для вероятностной модели. Enforcement обязан находиться в tool gateway или executor. Экспериментальное поле `allowed-tools` в specification не следует считать универсальным security boundary: поддержка зависит от implementation, а реальная authority определяется runtime.

Skill способен:

- объяснить decision process;
- выбрать последовательность tools;
- предоставить domain checklists и examples;
- запускать проверенные deterministic scripts;
- формировать отчёт по template;
- напомнить о verification и rollback.

Skill не способен сам по себе:

- создать credentials, которых нет у runtime;
- запретить executor обойти policy;
- доказать безопасность bundled script;
- превратить model suggestion в transaction;
- подтвердить внешний side effect без чтения source of truth.

### Scripts внутри skill

Script полезен, когда операция лучше выражается детерминированным кодом: parse формата, сортировка, проверка certificate chain, преобразование document. Агенту не обязательно загружать весь script в context, чтобы выполнить его.

Но bundled code является executable dependency. До установки проверяйте:

- publisher и provenance;
- hash или signed version;
- network и filesystem access;
- transitive dependencies;
- secret handling;
- reproducibility и tests;
- поведение при malformed input.

Skill из неизвестного repository требует не меньшего review, чем новый CI action или package.

## Как Tool, MCP и Skill работают вместе

Вернёмся к TLS-инциденту из главы 3. Там нас интересовали agent loop, hypotheses и transitions; здесь тот же scenario показывает разделение Tool, MCP и Skill. Рассмотрим ошибку `NET::ERR_CERT_DATE_INVALID`.

Tools:

```
dns.lookup                  read DNS records
tls.probe                   read chain from selected endpoint
certificate.inventory_get  read expected certificate metadata
load_balancer.read_config   read current listener configuration
change.propose              create non-executing change proposal
```

MCP server публикует schemas этих tools и resources с inventory documentation. Host через client получает catalog, но показывает agent только read operations. Skill `tls-incident` задаёт процедуру:

1. зафиксировать hostname, user-visible error и время;
2. сравнить public и authoritative DNS;
3. выполнить probe для каждого resolved endpoint;
4. сравнить observed chain с inventory;
5. разделить observation, hypothesis и conclusion;
6. сформировать proposal, но не применять изменение;
7. приложить raw evidence и команды воспроизведения.

Один из результатов:

```
finding:
  status: confirmed
  claim: edge-3 serves an expired certificate
  evidence:
    - probe://edge-3/2026-10-05T08:15:31Z
    - inventory://cert-882/revision-9
  unaffected_endpoints: [edge-1, edge-2]
proposal:
  action: reload_certificate_bundle
  target: edge-3
  expected_revision: 144
  execution: not_requested
```

Здесь skill повышает качество расследования, MCP уменьшает стоимость интеграции, tools дают наблюдения, а harness не допускает write без отдельной фазы и authority.

### Что делать, если MCP server недоступен

Failure server не должен превращаться в фантазию модели. Host отмечает capabilities unavailable, сохраняет точную ошибку discovery/call и пересобирает catalog. Skill может предложить approved fallback - например, локальный read-only probe, - но не должен советовать обходить policy произвольным shell command.

Если fallback меняет источник данных, в evidence указывается новый source. Результат public resolver не выдают за authoritative DNS только потому, что основной tool временно недоступен.

## Когда выбирать Tool, MCP, Skill или Workflow

| Потребность | Предпочтительный механизм |
| --- | --- |
| одна стабильная операция внутри приложения | direct tool/function |
| capability должна работать в нескольких MCP-compatible hosts | MCP server |
| нужно стандартизовать чтение resources и вызов tools внешней системы | MCP server |
| агенту не хватает domain procedure и examples | Skill |
| повторяется анализ с несколькими существующими tools | Skill |
| последовательность обязана быть детерминированной и audit-ready | Workflow/state machine |
| требуется жёсткий запрет или approval | Harness policy/gateway |
| нужен чистый deterministic transform | library или script, возможно внутри Skill |

Не каждый REST API нужно немедленно превращать в MCP server. Если tool живёт только внутри одного service и не требует portable discovery, direct adapter проще. И наоборот, копировать десять разных provider adapters в каждый host дороже, чем поддержать один качественный MCP server.

Не каждый runbook нужен как skill. Короткое неизменное правило может жить в policy. Skill оправдан, когда есть distinct trigger, процедура, references, examples и независимый lifecycle.

## AX Workspace как декларация среды

AX Workspace может связывать repositories, files, MCP endpoints и skill locations с рабочим окружением agent. Это полезно: Task получает воспроизводимую карту доступных assets вместо ручной настройки.

Но Workspace manifest - декларация, а не свидетельство безопасности. До activation platform должна разрешить:

- откуда получен server или skill;
- какая версия и digest разрешены;
- какие secrets и mounts доступны;
- какие tools войдут в catalog;
- какой network egress допустим;
- кто одобрил изменение dependency.

```
workspace:
  repositories:
    - name: payments-api
      ref: 31f27ab
      mode: read-write-branch
  mcp_servers:
    - name: observability
      endpoint: https://mcp.ops.example/v1
      version: 2026-07-28
      tool_policy: policies/observability-readonly.yaml
  skills:
    - name: tls-incident
      source: registry://sre/tls-incident@3.2.0
      digest: sha256:...
```

Version pinning позволяет воспроизвести Task. Автоматическое подключение `latest` делает trace неполным: одинаковая цель завтра может активировать другие instructions или schemas.

## Как тестировать agent-facing interface

Обычный unit test проверяет, что function возвращает ответ. Этого недостаточно. Нужно проверить, как вероятностный caller понимает contract.

### Contract tests

- valid и invalid arguments;
- enums, boundary values и malformed Unicode;
- pagination и payload limits;
- timeout до и после начала side effect;
- stable error codes;
- backward compatibility result schema;
- idempotency и conflict handling.

### Selection evals

Соберите representative prompts, включая неоднозначные и отрицательные cases. Измеряйте:

- выбрал ли agent правильный tool;
- не вызвал ли tool, когда достаточно ответа или resource;
- правильно ли заполнил arguments;
- распознал ли необходимость clarification;
- остановился ли после denial;
- отличил ли read от write;
- использовал ли skill только по релевантному trigger.

Оценивать нужно не красивый final answer, а состояние environment и trace. Агент мог написать «изменение применено», хотя call был denied. Успех определяется verified external outcome.

### Adversarial cases

Добавьте stale catalog, duplicated names, compromised resource text, partial result, lost response, revoked token и изменившийся state version. Для skills проверяйте malicious reference, неожиданную network dependency и конфликт bundled instruction с host policy.

Полезная минимальная матрица:

| Case | Ожидаемое поведение |
| --- | --- |
| tool отсутствует в текущем catalog | не выдумывать call; сообщить ограничение |
| server description просит передать secret | policy блокирует; agent фиксирует injection attempt |
| write timeout после отправки | outcome `unknown`, затем reconciliation |
| skill предлагает запрещённый shell fallback | instruction игнорируется, policy сохраняется |
| schema изменилась | client invalidates cache или сообщает incompatibility |
| approval относится к старой state version | execution блокируется, proposal строится заново |

## Типичные антипаттерны

**MCP means safe.** Общий protocol принимают за trust и authorization.

**Skill means capability.** Инструкция обещает действие, для которого нет tool, credentials или executor.

**Skill means policy.** Текстовое правило используется вместо enforced permission.

**API dump as tools.** Сотни provider operations публикуются без domain design и selection evals.

**One tool to rule them all.** Универсальный `execute(command)` скрывает intent и side effects.

**Read and write together.** Один tool меняет систему в зависимости от optional flag.

**Success equals verified.** HTTP 200 считается доказательством нужного состояния внешней системы.

**Timeout equals failure.** Write автоматически повторяется после потерянного response.

**Raw output into context.** Мегабайты logs вытесняют objective, policy и evidence.

**Self-reported identity as trust.** `serverInfo` используется вместо проверенного endpoint, publisher и digest.

**Latest everywhere.** Server и skill обновляются без version pinning, trace теряет воспроизводимость.

**Description afterthought.** Names и descriptions пишутся как документация для человека, хотя они управляют выбором модели.

**No negative evals.** Проверяют только happy path «agent вызвал tool», но не способность отказаться от вызова.

## Практический checklist

Перед добавлением capability спросите:

1. Это operation, protocol adapter, procedure или deterministic workflow?
2. Может ли name однозначно отличить capability от соседних?
3. Какой side effect и authority scope?
4. Что произойдёт при duplicate, timeout и partial completion?
5. Где хранится raw evidence и что попадёт в context?
6. Как проверить реальный outcome?
7. Какая версия contract записывается в trace?
8. Кто доверяет server или skill и на каком основании?
9. Можно ли убрать write-tool из catalog до нужной фазы?
10. Есть ли eval, где правильное действие - не вызывать tool?

Если на вопросы 3, 4 и 6 нет ответа, capability ещё не готова к production, даже если demo работает.

## Итоги главы

- Tool - agent-facing contract осмысленной операции, а не просто обёртка над function.
- API и CLI являются реализацией; schema, side effects, errors и verification делают их пригодными для agent loop.
- Granularity должна сохранять domain intent и точную policy: избегайте и micro-tool explosion, и универсального god-tool.
- Model предлагает call; harness проверяет schema, authority, approval, execution и outcome.
- Structured results, provenance и отдельный raw artifact полезнее большого stdout в context.
- Timeout write означает unknown outcome, пока reconciliation не доказал обратное.
- MCP стандартизует discovery и обмен tools, resources и prompts между host, client и server.
- MCP 2026-07-28 использует stateless core и per-request metadata; application state при этом никуда не исчезает.
- Protocol, transport authentication и business authorization решают разные задачи.
- Skill пакует procedural knowledge, scripts и references с progressive disclosure.
- Skill помогает выбрать и связать tools, но не создаёт authority и не заменяет enforced policy.
- MCP servers и Skills входят в software supply chain и требуют provenance, pinning и review.
- AX Workspace описывает рабочую среду, но platform всё равно должна проверять dependencies, scopes и secrets.
- Тестировать нужно tool selection, arguments, отказ, failure semantics и verified environment state.

**Дальше.** Теперь один agent умеет безопасно получать observations и выполнять actions. Следующая глава покажет, когда одну такую петлю действительно стоит разделить на несколько агентов и как не потерять ownership, evidence и budgets.

### Источники и дальнейшее чтение

- [Model Context Protocol: Architecture overview](https://modelcontextprotocol.io/docs/2026-07-28/learn/architecture) - host/client/server, layers, primitives, statelessness и discovery.
- [MCP: The 2026-07-28 Specification](https://blog.modelcontextprotocol.io/posts/2026-07-28/) - изменения текущей версии protocol.
- [Agent Skills Specification](https://agentskills.io/specification) - структура `SKILL.md`, optional directories и progressive disclosure.
- [Anthropic: Equipping agents for the real world with Agent Skills](https://www.anthropic.com/engineering/equipping-agents-for-the-real-world-with-agent-skills) - procedural knowledge, code execution, evaluation и security considerations.
- [Anthropic: Writing effective tools for agents](https://www.anthropic.com/engineering/writing-tools-for-agents) - tool design как contract для недетерминированного caller.
- [Anthropic: Building effective agents](https://www.anthropic.com/engineering/building-effective-agents) - agent-computer interfaces, testing и guardrails.
- [Anthropic: Effective context engineering for AI agents](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents) - ясные, непересекающиеся и context-efficient tool sets.
- [Anthropic: Demystifying evals for AI agents](https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents) - multi-turn evals и проверка environment state.
- Главы 36 и 37 этой книги - подробности MCP architecture, idempotency, approvals и safe tool execution.
