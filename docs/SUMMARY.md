# Table of contents

* [Главная](README.md)

## Начало

* [0. Как читать эту книгу и что именно зафиксировано](nachalo/00-reading-guide.md)

## I. Фундамент агентных систем

* [1. Карта современной агентной системы: от модели до кластера](i.-fundament-agentnykh-sistem/01-agent-stack-map.md)
* [2. LLM как вычислительный компонент агентной системы](i.-fundament-agentnykh-sistem/02-llm-as-compute.md)
* [3. Что такое агент: цикл, границы и критерии остановки](i.-fundament-agentnykh-sistem/03-agent-loop-and-boundaries.md)
* [4. Agent harness: система вокруг LLM](i.-fundament-agentnykh-sistem/04-agent-harness.md)
* [5. Context, state, memory и snapshot: не смешивать](i.-fundament-agentnykh-sistem/05-context-state-memory-snapshot.md)
* [6. Tools, MCP и Skills](i.-fundament-agentnykh-sistem/06-tools-mcp-skills.md)
* [7. Multi-agent архитектуры без лишней магии](i.-fundament-agentnykh-sistem/07-multi-agent-architectures.md)

## II. Runtime, isolation и Agent Substrate

* [8. От Linux process к gVisor и microVM](ii.-runtime-isolation-i-agent-substrate/08-process-container-gvisor-microvm.md)
* [9. Kubernetes: необходимый минимум для понимания AX/Substrate](ii.-runtime-isolation-i-agent-substrate/09-kubernetes-minimum.md)
* [10. Control plane и distributed systems: как проектировать операции, которые переживают сбои](ii.-runtime-isolation-i-agent-substrate/10-control-plane-distributed-systems.md)
* [11. Actor model для агентных workload: identity, state и lifecycle](ii.-runtime-isolation-i-agent-substrate/11-actor-model.md)
* [12. Agent Substrate: как устроен runtime для stateful Actors](ii.-runtime-isolation-i-agent-substrate/12-agent-substrate-architecture.md)
* [13. Ресурсная модель Substrate: от capacity до долговечного Actor](ii.-runtime-isolation-i-agent-substrate/13-substrate-resources.md)
* [14. Lifecycle stateful Actor: pause, suspend, resume и восстановление](ii.-runtime-isolation-i-agent-substrate/14-suspend-snapshot-resume.md)
* [15. Substrate networking: routing, request parking и безопасный egress](ii.-runtime-isolation-i-agent-substrate/15-substrate-networking.md)

## III. Google AX

* [16. AX control plane: от декларации к Actor и граница с Substrate](iii.-google-ax/16-ax-control-plane.md)
* [17. AX primitives: Task, Workspace и Model без магии](iii.-google-ax/17-ax-primitives.md)
* [18. Runner: PID 1 как платформенный контракт, а не просто entrypoint](iii.-google-ax/18-ax-runner.md)
* [19. Task lifecycle в AX: synchronous reconciliation, состояния и recovery](iii.-google-ax/19-ax-task-lifecycle.md)

## IV. Local inference и multi-agent execution

* [20. Local inference как сервис: как выбрать runtime, модель и границу ответственности](iv.-local-inference-i-multi-agent-execution/20-local-inference.md)
* [21. Производительность inference: как измерять latency, throughput и capacity для 10-15+ агентов](iv.-local-inference-i-multi-agent-execution/21-inference-performance.md)
* [22. Multi-agent на AX: как проектировать делегирование без task explosion](iv.-local-inference-i-multi-agent-execution/22-multi-agent-on-ax.md)

## V. Security, observability и эксплуатация

* [23. Security architecture агентной платформы: как построить threat model и defense in depth](v.-security-observability-i-ekspluataciya/23-security-architecture.md)
* [24. Identity, authorization и human-in-the-loop: как безопасно делегировать authority агенту](v.-security-observability-i-ekspluataciya/24-identity-authorization-hitl.md)
* [25. Observability: от user goal до подтверждённого результата](v.-security-observability-i-ekspluataciya/25-observability.md)
* [26. Лабораторный стенд на Proxmox/KVM: от первого запуска до production rehearsal](v.-security-observability-i-ekspluataciya/26-proxmox-kvm-lab.md)
* [27. Production engineering: как доказать HA, persistence, upgrades и DR](v.-security-observability-i-ekspluataciya/27-production-engineering.md)
* [28. Performance и scaling: как найти bottleneck и доказать capacity](v.-security-observability-i-ekspluataciya/28-performance-scaling.md)
* [29. Failure modes и troubleshooting: как локализовать нарушенный контракт](v.-security-observability-i-ekspluataciya/29-failure-modes-troubleshooting.md)
* [30. Когда AX нужен, а когда проще другой инструмент](v.-security-observability-i-ekspluataciya/30-when-to-use-ax.md)
* [31. Антипаттерны](v.-security-observability-i-ekspluataciya/31-antipatterns.md)

## VI. Практика и reference architectures

* [32. Практикум: 14 последовательных лабораторных работ](vi.-praktika-i-reference-architectures/32-labs.md)
* [33. Две референсные multi-agent архитектуры](vi.-praktika-i-reference-architectures/33-reference-multi-agent-architectures.md)
* [34. Mental models, glossary и cheatsheets](vi.-praktika-i-reference-architectures/34-mental-models-glossary-cheatsheets.md)

## VII. Deep dives

* [35. Context engineering: управление контекстом и durable state](vii.-deep-dives/35-context-engineering.md)
* [36. MCP 2026: протокол, discovery, transport и trust boundaries](vii.-deep-dives/36-mcp-2026.md)
* [37. Безопасное выполнение tools: idempotency, approvals и attenuation полномочий](vii.-deep-dives/37-safe-tool-execution.md)
* [38. AX control plane: state, event flow и operational truth](vii.-deep-dives/38-ax-state-event-flow.md)
* [39. Substrate snapshots: ownership, storage и совместимость](vii.-deep-dives/39-substrate-snapshot-ownership.md)
* [40. Gateway и egress: DNS, TLS и проверка реальной доступности](vii.-deep-dives/40-gateway-egress.md)
* [41. Local inference sizing: память GPU, parallelism и эксплуатационные SLO](vii.-deep-dives/41-local-inference-sizing.md)
* [42. Production security, secrets и disaster recovery](vii.-deep-dives/42-production-security-dr.md)
* [43. Operations runbook: диагностика по слоям и release acceptance](vii.-deep-dives/43-operations-runbook.md)

## Приложения

* [Приложение A. Аудит проектов и выявленные расхождения](prilozheniya/appendix-a-audit.md)
* [Приложение B. Production readiness checklist](prilozheniya/appendix-b-production-readiness.md)
* [Приложение C. Карта источников](prilozheniya/appendix-c-sources-map.md)
