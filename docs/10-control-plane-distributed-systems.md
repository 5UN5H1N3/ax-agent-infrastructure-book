# Control plane и distributed systems: база без академической перегрузки

AX/Substrate - распределённые control planes, поэтому несколько идей необходимо понимать практически.

### Desired/actual state и eventual consistency

После `apply` результат не обязан появиться мгновенно. API может успешно принять объект, а reconciler ещё будет создавать Actor, ждать Worker и readiness. Поэтому корректный клиент читает status/conditions или watch stream, а не считает HTTP/gRPC success равным готовности workload.

### Idempotency

Control plane операции будут повторяться: из-за retry, restart controller'а или потери ответа. Поэтому шаги reconciliation должны быть идемпотентными либо иметь уникальные operation IDs. Для внешних tools это особенно важно: «повторить API call» может быть безопасно для GET и опасно для `create payment`/`change route`.

### Distributed lock

Когда несколько controller replicas могут reconciliate один ресурс, нужен механизм не выполнять конфликтующие операции одновременно. Lock не заменяет идемпотентность: holder может умереть после side effect, но до записи status. Правильная система выдерживает повтор после lock expiry.

### Redis, PostgreSQL, streams

AX использует Redis как высокоскоростной store/event mechanism для своего task control plane. Current `ax-controller` читает Redis stream consumer group и вызывает reconciler. Substrate current architecture использует PostgreSQL для высокочастотного dynamic Actor/Worker state. Это два разных слоя с разными failure domains; Redis backup не восстанавливает Substrate actors, а PostgreSQL backup не восстанавливает AX desired specs.

### gRPC/protobuf

Protobuf задаёт typed message schema, gRPC - RPC contract и streaming. Операционно важны deadlines, retries, message-size limits, TLS/mTLS, authority/server-name, load balancing и backpressure. «RPC висит» без deadline способен привязать controller worker надолго.

### Consistency versus recovery

Для orchestration важнее не абсолютная синхронность каждого поля, а возможность восстановить истинное состояние. Status должен быть наблюдаемым и переоцениваться. Если runtime сообщает Actor RUNNING, но Worker умер, reconciler обязан обнаружить расхождение и перевести ресурс в корректное состояние.
