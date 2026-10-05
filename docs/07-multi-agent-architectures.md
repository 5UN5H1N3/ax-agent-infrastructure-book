# Multi-agent архитектуры без лишней магии

Главы 3-6 построили один bounded agent: loop, harness, state и capabilities. Теперь решим, когда нескольких таких loops действительно требуют dependency graph, isolation или независимая проверка, а когда multi-agent только увеличивает стоимость.

Команда создаёт четырёх агентов: architect, developer, tester и reviewer. На демонстрации это выглядит как автономная инженерная организация. В реальной задаче architect пишет общий план, developer меняет файлы до завершения плана, tester запускает тесты на старом commit, а reviewer читает summary вместо diff. Все четыре используют один repository, один token и одну модель. Работа стала дороже, но не быстрее и не надёжнее.

Проблема не в количестве агентов. В системе не было настоящих границ: независимых subtasks, разных capabilities, изолированного state и проверяемого protocol передачи результата. Названия ролей создали театральную организацию, но не архитектуру.

Главный принцип главы:

**Multi-agent система оправдана, когда разделение создаёт измеримую пользу: параллелизм, изоляцию context, специализацию capabilities или независимую проверку. Если такой пользы нет, один хорошо спроектированный agent почти всегда проще, дешевле и надёжнее.**

## Что именно считается отдельным агентом

Не каждый LLM call является агентом, и не каждый параллельный worker делает систему multi-agent.

| Сущность | Что сохраняется между шагами | Самостоятельно выбирает действия | Пример |
| --- | --- | --- | --- |
| Function/model call | ничего, кроме входа и ответа | нет | классификация issue |
| Workflow node | state задаёт workflow engine | обычно нет | generate -> validate -> publish |
| Worker | process и служебный state | не обязательно | executor test jobs |
| Agent | objective, context, tools, loop и termination | да, в заданных пределах | исследователь repository |
| Multi-agent system | несколько автономных loops плюс coordination layer | да | coordinator и независимые specialists |

Роль - это configuration, а не identity. Два prompts `researcher` и `reviewer`, вызванные последовательно одним controller, могут быть обычным workflow. Это хорошо: не нужно называть multi-agent то, что проще выразить pipeline.

Отдельный agent появляется там, где есть собственный decision loop:

```
objective -> observe -> decide -> act -> update state -> stop/escalate
```

Если несколько таких loops взаимодействуют, архитектура должна определить ownership, protocol и failure semantics. Иначе автономность умножает неопределённость.

## Начинайте с single-agent baseline

Перед добавлением второго агента соберите однопоточную версию и измерьте:

- качество результата;
- end-to-end latency;
- model tokens и tool calls;
- долю успешных Tasks;
- число human interventions;
- ошибки verification;
- стоимость одного verified outcome.

Без baseline нельзя понять, принесло ли разделение пользу. Multi-agent система может дать лучший ответ просто потому, что потратила в пятнадцать раз больше tokens. Это допустимо для дорогой research-задачи, но не доказывает эффективность архитектуры.

### Четыре причины разделять работу

**Независимый параллелизм.** Несколько направлений можно исследовать одновременно: регионы, источники, модули repository, hypothesis.

**Context isolation.** Каждому specialist нужен свой большой корпус данных, а coordinator получает только сжатые findings и evidence refs.

**Capability isolation.** Researcher читает, implementer пишет только branch, tester исполняет untrusted code в sandbox, approver не имеет execution tool.

**Независимая проверка.** Другой agent оценивает artifact по rubric или пытается опровергнуть hypothesis.

Пятая возможная причина - модельная специализация: дешёвая модель делает широкую фильтрацию, сильная решает сложные cases. Но смена модели сама по себе не требует multi-agent runtime; это может быть routing в обычном workflow.

### Когда разделение не помогает

Оставляйте один agent, если:

- каждый следующий шаг зависит от полного результата предыдущего;
- всем участникам нужен один и тот же большой context;
- несколько writers должны постоянно менять одни файлы;
- задачу быстрее решить одним дополнительным tool call;
- проверяющий не имеет независимого evidence или rubric;
- стоимость coordination сравнима с самой работой;
- outcome нельзя разложить на проверяемые части.

Особенно плохо параллелизуются короткие задачи с высокой связностью. Пять агентов, обсуждающих имя одной function, создают latency, а не производительность.

## Сначала нарисуйте граф зависимостей

Multi-agent architecture начинается не со списка персонажей, а с directed acyclic graph работы.

```
                 +-> dependency audit -+
issue -> scope --+-> code inspection ---+-> synthesis -> implementation
                 +-> test discovery ----+                  |
                                                            v
                                                     tests -> review
```

Ветки audit, inspection и test discovery независимы и могут идти параллельно. Implementation зависит от synthesis. Tests зависят от конкретного commit. Review зависит от diff и test results.

Если параллельная доля работы равна `P`, а число workers равно `N`, идеальное ускорение ограничено:

```
speedup <= 1 / ((1 - P) + P / N)
```

В agent system добавляется coordination overhead `C`:

```
wall_time ~= serial_path + max(parallel_branches) + C
```

Если decomposing, context packaging, queueing и merging занимают больше, чем сэкономлено на ветках, система стала медленнее. Закон не требует точного математического прогнозирования; он заставляет увидеть critical path.

### Независимость бывает трёх видов

| Вид | Вопрос | Пример |
| --- | --- | --- |
| Data independence | могут ли agents читать разные данные без обмена каждым шагом? | поиск по разным регионам |
| Write independence | могут ли они менять разные targets без конфликтов? | разные branches или modules |
| Decision independence | можно ли принять локальное решение без глобального context? | проверить license dependency |

Параллелизм чтения обычно проще параллелизма записи. Поэтому production multi-agent workflow часто делает широкий fan-out для исследования, а write оставляет одному owner.

## Основные топологии

В этой главе **coordinator** - прикладная роль: она декомпозирует цель, делегирует работу и собирает результат. Это не infrastructure orchestrator из глав 1 и 4, который размещает workloads и обеспечивает их lifecycle.

Topology отвечает на вопросы: кто выбирает следующего участника, кто владеет разговором, где сходятся результаты и кто имеет право завершить Task.

### Manager и agents-as-tools

Manager сохраняет objective и user-facing ownership. Specialists вызываются как tools для bounded subtasks и возвращают результат manager.

```
user -> manager -> specialist A
                -> specialist B
                -> specialist C
             <- structured results
user <- final synthesis
```

Подходит, когда нужен единый final answer, общая policy и централизованная агрегация. Specialist получает сформированный input, а не обязательно всю conversation history.

Риск - manager становится bottleneck: плохо decomposes, теряет minority finding или пересказывает evidence с ошибкой. Manager не должен вручную копировать большие результаты; лучше принимать schemas и artifact refs.

### Handoff

Текущий agent передаёт ownership specialist, который продолжает interaction.

```
user -> triage -> billing specialist -> user
```

Handoff полезен для routing между distinct domains: support, legal intake, incident type. Он уменьшает manager narration и позволяет specialist работать со своими instructions.

Нужно точно определить:

- какой history получает новый agent;
- какие pending approvals переходят вместе с Task;
- кто отвечает за возврат или следующий handoff;
- сохраняются ли guardrails;
- как предотвращается цикл A -> B -> A.

Handoff не равен вызову function. Меняется owner текущего диалога, поэтому trace и UX должны это отражать.

### Fan-out/fan-in

Coordinator создаёт несколько независимых subtasks, затем агрегирует результаты.

```
             +-> region A -+
objective ---+-> region B --+-> merge -> verify
             +-> region C -+
```

Это сильная topology для research, vulnerability discovery и независимых checks. Она требует overlap policy: ветки должны либо сознательно искать разные области, либо сознательно дублировать поиск для оценки recall.

Fan-in - не простая конкатенация. Aggregator валидирует schema, удаляет duplicates, сохраняет provenance, разрешает contradictions и отмечает gaps.

### Pipeline

Каждая стадия преобразует artifact и передаёт его следующей:

```
research -> plan -> implementation -> test -> review
```

Pipeline проще наблюдать, но часто это workflow, а не несколько автономных agents. Он оправдан, когда стадии используют разные tools или authority. Если все стадии детерминированы, реализуйте их code, а не LLM loops.

### Generator/evaluator

Generator создаёт artifact, evaluator проверяет по rubric и возвращает defects. Controller решает, повторять ли цикл.

```
generator -> candidate -> evaluator -> pass
     ^                       |
     +------- defects -------+
```

Evaluator не должен бесконечно говорить «можно улучшить». Нужны pass criteria, maximum iterations и terminal outcome `needs_human`.

Независимость evaluator не возникает от другого имени. Та же модель, тот же context и те же assumptions дают correlated errors. Усилить проверку можно отдельным evidence, adversarial prompt, deterministic tests, другой model family или human review.

### Blackboard и peer swarm

Agents публикуют findings в общее пространство и сами выбирают следующую работу. Такая схема полезна для open-ended exploration, где заранее неизвестна decomposition.

Но coordination становится сложнее: duplicate claims, stale records, race conditions, popularity bias и отсутствие owner. Blackboard должен иметь typed records, versioning, leases и moderator/arbiter. Общий чат без protocol - не blackboard architecture.

Для большинства production systems начинайте с manager или fan-out/fan-in. Peer swarm нужен только после доказательства, что централизованная decomposition действительно ограничивает outcome.

## Delegation contract: что получает child

Фраза «изучи security» создаёт дублирование и gaps. Child должен получить ограниченный task envelope.

```
task_id: sec-audit-42/dependency-review
parent_id: sec-audit-42
objective: Identify exploitable dependency risks in the current lockfile
non_goals:
  - do not modify files
  - do not review application authorization logic
inputs:
  repository_ref: 31f27ab
  files:
    - package.json
    - package-lock.json
required_output:
  schema: vulnerability-findings@2
  artifact: artifact://sec-audit-42/dependencies.json
authority:
  tools: [repository.read, advisory.lookup]
  network: advisories-only
budget:
  model_steps: 12
  tool_calls: 30
  wall_time_seconds: 300
termination:
  success: all direct and transitive packages assessed
  escalate: lockfile missing or advisory source unavailable
```

Contract отвечает минимум на девять вопросов:

1. Какую часть общей цели решает child?
2. Что явно не входит в scope?
3. Какая version входных artifacts?
4. Какие tools и authority разрешены?
5. Какой budget?
6. В каком schema вернуть результат?
7. Как доказать claims?
8. Когда остановиться?
9. Когда вернуть вопрос parent, а не импровизировать?

### Делегирование уменьшает authority

Child не должен получать больше полномочий, чем parent:

```
child_authority <= parent_authority AND task_scope
```

Coordinator с read/write repository scope может выдать researcher только read. Implementer получает write в отдельный branch, но не merge. Tester запускает code в sandbox без production secrets. Reviewer читает diff и test artifacts.

Если child способен повторно делегировать, attenuation применяется рекурсивно. Глубокая иерархия не должна случайно восстановить привилегии через другого worker.

### Делегирование передаёт objective, а не чужой transcript

Полная history parent часто содержит нерелевантные instructions, untrusted text и большой token cost. Child обычно нужны:

- objective и constraints;
- selected evidence;
- versioned artifact refs;
- известные uncertainties;
- expected output schema.

Это projection state, а не копия разговора. Handoff может требовать conversation history для UX, но даже тогда input filter должен удалять secrets и не относящиеся к domain данные.

## Coordination state: сообщения не заменяют базу данных

Agents могут обмениваться natural language, но application state должен оставаться structured.

```
parent_task: migration-81
state_version: 17
children:
  - id: inventory
    status: succeeded
    output_ref: artifact://migration-81/inventory.json
  - id: compatibility
    status: running
    lease_expires_at: 2026-10-05T09:10:00Z
  - id: rollback
    status: blocked
    reason_code: MISSING_BACKUP_POLICY
join:
  policy: all_required
  required: [inventory, compatibility, rollback]
```

Natural-language summary полезна модели, но controller не должен извлекать из неё status, budget или operation ID.

### Разделяйте control messages и evidence

| Тип | Назначение | Пример |
| --- | --- | --- |
| Command | назначить bounded work | `analyze module payments at commit X` |
| Progress | сообщить состояние без изменения conclusion | `12/18 files checked` |
| Finding | передать claim и evidence | vulnerability + file/line + reproduction |
| Question | запросить missing decision | target environment unknown |
| Cancellation | прекратить новые действия | parent no longer needs branch |
| Completion | зафиксировать terminal result | succeeded/failed/partial |

Finding не должен скрываться в progress text. Completion не доказывает correctness. Каждый тип имеет schema, correlation ID и timestamp.

### Shared workspace требует ownership

Несколько agents в одном filesystem без правил создают lost updates. Выберите один из patterns:

- один writer, остальные read-only;
- отдельный branch/worktree на child;
- file/module ownership;
- append-only artifacts;
- transactional store с optimistic concurrency;
- proposal вместо прямой записи.

Lock на весь repository безопасен, но уничтожает параллелизм. Лучше уменьшать write overlap архитектурой.

## Budgets, backpressure и защита от task explosion

Если каждый agent создаёт пять children на глубине четыре, получится до 625 leaf tasks. Даже если каждый дешёвый, система быстро исчерпает concurrency, rate limit и context budget.

Root задаёт envelope для всего дерева:

```
subtree_budget:
  model_tokens: 2_000_000
  tool_calls: 800
  wall_time_seconds: 1800
  max_concurrency: 8
  max_depth: 2
  max_children_total: 24
```

Parent резервирует budget до spawn. Неиспользованный остаток возвращается после terminal state. Формально:

```
sum(reserved_child_budget) <= parent_remaining_budget
```

Дополнительно нужны:

- queue capacity;
- per-tool rate limits;
- admission control;
- priority и fairness между Tasks;
- backoff вместо aggressive polling;
- circuit breaker при повторяющейся системной ошибке.

Agent не должен создавать нового child только потому, что не знает следующий шаг. Spawn является дорогим действием и требует причины: independent scope, expected gain и available budget.

### Dynamic effort scaling

Coordinator оценивает complexity до fan-out:

| Класс | Типичная стратегия |
| --- | --- |
| Simple fact | один agent, без delegation |
| Bounded comparison | 2-3 независимые ветки |
| Broad research | несколько domain/region specialists |
| Coupled implementation | один writer плюс параллельные read-only checks |
| High-risk change | planner, policy/approval, executor, independent verifier |

Числа не универсальны. Их калибруют evals и production telemetry, а не интуиция модели.

## Failure semantics дерева Tasks

Multi-agent система добавляет распределённые failure modes поверх ошибок каждого agent.

### Дублирование и gaps

Два children выполняют один и тот же поиск, а третий scope остаётся пустым. Причина обычно в расплывчатом contract или отсутствии global coverage map.

Исправление: явные non-goals, partition key и проверка overlap до spawn.

### Role drift и reasoning-action mismatch

Reviewer начинает исправлять code, researcher объявляет release, implementer делает вывод без tests. Название роли не ограничивает capability.

Исправление: tool catalog и authority соответствуют роли; output schema не допускает скрытый side effect.

### Потеря важного finding

Child нашёл исключение, но aggregator выбрал majority summary. Consensus не является truth. Несколько agents одной model family могут повторять одинаковую ошибку, а один dissenter держать решающее evidence.

Исправление: merge claims по evidence, сохранять contradictions и отдельно поднимать high-severity minority findings.

### Premature termination

Coordinator завершает Task после первых успешных branches или evaluator выдаёт pass без проверки required criteria.

Исправление: join policy и completion predicate проверяются code. Только определённый controller переводит root в terminal state.

### Livelock и conversation loops

Agents бесконечно передают Task друг другу, критикуют и переписывают один artifact без measurable progress.

Исправление: max handoffs, max iterations, progress metric и outcome `needs_human`.

### Stale result

Child работал с commit X, а parent уже перешёл к X+3. Формально успешный result больше не применим.

Исправление: каждый output связывается с input versions. Fan-in отклоняет stale artifacts или запускает targeted revalidation.

### Неизвестный side effect

Child применил изменение, но умер до completion message. Parent считает его failed и повторяет операцию.

Исправление: operation ledger, idempotency key и reconciliation с external source of truth. Cancellation не откатывает уже выполненный write.

### Коррелированная ошибка

Десять одинаковых agents с одинаковым prompt и sources могут быть уверены в одной неверной hypothesis. Голосование создаёт иллюзию независимости.

Исправление: разнообразить evidence и method, а не только random seed. Один agent ищет подтверждение, другой - counterexample, третий выполняет deterministic test.

Исследование MAST группирует наблюдаемые multi-agent failures в три большие категории: specification/system design, inter-agent misalignment и verification/termination. Практический вывод не в конкретных процентах, а в том, что большинство проблем находятся в orchestration layer и не исчезают от более длинного role prompt.

## Fan-in: как собирать результат без потери смысла

Aggregator получает не «мнения агентов», а набор claims.

```
{
  "claim_id": "finding-17",
  "statement": "dependency X is reachable from the public endpoint",
  "confidence": "supported",
  "evidence": [
    "artifact://audit/callgraph.json#path-81",
    "repo://payments@31f27ab/src/api.ts:44"
  ],
  "input_versions": {
    "repository": "31f27ab",
    "advisory_db": "2026-10-05T08:00Z"
  },
  "limitations": ["runtime feature flag not observed"]
}
```

Правильный merge:

1. валидирует output schema;
2. проверяет input versions;
3. группирует claims, но не удаляет provenance;
4. различает duplicate evidence и независимое подтверждение;
5. сохраняет conflicts;
6. проверяет coverage required scopes;
7. вызывает verifier для high-impact claims;
8. создаёт compact synthesis для следующего decision point.

Если два children ссылаются на одну web page, это не два независимых подтверждения. Если conclusions расходятся, aggregator не обязан выбирать среднее: он может вернуть `unresolved` и назначить discriminating test.

## Cancellation, retry и recovery

Parent restart не должен повторно создавать всех children. Используйте deterministic child ID:

```
child_id = hash(parent_id, decomposition_version, partition_key)
```

После recovery coordinator читает journal и для каждого child выбирает:

- attach к running Task;
- принять terminal output;
- reclaim после expired lease;
- retry с тем же logical ID;
- cancel, если result больше не нужен;
- reconcile, если outcome write неизвестен.

Cancellation является request, а не мгновенным фактом. Child может находиться внутри external call. Controller ждёт acknowledged stop или помечает Task `cancel_requested` и отслеживает поздний result.

При fan-out fail-fast подходит не всегда. Если одна region query упала, остальные findings всё ещё полезны. Join policy должна различать:

```
join:
  mode: quorum
  minimum_success: 3
  required_partitions: [production]
  allow_partial: true
  partial_label: incomplete_coverage
```

## Observability: видеть дерево, а не набор чатов

Trace должен связывать root, children, tool calls и artifacts.

Минимальные identifiers:

- `trace_id` общей цели;
- `task_id` и `parent_task_id`;
- `agent_config_version`;
- `delegation_contract_version`;
- `input_state_version`;
- `tool_catalog_version`;
- `operation_id` для side effects;
- `artifact_ref` результата.

Полезные метрики:

| Метрика | Что показывает |
| --- | --- |
| Spawn count/depth | task explosion и качество decomposition |
| Useful parallelism | сколько branches реально перекрылись по времени |
| Duplicate work rate | стоимость плохого partitioning |
| Handoff count | routing loops и UX complexity |
| Join wait time | bottleneck fan-in |
| Stale output rate | слишком долгие subtasks или слабое versioning |
| Verified outcome rate | конечное качество, а не число завершённых agents |
| Cost per verified outcome | экономическую полезность topology |
| Cancellation lag | способность остановить дерево |

Красивый transcript не заменяет distributed trace. Оператор должен быстро ответить: какой child держит critical path, кто расходует budget и какие writes уже произошли.

## Как оценивать multi-agent систему

Сравнивайте минимум три variants:

1. сильный single agent;
2. deterministic workflow с теми же tools;
3. multi-agent topology.

Уравняйте или отдельно покажите token/tool budgets. Иначе «архитектура» побеждает просто дополнительным compute.

### Outcome metrics

- factual/correctness score;
- completeness и coverage;
- verified environment state;
- defects escaped после review;
- human acceptance;
- latency p50/p95;
- model и tool cost;
- failure recovery rate.

### Coordination metrics

- правильность routing и decomposition;
- overlap/gap rate;
- schema-valid child outputs;
- ignored findings;
- incorrect or premature termination;
- количество cycles;
- authority violations;
- reproducibility при одинаковом input version.

### Ablation tests

Удаляйте по одному элементу:

- без reviewer;
- без parallelism;
- один model вместо разных;
- text summary вместо structured result;
- общий workspace вместо branches;
- фиксированная decomposition вместо dynamic.

Так становится видно, какой component даёт benefit. Если removal evaluator не меняет качество, evaluator был ритуалом.

End-state evaluation важнее проверки единственного «правильного» пути. Agents могут прийти к одному verified outcome разными trajectories. Но process constraints - authority, budget, запрещённые actions - всё равно оцениваются отдельно.

## Практика: software-engineering workflow

![Практика: software-engineering multi-agent workflow](.gitbook/assets/diagrams/07-08.png)  
*Практика: software-engineering multi-agent workflow*

Рассмотрим issue: «после обновления cache library периодически теряются session records».

### 1. Coordinator фиксирует scope

Он создаёт immutable task manifest:

```
goal: identify cause and prepare reviewed fix
repository_ref: 31f27ab
acceptance:
  - regression test reproduces loss before fix
  - test passes after fix
  - no production deployment
budgets:
  children: 4
  wall_time_minutes: 30
```

### 2. Read-only fan-out

Три children работают параллельно:

- Researcher прослеживает session lifecycle и возвращает call graph.
- Dependency analyst читает changelog и semantics новой cache library.
- Test analyst ищет existing race tests и строит reproduction plan.

У каждого distinct scope и один repository commit. Они пишут только artifacts.

### 3. Synthesis gate

Coordinator объединяет evidence. Если analysts расходятся, он не выбирает majority, а формирует discriminating experiment: искусственно задержать cache callback и проверить порядок delete/write.

### 4. Один implementer

Implementer получает:

- confirmed hypothesis;
- reproduction artifact;
- allowed files;
- отдельный branch;
- запрет merge и release.

Он создаёт test и fix в одном bounded change set.

### 5. Tester проверяет конкретный commit

Tester запускает commands в sandbox, записывает environment digest и возвращает machine-readable report. Он не принимает summary «tests passed» от implementer.

### 6. Reviewer оценивает artifact

Reviewer получает goal, diff, test results и rubric. Он не имеет write tool. Verdict содержит blocking defects, evidence и coverage gaps.

### 7. Human/policy gate

Merge остаётся отдельной capability. Approval связан с exact commit SHA, а не с фразой «исправь проблему».

Эта система multi-agent не потому, что роли похожи на людей. Её ценность в parallel read, одном writer, независимом execution environment и отдельном verification evidence.

## AX и дерево Tasks

AX Task может служить unit isolation для child agent: отдельные resources, Workspace, lifecycle и credentials. Но платформа не обязана угадывать business semantics дерева.

Application layer всё равно определяет:

- parent/child relation;
- deterministic task IDs;
- budget propagation;
- authority attenuation;
- join policy;
- cancellation и recovery;
- output schemas.

Не создавайте AX Task на каждый model call. Новая Task оправдана, когда нужны isolation, отдельный lifecycle, параллельная capacity или другой security scope. Подробная реализация на AX рассматривается в главе 22, а две полные reference architectures - в главе 33.

## Где заканчивается orchestration protocol

Внутри одного application agents можно вызывать как tools или handoffs. Между независимыми systems нужен protocol discovery, messaging и long-running task exchange. A2A, например, стандартизует взаимодействие потенциально opaque agents: capability discovery, structured messages, streaming и asynchronous task updates.

Но interoperability protocol не решает:

- правильно ли decomposed objective;
- можно ли доверять remote agent;
- какой budget ему выдать;
- достаточно ли evidence;
- кто авторизует side effect;
- когда root Task считается успешной.

Протокол переносит delegation contract и artifacts. Coordinator и прикладной orchestration layer остаются ответственными за смысл; infrastructure orchestrator лишь исполняет lifecycle выделенных workloads.

## Типичные антипаттерны

**Role-play organization.** Названия CEO, architect и engineer есть, а capabilities и boundaries одинаковы.

**Spawn before decompose.** Coordinator создаёт children до построения coverage map.

**Everyone writes.** Несколько agents меняют общий workspace без ownership.

**Chat as state.** Status и operation IDs извлекаются из сообщений.

**Transcript forwarding.** Каждый child получает всю history, включая secrets и irrelevant instructions.

**Majority means truth.** Correlated answers принимаются за независимое подтверждение.

**Reviewer can silently fix.** Verification и mutation смешаны в одной роли.

**Unlimited recursion.** Children создают children без subtree budget и depth limit.

**Cancel means rollback.** Parent считает остановленный child доказательством отсутствия side effect.

**Success means done.** Completion message принимается без output validation и external verification.

**More agents in every eval.** Multi-agent variant получает больше tokens, но cost не учитывается.

**A2A/MCP as architecture.** Наличие protocol заменяет design decomposition, authority и join semantics.

## Checklist выбора и проектирования

Перед внедрением ответьте:

1. Какую метрику single-agent baseline нужно улучшить?
2. Какая часть работы действительно параллельна?
3. Где critical path и coordination overhead?
4. Почему нужен agent, а не function или workflow node?
5. Какая topology соответствует ownership?
6. Как выглядит delegation contract?
7. Какие input versions связывают child result?
8. Кто единственный owner каждого write target?
9. Как attenuate authority?
10. Как ограничены subtree cost, concurrency и depth?
11. Как fan-in сохраняет contradictions и provenance?
12. Кто имеет право завершить root Task?
13. Что происходит при parent restart и child timeout?
14. Как измеряется verified outcome и стоимость?
15. Какой ablation докажет, что дополнительный agent действительно нужен?

Если на вопросы 2, 6, 8 и 12 нет ответа, система ещё не готова к нескольким автономным loops.

## Итоги главы

- Multi-agent architecture - это несколько decision loops плюс coordination layer, а не несколько role prompts.
- Начинайте с single-agent baseline и измеримой проблемы, которую должно решить разделение.
- Основные источники пользы: независимый параллелизм, context isolation, capability isolation и verification.
- Dependency graph и critical path важнее списка агентов.
- Manager, handoff, fan-out/fan-in, pipeline, evaluator и swarm имеют разные ownership semantics.
- Delegation требует structured contract: objective, non-goals, versions, authority, budget, schema и termination.
- Child authority должна быть уже parent authority и Task scope.
- Coordination state хранится структурированно; сообщения передают commands, findings и progress, но не заменяют state store.
- Shared writes требуют одного owner, отдельных branches или transactional concurrency control.
- Subtree budgets, admission control и depth limits предотвращают task explosion.
- Multi-agent failures включают gaps, duplication, role drift, lost findings, stale outputs, cycles и correlated errors.
- Fan-in агрегирует claims и evidence, а не голосует за самый популярный текст.
- Recovery опирается на deterministic child IDs, journal, leases и reconciliation side effects.
- Evals сравнивают outcome, latency и cost с single-agent и workflow baselines.
- На AX отдельная Task нужна для isolation, lifecycle, capacity или security boundary, а не для каждого шага reasoning.
- Protocol interoperability не заменяет orchestration design, trust и authorization.

**Дальше.** Логическое разделение ролей требует физических границ выполнения. В следующей главе сравним process, container, gVisor и microVM и свяжем степень изоляции с threat model.

### Источники и дальнейшее чтение

- [Anthropic: How we built our multi-agent research system](https://www.anthropic.com/engineering/multi-agent-research-system) - orchestrator-worker, delegation, parallelism, cost и evals.
- [Anthropic: Patterns and problems in emerging multiagent systems](https://www.anthropic.com/research/multiagent-systems) - coordination, systemic resource failures, correlated behavior и epistemic risks.
- [OpenAI Agents SDK: Agent orchestration](https://openai.github.io/openai-agents-python/multi_agent/) - manager/agents-as-tools, handoffs и code-driven orchestration.
- [OpenAI Agents SDK: Handoffs](https://openai.github.io/openai-agents-python/handoffs/) - ownership transfer, input filters и handoff configuration.
- [A2A Protocol Specification 0.3.0](https://a2a-protocol.org/v0.3.0/specification/) - interoperability между независимыми agent systems.
- [Cemri et al.: Why Do Multi-Agent LLM Systems Fail?](https://arxiv.org/abs/2503.13657) - MAST taxonomy и empirical analysis multi-agent failures.
- [Anthropic: Building effective agents](https://www.anthropic.com/engineering/building-effective-agents) - routing, parallelization, orchestrator-workers и evaluator-optimizer workflows.
- Главы 22, 33 и 37 этой книги - AX Task delegation, reference architectures и safe execution.
