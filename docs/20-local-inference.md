# Local inference как сервис: как выбрать runtime, модель и границу ответственности

![Local inference в AX: стабильный model endpoint скрывает runtime, модель и hardware](.gitbook/assets/diagrams/20-18.png)  
*Local inference в AX: стабильный model endpoint скрывает runtime, модель и hardware*

В предыдущей главе мы довели AX Task до состояния, в котором runner действительно готов принять работу. Следующий внешний dependency — модель. Именно здесь часто возникает опасная иллюзия: если файл с весами загрузился, а `/v1/chat/completions` вернул текст, значит локальный inference уже построен. На самом деле это только удачный smoke test.

Production inference — отдельный сервис со своей очередью, памятью, версиями модели, политикой доступа, SLO и отказами. Harness отправляет ему запросы, но не должен знать, на какой GPU лежат веса, сколько у сервиса replicas и каким engine выполняется decode. Для AX это стабильный **model endpoint**; за endpoint находится самостоятельная эксплуатационная система.

Эта глава отвечает на вопрос **что выбрать и почему**. Мы разберём границу между AX и inference, отличие vLLM от llama.cpp, роль CPU/GPU, состав memory budget, риск quantization и путь от ноутбука до shared production service. Метрики нагрузки подробно появятся в главе 21, а расчёт VRAM и parallelism — в главе 41.

## Что означает «локальный»

Local inference не обязательно означает «процесс на том же ноутбуке». Это inference, чьими model artifacts, runtime, hardware и политикой данных управляет ваша команда. Он может работать:

- на workstation инженера;
- на одном GPU-host в лаборатории;
- в отдельном Kubernetes pool;
- в изолированном on-premises кластере;
- в арендованной VM с GPU, если контроль deployment остаётся у вас.

Граница проходит не по длине сетевого кабеля, а по **операционной ответственности**. У внешнего API поставщик обновляет runtime, держит запас capacity и устраняет hardware failures. У local inference всё это становится вашей задачей.

Выбирать его разумно, когда важны data residency, работа без внешнего egress, контроль над model revision, адаптерами и sampling, предсказуемая стоимость при устойчивой нагрузке или очень малая network latency внутри площадки. Но локальный сервис не является автоматически более дешёвым, быстрым или приватным. Простаивающий GPU дорог; перегруженный GPU создаёт очередь; открытый endpoint в локальной сети не защищает данные; неудачная quantization может испортить tool calls незаметнее, чем обычные ответы.

Полезная проверка звучит так: **какую конкретную зависимость мы хотим взять под свой контроль и готовы ли мы эксплуатировать её сами?** Если ответа нет, решение «поставим модель локально» пока архитектурно не закончено.

## Где проходит граница AX

AX управляет жизненным циклом Task и Actor, workspace и runner. Inference server управляет загрузкой модели, tokenization, scheduling, KV cache и генерацией. Эти циклы не следует смешивать.

Обычно model server живёт дольше отдельного Task и обслуживает несколько Tasks. AX передаёт runner стабильный URL, identity/credential и логическое имя модели. Reverse proxy или model gateway выбирает backend, проверяет доступ, ограничивает запрос и добавляет correlation ID. За ним runtime распределяет запросы по очереди и hardware.

Такое разделение даёт три свойства:

1. **Task не привязан к физическому GPU.** Host можно заменить, не пересоздавая agent workspace.
2. **Готовность измеряется по слоям.** `Task Ready` не доказывает, что модель загружена; health model server не доказывает, что она корректно вызывает tools.
3. **Отказы изолированы.** Перезапуск inference replica не обязан останавливать Actor, а удаление Task не выгружает общую модель.

Запускать model server внутри каждого Task допустимо для изолированного эксперимента, но обычно это плохой default: веса загружаются повторно, GPU capacity дробится, cold start растёт, а модель обновляется вместе с пользовательской средой. Per-Task server имеет смысл только при жёсткой tenant isolation, уникальной модели/adapter или исследовании, где воспроизводимость важнее utilization.

## Сначала контракт, потом engine

Команда часто начинает с вопроса «vLLM или llama.cpp?», хотя раньше нужно определить контракт workload:

- какие модели и точные revisions нужны;
- какой context реально приходит, а не только объявлен в model card;
- нужны ли tools, parallel tool calls, JSON Schema, vision, embeddings или reranking;
- допустимы ли streaming и cancellation;
- сколько запросов приходит одновременно и насколько они bursty;
- какие TTFT, inter-token latency и availability приемлемы;
- можно ли отправлять prompt во внешний API;
- что происходит при перегрузке: очередь, отказ, fallback или деградация на меньшую модель.

Только после этого сравнивают runtime и hardware. Иначе команда оптимизирует удобный benchmark, а не свой агентный сценарий.

## vLLM и llama.cpp — два разных центра тяжести

Оба проекта умеют предоставлять HTTP API, похожий на OpenAI API, выполнять quantized models и обслуживать несколько запросов. Но они оптимизированы под разные исходные ограничения.

| Если главный вопрос звучит так… | Первым кандидатом будет | Почему |
| --- | --- | --- |
| «Как получить высокий aggregate throughput на GPU и обслуживать общий pool запросов?» | **vLLM** | Scheduler, continuous batching, PagedAttention, prefix caching и широкий набор distributed/parallel режимов ориентированы на serving. |
| «Как запустить GGUF на CPU, Apple Silicon, небольшой GPU или гибриде CPU/GPU?» | **llama.cpp** | Лёгкий C/C++ runtime, большой выбор backends и удобное управление offload делают его сильным для constrained hardware. |
| «Нужен воспроизводимый lab endpoint без отдельного GPU-server?» | **llama.cpp** | Один model artifact и компактный server снижают порог первого запуска. |
| «Модель не помещается на одной GPU или нужен общий production pool?» | **vLLM**, если модель и hardware поддерживаются | Tensor/pipeline/data/expert parallelism и production metrics дают больше возможностей для масштабирования. |
| «Нужен один редкий формат модели или backend?» | **Проверка support matrix обоих** | Название runtime ничего не гарантирует: важна конкретная комбинация architecture, quantization, device и kernel. |

**vLLM** — не просто Python-обёртка над моделью. Его основная ценность — scheduler, который держит множество sequences, переиспользует блоки KV cache и формирует работу для accelerator. В актуальной на октябрь 2026 года линии `v0.31.0` проект предоставляет OpenAI-compatible endpoints, distributed serving, несколько видов parallelism, quantization, structured outputs и Prometheus metrics. Этот список быстро меняется, поэтому production deployment обязан pin'ить image digest и сверяться с документацией именно выбранной версии.

**llama.cpp** — runtime и набор инструментов вокруг формата GGUF. `llama-server` умеет CPU и GPU inference, partial offload, parallel decoding, continuous batching, embeddings, structured JSON и tool use. Его сильная сторона — не «медленный fallback», а широкий диапазон устройств и очень низкий порог управления. На подходящем GPU он тоже может быть быстрым; на CPU — давать полезный private endpoint там, где отдельный accelerator экономически не оправдан.

Это не единственные варианты. TensorRT-LLM, TGI, SGLang, Ollama и vendor-specific stacks могут оказаться правильнее. Но выбирать следует не по числу звёзд на GitHub, а по шести совпадениям: **model architecture, artifact format, hardware backend, API capabilities, SLO и компетенции команды**.

## OpenAI-compatible — это форма разъёма, а не одинаковое поведение

Совместимый endpoint очень полезен: harness может менять `base_url` и переиспользовать client library. Но одинаковые пути `/v1/...` не делают providers семантически идентичными.

Различаться могут:

- поддерживаемые поля запроса и error codes;
- chat template и способ размещения system/tool messages;
- token accounting и предел context;
- формат streaming chunks и cancellation;
- `response_format`, JSON Schema и grammar constraints;
- parallel tool calls, tool choice и сериализация arguments;
- reasoning fields, multimodal parts и usage metadata.

Особенно важен **chat template**. Одна и та же модель с неправильным template может выглядеть «глупее», повторять служебные токены или ломать tool calls. Template — часть deployment artifact наравне с weights и tokenizer, а не косметическая настройка клиента.

Поэтому model adapter в harness должен иметь capability tests. Минимальный набор проверяет обычный chat, streaming, отмену запроса, лимит context, один и несколько tool calls, валидный structured output, обработку tool result и ожидаемые ошибки. Smoke test «модель рассказала анекдот» для agent platform почти ничего не доказывает.

## Из чего на самом деле состоит deployment модели

Идентификатора вида `org/model-name` недостаточно для воспроизводимости. Deployment должен фиксировать:

- model repository и immutable revision/commit;
- weights и их format/quantization;
- tokenizer и chat template;
- runtime version и container image digest;
- engine arguments: context limit, dtype, parallelism, cache settings;
- model license и разрешённые способы использования;
- checksum или provenance скачанных artifacts;
- набор capability/evaluation tests;
- hardware, driver и accelerator runtime, на которых выполнена приёмка.

Это можно представить как release модели. Сначала artifacts попадают в контролируемое хранилище, затем проверяются license, hash и совместимость, после чего новая replica прогревает weights и проходит probes. Лишь затем gateway отправляет на неё canary traffic. Старую revision удаляют только после drain активных streams и возможности rollback.

Тег `latest` разрушает эту цепочку: сегодня за тем же именем могут оказаться другие weights, template или runtime. Для агента это особенно опасно — поведение изменится не только в тексте, но и в выборе tools и структуре side effects.

## Memory budget: почему «веса помещаются» недостаточно

Грубая оценка размера weights выглядит просто:

`параметры × битность / 8`

Например, 8 миллиардов параметров в 4-bit representation дают около 4 GB сырых данных. Но это не обещание, что модель запустится в 4 GB: format хранит scales и metadata, часть tensors может иметь другую precision, а runtime нужны дополнительные allocations.

Полный budget включает как минимум:

`weights + KV cache + runtime/workspace buffers + временные allocations + fragmentation/reserve`

**KV cache** хранит attention state активных sequences. Он растёт с фактическим context и concurrency, поэтому длинные agent histories могут исчерпать память, даже если weights занимают только половину VRAM. При нехватке cache runtime уменьшает concurrency, вытесняет blocks, recompute'ит данные или отклоняет запрос — конкретное поведение зависит от engine.

**Runtime buffers** зависят от kernels, graph capture, batch shape и backend. Свободный остаток в 200 MB — не capacity plan. Нужен резерв для пиков, инициализации и version drift.

Глава 41 даст более точный sizing. Здесь важен mental model: сначала измеряем распределение input/output tokens и одновременных sequences, потом выбираем context limit и cache, и лишь затем отвечаем, «помещается ли модель».

## Quantization — инженерный компромисс, а не кнопка «сделать меньше»

Quantization хранит weights, а иногда activations или KV cache, с меньшей precision. Это может уменьшить memory footprint и ускорить inference, если hardware имеет подходящие kernels. Но «4-bit» не является единым форматом и не гарантирует ускорения.

Результат зависит от:

- метода quantization и наличия calibration;
- того, какие слои оставлены в большей precision;
- поддержки конкретного format runtime'ом;
- accelerator generation и выбранного kernel;
- стоимости dequantization и размера batch;
- чувствительности модели и задачи к ошибке.

На CPU GGUF quantization часто является естественным способом уместить модель и увеличить скорость за счёт меньшего memory traffic. На GPU неподходящий format может выбрать менее эффективный kernel и проиграть FP16/BF16 или FP8, несмотря на меньшие weights.

Для agent workload evaluation обязана проверять не только «качество ответов». Отдельно измеряйте:

- выбор правильного tool;
- полноту и типы arguments;
- соблюдение JSON Schema;
- устойчивость на длинном context;
- частоту повторов и незавершённых tool loops;
- поведение на отказах tools и adversarial input.

Экономия VRAM не окупается, если модель на несколько процентов чаще создаёт неверный side effect или вынуждает harness повторять шаг.

## CPU, GPU и hybrid offload

**CPU** хорош для небольших моделей, низкой или прерывистой нагрузки, лаборатории и data-local environments без accelerator. Он также удобен как функциональный fallback: endpoint остаётся доступным, пусть SLO и ухудшается. Но нельзя без измерений обещать, что GPU workload «просто переедет на CPU» — timeout'ы и длина очереди могут изменить поведение всего agent loop.

**GPU/accelerator** нужен, когда важны высокая пропускная способность, короткий prefill на больших prompts или несколько одновременных generations. Здесь производительность часто ограничивается не только FLOPS, но и bandwidth, capacity memory и scheduler'ом.

**Hybrid offload** переносит часть layers на GPU, а оставшиеся держит на CPU. Это позволяет запустить модель, которая не помещается в VRAM, но создаёт transfer и synchronization cost. Частичный offload — разумный путь для workstation, однако не следует автоматически считать его production scaling strategy. Сначала измеряют реальный TTFT и decode speed на нужном context.

Несколько GPU тоже не являются «одной большой GPU». Tensor parallel делит вычисление слоя и требует communication между devices; pipeline parallel делит layers и добавляет pipeline bubbles; replicas увеличивают throughput независимых запросов, но каждая хранит полный набор weights. Если модель помещается на одном устройстве, несколько replicas часто проще и надёжнее сложного model parallelism. Подробно этот выбор разбирается в главе 41.

## Три практических topology

### 1. Инженерный ноутбук или lab VM

Одна quantized GGUF-модель, `llama-server`, loopback или закрытая lab network. Цель — проверить model/harness contract, tool schema и воспроизводимость, а не имитировать production throughput. Model revision, template и параметры запуска всё равно фиксируются.

### 2. Один GPU-host для команды

Одна или несколько vLLM/llama.cpp replicas находятся за reverse proxy. Gateway завершает TLS, проверяет service identity, задаёт request limits и correlation ID. Model server не публикуется напрямую наружу. Deployment имеет readiness probe, startup timeout для загрузки weights и отдельный metrics path.

### 3. Shared production pool

Gateway маршрутизирует логические имена моделей на versioned pools. Replicas проходят canary и drain; overload policy ограничивает очередь; scheduler и autoscaling опираются на model-specific metrics. Model artifacts берутся из контролируемого registry/cache, а не скачиваются из интернета каждым Pod при старте. Tasks видят стабильный endpoint и не знают topology pool.

Переход между topology должен менять infrastructure, но не contract harness. Если для миграции с ноутбука в кластер приходится переписывать agent loop, граница сервиса была выбрана плохо.

## Readiness, overload и отказ

Обычный TCP health check слишком слаб. Нужны как минимум разные сигналы:

- **process alive** — процесс не завершился;
- **model loaded** — weights и tokenizer готовы;
- **inference ready** — короткий контролируемый запрос проходит;
- **capability ready** — canary с tool/JSON contract даёт ожидаемую структуру;
- **service healthy** — очередь и error rate позволяют принимать новый traffic.

При старте large model загрузка weights может занимать минуты. Оркестратор не должен убивать Pod коротким liveness timeout, пока тот легитимно инициализируется. Напротив, readiness нельзя выставлять до завершения загрузки: иначе первые запросы попадут в холодный и ещё не готовый process.

Overload — штатное состояние, которое следует проектировать. Бесконечная очередь превращает локальную экономию в непредсказуемые минуты ожидания. Лучше иметь bounded queue и явный ответ `429/503` с retry policy. Gateway может направить часть traffic на другую replica, меньшую модель или внешний provider — но только если этот fallback прошёл те же capability tests.

Ретрай streaming request требует осторожности. Если клиент уже получил часть текста или agent успел выполнить tool, слепой повтор может дублировать действие. Harness должен отличать безопасный повтор model call от повторения application side effect и связывать попытки единым request/trace ID.

## Безопасность: модель тоже часть control boundary

Local endpoint часто считают доверенным просто потому, что он находится «в нашей сети». Для agent platform это опасно: через него проходят system prompts, retrieved documents, tool schemas и иногда секреты из workspace.

Минимальная архитектура включает:

- private network path и deny-by-default policy;
- service identity или gateway authentication, а не общий ключ во всех Tasks;
- отдельный доступ к inference, metrics, profiling и administrative endpoints;
- limits на body size, context, output tokens, concurrency и время запроса;
- проверку provenance model artifacts и pinning image digest;
- редактирование/отключение prompt logging, если там могут быть секреты;
- tenant-aware audit metadata без сохранения лишнего содержимого;
- egress policy для container, который не обязан ходить в интернет после загрузки artifacts.

OpenAI-compatible API key защищает только то, что действительно проверяет выбранный server. Не предполагайте, что тем же механизмом закрыты `/metrics`, profiling, model load/unload или debug routes. Это проверяют по версии runtime и закрывают на proxy/network layer.

## Как принимать решение

Рабочий порядок выглядит так:

1. Зафиксируйте use case и capability contract агента.
2. Выберите model revision по качеству на собственных задачах, а не по общей таблице лидеров.
3. Определите допустимость external API и требования к данным.
4. Соберите workload distribution: context, output, burst concurrency, tools.
5. Проверьте runtime support для модели, format, device и API capabilities.
6. Рассчитайте предварительный memory budget с KV/cache reserve.
7. Соберите минимальный endpoint и прогоните capability tests.
8. Проведите нагрузочный тест на целевом профиле, а не на одном коротком prompt.
9. Добавьте gateway, identity, observability, bounded queue и rollout/rollback.
10. Только после этого называйте endpoint production-ready.

### Короткая матрица выбора

| Сценарий | Разумный первый вариант | Что доказать до эксплуатации |
| --- | --- | --- |
| Personal/lab, CPU или mixed hardware | llama.cpp + pinned GGUF | Template, tool calls, context, реальная скорость, закрытый bind address |
| Shared single-GPU service | vLLM или llama.cpp за gateway | Memory headroom, burst queue, cancellation, structured output, restart time |
| Multi-GPU large model | vLLM и поддерживаемый parallel mode | Interconnect cost, failure domain, scaling benefit относительно меньшей модели |
| Строго изолированный tenant | Dedicated replica/pool | Artifact provenance, identity, network и logging isolation |
| Нерегулярная малая нагрузка | Сначала сравнить с managed API | Полную стоимость владения, cold start и on-call burden |

## Антипаттерны

- **Выбирать runtime до workload contract.** Получается быстрый server, который не умеет нужный tool protocol.
- **Считать OpenAI compatibility полной взаимозаменяемостью.** Несовпадение template или streaming ломает harness уже после smoke test.
- **Публиковать model server напрямую в общую сеть.** Gateway и network policy нужны даже «внутри».
- **Использовать `latest`.** Невозможно связать regression с конкретными weights или runtime.
- **Считать размер файла модели размером необходимой памяти.** KV cache и buffers появляются только во время работы.
- **Выбирать максимальный context «на всякий случай».** Он съедает cache capacity и ухудшает полезную concurrency.
- **Оценивать quantization только по красивому диалогу.** Tool calls и structured output могут деградировать отдельно.
- **Считать CPU fallback прозрачным.** Другой latency profile меняет timeout'ы и queueing всего агента.
- **Лечить overload бесконечной очередью.** Система остаётся формально доступной, но перестаёт выполнять SLO.
- **Смешивать rollout модели с rollout Task.** Эти жизненные циклы должны быть независимы.

## Итог

Local inference — это не «модель рядом с агентом», а отдельный сервисный контур. vLLM обычно начинает выигрывать там, где главным становится GPU serving и общий throughput; llama.cpp — там, где важны GGUF, CPU/hybrid execution, constrained hardware и простота первого deployment. Но финальный выбор определяется пересечением model, hardware, API contract, SLO и способности команды сопровождать систему.

Для AX правильная абстракция проста: Task получает стабильный model endpoint, а runtime, revision и physical placement остаются за этой границей. Внутри границы команда pin'ит artifacts, тестирует tool semantics, планирует memory, защищает endpoint и умеет обновлять его без разрушения agent state.

Теперь можно перейти от архитектуры к числам. В следующей главе мы измерим TTFT, inter-token latency, queue time, aggregate throughput и KV pressure для нагрузки из 10–15+ агентов — и увидим, почему количество агентов почти никогда не равно одновременной inference concurrency.

### Источники и дальнейшее чтение

- [vLLM: online serving и поддерживаемые API](https://docs.vllm.ai/en/latest/serving/openai_compatible_server.html)
- [vLLM: возможности engine и parallelism](https://docs.vllm.ai/en/latest/)
- [vLLM: production metrics](https://docs.vllm.ai/en/latest/usage/metrics/)
- [vLLM releases](https://github.com/vllm-project/vllm/releases)
- [llama.cpp](https://github.com/ggml-org/llama.cpp)
- [llama-server: API, batching, structured output и tool use](https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md)
