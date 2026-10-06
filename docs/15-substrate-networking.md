# Substrate networking: routing, request parking и безопасный egress

**Статус:** `ACTOR-AWARE ROUTING` · `BOUNDED PARKING` · `DEFAULT-DENY EGRESS`

В главе 14 Actor исчезал с Worker и появлялся снова, сохраняя logical identity. Теперь проследим сетевой путь. Клиент не может обращаться к Pod IP: Actor может быть suspended, переехать на другую node или получить новый Worker. Router должен разрешить logical Actor в текущее назначение, при необходимости дождаться resume и только затем открыть защищённый tunnel.

В обратную сторону действует другой контракт. Код агента считается недоверенным и не должен свободно выходить в интернет. Egress path перехватывает соединение, подтверждает identity Actor и применяет его policy. Ingress решает «куда доставить запрос», egress - «куда этому Actor разрешено обратиться». Смешивать эти задачи опасно.

## Что читатель должен унести из главы

- почему `ate-target-actor` является routing key, но не заменяет caller authorization;
- как Envoy, `atenet-router`, ate API, Worker и `atunnel` образуют ingress path;
- что request parking сглаживает краткий дефицит capacity, но не создаёт capacity;
- как per-Actor flight registry предотвращает resume storm;
- почему egress policy должна быть default-deny и как различаются HTTP, HTTPS и TLS passthrough;
- как credential injection не допускает secret внутрь Actor и snapshot;
- какие positive/negative probes нужны после каждого изменения policy.

```
INGRESS
Client -> atenet-router -> ate-api Resume/Assign -> mTLS atunnel -> Actor
             | park/retry when transient capacity is unavailable |

EGRESS
Actor -> atunnel (original destination) -> Egress PEP -> authorized upstream
                                           identity + policy + effects
```

## Стабильная identity вместо Pod address

Ingress request указывает Actor заголовком `ate-target-actor: <atespace>/<actor>`. `Host` остаётся application authority и не выбирает Actor. Router проверяет формат, обращается к control plane и получает текущий `workerAssignment`. Если Actor suspended, этот же lookup запускает resume.

Actor name удобен для маршрутизации, но lifetime определяется UID. Удалённый и заново созданный Actor с тем же именем - другой объект. Long-lived authorization, audit и certificate validation должны сверять UID, иначе имя можно случайно принять за непрерывную identity.

> **Security boundary.** Заголовок сообщает router, куда направить запрос. Он не доказывает, что caller имеет право обращаться к Actor. Caller authentication и authorization должны выполняться upstream или отдельным policy layer; сам пользовательский заголовок нельзя считать разрешением.

## Ingress path: от первого байта до Actor

1. Client отправляет HTTP request в Envoy перед `atenet-router`.
2. Envoy передаёт headers внешнему processor через `ext_proc`.
3. Router извлекает Actor ref и вызывает `ResumeActor`.
4. Control plane возвращает текущий assignment либо назначает Worker и восстанавливает Actor.
5. Router открывает authenticated TLS tunnel к `atunnel` на Worker port 443.
6. `atunnel` пересылает request в private veth Actor.
7. Response проходит назад по тому же tunnel.

Worker Pod port 80 не является прямым ingress Actor. Обход router лишил бы систему resume, assignment lookup и actor-aware authentication. NetworkPolicy должна запрещать такой обход, а observability - различать router traffic и неожиданные прямые connections.

### Почему routing нельзя построить на Kubernetes Service

Kubernetes Service хорошо маршрутизирует к набору взаимозаменяемых Pods. Здесь нужен конкретный logical Actor, который может вообще не иметь Pod. Его placement меняется при каждом resume и фиксируется control plane атомарно, без ожидания reconciliation Service/EndpointSlice. Поэтому Substrate использует собственный mapping Actor -> Worker.

## Request parking: bounded wait вместо мгновенного 503

Oversubscription означает, что Actors больше, чем свободных Workers. Во время burst scheduler может вернуть `ResourceExhausted`, хотя через сотни миллисекунд другой Actor suspend'ится и освободит capacity. Request parking удерживает входящий request и повторяет resume с backoff вместо немедленного 503.

По умолчанию park budget равен 5 секундам, parking lot допускает 1024 parked requests. Retryable считаются `ResourceExhausted`, transient `FailedPrecondition`, `Unavailable` и concurrent `Aborted`. `NotFound`, authentication/authorization errors и обычный deadline fail fast.

| Resume result | Router behavior | HTTP смысл |
| --- | --- | --- |
| `OK` | Route к Worker | Продолжить исходный request |
| `Aborted` | Retry | Конкурентный lifecycle |
| `ResourceExhausted` | Park + retry | Временный дефицит capacity |
| `FailedPrecondition` | Park + retry | Actor в переходном состоянии |
| `Unavailable` | Park + retry | Краткий сбой control plane |
| `NotFound` | Fail fast | 404 |
| `PermissionDenied/Unauthenticated` | Fail fast | 403/401 |
| `DeadlineExceeded` | Fail fast | 504 |

### Budget ограничивает новые retries, а не начатый restore

Если budget закончился, router перестаёт начинать новые попытки. Уже выполняющийся resume не отменяется: control plane мог назначить Worker и загружать snapshot. Router ждёт его реальный результат в пределах server-side RPC deadline и Envoy `ext_proc` timeout. Поэтому request иногда обслуживается позже park budget. Это лучше, чем отменить почти завершённый restore и оставить дорогой workflow без клиента.

### Один Actor - один resume flight

Конкурентные requests одного Actor присоединяются к единому in-flight `ResumeActor`. Они занимают отдельные parking slots, но не создают resume storm. Budget принадлежит flight: поздний request получает только оставшееся время, поэтому может увидеть `budget_exhausted` раньше пяти секунд собственного ожидания.

### Parking lot - backpressure, а не очередь без границ

Slot берётся только после первой retryable ошибки. Fast-path request к running Actor lot не использует. При заполнении новые parked requests получают 503 `router at capacity`. Envoy circuit breaker по умолчанию рассчитывается как удвоенный размер lot, минимум 1024: одна часть для parked streams, вторая сохраняет headroom быстрым запросам.

```
parking capacity plan:
  parked_request_max <= ext_proc max_requests
  fast_path_headroom remains available
  client timeout > expected park + restore
  park budget < upstream end-to-end deadline
```

### Graceful shutdown

При SIGTERM router и Envoy sidecar продолжают обслуживать in-flight ext\_proc streams до drain deadline. Он должен быть не меньше park budget. Иначе rolling update сам создаст сбросы parked requests.

## Нестандартные ingress ports

Для Actor port, отличного от default 80, клиент использует HTTP `CONNECT` с port в authority. Router завершает CONNECT и проводит HTTP(S) traffic через тот же actor-aware path. Каждый request внутри long-lived tunnel снова может разрешить assignment после relocation.

Это не универсальный TCP proxy. Сейчас arbitrary-port path поддерживает HTTP(S); raw TCP и другие protocols таким способом недоступны. Protocol requirement нужно проверить до выбора Substrate, а не после переноса workload.

## Worker tunnel и mTLS

После assignment router соединяется с `atunnel` на Worker port 443. Tunnel аутентифицирован TLS, а Actor доступен только через private veth. Certificate и control-plane assignment связывают логическую identity с текущим runtime location.

Нельзя кэшировать Worker IP дольше lifecycle assignment. После suspend/resume Actor может оказаться в другом Pod. Любой sidecar или higher-order gateway, который запомнил старый address, должен повторить actor-aware lookup.

## Egress: Actor считается недоверенным

Каждое исходящее TCP connection Actor, кроме DNS на destination port 53, перенаправляется nftables в `atunnel`. Он получает original destination и открывает mTLS HTTP/1.1 CONNECT к policy enforcement point. PEP проверяет Actor certificate, его UID и состояние через control plane, затем применяет `EgressPolicy`.

DNS по TCP/UDP 53 может обходить PEP к node-configured resolver. Остальной UDP блокируется, как и прочие IP protocols. Actor networking сейчас IPv4-only. Это конкретный product contract, а не общая «сетевость контейнера».

### Почему PEP не доверяет SNI и Host от Actor

Actor контролирует HTTP headers и TLS ClientHello. SNI может быть policy input, но не доказательством, что connection действительно идёт к этому hostname. Если policy разрешает имя, gateway должен сам разрешить и dial'ить разрешённое имя, а не авторизовать произвольный original IP только потому, что Actor прислал подходящий SNI.

## EgressPolicy: allow rules, default deny

EgressPolicy является nested resource конкретного Actor. У Actor может быть максимум одна policy с именем `default`. Rules - unordered allow set; если ни одно правило не совпало, traffic запрещён. Policy имеет UID/version и обновляется read-modify-write, как другие ate resources.

| Rule | Что видит gateway | TLS | Effects |
| --- | --- | --- | --- |
| `http` | Каждый cleartext HTTP request, authority и port | Нет | Возможна замена headers |
| `https` | SNI, затем decrypted HTTP requests | Gateway MITM и re-origination | Header replacement/credential injection |
| `tlsPassthrough` | ClientHello SNI и port один раз | Без расшифровки | Нет |

Exact hostname сильнее wildcard; explicit port сильнее all ports. Конфигурации, где два rules одинаково решают один name/port, отклоняются. Wildcard `*.example.com` покрывает ровно один label: он не включает `example.com` и `a.b.example.com`.

### Поддерживаемый egress traffic

| Traffic | Поддержка | Отказ виден Actor |
| --- | --- | --- |
| HTTP/1.1, HTTP/2, gRPC over HTTP/2 | Через policy | 403 |
| HTTPS с interception или TLS passthrough | Через соответствующий rule | 403/TLS failure |
| WebSocket | Блокируется | 403 |
| Forward-proxy CONNECT | Блокируется | 403 |
| Другой TCP | Блокируется | Connection закрывается |
| UDP 53 DNS | Разрешён к node resolver | Обычный DNS |
| Другой UDP/protocol | Drop | Ожидание до client timeout |

## Пример policy

```
metadata:
  atespace: team-a
  name: default
rules:
- https:
    hostnames: ["api.example.com"]
    ports:
      numbers: [443]
- tlsPassthrough:
    hostnames: ["models.example.net"]
    ports:
      numbers: [443]
- http:
    hostnames: ["otel-collector.ate-system.svc"]
    ports:
      numbers: [4318]
```

Policy должна перечислять также telemetry endpoints. OpenTelemetry SDK внутри Actor является обычным egress client и не достигает collector без разрешающего rule.

## HTTPS interception и trust bundle

`https` rule требует расшифровать HTTP, поэтому gateway выдаёт leaf certificate для SNI и устанавливает новый TLS session к upstream. Actor обязан доверять gateway CA через projected trust bundle. Без него TLS validation внутри Actor должна завершиться ошибкой; отключать verification нельзя.

`tlsPassthrough` сохраняет end-to-end TLS до origin, но gateway видит только ClientHello. Он не может заменить headers или проверять каждый HTTP request. Policy update начинает действовать для нового connection; уже открытый passthrough tunnel может жить до закрытия.

## Credential injection: secret не входит в Actor

Для HTTPS API policy может заменить placeholder header реальным credential. Actor отправляет, например, `Authorization: placeholder`. Gateway спрашивает credential provider, provider проверяет actor identity/atespace и читает secret store, затем gateway подставляет значение перед отправкой upstream. Actor никогда не получает secret, поэтому не может прочитать, записать в snapshot или exfiltrate его.

```
- https:
    hostnames: ["api.example.com"]
    effects:
      replaceHeaders:
      - header: Authorization
        prefix: "Bearer "
        credentialUri: ate-secret://k8s.io/default/ns1/api/token
```

Отсутствующий placeholder означает «не запрашивать credential» и request идёт без замены. Если header есть, но provider отключён, secret не найден, Actor не авторизован или provider недоступен, gateway fail closed: обычно 403 для постоянного отказа и retryable 503 для временной недоступности.

Reference Kubernetes provider использует default-deny mapping Atespace -> разрешённые namespaces. Cluster-wide permission читать Secrets у provider не означает, что любой Actor может получить любой Secret; provider обязан применить actor-scoped authorization.

## Policy rollout и consistency

Egress gateway может кэшировать policy, поэтому update действует не мгновенно. Long-lived TLS passthrough connection также сохраняет решение до reconnect. Runbook должен учитывать cache TTL и принудительное закрытие connections, когда требуется срочный revoke.

Readiness gateway подтверждает, что компоненты и configuration загрузились. Она не доказывает end-to-end доступность конкретного hostname, корректность DNS, CA chain или credential provider. После deployment нужны probes из реального sandbox.

## Как тестировать ingress и egress

### Ingress probes

- running Actor: fast path без parking;
- suspended Actor: первый request переживает resume;
- несколько requests одного Actor: один resume flight;
- pool saturation: served within budget и budget-exhausted 503;
- unknown Actor: 404 без parking;
- rolling restart router: parked stream получает нормальный verdict.

### Egress probes

- разрешённый DNS и HTTPS hostname;
- запрещённый hostname и тот же IP с поддельным SNI;
- wildcard boundary: один label, apex и nested name;
- TLS validation через projected gateway CA;
- credential placeholder: успешная замена и отрицательный atespace;
- UDP/TCP protocol, который workload реально использует;
- telemetry, model endpoint и MCP endpoint отдельно;
- revoke policy на новом и уже открытом connection.

## Observability и capacity signals

| Сигнал | Что означает | Действие |
| --- | --- | --- |
| `parking.active` | Текущий demand сверх мгновенной capacity | Смотреть длительность и pool saturation |
| `parking.wait.duration` by outcome | Served, budget exhausted, canceled, timeout, error | Разделять capacity и client cancellation |
| `parking.rejected` | Parking lot заполнен | Scale router и проверить burst/circuit breaker |
| Resume latency | Причина задержки первого request | Коррелировать с главой 14 |
| Egress denies by actor/rule | Policy gap или атака | Не разрешать wildcard без расследования |
| Credential provider 403/503 | Authorization или availability | Разные alerts/runbooks |
| Tunnel TLS failures | Identity/CA/certificate rotation | Проверить обе стороны mTLS |

> **Capacity lesson.** Большой parking lot скрывает burst, но удерживает memory и ext\_proc streams. Если `budget_exhausted` растёт постоянно, проблема не в размере очереди: WorkerPool или suspend rate не соответствуют demand.

## Антипаттерны

- **Маршрутизировать по Pod IP.** Address устаревает после каждого relocation.
- **Считать target header авторизацией.** Caller может подставить чужое имя.
- **Увеличивать park budget вместо capacity.** Ошибка превращается в долгую latency.
- **Доверять SNI как доказательству destination.** Actor контролирует ClientHello.
- **Разрешать `*` для удобства.** Default-deny исчезает именно там, где agent способен exfiltrate данные.
- **Отключать TLS verification для interception.** Нужно проецировать gateway CA.
- **Класть API keys в env Actor.** Они попадут в memory/snapshot и доступны недоверенному коду.
- **Проверять policy только с admin Pod.** Реальный path из sandbox другой.

## Production checklist networking

- Ingress принимает logical Actor ref и не использует Worker address как identity.
- Caller authorization выполняется до или независимо от routing.
- Park budget, lot size, ext\_proc breaker и client deadline согласованы.
- Fast-path headroom остаётся при заполненном parking lot.
- Router drain timeout не меньше park budget.
- Worker ingress доступен только через authenticated tunnel.
- У каждого Actor есть минимальная default-deny EgressPolicy.
- Выбран правильный rule: http, https interception или tls passthrough.
- Gateway CA доступен только workload, которым нужен HTTPS interception.
- Secrets выдаёт credential provider с actor-scoped authorization.
- Positive и negative probes выполняются из обоих sandbox classes.
- Dashboards разделяют parking capacity, lifecycle errors и egress denies.

## Итог

Substrate networking связывает стабильную Actor identity с меняющейся execution. Ingress router при необходимости будит Actor, bounded parking сглаживает краткий дефицит Workers, а mTLS tunnel доставляет request только текущему assignment. Egress начинается с противоположного предположения: Actor недоверен, traffic default-deny, destination выбирает gateway, а secrets остаются у credential provider.

Эта глава завершает dataplane path. В следующей главе мы поднимемся уровнем выше и разберём AX control plane: какие решения принадлежат AX, какие делегируются Substrate и где проходит граница ответственности между продуктовым orchestration и runtime.

### Источники и дальнейшее чтение

- [Agent Substrate architecture](https://github.com/agent-substrate/substrate/blob/main/docs/architecture.md)
- [Request parking design](https://github.com/agent-substrate/substrate/blob/main/docs/request-parking.md)
- [Network egress contract](https://github.com/agent-substrate/substrate/blob/main/docs/network-egress.md)
- [Supported egress traffic](https://github.com/agent-substrate/substrate/blob/main/docs/egress-traffic.md)
- [Egress credential injection](https://github.com/agent-substrate/substrate/blob/main/docs/egress-credential-injection.md)
- [EgressPolicy API definitions](https://github.com/agent-substrate/substrate/blob/main/pkg/proto/ateapipb/ateapi.proto)
