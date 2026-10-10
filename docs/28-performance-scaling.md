# Performance и scaling: как найти bottleneck и доказать capacity

**Снимок главы:** **AX main `ac233282` · Substrate main `df78882` · проверено 2026-10-10**. Числа из README, demo и issues ниже отделены от измерений вашей системы. Оба проекта pre-1.0, поэтому performance contract нужно перепроверять для каждой закреплённой версии.

После главы о HA возникает естественный соблазн: добавить replicas, включить autoscaling и считать задачу закрытой. Но производительность распределённой agent platform определяется не количеством Pods, а самой медленной стадией полезного пути. AX может быстро принять Task и медленно reconcile'ить его. WorkerPool может иметь свободные Pods, пока object store ограничивает одновременный restore. Model endpoint может отвечать быстро, но агент делает вдвое больше шагов из-за неудачного routing.

Цель performance engineering - не получить максимальное число в синтетическом тесте. Нужно доказать, какую полезную нагрузку система выдерживает при заданных SLO, качестве результата и сценарии отказа, где находится saturation knee и как система ведёт себя за ним.

## Сначала определите, что именно считается производительностью

У слова «быстро» нет инженерного смысла без outcome и границы измерения. Для этой платформы нужны как минимум пять разных понятий.

| Понятие | Что означает | Типичная ошибка |
| --- | --- | --- |
| Latency | Время одного результата: Task accepted, Actor ready, первый полезный ответ, завершённая задача | Смешать queue wait, resume и execution в один average |
| Throughput | Сколько операций система завершает за единицу времени | Считать все HTTP 200, включая бесполезные или повторные результаты |
| Goodput | Сколько корректных результатов выполнено в пределах SLO без запрещённых side effects | Оптимизировать requests/s при падении качества и росте retries |
| Capacity | Максимальная устойчивая нагрузка при заданном workload mix и SLO | Назвать capacity числом созданных Actors |
| Headroom | Резерв для burst, rollout и потери части инфраструктуры | Считать 100% utilization нормальным рабочим режимом |

Главная метрика agent platform - **goodput**. Быстро созданный Task, который застрял в readiness, выполнил tool дважды или не уложился в пользовательский deadline, не увеличивает полезную пропускную способность.

## Workload model: переведите «N агентов» в нагрузку

Число зарегистрированных агентов почти ничего не говорит о capacity. Один агент может ждать человека сутки, другой непрерывно компилировать проект, третий создавать десять child Tasks и одновременно обращаться к модели. До benchmark зафиксируйте распределения, а не один «типичный» пример.

| Ось | Что измерить | Почему влияет |
| --- | --- | --- |
| Arrival | tasks/s, wakeups/s, burst size, суточный профиль | Определяет queue и скорость scale-up |
| Duty cycle | доля времени Actor действительно занимает Worker | Определяет реальную ценность oversubscription |
| Session shape | число steps, tool/model calls, think gaps, fan-out | Один Task порождает много внутренних операций |
| State | RAM, writable data, dirty pages, snapshot p50/p95/max | Меняет suspend/resume time и storage traffic |
| Sandbox work | CPU, RAM peak, filesystem, network, file descriptors | Определяет Worker packing и noisy-neighbor risk |
| Dependency mix | model route, tool latency, egress destinations | Внешняя очередь может стать end-to-end bottleneck |
| Priority | interactive, background, maintenance | Классы требуют разных deadline и admission policy |

Для первичной оценки полезны две формулы. Если средняя скорость прихода задач равна `λtask`, то внутренняя нагрузка приблизительно равна:

`λoperation = λtask × E[operations per task]`

Operations здесь нужно считать раздельно: AX reconcile events, Actor wakeups, snapshot bytes, model calls и tool calls имеют разные service stations.

В устойчивом режиме закон Литтла даёт:

`среднее число работ в системе = arrival rate × среднее время в системе`

Если приходит 4 wakeups/s, а wake вместе с ожиданием занимает в среднем 1,5 s, одновременно в этом пути находится около 6 операций. Это не sizing formula для p99: она не учитывает burst, корреляцию и тяжёлый хвост. Но она быстро выявляет невозможные ожидания.

## Oversubscription работает только при правильном duty cycle

`logical actors / physical workers` - коэффициент oversubscription, а не коэффициент ускорения. Он экономит ресурсы, когда Actors большую часть времени suspended или idle вне Worker. Для постоянно активных coding jobs десятикратный oversubscription превращается в queue, parking wait или `ResourceExhausted`.

Грубая оценка при независимой активности выглядит так:

`expected active actors ≈ logical actors × active duty cycle`

Если 1000 Actors активны в среднем 2% времени, математическое ожидание равно 20. Это не означает, что достаточно ровно 20 slots. Пользовательские события, cron, coordinator fan-out и восстановление после outage делают wakeups коррелированными. Нужен запас, рассчитанный по наблюдаемому burst distribution, а не по среднему duty cycle.

Substrate README на выбранном снимке заявляет 10x density, sub-500ms resume и более 500 activations/s, а demo показывает около 250 Actors на 8 Pods. Это **PROJECT CLAIM и демонстрационный результат**, не capacity contract. Architecture document отдельно предупреждает, что значительная часть дизайна aspirational. Переносить эти числа на большой repository, другой sandbox, cold object cache и свой failure domain нельзя.

## Разложите end-to-end latency по критическому пути

Task latency - не сумма времени всех компонентов. Последовательные этапы складываются, а параллельная ветка добавляет максимум своего критического пути плюс coordination overhead:

`Ttask = Σ Tsequential + max(Tparallel branches) + Tcoordination`

Для первого запуска полезно измерять timestamps как отдельные milestones:

1. client send → AX accepted;
2. accepted → event visible controller-у;
3. reconcile start → Substrate Actor created/selected;
4. resume requested → Worker assigned;
5. snapshot lookup/download/restore;
6. runner process started → `/readyz` подтверждён;
7. первый tool/model call → первый полезный результат.

Так становится видно, что «Task создаётся 18 секунд» может означать 20 ms API, 15 s readiness polling и 3 s restore. Увеличение replicas API не изменит результат.

Не ограничивайтесь average. Coordinator, который ждёт завершения десяти children, чувствителен к самому медленному child. Даже умеренная вероятность tail event усиливается fan-out: чем больше параллельных ветвей требуется дождаться, тем чаще одна из них попадёт в p99.

## Карта bottlenecks и правильные scaling knobs

| Plane | Признак насыщения | Что измерять | Что масштабировать после доказательства |
| --- | --- | --- | --- |
| Ingress / atenet | parking wait и 503 растут, Workers освобождаются слишком медленно | parked requests, retry budget, route latency, deadline | warm capacity, admission, routing; не бесконечный parking |
| AX API/store | API latency, Redis lag/QPS или lock wait растут | request latency, stream backlog, store RTT, errors | API replicas и production store - только с проверенной concurrency semantics |
| AX controller | Task events ждут при свободных Workers | event age, reconcile duration, readiness wait, ack rate | controller concurrency/replicas, sharding и coalescing |
| Substrate control plane | lifecycle RPC и placement замедляются | RPC phase latency, DB latency, operation state, workqueue | API/controller/store capacity с сохранением ordering |
| WorkerPool | at-capacity workers, pending placement, CPU/RAM pressure | assigned/full/idle workers, resource headroom, OOM/throttle | replicas, nodes, pool segmentation, Actor right-sizing |
| Snapshot store | suspend/resume tail растёт при concurrency | bytes, IOPS, RTT, cache state, concurrent transfers, errors | bandwidth/IOPS, locality, snapshot policy; не только Workers |
| Tools/model | sandboxes idle, но Tasks не завершаются | queue, TTFT, tool latency, retries, rate limits | endpoint capacity, routing, caching, workload reduction |

Эффективная capacity цепочки приблизительно равна минимуму capacity её обязательных стадий. Scale-out одного plane может даже ухудшить систему: больше Workers одновременно начнут restore и перегрузят object store; больше controllers создадут давление на Redis/Substrate и увеличат lock contention.

## Текущая AX-граница: controller throughput нужно измерять отдельно

На снимке AX `ac233282` controller создаёт один `Worker` loop в process и выполняет цепочку Substrate RPC плюс readiness verification. Поэтому свободный WorkerPool не доказывает, что массовое создание Tasks будет быстрым.

Issue AX #384 описывает serial processing и приводит prototype benchmark concurrency 1/4/16/64. Автор честно отмечает, что Substrate был mock с искусственными задержками и что branch отличался размером read-ahead. Эти числа показывают возможное направление, но не являются измерением release или real cluster. Issue закрыт как duplicate #433, а не как доказательство конкретной production capacity.

Если увеличивать controller concurrency, нужно сохранить порядок событий одного Task, idempotency reconcile, bounded queues, корректный ACK и shutdown in-flight work. Иначе tasks/s вырастет ценой гонок и потерянных событий. Поэтому проверяйте одновременно throughput, correctness и recovery после kill controller-а.

## WorkerPool capacity: replicas недостаточно

Substrate API guide описывает WorkerPool как физическую warm capacity. Placement учитывает объявленные Actor resource limits и оставшийся budget Worker; отдельно действует actor limit процесса. Практически pool ограничивается первым исчерпанным ресурсом: CPU, RAM, actor slots, file descriptors, sandbox/runtime overhead или node capacity.

Документация на выбранном снимке требует внимательного чтения. API guide уже описывает shared budget и `--max-actors`, но autoscaled WorkerPool demo предупреждает: его сигнал «число полных workers» отражает utilization только пока Worker фактически держит одного Actor. Когда packing меняется, прежняя метрика перестаёт означать процент заполнения. Значит autoscaling policy должна быть привязана к semantics конкретной версии, а не к знакомому имени metric.

Разделяйте pools по workload class, если они конкурируют за разные ресурсы: interactive small Actors, memory-heavy coding workspaces, microVM и background jobs. Общий pool повышает среднюю utilization, но может создать head-of-line blocking: крупный Actor не помещается, хотя суммарно свободной памяти в кластере много.

## Snapshot path имеет собственную capacity

Нижняя оценка времени передачи snapshot проста:

`Ttransfer ≥ snapshot bytes / effective bandwidth`

Реальный resume добавляет metadata lookup, object-store RTT, decompression, sandbox restore, lazy page faults и readiness. При `k` одновременных restores каждый не получает полную номинальную полосу: общий network path, storage IOPS и CPU decompression становятся shared resources.

Тестируйте как минимум четыре состояния: golden/warm cache, Actor-owned warm cache, cold object cache и restore storm после node loss. Отдельно измеряйте первый полезный filesystem/RAM access после ответа Resume: lazy restore может сделать RPC быстрым, перенёсши задержку в первую команду пользователя.

## Autoscaling - запаздывающий control loop

CPU HPA отвечает на вопрос «насколько заняты уже запущенные Pods». Он может не увидеть ожидающие wakeups, исчерпанные Actor slots или pressure object store. Для queue-like workload полезнее backlog, age oldest work, parking wait, available slots или другой custom/external metric, который уменьшается при добавлении replicas.

В pinned Substrate demo HPA получает через Prometheus adapter число Workers в состоянии `at_capacity`. Target `AverageValue` задаёт желаемое число полных Workers на replica: например 0,7 оставляет около 30% idle headroom в assumptions demo. Scale-up там агрессивный, а scale-down имеет 300 s stabilization. Документ одновременно предупреждает, что demo поддержан только на kind и его signal зависит от текущей модели one-Actor-per-Worker.

Для interactive workload автоскейлер часто опаздывает: metric scrape, HPA sync, scheduling, image pull и Worker readiness длиннее пользовательского deadline. Поэтому production policy сочетает:

- минимальную warm reserve для ожидаемого burst;
- admission и priority, чтобы сохранить interactive SLO;
- предиктивное увеличение capacity для известных событий;
- реактивный autoscaling для устойчивого роста;
- медленный scale-down, чтобы не получить oscillation и snapshot storm.

## Benchmark protocol: от гипотезы к capacity envelope

1. **Зафиксируйте contract.** Workload mix, SLO, correctness checks, отказ и допустимый error rate.
2. **Закрепите среду.** Commit/digest AX, Substrate, sandbox assets, images, Kubernetes, node type, store topology и network path.
3. **Проверьте telemetry.** Все milestones и errors должны быть видны до нагрузки. Генератор тоже может стать bottleneck.
4. **Соберите representative dataset.** p50/p95/max snapshots, реальные repository sizes, think gaps, tool/model distributions без секретов.
5. **Отделите cache states.** Cold и warm tests запускаются и помечаются отдельно.
6. **Выполните load sweep.** Повышайте arrival/concurrency ступенями, удерживая каждую достаточно долго для steady state.
7. **Найдите saturation knee.** Точку, после которой queue/tail быстро растут, а goodput почти не увеличивается.
8. **Проверьте overload.** Burst выше capacity должен вызвать bounded queue/load shedding и затем восстановление, а не бесконечный хвост.
9. **Повторите с отказом.** Потеря Worker/node, controller restart, storage throttling и rollout показывают usable capacity, а не лабораторный максимум.
10. **Повторите прогоны.** Сохраняйте raw per-request results, percentiles, failures, versions и variance.

### Минимальная матрица

| Ось | Минимальные значения |
| --- | --- |
| Lifecycle | cold create, golden restore, Actor-owned resume, warm request |
| Snapshot | small, p50, p95, max credible; warm/cold cache |
| Load | 1, expected, burst, saturation, beyond saturation |
| Pool | 50%, 80%, 95% utilization и одна потерянная replica/node |
| Arrival | steady, jittered, coordinator fan-out, recovery storm |
| Result | latency distribution, goodput, errors, queue, recovery time, resource cost |

## Используйте готовые Substrate scenarios, но не путайте их с вашим acceptance test

На снимке `df78882` repository уже содержит nascent Locust-based suite. Она полезна как строительный блок:

- **DurDir** измеряет запись, suspend/resume и первый read после restore с разными durable/full profiles;
- **SWE-Perf replay** добавляет реалистичные execution cycles и разделяет RPC, RTT и wall clock;
- **Agent-Session** моделирует coding session с think gaps, suspend между шагами, CPU/RAM/disk/network operations и user-visible `WakeFirstTouch`;
- **Spawn** измеряет batch create-to-first-ping, readiness percentiles и `TimeToAllReady`;
- результаты сохраняют Locust CSV, failures, traces, server telemetry и density facts.

Это сильнее игрушечного counter, потому что workload явно описывает state и операции. Но suite сама названа nascent, а synthetic script остаётся моделью. Адаптируйте scripts и acceptance thresholds под свои repositories, language toolchains, model think time и policy. Проверяйте известные caveats конкретного test: например, документация Spawn прямо предупреждает, что некоторые общие `actors_per_*` ratios для него используют неподходящий denominator.

## Как оформить capacity envelope

Финальный результат benchmark должен быть проверяемым утверждением, а не «кластер выдержал 500 агентов»:

> При workload mix X, snapshot p95 Y и warm reserve Z система выдерживает λ wakeups/s и burst B, сохраняя Actor-ready p95, Task goodput и error budget в пределах SLO. При потере одной Worker node admission отклоняет background class, interactive class восстанавливается за R секунд. Следующий bottleneck - object-store bandwidth.

К envelope приложите manifest/digests, генератор, raw data, dashboards/traces, cache state, стоимость и точку saturation. Отдельно запишите overload behavior. Capacity без описания поведения за пределом опасна: система может стабильно выполнять 100 tasks/min и катастрофически зависать на 105.

## Что оптимизировать и в каком порядке

1. **Уберите лишнюю работу.** Дубликаты reconcile, ненужные snapshots, лишние agent steps и повторные tool calls дешевле любого scale-out.
2. **Ограничьте перегрузку.** Bounded queues, deadlines, cancellation, retry budget и load shedding сохраняют goodput.
3. **Исправьте самый узкий stage.** Используйте trace и load sweep, а не CPU dashboard одного Pod.
4. **Добавьте параллелизм с invariants.** Ordering per Task/Actor, idempotency и store semantics важнее headline tasks/s.
5. **Добавьте capacity и locality.** Workers, storage или model replicas только там, где измерения показывают дефицит.
6. **Повторите benchmark.** Оптимизация считается успешной, если envelope расширился без ухудшения correctness, tail и recovery.

## Антипаттерны

- **Capacity равна числу Actors.** Не указаны duty cycle, state и arrival rate.
- **Тест concurrency 1 и линейная экстраполяция.** Queue, locks, cache и storage contention нелинейны.
- **Только average latency.** Tail определяет fan-out и пользовательский outcome.
- **Cold и warm samples в одной выборке.** Число невозможно интерпретировать.
- **Scale по CPU при исчерпанных slots.** Signal не связан с добавляемой capacity.
- **Бесконечный parking/retry.** Явный отказ превращается в длинный отказ и retry storm.
- **Больше controller replicas без проверки ordering.** Throughput покупается гонками.
- **Benchmark без correctness.** Быстрый duplicate side effect считается успехом.
- **Нагрузка без recovery phase.** Не видно, очищается ли queue после burst.
- **PROJECT CLAIM как SLO.** Чужая topology и workload выдаются за гарантию.

## Итог

Performance AX/Substrate - это задача о цепочке очередей, stateful transitions и внешних dependencies. Начните с workload model и goodput, разложите critical path, измерьте каждый service station, найдите saturation knee и только потом выбирайте scaling knob. Oversubscription приносит пользу только при низком и некоррелированном duty cycle; autoscaling требует signal, связанный с добавляемой capacity; storage и model/tool endpoints остаются частью end-to-end SLO.

Следующая глава превращает эти измерения в troubleshooting handbook. Когда Task медленный или застрял, мы будем искать не «медленный компонент вообще», а конкретный слой, queue и переход состояния, на котором перестал выполняться проверенный contract.

### Источники и дальнейшее чтение

- [Substrate README: project claims, multiplexing и demos на снимке df78882](https://github.com/agent-substrate/substrate/blob/df788825e6fc13dd7aa5fd283c8f14bd4c818247/README.md)
- [Substrate architecture и aspirational warning на снимке df78882](https://github.com/agent-substrate/substrate/blob/df788825e6fc13dd7aa5fd283c8f14bd4c818247/docs/architecture.md)
- [Substrate API guide: WorkerPool resource budget и placement](https://github.com/agent-substrate/substrate/blob/df788825e6fc13dd7aa5fd283c8f14bd4c818247/docs/api-guide.md)
- [Substrate benchmarking suite: DurDir, SWE-Perf, Agent-Session и Spawn](https://github.com/agent-substrate/substrate/blob/df788825e6fc13dd7aa5fd283c8f14bd4c818247/benchmarking/README.md)
- [Substrate autoscaled WorkerPool demo и ограничения scaling signal](https://github.com/agent-substrate/substrate/blob/df788825e6fc13dd7aa5fd283c8f14bd4c818247/demos/autoscaled-workerpool/README.md)
- [AX issue #384: serial reconcile и prototype benchmark с caveats](https://github.com/google/ax/issues/384)
- [Kubernetes Horizontal Pod Autoscaling: algorithm, metrics и behavior](https://kubernetes.io/docs/concepts/workloads/autoscaling/horizontal-pod-autoscale/)
