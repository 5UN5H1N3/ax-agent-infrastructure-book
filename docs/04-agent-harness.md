# Agent harness: система вокруг LLM

![Agent harness: минимальная операционная анатомия](.gitbook/assets/diagrams/04-04.svg)  
*Agent harness: минимальная операционная анатомия*

`Harness` - удобный термин для слоя, который превращает модель в управляемый исполняемый процесс. В разных frameworks границы отличаются, но эксплуатационно полезно выделять одинаковые обязанности.

### Обязательные функции harness

**Context assembly** выбирает инструкции, goal, историю, memory и результаты tools. **Model adapter** нормализует различия OpenAI/Anthropic/Gemini/local endpoints. **Tool executor** преобразует tool proposal в настоящий вызов. **State manager** хранит progression. **Policy layer** решает, допустима ли операция. **Budget controller** ограничивает число шагов, tokens, wall time и деньги. **Retry policy** различает временный сбой и логическую ошибку. **Tracing** связывает LLM request, tool call и task/actor identity.

Хороший harness не должен полагаться на prompt как на единственную защиту. Правило «никогда не выполняй `shutdown`» внутри system prompt слабее технического deny policy, потому что prompt injection воздействует на тот же канал принятия решения. Правильный подход: model proposes, policy decides, tool enforces.

### Harness и orchestrator - разные роли

Harness управляет шагами **внутри** agent execution. Orchestrator управляет множеством executions: создать Task, выбрать Workspace, приостановить idle workload, выделить ресурсы, повторить при инфраструктурном сбое, уничтожить sandbox. Если смешать уровни, возникает путаница: retry HTTP 503 модели должен быть обязанностью harness/model gateway, а восстановление Actor после падения Worker - обязанностью runtime/control plane.

### Minimal production contract

Для каждого агента стоит явно определить: входной schema, maximum wall time, maximum steps, token budget, разрешённые tools, outbound destinations, persistent state location, checkpoint strategy, termination semantics и expected artifacts. Это превращает «автономного агента» в управляемую workload единицу.

AX не навязывает конкретный harness. Это важная архитектурная граница: можно запускать собственный Python/Go/Node agent, framework или CLI-agent, если container/runner contract соблюдён.
