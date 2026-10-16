# Приложение A. Аудит проектов и выявленные расхождения

Инженер открывает README, находит знакомую схему и строит по ней процедуру обновления. Коллега смотрит исходники и видит другую архитектуру. Кто прав? Возможно, оба читают правильные материалы, но для разных версий. Опасность начинается, когда объяснение из одного среза, YAML из другого и image из третьего принимают за единую систему.

**Технический аудит связывает утверждение с версией, источником и проверкой.** Он помогает выбрать пригодный инструмент и понять его ограничения. Наличие функции в коде ещё не доказывает её работоспособность в вашей установке; цель в архитектурном документе не становится измеренным результатом. Здесь зафиксируем границы AX, Substrate и MCP, на которых построены поздние главы книги, и способ перепроверить их перед изменением deployment.

## A.1 Версии и область применимости

У книги нет одной версии, которая автоматически описывает все её зависимости. Для программного проекта фиксируйте commit исходников и digest собранного image. Для протокола - revision спецификации, версии client и server и используемые extensions. Дата проверки отвечает на вопрос «когда смотрели», а не «какие bytes запускаются».

| Компонент | Опорный срез | Что следует учитывать |
| --- | --- | --- |
| AX release | `v0.3.1`, `e70162a3`; опубликован 25.09.2026 | Историческая release point, не срез реализации поздних глав |
| AX в главах 38-43 | `ac233282`, изменение от 27.09.2026 | После v0.3.1; direct execution вместо отдельного controller и queue |
| Substrate в главах 39-43 | `v0.3.0`, `ccecc788`; опубликован 30.09.2026 | Контракты snapshot, routing и наблюдения относятся к этому срезу |
| MCP в главе 36 | Спецификация `2026-07-28` | Revision протокола не является версией SDK или tool schema |

Полные identity исходников: AX `ac2332829f22360ff97b0ba34d94dd0dd782f17e`; Substrate `ccecc788a327dc11dcd6c21ee153f3d0cbb5cc97`. Они доступны по ссылкам на [AX](https://github.com/google/ax/tree/ac2332829f22360ff97b0ba34d94dd0dd782f17e) и [Substrate](https://github.com/agent-substrate/substrate/tree/ccecc788a327dc11dcd6c21ee153f3d0cbb5cc97). Даты публикации release подтверждают страницы [AX v0.3.1](https://github.com/google/ax/releases/tag/v0.3.1) и [Substrate v0.3.0](https://github.com/agent-substrate/substrate/releases/tag/v0.3.0); дата commit не подменяет дату release.

На 11.10.2026 уже опубликован [Substrate v0.4.0](https://github.com/agent-substrate/substrate/releases/tag/v0.4.0) от 08.10.2026. Release notes отдельно помечают breaking changes в упаковке rootfs и durable-dir snapshots и выводе actor events. Это повод проверить migration и restore, а не основание объявить прежние snapshots совместимыми. Опорный срез книги остаётся v0.3.0; новая версия требует отдельного сравнительного аудита.

## A.2 Что доказывают разные источники

Вместо вопроса «какой документ главнее?» спросите, какое утверждение нужно подтвердить. Schema отвечает за допустимую форму сообщения. Реализация показывает путь выполнения при определённых условиях. Тест демонстрирует поведение в конкретной среде и с конкретными входами. Эти доказательства дополняют друг друга.

| Источник | Что он подтверждает | Чего недостаточно для вывода |
| --- | --- | --- |
| README, design, roadmap | Замысел, термины и заявленные направления | Наличие обещанного поведения в выбранной сборке |
| API schema и parser | Поля, методы, правила разбора | Исполнение policy или сохранность payload |
| Код обработчика и workflow | Условия, переходы и последствия ошибки | Полная корректность интеграции и отсутствие races |
| Воспроизводимый integration test | Наблюдаемое поведение выбранного case | Все workloads, нагрузки и варианты отказа |

Полезная запись аудита содержит точное утверждение, immutable ссылку, условия, ожидаемый результат и критерий опровержения. «Изоляция есть» слишком широко. «Caller из tenant B не может прочитать snapshot tenant A» проверяется конкретной identity, policy, объектом и отрицательным тестом. Разрешённый контрольный read помогает отличить запрет от недоступности всего storage.

Issue является свидетельством конкретного сообщения об ошибке. Закрытый issue не доказывает, что fix попал в установленный image. Найдите изменение, сравните его с вашей revision и воспроизведите relevant regression case. Аналогично, зелёный unit test не подтверждает правильную выдачу credentials в реальном cluster.

## A.3 AX и границы его реализации

### Ресурсы и процесс управления

В [AX schema ac233282](https://github.com/google/ax/blob/ac2332829f22360ff97b0ba34d94dd0dd782f17e/pkg/apis/v1alpha1/ax.proto) сервис предоставляет `Task`, `Workspace` и `Model`. Отдельного resource `Gateway` и его RPC в этом срезе нет. Сетевой gateway как компонент остаётся самостоятельным понятием; он не становится AX kind от знакомого названия.

[Manifest guide](https://github.com/google/ax/blob/ac2332829f22360ff97b0ba34d94dd0dd782f17e/docs/manifests.md) сохраняет фразу о четырёх kinds, тогда как [README](https://github.com/google/ax/blob/ac2332829f22360ff97b0ba34d94dd0dd782f17e/README.md) перечисляет три. Это расхождение внутри одного среза. Для manifest определяющими становятся schema и parser, а не количество сущностей в вводном предложении. Не добавляйте поля по аналогии с Kubernetes: `generation`, `resourceVersion` и resource UID у AX ObjectMeta здесь отсутствуют.

[Изменение ac233282](https://github.com/google/ax/commit/ac2332829f22360ff97b0ba34d94dd0dd782f17e) удаляет `cmd/ax-controller`, его deployment и Redis Streams queue. `ax-server` непосредственно выполняет reconciliation с Substrate под resource locks; Redis продолжает хранить AX records. Поэтому для этого среза queue lag и состояние отдельного controller не являются сигналами рабочего lifecycle path. Не объявляйте схему старого release ошибочной вообще: сначала проверьте, к какой версии она относится.

### Готовность процесса и успех работы

[Default runner](https://github.com/google/ax/blob/ac2332829f22360ff97b0ba34d94dd0dd782f17e/runner/runner.go) подготавливает Workspace, запускает command как child и остаётся доступным после её завершения. Его `/readyz` связан с setup, а не с результатом агента. Зелёная Task и отвечающий runner не заменяют application checkpoint и проверку внешнего результата. Custom image, встраивающий runner package, также не обязан иметь универсальный PID layout.

[ax-server](https://github.com/google/ax/blob/ac2332829f22360ff97b0ba34d94dd0dd782f17e/cmd/ax-server/main.go) может запуститься без reconciler при ошибке инициализации Substrate client. Его [health handler](https://github.com/google/ax/blob/ac2332829f22360ff97b0ba34d94dd0dd782f17e/internal/server/server.go) возвращает HTTP 200 без проверки Redis и runtime. Acceptance должен пройти полезный lifecycle и application probe, как в главе 43, а не только HTTP endpoint.

Workspace setup не является универсальным harness. Размещение files, Git repositories и skills path не доказывает работающий MCP client, действительные credentials или корректный tool result. Эти проверки принадлежат приложению. По той же причине platform lifecycle не обещает exactly-once внешние действия; approval, operation ledger и reconciliation из глав 37 и 42 остаются необходимыми.

## A.4 Substrate и условия восстановления

Substrate разделяет виртуально долгоживущий Actor и физический Worker. Kubernetes управляет инфраструктурой и Worker Pods; runtime размещает Actors и обслуживает их lifecycle. Подмена «один Actor равен одному Pod» приводит к неверным расчётам capacity и поиску логов не того процесса. Placement и Worker assignment нужно читать через соответствующий Substrate API.

[Architecture document v0.3.0](https://github.com/agent-substrate/substrate/blob/ccecc788a327dc11dcd6c21ee153f3d0cbb5cc97/docs/architecture.md) прямо предупреждает о нереализованных частях замысла. Цели по latency и density не являются benchmark вашей установки. Проверяйте выбранный sandbox class, workload, распределение active и idle Actors, storage и время до первого полезного действия. gVisor и microVM нельзя объявить взаимозаменяемыми лишь потому, что обе реализации предлагают suspend/resume.

### Snapshot является контрактом состояния

В v0.3.0 различаются Full и Data, локальная точка и external snapshot, собственные и заимствованные bytes. Full может сохранять захваченную execution при поддерживаемой совместимости; Data не содержит индивидуальную RAM и использует настроенный источник boot. Сохранение файлов само по себе не означает продолжение прежнего процесса. Подробный разбор scope и `onResume.fromData` находится в главе 39.

[CreateTag](https://github.com/agent-substrate/substrate/blob/ccecc788a327dc11dcd6c21ee153f3d0cbb5cc97/cmd/ateapi/internal/controlapi/workflow_tag.go) создаёт отдельную копию external snapshot suspended Actor. Новый Actor с `sourceTag`, напротив, сначала заимствует Tag URI: это видно в [CreateActor](https://github.com/agent-substrate/substrate/blob/ccecc788a327dc11dcd6c21ee153f3d0cbb5cc97/cmd/ateapi/internal/controlapi/actor.go). Наличие clone не даёт права удалять исходный Tag. До cleanup нужно учесть borrowers и golden dependencies, а для восстановления - metadata, payload и совместимость runtime.

Object prefix с UID различает владельцев, но не заменяет storage authorization. Схема пути не запрещает caller читать чужие bytes. [Threat model](https://github.com/agent-substrate/substrate/blob/ccecc788a327dc11dcd6c21ee153f3d0cbb5cc97/docs/threat-model.md) формулирует угрозы и желаемые защитные свойства; каждое нужное свойство должно иметь работающий enforcement path и испытание в вашем deployment. Правильное название security feature не является результатом такого испытания.

### Наблюдение имеет известные пробелы

[Actor attribution](https://github.com/agent-substrate/substrate/blob/ccecc788a327dc11dcd6c21ee153f3d0cbb5cc97/docs/observability.md) связывает текущие logs с identity workload. История после relocation требует центрального backend; application trace context не появляется автоматически в каждой строке. [Registry blind spots](https://github.com/agent-substrate/substrate/blob/ccecc788a327dc11dcd6c21ee153f3d0cbb5cc97/docs/metrics/substrate.yaml) отмечают отсутствие store metrics и неполное наблюдение worker cache. Отсутствие соответствующего alert не доказывает исправность этих участков.

## A.5 MCP и совместимость двух моделей

[MCP 2026-07-28](https://modelcontextprotocol.io/specification/2026-07-28/basic/index) использует самодостаточные requests: protocol version и client capabilities передаются в `params._meta`. [server/discover](https://modelcontextprotocol.io/specification/2026-07-28/server/discover) обязателен для server этой revision, но предварительный вызов необязателен для client. Discovery сообщает возможности; он не удостоверяет доверие к server и не выдаёт право на tool operation.

Это отличается от [revision 2025-11-25](https://modelcontextprotocol.io/specification/2025-11-25/basic/lifecycle) с `initialize` и `notifications/initialized`. Legacy integration не становится неисправной только потому, что использует тот handshake. Она должна взаимодействовать с совместимым peer. Проверяйте contract обеих сторон вместо попытки универсально добавить или удалить session header.

[Modern Streamable HTTP](https://modelcontextprotocol.io/specification/2026-07-28/basic/transports/streamable-http) не использует прежнюю protocol session. Но отсутствие скрытой session не устраняет прикладное состояние: operation handles, права, unfinished writes и deduplication всё равно нужны. Stateless transport не доказывает, что потерянный write можно безопасно повторить.

В audit record разделите protocol revision, SDK version, server image, tool schema и разрешённые extensions. Совпадение protocol revision ещё не гарантирует одинаковый смысл аргумента или поддержку optional feature. Минимальная проверка совместимости включает разрешённый read, неверный input, отказ чужому principal и понятный результат несовместимого request. Writes испытывают на simulator или ограниченном target с отдельной проверкой effect.

## A.6 Как превращать расхождение в решение

**Documentation drift** - расхождение документации с выбранной реализацией. Отделяйте его от изменения между версиями и от различия конфигураций. Если release действительно использовал queue, а следующий commit убрал её, это эволюция системы. Если guide того же commit перечисляет несуществующий kind, это drift. Если ваш image не включает нужный fix, обновлённый guide не изменит его поведение.

| Утверждение | Граница вывода | Инженерное решение |
| --- | --- | --- |
| AX управляется отдельным controller | Верно не для ac233282 | Выбрать release-specific lifecycle runbook |
| AX принимает четыре kinds | Schema этого среза определяет три | Проверять manifest по parser и API |
| Ready означает успешную работу | Runner подтверждает другой контракт | Проверять child, progress и external truth |
| Snapshot равен полному backup | Scope, ownership и dependencies различаются | Составить recovery inventory и выполнить restore drill |
| Security описана в threat model | Описание цели не доказывает enforcement | Проверить allowed и denied paths |
| MCP поддерживается обеими сторонами | Revision, transport и extensions могут различаться | Испытать конкретную client/server пару |

После классификации выбирайте действие: исправить инструкцию, сменить конфигурацию, подобрать совместимую сборку или отложить rollout до реализации недостающей защиты. Если изменением можно затронуть данные или права, нужен критерий приёмки и безопасный путь восстановления. «Обновим до latest» не является решением без проверки этих условий.

## A.7 Повторный аудит перед обновлением

Начните с release inventory из главы 43. Сопоставьте запущенные digests с provenance сборки и commit, сохраните manifests, API schema, sandbox configuration и references на защищённые policy artifacts. Не выгружайте secret values в общий отчёт. Тег или вывод версии CLI сами по себе не доказывают, какой server и runner сейчас выполняются.

Следующие команды только читают локальный AX checkout. `/path/to/ax` - заменяемый путь к проверенному репозиторию; оба указанных commits должны существовать в нём. Команды не переключают branch, не применяют manifests и не меняют cluster:

```
git -C /path/to/ax rev-parse --verify HEAD
git -C /path/to/ax ls-tree -r --name-only ac233282 -- \
  cmd/ax-controller cmd/ax-server pkg/apis/v1alpha1
git -C /path/to/ax diff --stat e70162a3 ac233282 -- \
  cmd internal deploy docs DESIGN.md
```

В дереве ac233282 нет отдельного `cmd/ax-controller`; diff показывает его удаление. Краткая статистика лишь указывает, куда смотреть дальше. Чтобы подтвердить поведение, прочитайте затронутый handler, schema и тесты именно выбранного commit. Checkout HEAD может быть другим; он не переопределяет явно заданную revision в этих командах.

Для candidate повторите сравнение с новым полным commit и digest. Выпишите изменения API, snapshot format, authentication, egress, observability и rollback. Затем проведите выбранные integration cases на ограниченном стенде. Отчёт должен различать `PASS`, `FAIL`, `BLOCKED` и `SKIPPED`; непроверенная обязательная предпосылка оставляет release gate закрытым.

Особенно проверьте переходы состояния: чтение старого checkpoint новой сборкой, обратный путь старой сборки после migration, Worker loss, обновление credentials и uncertain tool outcome. Наличие новой функции оценивайте вместе с ценой её эксплуатации. Если восстановление совместимого состояния не получается, rollout не готов, даже когда fresh start проходит.

## A.8 Практикум проверки технического обещания

Возьмите одно важное для вашей задачи утверждение, а не весь проект сразу. Например, «после выхода command readiness станет отрицательной» или «clone не зависит от исходного Tag». Работайте с synthetic данными и ограниченными lab credentials; production snapshots и чужие tenant objects для упражнения не нужны.

1. **Определите контракт.** Что пользователь должен получить и при каких условиях? Запишите identity сборки и конфигурации.
2. **Найдите путь исполнения.** Сопоставьте guide, schema, handler и storage ownership. Отделите обещание от реализованного механизма.
3. **Задайте различающий тест.** Опишите ожидаемый результат и наблюдение, которое опровергнет утверждение. Добавьте исправный контроль.
4. **Получите evidence.** Сохраните inputs, timestamps, relevant logs и проверку authoritative state без secrets. Ошибка стенда не является успешным отрицательным тестом.
5. **Примите решение.** Ограничьте область вывода, исправьте runbook или поставьте gate. Другой инженер должен суметь повторить проверку по этой записи.

Хороший аудит заканчивается не общим ярлыком «готово к production», а понятным решением для определённой задачи и сборки. Приложение B превращает такие решения в критерии готовности. Приложение C помогает находить первичные источники; для воспроизводимых выводов используйте ссылки на revision, а не меняющийся `main`.
