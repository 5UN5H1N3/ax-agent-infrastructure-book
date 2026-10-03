# AX primitives: Task, Workspace, Gateway и Model

Current AX README описывает четыре маленьких пользовательских primitive. Они не являются эквивалентами Kubernetes Pod/PVC/NetworkPolicy; это agent-oriented abstraction layer.

### Task

Task - минимальная единица isolated execution. Он задаёт image, command, compute requests/limits, environment, Workspace bindings, Gateway reference и debug behavior. Task intentionally small: один Task может быть всей работой или узлом дерева delegated tasks. Status содержит phase и conditions. Не следует считать `Running` равным «agent command жив»: current issue показывает случай, когда runner остаётся PID 1 после exit child command, а status продолжает выглядеть healthy. Значит, application-level liveness нужно измерять отдельно.

### Workspace

Workspace описывает то, что агент должен получить в рабочей среде: Git sources, static files, MCP configuration, skills и optional goal для environment preparation. Один Task может bind несколько workspaces; первый становится working directory command. Подготовка должна быть idempotent и выполняться один раз на durable state.

### Gateway

Gateway задаёт сетевую границу: listeners и outbound egress allowlist. Это не «удобная настройка сети», а ключевой security primitive. Правильный default - deny и явные destinations. Реальные версии требуют end-to-end validation: наличие status `GatewayReady` подтверждает reconciliation policy, но не доказательство работоспособности DNS/TLS/application protocol.

### Model

Model описывает provider, model identifier, parameters и secret reference. Важно не приписывать Model больше, чем он делает в текущей версии. Публичный issue v0.3.0 указывает, что наличие Model не означало автоматического безопасного secret injection в произвольный Task: связь Task→Model и secret semantics менялись. Поэтому в production надо проверять actual API schema pinned release, а не рассчитывать на имя ресурса.

### Manifest discipline

Все имена следует считать API identifiers: lowercase RFC1123-style, pinned image digests, explicit atespace, explicit resource requests/limits и ограниченный Gateway. Multi-document YAML удобно хранить как immutable deployment unit для лаборатории, но в production reusable Workspace/Gateway/Model могут иметь самостоятельный lifecycle.

### Источники и дальнейшее чтение

- [AX README](https://github.com/google/ax/blob/main/README.md)
- [AX manifests](https://github.com/google/ax/blob/main/docs/manifests.md)
- [AX concepts](https://github.com/google/ax/blob/main/docs/concepts.md)
- [AX issue #348](https://github.com/google/ax/issues/348)
