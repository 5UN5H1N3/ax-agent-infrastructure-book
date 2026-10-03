# Приложение A. Аудит проектов и выявленные расхождения

## A.1 AX

**Release baseline:** `v0.3.1`, commit `e70162a`, release 25.09.2026.  
**Current API family:** `ax.io/v1alpha1`.  
**Stability:** project explicitly warns about breaking changes before stable.

Ключевые факты current documentation/source audit:

- README описывает четыре user primitives: `Task`, `Workspace`, `Gateway`, `Model`.
- `docs/manifests.md` в current crawl говорит «all four kinds», но видимая часть документа подробно показывает Task/Workspace/Model; Gateway лучше проверять по README/examples/API, а не по одной странице.
- `cmd/ax-controller` существует и читает Redis stream consumer group, создавая `TaskReconciler` с Substrate client.
- `DESIGN.md` всё ещё описывает reconciliation внутри `ax-server`. Это documentation drift.
- Runner всегда PID 1 и restart после resume видит durable `/workspace`, но новое process tree.
- Current known issues показывают полезные ограничения: readiness может не отражать exit child command; workspace clone мог завершаться «успешно» при пустом repo; hostname Gateway/TLS имел bug; ssh/control-plane auth вопросы меняются.

**Вывод:** production runbook должен ссылаться на pinned release и regression tests, а не только на `main` docs.

## A.2 Agent Substrate

**Release baseline:** `v0.3.0`, commit `ccecc78`, release 30.09.2026.  
**Status:** pre-1.0; API compatibility не гарантирована.

Ключевые факты:

- Quickstart использует Kind и может разворачивать PostgreSQL/RustFS локально.
- WorkerPool - Kubernetes-side physical capacity; ActorTemplate/Actor/Worker dynamic state относится к Substrate API/state store.
- Current architecture использует PostgreSQL для dynamic control state и object storage для snapshots.
- gVisor и microVM - разные sandbox classes.
- Golden snapshot и Tag ownership - важная часть clone/resume semantics.
- Architecture document содержит прямое предупреждение, что часть design aspirational.
- v0.2.0/v0.3.0 быстро усиливали multi-actor networking, egress, identity, authorization/observability, поэтому старые статьи быстро устаревают.

## A.3 MCP

Для книги принята specification `2026-07-28`, потому что она меняет базовый mental model: stateless core, `server/discover`, request metadata versioning, новый subscription mechanism и изменения authorization/deprecations. Материалы 2024-2025 про обязательный initialize/session flow нужно читать как legacy compatibility, а не current normative behavior.
