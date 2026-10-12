# Приложение C. Карта источников

Представьте знакомую ситуацию: пример из README запускается, но параметр из книги сервер не принимает. В другом окне документация обещает функцию, которой нет в вашем image. Прежде чем менять конфигурацию, стоит проверить, об одной ли реализации говорят эти три источника. Часто проблема не в понимании инженера, а в незаметно смешанных версиях.

Это приложение помогает найти первоисточник под конкретный вопрос. Здесь собраны основные маршруты чтения, а не полная библиография каждой главы: локальные ссылки рядом с разбором остаются важны. Начинайте с задачи - проверить API, выбрать sandbox, рассчитать inference или подготовить восстановление - и переходите к соответствующему разделу карты.

## C.1 Как читать карту и фиксировать версию

У первоисточников разные роли. Guide объясняет намерение и порядок настройки; schema задаёт поля и типы; код показывает конкретную ветку исполнения; тест иллюстрирует проверенный сценарий. Roadmap описывает планы, issue - обсуждение или наблюдение в чужой среде. Ни один из этих документов сам по себе не подтверждает, что нужный контракт работает в вашем deployment.

Поэтому полезный маршрут выглядит так: **объяснение → контракт → реализация → собственная проверка**. Для простого ознакомления достаточно первых двух шагов. Для решения о допуске к production нужны также сведения о фактической сборке, конфигурации и результаты испытания из приложения B. Если источники расходятся, сохраните расхождение как открытый вопрос, а не выбирайте удобную формулировку.

| Область книги | Опорный срез | Как использовать |
| --- | --- | --- |
| AX: релизный контекст и поздний аудит | `v0.3.1`: `e70162a`; главы 38-43: `ac233282` | Это разные срезы. Ссылки AX ниже фиксируют код позднего аудита, а не заменяют его релизным tag. |
| Substrate: поздний аудит и lab | `v0.3.0`: `ccecc788`; development quickstart главы 26: `df788825` | API-карта ниже относится к v0.3.0. Установка из другой ветки требует своего compatibility record. |
| MCP, глава 36 | Revision `2026-07-28`; отдельно legacy `2025-11-25` | Не объединять сообщения и lifecycle двух revisions в один протокол. |
| Inference, глава 41 | Документация vLLM `v0.31.0` | Параметры сопоставлять с установленным server, model profile и hardware. |

Короткие SHA здесь служат подписью; ссылки на исходники содержат полные commit IDs. Полный SHA фиксирует объект Git, тогда как `main`, `master` и `latest` могут измениться. Tag удобен для навигации, но для воспроизведения сохраните и разрешённый commit, и digest реально запущенного image. Версия документации не устанавливает версию вашего бинарника автоматически.

Ссылки карты и выбранные опорные разделы проверены **12.10.2026**. Это не аудит всей внешней документации и не испытание runtime. Для Kubernetes, gVisor, lab tooling и других меняющихся руководств ниже указан маршрут поиска, а не единая новая baseline книги. После обновления сверяйте их с release notes вашей версии и повторяйте затронутые проверки.

## C.2 AX: от понятий к фактическому исполнению

Начинайте с роли AX в общей системе, затем проверяйте интересующий ресурс. Само слово «оркестратор» не отвечает, кто запускает child process, кто хранит progress и кто запрещает tool write. Эти границы видны в разных частях репозитория.

| Инженерный вопрос | Куда идти в срезе ac233282 | Что уточнить после чтения |
| --- | --- | --- |
| Что такое Task, Workspace и Model? | [README](https://github.com/google/ax/blob/ac2332829f22360ff97b0ba34d94dd0dd782f17e/README.md) и [concepts](https://github.com/google/ax/blob/ac2332829f22360ff97b0ba34d94dd0dd782f17e/docs/concepts.md) | Какие сущности принадлежат AX, какие - Substrate, какие состояния остаются внутри вашего harness. |
| Принимает ли API нужное поле? | [Manifest guide](https://github.com/google/ax/blob/ac2332829f22360ff97b0ba34d94dd0dd782f17e/docs/manifests.md) и [ax.proto](https://github.com/google/ax/blob/ac2332829f22360ff97b0ba34d94dd0dd782f17e/pkg/apis/v1alpha1/ax.proto) | Тип, допустимое значение и обработку поля сервером. Желаемый ресурс из архитектурной схемы не становится API только от наличия на рисунке. |
| Кто выполняет reconciliation? | [ax-server entry point](https://github.com/google/ax/blob/ac2332829f22360ff97b0ba34d94dd0dd782f17e/cmd/ax-server/main.go) и [server implementation](https://github.com/google/ax/blob/ac2332829f22360ff97b0ba34d94dd0dd782f17e/internal/server/server.go) | Direct reconciliation этого среза; не переносить на него старую модель отдельного controller и Redis queue. |
| Что означает запуск и готовность runner? | [Runner guide](https://github.com/google/ax/blob/ac2332829f22360ff97b0ba34d94dd0dd782f17e/docs/runner.md) и [runner.go](https://github.com/google/ax/blob/ac2332829f22360ff97b0ba34d94dd0dd782f17e/runner/runner.go) | Setup, запуск дочернего процесса и поведение после его ошибки. Ready endpoint не заменяет результат задачи. |
| Что реализовано, а что задумано? | [DESIGN](https://github.com/google/ax/blob/ac2332829f22360ff97b0ba34d94dd0dd782f17e/DESIGN.md), [roadmap](https://github.com/google/ax/blob/ac2332829f22360ff97b0ba34d94dd0dd782f17e/docs/roadmap.md), затем код | Будущие budgets, approvals или telemetry не считать готовыми механизмами контроля без реализации и проверки. |

Для релизного сравнения откройте [дерево v0.3.1](https://github.com/google/ax/tree/e70162a34037c221fe6fadefd98308c05a4ad8f3) и соответствующий diff. [Issues AX](https://github.com/google/ax/issues) помогают найти известное ограничение, но дата закрытия issue не доказывает наличие исправления в вашем image. Harness - прикладная управляющая обвязка вокруг model/tool loop - требует отдельного контракта. Его checkpoints, approvals и operation ledger нельзя вывести только из описания AX runner.

## C.3 Substrate: lifecycle, доступ и сохранность состояния

Вопрос «Actor восстановился?» слишком широк. Иногда нужно узнать, продолжилось ли исполнение, иногда - кому принадлежит snapshot, а иногда - можно ли безопасно удалить исходный Tag. Для каждого случая нужен свой маршрут. Ниже все ссылки на код и guide фиксируют `ccecc788`, то есть Substrate v0.3.0.

| Задача | Первичные источники | Граница вывода |
| --- | --- | --- |
| Разделить Actor, Worker и pool capacity | [Architecture](https://github.com/agent-substrate/substrate/blob/ccecc788a327dc11dcd6c21ee153f3d0cbb5cc97/docs/architecture.md), [API guide](https://github.com/agent-substrate/substrate/blob/ccecc788a327dc11dcd6c21ee153f3d0cbb5cc97/docs/api-guide.md), [ateapi.proto](https://github.com/agent-substrate/substrate/blob/ccecc788a327dc11dcd6c21ee153f3d0cbb5cc97/pkg/proto/ateapipb/ateapi.proto) | Actor не равен выделенному Pod. Архитектурная цель плотности не задаёт вашу измеренную capacity. |
| Проверить создание Tag и restore dependency | [Tag workflow](https://github.com/agent-substrate/substrate/blob/ccecc788a327dc11dcd6c21ee153f3d0cbb5cc97/cmd/ateapi/internal/controlapi/workflow_tag.go) и [Actor creation](https://github.com/agent-substrate/substrate/blob/ccecc788a327dc11dcd6c21ee153f3d0cbb5cc97/cmd/ateapi/internal/controlapi/actor.go) | Копирование внешнего payload при CreateTag и заимствование URI через sourceTag - разные действия. Проверить ownership и retention до удаления источника. |
| Отделить authentication от authorization | [Authentication](https://github.com/agent-substrate/substrate/blob/ccecc788a327dc11dcd6c21ee153f3d0cbb5cc97/docs/authentication.md) и [Threat model](https://github.com/agent-substrate/substrate/blob/ccecc788a327dc11dcd6c21ee153f3d0cbb5cc97/docs/threat-model.md) | Доверенный JWT не доказывает fine-grained RBAC. Уточнить права конкретного provider и внешние tenant boundaries. |
| Ограничить исходящий доступ Actor | [Network egress](https://github.com/agent-substrate/substrate/blob/ccecc788a327dc11dcd6c21ee153f3d0cbb5cc97/docs/network-egress.md) и [Credential injection](https://github.com/agent-substrate/substrate/blob/ccecc788a327dc11dcd6c21ee153f3d0cbb5cc97/docs/egress-credential-injection.md) | Сетевой маршрут, доверие TLS и выдача credential проверяются отдельно; разрешённый host не означает разрешённый business write. |
| Найти события после relocation | [Observability](https://github.com/agent-substrate/substrate/blob/ccecc788a327dc11dcd6c21ee153f3d0cbb5cc97/docs/observability.md) и [Metrics и blind spots](https://github.com/agent-substrate/substrate/blob/ccecc788a327dc11dcd6c21ee153f3d0cbb5cc97/docs/metrics/substrate.yaml) | Attribution текущего workload не заменяет центральное хранение истории. Отсутствующий сигнал надо компенсировать probe, а не трактовать как отсутствие отказа. |

Development quickstart главы 26 закреплён в [отдельном README-срезе df788825](https://github.com/agent-substrate/substrate/blob/df788825e6fc13dd7aa5fd283c8f14bd4c818247/README.md). Это инструкция для своего контекста установки, не доказательство совместимости с API v0.3.0. При выборе новой версии используйте [Substrate releases](https://github.com/agent-substrate/substrate/releases), сравнение schema и ваш restore test. Книга сохраняет исторический разбор, а не обещает перенос snapshot между любыми сборками.

## C.4 MCP: начинать с revision, а не со знакомого SDK

MCP стандартизует взаимодействие клиента с сервером tools и других возможностей. Он не решает за инженера, кому разрешено выполнять действие, как долго ждать approval и как установить результат потерянного write. Не путайте protocol contract с policy и durable state вашего harness.

| Что выясняете | Источник | На что обратить внимание |
| --- | --- | --- |
| Базовая модель revision 2026 | [Base protocol 2026-07-28](https://modelcontextprotocol.io/specification/2026-07-28/basic/index) | Самодостаточность сообщений, metadata и применимая schema; не подставлять lifecycle прежней revision. |
| Как узнать возможности и вызвать tool | [Discover](https://modelcontextprotocol.io/specification/2026-07-28/server/discover) и [Tools](https://modelcontextprotocol.io/specification/2026-07-28/server/tools) | Discovery, input/output schemas, ошибки и смысл annotations. Описание tool не заменяет проверку разрешений. |
| Как передавать сообщения по HTTP | [Streamable HTTP](https://modelcontextprotocol.io/specification/2026-07-28/basic/transports/streamable-http) | Transport requirements, authentication context и поведение соединения. Network retry ещё не означает безопасный повтор mutation. |
| Почему старый клиент работает иначе | [Legacy lifecycle 2025-11-25](https://modelcontextprotocol.io/specification/2025-11-25/basic/lifecycle) и [объяснение изменений 2026](https://blog.modelcontextprotocol.io/posts/2026-07-28/) | Legacy lifecycle читать в его версии; release article даёт контекст, но нормативные требования искать в specification. |

В integration record сохраняйте revision протокола, версии client/server SDK, transport и реально поддерживаемые capabilities. Одинаковое название библиотеки недостаточно. Проверяйте совместимость на разрешённом fixture: invalid input, denied action, timeout и корректное отображение результата. Бизнес-гарантию отсутствия второго effect подтверждает adapter/provider contract и тест H4, а не сам MCP.

## C.5 Kubernetes и sandbox: искать механизм нужной границы

[Kubernetes Concepts](https://kubernetes.io/docs/concepts/) удобно использовать как оглавление. Дальше выбирайте механизм по задаче. Если нужно понять, почему контейнер не принимает traffic, начните с probes; если нужно запретить выход к чужому service, начните с сетевой политики и её реализации в кластере. Это разные проблемы, хотя обе могут выглядеть как failed request.

| Решение | Руководство | Что проверить в своей среде |
| --- | --- | --- |
| Разделить startup, liveness и readiness | [Pod probes](https://kubernetes.io/docs/concepts/workloads/pods/probes/) | Какой endpoint измеряет probe и что делает платформа при failure. Application outcome проверяется отдельно. |
| Ограничить network reachability | [NetworkPolicy](https://kubernetes.io/docs/concepts/services-networking/network-policies/) | Поддержку enforcement вашим network plugin, selectors и allowed control. Объект policy без применения не защищает traffic. |
| Хранить credentials и описать ресурсы | [Secrets](https://kubernetes.io/docs/concepts/configuration/secret/) и [Custom resources](https://kubernetes.io/docs/concepts/extend-kubernetes/api-extension/custom-resources/) | Доступ и защиту Secret; где находится фактический API. Kubernetes CRD-механизм не делает AX Task Kubernetes CRD автоматически. |
| Выбрать границу изоляции | [gVisor architecture и security](https://gvisor.dev/docs/architecture_guide/intro/) | Threat assumptions, совместимость workload и residual risks. Sandbox не даёт прикладную авторизацию на tool account. |
| Подобрать учебный кластер | [Kind quickstart](https://kind.sigs.k8s.io/docs/user/quick-start/), [K3s architecture](https://docs.k3s.io/architecture), [kubeadm cluster guide](https://kubernetes.io/docs/setup/production-environment/tools/kubeadm/create-cluster-kubeadm/) | Цель lab, доступные ресурсы, topology и поддерживаемые версии; удобство установки не доказывает production readiness. |

Для nested lab из главы 26 дополнительно проверьте [Proxmox Nested Virtualization](https://pve.proxmox.com/wiki/Nested_Virtualization) и доступность нужных CPU extensions внутри VM. Один успешно созданный кластер не подтверждает работу выбранного sandbox backend. В отчёте разделяйте установленный компонент и реально испытанный путь исполнения.

## C.6 Inference: от механизма к измеренному профилю

Inference-документация отвечает на два разных вопроса: «как настроить server?» и «почему меняются память и latency?». Первое требует совпадения версии, второе - понимания механизма и измерения своего workload. Не переносите benchmark чужой модели на свою только потому, что backend называется так же.

- **API и startup configuration.** Для профиля главы 41 используйте vLLM v0.31.0: [OpenAI-compatible server](https://docs.vllm.ai/en/v0.31.0/serving/online_serving/openai_compatible_server/) и [engine arguments](https://docs.vllm.ai/en/v0.31.0/configuration/engine_args/). Проверяйте поддерживаемые endpoints и flags. Совместимый API не обещает идентичный tokenizer, chat template или все semantics другого provider.
- **Memory и parallelism.** Начните с [conserving memory](https://docs.vllm.ai/en/v0.31.0/configuration/conserving_memory/) и [parallelism and scaling](https://docs.vllm.ai/en/v0.31.0/serving/parallelism_scaling/). Запишите weights, KV cache, context, concurrency и GPU topology; затем измерьте TTFT, ITL и ошибки при выбранном профиле. Инструкция объясняет knobs, но не выбирает за вас допустимую деградацию качества.
- **Наблюдение и переход на другую сборку.** [Metrics v0.31.0](https://docs.vllm.ai/en/v0.31.0/usage/metrics/) задают доступные сигналы. [vLLM releases](https://github.com/vllm-project/vllm/releases) помогают искать изменения перед обновлением; повторный capacity/eval прогон всё равно нужен.
- **Альтернативный server.** [llama.cpp server README](https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md) - меняющийся навигационный источник. Перед воспроизведением сохраните commit/build, GGUF model, quantization, template и runtime flags. Эта ссылка не вводит новую фиксированную версию llama.cpp для всей книги.
- **Основания механизма.** Статьи [GQA](https://arxiv.org/abs/2305.13245) и [PagedAttention](https://arxiv.org/abs/2309.06180) помогают понять устройство attention и управление KV memory. Они не являются справочником сегодняшних server flags и не гарантируют опубликованный выигрыш на вашей конфигурации.

Хороший результат чтения - выбранный профиль и объяснение trade-off: например, почему конкретные context/concurrency помещаются в вашу память и какую задержку вы готовы принять. Просто перечень поддерживаемых optimizations ещё не является engineering decision.

## C.7 Эксплуатация: документация механизма не заменяет runbook

Для секретов начните с [Vault leases](https://developer.hashicorp.com/vault/docs/concepts/lease): отделите срок жизни и revocation dynamic credential от хранения значения в KV. Наличие secret store не означает, что старый credential перестал работать у конечного provider. В source record отметьте способ выдачи, renewal, отзыв и проверку после restore.

Для PostgreSQL полезен раздел [continuous archiving и PITR, manual 17](https://www.postgresql.org/docs/17/continuous-archiving.html); для Redis - [RDB/AOF persistence](https://redis.io/docs/latest/operate/oss_and_stack/management/persistence/). Номер PostgreSQL здесь обозначает версию руководства, а не утверждает, что deployment книги использует PostgreSQL 17. Выбирайте manual установленной major version. Redis-ссылка меняющаяся; сохраняйте версию server и фактические persistence settings.

Эти руководства объясняют восстановление stores, но не описывают полный recovery contract вашей агентной системы. Добавьте metadata, payloads, keys, checkpoints и external effects. Проверка RPO/RTO из B.5 относится к полезному восстановлению всего выбранного workflow, а не только к успешному старту database.

[Google SRE: Effective Troubleshooting](https://sre.google/sre-book/effective-troubleshooting/) помогает строить и проверять гипотезы; [Canarying Releases](https://sre.google/workbook/canarying-releases/) - выбирать comparison и stop conditions; [Reliable Product Launches](https://sre.google/sre-book/reliable-product-launches/) - готовить выпуск и передачу ответственности. Это методы инженерной работы, не встроенные гарантии AX/Substrate. Привяжите их к своему SLO, владельцам и повторяемым tests, а не копируйте чужой checklist без контекста.

## C.8 Практикум: проверить одно утверждение по источникам

Практикум выполняется read-only: здесь не нужно разворачивать production, удалять snapshots или проводить fault injection. Выберите одно утверждение из книги, влияющее на решение, например: «использование sourceTag делает snapshot нового Actor независимым от исходного Tag». Важен не быстрый ответ, а возможность показать путь к нему.

1. **Сделайте вопрос точным.** Какой вызов, поле, тип snapshot и версия рассматриваются? Сформулируйте наблюдаемое следствие: безопасно ли удалить исходный payload, сохранив возможность restore?
2. **Зафиксируйте identity.** Возьмите commit из нужного раздела карты, полную ссылку на файл и интересующий symbol. Отдельно запишите неизвестную или отличающуюся версию deployed image; не приравнивайте её к прочитанному коду.
3. **Пройдите маршрут.** Найдите объяснение в guide, поле в schema и обработчик в реализации. Для примера сравните Actor creation с Tag workflow. Различайте копирование bytes и сохранение ссылки; проверьте, какие dependencies остаются.
4. **Составьте evidence card.** Запишите вопрос, версии, найденные ссылки/symbols, вывод, его ограничения и необходимый lab test. Не утверждайте, что restore испытан, если вы только прочитали исходник.
5. **Передайте другому инженеру.** Пусть он воспроизведёт ход чтения и объяснит, что разрешено решить сейчас, а что требует проверки deployment. Сохраните несогласие или недоступный источник как открытый вопрос.

> **Учебный вывод для Substrate v0.3.0.** Actor creation через sourceTag заимствует payload URI; CreateTag из внешнего источника имеет отдельный copy path. Поэтому независимость нельзя вывести только из появления нового Actor record. Проверка кода ограничена срезом ccecc788; безопасная deletion/restore policy требует учёта dependencies и отдельного lab test. Это пример source analysis, не результат выполненного restore.

Приложение A помогает обнаружить опасное допущение, B - превратить его в проверяемый критерий допуска, C - найти контракт и реализацию для проверки. Книга заканчивается, но инженерная работа продолжается: сохраняйте версии, ограничивайте выводы полученными evidence и возвращайтесь к затронутым решениям после изменений. Хороший источник нужен не для убедительной ссылки в отчёте, а чтобы следующий инженер мог понять и проверить ваше решение.
