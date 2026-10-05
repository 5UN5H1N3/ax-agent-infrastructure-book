# Kubernetes: необходимый минимум для понимания AX/Substrate

![Kubernetes reconciliation loop - mental model, который нужен для AX/Substrate](.gitbook/assets/diagrams/09-10.png)  
*Kubernetes reconciliation loop - mental model, который нужен для AX/Substrate*

Kubernetes здесь нужен не как отдельный предмет, а как инфраструктурная среда, в которой живут AX и Substrate. Нам не требуется знать каждую настройку кластера; важно видеть путь workload от намерения до работающего процесса и понимать, где искать сбой.

Эта глава не пытается заменить отдельную книгу о Kubernetes. Её задача практичнее: дать инженеру ровно ту модель, которая нужна, чтобы читать manifests AX/Substrate, понимать placement и lifecycle, отличать проблему Kubernetes от проблемы agent runtime и не принимать cluster abstraction за security boundary.

## После главы вы должны уметь

- проследить путь объекта от YAML до процесса на конкретной node;
- объяснить разницу между `spec`, `status`, событием и реальным состоянием процесса;
- выбрать Deployment, StatefulSet, DaemonSet или Job по семантике workload;
- понять, почему Pod остаётся Pending, перезапускается или не получает traffic;
- связать requests, limits, probes, Service, storage и RuntimeClass с поведением agent platform;
- определить, какие данные уместны в Kubernetes API, а какие должны жить в специализированном store;
- провести первый диагностический проход без случайного изменения production state.

## Kubernetes не запускает контейнер «по команде»

Самая полезная смена мышления - отказаться от модели удалённого shell. В shell команда означает разовое действие: «запусти процесс». В Kubernetes manifest означает **намерение**: «в cluster должен существовать workload с такими свойствами». Система сохраняет это намерение и продолжает работать с ним после завершения `kubectl`, disconnect клиента и перезапуска отдельных компонентов.

Пользователь пишет объект в API. API server проверяет запрос, применяет defaulting и admission policy, затем сохраняет объект. Controllers наблюдают объекты и создают следующие объекты. Scheduler выбирает node для Pod. Kubelet на этой node через CRI просит container runtime подготовить sandbox и containers. CNI настраивает сеть, CSI при необходимости подключает storage. После каждого шага компоненты публикуют status, но ни один успешный API response не обещает, что приложение уже готово.

Поэтому фраза `deployment.apps/controller created` означает только «API принял объект». Она не означает «image скачан», «Pod запущен», «probe прошла» или «Service направляет traffic». Между принятым намерением и полезно работающим процессом находится асинхронная цепочка reconciliation.

### Reconciliation как замкнутый контур

Controller читает desired state, наблюдает actual state, вычисляет различие и делает ограниченный шаг. Затем цикл повторяется. Он не обязан завершить всю работу за одну итерацию и должен переживать повторное выполнение. Если Pod исчез, Deployment controller создаст замену. Если node недоступна, другие controllers обновят состояния, а workload controller попытается восстановить нужное число replicas на доступных nodes.

Важно различать **объект** и **реальность**. Запись Pod в API может существовать, пока container ещё не создан. Status может кратко отставать от процесса. Event описывает наблюдавшийся эпизод, но не является долговечным журналом. Именно поэтому оператор читает несколько сигналов, а не делает вывод по одному полю.

## Кто за что отвечает

| Компонент | Ответственность | Чего он не делает |
| --- | --- | --- |
| `kube-apiserver` | единая API boundary, authentication, authorization, admission, validation и доступ к object state | не запускает containers и не выбирает node |
| `etcd` | хранит cluster state, который обслуживает API server | не является application database для произвольных task events |
| `kube-controller-manager` | запускает built-in control loops для workloads, nodes, endpoints и других ресурсов | обычно не выполняет workload сам |
| `kube-scheduler` | для unscheduled Pod выбирает подходящую node и записывает binding | не запускает process и не переносит живой Pod между nodes |
| `kubelet` | на assigned node приводит Pod к локально требуемому состоянию через runtime и публикует status | не решает глобальное размещение |
| container runtime / CRI | создаёт Pod sandbox, images и containers | не определяет application policy |
| CNI implementation | подключает Pod к cluster network и часто реализует NetworkPolicy | наличие API NetworkPolicy не гарантирует enforcement |
| CSI driver | provision, attach и mount storage по storage contract | не выбирает retention и backup policy за владельца данных |

## Объект Kubernetes: intent, identity и observation

Почти любой объект имеет четыре слоя. `apiVersion` и `kind` выбирают schema. `metadata` содержит имя, namespace, UID, labels, annotations, owner references и generation. `spec` выражает desired state владельца. `status` сообщает наблюдаемое состояние, записанное системой или controller.

Это не просто формат YAML. Граница между `spec` и `status` определяет ownership. Пользователь не должен вручную «исправлять» status, а controller не должен тихо переписывать intent. Для custom controller полезно публиковать `observedGeneration`: тогда клиент понимает, относится ли status к последней версии spec. Conditions должны отвечать на операционные вопросы вроде `Ready`, `Progressing` и `Degraded`, а не превращаться в неструктурированный log.

Labels предназначены для выбора и группировки объектов; annotations - для дополнительной метаинформации, которую обычно не используют как selector. Namespace создаёт scope имён, RBAC и quotas, но сам по себе не даёт сильной tenant isolation: Pods разных namespaces всё ещё могут оказаться на одной node и использовать общий kernel, если platform не добавила другие boundaries.

### Минимальный manifest, который стоит уметь читать

```
apiVersion: apps/v1
kind: Deployment
metadata:
  name: ax-controller
  namespace: ax-system
spec:
  replicas: 2
  selector:
    matchLabels:
      app: ax-controller
  template:
    metadata:
      labels:
        app: ax-controller
    spec:
      serviceAccountName: ax-controller
      containers:
      - name: controller
        image: registry.example/ax-controller@sha256:...
        resources:
          requests: {cpu: "500m", memory: "512Mi"}
          limits: {memory: "1Gi"}
        startupProbe:
          httpGet: {path: /health/startup, port: 8080}
        readinessProbe:
          httpGet: {path: /health/ready, port: 8080}
        livenessProbe:
          httpGet: {path: /health/live, port: 8080}
```

Здесь Deployment владеет ReplicaSet, ReplicaSet владеет Pods, а Pod template определяет будущие экземпляры. Selector обязан соответствовать labels template. ServiceAccount задаёт workload identity. Digest фиксирует точный image content. Requests участвуют в scheduling, memory limit ограничивает cgroup, а probes отвечают на три разных вопроса о lifecycle.

## Pod: общая судьба, а не маленькая VM

Pod - минимальный deployable compute object Kubernetes. В нём один или несколько containers делят network namespace и обращаются друг к другу через `localhost`. Они могут делить volumes и часть lifecycle. Это делает Pod хорошей границей для процессов, которые должны размещаться и завершаться вместе, но плохой границей для независимых services.

Pod не является VM. Containers обычно используют kernel node. Pod IP может исчезнуть при пересоздании. Pod не «переезжает» на другую node: controller создаёт новый Pod с новым UID, а старый завершается или теряется. Локальный filesystem container не должен считаться durable state.

**Init container** выполняет подготовительный шаг до application containers: например, проверяет schema или получает configuration. **Sidecar** живёт рядом с приложением ради тесно связанной функции. Каждый sidecar увеличивает resource footprint, startup dependencies и failure surface; telemetry, proxy или secret delivery не становятся бесплатными только потому, что вынесены во второй container.

Для agent runtime Pod может содержать trusted supervisor и sandboxed execution component, но совместное размещение не отменяет security analysis. Общие volumes, localhost, process namespace и credentials способны превратить удобный sidecar в путь обхода boundary. Изоляцию следует доказывать конкретной runtime и Pod configuration, а не количеством containers.

## Как выбрать workload controller

| Ресурс | Когда выбирать | Ключевая семантика | Типичная ошибка |
| --- | --- | --- | --- |
| Deployment | stateless или externally stateful service с взаимозаменяемыми replicas | rolling update и поддержание replica count через ReplicaSet | хранить identity или единственную копию state в filesystem Pod |
| StatefulSet | нужны стабильные ordinal identity, порядок rollout или отдельные persistent claims | предсказуемые имена Pods и управляемая связь с storage | считать StatefulSet автоматической репликацией и backup базы |
| DaemonSet | по одному Pod на каждой подходящей node или на выбранном классе nodes | node-local agent, supervisor, networking или telemetry | использовать для обычного horizontally scaled API |
| Job | конечная работа должна завершиться успешно | completion, retries и parallelism для batch | считать повтор Pod гарантией exactly-once side effect |
| CronJob | периодическое создание Jobs | расписание, concurrency policy и history | полагаться на него как на единственный источник строгой бизнес-периодичности |

Для AX/Substrate типичная картина неоднородна. API/controller components естественно выглядят как Deployment. Node-local worker supervisor или runtime integration может использовать DaemonSet. Stateful backing services требуют собственного operator или внешнего managed service. Отдельная agent Task не обязана быть отдельным Kubernetes Job: если platform создаёт тысячи коротких или suspendable actors, прямое отображение «одна Task = один API object» может стать дорогим и семантически неверным.

## Scheduling: почему Pod остаётся Pending

Scheduler не спрашивает, сколько ресурсов Pod потребляет прямо сейчас. Он проверяет requests и constraints против allocatable capacity и свойств nodes. Поэтому пустая node по графику CPU всё равно может не принять Pod, если уже размещённые requests исчерпали allocatable. И наоборот, отсутствие requests позволяет overcommit, но ухудшает предсказуемость и eviction behavior.

**Request** - заявка на capacity и основа scheduling. CPU request также влияет на относительную долю CPU при contention. **Limit** - runtime ceiling: CPU обычно throttled, превышение memory limit может завершить process через OOM. Это разные механизмы; limit не резервирует capacity, а request не является жёстким потолком.

Размещение уточняют несколько инструментов:

- **nodeSelector/node affinity** притягивают workload к nodes с нужными labels, например virtualization support или local SSD;
- **taints/tolerations** отталкивают неподходящие Pods; toleration разрешает рассматривать tainted node, но не гарантирует выбор;
- **pod anti-affinity/topology spread** распределяют replicas по nodes и zones, уменьшая correlated failure;
- **priority/preemption** помогают критичным Pods получить место, но могут вытеснить менее приоритетную работу;
- **RuntimeClass** выбирает runtime handler и может добавлять scheduling constraints и Pod overhead.

Для agent workloads RuntimeClass связывает главу 8 с Kubernetes: classification решает, что hostile build требует gVisor или microVM-backed runtime, а platform policy назначает разрешённый class. Недоверенный Task не должен самостоятельно понижать isolation tier. Nodes с KVM, gVisor или GPU обычно разделяют labels/taints и admission policy, иначе Pod либо не запустится, либо попадёт на неверный runtime pool.

## Lifecycle и probes: три разных вопроса

**Startup probe** отвечает: «успело ли приложение инициализироваться?». Пока она не прошла, liveness и readiness не начинают обычную работу. **Readiness probe** отвечает: «можно ли сейчас отправлять traffic?». Её failure убирает endpoint из готовых backend Service, но не обязана перезапускать container. **Liveness probe** отвечает: «застряло ли приложение так, что требуется restart?». Слишком агрессивная liveness во время нагрузки создаёт restart storm и усиливает incident.

Для controller readiness должна учитывать способность обслуживать новые requests, а не обещать здоровье всех external dependencies сразу. Liveness не должна падать из-за временной недоступности Redis, PostgreSQL или model gateway, если process способен восстановиться без restart. Startup probe особенно важна для больших images, migrations, cache warm-up и local inference servers.

Удаление Pod - протокол, а не мгновенное исчезновение. Kubernetes начинает termination, endpoint перестаёт считаться ready, выполняется lifecycle hook при наличии, process получает termination signal и имеет grace period. Приложение должно прекратить принимать новую работу, завершить или checkpoint активные операции и освободить lease. Если agent runtime игнорирует termination, rollout превращается в потерю задач.

Pod phase `Running` означает, что Pod назначен node и хотя бы один primary container запущен или запускается; это не эквивалент application readiness. `CrashLoopBackOff` - не phase, а сообщение о backoff между повторными запусками. `OOMKilled` обычно указывает на memory boundary, но для причины нужно сопоставить last state, events, node pressure и application metrics.

## Service, DNS и реальный путь traffic

Pod IP нестабилен, поэтому клиент обычно обращается к Service. Selector Service выбирает Pods, EndpointSlice хранит актуальные backends, а service proxy или network dataplane направляет traffic. DNS создаёт удобное имя вроде `ax-controller.ax-system.svc`. Service не запускает health check сам: готовность endpoints обычно следует из Pod readiness.

Если DNS name разрешается, это доказывает только discovery. Если TCP connection устанавливается, это ещё не доказывает корректность application protocol, TLS identity или authorization. Диагностика должна идти слоями: DNS - endpoint membership - route - port - TLS - application response.

NetworkPolicy определяет разрешённые ingress/egress flows на уровне Pod selectors, namespaces, IP blocks и ports. Но policy работает только если network implementation её поддерживает и применяет. Policy не является HTTP authorization, не понимает MCP semantics и не заменяет egress proxy. Для agent sandbox особенно опасен «разрешить весь egress ради package install»: тот же путь может использоваться для exfiltration.

## Storage: lifetime важнее названия volume

Container filesystem и `emptyDir` удобны для scratch data. `emptyDir` переживает restart отдельного container внутри того же Pod, но удаляется вместе с Pod. PersistentVolume имеет lifecycle, независимый от конкретного Pod; PersistentVolumeClaim выражает запрос workload, а StorageClass описывает provisioning и operational class storage.

Наличие PVC не гарантирует durability. Нужно отдельно знать reclaim policy, replication, failure domain, snapshot support, backup, restore test, encryption и access mode. Local volume способен пережить Pod, но привязывает workload к node. Network volume снимает эту привязку ценой latency и внешней failure domain.

В agent platform полезно разделять:

- ephemeral workspace, которое можно уничтожить вместе с attempt;
- durable artifacts, которые публикуются в object store;
- runtime snapshots с отдельной compatibility и confidentiality policy;
- control-plane database state, которое восстанавливается по собственному backup contract.

Попытка смонтировать один общий PVC во все агенты упрощает demo, но создаёт race conditions, lateral access и неясное ownership. Workspace и artifact должны иметь tenant/task scope, quota и явный cleanup.

## Configuration, identity и policy

ConfigMap отделяет non-confidential configuration от image. Secret предназначен для confidential data, но base64 не является encryption. Без encryption at rest Secret может храниться в etcd незашифрованным; любой субъект с широким `get/list/watch` получает чувствительные значения. Монтирование Secret в Pod передаёт секрет процессу, а значит application обязано не логировать и не пересылать его.

ServiceAccount даёт workload identity для Kubernetes API и связанных identity systems. RBAC отвечает, какие API verbs разрешены над какими resources. Для большинства execution Pods безопасный default - `automountServiceAccountToken: false`; token нужен только workload, который действительно обращается к Kubernetes API. Controller получает отдельный ServiceAccount и минимальные namespaced или cluster permissions.

Admission выполняется до сохранения объекта. Здесь можно запретить privileged containers, host mounts, unsafe capabilities, untrusted registries, произвольный RuntimeClass и отсутствие resource requests. Pod Security Admission применяет профили Pod Security Standards на уровне namespace, но для platform-specific правил часто нужны ValidatingAdmissionPolicy или admission webhook.

Namespace - удобный policy scope, не полноценная hostile multi-tenant boundary. Для недоверенного кода дополнительно нужны runtime isolation, node separation, NetworkPolicy, egress control, scoped identity, quotas и защита control-plane endpoints. Kubernetes оркестрирует эти controls, но не создаёт автоматически правильную threat model.

## CRD и operator: когда расширять Kubernetes API

CustomResourceDefinition добавляет новый kind, а custom controller реализует его lifecycle. Это полезно, когда объект действительно является декларативным infrastructure resource: имеет сравнительно небольшую population, долгоживущий desired state, понятные ownership/finalizers и естественно управляется через Kubernetes API.

CRD не стоит использовать как удобную бесплатную database. Официальная документация прямо рекомендует не хранить в Custom Resources обычные application, end-user или monitoring data. Высокочастотные task events, model tokens, logs, traces и миллионы короткоживущих attempts создают нагрузку на API storage/watch и слишком тесно связывают application с cluster control plane.

Хороший вопрос перед созданием CRD: «должен ли cluster operator управлять жизненным циклом этого объекта через декларативный API?». Если ответ звучит как «нам просто нужен JSON store с watch», вероятно, нужен PostgreSQL, Redis, event stream или object storage.

## Граница ответственности Kubernetes, AX и Substrate

| Слой | Владеет | Не должен подменять |
| --- | --- | --- |
| Kubernetes | nodes, Pods, placement, restart, cluster networking/storage integration, workload identity и cluster policy | agent semantics, tool authorization, conversation state и task event store |
| Substrate | Actor/Worker lifecycle, runtime backend, sandbox placement, suspend/resume и snapshot contract | общий Kubernetes scheduler или application goal |
| AX | Task intent, orchestration, agent execution policy, task state/events и связь с tools | container runtime internals и node lifecycle |
| Harness/application | agent loop, prompts, context, domain state и решение о следующем действии | physical isolation и cluster-wide capacity management |

Пример цепочки: пользователь создаёт AX Task. AX сохраняет intent и просит Substrate предоставить isolated Actor. Substrate выбирает подходящий WorkerPool и physical runtime. Kubernetes обеспечивает, чтобы нужная worker capacity существовала на nodes с требуемым RuntimeClass и ресурсами. Kubelet запускает Pod, runtime создаёт sandbox. При этом task progress не обязан записываться как новый Kubernetes object на каждый model/tool step.

Эта декомпозиция важна при incident. `Pod Pending` обычно указывает на placement/resource/runtime integration. `Pod Ready, Actor not ready` указывает на Substrate lifecycle или ошибочную probe. `Actor ready, Task stuck` переводит расследование в AX, harness, model или tool layer. Без границ команда лечит каждую проблему перезапуском Pods и теряет исходную причину.

## Диагностический маршрут: от intent к процессу

1. **Найдите владельца.** Определите namespace, kind, name, labels и owner references. Не начинайте с произвольного Pod, если им управляет Deployment или DaemonSet.
2. **Сравните spec и status.** Проверьте replica counts, conditions, generation, selected node, phase и container states.
3. **Прочитайте Events.** Они часто сразу показывают FailedScheduling, image pull, mount, admission или probe failure. Помните об их ограниченном retention.
4. **Если Pod Pending,** проверьте requests, unbound PVC, node selectors, affinity, taints/tolerations, RuntimeClass, quotas и admission.
5. **Если container Waiting,** смотрите reason: image, configuration, volume, runtime sandbox или backoff.
6. **Если container перезапускается,** сопоставьте current/previous logs, exit code, signal, OOMKilled, probe и termination events.
7. **Если Pod Ready, но запрос не проходит,** проверьте Service selector, EndpointSlice, port mapping, DNS, NetworkPolicy, TLS и application authorization.
8. **Если node подозрительна,** изучите Node conditions, allocatable/allocated resources, pressure, kubelet/runtime/CNI/CSI signals и соседние Pods.
9. **Свяжите с platform ID.** Kubernetes metadata должны позволять найти AX Task, Actor, Worker и trace, не раскрывая secret data.
10. **Не исправляйте до гипотезы.** Delete Pod может временно скрыть race, OOM или bad rollout и уничтожить evidence.

### Минимальный набор read-only команд

```
kubectl get deploy,ds,sts,pod -n ax-system -o wide
kubectl describe pod <pod> -n ax-system
kubectl get events -n ax-system --sort-by=.metadata.creationTimestamp
kubectl logs <pod> -n ax-system -c <container> --previous
kubectl get pod <pod> -n ax-system -o yaml
kubectl get endpointslice -n ax-system -l kubernetes.io/service-name=<service>
kubectl describe node <node>
```

`logs --previous` особенно полезен после restart. `describe` объединяет status и недавние events, но для точной automation лучше читать structured fields через API. Доступ к debug shell, ephemeral containers и node logs должен быть отдельной привилегией и аудироваться.

## Практика: разместить hostile build pool

Представим, что AX исполняет tests из недоверенных pull requests. Из главы 8 известно, что обычный process или default container недостаточен. Теперь нужно выразить platform decision в Kubernetes.

1. Выделите nodes или node pool с поддерживаемым sandbox runtime; добавьте проверяемые labels и taint.
2. Создайте RuntimeClass для gVisor или microVM-backed handler. Укажите scheduling constraints и overhead, если integration это поддерживает.
3. Разрешите этот RuntimeClass только trusted controller через admission policy; пользовательский manifest не должен выбирать более слабый handler.
4. Задайте requests/limits для CPU, memory и ephemeral storage; PID budget обеспечьте настройкой runtime/kubelet, а namespace ограничьте ResourceQuota.
5. Отключите service account token, host namespaces, privileged mode и broad capabilities. Не монтируйте runtime socket.
6. Примените default-deny ingress/egress и точечные разрешения к dependency mirror, artifact store и control endpoint.
7. Workspace сделайте ephemeral и task-scoped; artifacts публикуйте через отдельный scoped identity.
8. Добавьте startup/readiness для worker supervisor и graceful termination, который перестаёт принимать Tasks и завершает или checkpoint активную работу.
9. Проверьте negative cases: host access, metadata, lateral traffic, token absence, resource storm, node drain и forced termination.
10. Измерьте queue time, scheduling latency, sandbox start, image pull, execution и cleanup отдельно.

Результат - не просто «Pod запущен». Готовый contract утверждает, какой runtime применён, на каких nodes, с какими ресурсами и network policy, какой Task размещён, что произойдёт при drain и какие доказательства сохраняются в audit.

## Типичные антипаттерны

**Running means ready.** Phase Pod принимают за готовность application и направляют traffic слишком рано.

**Liveness checks every dependency.** Краткая проблема внешней системы запускает restart storm.

**No requests, generous limits.** Scheduler не получает честной модели demand, а node становится непредсказуемой под нагрузкой.

**Namespace equals sandbox.** Логическое разделение имён ошибочно принимают за boundary против hostile code.

**Secret equals vault.** Base64, broad RBAC и mounted long-lived token оставляют прямой путь к credential theft.

**One Task, one CRD.** Высокочастотный application state перекладывают в etcd и watch infrastructure.

**Delete until green.** Перезапуск уничтожает evidence и маскирует race, leak или неверный capacity model.

**Latest image tag.** Rollout становится невоспроизводимым, а rollback не гарантирует прежний content.

**Service proves connectivity.** Создание Service принимают за доказательство endpoints, route, TLS и authorization.

**RuntimeClass as user input.** Недоверенный workload сам выбирает более дешёвую и слабую isolation boundary.

## Checklist перед эксплуатацией AX/Substrate в Kubernetes

1. Какие components являются Deployment, DaemonSet, StatefulSet или внешним managed service и почему?
2. Какие labels и owner references связывают Task, Actor, Worker, Pod и node?
3. Что хранится в Kubernetes API, а что в Redis, PostgreSQL, object store и telemetry backend?
4. Есть ли у status понятные conditions и связь с последней generation?
5. Заданы ли реалистичные requests, limits, quotas и Pod overhead?
6. Как workload попадает на нужный runtime/node pool и кто имеет право выбрать RuntimeClass?
7. Разнесены ли replicas по failure domains?
8. Что именно проверяют startup, readiness и liveness?
9. Как проходит graceful termination при rollout, drain и scale-down?
10. Какие данные исчезают вместе с Pod, а какие восстанавливаются после потери node?
11. Поддерживает ли CNI применяемые NetworkPolicy и проверен ли default-deny?
12. Отключён ли service account token там, где Kubernetes API не нужен?
13. Ограничены ли RBAC, Secret access, privileged settings, host mounts и admission exceptions?
14. Есть ли read-only диагностический маршрут и отдельный audited break-glass path?
15. Проверены ли node pressure, OOM, image pull, CNI/CSI failure, drain и control-plane outage?

## Итоги главы

- Kubernetes хранит intent и непрерывно reconciles actual state; успешный API response не равен готовому workload.
- API server, scheduler, controllers, kubelet, runtime, CNI и CSI образуют цепочку с разными обязанностями и failure modes.
- `spec` принадлежит intent, `status` отражает observation; conditions должны быть операционно значимыми.
- Pod - группа совместно размещённых containers с общей судьбой, но не VM и не durable identity.
- Deployment, StatefulSet, DaemonSet и Job выбираются по lifecycle semantics, а не по привычке команды.
- Requests управляют scheduling и долей ресурсов; limits задают runtime ceilings; отсутствие честной модели ломает capacity planning.
- Startup, readiness и liveness отвечают на разные вопросы; ошибочная probe способна создать incident.
- Service стабилизирует discovery над меняющимися Pods, а NetworkPolicy требует реального enforcement со стороны network implementation.
- PVC не равен backup, Secret не равен vault, Namespace не равен hostile tenant boundary.
- RuntimeClass связывает workload classification с physical isolation, но выбор должен контролироваться platform policy.
- CRD подходит для декларативного infrastructure resource, а не для потока task events, logs или model tokens.
- Kubernetes владеет cluster execution substrate; Substrate - Actor/Worker runtime; AX - Task orchestration; harness - agent semantics.
- Диагностика идёт от owner/spec/status через events и container state к network, storage и node, сохраняя evidence.

**Дальше.** Kubernetes показал, как desired state превращается в Pods и почему observation всегда немного отстаёт от intent. Следующая глава разберёт общие законы control plane и distributed systems: eventual consistency, idempotency, retries, locks, streams и восстановление после частично выполненных операций.

### Источники и дальнейшее чтение

- [Kubernetes Components](https://kubernetes.io/docs/concepts/overview/components/) - роли control plane и node components.
- [Objects in Kubernetes](https://kubernetes.io/docs/concepts/overview/working-with-objects/) - object model, spec, status и record of intent.
- [Controllers](https://kubernetes.io/docs/concepts/architecture/controller/) - reconciliation и controller pattern.
- [Workloads](https://kubernetes.io/docs/concepts/workloads/) - Pods и workload resources.
- [Pod Lifecycle](https://kubernetes.io/docs/concepts/workloads/pods/pod-lifecycle/) - phases, container states, restart и termination.
- [Liveness, Readiness and Startup Probes](https://kubernetes.io/docs/concepts/workloads/pods/probes/) - semantics probes и предупреждение о cascading failures.
- [Resource Management for Pods and Containers](https://kubernetes.io/docs/concepts/configuration/manage-resources-containers/) - requests, limits, scheduling и cgroups.
- [Scheduling, Preemption and Eviction](https://kubernetes.io/docs/concepts/scheduling-eviction/) - placement constraints и disruptions.
- [RuntimeClass](https://kubernetes.io/docs/concepts/containers/runtime-class/) - runtime handler, scheduling и overhead.
- [Services, Load Balancing and Networking](https://kubernetes.io/docs/concepts/services-networking/) - Pod network, Services, EndpointSlices, CNI и NetworkPolicy.
- [Storage](https://kubernetes.io/docs/concepts/storage/) - ephemeral и persistent storage abstractions.
- [Service Accounts](https://kubernetes.io/docs/concepts/security/service-accounts/) - workload identity и least-privilege RBAC.
- [Good Practices for Kubernetes Secrets](https://kubernetes.io/docs/concepts/security/secrets-good-practices/) - encryption, access и ограничения base64.
- [Pod Security Admission](https://kubernetes.io/docs/concepts/security/pod-security-admission/) - enforcement Pod Security Standards.
- [Custom Resources](https://kubernetes.io/docs/concepts/extend-kubernetes/api-extension/custom-resources/) - назначение CRD и рекомендация не использовать Kubernetes API как application data store.
- [AX Core Concepts](https://github.com/google/ax/blob/main/docs/concepts.md) - Task и declarative API AX.
- [Agent Substrate](https://github.com/agent-substrate/substrate) - Actor/Worker model и Kubernetes-backed runtime.
