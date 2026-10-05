# Control plane и distributed systems: база без академической перегрузки

Предыдущая глава показала mechanics Kubernetes reconciliation: как intent проходит через API, scheduler и kubelet до работающего процесса. Теперь фокус смещается с компонентов на поведение системы при задержках, повторах и частичных отказах. AX и Substrate - самостоятельные распределённые control planes поверх Kubernetes, поэтому одного знания объектов и Pods недостаточно.

## Как складываются главы 8-10

| Глава | Главный вопрос | Инженерное решение |
| --- | --- | --- |
| 8. Runtime isolation | от чего и насколько сильно изолировать workload? | process, container, gVisor или microVM по threat model |
| 9. Kubernetes | где и как разместить выбранный runtime? | Pod, controller, scheduling, сеть, storage и RuntimeClass |
| 10. Control plane | что произойдёт при retry, delay, restart и частичном side effect? | status, idempotency, operation ID, lock, deadline и recovery |

Эти вопросы нельзя переставить местами. Kubernetes не усиливает слабую isolation boundary, а microVM не делает control-plane operation идемпотентной. Надёжная agent platform требует обеих групп controls и явного контракта между ними.

### Desired/actual state и eventual consistency

В главе 9 мы видели цикл desired/actual на примере Kubernetes. В AX/Substrate действует тот же общий принцип, но со своим состоянием и своими задержками. После `apply` API может успешно принять intent, а reconciler ещё будет создавать Actor, ждать Worker, восстанавливать snapshot и проверять readiness. Поэтому **accepted**, **ready** и **completed** - разные состояния. Корректный клиент читает status/conditions или watch stream, а не считает HTTP/gRPC success равным готовности workload.

### Idempotency

Control-plane операции будут повторяться из-за retry, restart controller или потери ответа. Шаги reconciliation должны быть идемпотентными либо иметь устойчивые operation IDs. Повторное «убедиться, что Worker назначен» может быть безопасным; повторное `create payment` или `change route` без idempotency key - нет. Именно здесь инфраструктурный retry соприкасается с tool semantics агента.

### Distributed lock

Когда несколько controller replicas могут одновременно reconcile один ресурс, нужен механизм, который ограничивает конфликтующие операции. Lock не заменяет идемпотентность: holder может умереть после side effect, но до записи status. После expiry другой replica повторит работу и должна распознать уже выполненный шаг. Lock уменьшает concurrency, но не превращает распределённую операцию в транзакцию.

### Redis, PostgreSQL и streams

Глава 9 провела границу между Kubernetes API и высокочастотным application state. В AX эту роль выполняет Redis как быстрый store/event mechanism task control plane; current `ax-controller` читает Redis stream consumer group и вызывает reconciler. Current Substrate architecture использует PostgreSQL для dynamic Actor/Worker state. Это разные слои и failure domains: Redis backup не восстанавливает Substrate actors, а PostgreSQL backup не восстанавливает AX desired specs.

### gRPC/protobuf

Protobuf задаёт typed message schema, gRPC - RPC contract и streaming. Операционно важны deadlines, retries, message-size limits, TLS/mTLS, authority/server-name, load balancing и backpressure. RPC без deadline способен надолго занять controller worker; retry без знания method semantics способен повторить side effect.

### Consistency versus recovery

Для orchestration важнее не мгновенная синхронность каждого поля, а способность обнаружить расхождение и восстановить истинное состояние. Status должен быть наблюдаемым и переоцениваться. Если runtime сообщает Actor RUNNING, но Worker умер, reconciler обязан обнаружить это и перевести ресурс в корректное состояние. Если внешний side effect уже произошёл, recovery должен опираться на operation journal или запрос фактического состояния, а не слепо повторять команду.

## Итог связки

- Глава 8 выбирает physical security boundary по threat model.
- Глава 9 выражает placement, resources, identity, network и lifecycle в Kubernetes.
- Глава 10 объясняет, почему любое действие control plane может быть отложено, повторено или выполнено частично.
- Ни один слой не компенсирует автоматически ошибку другого: сильный sandbox не исправляет duplicate side effect, а надёжный reconciler не делает privileged container безопасным.

**Дальше.** Теперь у нас есть physical boundary, orchestration substrate и правила поведения control plane при отказах. Следующая глава вводит Actor model - способ отделить логическую долгоживущую identity агента от конкретного Worker, Pod и периода активного выполнения.
