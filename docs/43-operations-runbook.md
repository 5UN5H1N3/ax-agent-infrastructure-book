# Operations runbook: диагностика по слоям и release acceptance

![Troubleshooting ladder: идите от физического слоя к agent logic](.gitbook/assets/diagrams/43-41.png)  
*Troubleshooting ladder: идите от физического слоя к agent logic*

### Не начинайте инцидент с restart всего кластера

Сначала сохраните evidence: Task manifest/status, generation/conditions, controller lag/logs, Actor/Worker assignment, runner health, child process state, Gateway/DNS test, model request ID, MCP call ID и последние внешние side effects. Restart может удалить самую полезную информацию.

### Слой 1 - Kubernetes/infrastructure

```
kubectl get nodes -o wide
kubectl get pods -A -o wide
kubectl get events -A --sort-by=.lastTimestamp
kubectl top nodes
kubectl top pods -A
```

Проверяем pressure, scheduling, image pulls, storage/network и crash loops.

### Слой 2 - Substrate

Проверяем WorkerPool capacity, Actor phase/assignment, snapshot URI/compatibility, resume errors, object-store reachability и atelet/atecontroller logs. Если Actor не получил Worker, AX runner ещё вообще не участвует в проблеме.

### Слой 3 - AX

Сверяем desired Task generation, controller reconcile/error/backlog, referenced Workspace/Model/Gateway, conditions и suspend/resume transition. Current-main split `ax-server`/`ax-controller` означает, что API может быть доступен при сломанном reconciliation.

### Слой 4 - runner/workload

`/healthz` отвечает на вопрос о runner, `/readyz` - о readiness contract, но отдельно проверяется child process и task-specific progress heartbeat. Из debug Task полезны `ps`, environment metadata, `/workspace` contents и DNS/network probes.

### Слой 5 - MCP/network/model

Проверяем DNS -> TCP -> TLS -> HTTP/protocol; затем auth, rate limit и server-side operation status. Для inference сравниваем queue time, TTFT, decode throughput, KV/VRAM pressure и OOM/eviction events.

### Слой 6 - agent logic

Только после инфраструктурных проверок анализируем prompt/context, tool schema, retry loop, planning и memory. Это предотвращает типичную ошибку: «тюнинговать prompt», когда MCP endpoint просто не резолвится.

### Failure taxonomy

Используйте категории: desired-state admission, reconciliation, placement/capacity, restore/snapshot, runner/bootstrap, dependency/network, inference, tool authorization, external side effect, agent reasoning. Incident tag по слою облегчает статистику повторяемости.

### Release acceptance suite

Перед обновлением AX/Substrate прогоните pinned набор: create simple Task; Workspace Git bootstrap; MCP read; denied write; approved idempotent write + verification; suspend/resume with durable file; worker loss; forbidden egress; model call; child-process exit; snapshot restore; backup/restore control state. Результаты сохраняйте вместе с exact versions/commits.

### Post-incident

Фиксируйте timeline, root layer, detection gap, unsafe retry/approval opportunities, lost observability и конкретный regression test. Для быстро меняющегося pre-1.0 stack regression suite важнее предположения «minor update ничего не сломает».
