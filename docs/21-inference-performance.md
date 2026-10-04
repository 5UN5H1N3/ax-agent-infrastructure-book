# Производительность inference: что измерять для 10-15+ агентов

![Local inference: путь запроса через batching, prefill, KV cache и decode](.gitbook/assets/diagrams/21-19.png)  
*Local inference: путь запроса через batching, prefill, KV cache и decode*

Для agent platform количество агентов не равно числу одновременно декодирующих запросов. Агент проводит время в tool calls, waiting, planning, approvals и I/O. Поэтому capacity моделируется через **arrival rate и duty cycle**, а не «N agents × full model speed».

### Основные метрики

- **TTFT (time to first token)** - задержка до начала ответа; чувствительна к prefill/queueing.
- **ITL (inter-token latency)** - задержка между токенами decode.
- **Output tokens/s** - скорость отдельной sequence.
- **Aggregate tokens/s** - throughput всего server.
- **Queue time** - время ожидания scheduler.
- **KV cache occupancy** - pressure на memory.
- **GPU utilization / memory bandwidth** - помогает отличить compute от memory bottleneck.
- **p50/p95/p99 latency** - среднее почти бесполезно при agent orchestration.

### Пример capacity mental model

Допустим, 15 голосовых/чат agents активны одновременно, но каждый делает LLM inference 35% wall time, остальное - ASR/TTS/tools/wait. Тогда средняя inference concurrency ближе к 5-6, однако burst может дать все 15. Сервер нужно sizing'овать по приемлемому p95/p99 burst latency, а не только average utilization.

### Continuous batching

Server объединяет sequences на каждом decode step, что увеличивает hardware utilization. Но слишком высокая concurrency увеличивает per-request latency и KV pressure. Production goal обычно не «максимум tokens/s любой ценой», а SLO на TTFT/ITL при заданной concurrency.

### Agent-aware routing

Не все шаги требуют одной модели. Router может отправлять дешёвую классификацию/validation на меньшую модель, а сложный planning - на более крупную. Но routing logic должен быть измеримым: экономия tokens не нужна, если маленькая модель удваивает retries и tool errors.

### Benchmark protocol

Сохраняйте модель/revision, quantization, hardware, driver/CUDA, server version, context distribution, output distribution, concurrency, sampling config и tool schema. Без этого benchmark нельзя сравнивать во времени.
