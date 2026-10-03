# Failure modes и troubleshooting handbook

Диагностика идёт снизу вверх: infrastructure → runtime → AX control plane → runner/workspace → tools/model → agent logic. Ниже - рабочая матрица, которую стоит адаптировать под конкретный deployment.

| Симптом | Слой | Что проверить первым | Типичные причины |
| --- | --- | --- | --- |
| Task не создаётся | AX API | `ax apply`, server logs, schema | invalid name/spec, missing ref |
| Task долго Pending | AX/Substrate | controller events, Actor status | no eligible Worker, reconcile backlog |
| Actor не resume | Substrate | WorkerPool, snapshot, atelet | no capacity, incompatible/broken snapshot |
| WorkspaceReady=False | Runner | runner logs, `/readyz` | Git/MCP/goal bootstrap failed |
| WorkspaceReady=True, файлов нет | Runner | filesystem + Git exit | silent bootstrap error/version bug |
| Task Running, агент умер | Runner/app | `ps`, custom app metric | child exited, PID1 runner жив |
| `ax ssh` не работает | debug/network | `spec.debug`, routing | debug off, CLI bug, guest service unreachable |
| TLS egress ломается | Gateway/atenet | DNS/TCP/TLS probes | policy translation/SNI/hostname issue |
| Model timeout | inference/network | server queue, TTFT, GPU | saturation, oversized context, egress |
| GPU OOM | inference | KV/weights/batch | context/concurrency too high |
| Suspend медленный | Substrate/storage | snapshot size/bandwidth | dirty memory/files, object store |
| Resume cold | Substrate/storage | cache/locality | remote snapshot/image pull |
| Child Tasks множатся | harness | spawn events/budget | retry without idempotency, no depth limit |
| Write action повторён | tool layer | action journal/idempotency | timeout after side effect |

### Командный порядок

1. Зафиксировать exact versions и timestamps.
2. `kubectl get pods -A` / events / node pressure.
3. Проверить AX server/controller health/logs и Redis reachability.
4. Проверить Actor/WorkerPool status и Substrate components.
5. Проверить runner health/readiness и actual child process.
6. Positive/negative egress probes из sandbox.
7. Model endpoint latency independently.
8. MCP endpoint independently с той же identity/policy.
9. Только после этого анализировать agent prompt/loop.

### Не доверять одному status

Current AX issues показывают полезный принцип: control-plane condition может отражать «шаг reconciliation завершён», а не end-to-end truth. `Ready=True` не доказывает живой child command; `GatewayReady=True` не доказывает успешный TLS к allowlisted host. Верификация должна соответствовать пользовательскому outcome.

### Источники и дальнейшее чтение

- [AX issue #346](https://github.com/google/ax/issues/346)
- [AX issue #345](https://github.com/google/ax/issues/345)
- [AX issue #347](https://github.com/google/ax/issues/347)
- [AX issues](https://github.com/google/ax/issues)
