# Когда AX нужен, а когда проще другой инструмент

![Когда AX является разумным кандидатом, а когда это избыточный слой](.gitbook/assets/diagrams/30-25.png)  
*Когда AX является разумным кандидатом, а когда это избыточный слой*

AX разумно оценивать по workload properties, а не по моде на agents.

### Сильные сигналы в пользу AX/Substrate

- Много логических agents, значительная часть времени idle.
- Нужна изоляция untrusted/generated code.
- Workspace/state должны переживать паузы.
- Желательны suspend/resume и reclaim physical capacity.
- Нужен единый declarative lifecycle для heterogeneous agent images.
- Task population и churn выходят за удобную модель «один Pod/Job на всё».

### Когда обычный Kubernetes Job лучше

Если workload stateless, запускается, делает deterministic batch и завершает работу, Job проще. Он интегрирован с Kubernetes auth/observability и не требует дополнительного control plane.

### Когда worker queue лучше

Если code trusted, tasks короткие, state хранится в DB, а изоляция не нужна, Celery/RQ/custom workers или message queue могут быть намного дешевле операционно.

### Когда Temporal/workflow engine лучше

Если главная сложность - durable business workflow, timers, retries и exactly-tracked transitions, а execution code trusted, workflow engine часто естественнее. AX можно комбинировать с ним: workflow engine решает business orchestration, AX - sandboxed agent execution.

### Когда LangGraph/AutoGen/CrewAI уместны

Они находятся ближе к agent logic/harness. Их можно запускать **внутри** AX Task. Сравнивать «AX vs LangGraph» как взаимоисключающие продукты некорректно.

### Ray

Ray решает distributed compute/scheduling и actors в другом смысле. Он может быть хорош для parallel Python/ML workloads, но security/sandbox/suspend semantics и agent-specific primitives отличаются. Выбор зависит от dominant problem.

Главный принцип: сначала используйте самый простой runtime, который удовлетворяет threat model, state и scaling requirements. Дополнительный control plane должен окупать собственную сложность.
