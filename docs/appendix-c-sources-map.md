# Приложение C. Карта источников

Вы готовите очистку storage после учебного запуска. Новые Actors созданы из Tag и уже видны в API. Можно ли удалить исходный Tag? По названию операции легко представить, что каждый Actor получил собственную копию. Но решение зависит от того, что именно скопировано: запись с адресом snapshot или сами файлы. Одна ссылка на общий обзор эту разницу не объяснит.

Карта источников нужна для таких вопросов. Она помогает выбрать документ, найти нужный участок реализации и понять, какое решение из него следует. Здесь собраны основные маршруты по стеку книги; подробные ссылки рядом с главами дополняют их. В C.8 мы пройдём один маршрут целиком, используя только чтение исходников. К концу приложения вы сможете собрать короткое, проверяемое обоснование выбора и назвать наблюдение, которое потребуется от вашей установки.

## C.1 Как выбрать источник и сохранить контекст

### Начать с решения, которое предстоит принять

Попробуйте заменить тему вопросом о действии. Вместо «почитать про snapshots» спросите: «Какие данные должны остаться, чтобы этот Actor восстановился после потери Worker?». Вместо «разобраться с MCP» - «Поддержит ли эта пара client/server нужный способ вызова tool?». Тогда у чтения появится понятная граница: найденное объяснение должно менять конфигурацию, проверку, выбор компонента или порядок действий оператора.

Полезный маршрут чтения: **объяснение → контракт → реализация → проверка в нужных условиях**. Руководство вводит понятия. Контракт определяет поля, допустимые сообщения и обязательства сторон. Реализация показывает ветку, которая обрабатывает выбранный случай. Проверка связывает это знание с конкретной сборкой и входами. При знакомстве со стеком можно остановиться на первых шагах; при решении об удалении данных понадобятся также зависимости и наблюдения от установки.

У источников нет универсальной очереди авторитетности. Для вопроса «как должен выглядеть request?» нужна спецификация. Для вопроса «что делает этот server при таком request?» нужен код его версии. Если реализация нарушает спецификацию, её поведение остаётся фактом этой реализации, а нарушение - причиной завести дефект или отказаться от интеграции.

| Источник | Какую работу он делает | Что искать читателю |
| --- | --- | --- |
| Guide и README | Объясняют модель и обычный путь настройки | Предпосылки, выбранный профиль и ссылки на точный контракт |
| Schema и спецификация | Задают структуру и требования | Поле, тип, обязательность, default и условия применения |
| Исходный код | Показывает конкретную обработку | Entry point, проверки, вызовы helpers и запись результата |
| Тест | Показывает сценарий с проверяемым ожиданием | Входы, assertions, mocks и реальные границы исполнения |
| Release notes и issue | Помогают найти изменение или известную проблему | Commit исправления и ветку, в которую он попал |
| Наблюдение установки | Показывает результат при данных условиях | Сборку, конфигурацию, вход, измерение и прикладной результат |

Тест особенно полезно читать от assertion назад. Если он проверяет только, что mock получил вызов `CopyPrefix`, то подтверждает выбор пути, но не сохранность bytes в настоящем object storage. Следующий опыт выбирают по оставшемуся вопросу: доступ к payload, совместимость runtime или корректность восстановленного приложения. Метод такого аудита подробно разобран в [приложении A](appendix-a-audit.md), а критерии допуска - в [приложении B](appendix-b-production-readiness.md).

### Различать исходник, сборку и запущенный экземпляр

Commit фиксирует дерево исходников. Из него могут собрать несколько images с разными параметрами и зависимостями; установленный server может содержать локальный patch. Digest идентифицирует содержимое image, но соответствие этого image исходникам устанавливают сведения о сборке и доверенный provenance. Для воспроизведения нужны обе стороны этой связи.

В записи решения сохраните repository и полный commit, путь и symbol, затем image digest и значимую конфигурацию установки. Для протокола добавьте revision и capabilities обеих сторон; для модели - её revision или hash, tokenizer и template. Если исследуете только код, поле с установленной сборкой может остаться неизвестным. Тогда вывод относится к прочитанному пути исполнения.

Единая [карта версий находится в главе 0](00-reading-guide.md). Здесь AX разбирается по `ac233282`, Substrate - по release `v0.3.0`, commit `ccecc788`. AX `v0.3.1` сохраняется как отдельный релизный контекст. Отдельные разборы используют development-срезы Substrate: threat model `296e329a` в главе 23, experimental authz `36d6d222` в главе 24 и lab `df788825` в разделах 25-31, 32 и 34, где он указан. Между ними нужно явно переключать источник.

Профиль inference главы 41 использует vLLM `v0.31.0`; практикум 32 - отдельную пару AX `v0.3.1` / Substrate `df788825` и vLLM `v0.30.0`. MCP читается по revision `2026-07-28`, legacy `2025-11-25` отмечен отдельно. Эти обозначения сохраняют контекст разборов, а совместимость выбранной для запуска пары проверяется интеграционным тестом.

Полные SHA в ссылках закрепляют объекты Git. `main`, `master` и `latest` перемещаются; tag тоже следует разрешить в commit при подготовке воспроизведения. У rolling-документации сохраните URL, дату и нужный фрагмент вместе с версией установленного компонента. Если страница недоступна или переехала, ищите файл в закреплённом дереве либо архив соответствующего release. Молчаливая замена на сегодняшнюю страницу может изменить сам ответ.

### Выбирать инструмент по масштабу вопроса

Для одного поля обычно достаточно браузера: переключите commit, откройте schema и сделайте permalink на нужный участок. Локальный Git удобнее, когда требуется искать несколько symbols, проследить helpers или сравнить версии. `git show` читает файл выбранного дерева без checkout; `git grep` ищет в этом дереве; `git diff` показывает изменение между двумя commits. Назначение команд описано в официальных справках [show](https://git-scm.com/docs/git-show), [grep](https://git-scm.com/docs/git-grep) и [diff](https://git-scm.com/docs/git-diff).

IDE с переходом к определению полезна для большого call graph. Убедитесь, что она индексирует нужный checkout и конфигурацию сборки: навигация может вести в generated code или другую реализацию interface. Поисковик помогает найти репозиторий, а AI-помощник - предложить symbols и объяснить незнакомый синтаксис. Окончательные ссылки, условия ветки и вывод проверьте по файлам. Для поведения внешнего сервиса следующим инструментом становится ограниченный integration test с известным ожидаемым результатом.

## C.2 AX и границы прикладного harness

Начните с того, чью ответственность выясняете. AX хранит и согласует свои ресурсы; runner подготавливает Workspace и запускает command; прикладной harness управляет циклом модели и инструментов. Harness - обвязка, которая хранит progress приложения, выбирает следующий шаг, проверяет разрешение и принимает результат tool. Чтобы понять судьбу потерянной записи во внешнюю систему, придётся читать также adapter и operation ledger приложения.

| Инженерный вопрос | Куда идти в срезе ac233282 | Что получить из чтения |
| --- | --- | --- |
| Что такое Task, Workspace и Model? | [README](https://github.com/google/ax/blob/ac2332829f22360ff97b0ba34d94dd0dd782f17e/README.md) и [concepts](https://github.com/google/ax/blob/ac2332829f22360ff97b0ba34d94dd0dd782f17e/docs/concepts.md) | Роль ресурса и его связь с приложением и Substrate |
| Принимает ли API нужное поле? | [Manifest guide](https://github.com/google/ax/blob/ac2332829f22360ff97b0ba34d94dd0dd782f17e/docs/manifests.md) и [ax.proto](https://github.com/google/ax/blob/ac2332829f22360ff97b0ba34d94dd0dd782f17e/pkg/apis/v1alpha1/ax.proto) | Тип поля и место его обработки server; список поддерживаемых kinds |
| Кто выполняет reconciliation? | [ax-server entry point](https://github.com/google/ax/blob/ac2332829f22360ff97b0ba34d94dd0dd782f17e/cmd/ax-server/main.go) и [server implementation](https://github.com/google/ax/blob/ac2332829f22360ff97b0ba34d94dd0dd782f17e/internal/server/server.go) | Direct path этого среза, условия запуска reconciler и вызывающий RPC |
| Что означает готовность runner? | [Runner guide](https://github.com/google/ax/blob/ac2332829f22360ff97b0ba34d94dd0dd782f17e/docs/runner.md) и [runner.go](https://github.com/google/ax/blob/ac2332829f22360ff97b0ba34d94dd0dd782f17e/runner/runner.go) | Setup, запуск child process, обработку его завершения и inspection |
| Как оценить обещанную возможность? | [DESIGN](https://github.com/google/ax/blob/ac2332829f22360ff97b0ba34d94dd0dd782f17e/DESIGN.md), [roadmap](https://github.com/google/ax/blob/ac2332829f22360ff97b0ba34d94dd0dd782f17e/docs/roadmap.md), затем реализация | Статус замысла и наличие конкретного механизма в выбранном дереве |

Например, вам нужен предел расходов агента. В roadmap можно найти направление развития, в schema - место для параметра, а в коде - фактическую проверку перед следующим model/tool call. Прочитайте, кто прекращает новые попытки и что происходит с уже отправленным действием. Такой маршрут показывает, нужен ли контроль внутри harness и какой failure case включить в тест. Одного поля с названием budget для этого решения мало.

Для смены архитектурного профиля откройте [дерево AX v0.3.1](https://github.com/google/ax/tree/e70162a34037c221fe6fadefd98308c05a4ad8f3) и сравните его с `ac233282`. В первом есть отдельный controller; во втором reconciliation выполняет server. Поэтому старый runbook поиска зависшей очереди нужно сопоставить с новым путём обработки. [Issues AX](https://github.com/google/ax/issues) помогают найти обсуждение и fix; включение fix в используемую сборку устанавливается по commit и сведениям о её происхождении.

Для готовности приложения выберите наблюдение его полезного результата: отчёт, изменение объекта или проверку цели. Ответ probes и exit code процесса относятся к своим контрактам. В главе 43 показано, как связать эти наблюдения в приёмку, сохраняя отдельный статус каждого case.

## C.3 Substrate и зависимости восстановления

В Substrate полезно сначала разделить логический Actor, физическое место исполнения и сохраняемые bytes. Один и тот же Actor может менять Worker. Metadata сообщает, на какой snapshot он ссылается, а storage policy определяет, кто хранит payload и когда его удаляет. Вопросы о lifecycle, capacity и retention поэтому ведут в разные участки проекта.

| Задача | Источники Substrate v0.3.0 ccecc788 | Что проследить |
| --- | --- | --- |
| Разделить Actor, Worker и pool | [Architecture](https://github.com/agent-substrate/substrate/blob/ccecc788a327dc11dcd6c21ee153f3d0cbb5cc97/docs/architecture.md), [API guide](https://github.com/agent-substrate/substrate/blob/ccecc788a327dc11dcd6c21ee153f3d0cbb5cc97/docs/api-guide.md), [ateapi.proto](https://github.com/agent-substrate/substrate/blob/ccecc788a327dc11dcd6c21ee153f3d0cbb5cc97/pkg/proto/ateapipb/ateapi.proto) | Lifetime ресурса, assignment и связь с инфраструктурой |
| Выяснить владение snapshot | [Tag workflow](https://github.com/agent-substrate/substrate/blob/ccecc788a327dc11dcd6c21ee153f3d0cbb5cc97/cmd/ateapi/internal/controlapi/workflow_tag.go) и [Actor creation](https://github.com/agent-substrate/substrate/blob/ccecc788a327dc11dcd6c21ee153f3d0cbb5cc97/cmd/ateapi/internal/controlapi/actor.go) | Copy, borrowed URI, финализацию и cleanup |
| Понять доступ к control plane | [Authentication](https://github.com/agent-substrate/substrate/blob/ccecc788a327dc11dcd6c21ee153f3d0cbb5cc97/docs/authentication.md) и [Threat model](https://github.com/agent-substrate/substrate/blob/ccecc788a327dc11dcd6c21ee153f3d0cbb5cc97/docs/threat-model.md) | Принятого principal, разрешённые RPC и доверенные границы |
| Ограничить исходящий доступ | [Network egress](https://github.com/agent-substrate/substrate/blob/ccecc788a327dc11dcd6c21ee153f3d0cbb5cc97/docs/network-egress.md) и [Credential injection](https://github.com/agent-substrate/substrate/blob/ccecc788a327dc11dcd6c21ee153f3d0cbb5cc97/docs/egress-credential-injection.md) | Маршрут, TLS trust и выдачу credential для target |
| Собрать историю workload | [Observability](https://github.com/agent-substrate/substrate/blob/ccecc788a327dc11dcd6c21ee153f3d0cbb5cc97/docs/observability.md) и [metric registry](https://github.com/agent-substrate/substrate/blob/ccecc788a327dc11dcd6c21ee153f3d0cbb5cc97/docs/metrics/substrate.yaml) | Labels, stages, доставку событий и перечисленные пробелы наблюдения |

При выборе изоляции сначала прочитайте threat assumptions, затем найдите enforcement конкретного пути. В закреплённом authentication guide обычные доверенные providers получают широкий доступ к RPC; наличие JWT не следует читать как готовый fine-grained RBAC. Это существенное условие выбора deployment boundary. Для экспериментов с authz из главы 24 откройте именно её срез и отдельно составьте профиль доступа.

Для storage ищите операции, создающие новый payload, и операции, только сохраняющие его адрес. Затем проверьте удаление и смену текущей точки. При Full restore добавляется совместимость захваченной среды; при Data - источник boot и необходимые golden dependencies. Разбор этих механизмов находится в [главе 39](39-substrate-snapshot-ownership.md). В C.8 мы ограничимся одним звеном: зависимостью нового Actor от исходного Tag сразу после создания.

Development quickstart главы 26 закреплён в [README df788825](https://github.com/agent-substrate/substrate/blob/df788825e6fc13dd7aa5fd283c8f14bd4c818247/README.md). Выбирайте его для воспроизведения того lab, а release-документы - для механизма своей версии. При обновлении [Substrate releases](https://github.com/agent-substrate/substrate/releases) дают список изменений, по которому выбирают schema diff и затронутые restore cases. Перенос snapshots требует проверки совместимости формата, runtime assets и зависимостей.

## C.4 MCP и выбор совместимой пары

Сначала запишите, какую операцию должно уметь приложение: короткое чтение, запись с approval, получение промежуточного результата или продолжение долгой работы. Затем установите revision протокола, transport и нужные возможности. SDK делает вызовы удобными, но выбор его версии следует из этого профиля. Поддержка одного простого tool не отвечает на вопросы о всех остальных сценариях.

| Вопрос интеграции | Первичный источник | Что сопоставить со своей парой |
| --- | --- | --- |
| Что обязательно в сообщениях? | [Base protocol 2026-07-28](https://modelcontextprotocol.io/specification/2026-07-28/basic/index) | Самодостаточный request, metadata и schema |
| Как узнать возможности и вызвать tool? | [Discover](https://modelcontextprotocol.io/specification/2026-07-28/server/discover) и [Tools](https://modelcontextprotocol.io/specification/2026-07-28/server/tools) | Capabilities, input/output, обработку ошибок и annotations |
| Что требует HTTP transport? | [Streamable HTTP](https://modelcontextprotocol.io/specification/2026-07-28/basic/transports/streamable-http) | Headers, authentication context и поведение соединения |
| Откуда взялся другой порядок сообщений? | [Legacy lifecycle 2025-11-25](https://modelcontextprotocol.io/specification/2025-11-25/basic/lifecycle) и [контекст изменений 2026](https://blog.modelcontextprotocol.io/posts/2026-07-28/) | Revision сохранённого trace и правила каждой стороны |

Например, в trace виден `initialize`. Для revision `2025-11-25` это часть handshake. В модели `2026-07-28` request несёт собственные protocol metadata и client capabilities. Чтобы оценить trace, выясните заявленную revision, а затем сравните сообщение с её требованиями. Перестройка современного клиента по старому примеру могла бы внести ошибку в исправную интеграцию. Подробный разбор сообщений находится в главе 36.

На разрешённом fixture проверьте корректный read, ошибочный input и понятную ошибку несовместимому запросу. Для доступа добавьте разрешённый контрольный вызов и ожидаемый отказ чужому principal. Для write заранее выберите способ выяснить outcome после потери ответа. В integration record сохраните revision, версии и builds client/server SDK, transport и нужные capabilities.

Аннотация tool помогает интерфейсу описать действие; право выполнять его проверяет соответствующая граница доступа. Для отсутствия второго внешнего effect нужны контракт adapter/provider и проверка H4 из приложения B. Это отдельная часть решения о выборе клиента с автоматическими retries.

## C.5 Kubernetes и выбор границы изоляции

У похожих симптомов бывают разные механизмы. Failed request может означать неготовое приложение, запрещённый маршрут или отказ авторизации на target. Выберите границу, где должно выполняться нужное условие, и найдите её реализацию. [Kubernetes Concepts](https://kubernetes.io/docs/concepts/) удобно использовать как оглавление для этого поиска.

| Решение | Руководство | Следующий инженерный шаг |
| --- | --- | --- |
| Разделить startup, liveness и readiness | [Pod probes](https://kubernetes.io/docs/concepts/workloads/pods/probes/) | Установить, что измеряет endpoint и какое действие следует за failure |
| Ограничить сетевую достижимость | [NetworkPolicy](https://kubernetes.io/docs/concepts/services-networking/network-policies/) | Проверить поддержку enforcement network plugin, selectors и контрольный разрешённый путь |
| Хранить credentials | [Secrets](https://kubernetes.io/docs/concepts/configuration/secret/) | Выбрать доступ, защиту хранения и способ отзыва у конечного provider |
| Разобраться с расширением API | [Custom resources](https://kubernetes.io/docs/concepts/extend-kubernetes/api-extension/custom-resources/) | Найти реальный API своего объекта и его controller |
| Выбрать sandbox | [gVisor architecture](https://gvisor.dev/docs/architecture_guide/intro/) | Сопоставить угрозы, совместимость workload, ограничения и стоимость исполнения |

Например, перед применением NetworkPolicy выясните, где возникает traffic агента: в обычном Pod network path, внутри guest или через egress proxy. Затем найдите механизм, который действительно контролирует этот маршрут. Нужны два наблюдения: разрешённый запрос проходит, запрещённый не достигает тестового target. Ошибка DNS без исправного контрольного запроса оставляет причину неопределённой. Конкретные ограничения enforcement сверяйте с документацией используемого plugin.

При выборе sandbox задайте вопрос о том, что считаете недоверенным: код внутри гостя, его зависимости или доступные ему данные и credentials. Изоляция исполнения сужает последствия захвата среды. Доступ к внешнему tool account регулируется отдельно. Даже сильная граница вокруг процесса не помешает выполнить разрешённый этому account опасный write, поэтому в решении должны присутствовать оба слоя.

Среду для обучения выбирайте по опыту, который хотите провести. [Kind](https://kind.sigs.k8s.io/docs/user/quick-start/) удобен для локальной работы с Kubernetes API и manifests. [K3s architecture](https://docs.k3s.io/architecture) помогает оценить компактную установку на выделенных nodes. [kubeadm guide](https://kubernetes.io/docs/setup/production-environment/tools/kubeadm/create-cluster-kubeadm/) полезен, когда учебная задача включает устройство и подготовку самого кластера. Возможность запустить выбранный sandbox backend проверяют по его host requirements и вашей topology.

Для nested lab главы 26 начните также с [Proxmox Nested Virtualization](https://pve.proxmox.com/wiki/Nested_Virtualization). Расширения CPU должны быть доступны гостевой VM, а нужные runtime interfaces - среде исполнения. В протоколе опыта разделите «кластер установлен» и «нужный sandbox path испытан». Так результат одной проверки не подменит другую.

## C.6 Inference и выбор измеримого профиля

Предположим, агент отвечает слишком долго, и команда предлагает включить оптимизацию из свежего benchmark. Сначала выясните, где тратится время: ожидание очереди, обработка input или генерация tokens. Затем смотрите документацию механизма, который может изменить эту часть. Короткий prompt и длинный context предъявляют разные требования; выигрыш на чужой модели ещё не даёт основания менять свой deployment.

Для профиля главы 41 используйте следующие маршруты vLLM `v0.31.0`:

- [OpenAI-compatible server](https://docs.vllm.ai/en/v0.31.0/serving/online_serving/openai_compatible_server/) и [engine arguments](https://docs.vllm.ai/en/v0.31.0/configuration/engine_args/) - для endpoints, startup flags и поддерживаемых режимов. Сопоставьте client request с фактическим model/tokenizer и chat template. Совпадение формы API оставляет эти параметры существенными.
- [Conserving memory](https://docs.vllm.ai/en/v0.31.0/configuration/conserving_memory/) и [parallelism and scaling](https://docs.vllm.ai/en/v0.31.0/serving/parallelism_scaling/) - для ограничений памяти и распределения работы. Запишите weights, KV cache, длину context, concurrency и GPU topology, чтобы понять причину выбранного ограничения.
- [Metrics](https://docs.vllm.ai/en/v0.31.0/usage/metrics/) - для выбора наблюдений и единиц. Сопоставьте серверные сигналы с задержкой клиента: TTFT показывает время до первого token, ITL - интервалы последующей генерации. Границу измерения указывайте явно, особенно если между клиентом и backend есть gateway и очередь.
- [vLLM releases](https://github.com/vllm-project/vllm/releases) - для поиска изменений перед обновлением. Выделите затронутые flags, модельные пути и ограничения, затем повторите нужные capacity и quality cases на candidate.

Выбор между увеличением памяти, сокращением context и снижением concurrency зависит от задачи. Для большого документа сокращение context может убрать нужные сведения; для интерактивного помощника длинная очередь может нарушить допустимое ожидание. Сравните варианты при одинаковых входах и отдельно оцените качество результата. Рабочий итог - профиль с объяснимой ценой в памяти, задержке и качестве.

Если нужен другой backend, начните с [llama.cpp server README](https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md). Это moving-ссылка: перед опытом закрепите commit/build, hash GGUF, quantization, template и flags. Релизный ориентир книги `v0.4.1` и rolling build - разные способы обозначить поставку. Выбирайте конкретную сборку по поддержке модели, доступному hardware и требуемым операциям API, затем испытывайте тот же workload.

Статьи [GQA](https://arxiv.org/abs/2305.13245) и [PagedAttention](https://arxiv.org/abs/2309.06180) полезны для понимания attention и управления KV memory. Их роль - объяснить механизм и устройство опубликованного опыта. Флаги своей сборки ищите в её руководстве, а sizing и выигрыш устанавливайте измерением. В главе 41 этот переход от механизма к бюджету разобран подробнее.

## C.7 Эксплуатация и проверка полезного восстановления

Для эксплуатации источник выбирают по объекту ответственности. Если после restore перестал работать credential, проверьте его issuer и правила отзыва. Если database поднялась, но агент не может продолжить задачу, выясните, каких metadata, payload или ключей ему не хватает. Одно событие «service started» не описывает весь путь возврата полезной работы.

В [Vault leases](https://developer.hashicorp.com/vault/docs/concepts/lease) найдите срок жизни, renewal и revocation dynamic credential. Затем проследите, что соответствующий secrets engine делает у provider. Для значения, сохранённого в KV, нужны правила ротации и отзыва самого конечного credential. Сохранение старого значения в snapshot и доступ к secret store после restore - разные части этой задачи.

Для PostgreSQL начните с [continuous archiving и PITR в manual 17](https://www.postgresql.org/docs/17/continuous-archiving.html). PITR восстанавливает данные к выбранному моменту с помощью backup и журнала изменений; требуемая цепочка должна быть доступна и пригодна для вашей major version. Номер 17 обозначает версию этого руководства, а не установленную database книги. Для Redis откройте [RDB/AOF persistence](https://redis.io/docs/latest/operate/oss_and_stack/management/persistence/) и сопоставьте описанные свойства со своей версией и фактическими settings.

Дальше соедините store recovery с агентным workflow. У восстановленной Task должны быть нужные Workspace payloads, ключи и checkpoints, а у незавершённого write - способ установить внешний outcome. Выберите прикладной контрольный результат, который можно получить после restore. Потерю progress и время до этого результата считают раздельно; пример RPO/RTO находится в B.5 и B.8. Успешный запуск database закрывает только её часть восстановления.

[Effective Troubleshooting](https://sre.google/sre-book/effective-troubleshooting/) помогает строить проверяемые гипотезы. Например, из предположения об истёкшем credential следует конкретная проверка issuer/expiry и разрешённый контрольный запрос. [Canarying Releases](https://sre.google/workbook/canarying-releases/) помогает выбрать comparison, ограничить экспозицию и определить stop conditions. [Reliable Product Launches](https://sre.google/sre-book/reliable-product-launches/) - подготовить выпуск и передачу ответственности. Эти методы команда связывает со своим workload, SLO и доступными наблюдениями.

Из чтения должен получиться шаг runbook: какое состояние проверить, какой результат ожидается и кому передать открытый вопрос. Для доступа к storage это может быть проверка нужного principal на synthetic object. Для восстановления - заранее разрешённый drill в отдельной lab области. Область такого опыта определяется отдельно от доступа на чтение документации.

## C.8 Сквозной разбор зависимости Actor от Tag

### Поставить вопрос о конкретной операции

Вернёмся к очистке storage. Рассматриваем Substrate `v0.3.0`, новый Actor, созданный с явно заданным `sourceTag`, до его первого собственного успешного suspend. Вопрос: появился ли независимый snapshot payload сразу при CreateActor? От ответа зависит, можно ли считать исходный Tag лишней копией.

Для разбора нужен Git, POSIX shell, доступ к публичному репозиторию и место для исходников. Мы создадим отдельный временный bare repository - хранилище объектов Git без рабочего checkout. Загрузим один закреплённый commit и прочитаем файлы. Команды не запускают upstream-код, не подключаются к cluster API и не удаляют Tag или payload.

```
sources_dir=$(mktemp -d)
git init --bare "$sources_dir/substrate.git"
sources_rev=ccecc788a327dc11dcd6c21ee153f3d0cbb5cc97
git -C "$sources_dir/substrate.git" fetch --depth=1 \
  https://github.com/agent-substrate/substrate.git "$sources_rev"
git -C "$sources_dir/substrate.git" rev-parse 'FETCH_HEAD^{commit}'
```

Последняя команда должна напечатать полный SHA из `sources_rev`. Так вы проверяете выбранный объект. Это ещё не проверка происхождения установленного image. Загрузка с `--depth=1` экономит историю; для поиска старого изменения потребуется отдельно получить нужные commits. Если нет Git, те же закреплённые файлы можно открыть по ссылкам из C.3 в браузере.

### Найти поле и пройти путь записи результата

```
git -C "$sources_dir/substrate.git" grep -n \
  -e source_tag -e sourceTag "$sources_rev" -- \
  pkg/proto/ateapipb/ateapi.proto docs/api-guide.md \
  cmd/ateapi/internal/controlapi/actor.go
git -C "$sources_dir/substrate.git" show \
  "${sources_rev}:cmd/ateapi/internal/controlapi/actor.go"
git -C "$sources_dir/substrate.git" show \
  "${sources_rev}:cmd/ateapi/internal/controlapi/workflow_tag.go"
```

В schema найдите `Actor.source_tag`, в API guide - описание `CreateActor`. Далее читайте `ServiceImpl.CreateActor` в `actor.go`: после разрешения source Tag код заполняет `outActor.Status.ExternalSnapshot` через `proto.CloneOf` snapshot-записи Tag. Важен тип аргумента. Здесь clone создаёт копию protobuf message с адресом и metadata, а сами файлы по этому адресу остаются прежними. Слово `CloneOf` без чтения аргумента могло бы увести к противоположному выводу.

Откройте `workflow_tag.go` и найдите `ensureTagSnapshotCopied`. В нём есть отдельный вызов `objectstore.CopyPrefix` из source prefix в prefix Tag. В `ensureTagFinalized` результат публикуется в `status.snapshot`. Это другой переход: создание Tag из external snapshot suspended Actor действительно проходит путь копирования payload.

Для решения об очистке нужен ещё `DeleteTag`. Он вызывает `ensureTagSnapshotReleased`, где используется `DeletePrefix` для tag-owned snapshot, а затем удаляется запись Tag. Так три участка объясняют одну зависимость: новый Actor получил адрес данных Tag; отдельная операция умеет создавать tag-owned копию; удаление владельца освобождает его данные. Отсутствие `CopyPrefix` в одном файле само по себе было бы слабым аргументом. Здесь вывод опирается на присваивание конкретного URI, его владельца и связанный cleanup path.

Теперь полезно сверить собственное чтение с комментарием рядом с присваиванием. Он описывает borrowing до первого собственного suspend. Комментарий помог бы найти нужный механизм, но основание вывода уже видно в операциях с metadata и storage. Если helper непонятен, продолжайте по его определению; останавливаться на названии функции рано.

### Сформулировать решение с правильной областью действия

Новый Actor record не создаёт независимых bytes в рассмотренном пути. Исходный Tag остаётся зависимостью его snapshot. Поэтому очистку нельзя разрешить только по числу созданных Actors или факту их запуска. Нужен inventory ссылок на payload, включая другие borrowers и golden dependencies. Условия перехода на собственную завершённую точку разобраны в главе 39.

Учебная карточка фиксирует этот узкий результат. Имена полей ниже принадлежат формату отчёта команды, а не API Substrate. Полный commit и пути заданы в командах выше; в рабочую запись перенесите также immutable ссылки и дату:

```
question: independent payload immediately after CreateActor
source_revision: ccecc788a327dc11dcd6c21ee153f3d0cbb5cc97
case: explicit sourceTag before first own suspend
symbols:
  - ServiceImpl.CreateActor
  - ActorWorkflow.ensureTagSnapshotCopied
  - ActorWorkflow.ensureTagSnapshotReleased
finding: actor initially borrows tag snapshot URI
source_claim_independent_copy: REFUTED
deployed_image_mapping: UNKNOWN
restore_test: NOT_RUN
decision: keep source tag pending dependency review
next_check: inventory payload references for selected build
```

`REFUTED` относится к утверждению о немедленной независимой копии в прочитанном срезе. Для действующей установки сначала установите соответствие сборки, затем её текущие references. Проверка восстановления потребует отдельного разрешённого lab опыта с synthetic данными и прикладным результатом. Поиск по репозиторию не выполняет такой опыт.

### Разобрать расхождение и закончить поиск

Если другое руководство говорит о копировании, уточните операцию: оно может описывать CreateTag, а не CreateActor через sourceTag. Затем сравните версии и условия ветки. Для изменения между версиями используйте diff нужных файлов и прочитайте затронутые helpers. Упоминание clone в рекламном обзоре имеет меньшую точность, чем описание конкретного API path.

Бывает, что причина всё же в ошибке документации или реализации. Сохраните обе ссылки и минимальный сценарий, на котором они расходятся. Если deployment содержит локальный patch или его происхождение неизвестно, назначьте получение inventory отдельным шагом. Это позволяет закончить текущий разбор: вопрос по исходникам получил ответ, опасная очистка остановлена, следующая проверка определена.

## C.9 Самостоятельная работа и обновление карты

Выберите решение из своей задачи: подобрать клиент MCP, изменить лимит context или разрешить recovery. Сформулируйте один вопрос с наблюдаемым следствием и найдите маршрут в C.2-C.7. Выполните следующие действия:

1. Запишите рассматриваемую версию, входы и существенные условия. Назовите другой профиль книги, с которым её легко перепутать.
2. Выберите объясняющий источник и точный контракт. Покажите, какие части вопроса каждый из них закрывает.
3. Найдите реализацию или тест нужной ветки. Для теста укажите, что исполняется реально и где стоят mocks; для кода - проверку условия и запись результата.
4. Составьте карточку: вывод, разрешённое сейчас действие, оставшийся вопрос и способ его проверить. Если нужен опыт, задайте ожидаемый прикладной результат и допустимую область воздействия.
5. Передайте запись коллеге. Он должен открыть те же объекты и объяснить, почему принято это решение, без угадывания версии и значения слова «работает».

При обновлении начинайте с сохранённых предпосылок решения. Смена API, snapshot format или auth policy направляет повторное чтение и тесты на соответствующие пути. Изменение ссылки на сайте сначала требует найти тот же документ; оно само по себе не меняет runtime. Обновляйте source record и результаты проверки раздельно, чтобы было видно, откуда появился новый вывод.

В итоге у инженера остаётся понятная цепочка: вопрос, источник нужной версии, объяснение механизма и проверяемое действие. Приложение A помогает провести аудит, B - принять ограниченное решение о допуске, C - найти основание для следующего шага. Этой цепочки должно хватать другому человеку, чтобы продолжить работу, не повторяя весь ваш поиск.
