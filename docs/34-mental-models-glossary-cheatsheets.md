# Mental models, glossary и cheatsheets

### Если запомнить только десять вещей

1. **LLM ≠ Agent.** Модель предлагает следующий output; agent появляется из loop + state + tools.
2. **Agent ≠ Harness.** Harness - управляющая система вокруг reasoning.
3. **Harness ≠ Orchestrator.** Первый управляет шагами одного execution, второй - population/lifecycle.
4. **AX ≠ Substrate.** AX даёт agent-oriented declarative control plane; Substrate - actor/sandbox runtime.
5. **Actor ≠ Worker.** Actor - logical identity/state; Worker - physical capacity.
6. **Task ≠ Pod.** Task - AX abstraction, которая через Substrate в итоге использует Kubernetes resources.
7. **Context ≠ Memory.** Context - текущие tokens; memory/state должны иметь durable representation.
8. **Snapshot ≠ Backup.** Snapshot оптимизирует resume и имеет lifecycle/compatibility ограничения.
9. **MCP ≠ Authorization.** MCP стандартизует capability exchange; policy всё равно надо enforce.
10. **Ready ≠ outcome.** Control-plane readiness не доказывает, что бизнес/agent action успешен.

### Мини-глоссарий

| Термин | Практическое значение |
| --- | --- |
| Agent | цикл принятия действий вокруг модели и среды |
| Harness | context/tool/state/policy/retry infrastructure агента |
| Tool | формализованная операция с schema и side effect semantics |
| MCP | протокол взаимодействия agent clients с tools/resources/prompts |
| Skill | переиспользуемая инструкция/процедура, а не transport |
| Task | AX unit isolated execution |
| Workspace | declarative Git/files/MCP/skills environment |
| Gateway | AX network ingress/egress policy abstraction |
| Model | AX model provider/config/credential reference |
| Actor | Substrate logical stateful instance |
| ActorTemplate | immutable runtime blueprint + golden snapshot root |
| WorkerPool | physical warm execution capacity |
| Worker | physical assignment target for running Actor |
| Snapshot | checkpoint RAM/durable data according to runtime semantics |
| Golden snapshot | prepared baseline used to start new Actors |
| Reconciliation | повторное сведение actual state к desired state |
| TTFT | time to first generated token |
| ITL | inter-token latency during decode |
| KV cache | attention state для active sequence |

### AX operational cheatsheet

```
# Используйте CLI той же версии, что control plane
ax apply -f stack.yaml
ax get tasks
ax describe task <name>
ax watch task <name>
ax suspend task <name>
ax resume task <name>
ax delete task <name>
# только при debug=true
ax ssh <name> -- ps aux
```

### Kubernetes first-response cheatsheet

```
kubectl get nodes -o wide
kubectl get pods -A -o wide
kubectl get events -A --sort-by=.lastTimestamp
kubectl describe pod -n <ns> <pod>
kubectl logs -n <ns> <pod> --all-containers --tail=200
kubectl top nodes
kubectl top pods -A
```

### Incident questions

- Какая exact версия AX/Substrate?
- Какой Task/Actor UID?
- Desired state принят API?
- Controller обработал событие?
- Есть подходящий Worker?
- Snapshot доступен и совместим?
- Runner `/healthz` и `/readyz`?
- Child agent process реально жив?
- Gateway пропускает DNS/TCP/TLS до нужного endpoint?
- Model server не в queue/OOM?
- MCP tool доступен с той же identity?
- Есть ли записанный side effect до retry?

### Что изучать глубже отдельно

Для Kubernetes - официальные Concepts, RBAC, NetworkPolicy и kubeadm docs. Для Linux isolation - namespaces/cgroups/seccomp и gVisor architecture. Для distributed systems - idempotency, leases, consensus и transactional outbox. Для inference - vLLM/llama.cpp performance docs и GPU architecture. Для MCP - всегда сверяйте **versioned** specification, потому что протокол в 2026 существенно изменился относительно ранних материалов.
