# Performance и scaling: что реально ограничивает систему

Производительность AX/Substrate определяется цепочкой bottlenecks. Headline density бесполезна без понимания active ratio и snapshot profile.

### Основные коэффициенты

`logical actors / physical workers` - oversubscription. Он полезен только если большая доля actors suspended/idle. Для постоянно активных agents oversubscription не создаёт бесплатный compute.

`resume latency` складывается из assignment, snapshot location/transfer, restore, readiness и routing. Большой workspace/snapshot или удалённый object store легко доминирует над собственно sandbox startup.

`controller throughput` определяет скорость массового create/update. Current AX issue про serial reconcile напоминает: bottleneck может быть в control-plane loop, даже когда Workers свободны.

`state-store QPS` и tail latency влияют на scheduling/status. Redis/PostgreSQL должны benchmark'иться с реальным object count и payload size.

`object-store bandwidth/IOPS` особенно критичны при suspend storm, node drain или массовом resume после события.

### Benchmark matrix

Измеряйте отдельно:

- cold create → ready;
- golden start → ready;
- suspend latency vs snapshot size;
- resume latency warm/cold object cache;
- concurrent resumes 1/10/100/...;
- controller create/reconcile throughput;
- WorkerPool saturation behavior;
- egress proxy overhead;
- inference latency independently от AX.

### Project claims

Substrate README публикует ориентиры высокой density и sub-second resume. В книге они отмечаются как **PROJECT CLAIM**, а не гарантия. Ваша target workload может иметь большие files, dirty memory, network dependencies и другую storage topology.

### Practical performance budget

Полезно заранее разложить SLO: например, interactive agent wake p95 ≤ 2 s, LLM TTFT p95 ≤ 1.5 s, tool call p95 ≤ 2 s. Тогда видно, где оптимизация действительно заметна пользователю, а где выигрыш 50 ms растворяется в model inference.
