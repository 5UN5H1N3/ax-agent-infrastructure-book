# Multi-agent на AX: как проектировать делегирование без task explosion

**Статус:** AX MAIN ac233282 · VERIFIED 2026-10-08 · APPLICATION PROTOCOL where noted

![Multi-agent workflow на AX: платформа исполняет Tasks, приложение управляет деревом работы](.gitbook/assets/diagrams/22-20.png)  
*Multi-agent workflow на AX: платформа исполняет Tasks, приложение управляет деревом работы*

Представим обычную инженерную задачу: найти причину сбоя, подготовить исправление, прогнать тесты и проверить diff. Хочется создать четырёх агентов — researcher, implementer, tester и reviewer — и назвать это командой. Но количество процессов ничего не гарантирует. Если все они получили один repository, одинаковые полномочия и не знают, какой commit проверяют, мы получили четыре источника конфликтов, а не архитектуру.

Глава 7 уже разобрала manager, handoff, fan-out/fan-in, pipeline и generator/evaluator. Здесь вопрос практический: **как положить такую систему на AX, не приписав платформе функций, которых у неё нет?**

AX предоставляет изолированное место исполнения и lifecycle для Task. Он умеет создать Task, связать её с Workspace и Model, запустить через Substrate, suspend/resume/delete и показать инфраструктурный status. Но current AX не является multi-agent scheduler, durable workflow engine или coordinator. Декомпозиция цели, child IDs, budgets, join policy, прикладной результат и восстановление дерева — обязанности вашего orchestration layer.

Это разделение полезно. AX не должен угадывать, что означает «исследование закончено» или «review пройден». Приложение не должно вручную реализовывать sandbox, Worker assignment и восстановление Workspace. Надёжная система соединяет оба слоя явным контрактом.

## Три сущности, которые нельзя называть одним словом «агент»

| Уровень | Что это | Кто управляет | Что сохраняется |
| --- | --- | --- | --- |
| Логическая роль | Goal, instructions, tools, policy и decision loop | Harness/coordinator | Application state, messages, checkpoints |
| AX Task | Immutable launch specification и lifecycle resource | AX control plane | Spec, infrastructure status, связь с Workspace/Model |
| Substrate Actor/Worker | Изолированная runtime-сущность и место исполнения | Agent Substrate | Runtime state и snapshot в пределах его гарантий |

Связь между уровнями не обязана быть один к одному. Несколько коротких specialist calls могут выполняться внутри одной AX Task. И наоборот, один долгоживущий логический agent после suspend/resume продолжит работу в новом process из checkpoint на том же durable Workspace.

> **Создавайте AX Task ради отдельной границы исполнения, а не ради каждого шага рассуждения.**

Новый Task оправдан, когда child требует хотя бы одной реальной границы: другого security scope, собственного Workspace, отдельного image/toolchain, независимого lifecycle, resource profile, fault domain или долгого параллельного исполнения. Фраза «посмотри ещё раз» такой границей не является.

## Когда child нужен отдельный Task

| Ситуация | Отдельный AX Task? | Почему |
| --- | --- | --- |
| Короткая классификация или второй model call | Обычно нет | Новый sandbox и lifecycle дороже локального workflow node |
| Research по независимым модулям | Иногда | Полезен параллелизм и context isolation; запись не нужна |
| Запуск untrusted tests | Да | Нужна отдельная execution и security boundary |
| Изменение repository | Да, если нужен отдельный Workspace/branch | Изолирует writes и позволяет проверить exact artifact |
| Reviewer читает готовый diff | Не обязательно | Task нужен при отдельном scope, toolchain или lifecycle |
| Один agent меняет роль на следующем шаге | Нет | Смена prompt не создаёт инфраструктурную границу |
| Длительная ветка, которую нужно suspend/resume | Да | Здесь AX даёт эксплуатационную ценность |

Полезный тест: **что сломается или станет небезопасным, если выполнить child в том же Task?** Если ответ — только «так выглядит менее multi-agent», новый Task не нужен.

Есть и обратная ошибка: держать все роли внутри одного привилегированного Task, хотя tester запускает чужой код, а implementer пишет в repository. Тогда application-level роли существуют, но infrastructure-level isolation отсутствует.

## Где живёт coordinator

Coordinator — это не ax-server. Здесь так называется прикладной controller, который владеет root objective и деревом работы. Он может работать:

- как service вне AX, если должен переживать Tasks и иметь устойчивый доступ к database/queue;
- как root AX Task, если ему полезны isolation и suspend/resume;
- как deterministic workflow, который вызывает AX API, а LLM использует только для отдельных решений.

Третий вариант часто надёжнее. Создание child, проверка budget, ожидание required branches и повтор после timeout — операции state machine. Их не нужно каждый раз заново «придумывать» моделью.

Минимальные обязанности coordinator:

1. Превратить цель в ограниченные subtasks и зафиксировать версию decomposition.
2. До запуска зарезервировать budget и проверить admission limits.
3. Выдать каждому child только необходимые inputs и capabilities.
4. Создать или найти соответствующий AX Task по deterministic identity.
5. Отслеживать отдельно infrastructure state и application outcome.
6. Валидировать structured result и его связь с версиями входов.
7. Выполнить join policy, разрешить conflicts или запросить человека.
8. Отменить ненужные ветви и освободить зарезервированные ресурсы.

Если coordinator хранит всё только в своём context window, restart означает потерю системы управления. Source of truth — durable journal, а conversation — лишь один из интерфейсов к нему.

## AX Task — не запись о делегировании

В current AX Task содержит launch configuration и инфраструктурный status. CreateTask делает Task immutable, сохраняет её в Redis и синхронно запускает reconciliation с Substrate под per-resource lock. Это полезный lifecycle contract, но не business record.

AX не знает:

- parent/child relationship вашей decomposition;
- почему child был создан;
- его token/tool/cost budget;
- required output schema;
- что считать частичным успехом;
- какую join policy применить;
- завершилась ли команда внутри default runner успешно.

Как показала глава 18, default runner остаётся жив после exit child process, а current control plane не переводит Task в Completed по exit code. Поэтому phase=Running нельзя читать как «researcher ещё работает», а наличие Task — как «делегирование принято».

Нужен отдельный прикладной record:

```
delegation_id: fix-184/parser-audit
parent_id: fix-184
decomposition_version: 3
ax_task:
  atespace: eng
  name: fix-184-parser-audit-v3
objective: Найти причину падения parser на commit 7f31c2a
inputs:
  repository: repo://service@7f31c2a
expected_output:
  schema: investigation-result@2
  artifact: artifact://fix-184/parser-audit.json
authority:
  repository: read
  network: advisories-only
budget:
  model_steps: 10
  tool_calls: 25
  wall_time_seconds: 600
state: dispatched
```

Это application protocol, а не новый AX manifest. В AX Task попадает только то, что нужно runtime: image, command, env, Workspace bindings, Model и platform resources. Goal, budgets и result contract можно передать harness через versioned artifact/config, но authoritative запись должна остаться снаружи Task.

## Две машины состояний вместо одной

| AX/infrastructure state | Application state |
| --- | --- |
| Task создан или не найден | Subtask planned/dispatched |
| Initializing/Running/Suspended/Failed | accepted/running/blocked/succeeded/failed/partial/cancelled |
| WorkspaceReady condition | Input version проверена и toolchain готов |
| Actor/Worker assignment | Progress по acceptance criteria |
| Delete/Terminating | Result сохранён, проверен и больше не нужен |

Эти состояния коррелируют, но не выводятся друг из друга. Running плюс отсутствие heartbeat может означать умерший child process. Failed при create может соседствовать с уже совершённым внешним side effect. Suspended не означает, что прикладная операция была аккуратно прервана.

Coordinator принимает решение по паре фактов:

```
execution state + application state -> next action
```

Например, AX Task Running, а application heartbeat истёк: сначала проверить runner/process и operation ledger, затем решать о restart. AX Task исчез, но result artifact уже прошёл validation: child не нужно повторять. Recovery перестаёт быть догадкой.

## Идентичность и идемпотентное создание children

Timeout CreateTask не сообщает, состоялась ли операция. Соединение могло оборваться после сохранения Task или после создания Actor. Без deterministic identity повтор создаст второй child.

Логический child ID удобно строить из стабильных входов:

```
child_id = hash(root_id, decomposition_version, partition_key)
```

Из него выводится допустимое AX Task name. После timeout coordinator не делает слепой create, а выполняет GetTask по exact atespace/name и сверяет journal:

1. Task существует и contract/version совпадают — attach и продолжить наблюдение.
2. Task отсутствует — повторить create.
3. Task относится к другой версии decomposition — создать новое versioned имя.
4. Возможен внешний write — сначала reconcile operation ledger и target system.

Идемпотентность имени решает только duplicate Task. Она не делает идемпотентными git push, изменение ticket или инфраструктурный API. Для каждого side effect нужен отдельный operation ID и проверка внешнего source of truth.

## Budget propagation: ограничивайте всё дерево

Глава 21 показала, почему fan-out создаёт burst на inference endpoint. Ограничение max\_tokens у каждого child не спасает root budget. Двадцать детей с «маленьким» лимитом всё равно способны одновременно занять queue, GPU и внешние API.

Root envelope включает:

- суммарные model tokens или денежный cost;
- максимальное число model steps и tool calls;
- wall-clock deadline;
- max\_concurrency активных children;
- max\_children\_total и max\_depth;
- лимиты дорогих или рискованных tools;
- reserved capacity для synthesis, verification и recovery.

До spawn parent резервирует долю остатка:

```
sum(reserved_child_budget) <= parent_remaining_budget - root_reserve
```

Неиспользованный резерв возвращается после terminal application state, а не после исчезновения process. Если child может делегировать дальше, он получает собственный subtree envelope. Лимиты только в prompt недостаточны: admission controller проверяет их кодом.

Практически coordinator держит две очереди. Первая ограничивает число живых AX Tasks и pressure на WorkerPool. Вторая ограничивает одновременные model calls согласно capacity envelope главы 21. Task может быть Running, но ждать inference permit; это лучше, чем обрушить endpoint и запустить retry storm.

### Как остановить task explosion

Task explosion создают не только рекурсия, но и timeout retries, случайные IDs, polling через новые Tasks и попытка «спросить ещё одного эксперта» при любой неопределённости.

Spawn допускается, только если coordinator может записать независимый scope, expected gain, input version, completion predicate, budget, место результата в join policy, владельца writes и правило cancel/retry.

Глобальные guardrails просты: ограничить depth, total children, live children и spawn rate; запретить child delegation по умолчанию; применять backoff при системной ошибке; открывать circuit breaker, если несколько ветвей падают по одной infrastructure-причине. Пятнадцать повторов после недоступности Model не дают пятнадцать новых доказательств.

## Authority attenuation: child получает меньше, чем parent

Изолированный sandbox не помогает, если каждый child получает admin token. Делегирование должно уменьшать полномочия:

```
child authority = parent authority ∩ task scope ∩ policy decision
```

| Роль | Workspace/репозиторий | Execution | Внешние действия |
| --- | --- | --- | --- |
| Researcher | Read-only snapshot конкретного commit | Анализ без untrusted execution | Только разрешённые источники |
| Implementer | Отдельный branch/worktree | Build tools по необходимости | Push в branch, без merge/release |
| Tester | Artifact/commit от implementer | Sandbox для tests | Нет production credentials |
| Reviewer | Diff, goal, test artifacts | Read-only проверки | Verdict/proposal, без merge |
| Coordinator | Journal и artifact refs | Обычно без произвольного shell | Dispatch, cancel, synthesis |

Не передавайте parent environment целиком. Credentials должны быть short-lived и scoped. Tool/MCP server обязан авторизовать method и arguments, а не доверять названию роли в prompt.

AX помогает провести execution boundary через Tasks, Workspaces и sandbox runtime. Но текущий Task API сам по себе не выражает fine-grained capability policy, parent-child attenuation или approval. Эти проверки проектируются в identity/tool/gateway layers следующих глав.

## Workspace и ownership

Один общий writable checkout — быстрый путь к race condition. Researcher может читать immutable commit. Implementer владеет отдельным branch/worktree. Tester запускает exact commit/artifact, а reviewer сверяет тот же diff, а не «последнее состояние каталога».

Безопасные patterns:

- один writer, остальные read-only;
- отдельный Workspace/branch на каждый write child;
- append-only result artifacts;
- ownership по модулю, если границы действительно независимы;
- proposal/patch вместо прямой записи;
- один явный merge owner после fan-in.

Каждый result содержит input versions: repository commit, dependency lock и policy revision. Если parent уже перешёл с commit A на B, успешный тест A является stale result, а не зелёным доказательством для B.

Durable Workspace полезен для code и checkpoints, но не заменяет artifact store и journal. Coordinator не должен читать случайные файлы из child directory и угадывать завершение. Child атомарно публикует structured result, затем отмечает completion в application state.

## Result protocol и fan-in

Возвращать coordinator весь transcript дорого и опасно: там смешаны untrusted tool outputs, промежуточные гипотезы и лишний context. Нужен compact result contract:

```
{
  "outcome": "succeeded",
  "summary": "Падение вызывается несовместимым lexer mode",
  "claims": [{
    "statement": "Ошибка воспроизводится на commit 7f31c2a",
    "evidence_refs": ["artifact://fix-184/repro.log#L18-L27"]
  }],
  "artifacts": ["artifact://fix-184/patch.diff"],
  "input_versions": {"repository": "7f31c2a"},
  "limitations": ["Windows runner не проверен"]
}
```

Fan-in выполняется кодом до model synthesis:

1. Проверить schema, размер и допустимые artifact URI.
2. Сверить child ID, contract version и input versions.
3. Проверить required partitions.
4. Сгруппировать claims, не теряя provenance.
5. Сохранить contradictions и high-severity minority findings.
6. Проверить существование artifacts и test/verdict records.
7. Создать короткий synthesis context из валидированных данных.

Результат child остаётся недоверенным input. Child мог обработать prompt injection из repository, web page или tool response. Coordinator не должен исполнять инструкции из summary.

## Recovery: coordinator как reconciler

WatchTask удобен для status updates, но Redis Pub/Sub не является durable журналом промежуточных событий; после reconnect current state приходит как initial snapshot. Infrastructure watch также не сообщает application completion.

После restart coordinator читает journal и для каждого nonterminal child сверяет:

1. application record и lease/heartbeat;
2. AX Task через GetTask;
3. result/artifact store;
4. external target или operation ledger для side effects.

Затем он выбирает attach, принять готовый result, resume, retry, cancel, создать replacement или эскалировать неизвестный outcome человеку. Это reconciliation loop: наблюдаем факты, сравниваем с desired tree, выполняем минимальное безопасное действие.

### Типовые сбои и реакция

| Сбой | Опасная реакция | Правильная проверка |
| --- | --- | --- |
| Timeout после CreateTask | Сразу создать новый Task | GetTask по deterministic name и journal |
| Task Running, heartbeat отсутствует | Считать child живым | Runner/process и application lease |
| Child сообщил success, artifact отсутствует | Завершить branch | Не принимать completion до validation |
| Child умер после внешнего write | Повторить весь subtask | Operation ID и target system |
| Один child упал из-за Model outage | Spawn replacement fleet | Circuit breaker и backoff |
| Parent отменён | Удалить Tasks и считать всё откатанным | Stop new actions, acknowledgement, side effects |
| Coordinator перезапущен | Повторить fan-out | Rehydrate journal и attach |

Delete AX Task освобождает инфраструктурный ресурс, но не отменяет Git push и не является rollback. Cancellation — отдельный protocol со статусом request/acknowledged и проверкой поздних результатов.

## Сквозной пример: исправление дефекта

Root objective: «исправить issue #184 и подготовить проверяемый pull request».

### Шаг 1. Зафиксировать вход

Coordinator записывает repository commit, issue snapshot, acceptance criteria и root budget. Он не создаёт Tasks, пока decomposition не получила version и deterministic child IDs.

### Шаг 2. Параллельное исследование

Два read-only researcher loops проверяют разные scopes: parser и tests/history. Если анализ короткий и tools одинаковы, они могут жить в одном AX Task как bounded workflow. Если context велик или ветки должны независимо suspend/resume, coordinator создаёт два Tasks.

Join требует оба scope либо помечает результат partial. Coordinator проверяет evidence refs и создаёт одну diagnosis artifact.

### Шаг 3. Один владелец изменения

Implementer получает diagnosis, exact base commit и отдельный writable Workspace/branch. Capability позволяет создать commit, но не merge в main. Operation ledger связывает logical operation с branch и commit SHA.

### Шаг 4. Проверка exact artifact

Tester получает не текст «implementer всё сделал», а commit SHA. Он запускает tests в отдельном sandbox без production credentials и публикует machine-readable report. Reviewer читает goal, diff и test artifact; verdict schema различает pass, changes\_required и needs\_human.

### Шаг 5. Ограниченный цикл

При changes\_required coordinator возвращает implementer конкретные defects. Максимальное число итераций задано заранее. Новый review относится к новому commit. После исчерпания budget outcome становится needs\_human, а не запускает бесконечный generator/evaluator loop.

### Шаг 6. Финальный gate

Coordinator проверяет coverage, актуальность commit, tests, reviewer verdict и root budget. Merge/release выполняет человек или отдельный policy gate. Reviewer не получает скрытую capability на публикацию.

AX здесь создаёт и изолирует Tasks, материализует Workspaces, связывает Model и обеспечивает lifecycle. Application layer решает, кто кому делегировал, какой commit является входом, сколько осталось budget, прошёл ли review и можно ли завершать root objective.

## Что наблюдать

Оператору нужен связанный trace дерева, а не список чатов:

- trace\_id root objective;
- delegation\_id, parent\_id, decomposition/contract version;
- AX atespace/task name и Actor identity;
- input version и artifact refs;
- model/tool operation IDs;
- reserved/used budget;
- application state и infrastructure phase;
- cancellation/retry reason.

На dashboard полезны spawn count/depth, live children, admission rejects, duplicate/stale result rate, join wait, cancellation lag, cost per verified outcome и Tasks без heartbeat. Critical path определяется самой длинной зависимой ветвью, а не суммой длительностей children.

Для metrics используйте bounded labels — role, outcome, pool, model route. Уникальные task/trace IDs принадлежат logs и traces, иначе cardinality сделает monitoring новой причиной инцидента.

## Минимальный production checklist

- Есть single-agent baseline и доказана польза разделения.
- Отдельный AX Task создаётся только ради явной boundary.
- Coordinator state durable и восстанавливается после restart.
- Child IDs deterministic, create retry начинается с read.
- Infrastructure и application states разделены.
- Budgets резервируются сверху вниз и проверяются admission code.
- Depth, total/live children и spawn rate ограничены.
- Authority уменьшается, credentials не копируются из parent env.
- Writable Workspace имеет одного owner или отдельную branch.
- Output schema содержит evidence, artifacts и input versions.
- Join, partial success, retry и cancellation заданы заранее.
- External writes имеют operation IDs и reconciliation.
- Model capacity выдерживает credible fan-out burst.
- Trace связывает root, child Tasks, model/tool calls и artifacts.

## Антипаттерны

- **Task на каждый model step.** Lifecycle overhead маскируется под архитектуру.
- **Один AX status как source of truth.** Running не означает полезную работу, а Delete — rollback.
- **Coordinator только в prompt.** После restart исчезают budgets, ownership и join state.
- **Случайные child names.** Timeout превращается в duplicate execution.
- **Безграничная рекурсия.** Child может spawn без subtree budget и depth limit.
- **Общий writable Workspace.** Результат зависит от race, provenance теряется.
- **Admin credential у всех ролей.** Sandbox не ограничивает внешний blast radius.
- **Fan-in через конкатенацию transcripts.** Растут cost, prompt injection и потеря evidence.
- **Retry после неизвестного write.** Повторяет side effect вместо reconciliation.
- **Удаление child как cancellation.** Process остановлен, но внешний effect уже мог состояться.
- **Голосование одинаковых агентов.** Коррелированная ошибка становится увереннее, а не истиннее.

## Итог

Multi-agent на AX строится не формулой «один агент — один Task», а согласованием двух архитектур. AX отвечает за declarative launch, isolation, Workspace/Model bindings и lifecycle исполнителей. Прикладной orchestration layer отвечает за дерево делегирования, budgets, authority, business state, result contract, join и recovery.

Главный выбор делается до spawn: действительно ли child нужна отдельная execution boundary? Если да, AX Task даёт собственный sandbox, state и lifecycle. Если нет, оставьте работу внутри одного harness или deterministic workflow. Так сохраняется полезный параллелизм без превращения каждого решения модели в инфраструктурный объект.

После определения roles, inputs, ownership и authority возникает следующий вопрос: кто может создать такой Task, какие credentials попадут внутрь, чему доверять в tool result и какой blast radius останется при компрометации child. Этому посвящена следующая глава о security architecture.

### Источники и дальнейшее чтение

- [AX Design: архитектура, компоненты и API на снимке ac233282](https://github.com/google/ax/blob/ac2332829f22360ff97b0ba34d94dd0dd782f17e/DESIGN.md)
- [AX server: lifecycle RPC, per-resource locks и WatchTask](https://github.com/google/ax/blob/ac2332829f22360ff97b0ba34d94dd0dd782f17e/internal/server/server.go)
- [AX Task API types и validation](https://github.com/google/ax/tree/ac2332829f22360ff97b0ba34d94dd0dd782f17e/pkg/apis/v1alpha1)
- [AX runner: process supervision и application completion boundary](https://github.com/google/ax/tree/ac2332829f22360ff97b0ba34d94dd0dd782f17e/internal/runner)
- [W3C Trace Context](https://www.w3.org/TR/trace-context/)
- [NIST SP 800-53 Rev. 5: AC-6 Least Privilege](https://csrc.nist.gov/pubs/sp/800/53/r5/upd1/final)
