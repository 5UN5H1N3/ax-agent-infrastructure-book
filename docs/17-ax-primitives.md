# AX primitives: Task, Workspace и Model без магии

**Статус:** `CURRENT MAIN VERIFIED` · `THREE API KINDS` · `CONTRACTS VS WIRING`

В главе 16 мы проследили путь запроса через `ax-server`, Redis и Substrate. Теперь пора разобрать язык, на котором пользователь объясняет AX, что именно нужно запустить. Этот язык намеренно мал: в проверенном current main есть три API kind - `Task`, `Workspace` и `Model`.

Маленький API полезен только тогда, когда границы объектов понятны. Если положить image, Git repositories, credentials, model parameters, network policy и весь lifecycle в один универсальный manifest, его легко создать, но почти невозможно безопасно переиспользовать. AX разделяет описание выполняемой единицы, подготовку рабочей среды и model configuration. При этом текущая реализация ещё не соединяет все три объекта так полно, как подсказывают их названия.

Эта глава поэтому отвечает на два вопроса одновременно: какой mental model заложен в primitives и что действительно делает source code на зафиксированном commit. Для pre-stable системы такое разделение важнее красивой диаграммы.

## Что читатель должен унести из главы

- почему Task не равен Pod, Job или «агенту целиком»;
- почему Workspace является blueprint среды, а не общей сетевой файловой системой;
- что Model настраивает для платформы и почему Task пока не может ссылаться на него;
- почему Gateway больше не является AX kind в current main;
- какие поля уже влияют на runtime, а какие пока существуют только в schema;
- почему порядок документов в multi-document YAML имеет значение;
- что на самом деле доказывает `WorkspaceReady`;
- как выбирать границы ресурсов и versioning strategy для production.

## Primitive - это граница ответственности и lifecycle

Primitive полезно выделять не потому, что ему нашёлся знакомый аналог в Kubernetes. Его выделяют, когда часть системы должна иметь собственные владельца, частоту изменений, правила переиспользования и failure semantics.

| Primitive | Главный вопрос | Типичный lifecycle |
| --- | --- | --- |
| `Task` | Что изолированно запустить и как управлять этим экземпляром? | Создать один раз, resume/suspend, затем удалить |
| `Workspace` | Какой materialized environment должен получить Task? | Переиспользовать и обновлять как blueprint |
| `Model` | Как platform component должен вызвать model provider? | Централизованно менять provider configuration и secret reference |

Эти lifecycle различаются намеренно. Task immutable после создания: её runtime identity уже связана с Actor и durable state. Workspace и Model обновляемы: это reusable configuration. Но обновляемость объекта не означает автоматическое распространение изменения во все уже созданные Tasks. Между API semantics и rollout semantics всегда нужен отдельный ответ.

## Current API surface и documentation drift

Источником истины для поддерживаемых kinds является protobuf и CLI switch, а не отдельная фраза в документации. На проверенном commit `ax.proto` и `ax apply` принимают ровно три kind.

| Kind | В protobuf/CLI | Комментарий |
| --- | --- | --- |
| `Task` | Да | Create/get/list/delete/suspend/resume/watch |
| `Workspace` | Да | Update/get/list/delete |
| `Model` | Да | Update/get/list/delete |
| `Gateway` | Нет | Нет kind, message или CLI branch в current main |

`docs/manifests.md` всё ещё начинается словами «all four kinds», хотя дальше показывает только Task, Workspace и Model. Это обычный пример documentation drift. Gateway нельзя объявить «на всякий случай»: CLI вернёт unsupported kind. Сетевые ingress/egress contracts принадлежат Agent Substrate и были разобраны в главе 15.

> **Правило для pre-stable API.** README помогает понять intent, docs дают examples, но окончательный contract подтверждают schema, validator и execution path выбранного commit.

## Общие правила: metadata, atespace и apply

Каждый AX object имеет `apiVersion: ax.io/v1alpha1`, kind, `metadata.name` и `metadata.atespace`. Пустой atespace server заменяет на `default`. Name и atespace становятся именами ресурсов Substrate, поэтому validator требует lowercase RFC 1123 label длиной не более 63 символов.

Atespace - не декоративная папка. Это scope имени, secret lookup и runtime resources. Одинаковые имена в разных atespaces различаются, а Kubernetes Secret для Model client ищется в namespace, равном atespace. Поэтому production manifest лучше задаёт atespace явно, даже если лаборатория использует `default`.

`ax apply` читает multi-document YAML последовательно и отправляет каждый document отдельным typed RPC. Server не получает исходный YAML целиком и не строит dependency graph. Общей транзакции тоже нет: первые документы могут примениться, а следующий завершиться ошибкой.

| Resource | Apply operation | Повторное применение |
| --- | --- | --- |
| Task | `CreateTask` | Ошибка, если имя уже существует |
| Workspace | `UpdateWorkspace` | `created`, `configured` или `unchanged` |
| Model | `UpdateModel` | `created`, `configured` или `unchanged` |

### Порядок документов является contract

Task reconciliation читает связанные Workspaces из Redis во время создания. Если Task стоит раньше Workspace, отсутствующая ссылка сейчас не вызывает validation error: server просто не передаст Workspace в runner. Runner сопоставит binding с пустым объектом и создаст directory без ожидаемого содержимого.

Поэтому безопасный порядок таков: сначала Workspace, затем Model, если он нужен platform component, и только потом Task. Ещё надёжнее применять reusable resources отдельным deployment step и создавать Tasks после проверки их наличия.

```
safe apply sequence:
  1. apply Workspace and Model definitions
  2. verify: ax get workspace / ax get model
  3. create Task that binds the Workspace
  4. resume Task and wait for Ready=True
```

## Task: единица isolation и runtime identity

Task отвечает не на вопрос «какую бизнес-задачу решает агент», а на более конкретный: какой sandboxed execution instance должен существовать, с каким filesystem state и lifecycle. Один Task может содержать короткую команду, долгоживущий agent harness или корневой узел, который порождает другие Tasks.

Границу Task стоит проводить там, где нужны отдельные isolation, credentials, suspend/resume, failure recovery или audit identity. Два процесса, которые должны падать, восстанавливаться и получать permissions независимо, не следует помещать в одну Task только ради удобного manifest.

### Поля Task и их фактический эффект

| Поле | Назначение | Current main |
| --- | --- | --- |
| `image` | Container image для ActorTemplate | Работает; при пустом значении используется default runner image |
| `command` | Agent process и arguments | Runner запускает как child; это не container entrypoint |
| `env` | Переменные окружения workload | Передаются в ActorTemplate и доступны через API в открытом виде |
| `resources` | CPU/memory requests и limits | Есть в schema, но inspected reconciler не маппит их в Substrate ActorTemplate |
| `workspaces` | Именованные bindings, paths и goals | Работают, но existence ссылки не валидируется |
| `debug` | Включить guest services для `ax ssh` | Работает; открывает process/file access внутри sandbox |

### Image и command - разные уровни

AX всегда запускает в container фиксированный путь `/usr/local/bin/ax-task-runner`. Пользовательский `spec.command` приходит runner через `AX_TASK_YAML` и становится дочерним процессом. Поэтому custom image обязан содержать совместимый runner по ожидаемому пути. Просто указать image с привычным ENTRYPOINT недостаточно.

Если command пуст, runner продолжает обслуживать metadata до остановки. Если child завершился, runner также остаётся PID 1. Следовательно, phase `Running` и даже container health не доказывают, что agent process продолжает работу. Подробный runner contract будет в главе 18.

### Env не является secret store

`EnvVar` содержит только `name` и literal `value`. Значение сохраняется в Task record и возвращается через `ax get task`. В schema нет `valueFrom.secretKeyRef`, а у Task нет `modelRef`. Open issue #348 именно поэтому предупреждает: credentials, помещённые в Task env, становятся API-readable.

Current reconciler отдельно умеет находить hardcoded `gemini-api-secret/GEMINI_API_KEY` и добавлять его в ActorTemplate. Это не универсальная связь Task -> Model и не решает безопасную выдачу произвольных provider credentials. Для production предпочтительны egress credential injection или другой broker, при котором secret не входит в sandbox.

### Resources: declaration ещё не enforcement

Manifests показывают `requests.cpu`, `requests.memory`, limits и похожи на Kubernetes. Но `TaskReconciler` строит ActorTemplate без чтения `task.spec.resources`. Пока mapping не реализован, эти значения нельзя использовать как доказательство quota или isolation.

При выборе AX инженер должен проверить не только наличие поля, но и полный путь: parse -> validation -> translation -> runtime enforcement -> observation. Если цепь заканчивается на schema, поле является intent или placeholder, а не control.

### Task immutable, но state продолжает жить

Повторный CreateTask с тем же именем запрещён. Это защищает связь Task name, Actor и durable state от неявной подмены image или command. Изменение executable contract должно создавать новую Task identity либо проходить явную migration procedure.

Suspend/resume меняют lifecycle, а не spec. Delete сначала удаляет Actor и task-specific ActorTemplates, затем Redis record. Если нужна история завершённых задач, её следует экспортировать до delete: Task store не является архивом.

## Workspace: blueprint materialized environment

Workspace часто ошибочно воспринимают как общий PVC. На самом деле это reusable blueprint: список Git sources, inline files, MCP/skills declarations. Когда Task связывает Workspace, runner материализует его в durable filesystem именно этой Task. Две Tasks ссылаются на один Workspace definition, но получают независимые рабочие копии и независимый runtime state.

Такое разделение полезно для агентных нагрузок. Агент должен свободно менять checkout, создавать artifacts и сохранять локальные caches, не повреждая среду соседней Task. Shared collaboration требует отдельного storage или coordination service; Workspace reference сама по себе его не создаёт.

### Что хранит Workspace и что делает setup

| Часть spec | Intent | Фактическая maiden setup |
| --- | --- | --- |
| `git` | Repositories, branch, dir, depth | Init/fetch/checkout, до 5 попыток, default branch `main` |
| `files` | Создать небольшие configuration files | Записываются с mode 0644; absolute path также разрешён |
| `mcp.registries/servers` | Объявить tool landscape | Хранится в resource и metadata; inspected `SetupWorkspace` не materializes MCP config |
| `skills.registries` | Источники skill packages | Inspected setup не загружает registries |
| `skills.path` | Directory для skills | Directory создаётся, но packages не устанавливаются |

Это не повод объявлять Workspace бесполезным. Git и files уже дают воспроизводимый старт, а MCP/skills schema фиксирует желаемый contract для runner или внешнего planner. Но platform owner должен различать declarative data и materialization implementation.

### Binding принадлежит Task

Path и goal находятся не в WorkspaceSpec, а в `Task.spec.workspaces[]`. Один blueprint можно смонтировать по разным paths и подготовить под разные цели. Binding без path получает `/workspace/<name>`; первый binding становится working directory command. Paths должны быть absolute и уникальными.

Если bindings нет, runner всё равно создаёт пустой `/workspace`. Если binding ссылается на отсутствующий Workspace, current code также создаёт пустую directory. Поэтому `WorkspaceReady=True` нельзя трактовать как доказательство, что named Workspace был найден.

### Goal - просьба bootstrap agent, а не postcondition

Goal передаётся default runner в Antigravity bootstrap script. Script запускается только если он присутствует в image и доступен `GEMINI_API_KEY`; default timeout равен 10 минутам. Goal может установить toolchain или dependencies, но он не является формально проверяемым desired state.

В current setup skipped, timed-out или failed bootstrap логируется, но не делает `SetupWorkspace` error. После этого marker всё равно может быть записан, а runner выставит readiness. Аналогично Git failure не возвращается runner как error: marker не пишется для retry, однако текущий boot может получить `WorkspaceReady=True`.

> **Readiness lesson.** В текущей реализации `WorkspaceReady` ближе к «runner завершил setup procedure без fatal Go error», чем к «все repositories, tools и goal postconditions проверены». Нужны собственные acceptance probes перед запуском дорогой agent loop.

### Idempotency marker и versioning

Runner создаёт marker `/ax/initialized-<path>` отдельно для каждого binding. Если marker найден, maiden setup пропускается. Это защищает изменённый агентом checkout от повторного clone после restart/resume.

Но marker не содержит digest Workspace spec. Update Workspace не запускает fan-out reconciliation существующих Tasks, а already initialized runner не обязан повторять setup. Поэтому изменение branch, files или skill declarations следует считать новой версией environment.

Практичные стратегии: включать version в Workspace name, создавать новую Task на новую версию, либо писать custom migration step с собственным digest marker. Mutable Workspace удобен для future Tasks, но не является fleet-wide rollout mechanism.

### Security и ownership

Inline file может иметь absolute path, а goal запускает agent-assisted bootstrap с доступными sandbox privileges. Workspace поэтому должен считаться доверенным platform configuration, а не произвольным input конечного пользователя. Code review, provenance и ограничения egress для maiden setup важны не меньше, чем policy runtime agent.

Credentials для private Git, MCP registries и package managers требуют отдельного design. Простое добавление secret в files или env делает его частью Task record или filesystem snapshot. Лучше выдавать short-lived credentials только setup phase и отделять setup Actor от runtime Actor; именно такое разделение присутствует в AX roadmap, но ещё не реализовано.

## Model: configuration для platform model client

Model хранит provider name, model identifier, provider-specific parameters и Kubernetes Secret reference. Его смысл - централизовать model configuration для компонентов AX, например Workspace planner. Это не inference service и не автоматическая настройка каждого agent process.

| Поле | Что означает | Current main caveat |
| --- | --- | --- |
| `provider` | Provider selector | Schema принимает string; remote Generate path реализован только для Google |
| `model` | Provider model identifier | Используется internal model client |
| `parameters` | Свободная JSON map generation settings | Для Google передаётся как generationConfig; `systemInstruction` обрабатывается отдельно |
| `secretKey` | Kubernetes Secret name/key | Secret ищется в namespace, равном atespace |

### Model не привязан к Task

В TaskSpec нет model reference. Создание `Model/default-model` не меняет environment пользовательской команды и не заставляет agent использовать указанный model. Internal `model.Client` умеет загрузить Model из store, но такой client должен быть явно создан platform component.

Workspace planner содержит constructor, который читает `default-model` из store, однако default runner path в inspected code напрямую запускает Antigravity bootstrap и не вызывает этот planner. Для task workload выбор model остаётся ответственностью harness внутри image или внешнего model gateway.

### Provider name не гарантирует adapter

Manifest documentation показывает Google и Anthropic examples. Но current `Generate` выполняет remote request только для `google`; другой provider возвращает unsupported provider, если не включён local fallback mode. Схема специально extensible, однако adapter support нужно проверять по коду и end-to-end test.

### Secret reference имеет узкую область

`secretKey` не копирует secret в Model record: internal client читает Kubernetes Secret. Это лучше literal env. Но наличие Model не даёт credential Task. Более того, current Task reconciler использует отдельный hardcoded Gemini lookup, а не выбирает Model resource по ссылке.

Поэтому перед production нужно ответить: кто вызывает model - AX planner или agent command? В первом случае Model подходит как platform configuration. Во втором нужен contract harness/gateway и отдельный способ безопасной выдачи credentials.

## Куда делся Gateway

Старая версия главы называла Gateway четвёртым primitive и приписывала ему listeners, allowlist и `GatewayReady`. В current protobuf такого ресурса нет. Сетевой boundary существует, но реализуется на уровне Substrate routing и EgressPolicy, а не AX Gateway manifest.

Это хороший пример правильной эволюции abstraction: отсутствие user-facing kind не означает отсутствие функции. Иногда platform становится яснее, когда policy остаётся у слоя, который реально применяет её. AX Task может пользоваться runtime networking, не копируя весь Substrate network API в свой schema.

## Как собрать primitives в рабочую декларацию

```
apiVersion: ax.io/v1alpha1
kind: Workspace
metadata:
  name: checkout-v3
  atespace: team-a
spec:
  git:
  - repo: https://github.com/example/service.git
    branch: main
    depth: 1
  files:
  - path: AGENTS.md
    content: |
      Run tests before changing code.
---
apiVersion: ax.io/v1alpha1
kind: Task
metadata:
  name: investigate-142
  atespace: team-a
spec:
  image: ghcr.io/example/agent@sha256:PINNED_DIGEST
  command: ["agent", "--ticket", "142"]
  workspaces:
  - name: checkout-v3
    path: /workspace/service
    goal: Verify the toolchain and dependencies.
  debug: false
```

Workspace расположен первым намеренно. Image pinned digest отделяет изменение runtime от изменения Task name. Goal короткий и проверяемый внешним probe. Debug выключен по умолчанию. Credentials в manifest отсутствуют. Model не добавлен, потому что этот Task использует model gateway через собственный harness; объявлять неиспользуемый resource только для красоты не нужно.

## Как выбирать primitive под задачу

| Потребность | Правильная точка | Почему |
| --- | --- | --- |
| Другой image, command или isolation boundary | Новая Task | Меняется runtime identity |
| Одинаковый checkout/setup для многих Tasks | Versioned Workspace | Переиспользуется blueprint, не live filesystem |
| Несколько repositories с разными paths | Один или несколько Workspace bindings | Первый задаёт CWD, каждый получает свой marker |
| Model для platform planner | Model resource | Internal client может прочитать config и secret reference |
| Model для agent command | Harness/model gateway contract | Task не имеет modelRef |
| Egress allowlist и credential injection | Substrate EgressPolicy/gateway | Gateway не является AX kind |
| Общий mutable filesystem нескольких agents | Отдельный storage/collaboration service | Workspace создаёт независимые copies |

## Антипаттерны

- **Считать field реализованным по наличию в YAML.** Проверяйте translation и enforcement.
- **Ставить Task первым в multi-document file.** Dependencies не сортируются автоматически.
- **Хранить API key в Task env.** Literal возвращается через API.
- **Считать Workspace общим volume.** Каждая Task получает собственную materialization.
- **Обновлять Workspace и ожидать rollout.** Existing Tasks не получают новую среду автоматически.
- **Считать WorkspaceReady semantic test.** Goal, Git или tools могут быть неполными.
- **Создавать Model и ожидать его внутри Task.** Прямой связи в schema нет.
- **Писать Gateway manifest из старой документации.** Current CLI его отвергает.
- **Включать debug постоянно.** Guest services дают process execution и file access.

## Production checklist primitives

- API kinds и fields сверены с pinned `ax.proto`.
- Atespace задан явно и соответствует namespace/security boundary.
- Workspace и Model применяются до Task и проверяются отдельно.
- Task image закреплён digest, command и working directory однозначны.
- Task resources подтверждены на уровне Substrate, а не только manifest.
- Workspace references существуют; missing reference даёт deployment failure в вашей pipeline.
- Workspace versioning не зависит от mutable update существующего имени.
- Git checkout, required tools и goal postconditions проверяются отдельным probe.
- Secrets отсутствуют в Task env, inline files и durable snapshot.
- Model используется только компонентом, который действительно читает resource; provider adapter проверен end-to-end, включая rate limits и auth failure.

## Итог

Task, Workspace и Model разделяют три разных вида решений: runtime identity, materialized environment и platform model configuration. Их полезно комбинировать, но нельзя воображать связи, которых нет в current implementation. Task пока не выбирает Model, WorkspaceReady не является полноценным acceptance test, resources ещё не доходят до runtime, а Gateway больше не входит в AX API.

Зрелая работа с primitives начинается с вопроса «кто применяет это поле и когда?». Schema описывает intent, reconciler переводит часть intent в Substrate, runner материализует Workspace, а harness выполняет agent logic. Только пройдя всю цепочку, инженер может считать capability реализованной.

В следующей главе мы углубимся в runner: почему он становится PID 1, как принимает Task и Workspace contracts, что означает maiden setup и какие обязанности нельзя переложить на обычный container entrypoint.

### Источники и дальнейшее чтение

- [AX README and three primitives](https://github.com/google/ax/blob/ac2332829f22360ff97b0ba34d94dd0dd782f17e/README.md)
- [AX v1alpha1 protobuf schema](https://github.com/google/ax/blob/ac2332829f22360ff97b0ba34d94dd0dd782f17e/pkg/apis/v1alpha1/ax.proto)
- [Validation and workspace binding helpers](https://github.com/google/ax/blob/ac2332829f22360ff97b0ba34d94dd0dd782f17e/pkg/apis/v1alpha1/types.go)
- [AX CLI apply implementation](https://github.com/google/ax/blob/ac2332829f22360ff97b0ba34d94dd0dd782f17e/cmd/ax/main.go)
- [AX manifest examples](https://github.com/google/ax/blob/ac2332829f22360ff97b0ba34d94dd0dd782f17e/docs/manifests.md)
- [AX runner lifecycle](https://github.com/google/ax/blob/ac2332829f22360ff97b0ba34d94dd0dd782f17e/runner/runner.go)
- [Workspace maiden setup implementation](https://github.com/google/ax/blob/ac2332829f22360ff97b0ba34d94dd0dd782f17e/internal/workspace/setup.go)
- [AX internal model client](https://github.com/google/ax/blob/ac2332829f22360ff97b0ba34d94dd0dd782f17e/internal/model/client.go)
- [Issue #348: Task secret and modelRef gap](https://github.com/google/ax/issues/348)
