# Summary

* [Главная](README.md)

## Начало

* [Как читать эту книгу и что именно зафиксировано](00-reading-guide.md)

## I. Фундамент агентных систем

* [Карта современной агентной системы: от модели до кластера](01-agent-stack-map.md)
* [LLM как вычислительный компонент агентной системы](02-llm-as-compute.md)
* [Что такое агент: цикл, границы и критерии остановки](03-agent-loop-and-boundaries.md)
* [Agent harness: система вокруг LLM](04-agent-harness.md)
* [Context, state, memory и snapshot: не смешивать](05-context-state-memory-snapshot.md)
* [Tools, MCP и Skills](06-tools-mcp-skills.md)
* [Multi-agent архитектуры без лишней магии](07-multi-agent-architectures.md)

## II. Runtime, isolation и Agent Substrate

* [От Linux process к gVisor и microVM](08-process-container-gvisor-microvm.md)
* [Kubernetes: необходимый минимум для понимания AX/Substrate](09-kubernetes-minimum.md)
* [Control plane и distributed systems: как проектировать операции, которые переживают сбои](10-control-plane-distributed-systems.md)
* [Actor model для агентных workload: identity, state и lifecycle](11-actor-model.md)
* [Agent Substrate: как устроен runtime для stateful Actors](12-agent-substrate-architecture.md)
* [Ресурсная модель Substrate: от capacity до долговечного Actor](13-substrate-resources.md)
* [Lifecycle stateful Actor: pause, suspend, resume и восстановление](14-suspend-snapshot-resume.md)
* [Substrate networking: routing, request parking и безопасный egress](15-substrate-networking.md)

## III. Google AX

* [AX control plane: от декларации к Actor и граница с Substrate](16-ax-control-plane.md)
* [AX primitives: Task, Workspace и Model без магии](17-ax-primitives.md)
* [Runner: PID 1 как платформенный контракт, а не просто entrypoint](18-ax-runner.md)
* [Task lifecycle в AX: synchronous reconciliation, состояния и recovery](19-ax-task-lifecycle.md)

## IV. Local inference и multi-agent execution

* [Local inference как сервис: как выбрать runtime, модель и границу ответственности](20-local-inference.md)
* [Производительность inference: как измерять latency, throughput и capacity для 10-15+ агентов](21-inference-performance.md)
* [Multi-agent на AX: как делегировать без task explosion](22-multi-agent-on-ax.md)

## V. Security, observability и эксплуатация

* [Security architecture: threat model agent platform](23-security-architecture.md)
* [Identity, authorization и Human-in-the-loop](24-identity-authorization-hitl.md)
* [Observability: четыре слоя и единая correlation identity](25-observability.md)
* [Лабораторный стенд на Proxmox/KVM: Kind, k3s или kubeadm](26-proxmox-kvm-lab.md)
* [Production engineering: HA, persistence, upgrades и DR](27-production-engineering.md)
* [Performance и scaling: что реально ограничивает систему](28-performance-scaling.md)
* [Failure modes и troubleshooting handbook](29-failure-modes-troubleshooting.md)
* [Когда AX нужен, а когда проще другой инструмент](30-when-to-use-ax.md)
* [Антипаттерны](31-antipatterns.md)

## VI. Практика и reference architectures

* [Практикум: 14 последовательных лабораторных работ](32-labs.md)
* [Две референсные multi-agent архитектуры](33-reference-multi-agent-architectures.md)
* [Mental models, glossary и cheatsheets](34-mental-models-glossary-cheatsheets.md)

## VII. Deep dives

* [Context engineering: управление контекстом и durable state](35-context-engineering.md)
* [MCP 2026: протокол, discovery, transport и trust boundaries](36-mcp-2026.md)
* [Безопасное выполнение tools: idempotency, approvals и attenuation полномочий](37-safe-tool-execution.md)
* [AX control plane: state, event flow и operational truth](38-ax-state-event-flow.md)
* [Substrate snapshots: ownership, storage и совместимость](39-substrate-snapshot-ownership.md)
* [Gateway и egress: DNS, TLS и проверка реальной доступности](40-gateway-egress.md)
* [Local inference sizing: память GPU, parallelism и эксплуатационные SLO](41-local-inference-sizing.md)
* [Production security, secrets и disaster recovery](42-production-security-dr.md)
* [Operations runbook: диагностика по слоям и release acceptance](43-operations-runbook.md)

## Приложения

* [Приложение A. Аудит проектов и выявленные расхождения](appendix-a-audit.md)
* [Приложение B. Production readiness checklist](appendix-b-production-readiness.md)
* [Приложение C. Карта источников](appendix-c-sources-map.md)
