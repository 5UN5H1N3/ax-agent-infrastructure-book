# Production engineering: как доказать HA, persistence, upgrades и DR

**Снимок главы:** **AX main `ac233282` · Substrate main `df78882` · проверено 2026-10-10**. Оба проекта pre-1.0. Реализованные механизмы, development manifests и рекомендуемая production architecture ниже разделены явно.

В лаборатории достаточно увидеть, что Task создался, Actor проснулся и состояние пережило suspend/resume. Production задаёт более неприятные вопросы. Что произойдёт, если node исчезнет во время snapshot? Можно ли обновить dataplane, не потеряв активных Actors? Сколько последних минут состояния допустимо потерять? Как отличить восстановленный Task от повторного выполнения внешнего платежа или публикации?

Ответ «у нас по три replicas» на эти вопросы не отвечает. Доступность API, сохранность данных, возможность восстановления и безопасность повторного исполнения - разные свойства. Их проектируют отдельно, а затем связывают общим recovery contract.

![Production-like topology: availability зависит от всей цепочки stateful и stateless planes](.gitbook/assets/diagrams/27-25.png)  
*Production-like topology: availability зависит от всей цепочки stateful и stateless planes*

## Четыре разных свойства production-системы

Инженеры часто используют слово «надёжность» для всего сразу. Это скрывает решения.

| Свойство | Вопрос | Чем доказывается |
| --- | --- | --- |
| Availability | Принимает ли система новые запросы во время отказа? | SLO, multi-zone topology, failover test, dependency health |
| Durability | Сохранится ли уже подтверждённое состояние? | replication, backup policy, checksum, retention и immutability |
| Recoverability | Можно ли вернуть согласованную систему после разрушения? | проверенный restore, RTO/RPO и dependency order |
| Execution safety | Не повторит ли восстановленный агент внешний side effect? | idempotency key, operation ledger, reconciliation и human review |

Replica отвечает только на часть availability. Она не исправляет логическую порчу данных, удаление operator-ом, несовместимую schema migration или повторный tool call. Реплика может очень быстро распространить ошибку. Поэтому **replication не заменяет backup, а backup не считается защитой, пока restore не проверен**.

## RPO, RTO и failure domain

**RPO** - сколько подтверждённого состояния допустимо потерять. **RTO** - сколько времени допустимо восстанавливать service. Эти цели задаются отдельно для каждого plane. Требование «RPO 0 и RTO 30 секунд для всего» обычно означает синхронную replication, горячий standby и высокую стоимость, но всё равно не решает duplicate external effects.

**Failure domain** - набор компонентов, которые могут исчезнуть одной причиной: process, Pod, node, rack/zone, cluster, region, cloud account или credentials domain. Две replicas на одном node защищают от падения process, но не node. PostgreSQL replica в той же zone не защищает от zone outage. Backup в том же account может быть удалён теми же скомпрометированными credentials.

| Plane | Пример цели | Главная цена | Что проверить |
| --- | --- | --- | --- |
| AX API/status | короткий RTO, небольшой RPO desired state | репликация store и безопасный multi-writer | failover не создаёт два Actor для одного Task |
| Substrate lifecycle | Actor metadata доступна после node loss | PostgreSQL HA/PITR и согласованность snapshot references | Actor можно найти, revert/resume работает по контракту |
| Actor snapshots | потеря не более последнего допустимого checkpoint | object durability, bandwidth и retention | blob читается, checksum верен, runtime совместим |
| Audit/evidence | не терять security и side-effect evidence | append-only storage и отдельный trust domain | можно доказать, какие действия уже выполнены |

Числа в таблице должны стать вашими числами. Сначала product owner определяет ущерб от потери и недоступности; затем архитектура выбирает replication, backup frequency и reserve. Не наоборот.

## Инвентаризация состояния: что является source of truth

DR начинается не с команды backup, а с ответа «какое состояние authoritative и кто им владеет?» Для AX/Substrate минимум выглядит так:

| Состояние | Владелец | Можно ли пересоздать? | Что нужно для восстановления |
| --- | --- | --- | --- |
| Task/Workspace/Model specs, status, events | AX store | specs - из Git только при строгом GitOps; live status - нет | backup Redis/другого production store, schema version, manifests |
| Actor, assignment, lifecycle metadata | Substrate/PostgreSQL | не безопасно угадывать | database backup/PITR, migrations, Actor IDs |
| Memory/filesystem checkpoints | Substrate/object store | обычно нет | snapshot blobs, metadata, checksum, runtime/image compatibility |
| Worker Pods и caches | Kubernetes/dataplane | да | WorkerPool spec, images, node labels, capacity |
| Images и sandbox assets | registry/release pipeline | только из reproducible build | immutable digest, provenance, old and new release images |
| Secrets, CA, signing keys | identity/secret system | не всегда | separate encrypted backup, rotation and revocation procedure |
| External side effects | внешняя authoritative system | не часть cluster restore | operation ID, idempotency key, receipt и reconciliation API |

Особенно опасна связь PostgreSQL metadata с object blobs. База может утверждать, что snapshot существует, пока blob ещё не завершён или уже удалён lifecycle policy. И наоборот, object store может содержать orphan blobs. Backup этих систем в разные моменты времени не образует автоматически согласованную recovery point.

Зафиксируйте recovery manifest: cutoff time, версии схем, database backup ID, object-store version/generation, registry digests и key versions. Если атомарный cross-store snapshot невозможен, restore procedure должна уметь выбрать последний полностью завершённый checkpoint и отбрасывать incomplete references.

## Что реально развёртывают текущие manifests

Нельзя читать README quickstart как production blueprint.

- На снимке AX `ac233282` manifest `deploy/ax-server.yaml` задаёт одну replica `ax-server`.
- `deploy/redis.yaml` запускает одиночный Redis как `Deployment` без PVC и без настроенной persistence/replication.
- Substrate development quickstart устанавливает bundled PostgreSQL и RustFS, удобные для локальной проверки, но не объявляет их готовой HA/backup architecture.
- AX использует `v1alpha1` resources и прямо предупреждает о breaking changes до стабильного релиза.
- Substrate содержит конкретный rolling-upgrade runbook, однако compatibility всё равно определяется выбранными release pair и documented preconditions.

Следствие простое: **текущий quickstart нельзя сделать production-ready добавлением одного Helm value**. Нужно выбрать production stores, identity, topology, backup, upgrade authority и наблюдаемость, а затем проверить интеграцию. Если конкретный компонент не документирует multi-replica semantics, не предполагайте, что горизонтальное масштабирование безопасно.

## HA - свойство цепочки, а не Deployment

### Stateless API всё равно имеет stateful dependencies

Несколько API replicas полезны только если:

- requests можно безопасно отправить любой replica;
- leader election/locks исключают двойную reconciliation там, где это нужно;
- store выдерживает concurrent clients и имеет согласованный failover;
- retry не превращает timeout в duplicate create/tool action;
- load balancer проверяет readiness, а не только открытый TCP port;
- replicas распределены по реальным failure domains.

Readiness должна означать способность обслужить полезный запрос. Процесс с живым HTTP endpoint, но без Redis, PostgreSQL, Substrate API или signing service, не Ready.

### PDB защищает не от всех отказов

PodDisruptionBudget ограничивает voluntary evictions, например при controlled node drain. Он не предотвращает падение node, kernel panic, потерю zone или удаление Pods напрямую. PDB с `minAvailable: 2` также бесполезен, если все replicas оказались на одном node. Нужны topology spread/anti-affinity, достаточное число nodes и capacity, чтобы replacement действительно разместился.

### Stateful stores добавляют собственную distributed system

PostgreSQL HA, Redis replication/Sentinel/Cluster или managed equivalents несут свои quorum, failover, fencing и lag semantics. Выбор должен исходить из RPO/RTO и write model AX/Substrate, а не из привычки команды. После failover клиент обязан переподключиться к новому writer, не записав одновременно в старый split-brain endpoint.

Для PostgreSQL PITR требует base backup и непрерывного архива WAL. Для Redis RDB и AOF дают разные компромиссы durability/performance. Название технологии не является политикой: зафиксируйте режим, fsync/backup interval, retention, restore command и измеренный worst-case data loss.

## Backup design: три копии недостаточно без semantics

Рабочая политика отвечает на вопросы:

1. Что именно входит в backup и какой plane намеренно исключён?
2. Какой consistency point связывает database и object storage?
3. Где хранятся encryption keys и кто может удалить backup?
4. Как обнаруживается silent corruption?
5. Как часто выполняется restore test и кто подписывает результат?
6. Можно ли восстановить старую release, если новая уже изменила schema?

Храните backup вне основного failure и credentials domain. Используйте versioning/object lock там, где threat model требует защиты от ransomware или operator deletion. Проверяйте не только наличие файла, но и checksum, decryptability, schema migration и запуск минимального workload после restore.

Kubernetes objects также требуют стратегии. GitOps repository восстанавливает declared resources, но не status, dynamically created objects, Secrets и данные PVC. Для self-managed Kubernetes нужен отдельный план etcd backup; managed control plane не освобождает от backup application state.

## Upgrade engineering: совместимость важнее порядка команд

Upgrade AX/Substrate пересекает несколько version boundaries одновременно:

- Kubernetes API и node version skew;
- AX `v1alpha1` resource schema, CLI и server;
- Substrate controller, API, atelet и Worker images;
- PostgreSQL schema migrations;
- snapshot format и sandbox runtime;
- runner contract, Workspace assets и Model/tool protocols;
- identity/trust bundle и credential provider.

Compatibility matrix должна быть release artifact. Для каждой пары old/new укажите: поддерживается ли mixed-version window, можно ли читать old snapshot, можно ли rollback database, какие CRD fields меняются, какой component обновляется первым и какие tests обязательны.

### Безопасный общий протокол

1. **Freeze scope:** зафиксировать old/new SHA, images/digests, migrations, feature flags и owners.
2. **Проверить preconditions:** stores healthy, backups restorable, WorkerPools имеют reserve, нет активного incident.
3. **Создать recovery point:** согласованный backup и inventory, а не только VM snapshot.
4. **Развернуть staging/canary:** прогнать create, route, suspend, resume, revert, delete, egress и side-effect tests.
5. **Expand before contract:** сначала backward-compatible schema/API, затем новые writers, и только после полного перехода удалять старое.
6. **Roll по failure domain:** одна node/zone cohort за шаг, с выдержкой и наблюдением SLO.
7. **Проверить state compatibility:** old и new Actors/snapshots в обе стороны только там, где это обещано.
8. **Закрыть mixed-version window:** удалить old capacity после подтверждённой устойчивости, сохранить rollback assets.

### Конкретика Substrate на снимке df78882

Зафиксированный rolling-upgrade runbook задаёт порядок: новый `ate-controller`, затем versioned dataplane node за node, затем остальной control plane. Nodes и WorkerPools привязаны label `ate.dev/substrate-version`. Serving WorkerPool не редактируют на месте: его клонируют под новую worker image/version, потому что обычное изменение Deployment может прокатиться через живых Actors.

Перед заменой node workers должны быть корректно drained/suspended. Удаление worker Pod не эквивалентно сохранению Actor: если suspend не завершился в предусмотренное окно, Actor может перейти в `CRASHED`, а recovery потребует `RevertActor` к последнему внешнему snapshot с потерей изменений после него. На single-node cluster rolling upgrade неизбежно становится outage.

```
kubectl get ds -n ate-system -l app=atelet \
  -L ate.dev/substrate-version
kubectl get nodes -L ate.dev/substrate-version
kubectl get workerpools -A
```

Эти команды показывают progress, но не доказывают correctness. После каждой cohort нужны lifecycle tests и outcome SLI. Не импровизируйте порядок по общей Kubernetes-интуиции: используйте runbook именно выбранной Substrate release.

### Rollback может быть невозможен

Rollback binary безопасен только если old version понимает новую schema, snapshots и stored data. После destructive migration единственный путь назад может состоять из остановки writes и восстановления pre-upgrade backup, то есть с потерей данных после recovery point. Поэтому у каждого шага должен быть заранее выбран один вариант: instant rollback, roll-forward fix или restore. Фраза «если что откатим image» без этой классификации опасна.

## Capacity reserve как часть HA

Кластер, работающий на 100% average utilization, уже не высокодоступен. При потере node оставшиеся nodes должны принять control-plane Pods, replacement workers и активных Actors. Во время drain появляются suspend/resume I/O, object-store burst и дополнительные reconciliation events. Model rollout может временно держать две копии weights в GPU memory.

Reserve рассчитывается по сценарию, а не фиксированным «20%»:

- N+1 node или потеря целой zone;
- максимальная upgrade cohort;
- resume burst после gateway/region recovery;
- snapshot/checkpoint bandwidth и database compaction;
- image pull/cache miss после появления чистых nodes;
- inference queue при недоступности части GPU pool.

Control plane, state stores, WorkerPools и inference лучше иметь в разных capacity pools с priority/taints. Иначе agent workload может вытеснить компоненты, которые нужны, чтобы этот же workload остановить или восстановить.

## Disaster recovery: восстанавливайте причинную цепочку

DR plan должен различать сценарии: потеря node, zone, cluster/region; corruption store; ошибочное удаление; compromise credentials; потеря object store; недоступность external model/tool. Для каждого сценария нужны trigger, incident commander, RPO/RTO, точка остановки writes и authority на failover.

### Порядок восстановления

1. **Contain:** остановить writers и автоматические retries, отозвать скомпрометированные credentials, зафиксировать cutoff.
2. **Поднять trust foundation:** DNS, networking, KMS/secret store, CA/trust bundles и registry access.
3. **Восстановить Kubernetes foundation:** cluster, storage classes, admission/network policies и system workloads.
4. **Вернуть immutable artifacts:** exact AX/Substrate/worker/runner images и configs нужной release.
5. **Восстановить object snapshots и PostgreSQL:** до согласованного recovery point; проверить references/checksums.
6. **Запустить Substrate:** сначала control/state plane, затем WorkerPools; не принимать traffic до lifecycle smoke test.
7. **Восстановить AX state:** manifests/status/events или replay authoritative specs; сопоставить Tasks с Actors.
8. **Reconcile external side effects:** по operation ledger узнать, что уже произошло; не повторять tool calls вслепую.
9. **Открывать traffic поэтапно:** synthetic Task, canary tenant, затем cohorts с наблюдением SLO.

Порядок может отличаться в вашей реализации, но dependencies должны быть явными. Запуск AX до доступности authoritative Substrate state может создать ложную картину отсутствующих Actors. Resume до проверки snapshot/runtime compatibility превращает recovery в дополнительное повреждение.

### Минимальный DR drill

Раз в оговорённый период восстановите не пустой control plane, а проверяемую историю:

1. создайте Task, выполните локальное изменение и внешний idempotent side effect;
2. suspend Actor и запишите correlation/operation IDs;
3. создайте согласованный backup и уничтожьте isolated test environment;
4. восстановите environment только по runbook и сохранённым artifacts;
5. проверьте Task ↔ Actor mapping, snapshot state и отсутствие duplicate side effect;
6. измерьте фактические RPO/RTO, ручные шаги и missing credentials;
7. исправьте runbook и повторите drill, пока результат не станет воспроизводимым.

Backup success metric - не число загруженных bytes. Это доля успешных restore drills, фактический RPO/RTO и количество необъяснимых расхождений после reconciliation.

## Production readiness gate

Перед допуском реального workload команда должна показать:

- dependency/state inventory с owner для каждого plane;
- измеримые availability SLO, RPO и RTO;
- topology по node/zone/credentials domains, а не только replica count;
- restorable backups PostgreSQL, AX store, object snapshots, identity и manifests;
- immutable release BOM и compatibility matrix;
- проверенный Substrate drain/upgrade path и отдельный AX migration plan;
- capacity reserve для node/zone loss и rolling upgrade;
- operation ledger/idempotency для внешних side effects;
- успешный restore drill с evidence и подписанными результатами;
- runbooks, доступные при недоступности основного cluster и observability backend.

## Итог

Production engineering AX/Substrate начинается не с replica count. Сначала система разделяется на stateful planes и failure domains. Для каждого plane назначаются source of truth, RPO/RTO, backup и restore owner. Затем проектируются multi-replica semantics, capacity reserve, compatibility matrix и порядок recovery.

Текущие development manifests AX и Substrate дают работающую основу для экспериментов, но не готовую production topology. Особенно важно не приписывать persistence одиночному Redis без PVC, HA - одному PostgreSQL, а rollback - простой замене image. Substrate уже документирует сложный versioned rolling path; его детали показывают, почему live Actors нельзя обслуживать обычным бездумным Deployment rollout.

Когда failure и recovery доказаны, можно честно обсуждать производительность. Следующая глава разложит latency, throughput, active ratio, resume storms, store QPS и object bandwidth, не оптимизируя систему ценой её восстановимости.

### Источники и дальнейшее чтение

- [Substrate rolling upgrade runbook на снимке df78882](https://github.com/agent-substrate/substrate/blob/df788825e6fc13dd7aa5fd283c8f14bd4c818247/docs/upgrade.md)
- [Substrate architecture на снимке df78882](https://github.com/agent-substrate/substrate/blob/df788825e6fc13dd7aa5fd283c8f14bd4c818247/docs/architecture.md)
- [Substrate threat model на снимке df78882](https://github.com/agent-substrate/substrate/blob/df788825e6fc13dd7aa5fd283c8f14bd4c818247/docs/threat-model.md)
- [AX development Redis manifest на снимке ac233282](https://github.com/google/ax/blob/ac2332829f22360ff97b0ba34d94dd0dd782f17e/deploy/redis.yaml)
- [AX server deployment на снимке ac233282](https://github.com/google/ax/blob/ac2332829f22360ff97b0ba34d94dd0dd782f17e/deploy/ax-server.yaml)
- [Kubernetes: highly available clusters with kubeadm](https://kubernetes.io/docs/setup/production-environment/tools/kubeadm/high-availability/)
- [Kubernetes: disruptions и PodDisruptionBudget](https://kubernetes.io/docs/concepts/workloads/pods/disruptions/)
- [Kubernetes version skew policy](https://kubernetes.io/releases/version-skew-policy/)
- [Kubernetes: etcd backup and recovery](https://kubernetes.io/docs/tasks/administer-cluster/configure-upgrade-etcd/)
- [PostgreSQL continuous archiving и PITR](https://www.postgresql.org/docs/current/continuous-archiving.html)
- [Redis persistence: RDB и AOF](https://redis.io/docs/latest/operate/oss_and_stack/management/persistence/)
