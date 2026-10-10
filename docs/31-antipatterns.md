# Антипаттерны

**Антипаттерн - не просто «плохой код».** Обычно это удобное локальное решение, которое скрывает системный риск. Один общий token ускоряет первый demo. Бесконечный retry помогает пережить случайный timeout. Wildcard egress убирает сетевые помехи. Проблема проявляется позже, когда shortcut превращается в постоянный контракт и несколько безобидных допущений складываются в один серьёзный incident.

В агентной системе цена таких ошибок выше, чем в обычном service. LLM выбирает следующий шаг вероятностно, читает недоверенный контент и может вызывать tools с внешними эффектами. AX/Substrate добавляет isolation и lifecycle primitives, но платформа не исправит неверную границу полномочий, плохую retry policy или отсутствие authoritative state. Sandbox ограничивает место выполнения; безопасность результата всё равно определяется всей цепочкой от prompt до downstream API.

**Снимок главы:** AX main `ac233282`, Substrate main `df78882`, внешние рекомендации проверены 2026-10-10. Конкретные поля API нужно сверять с закреплённой версией, а инженерные проверки ниже должны оставаться частью собственного threat model и production readiness review.

## Как распознать антишаблон до инцидента

Начинайте не с названия framework-а, а с шести вопросов. Если команда не может дать проверяемый ответ, архитектура пока держится на надежде.

| Граница | Контрольный вопрос | Что должно быть видно в evidence |
| --- | --- | --- |
| Authority | Кто и от чьего имени может читать, изменять и удалять? | Scoped identity, перечень разрешённых actions, срок действия и субъект делегирования |
| State | Где находится истина после compaction, restart или потери node? | Structured durable record, version/epoch, action journal и ссылка на artifacts |
| Effects | Что произойдёт, если один шаг будет доставлен или выполнен дважды? | Idempotency key, precondition, deduplication и проверка post-state |
| Network | К каким destinations реально нужен доступ? | Default deny, узкий allowlist, DNS/TLS policy и отрицательные probes |
| Lifecycle | Что является самостоятельной единицей изоляции, ресурса и восстановления? | Осмысленная Task boundary, owner, budget, timeout и terminal outcome |
| Operations | Как отличить полезный progress от процесса, который просто ещё жив? | Trace по task/tool/action, domain metrics, deadline и recovery drill |

Полезный практический тест: мысленно замените LLM на ошибочного, но очень быстрого стажёра, который буквально исполняет доступные команды. Если безопасность зависит от того, что стажёр «догадается не делать опасное», контроль находится не на той границе.

## Как антишаблоны собираются в цепочку отказа

Представим coding agent, который читает repository с вредоносной инструкцией в issue или README. У агента общий privileged token, доступен shell, egress открыт, state живёт только в context, а tool timeout повторяется без лимита. Ни один элемент по отдельности не гарантирует катастрофу. Вместе они дают цепочку:

1. Недоверенный текст влияет на план и предлагает отправить «диагностику» на внешний endpoint.
2. Гигантский agent уже имеет repository write, secrets read и deployment permissions.
3. Wildcard egress позволяет установить соединение.
4. Timeout делает outcome неоднозначным; retry повторяет side effect.
5. После compaction исчезает запись о предыдущей попытке, и действие выглядит новым.
6. Shared identity не позволяет уверенно восстановить, какая Task и от чьего имени выполнила операцию.

Защита строится не одним «лучшим prompt». Цепочку должны независимо разрывать scoped tool, short-lived identity, egress policy, durable action journal, idempotency и approval перед high-impact effect. Ни один слой не идеален, поэтому важна композиция.

## 1. Один гигантский агент

**Запах.** Один process планирует, читает все данные, вызывает любые tools, хранит весь context и имеет credentials на весь workflow. В demo это удобно: нет protocol между ролями и всё видно в одном окне. В production такой agent объединяет слишком разные authority, failure domain и cognitive context.

**Почему ломается.** Чем больше tools и данных доступно одновременно, тем выше blast radius ошибочного выбора и тем труднее понять, какой фрагмент context повлиял на action. Независимые задачи конкурируют за context window; секреты и результаты одного tenant-а могут попасть в reasoning другого; долгий agent loop становится общей точкой отказа. OWASP называет сочетание избыточной functionality, permissions и autonomy проблемой excessive agency.

**Замена.** Разделяйте систему не на декоративные «персоны», а по реальным границам:

- **authority:** read-only исследователь не получает write tool;
- **data:** agent видит только нужный Workspace или набор документов;
- **effect:** планирование отделено от execution, а high-impact action проходит approval;
- **lifecycle:** работа с отдельным timeout, budget и recovery становится самостоятельной Task;
- **trust:** недоверенный код исполняется в отдельной sandbox, а не внутри coordinator.

Coordinator должен передавать bounded task description, ссылки на authoritative artifacts и budget, а не копировать весь внутренний transcript. Исполнитель возвращает typed result с evidence. Разделение оправдано, если уменьшает полномочия или failure radius; создавать пять агентов с одинаковыми credentials и общим context бессмысленно.

**Проверка исправления.** Компрометация любого одного executor не даёт изменить объект вне его scope. По audit trail можно восстановить parent Task, делегированную capability, tool call, approval и post-state.

## 2. Agent вместо deterministic logic

**Запах.** LLM проверяет CIDR, обязательные поля JSON, арифметический budget, allowlist, health condition или право пользователя на action. Ответ выглядит правдоподобно, поэтому команда постепенно превращает model judgment в policy engine.

**Почему ломается.** Вероятностная модель может по-разному решить один и тот же формальный вопрос, принять убедительное объяснение вместо факта или пропустить крайний случай. Prompt не является надёжным механизмом enforcement: его можно вытеснить context-ом, исказить недоверенными данными или изменить вместе с model version.

**Замена.** Разделите proposal и enforcement:

- LLM интерпретирует неоднозначный запрос, предлагает план и объясняет намерение;
- parser/schema validator проверяет форму;
- policy code вычисляет разрешённый scope;
- tool adapter строит конкретный bounded request;
- downstream system повторно авторизует identity и проверяет preconditions;
- verifier читает post-state и сравнивает его с intended outcome.

Например, модель может предложить «разрешить egress к registry проекта», но DNS name, port, protocol, tenant ownership и срок действия валидирует код. Модель может классифицировать incident, но не должна сама объявлять health check успешным без измеримого predicate.

**Проверка исправления.** Для каждого запрета существует deterministic negative test. Замена модели, temperature или prompt не меняет решение policy при одинаковом structured input.

## 3. State только в prompt

**Запах.** Единственная запись о progress находится в conversation history: «мы уже создали branch», «approval получен», «этот tool call не удался». После context compaction, restart или handoff система пытается восстановить факты из естественного языка.

**Почему ломается.** Prompt - рабочая память, а не журнал транзакций. Он обрезается, суммируется и содержит одновременно факты, предположения и инструкции. Snapshot процесса тоже не решает задачу полностью: внешняя операция могла завершиться после последнего checkpoint, а её ответ потеряться.

**Замена.** Разделите минимум четыре вида state:

| Вид state | Пример | Где хранить |
| --- | --- | --- |
| Conversation context | Объяснения, промежуточное reasoning, последние сообщения | Prompt/checkpoint framework-а; можно сокращать |
| Workflow state | Шаг, owner, deadline, approval, attempt, terminal outcome | Durable structured store или workflow history |
| Artifacts | Patch, report, test log, dataset, Workspace content | Versioned repository/object store/Workspace с явной ссылкой |
| Action journal | Intent, idempotency key, request, response и verified post-state | Append-oriented durable record |

Каждый переход должен иметь version или epoch. Actor, возобновившийся со старого snapshot, не имеет права перезаписать более новый workflow state. Прежде чем повторить действие с неоднозначным outcome, он сначала читает journal и downstream state.

**Проверка исправления.** Удалите весь prompt и перезапустите Task. Система должна определить следующий допустимый шаг, уже выполненные effects и доступные artifacts без догадок по chat transcript.

## 4. Unlimited retries

**Запах.** Любая ошибка приводит к «попробуй ещё раз». LLM меняет формулировку, SDK повторяет HTTP request, queue повторно доставляет message, а workflow ещё раз запускает Activity. Лимиты каждого слоя рассматриваются отдельно, поэтому суммарное число попыток никто не знает.

**Почему ломается.** Retry умножает load именно тогда, когда dependency уже деградирует. Без idempotency повторяются платежи, commits, tickets и infrastructure changes. Backoff без jitter синхронизирует клиентов. Semantic error никогда не исправится ожиданием, а policy denial не должен обходиться новой формулировкой. AWS Well-Architected рекомендует ограничивать retries, применять backoff/jitter и сначала проверять idempotency.

**Замена.** Классифицируйте outcome до retry:

| Класс | Пример | Действие |
| --- | --- | --- |
| Transient | 429, временный network reset, leader election | Ограниченный retry с exponential backoff, jitter и deadline |
| Permanent | Invalid schema, отсутствующий object, несовместимая version | Fail fast; исправить input или deployment |
| Policy | Forbidden, approval отсутствует, scope превышен | Не повторять автоматически; запросить новую authority |
| Ambiguous | Timeout после отправки write request | Сначала reconcile по idempotency key или post-state |
| Capacity | Queue saturation, snapshot store overload | Load shedding/admission control, а не массовый retry |

Вводите единый retry budget на root request, а не независимый лимит в каждом слое. Записывайте attempt number и причину. После исчерпания budget задача переходит в terminal state или manual review, а не начинает новый скрытый loop.

**Проверка исправления.** Fault injection на timeout после успешного side effect создаёт ровно один внешний результат. При массовом отказе dependency исходящий request rate уменьшается, а не растёт.

## 5. Shared root credentials и excessive agency

**Запах.** Все Tasks используют один долгоживущий token администратора. Prompt просит «не трогать production», но технически tool способен читать secrets, менять ACL и удалять resources. В audit log виден только общий service account.

**Почему ломается.** Изоляция container-а не ограничивает облачный API, если credential имеет глобальные права. Общая identity уничтожает attribution и делает отзыв доступа аварией для всех agents. Срок жизни Task может быть минутами, а украденный token действует месяцами.

**Замена.** NIST формулирует least privilege как выдачу только минимальных resources и authorizations, необходимых сущности для её функции. Для agents это означает:

- identity на user/tenant/Task или хотя бы на role и execution scope;
- short-lived credential, выдаваемый после policy decision;
- отдельные read, propose и execute capabilities;
- resource selectors и preconditions внутри capability, а не только в prompt;
- approval для high-impact action на границе tool/downstream API;
- немедленный revoke при завершении, cancellation или изменении scope.

Human approval не должен быть театром. Человек видит конкретный diff: какой action, над каким object, с какими параметрами и ожидаемым эффектом. После approval нельзя незаметно подменить payload.

**Проверка исправления.** Украденная capability одной Task бесполезна для другого tenant-а, объекта, action или времени. Downstream audit связывает effect с user, root Task, approval и конкретным attempt.

## 6. `egress: *` как постоянная policy

**Запах.** Для первого запуска открыли весь outbound traffic и больше к policy не возвращались. Считается, что container isolation уже достаточно, а HTTPS делает соединение безопасным.

**Почему ломается.** Egress - путь exfiltration и command-and-control. Разрешение любого destination превращает prompt injection или compromised dependency в сетевой инцидент. В Kubernetes Pod по умолчанию не изолирован для egress; restrictions появляются только когда подходящая NetworkPolicy выбирает Pod, причём разрешающие правила складываются аддитивно.

**Замена.** Начинайте с default deny и добавляйте измеренные зависимости: model endpoint, выбранный Git/MCP service, artifact store, DNS и необходимые control-plane endpoints. Контролируйте не только IP: динамический DNS, CDN, TLS SNI, proxies и redirects могут расширить фактический scope. Для HTTP/API полезен application gateway, который проверяет identity, method, host и resource, а не просто разрешает TCP 443.

Policy должна иметь owner, причину, срок пересмотра и отрицательные probes. Временное диагностическое разрешение оформляется как time-bound exception с audit, а не как edit постоянного manifest-а.

**Проверка исправления.** Из Task успешны только документированные destinations. Запрос к test exfiltration domain, metadata endpoint, private ranges и неожиданному redirect блокируется и создаёт наблюдаемый security event.

## 7. Debug всегда включён

**Запах.** Debug shell, exec, verbose logs, port-forward или расширенный tool set остаются включёнными «на случай инцидента». Операционный обход постепенно становится обычным интерфейсом.

**Почему ломается.** Debug path часто обходит нормальные policy, открывает process/filesystem state и записывает prompts, secrets или tool payloads в logs. Постоянный доступ увеличивает attack surface, а его редкое использование означает, что путь плохо тестируется именно перед аварией.

**Замена.** Считайте debug отдельной привилегированной операцией: just-in-time enablement, named operator, ticket/incident ID, ограниченный TTL, scoped target, session audit и автоматическое выключение. Собирайте безопасную telemetry заранее, чтобы стандартная диагностика не требовала shell. Sensitive content логируйте только по явной opt-in policy с redaction и retention.

**Проверка исправления.** Обычная Task не может активировать debug самостоятельно. Просроченная session закрывается автоматически; audit показывает, кто, зачем и какие команды выполнял.

## 8. Task per thought

**Запах.** Каждый внутренний reasoning step, LLM turn или вызов простого read tool создаёт новую AX Task. Архитектура выглядит «по-настоящему multi-agent», но control plane используется как message bus.

**Почему ломается.** На один пользовательский outcome приходятся десятки create/watch/schedule/delete transitions, Workspaces и snapshots. Latency и стоимость определяются control-plane churn, а не полезной работой. Теряется единый context, растёт число partial failures и усложняется cancellation.

**Замена.** Создавайте Task, когда меняется хотя бы одна существенная граница: trust/authority, resource envelope, image/runtime, Workspace visibility, lifecycle/deadline или независимый recovery. Обычный reasoning, локальный parsing и серия безопасных reads могут оставаться шагами одного harness.

| Работа | Обычно шаг внутри Task | Обычно отдельная Task |
| --- | --- | --- |
| Суммировать два уже доступных файла | Да | Нет отдельной boundary |
| Запустить untrusted repository code | Нет | Новая sandbox и resource limit |
| Попросить модель уточнить план | Да | Тот же authority и Workspace |
| Делегировать write в другой tenant/project | Нет | Новая identity, approval и audit |
| Долгая независимая проверка с собственным SLO | Зависит | Отдельный lifecycle и retry budget оправданы |

**Проверка исправления.** Для каждой Task можно одним предложением назвать изменившуюся boundary. Метрика Tasks per successful outcome имеет объяснимый диапазон и не растёт вместе с числом внутренних мыслей модели.

## 9. Snapshot как backup или источник истины

**Запах.** Команда считает, что раз Actor можно suspend/resume, то данные защищены. Snapshot хранится рядом с рабочим state, не имеет независимой retention policy, restore никогда не проверялся.

**Почему ломается.** Snapshot оптимизирует продолжение конкретного process image. Он может содержать старые credentials, несовместимый runtime state и ссылки на внешние connections, которые уже не существуют. Он не фиксирует согласованно состояние внешних DB/API и может быть удалён вместе с Tag или lifecycle object. Повреждение платформы, ошибка оператора и security incident способны затронуть и рабочий state, и snapshots одновременно.

**Замена.** Определите authoritative data и отдельный recovery contract:

- RPO/RTO для PostgreSQL, object store, repositories, manifests и action journal;
- backup в отдельном failure/security domain с retention и immutability по требованиям;
- versioned export/rebuild path для Workspace и platform configuration;
- регулярный restore drill на чистой среде;
- snapshot только как cache ускоренного resume, который допустимо потерять и перестроить.

Документация PostgreSQL показывает различие между crash recovery и backup/PITR: для восстановления нужны base backup и сохранённая WAL history, а не просто память работающего process.

**Проверка исправления.** Удалите Actors, Workers и snapshot storage в test environment. Команда восстанавливает authoritative state в пределах RPO/RTO и явно знает, какие transient данные потеряны.

## 10. Benchmark на одном пустом Actor

**Запах.** Измерили десять suspend/resume пустого process на свободном cluster и объявили capacity. Среднее latency выглядит отлично; correctness, tail и failure recovery не проверяются.

**Почему ломается.** Реальная стоимость зависит от dirty memory, числа файлов, compression, snapshot store throughput, image diversity, Worker cache, concurrent wakeups и downstream latency. Среднее скрывает p99, а successful API response не доказывает правильный outcome. Вблизи saturation система становится нелинейной: очереди растут, retries усиливают load, control plane и store конкурируют за ресурсы.

**Замена.** Benchmark должен воспроизводить распределение workload-а:

- малые, типичные и крупные Workspace/snapshot sizes;
- cold и warm start, clean и dirty memory;
- steady load, burst create/resume и массовую cancellation;
- node loss, slow store, network partition и несовместимый image;
- реальные proportions read/write/tool/model operations;
- p50/p95/p99 latency, goodput, error taxonomy, correctness и cost per successful outcome;
- soak run для leaks, fragmentation и накопления stale resources.

Google SRE рекомендует проверять capacity и режим отказа при overload в реалистичной среде: заранее трудно угадать, какой ресурс исчерпается первым и как проявится каскадный отказ.

**Проверка исправления.** Результат содержит saturation point и безопасный operating envelope, а не одну цифру «Actors per node». Повторный прогон на production-like distribution укладывается в заранее заданные SLO и correctness checks.

## Ещё четыре скрытых антишаблона

### «RUNNING значит работает»

Lifecycle status сообщает, что controller видит объект в определённой фазе, но не подтверждает полезный progress. Нужны domain heartbeat, возраст последнего progress event, deadline и проверка результата. Эта граница продолжает troubleshooting-модель главы 29.

### Policy только в prompt

Фраза «никогда не удаляй production» помогает модели выбрать план, но не является control. Запрет должен жить в tool schema, authorization layer и downstream precondition. Prompt объясняет намерение; policy enforcement не зависит от послушания модели.

### Approval без конкретного diff

Кнопка «разрешить агенту продолжить» не даёт осмысленного контроля. Approval должен связывать immutable action digest, target, параметры, срок и approver. Изменился payload - требуется новое решение.

### Наблюдаемость после первого инцидента

Добавлять telemetry, когда evidence уже потеряно, поздно. До production нужны correlation IDs от root request до tool/action, attempt/retry reason, authority scope и outcome. OpenTelemetry semantic conventions полезны как общий словарь, но содержание и privacy policy команда определяет явно.

## Порядок исправления: сначала ограничить ущерб

Не пытайтесь переписать весь agent stack одним проектом. Исправляйте в порядке максимального снижения риска:

1. **Уберите глобальные credentials и необязательные write tools.** Это немедленно уменьшает blast radius.
2. **Закройте egress и debug paths.** Добавьте временные исключения только для измеренных зависимостей.
3. **Вынесите authoritative state и action journal из prompt.** Без этого безопасные retries невозможны.
4. **Классифицируйте errors и введите общий retry budget.** Отдельно обработайте ambiguous outcome.
5. **Поставьте deterministic validation перед effect.** Approval и policy связываются с конкретным request.
6. **Пересмотрите Task boundaries.** Разделите слишком мощные Tasks и объедините бессмысленные Task-per-thought.
7. **Проведите recovery и load drills.** Докажите restore, cancellation и поведение за saturation point.

## Definition of done перед практикумом

Следующая глава переводит книгу в лабораторные работы. До неё полезно зафиксировать минимальную планку, с которой эксперимент считается инженерным, а не демонстрационным:

- каждая Task имеет owner, purpose, timeout, budget и terminal outcome;
- каждый high-impact tool ограничен schema, identity, scope и approval policy;
- authoritative progress переживает удаление prompt и process restart;
- повтор action с тем же idempotency key не создаёт второй effect;
- default deny egress подтверждён положительными и отрицательными probes;
- debug выдаётся временно и оставляет audit trail;
- snapshot можно потерять без потери business truth;
- backup реально восстановлен хотя бы один раз;
- benchmark использует production-like distribution и измеряет tail/correctness;
- incident trace связывает root request, delegation, tool calls, retries, approval и post-state.

Антипаттерны не устраняются словами «мы будем осторожны». Они исчезают, когда неправильное действие становится технически невозможным, обнаруживаемым или ограниченным по ущербу. Именно это предстоит проверять руками в практикуме: не только поднять AX/Substrate, но и намеренно ломать boundaries, наблюдать failure modes и доказывать recovery.

### Источники и дальнейшее чтение

- [OWASP LLM06:2025 Excessive Agency](https://genai.owasp.org/llmrisk/llm062025-excessive-agency/)
- [OWASP AI Agent Security Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/AI_Agent_Security_Cheat_Sheet.html)
- [NIST: принцип least privilege](https://csrc.nist.gov/glossary/term/least_privilege)
- [NIST SP 800-207: Zero Trust Architecture](https://www.nist.gov/publications/zero-trust-architecture)
- [AWS Well-Architected: control and limit retry calls](https://docs.aws.amazon.com/wellarchitected/2024-06-27/framework/rel_mitigate_interaction_failure_limit_retries.html)
- [Kubernetes NetworkPolicy: egress isolation и default deny](https://kubernetes.io/docs/concepts/services-networking/network-policies/)
- [PostgreSQL: base backup, WAL и point-in-time recovery](https://www.postgresql.org/docs/17/continuous-archiving.html)
- [Google SRE: load testing и cascading failures](https://sre.google/sre-book/addressing-cascading-failures/)
- [OpenTelemetry Semantic Conventions](https://opentelemetry.io/docs/specs/semconv/)
- [AX concepts на закреплённом снимке ac233282](https://github.com/google/ax/blob/ac233282/docs/concepts.md)
- [Substrate architecture на закреплённом снимке df78882](https://github.com/agent-substrate/substrate/blob/df788825e6fc13dd7aa5fd283c8f14bd4c818247/docs/architecture.md)
