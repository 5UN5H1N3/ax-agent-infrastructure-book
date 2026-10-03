# Local inference: CPU, GPU, vLLM и llama.cpp

![Local inference: путь запроса через batching, prefill, KV cache и decode](.gitbook/assets/diagrams/20-17.png)  
*Local inference: путь запроса через batching, prefill, KV cache и decode*

Локальный inference позволяет контролировать данные, latency profile, стоимость hardware и availability, но переносит на вас capacity planning, model serving и upgrades. Для AX это просто model endpoint: orchestration не должно зависеть от того, облачный он или локальный.

### Два практических server класса

**vLLM** ориентирован на throughput GPU serving, continuous batching и масштабирование крупных моделей. Current release line 2026-09 - `v0.30.0`. OpenAI-compatible server упрощает интеграцию с harnesses. **llama.cpp** удобен для CPU, hybrid CPU/GPU, GGUF quantization, homelab и fallback; server тоже предоставляет OpenAI-compatible интерфейс и умеет parallel decoding/continuous batching.

### Prefill и decode

Prefill обрабатывает весь prompt и создаёт KV-cache. Decode генерирует токены последовательно. Поэтому TTFT зависит от prompt length, batching и prefill throughput, а inter-token latency - от decode path и contention. Large context может съесть VRAM даже если model weights помещаются комфортно.

### KV cache

KV cache - динамическая память, растущая с числом активных sequences и длиной context. Поэтому вопрос «модель занимает 20 ГиБ, хватит ли 32 ГиБ GPU?» неполон. Нужны weights + KV + runtime buffers + fragmentation margin. У vLLM есть отдельные настройки memory utilization и KV cache size/dtype; менять их следует после измерения, а не по случайному tuning guide.

### Quantization

Quantization снижает memory footprint и иногда повышает throughput, но может изменить quality и kernel path. Для agent workloads особенно важно тестировать tool calling/structured output, а не только perplexity. Модель, которая отвечает красиво, но чаще ломает JSON schema, эксплуатационно хуже.

### Security server endpoint

OpenAI-compatible endpoint не должен автоматически становиться доступным всей сети. API key конкретного inference server может защищать не все administrative endpoints. Помещайте server за network policy/reverse proxy, ограничивайте paths и identity, а metrics/admin отделяйте от inference traffic.

### Источники и дальнейшее чтение

- [vLLM docs](https://docs.vllm.ai/)
- [vLLM releases](https://github.com/vllm-project/vllm/releases)
- [llama.cpp](https://github.com/ggml-org/llama.cpp)
