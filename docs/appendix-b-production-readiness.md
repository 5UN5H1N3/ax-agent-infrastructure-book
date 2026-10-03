# Приложение B. Production readiness checklist

## Platform

- AX/Substrate/container images pinned по digest/tag.
- Compatibility matrix проверена в staging.
- Redis/PostgreSQL persistence и backup протестированы restore drill'ом.
- Object storage durability/throughput/retention проверены.
- WorkerPool headroom измерен burst benchmark'ом.
- Registry и DNS имеют понятные failure modes.

## Security

- Default-deny egress; wildcard отсутствует вне исключений.
- Debug/SSH disabled by default.
- Scoped short-lived tool credentials.
- MCP servers/skills/repositories проходят allowlist/review.
- Write/destructive tools требуют policy/approval.
- Sandbox backend выбран по threat model.
- Control-plane API закрыт perimeter/authn/authz.
- Audit logs нельзя изменить workload'у.

## Agent

- Max steps/tokens/wall time заданы.
- Retry limits и idempotency strategy заданы.
- Durable state не существует только в prompt/RAM.
- Child task depth/count budget задан.
- Tool calls имеют schema validation.
- Post-action verification есть для writes.

## Observability

- Trace связывает user/job → Task → Actor → tool/model request.
- TTFT/ITL/queue/KV metrics собираются.
- Suspend/resume/snapshot metrics собираются.
- Central logs переживают Worker relocation.
- Alerts основаны на SLO, а не только CPU.
