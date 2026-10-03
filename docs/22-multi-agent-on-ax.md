# Multi-agent на AX: как делегировать без task explosion

![Практика: software-engineering multi-agent workflow](.gitbook/assets/diagrams/22-19.png)  
*Практика: software-engineering multi-agent workflow*

AX Task удобно использовать как unit isolation для специализированных workers. Но Task не должен создаваться на каждый микрошаг. Граница Task оправдана, когда нужны отдельные resources, security scope, Workspace, lifecycle или parallelism.

### Coordinator pattern

Root Task получает goal и создаёт structured subtasks. Каждому child передаются ограниченный input, expected output schema, deadline, maximum steps/tokens и allowed capabilities. Результат сохраняется как artifact/state, а coordinator решает дальнейший переход.

### Budget propagation

Если root budget 100 условных единиц, дети не должны суммарно получить 500. Budget распределяется сверху вниз. Это касается не только tokens, но и wall time, GPU quota, external API cost и количества spawned Tasks. Хороший invariant: `sum(child maximum) <= remaining parent budget + explicitly approved burst`.

### Authority attenuation

Child Task не должен наследовать все credentials parent. Researcher получает read-only repo; implementer - write branch, но не merge; tester - execution tools без production credentials; reviewer - read. Для infrastructure workflow collector получает telemetry read-only, action agent - временный scope только после approval.

### Idempotent delegation

Coordinator restart не должен создавать дубликаты children. Используйте deterministic subtask IDs/idempotency keys и durable task journal. Если AX API create не имеет нужной high-level semantic, это обеспечивает application layer.

### Result aggregation

Не склеивайте все ответы агентов в огромный prompt. Сначала валидируйте structured outputs, извлекайте facts/artifacts и только затем создавайте компактный synthesis context. Это снижает token cost и prompt injection propagation.
