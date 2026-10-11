# Production security, secrets и disaster recovery

Представьте: ночью пропал кластер. Команда восстановила Redis, PostgreSQL и snapshots, дождалась зелёных health checks и разрешила Tasks продолжить работу. Один агент снова создал уже существующий тикет, другой проснулся со старым token, третий получил права, которые у пользователя отозвали вчера. Данные удалось вернуть, но безопасную систему - ещё нет.

**Disaster recovery, DR**, возвращает сервис к полезной работе после потери инфраструктуры или состояния. Для агентной платформы полезная работа означает не просто генерацию текста: восстановленный исполнитель должен знать, какие действия уже произошли, какие права действуют сейчас и откуда можно продолжить Task. Поэтому безопасность и восстановление нельзя проектировать независимо.

Глава 41 дала запас мощности для штатной нагрузки и отказа части inference. Здесь разберём жизненный цикл credentials, состав recovery point и допуск восстановленных workloads к действиям. Модель угроз и approval contract опираются на главы 23-24, совместимость snapshots - на главу 39. Эта глава превращает их в процедуры, которые можно проверить на стенде.

## Разделите отказ сервиса и потерю доверия

Не всякий инцидент требует полного DR. Потеря Worker при сохранных stores отличается от удаления metadata, повреждения object storage или компрометации ключа администратора. В первом случае может хватить размещения на новом Worker. Во втором нужно выбирать recovery point. В третьем восстановление старой копии способно вернуть и доступ атакующего.

**HA** поддерживает доступность при предусмотренных отказах. **Replication** копирует изменения между экземплярами; ошибочное удаление она тоже может скопировать. **Backup** удерживает пригодную прошлую точку, из которой можно восстановиться. **Cyber recovery** дополнительно требует чистой доверенной среды и устранения скомпрометированных identities. Эти механизмы дополняют друг друга, но ни один не заменяет остальные.

В runbook для каждого сценария укажите, кто объявляет инцидент, кто останавливает новые Tasks и внешние writes, кто выбирает точку восстановления и кто разрешает возврат traffic. Такая договорённость важна именно ночью, когда техническая возможность включить сервис не равна полномочию принять риск потери данных или повторного действия.

Не выдавайте quickstart за production boundary. В версии исходников AX `ac233282` [инициализация gRPC server](https://github.com/google/ax/blob/ac2332829f22360ff97b0ba34d94dd0dd782f17e/internal/server/server.go) сама не добавляет transport credentials и authn/authz interceptors; внешний контроль доступа требует отдельного решения. В [документации Substrate v0.3.0](https://github.com/agent-substrate/substrate/blob/ccecc788a327dc11dcd6c21ee153f3d0cbb5cc97/docs/authentication.md) authorization и RBAC ещё не реализованы: actor identity provider имеет отдельное ограничение, но остальные authenticated providers получают полный контроль над control plane. Зафиксируйте фактические image digests и проверки отказа неавторизованным вызовам в своей установке. Перенос в новый cluster не улучшает исходную модель доступа автоматически.

## Составьте паспорт каждого credential

**Credential** - предъявляемое доказательство доступа: token, пароль, client certificate или иной материал. **Secret** - чувствительные данные, которые нужно защищать; не всякий secret предоставляет доступ, а право на действие не сводится к наличию token. Credential подтверждает identity или делегирование, но authorization ещё должна разрешить конкретную операцию.

Для каждого credential запишите, кто его выпускает (**issuer**) и какой процесс или человек его использует (**holder**), а также audience, scope, TTL, механизм refresh и revoke, владельца rotation и места хранения. **Audience** определяет сервис, для которого credential выпущен; **scope** - разрешённую область использования. Model API key не должен становиться ключом к MCP tools, а token одного пользователя - общим доступом всех Tasks.

| Назначение | Кто использует | Граница |
| --- | --- | --- |
| Control-plane administration | Операторы и controllers | Не выдавать runner; изменение policy и state отдельно аудируется |
| Model и MCP access | Gateway, adapter или runner | Отдельные destinations, tenant и операции |
| User delegation | Исполнитель конкретного run | Текущие права пользователя, ограниченный срок и возможность отзыва |
| Registry pull | Инфраструктура загрузки image | Read-only доступ к нужным artifacts, не публикация новых images |
| Backup и encryption | Backup service и recovery operators | Read/write/delete/decrypt разделены по ролям |

Разделение уменьшает **blast radius** - объём ущерба при утечке одного credential. Оно также делает восстановление понятнее: можно заменить доступ runner, не уничтожая возможность читать зашифрованный backup. Универсальный административный ключ в каждом Actor устраняет обе границы.

## Выберите способ выдачи secrets под задачу

Начните с вопроса: должен ли workload вообще видеть secret? Если tool broker способен аутентифицировать Task, проверить операцию и обратиться к upstream сам, credential может остаться у broker. Но тогда broker становится доверенным посредником: он обязан не только скрывать bytes, но и ограничивать доступ. Один общий upstream key без проверки tenant и object ID оставляет возможность confused deputy из главы 23.

**Kubernetes Secret** подходит для интеграции Kubernetes workloads с существующими credentials. Это не шифрование по факту имени: base64 лишь кодирует bytes. Нужны ограничения API-доступа и проверенная encryption at rest. Обычный Secret volume обновляется с задержкой, а mount через `subPath` не получает автоматические обновления; приложение должно перечитать файл и обновить client. Значение, скопированное в environment процесса, само не меняется. Эти условия описаны в [документации Secrets](https://kubernetes.io/docs/concepts/configuration/secret/).

**External secret store** полезен для централизованного audit, разграничения доступа и независимой rotation. Он может доставлять статический credential в workload; это ещё не делает credential short-lived. **Dynamic credentials** выпускаются по identity потребителя с ограниченным сроком. Например, Vault управляет lease, renew и revoke для dynamic secrets; обычное хранение значения в KV backend не предоставляет такую же lease-семантику. Различие важно при выборе и объясняется в [документации Vault](https://developer.hashicorp.com/vault/docs/concepts/lease).

**Workload identity** позволяет обменивать identity workload на нужный доступ без универсального долгоживущего пароля. Она всё равно зависит от issuer, audience checks и authorization конечного сервиса. Projected ServiceAccount tokens в Kubernetes ограничены по сроку и обновляются kubelet; сохранённая копия token в памяти или snapshot не обязана обновиться вместе с файлом. Workload без задачи обращаться к Kubernetes API не должен получать автоматический API token без причины; соответствующие механизмы описаны в [руководстве Service Accounts](https://kubernetes.io/docs/concepts/security/service-accounts/).

Выбор - компромисс между exposure и зависимостями. Прямой mount прост, но secret попадает в процесс. Broker уменьшает exposure у Actor, но добавляет критический сервис. Dynamic access уменьшает окно утечки, но требует работающего refresh path. Для каждого варианта испытайте отказ issuer/store и определите, какие действия блокируются: недоступность authorization не должна открывать доступ по умолчанию.

## Проверьте rotation на всех копиях credential

**Rotation** выпускает новый материал и переводит consumers на него. **Revocation** прекращает действие старого. Обновить запись в secret store недостаточно: старый credential может оставаться в environment, client object, connection pool, gateway cache, shell history или snapshot. Некоторые виды уже открытых sessions требуют отдельного закрытия.

Для плановой смены сначала обеспечьте доставку нового credential, проверьте consumers, затем отзовите старый и докажите отказ на контролируемом canary. Допустимый overlap зависит от provider. При компрометации приоритет другой: ограничить вред, даже если немедленный revoke временно нарушит доступность. Время до фактического прекращения доступа измеряется, а не выводится из TTL записи.

В Substrate credential injection и policy caches имеют конкретные ограничения версии, разобранные в главе 40. Не считайте, что отсутствие secret у Actor доказывает запрет любых upstream операций, а сохранение policy мгновенно закрывает уже разрешённый stream. Проверяйте результат у конечного сервиса и поведение fresh request, existing session и resume отдельно.

После длительного suspend harness должен заново проверить identity, срок доступа, текущие права и состояние незавершённой операции. Ошибка `401` может потребовать refresh; `403` может означать изменившееся разрешение. Ни один код не является командой бесконечно повторять model call или заново выполнять tool write. Старый approval тоже не даёт вечного права: после restore должны проверяться его срок, binding к действию и действующая policy.

Full snapshot может содержать secrets в RAM, а Data snapshot - в сохранённых файлах. Зашифровать payload полезно, но это не удаляет credential из него. Относитесь к snapshots как к чувствительным artifacts с отдельными readers и retention. Старые keys для decrypt могут понадобиться до окончания срока backup; это не причина возвращать старый API credential в работу.

## Tenant isolation должна переживать восстановление

Namespace помогает организовать Kubernetes resources, но не даёт полную tenant isolation. Помимо RBAC нужны ограничения создания workloads и использования service accounts, admission policies, sandbox boundary, egress, разделение storage и quota. После DR эти controls восстанавливаются до пользовательских workload, а не добавляются вслед за первым успешным Task.

Для Secrets недостаточно запретить только `get`. Доступ `list/watch` может раскрывать значения, а право создать Pod с mount нужного Secret даёт косвенный путь чтения. Это прямо отмечено в [практиках безопасности Kubernetes](https://kubernetes.io/docs/concepts/security/secrets-good-practices/). Проверяйте direct API access и транзитивные возможности; один ответ RBAC-проверки не описывает всю границу.

Следующий пример проверяет только authorization в отдельном учебном стенде. Он не выводит secret values и не создаёт Pod. Context, namespace и ServiceAccount заранее подготовлены; оператору требуется право impersonation. Ошибку impersonation нельзя принять за доказанный запрет для runner.

```
kubectl --context ax-dr-lab -n ax-dr-lab auth can-i get secrets \
  --as=system:serviceaccount:ax-dr-lab:runner
kubectl --context ax-dr-lab -n ax-dr-lab auth can-i list secrets \
  --as=system:serviceaccount:ax-dr-lab:runner
kubectl --context ax-dr-lab -n ax-dr-lab auth can-i create pods \
  --as=system:serviceaccount:ax-dr-lab:runner
```

Для profile, которому не нужны Kubernetes Secrets и создание Pods, ожидается `no` во всех трёх случаях. Затем проверяются реальный sandbox, broker и чужой tenant через безопасные canaries. Debug access, backup readers и restore operators тоже входят в threat model: привилегия посмотреть snapshot иногда опаснее обычного model key.

## Backup удерживает граф зависимостей

Сначала составьте recovery inventory: какие данные authoritative, какие можно воспроизвести и какие нужны лишь для расследования. Git возвращает объявленную конфигурацию только в пределах того, что действительно хранилось в Git. Он не заменяет live state, содержимое volumes, receipts внешних операций и динамически созданные objects.

| Слой | Что должно пережить потерю площадки | Проверка |
| --- | --- | --- |
| Deployment | Manifests, схемы, exact images и configs | Artifacts доступны и совместимы, а не только указаны digests |
| AX и Substrate state | Resource records, Actor/Tag metadata, mappings и migrations | Tasks ссылаются на правильные Actors и completed checkpoints |
| Workspaces и snapshots | Payloads, manifests, borrowed/golden dependencies | Полные referenced prefixes читаются и проходят integrity checks |
| Trust и keys | Configs issuer/policy, decrypt key versions, recovery access | Доступен bootstrap и decrypt; старые grants не оживают автоматически |
| Actions и audit | Operation ledger, idempotency mapping, receipts | Внешний outcome можно установить без повторного write |

Согласованность здесь означает пригодность связей, а не равенство timestamps всех файлов. PostgreSQL metadata может ссылаться на незавершённый или удалённый payload. Clone может зависеть от чужого Tag, а тот - от golden baseline. Запись URI не содержит bytes по этому URI. Состав и ownership snapshot описаны в главе 39; backup должен удержать нужные зависимости независимо от обычного lifecycle cleanup.

Если доступны coordinated checkpoints, фиксируйте завершение application state и payload до включения ссылок в recovery point. Если общий atomic snapshot невозможен, нужен валидатор references и процедура выбора последнего пригодного checkpoint. Не переписывайте запись на «похожий» blob: чужой payload с подходящим размером не становится состоянием вашего Actor.

## Выберите persistence и проверьте доступ к копиям

**PostgreSQL PITR**, point-in-time recovery, восстанавливает database через base backup и непрерывный архив **WAL** - журнала изменений. Пропущенный segment может сделать требуемую точку недоступной. WAL не заменяет отдельное резервирование вручную изменяемых database configs. Эти условия описаны для [PostgreSQL 17](https://www.postgresql.org/docs/17/continuous-archiving.html); параметры и инструменты проверяются по вашей версии. PITR одной базы не восстанавливает object storage и operation ledger других систем.

В Redis **RDB** удерживает снимок состояния, а **AOF** записывает операции для восстановления. Режимы различаются частотой сохранения и fsync; `appendfsync everysec` допускает потерю последних записей при отказе. Это не универсальное обещание RPO в одну секунду для всей платформы. Для современных multipart AOF нужно сохранять согласованный набор его files и manifest, а не один удобный файл. Условия разобраны в [руководстве persistence Redis](https://redis.io/docs/latest/operate/oss_and_stack/management/persistence/).

В AX baseline `ac233282` [development manifest Redis](https://github.com/google/ax/blob/ac2332829f22360ff97b0ba34d94dd0dd782f17e/deploy/redis.yaml) не настраивает durable volume и production persistence. Наличие работающего Redis Pod не доказывает наличие recovery point. Выбор production store требует проверенного восстановления schema и write semantics AX, как в главе 27.

Храните копии вне выбранного failure domain и отдельно ограничивайте право их удалить. Backup account с тем же административным credential, который скомпрометирован в production, не независим. Versioning, immutability и retention помогают лишь при корректных permissions и сохранном доступе к ключам. Backup writer не обязан иметь право массового delete, а workload - право читать чужие recovery points.

Контрольная сумма выявляет изменение bytes относительно известного значения, но сама не доказывает происхождение backup. Если атакующий заменил и файл, и записанный рядом hash, сравнение пройдёт. Для cyber recovery нужны доверенный каталог или проверяемая подпись, раздельные права на artifacts и evidence, а также выбор точки до компрометации. Подлинный backup тоже может содержать уже повреждённое состояние; его пригодность подтверждается проверками приложения.

Шифрование добавляет dependency. В Kubernetes KMS envelope encryption данные шифруются DEK, защищённым ключом KEK из KMS; наличие encrypted etcd backup без нужного decrypt path недостаточно. Механизм описан в [руководстве KMS](https://kubernetes.io/docs/tasks/administer-cluster/kms-provider/). Для любого encrypted store испытайте восстановление нужных key versions и доступ recovery identity. Старые decrypt keys нельзя удалять раньше использующих их retained backups без осознанной потери восстановимости.

## Считайте RPO и RTO до полезного результата

**RPO** - допустимый интервал потери изменений к моменту инцидента; **RTO** - допустимое время восстановления сервиса. Это цели, а не свойства названия database. Фактическая потеря определяется последней пригодной точкой, а время возврата - всем recovery path, включая поиск ключей, загрузку images, прогрев model endpoint и проверки прав.

Учебный пример: отказ произошёл в `12:07 UTC`, metadata восстановима до `12:02`, но последний пригодный application checkpoint конкретного Task - `11:56`. Его состояние откатилось на 11 минут, а не на пять. Для других Tasks точка может быть другой. При этом тикет, созданный в `12:04`, во внешней системе сохранился. У него не «откатывают часы» вместе с checkpoint: этот outcome нужно учесть отдельно.

Если containment занял 8 минут, bootstrap 12, возврат stores и payloads 18, проверки и reconciliation 10, а canary admission 7, последовательный путь занял **55 минут**. Параллельные операции считают по critical path, а не складывают механически. Открытый API port в середине этого пути не означает, что бизнес-сервис восстановлен.

Определите отдельные цели для read-only diagnostics, создания новых Tasks, resume существующих и допуска high-risk writes. RTO платформы может быть выполнен, пока часть Actors требует ручного разбора. Такой degraded mode допустим только как явный контракт. Retention должна покрывать обнаружение повреждения: регулярные свежие копии бесполезны, если все они уже содержат одну давно незамеченную ошибку.

## Подготовьте recovery manifest и независимый bootstrap

**Recovery manifest** описывает выбранный набор backups, releases и mappings. Он не хранит открытые secrets, но содержит ссылки на нужные key versions и procedure IDs. Доступ к самому документу и artifacts должен оставаться возможным при отказе основной площадки.

Ниже учебный формат внутреннего документа, не AX resource, не Substrate API и не файл для `kubectl apply`. Идентификаторы иллюстративные; в рабочем варианте добавляются реальные checksums, object versions и результаты validators.

```
format: recovery-plan/v1
environment: ax-dr-lab
release_inventory: pinned-images-and-configs-01
incident_time_utc: "2026-10-11T12:07:00Z"
metadata_target_utc: "2026-10-11T12:02:00Z"
stores:
  ax_backup_id: ax-backup-1202
  substrate_base_backup_id: substrate-base-1100
  wal_archive_set: wal-verified-through-1202
snapshots:
  inventory_id: completed-snapshots-1202
  task_checkpoint_utc: "2026-10-11T11:56:00Z"
  dependency_report: snapshot-reference-check-01
identity:
  decrypt_key_reference: backup-key-version-7
  fresh_access_procedure: issue-recovery-identities
external_actions:
  ledger_reference: action-ledger-durable
  reconciliation_report: pending
admission:
  mutation_access: disabled
  acceptance_report: pending
```

Не делайте bootstrap циклическим: «получить ключ из secret store, который восстанавливается этим же ключом». Отдельно подготовьте доступ к KMS, identity provider, registry, backup store, DNS и средствам связи команды. Break-glass access из главы 24 ограничивается по времени и purpose, требует audit и последующего закрытия. Образ recovery tools и их versions тоже должны быть доступны без скачивания случайного `latest` из интернета.

Проверяйте эти зависимости с чистой operator environment. На ноутбуке автора runbook нужные credentials и configs часто уже присутствуют. Если другой оператор не может пройти bootstrap только по утверждённому комплекту, документ ещё не описывает воспроизводимое восстановление.

## Восстанавливайте через проверяемые этапы допуска

Первое действие - containment и **fencing**: исключение старых исполнителей из возможности писать. Потеря связи не доказывает, что старый cluster перестал выполнять Tasks. Если после переключения живы обе стороны, они могут независимо делать writes. Одного изменения DNS недостаточно; нужно проверить остановку writers, отказ старых credentials или другой поддерживаемый механизм fencing у конечной системы.

![Recovery gates: trusted bootstrap, isolated restore, reconciliation, controlled writes](.gitbook/assets/diagrams/42-41.png)  
*Recovery gates: trusted bootstrap, isolated restore, reconciliation, controlled writes*

Начните restore в изолированной среде: mutation credentials не выданы, egress ограничен canary destinations, автоматический resume не разрешён. Просто выключить внешний ingress мало - восстановленный Actor сам способен инициировать outbound call. Подготовьте policies и механизм блокировки до запуска controllers, которые могут начать reconciliation сразу после подключения stores. Это операционный gate вашей установки, не универсальный флаг AX.

Далее верните trust foundation и immutable artifacts, восстановите stores и object payloads, проверьте схемы, hashes и snapshot compatibility. Сопоставьте Task, Actor, Tag и execution identities. При переносе Kubernetes resources UIDs и owner references могут измениться; нельзя предполагать, что копия YAML автоматически восстанавливает mapping и ownership. Используйте поддерживаемую import/migration процедуру своей версии, а не ручное угадывание связей.

После integrity checks проверьте authentication, authorization и tenant boundary. Выпустите свежие credentials, сверьте текущую policy и unresolved actions, выполните read-only canary. Затем допускайте ограниченную группу writes и расширяйте traffic с наблюдением. Реальный dependency graph может отличаться от линейного порядка «identity, cluster, stores»: bootstrap identity иногда сама требует инфраструктуры. Runbook должен разрешить этот цикл заранее.

Если проверка gate не прошла, не маскируйте её ещё одним restart. Сохраните recovery evidence, остановитесь до следующего этапа и исправьте причину. Если recovery уже сделала новый write, повторное восстановление более старой копии не отменит этот write; он тоже входит в reconciliation.

## Внешние действия переживают откат checkpoint

Возьмём операцию `op-17` из главы 37. Агент подготовил изменение, tool выполнил его, но receipt не успел попасть в checkpoint. После restore приложение видит старое состояние «не выполнено», а внешняя система - новое «выполнено». Повторять вызов только по локальному state опасно.

Operation ledger связывает stable operation ID, точные arguments или их digest, authorization/approval, physical attempts и подтверждённый outcome. Он должен иметь durability, подходящую для действий, которые нельзя повторить, и не терять последние записи вместе с более старым Actor checkpoint. Если ledger всё же откатился, внешняя authoritative system и receipts нужны для установления outcome; факт отсутствия записи не доказывает отсутствие действия.

Reconciliation выполняет read-only lookup по прежнему ID или idempotency key. Если операция подтверждена, harness сохраняет outcome и продолжает после неё. Если надёжно установлено, что эффекта не было, повтор возможен по текущей authorization и прежней logical identity. Если исход неизвестен, high-risk действие остаётся заблокированным до разбора. Недоступность lookup API не является разрешением сделать write ещё раз.

Idempotency key помогает лишь в пределах контракта provider: scope, неизменность parameters и retention deduplication должны покрывать recovery window. Если ключ хранился у provider сутки, а backup восстановили через неделю, безопасный retry нельзя выводить из наличия старой строки key. Создавать новую operation identity ради обхода конфликта тоже нельзя. Где нет подходящего API, нужны domain reconciliation или явное решение человека, а не обещание exactly once.

## Практикум безопасного DR

Используйте отдельный стенд, synthetic tenant и тестовый tool с проверяемым receipt. Никаких production mutation credentials и намеренного повреждения реальных backups. Цель - восстановить историю работы и границы доступа, а не пустой dashboard.

1. **Подготовьте историю.** Создайте Task с durable marker, тестовой внешней операцией и suspend checkpoint. Сохраните mappings, operation ID, snapshot dependencies и current policy.
2. **Зафиксируйте комплект.** Соберите recovery manifest, согласованные backups, exact artifacts и key references. Проверьте decrypt и references до принятия recovery point.
3. **Смените доступ.** Пока test Actor suspended, отзовите canary credential или разрешение. После restore старый доступ не должен восстановиться вместе с state.
4. **Пройдите чистый bootstrap.** Восстанавливайте только по runbook с новой operator environment. Зафиксируйте скрытые prerequisites и ручные шаги; не копируйте забытые secrets из живого стенда.
5. **Проверьте gates.** Докажите блокировку старых writers, restricted egress, совместимость payloads и корректные Task/Actor mappings. Испытайте недоступный key/issuer и отсутствующий dependency на отдельной копии backup.
6. **Согласуйте внешний outcome.** Восстановите checkpoint до receipt и убедитесь, что уже выполненная операция не повторилась. Для unknown outcome проверьте остановку high-risk write.
7. **Проверьте полезную работу.** Read-only canary, чужой tenant deny, выдача свежего scoped credential, затем одна разрешённая test mutation. Измерьте время и фактическую потерю state.
8. **Закройте упражнение.** Сравните measured RPO/RTO с целями, закройте временный доступ, сохраните evidence без secret values и повторите исправленный runbook другим оператором.

Приёмка завершена, когда команда может доказать восстановимость данных, действительность текущих прав и отсутствие необъяснимых повторных эффектов. Успешная загрузка backup или зелёный health check закрывают только часть этой задачи. В следующей главе соберём диагностику по слоям и release acceptance: какие наблюдения нужны оператору, чтобы быстро найти именно нарушенный gate.

### Источники и дальнейшее чтение

Kubernetes: [Secrets](https://kubernetes.io/docs/concepts/configuration/secret/), [access practices](https://kubernetes.io/docs/concepts/security/secrets-good-practices/), [Service Accounts](https://kubernetes.io/docs/concepts/security/service-accounts/), [KMS](https://kubernetes.io/docs/tasks/administer-cluster/kms-provider/). [Vault leases](https://developer.hashicorp.com/vault/docs/concepts/lease), [PostgreSQL 17 PITR](https://www.postgresql.org/docs/17/continuous-archiving.html), [Redis persistence](https://redis.io/docs/latest/operate/oss_and_stack/management/persistence/). Границы release: AX `ac233282`, Substrate `ccecc788`. В книге: главы 23-24, 27, 37, 39-41.
