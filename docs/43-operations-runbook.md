# Operations runbook: диагностика по слоям и release acceptance

Дежурному пишут: «После обновления агент завис». На dashboard Task имеет phase `Running`, health endpoint отвечает, ошибок за последнюю минуту нет. Можно перезапустить всё и надеяться на улучшение. Но сначала стоит спросить: какой результат ожидался, когда пропал прогресс и какой именно процесс сейчас отвечает на health check? Без этих вопросов зелёный индикатор легко принять за работающего агента.

**Operations runbook** - проверяемая инструкция для конкретной операционной ситуации. Она связывает симптом, безопасные проверки, решение и подтверждение результата. **Release acceptance** - приёмка релиза: набор испытаний, доказывающий, что выбранная сборка сохраняет необходимые контракты. Хороший runbook помогает локализовать сбой, а хороший acceptance suite не даёт уже разобранному сбою незаметно вернуться.

Глава 29 дала общий метод troubleshooting, главы 38-40 - фактические границы AX и Substrate, глава 41 - измерение inference, глава 42 - безопасное восстановление. Здесь соберём их в рабочую процедуру дежурного и инженера релиза. Проверки относятся к AX `ac233282` и Substrate `ccecc788`; команды и доступные signals нужно сверять с реально установленной версией.

## Runbook задаёт условия и решения

Список команд ещё не является runbook. Команда может выполниться успешно и не ответить на нужный вопрос. Например, чтение Pod показывает инфраструктурный процесс, но не обязательно child process конкретного Actor: Substrate размещает Actors на Workers, а не создаёт отдельный Kubernetes Pod для каждого агента.

Для каждой процедуры запишите область применения, необходимые права, исходную проверку и ожидаемые развилки. Укажите, что делать при подтверждении гипотезы, что делать при её опровержении и когда остановиться. Добавьте предел времени или объёма диагностики, допустимые изменения, критерий восстановления и владельца escalation. Тогда следующий инженер сможет продолжить расследование, а не повторять догадки автора.

Runbook зависит от deployment. Имена namespaces, logging backend, sandbox class, endpoints и способ выдачи credentials должны быть известны заранее. Процедура «найдите нужный secret и включите debug» без границ доступа слишком опасна для ночного инцидента. Диагностическое действие может быть привилегированным, даже если формально ничего не меняет.

Версионируйте runbook рядом с release inventory и проверяйте его другим оператором на стенде. После смены API, routing или схемы логов обновление image без обновления инструкции оставляет команду с картой другой системы.

## Начните с ущерба и ограничения риска

**Triage** - первичная оценка: кого затронул сбой, что перестало работать, насколько быстро растёт ущерб и какая безопасная мера нужна сейчас. Разделите один медленный Task, потерю всей группы Workers и повторные изменения во внешней системе. Эти ситуации требуют разного масштаба реакции.

Если система продолжает повреждать данные или делать неразрешённые writes, containment важнее полного расследования. По утверждённой процедуре ограничьте новые действия или изолируйте опасный путь; сохраните доступное evidence, но не ждите идеального log bundle. Если риска новых effects нет, сначала зафиксируйте volatile state до restart. Этот порядок соответствует [методу troubleshooting Google SRE](https://sre.google/sre-book/effective-troubleshooting/): восстановление полезной работы и поиск причины связаны, но не обязаны завершаться одновременно.

В значимом инциденте назначьте человека, который принимает операционные решения, и человека, который ведёт timeline. При небольшой команде это может быть один инженер, но запись решений всё равно нужна. Параллельные «быстрые исправления» без общего журнала создают новые причины и мешают понять, какая мера помогла.

Сформулируйте impact через пользовательский контракт: «новые Tasks не получают runtime с 12:10 UTC» или «high-risk writes остановлены до сверки outcome». «Красный Pod» - наблюдение, а не описание ущерба. Нормальное ожидание approval также нельзя автоматически считать отказом платформы.

## Сохраните evidence без раскрытия secrets

Начните с identity и времени. Свяжите atespace/name Task, incarnation прикладного run, Actor UID, текущий Worker и Pod, model request и logical operation инструмента. После delete/recreate то же имя может означать другой объект. AX в данном срезе не предоставляет resource UID для Task; прикладной correlation record должен различать её incarnations.

Сохраняйте время возникновения события, время наблюдения и источник. Ответ API сейчас и log десятиминутной давности не образуют атомарный snapshot. При рассинхронизации часов укажите неопределённость, а для продолжительности используйте измерение на одном надёжном clock path. Отсутствие span при sampling или отсутствие старого log не доказывает, что операция не произошла.

Минимальный комплект содержит принятый spec с защищёнными значениями, status и conditions, runtime assignment, последние lifecycle responses, setup и command exit logs, progress checkpoint, версии artifacts и последние tool outcomes. Сохраняйте также наблюдения «это работает»: успешный запрос до инцидента и исправная контрольная Task часто полезнее ещё одного стека error.

Не прикладывайте полный `env`, kubeconfig, authorization headers, request bodies с пользовательскими данными или snapshot RAM в общий incident ticket. Metadata AX runner может содержать literal Task env. Ограничьте readers, примените redaction и храните чувствительные материалы отдельно. Redaction не должна удалять безопасные correlation IDs и timestamps, без которых невозможно восстановить последовательность.

По [observability guide Substrate](https://github.com/agent-substrate/substrate/blob/ccecc788a327dc11dcd6c21ee153f3d0cbb5cc97/docs/observability.md), Actor logs несут runtime attribution, но trace context прикладных строк зависит от самого workload. Запрос текущего Worker не заменяет историю после relocation. Централизованный backend должен удерживать нужный интервал и позволять искать Actor по UID и atespace, а не только по имени Pod.

## Выберите гипотезу и различающую проверку

Полезная гипотеза предсказывает наблюдение. «Модель сломалась» слишком широко. «Запрос ещё не дошёл до model server, потому что sandbox не разрешает нужный hostname» позволяет сравнить DNS, egress decision и server receive log. Если server уже получил запрос и поставил его в очередь, эта гипотеза перестаёт объяснять текущую задержку.

Ищите последнюю подтверждённую границу и первый неизвестный участок. Общая карта ниже показывает границы проверки, а не реальный порядок RPC или обязательную последовательность диагностики. Если есть доказательство отказа конкретного tool, не нужно каждый раз начинать с nodes. Если весь pool потерял Worker, разбор prompt пока не поможет.

![Troubleshooting ladder: infrastructure, Actor, AX, runner, dependencies, harness, outcome](.gitbook/assets/diagrams/43-42.png)  
*Troubleshooting ladder: infrastructure, Actor, AX, runner, dependencies, harness, outcome*

Выбирайте проверку, которая различает причины, и заранее определяйте её риск. Read-only probe тоже способен создать нагрузку, вызвать auto-resume или раскрыть данные. Для внешних операций используйте безопасный lookup, а не повтор исходного write. При неизвестном outcome действует gate из глав 37 и 42.

Меняйте один значимый фактор за раз на ограниченной группе. Одновременно сменить model, prompt, image и WorkerPool значит получить новый результат без объяснения причины. Отрицательный результат проверки тоже полезен: он исключает ветвь и должен остаться в timeline.

## Диагностика по слоям проверяет разные контракты

### Инфраструктура и Kubernetes

Проверьте node conditions, placement Pod, requests и limits, storage mounts, image pulls и container termination reason. Свободный суммарный CPU не гарантирует подходящее размещение: selectors, taints, topology и доступность нужного sandbox могут ограничивать выбор. `CrashLoopBackOff` описывает повторные неудачные запуски и задержку retry, а не первопричину.

Изучайте конкретный container и его previous logs. Exit code без reason и context недостаточен; например, сам по себе код 137 не доказывает OOM. Состояния container разбираются в [Pod lifecycle](https://kubernetes.io/docs/concepts/workloads/pods/pod-lifecycle/). Ошибку probes отличайте от ошибки приложения. Kubernetes [разделяет startup, liveness и readiness](https://kubernetes.io/docs/concepts/workloads/pods/probes/); неудачная readiness снимает Pod с подходящего Service traffic, а liveness может вызвать restart. Probe, зависящая от перегруженного внешнего сервиса, способна усилить отказ.

### Substrate и runtime Actor

Если Actor ещё не получил Worker, runner пока не участвует в сбое. Проверьте выбранный pool, допустимый sandbox class, assignment и доступность Worker. Если назначение есть, разделите поиск snapshot, получение payload, restore, container startup и готовность маршрута. Длинный общий resume не сообщает, какая из стадий заняла время.

Сверяйте snapshot scope, dependencies и compatibility по главе 39. URI в metadata не доказывает наличие полного payload. После lazy restore быстрый ответ resume не исключает задержку первого обращения к страницам памяти или файлам. Сопоставьте lifecycle timing с первым полезным действием workload.

Не приписывайте всю необъяснённую задержку scheduler. [Registry blind spots этого среза](https://github.com/agent-substrate/substrate/blob/ccecc788a327dc11dcd6c21ee153f3d0cbb5cc97/docs/metrics/substrate.yaml) отдельно отмечают отсутствие store metrics и неполное наблюдение worker cache. Неизмеренный участок остаётся кандидатом, а не становится исправным по умолчанию.

### AX и lifecycle path

Сначала определите архитектуру своей сборки. В [AX `ac233282`](https://github.com/google/ax/commit/ac2332829f22360ff97b0ba34d94dd0dd782f17e) отдельные `ax-controller` и Redis Streams queue удалены: reconciliation выполняется внутри `ax-server`. Поэтому ищите конкретный lifecycle RPC, Redis access, resource lock и Substrate call, а не consumer lag несуществующей очереди.

[Server может стартовать без reconciler](https://github.com/google/ax/blob/ac2332829f22360ff97b0ba34d94dd0dd782f17e/cmd/ax-server/main.go), если Substrate client не инициализирован. Его health handler при этом не проверяет Redis и runtime. Сопоставляйте startup warning, принятый resource record и фактический Actor. Timeout клиента не доказывает, что lifecycle action не выполнился; новый create с другим именем может добавить лишний runtime.

В [AX schema этого среза](https://github.com/google/ax/blob/ac2332829f22360ff97b0ba34d94dd0dd782f17e/pkg/apis/v1alpha1/ax.proto) нет `generation`, `resourceVersion` и `observedGeneration`. Сравнивайте утверждённый manifest digest, принятую immutable Task spec и прикладной release journal. Не добавляйте во время инцидента неизвестные поля в YAML. Conditions также могут быть устаревшими: `lastTransitionTime` не является непрерывным heartbeat.

### Runner и child process

Уточняйте владельца каждого endpoint. `ax-server /healthz` и runner `/healthz` отвечают за разные процессы. У default runner `/readyz` отражает успешное завершение Workspace setup, а не наличие живой command. После выхода child runner намеренно остаётся доступным для inspection; AX не получает из этого автоматический application completion.

Проверьте setup logs, фактическую Git revision и prerequisites Workspace, запуск command и её exit result. Затем смотрите прикладной checkpoint и meaningful progress. Регулярный heartbeat может сопровождать бесконечный retry одного и того же шага. Не выдавайте сохранённое `WorkspaceReady=True` за свежую проверку после каждого resume. Эти границы следуют из [runner lifecycle](https://github.com/google/ax/blob/ac2332829f22360ff97b0ba34d94dd0dd782f17e/runner/runner.go) и главы 18.

### Сеть и зависимости

Разделите DNS resolution, TCP connect, TLS с нужными SNI и trust roots, HTTP или gRPC, authentication и прикладной protocol. Успешный TCP не доказывает корректный MCP response. Проверка с ноутбука может обходить sandbox DNS, identity и egress path; воспроизводите нужный путь через разрешённый canary, а не расширяйте production allowlist до wildcard.

Для MCP различайте transport failure, tool error и неизвестный внешний outcome. Lookup у конечной системы важнее догадки по timeout. Для model call выясните, получил ли server request, сколько заняли queue и prefill, был ли первый token, чем закончился stream. TTFT, KV pressure и decode относятся к разным стадиям главы 41; один средний tokens/s скрывает задержку пользователя.

### Harness и прикладное решение

Когда нижние границы подтверждены, проверьте текущий шаг, budget, tool schema, контекст, checkpoint и ожидаемое внешнее событие. Harness может правильно ждать approval, rate-limit window или разбора `OUTCOME_UNKNOWN`. В этих случаях restart не создаёт права продолжить действие.

Если это действительно reasoning failure, сравните сохранённый input и tool responses с ожидаемыми postconditions. Проблема в извлечении данных, неправильной схеме tool и неверном плане требует разных исправлений. Успех HTTP и завершение процесса не доказывают качество ответа или нужный внешний эффект.

## Разберите зелёную Task без результата

Учебный случай: `lab/network-017` после resume остаётся `Running`. Сначала прочитали Actor и подтвердили текущий Worker, затем проверили runner readiness. Оба наблюдения нормальны. Но в command log есть exit с ошибкой, а прикладной checkpoint не продвинулся. Это уже достаточно, чтобы перестать объяснять симптом нехваткой model replicas.

| Наблюдение | Подтверждено | Ещё не подтверждено |
| --- | --- | --- |
| Actor назначен Worker | Текущее размещение runtime | Жизнь child и полезный прогресс |
| Runner ready сейчас | Setup contract runner | Результат command и доступ к tools |
| Child exit с ошибкой | Команда завершилась неуспешно | Почему она упала и был ли внешний эффект |

Сопоставьте exit с последним прикладным шагом. Если команда только читала данные, repair может свестись к исправлению prerequisites и безопасному новому запуску. Если перед exit она отправила write, сначала установите outcome по operation ID. Исправный runner в обоих случаях полезен для наблюдения, но сам ничего не доказывает о безопасности повторного запуска.

При другом наборе evidence вывод меняется. Если command жива и checkpoint говорит «ожидает approval», зависания может не быть. Если свежий probe готов, а сохранённая condition отрицательна, это расхождение observation. Runbook должен сохранять такие развилки, а не назначать restart любому отсутствию ответа.

## Минимальный сбор данных ограничьте контекстом

Ниже read-only команды для отдельного Kubernetes-стенда. Имена `ax-ops-lab`, `ax`, Pod и container иллюстративны: замените их на проверенные значения своей установки. Нужны разрешения на чтение выбранных ресурсов и logs. Команды не выполняют restore, restart или debug exec.

```
kubectl --context ax-ops-lab -n ax get pods -o wide
kubectl --context ax-ops-lab -n ax get events \
  --sort-by=.metadata.creationTimestamp
kubectl --context ax-ops-lab -n ax describe pod ax-server-lab
kubectl --context ax-ops-lab -n ax logs ax-server-lab \
  -c ax-server --since=20m --tail=200 --timestamps=true
kubectl --context ax-ops-lab -n ax logs ax-server-lab \
  -c ax-server --previous --tail=200 --timestamps=true
```

`--previous` запрашивает предыдущий экземпляр container, если он есть, а не всю историю удалённых Pods. `--tail` и `--since` ограничивают материал; при обрезке сохраните ссылку на центральную историю. Возможности описаны в [справочнике kubectl logs](https://kubernetes.io/docs/reference/kubectl/generated/kubectl_logs/). Describe и logs тоже могут раскрывать чувствительные данные, поэтому их вывод требует review перед пересылкой.

Для текущего активного Actor документация Substrate даёт отдельный путь. В этой иллюстрации kubeconfig уже указывает проверенный lab context, совместимый `kubectl-ate` установлен, Actor `network-017` находится в atespace `lab`, и разрешён диагностический доступ:

```
kubectl ate logs actors network-017 --atespace lab
```

Ошибка «Actor не выполняется на Worker» не означает потерю исторических logs. Ищите прошлую assignment и центральный backend. Actor log также не обязан содержать весь control-plane trace; иногда нужен отдельный log соответствующего ate-api или atelet request.

`kubectl top` зависит от работающего [Metrics API](https://kubernetes.io/docs/tasks/debug/debug-cluster/resource-metrics-pipeline/). Его отсутствие - отдельная неисправность наблюдения, а не доказательство нулевого потребления. Текущая CPU/memory выборка не заменяет timeline, GPU metrics, storage latency и длительности lifecycle. Kubernetes resources полезно разбирать по [руководству Debug Pods](https://kubernetes.io/docs/tasks/debug/debug-application/debug-pods/), но Actor-specific state всё равно читается через Substrate.

## Разделите смягчение восстановление и исправление причины

**Mitigation** уменьшает текущий impact: ограничивает admission, переводит безопасные reads на исправный путь или останавливает рискованные writes. **Recovery** возвращает сервис к согласованной полезной работе. **Permanent fix** устраняет причину и добавляет защиту от повторения. Это могут быть три разных изменения.

Любое state-changing действие в runbook должно иметь owner, prerequisites, предел воздействия, критерий успеха и stop condition. Перед retry lifecycle проверьте текущий Actor и завершённость прежнего attempt. Перед restart убедитесь, что есть durable application state и понятен outcome tools. Перед rollback snapshot оцените потерю состояния и выполните gates главы 42.

Не называйте временное ослабление policy восстановлением. Если canary заработала только с отключённой TLS verification или wildcard egress, исходный production contract ещё не выполнен. Такая диагностика допустима лишь в отдельном разрешённом стенде и не становится готовым repair для реального tenant.

После меры проверьте именно исходный пользовательский результат, прекращение ошибочных retries, отсутствие новых duplicates и сохранность границ доступа. Падение alert после удаления проблемной Task может означать исчезновение наблюдаемого объекта, а не решение задачи.

## Приёмка релиза проверяет контракты

Успешные build и unit tests необходимы, но не подтверждают интеграцию конкретных images, schemas, sandbox, gateway и внешних tools. Acceptance suite работает на выбранной матрице окружения и фиксирует expected outcome вместе с доказательствами. Smoke test быстро выявляет грубый отказ; regression suite удерживает ранее разобранные случаи; нагрузочные и recovery испытания проверяют другие риски.

| Группа | Сценарий | Критерий приёмки |
| --- | --- | --- |
| Lifecycle и setup | Create/resume, pinned Git Workspace, child exit | Правильный Actor, фактические files и отдельный application outcome |
| Tools | MCP read, denied write, approved write | Schema и result верны; запрет не создаёт effect; write сверяется по operation ID |
| Границы доступа | Чужой tenant, forbidden egress, stale credential | Отказ на нужной границе с понятным reason и без policy bypass |
| State и отказ | Suspend/resume, Worker loss, выбранный restore | Checkpoint и dependencies сохранны; нет двух writers и повторного effect |
| Model contract | Короткий и длинный запрос, перегрузка | Quality и latency budget выполнены; очередь и retry ограничены |
| Recovery | Restore control state и application payload | Mappings, свежие права и reconciliation проходят gates главы 42 |

Negative tests должны доказывать конкретный запрет. Timeout запрещённого tool не подтверждает authorization: endpoint мог быть недоступен всем. Нужны expected deny reason, безопасная разрешённая контрольная операция и проверка отсутствия внешнего изменения. Неожиданно разрешённое действие останавливает rollout, даже если остальные тесты зелёные.

Для теста repeated write воспроизведите потерю receipt на synthetic tool и проверьте lookup, прежний idempotency key и отсутствие второго logical effect. Это испытание вашего harness и adapter, а не встроенное обещание exactly once от AX. Для child exit сравните exit log с application result; green Task phase не является oracle успеха.

## Задайте условия и результат каждого прогона

Фиксируйте control-plane и runner digests, API schemas, sandbox/runtime versions, model ID и параметры, MCP adapter и server version, набор fixtures и policy revision. Для performance сохраните workload classes, concurrency, tokens и hardware profile. Входы должны быть воспроизводимыми, но model seed сам по себе не обещает byte-identical результат на любой сборке.

Различайте `PASS`, `FAIL`, `BLOCKED` и `SKIPPED`. Отсутствующий cluster, credential или backup не превращает обязательный DR test в успех. В release gate обязательные prerequisites и evidence проверяются явно; нулевой exit code оболочки не заменяет отчёт по test cases. Отключённый обязательный сценарий оставляет gate закрытым.

Следующий YAML - учебный контракт внутреннего test runner, не AX manifest и не готовая конфигурация существующей CI-системы. Fixture IDs и пределы иллюстративны; evidence становится результатом реального прогона, а не исходным разрешением:

```
format: acceptance-contract/v1
environment: ax-ops-lab
release_inventory: pinned-release-01
fixture_set: synthetic-network-v1
policy_revision: reviewed-policy-01
required_cases:
  - lifecycle-and-child-exit
  - denied-and-approved-tool-write
  - resume-after-worker-loss
  - restore-with-action-reconciliation
limits:
  case_timeout_profile: measured-lab-timeouts
  max_tool_attempts: 2
gates:
  unexpected_write_count: 0
  required_case_status: PASS
  missing_evidence: reject
result:
  report_reference: pending
  decision: not-evaluated
```

Пригодный report связывает каждый case с versions, input, timestamps, expected result, actual result и проверкой external truth. Ручное исключение требует отдельного решения владельца риска, срока и ограничения rollout; его нельзя спрятать под общим «all tests passed». Credentials и реальные персональные данные в fixtures не входят.

## Canary и rollback учитывают состояние

**Canary** допускает новую сборку к ограниченной репрезентативной группе и сравнивает её с контрольной. Заранее задайте stop conditions, владельца решения и окно наблюдения. Для агентов окно должно охватывать meaningful work, ожидание tools и нужный suspend/resume цикл, а не только старт Pod. Принципы контрольной группы и достаточного наблюдения разобраны в [Google SRE Workbook](https://sre.google/workbook/canarying-releases/).

Сравнивайте metrics canary отдельно от control и применяйте абсолютные SLO. Один общий p95 может скрыть отказ малой группы. Учтите shared failure domains: две версии могут использовать один store или tool account, и новая схема или write canary затронет старую группу. Сопоставляйте одинаковые workload classes и не выдавайте различие input lengths за regression binary.

Для stateful agents нельзя бездумно зеркалировать production запросы обеим версиям: два исполнителя могут сделать два внешних действия. Shadow evaluation использует записанные read-only fixtures или tool simulator; production mutation path остаётся у одного подтверждённого owner. Перевод существующего run требует отдельной совместимости checkpoint, schemas и unfinished operations.

Rollback image не равен rollback данных. Старая версия должна читать новую schema и snapshots либо иметь поддерживаемую migration procedure. Отмена deployment не отменяет уже созданный тикет. Перед update проверьте rollback на стенде; если обратного пути после migration нет, подготовьте explicit forward recovery и отдельное согласование риска. Удалить Task и создать заново - не универсальная миграция.

## Post incident превращает наблюдения в защиту

В post-incident record разделяйте trigger, механизм отказа, contributing factors и detection gap. «После релиза» описывает соседство во времени. Доказанная причина объясняет, почему изменение нарушило контракт и каким наблюдением это подтверждено. «Оператор ошибся» не заменяет вопрос, почему интерфейс, policy или тесты позволили опасное действие.

Handoff следующему дежурному должен содержать impact, последнюю исправную границу, текущую гипотезу и её evidence, timeline принятых мер, незавершённые operations и разрешённый следующий шаг. Укажите также запрещённый шаг, если он способен повторить write или потерять checkpoint. Это экономит время и предотвращает попытку «начать всё заново».

Для каждого corrective action назначьте владельца, критерий проверки и срок. Полезный regression test сначала воспроизводит defect или нарушение guard, а после исправления проходит. Runbook считается доработанным, когда другой оператор может по нему локализовать такой же сбой и подтвердить восстановление.

## Практикум диагностики и приёмки релиза

Работайте в отдельном стенде с synthetic tenant и проверяемым test tool. Нет production mutation credentials; fault injection, debug access и restore допускаются только в заранее утверждённой области. Цель - получить решение с evidence, а не запустить как можно больше команд.

1. **Зафиксируйте baseline.** Сохраните inventory, fixtures и успешный application outcome. Проверьте, что report не раскрывает secrets.
2. **Воспроизведите симптом.** Пусть test command завершится с ошибкой после setup, оставив runner живым. Сравните Task phase, свежие probes, child exit и progress.
3. **Проверьте развилку.** На отдельном run задайте штатное ожидание approval. Runbook должен отличить его от child failure и не предложить бессмысленный restart.
4. **Соберите историю.** После lab relocation найдите Actor по UID и предыдущим assignments в центральных logs. Отметьте, что текущий Pod не содержит полной истории.
5. **Испытайте security gate.** Denied write должен дать ожидаемый отказ и не изменить test object. Разрешённый контрольный read подтверждает, что test endpoint доступен.
6. **Испытайте uncertain outcome.** Потеряйте receipt synthetic write и восстановите более ранний checkpoint. Lookup должен вернуть прежний outcome без второго effect.
7. **Прогоните candidate.** Выполните required cases и совместимость rollback. Отсутствующий prerequisite или неожиданная mutation должны закрыть release gate.
8. **Передайте процедуру.** Другой оператор повторяет triage и объясняет каждое решение по report. Добавьте найденные пробелы в runbook и regression suite.

На этом основной маршрут книги завершён. Инженер должен уметь выбрать границу исполнения, определить источники истины, измерить полезную работу, безопасно восстановить состояние и доказать пригодность изменения. Приложения помогают сверять детали, но checklist и исторический аудит не заменяют испытание конкретной сборки.

### Источники и дальнейшее чтение

[Google SRE troubleshooting](https://sre.google/sre-book/effective-troubleshooting/) и [canary releases](https://sre.google/workbook/canarying-releases/). Kubernetes: [Debug Pods](https://kubernetes.io/docs/tasks/debug/debug-application/debug-pods/), [probes](https://kubernetes.io/docs/concepts/workloads/pods/probes/), [logs](https://kubernetes.io/docs/reference/kubectl/generated/kubectl_logs/), [Metrics API](https://kubernetes.io/docs/tasks/debug/debug-cluster/resource-metrics-pipeline/). AX `ac233282`; Substrate `ccecc788`. В книге: главы 18, 25, 29, 37-42 и приложения.
