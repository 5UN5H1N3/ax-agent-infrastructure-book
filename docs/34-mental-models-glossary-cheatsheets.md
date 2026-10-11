# Mental models, glossary и cheatsheets

После лабораторных работ и двух архитектур из главы 33 легко почувствовать, что стек наконец сложился в одну картину. Но во время реального инцидента знакомые слова снова начинают мешать: «агент завис», «snapshot есть», «Task готова». Каждая фраза скрывает несколько разных состояний. Если не уточнить, что именно наблюдается, можно чинить модель вместо очереди или повторить уже выполненную запись.

Эта глава нужна как рабочая опора: вернуться к границам ответственности, быстро найти значение термина и выбрать следующий диагностический шаг. Mental model - это упрощённая модель системы, которая помогает предсказывать её поведение. Хорошая модель не объясняет всё, зато показывает, где ожидать ограничения и какое доказательство искать. Мы сначала разберём десять таких границ, затем соберём глоссарий и памятки.

Для конкретных команд и устройства AX здесь используется тот же зафиксированный срез `ac23328`, что в главах 16-19; термины Substrate сверены со срезом `df78882` из главы 32. Это не обещание совместимости с любой версией `main` и не подмена release baseline книги. Перед применением памятки сопоставьте commit, image digest и CLI вашего deployment. Общие инженерные принципы ниже не зависят от названий ресурсов.

## Десять границ для инженерного решения

### Модель предлагает шаг, агент организует работу

**LLM не равна агенту.** Один model call преобразует переданный контекст в ответ. Агентное поведение появляется, когда система использует ответ для выбора действия, получает наблюдение из среды и решает, нужен ли следующий шаг. Цель, состояние и правила остановки принадлежат этому процессу, а не весам модели.

Предположим, модель предложила проверить DNS. Это ещё не проверка DNS: нужны tool execution, результат с источником и временем, интерпретация и проверка цели. При жалобе «агент ничего не делает» сначала найдите границу: model call не завершился, предложение отклонено policy или executor ждёт сеть? Замена модели имеет смысл при измеренной проблеме качества её решений; она не исправляет недоступный tool или отказ authorization.

### Harness исполняет решение, orchestrator размещает процесс

**Agent design, harness и orchestrator отвечают на разные вопросы.** Design определяет, какие решения поручить модели. Harness реализует цикл одного логического run: собирает context, проверяет proposals, ведёт state, budgets и tool ledger. Orchestrator обеспечивает запуск и lifecycle workload на доступной инфраструктуре. Workflow controller хранит порядок зависимых шагов и условия перехода между ними.

В одном продукте эти роли могут соседствовать. Разделение нужно не для выбора количества сервисов, а для назначения владельца ошибки. Если coordinator повторяет запрещённый write, новый Worker не исправит authorization. Если Actor некуда назначить, более подробный system prompt не добавит capacity. В главе 33 переход после отчёта Reviewer принимает доверенный workflow, а AX запускает нужную execution unit; это две разные обязанности.

### AX управляет Task, Substrate управляет sandbox lifecycle

**AX не равен Substrate.** Task описывает запуск и связанную рабочую среду; Substrate предоставляет Actor, assignment, snapshot и routing. Внутри sandbox runner готовит среду и запускает command, а harness уже выполняет прикладную задачу. Поэтому прохождение одного слоя не доказывает прохождение остальных.

Для среза `ac23328` lifecycle reconciliation выполняется синхронно в обработчиках `ax-server`. Не ищите отдельный `ax-controller` или durable очередь Redis Streams по старой схеме. Redis Pub/Sub сообщает об обновлениях, но не заменяет журнал незавершённых операций. При timeout сопоставьте AX resource, фактический Actor и runner: между этими системами нет общей транзакции.

### Логическая identity не равна физическому месту исполнения

**Actor не равен Worker, Task не равна Pod.** Actor обозначает логический экземпляр workload и его lifecycle; Worker предоставляет место исполнения. После suspend identity Actor может сохраниться, а после resume assignment измениться. Pod и node относятся к инфраструктурному размещению, а не являются единственным идентификатором задачи.

Сохраняйте цепочку связей: имя и scope Task, её ID, Actor UID, текущий assignment, template и image revision. Пересозданный объект с тем же именем не обязательно принадлежит прежней lifetime. Если расследование привязано только к IP или Pod name, после переезда workload легко сопоставить лог не с той попыткой. При отсутствии Worker проверяют eligibility и ресурсы pool, а не содержимое разговора агента.

### Контекст является входом вычисления, state является опорой восстановления

**Context, memory и durable state нельзя использовать как синонимы.** Context - фактически переданный модели input одного вызова. Memory - механизм отбора сведений из прошлой работы. Durable state - явно сохранённые цель, версии, решения и операции, которые должны пережить restart. Наличие chat history не доказывает, что незавершённый write можно безопасно восстановить.

После compaction инженер должен суметь найти approval, target, operation ID и последнее подтверждённое наблюдение без догадок по summary. Восстановленная фраза «мы уже согласовали изменение» не заменяет действующий approval record. И наоборот, файл на durable disk не попадает в context автоматически: harness должен выбрать его, проверить актуальность и включить нужные сведения. Это будет центральным вопросом главы 35.

### Snapshot сохраняет определённое состояние, backup обеспечивает восстановление системы

**Snapshot не равен backup и не равен откату внешнего мира.** В Substrate scope `Full` включает memory state и предусмотренные runtime данные, а `Data` предназначен для восстановления данных с новым запуском процессов. Node-local pause checkpoint и durable suspend snapshot имеют разные гарантии при потере node. В AX из разобранного среза используется Data-путь: рассчитывать на сохранение stack агента нельзя.

Backup policy дополнительно задаёт retention, независимый failure domain, доступ к ключам и проверенный restore. Snapshot Actor не восстанавливает всю платформу и не отменяет отправленное письмо, merge или конфигурацию маршрутизатора. После любого resume перепроверьте внешние receipts, свежесть credentials и действительность leases. Golden snapshot ускоряет общий старт, но не является индивидуальной историей задачи.

### Интерфейс инструмента не является разрешением на действие

**Tool schema, MCP и Skill решают разные задачи.** Schema задаёт форму arguments. MCP задаёт обмен между client и server и, для соответствующих transport, authorization framework. Skill описывает процедуру работы. Ни один из этих элементов сам по себе не доказывает, что конкретной identity разрешена запись в конкретный объект.

Корректный JSON может указать чужой tenant. Валидный access token может разрешать чтение, но не выбранный write. Правило в Skill «перед изменением запроси подтверждение» полезно для поведения модели, однако исполнитель должен проверить approval независимо. Для backend важны identity, target, arguments, revision и срок действия согласования. Общий prompt или доступность tool в catalog не заменяют enforcement.

### Готовность компонента не является результатом задачи

**Ready не равно outcome.** Ready имеет смысл только вместе с названием probe и временем наблюдения. В AX `WorkspaceReady` показывает результат bootstrap, а не здоровье model endpoint и не правильность вывода агента. После успешного setup эта condition может сохраняться при последующих lifecycle transitions; относиться к ней как к непрерывному acceptance test нельзя.

Разделяйте инфраструктурную готовность, жизнеспособность процесса и прикладной результат. Для coding workflow результатом будет проверенный candidate revision и receipt публикации. Для сетевого изменения нужны фактическая конфигурация и проверка эффекта на трафик в выбранном окне. Текст «успешно завершено» от модели не заменяет ни одно из этих доказательств.

### Timeout означает неизвестный исход до сверки

**Отсутствие ответа не означает отсутствие side effect.** Запрос мог не дойти, мог выполняться или мог завершиться до обрыва соединения. Retry - новая попытка, а не универсальное средство восстановления. Idempotency работает лишь в пределах конкретного contract: ключ должен распознаваться исполнительной системой, а изменённые arguments не должны молча приниматься как прежняя операция.

После timeout write сначала проверьте receipt и фактическое состояние по operation ID. Пока исход не установлен, заблокируйте конфликтующие изменения того же объекта. Журнал intent помогает расследованию, но запись перед HTTP call не создаёт exactly-once delivery. Если backend не поддерживает lookup или безопасную дедупликацию, может потребоваться ручная сверка. Из главы 33 здесь важно помнить состояние `OUTCOME_UNKNOWN`, а не только «ошибка».

### Параллелизм и tokens в секунду не равны полезной производительности

**Число агентов не равно числу одновременно активных model requests.** Часть runs ждёт approval или tool, а coordinator иногда создаёт burst из нескольких calls. Throughput inference service полезен лишь вместе с latency, error rate и качеством ответа. Больший batch может повысить aggregate tokens/s, но задержать tool call за deadline.

Выбирайте concurrency по representative workload и измеренному SLO. Отслеживайте completed verified tasks, повторные попытки и стоимость результата, а не только загрузку GPU. Если endpoint перегружен, сначала нужны bounded queue и admission; размножение Actors может увеличить очередь. Дополнительный Reviewer оправдан тогда, когда его стоимость действительно улучшает проверяемый результат, а не делает схему внушительнее.

## Глоссарий по уровням ответственности

Термины удобнее искать по слою, чем по алфавиту. Последняя колонка указывает, к какой главе вернуться за подробностями. Capitalized `Model` означает ресурс AX; слово «модель» без этого уточнения обычно означает LLM. «Задача пользователя» также не обязана совпадать с одним ресурсом `Task`.

### Агентное поведение и управление работой

| Термин | Практический смысл и граница | Главы |
| --- | --- | --- |
| Agent run | Один логический процесс достижения цели. Может пережить несколько запусков OS process. | 3-4 |
| Harness | Код вокруг модели: context, loop, validation, state, tools и budgets. Не дополнительная LLM. | 4 |
| Workflow | Зависимости и переходы работы. Долгое ожидание требует durable records и механизма восстановления. | 7, 33 |
| Context | Input данного model call, включая инструкции, evidence и descriptors tools. Ограничен token budget. | 2, 5, 35 |
| Memory | Сохранение и retrieval сведений между calls/runs. Retrieved факт может быть устаревшим. | 5, 35 |
| Artifact и provenance | Зафиксированный результат и его происхождение: источник, время, revision, digest. Происхождение не доказывает истинность. | 25, 33 |
| Tool, MCP, Skill | Операция, протокол доступа и процедура соответственно. Инструкция не выдаёт полномочия executor. | 6, 24 |
| Approval и receipt | Approval разрешает конкретное действие; receipt фиксирует принятый/исполненный запрос. Receipt не всегда доказывает полезный эффект. | 24, 33 |

### AX и Agent Substrate

| Термин | Практический смысл и граница | Главы |
| --- | --- | --- |
| Task | AX execution unit с immutable spec в данном срезе. Lifecycle resource, не вся бизнес-задача. | 17, 19 |
| Workspace | Blueprint рабочей среды. Update не означает rollout содержимого во все ранее созданные Tasks. | 17-18 |
| Model | AX configuration для platform model client. Не inference server; в данном TaskSpec нет model reference. | 17, 20 |
| Atespace | Scope ресурсов и их имён. Само имя scope не обеспечивает полную tenant isolation. | 13, 17 |
| Actor | Логический sandbox instance с identity и lifecycle. Важны UID и версия, а не только имя. | 11-14 |
| ActorTemplate | Blueprint запуска Actor и происхождение golden state. Совместимость snapshot зависит от runtime и template contract. | 13-14 |
| WorkerPool и Worker | Pool описывает физическую capacity и eligibility; Worker является конкретным target assignment. | 13, 28 |
| Tag и golden snapshot | Tag удерживает отдельную snapshot copy; golden state служит общей подготовленной точкой старта. | 13-14 |
| Runner | Process contract внутри sandbox: setup среды, child command и probes. Не workflow engine по умолчанию. | 18 |
| Gateway | Общее название boundary/proxy. Не AX API kind на `ac23328`; networking смотрите в Substrate. | 15, 17 |

### Надёжность и производительность

| Термин | Практический смысл и граница | Главы |
| --- | --- | --- |
| Reconciliation | Сведение наблюдаемого состояния к желаемому. Не общая транзакция всех участвующих сервисов. | 10, 19 |
| Lease и fencing | Lease ограничивает время владения; fencing позволяет backend отвергать действия устаревшего владельца. Это разные механизмы. | 10, 19 |
| Idempotency | Повтор операции с тем же ключом не создаёт лишний эффект в пределах contract и retention исполнителя. | 10, 33 |
| RPO и RTO | Допустимая потеря данных и срок восстановления. Требуют restore test, а не только наличия backup. | 27 |
| Prefill и decode | Обработка input и генерация output. По-разному конкурируют за compute и memory capacity. | 20-21 |
| TTFT и ITL | Время до первого token и интервал между tokens. Указывайте client/server boundary измерения. | 21 |
| KV cache | Attention state активных sequences; занимает memory. Не durable memory агента и не cache готовых ответов. | 2, 21 |
| Goodput | Полезные результаты, прошедшие критерий качества в пределах SLO. Повторные и просроченные ответы его не увеличивают. | 21, 28 |

## Памятка выбора следующего инструмента

Не начинайте с вопроса «какой framework самый мощный». Сначала назовите недостающее свойство. Тогда выбор становится проверяемым экспериментом, а не покупкой полного стека на всякий случай.

| Потребность | С чего начать | Как проверить выбор |
| --- | --- | --- |
| Известная последовательность коротких шагов | Обычная программа, CI pipeline или job | Повторяемость, bounded retries, понятный результат |
| Неизвестные заранее шаги исследования | Один harness с ограниченным tool catalog | Качество evidence и стоимость verified outcome |
| Approval на часы и recovery после restart | Durable workflow/state machine | Возобновление ожидания без повторного write |
| Изолированные executions с накопленным state | AX/Substrate после проверки runtime contract | Suspend/resume, потеря node, budgets и cleanup |
| Доступ к tools из нескольких клиентов | MCP либо узкий API с явной identity | Совместимость и object-level authorization |
| Общий model endpoint | Отдельный inference service; runtime по model/hardware | Representative load, latency и valid tool outputs |

Отдельная роль не требует отдельной Task, а отдельная Task не делает роль доверенной. Новую границу исполнения добавляют ради независимых credentials, isolation, failure recovery или placement. Новую модель выбирают ради измеримого улучшения решения. Эти причины полезно записывать рядом с architecture decision, чтобы позже понять, что именно должно измениться при росте нагрузки.

## AX памятка с разделением чтения и изменений

### Сначала проверьте адрес и scope

CLI ниже соответствует `ac23328`. Имена `lab-cluster`, `team-a` и `investigate-142` учебные: замените их на свои и проверьте deployment record. `ax version` в этом срезе выводит общую строку версии, а не точный commit бинарника; для сверки нужны provenance сборки и digest control-plane image.

```
ax help
ax version
ax ctx
kubectl config current-context
kubectl --context lab-cluster get deploy -n ax-system \
  -o 'custom-columns=NAME:.metadata.name,IMAGE:.spec.template.spec.containers[*].image'
```

`--context` выбирает Kubernetes context, `-n` у AX задаёт namespace установки control plane, а `-a` задаёт atespace ресурса. Это не три варианта одного namespace. Если используется явный `--server` или `AX_SERVER`, проверьте этот адрес отдельно: одного правильного kube context недостаточно. Автотуннель CLI может создать локальное соединение даже для диагностического запроса.

### Прочитайте resource и историю наблюдений

```
ax --context lab-cluster -a team-a get tasks
ax --context lab-cluster -a team-a get task investigate-142
ax --context lab-cluster -a team-a describe task investigate-142
ax --context lab-cluster -a team-a get workspaces
ax --context lab-cluster -a team-a get models
ax --context lab-cluster -a team-a watch task investigate-142
```

`get` и `describe` показывают сохранённый AX resource, а `watch` - поток изменений status. Watch не является durable audit log и не доказывает отсутствие действий между уведомлениями. Сопоставьте phase, conditions и timestamps с Actor и runner. Вывод может содержать Task env и inline files: перед отправкой в тикет или модель удалите credentials и персональные данные.

### Lifecycle команды применяйте после проверки preconditions

Следующий блок не предназначен для запуска целиком. Каждая строка - отдельная операция с предварительной проверкой цели и scope. Для `apply` atespace задавайте в metadata каждого manifest: глобальный `-a` не следует считать переписыванием содержимого YAML.

```
# Создание/обновление resources из проверенного manifest
ax --context lab-cluster apply -f stack.yaml
# Отдельные lifecycle действия над выбранной Task
ax --context lab-cluster -a team-a resume task investigate-142
ax --context lab-cluster -a team-a suspend task investigate-142
# Удаление - только после сохранения evidence и проверки ownership
ax --context lab-cluster -a team-a delete task investigate-142
```

`apply` применяет documents последовательно, без общей транзакции. Сначала создайте нужные Workspace/Model, проверьте их, затем Task. Существующая immutable Task не обновляется повторным create. После неоднозначного timeout сначала прочитайте объект. Resume запускает исполнение и может продолжить внешние операции; suspend не является business cancel; delete не откатывает side effects. Строгую остановку подтверждайте фактическим состоянием Actor, а не только AX phase.

Debug execution отделяйте от пассивной диагностики. Включённый `spec.debug` открывает дополнительные guest capabilities. Даже команда просмотра процессов исполняется внутри sandbox, поэтому требует разрешённого доступа и может показать sensitive arguments. Не включайте debug автоматически на production Task ради удобства.

```
# Только для уже разрешённого debug-доступа
ax --context lab-cluster -a team-a ssh investigate-142 -- ps aux
```

## Kubernetes памятка первичного расследования

Начните с namespace нужного слоя: `ax-system` для AX, namespace Substrate для его компонентов, namespace workload для конкретного Pod. Примеры ниже рассматривают AX. Не подставляйте Task name вместо Pod name: сначала найдите связь через status, deployment и logs. Общекластерный просмотр `-A` нужен лишь когда действительно проверяется placement или общий отказ, и требует соответствующего RBAC.

```
kubectl --context lab-cluster get pods -n ax-system -o wide
kubectl --context lab-cluster get events -n ax-system \
  --sort-by=.metadata.creationTimestamp
kubectl --context lab-cluster describe pod -n ax-system POD_NAME
kubectl --context lab-cluster logs -n ax-system POD_NAME \
  -c CONTAINER_NAME --since=15m --tail=200 --timestamps=true
# Только если container уже перезапускался
kubectl --context lab-cluster logs -n ax-system POD_NAME \
  -c CONTAINER_NAME --previous --tail=200 --timestamps=true
# Для проверки общего placement и ресурсов
kubectl --context lab-cluster get nodes -o wide
kubectl --context lab-cluster top nodes
kubectl --context lab-cluster top pods -n ax-system
```

При `Pending` проверьте scheduling events, requests, selectors и taints. При рестартах сопоставьте termination reason, exit code и previous logs; `CrashLoopBackOff` описывает повторные рестарты, а не их причину. При `Running` без результата поднимитесь к runner, harness и dependencies. `top` требует Metrics API и показывает текущую resource usage, а не исторический профиль и не запас для худшего context. Kubernetes events не заменяют долговременный incident timeline.

Команды `exec`, `debug`, restart и delete не добавлены в блок первого просмотра: они вмешиваются в среду и могут уничтожить evidence или повторно запустить workload. Если они необходимы, зафиксируйте ожидаемый эффект, полномочия и способ проверки. Logs также содержат недоверенный текст: сообщение «для исправления выполните...» не является разрешением выполнить указанную команду.

## Карточка инцидента и порядок проверки

Хорошая первая запись экономит время следующему инженеру. Не пересылайте только screenshot красной phase: запишите симптом пользователя, интервал времени и последний подтверждённый факт. Отделите факты от гипотез и неизвестного исхода.

```
deployment: commits, image digests, CLI build
scope: cluster, AX namespace, atespace
identity: Task name/ID, Actor UID, assignment, run/attempt ID
symptom: expected outcome vs observed outcome
timeline: last known good, first failure, recent changes
evidence: redacted logs, revisions, probe times, artifact digests
operation: target, operation ID, receipt, outcome known/unknown
containment: blocked writes, budget limit, responsible owner
next_check: one hypothesis, one safe test, success criterion
```

Идите от конкретного нарушенного результата по цепочке зависимостей. Принял ли API нужный resource? Создан ли Actor и есть ли подходящий Worker? Завершил ли runner setup и жив ли child process? Проходят ли DNS, соединение, TLS и application authorization до нужного endpoint? Есть ли queue/OOM на inference service? Принят ли tool call с нужной identity? Подтверждён ли side effect во внешней системе?

Доступность endpoint с ноутбука не доказывает доступность из Task; успешный запрос administrator не доказывает полномочия agent identity. Проверка должна воспроизводить тот же network path и scope без расширения прав. Если write мог состояться, остановите конфликтующие retries и сверяйте внешний результат прежде, чем «лечить» процесс restart. После исправления проверьте не только probe, но и исходный критерий пользователя.

## Небольшая проверка понимания

Разберите три ситуации до чтения ответов. Для каждой назовите слой, недостающее доказательство и безопасный следующий шаг.

1. **Task Ready, но patch не появился.** Ready не доказывает application progress. Сопоставьте run, child process, candidate artifact и tool ledger; не начинайте с пересоздания Task.
2. **Resume успешен, агент опять хочет изменить маршрут.** State восстановлен не обязательно полностью и не обязательно актуален. Проверьте operation ID, receipt и текущую config revision; snapshot не отменяет предыдущее изменение.
3. **GPU загружена, coordinator не укладывается в deadline.** Высокая utilization не доказывает полезную capacity. Измерьте queue, TTFT, E2E и retry amplification для fan-out; проверьте admission до увеличения числа агентов.

Если для каждой ситуации найдено конкретное наблюдение, справочник выполняет свою роль. Если ответ остаётся «агент сломался», вернитесь к границе между предложением, исполнением и проверенным результатом. Следующая глава углубляет одну из самых частых причин такого разрыва: как собирать ограниченный context из durable state, сохранять происхождение evidence и не терять важные решения при compaction.

### Источники и дальнейшее чтение

- [AX CLI на ac23328: syntax, flags и последовательное apply](https://github.com/google/ax/blob/ac2332829f22360ff97b0ba34d94dd0dd782f17e/cmd/ax/main.go)
- [AX schema на ac23328: Task, Workspace и Model](https://github.com/google/ax/blob/ac2332829f22360ff97b0ba34d94dd0dd782f17e/pkg/apis/v1alpha1/ax.proto)
- [AX reconciliation и Workspace readiness на ac23328](https://github.com/google/ax/blob/ac2332829f22360ff97b0ba34d94dd0dd782f17e/internal/controller/reconciler.go)
- [Substrate API guide на df78882: resources и lifecycle](https://github.com/agent-substrate/substrate/blob/df788825e6fc13dd7aa5fd283c8f14bd4c818247/docs/api-guide.md) и [глоссарий этого среза](https://github.com/agent-substrate/substrate/blob/df788825e6fc13dd7aa5fd283c8f14bd4c818247/docs/glossary.md)
- [MCP authorization 2026-07-28: protocol-level authorization](https://modelcontextprotocol.io/specification/2026-07-28/basic/authorization)
- [Kubernetes: Debug Pods](https://kubernetes.io/docs/tasks/debug/debug-application/debug-pods/) и [kubectl logs](https://kubernetes.io/docs/reference/kubectl/generated/kubectl_logs/)
