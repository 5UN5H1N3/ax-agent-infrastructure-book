# Антипаттерны

### Один гигантский агент

Один process с доступом ко всем tools и credentials удобен для demo, но создаёт огромный blast radius, context pollution и плохую диагностику. Разделяйте роли там, где различаются authority или context.

### Agent вместо deterministic logic

Не просите LLM считать CIDR, проверять обязательный формат или решать, выполнен ли health check, если это можно сделать кодом. LLM полезна для неоднозначного reasoning; deterministic constraints должны исполняться deterministically.

### State только в prompt

После context compaction/restart состояние исчезнет или исказится. Critical progression фиксируется в durable structured state.

### Unlimited retries

Retry без классификации error создаёт loops и дублирует side effects. Нужны limits, exponential backoff и idempotency.

### Shared root credentials

Один token на всех agents уничтожает возможность least privilege/audit. Используйте scoped/short-lived identity.

### `egress: *` навсегда

Wildcard удобен на первом smoke test и опасен как постоянная production policy. Сначала измерьте реальные destinations, затем сузьте allowlist и добавьте отрицательные probes.

### Debug всегда включён

`spec.debug` расширяет control surface внутри sandbox. Включайте только там, где есть операционная необходимость.

### Task per thought

Создание Task на каждый reasoning step превращает control plane в дорогую шину сообщений. Task boundary должна соответствовать isolation/resource/lifecycle boundary.

### Snapshot как backup

Snapshot оптимизирует resume; он не заменяет backup authoritative state. Особенно это верно для внешних systems и Tag lifetime.

### Benchmark на одном пустом actor

Результат не переносится на real workspace, dirty memory, large snapshots и concurrent bursts. Benchmark должен репрезентировать production distribution.
