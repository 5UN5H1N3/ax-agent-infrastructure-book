# Substrate snapshots: ownership, storage и совместимость

Worker, на котором выполнялась `lab/network-017`, потерян. Из главы 38 у нас остались запись Task, сведения об Actor и подтверждение внешней операции: `op-17` применена, проверка цели ещё не выполнена. Теперь нужно вернуть приложение в среду, где оно сможет эту проверку продолжить. В object storage лежат несколько похожих каталогов, но выбирать самый свежий по имени опасно: он может принадлежать другой lifetime или содержать незавершённый upload.

Начните с вопроса: «На какую завершённую точку указывает нужный ресурс?». Затем выясните, кому принадлежат её данные, какие ещё объекты нужны для запуска и совместима ли с ними среда восстановления. После этого выбирайте действие resume. Такая последовательность сохраняет основания для диагностики и помогает проверить прежнюю операцию вместо повторного сетевого изменения.

Глава 14 разобрала lifecycle pause, suspend и resume. Здесь сосредоточимся на долговечности снимков: копировании и заимствовании, границе завершённой записи, совместимости и выборе пути восстановления. Используем **Substrate v0.3.0, commit `ccecc788`**. Указанные ниже `externalSnapshot`, `localSnapshot`, Tag и `snapshotConfig` относятся к этому контракту. Поведение AX рассматриваем по закреплённому срезу `ac233282`.

## Из каких частей складывается восстановление

Snapshot - сохранённое состояние исполняемой среды в составе, который поддерживает выбранный runtime. Часть этого состояния может быть памятью процесса, часть - файлами. Для приложения важен ещё смысл этих данных: какой шаг завершён, какая операция отправлена и на каком основании разрешено продолжать работу.

Представьте восстановленный терминал со старым приглашением командной строки. Его экран мог вернуться ровно к прежнему виду, хотя удалённый сервер уже закрыл соединение, срок credentials истёк, а отправленная ранее команда успела изменить конфигурацию. При восстановлении агента происходит то же: сохранённое локальное знание возвращается в мир, который продолжал меняться.

| Что сохраняем | Зачем это нужно в нашем инциденте | Что потребуется дополнительно |
| --- | --- | --- |
| Snapshot Actor | Вернуть поддерживаемые файлы и, при Full, состояние выполнения | Совместимый runtime и доступ к зависимостям |
| Checkpoint harness | Узнать run, шаг, ожидаемые результаты и незавершённые операции | Проверка версии формата и актуальных внешних фактов |
| Независимый operation ledger | Установить, что `op-17` уже применена | Проверка инженерной цели и право на следующий шаг |
| Backup платформы | Вернуть metadata, snapshots и ключи после их потери | Согласованный план restore и проверка полезной работы |

У этих механизмов разные сроки жизни. Checkpoint в workspace откатится вместе с workspace. Независимый ledger останется новее и поможет восстановить результат операции. Backup должен сохранить компоненты, без которых нельзя прочитать ни checkpoint, ни ledger. При проектировании полезно прямо записать, какое хранилище переживает потерю Worker, а какое - ещё и ошибочное удаление управляющих ресурсов.

## Как выбрать Full, Data и источник гостевой среды

Сначала разделим две оси. Scope определяет состав сохранённого состояния. Место хранения определяет, какие отказы оно переживёт. Полный снимок на потерянной node недоступен независимо от количества захваченных байтов. Более узкий Data snapshot в доступном object storage способен вернуть индивидуальные файлы на другой совместимый Worker.

`Full` включает process memory, изменения writable rootfs и поддерживаемые durable data. `Data` сохраняет durable data без прежней памяти и rootfs changes. В частности, `DurableDir` - каталог, включаемый в механизм snapshot; он не означает непрерывную репликацию всех записей. Между завершёнными external snapshots новые файлы остаются под риском потери node. Важный checkpoint должен действительно находиться в snapshot-capable volume.

После Data restore нужно отдельно получить среду, которая запустит приложение. При `onResume.fromData=COLD_BOOT` containers стартуют из OCI image с восстановленными данными. При `GOLDEN` индивидуальные данные соединяются с общим подготовленным guest state. Golden возвращает общий старт, а не индивидуальный heap прежней сессии.

| Путь | Из чего возвращается приложение | Когда его стоит рассматривать |
| --- | --- | --- |
| Full restore | Захваченная среда и индивидуальные данные | Дорогая повторная инициализация или действительно нужное in-memory state, при проверенной совместимости |
| Data cold boot | OCI image и сохранённые durable данные | Приложение умеет читать checkpoint и заново устанавливать соединения |
| Data-on-golden | Общий Full baseline и индивидуальные Data | Многие независимые Actors повторяют одну дорогую подготовку |

Для restartable приложения возможна такая policy; это фрагмент конфигурации, не полный ActorTemplate:

```
snapshotConfig:
  onPause: SNAPSHOT_CONTENT_SCOPE_FULL
  onCommit: SNAPSHOT_CONTENT_SCOPE_DATA
  onResume:
    fromData: RESUME_SOURCE_COLD_BOOT
```

Она позволяет держать Full local pause ради быстрого возврата, а затем сохранять наружу Data для более долгого ожидания. `onCommit` должен быть подмножеством `onPause`: из Full можно выделить Data, а из Data нельзя получить уже не захваченную RAM. Когда вызывать операции, решает orchestration; сами enum не создают таймер автоматического suspend.

В AX [BuildActorTemplate](https://github.com/google/ax/blob/ac2332829f22360ff97b0ba34d94dd0dd782f17e/internal/substrate/client.go) создаёт `/workspace` как `DurableDir`, выбирает Data для pause и commit и GOLDEN для Data resume. Поэтому в нашем случае нужны индивидуальные данные и пригодный baseline. Если Task использует другой базовый template, сначала прочитайте его policy. Имя проекта AX не определяет состав произвольного пользовательского ActorTemplate.

## Как меняется владелец и кто продолжает зависеть от Tag

Ownership описывает ресурс, под которым хранится snapshot и который отвечает за его жизненный цикл. Ссылка даёт потребителю путь к данным, но сама по себе не передаёт владение. Это различие особенно заметно при клонировании.

Actor A выполнил suspend и получил собственную точку S1. Пока A suspended, инженер создаёт Tag T. Workflow копирует snapshot под UID Tag. Теперь есть две независимые копии: одна принадлежит A, вторая T. Следующий успешный suspend A заменит его текущую точку, а копия T сохранится.

Новый Actor B, созданный через `sourceTag=T`, сначала заимствует URI снимка T. Отдельная копия данных для B в этот момент не создаётся. Это экономит место и работу при клонировании, но T остаётся условием будущего восстановления B. Даже если B уже работает, удаление Tag после загрузки данных на узел может лишить его возможности восстановиться.

![Snapshot ownership: copy and borrowing have different lifetimes](.gitbook/assets/diagrams/39-38.png)  
*Snapshot ownership: copy and borrowing have different lifetimes*

После первого собственного успешного suspend B получает actor-owned snapshot S2. Прямая зависимость от T заканчивается. Удаление B не должно удалять T, а замена snapshot A не должна трогать его копию. При этом B с Data-on-golden всё ещё нуждается в golden Tag G своего template. «B больше не ссылается на T» и «B не зависит ни от одного Tag» - разные выводы.

Отдельно определите назначение clone. Для независимой новой задачи нужны новый run и очистка или переосмысление скопированных pending operations. Если B должен продолжить прежний run после потери A, требуется подтверждённая передача владения приложением и исключение конкурирующего исполнителя. Копирование snapshot переносит данные, но не выдаёт право продолжать внешние записи с сохранёнными полномочиями.

В v0.3.0 [CreateActor](https://github.com/agent-substrate/substrate/blob/ccecc788a327dc11dcd6c21ee153f3d0cbb5cc97/cmd/ateapi/internal/controlapi/actor.go) требует совпадения template UID source Tag и нового Actor, даже для Data. Явный `sourceTag` не допускается у template с external volumes: такой snapshot не клонирует содержимое подключённой CSI volume. Сохранённая копия workspace не создаёт копию внешней database.

Защита от удаления нужного Tag требует отдельной процедуры. В данном срезе `DeleteTag` не запрещает удаление из-за borrowers; source Tag остаётся разрешимым даже во время очистки его bytes. Следовательно, проверка «сейчас clones не нашлись» недостаточна: новый clone может появиться между проверкой и удалением. Сначала нужно остановить создание новых зависимостей, затем получить актуальный inventory и применить UID/version guards. Эти guards защищают от удаления уже другого объекта под прежним именем, но не заменяют барьер для новых consumers.

## Как читать URI и происхождение снимка

Storage URI связывает данные с lifetime владельца. Базовый `snapshotConfig.storageLocation` дополняется Atespace, видом ресурса и UID. Логическая структура выглядит так:

```
<location>/atespaces/<atespace>/actors/<actor-uid>/snapshots/<snapshot-id>
<location>/atespaces/<atespace>/tags/<tag-uid>
```

Используйте URI, который вернул сервер, а не собирайте адрес по читаемому имени объекта. После удаления и повторного создания Actor с тем же именем получает новую идентичность. Старый каталог не становится его текущим снимком автоматически. Для восстановления нужны запись ресурса, её ссылка на данные и сведения о сохранённом состоянии.

У скопированного снимка владельцем может быть Tag T, а Actor UID внутри manifest — указывать на A. UID объясняет, чьё состояние захватили; владельцем копии остаётся T. Для решения о сроке хранения сопоставляйте метаданные владельца и префикс, а для восстановления — происхождение снимка и требования к среде.

Prefix также не выдаёт права чтения. Их обеспечивает IAM или policy storage backend. При cross-Atespace clone данные остаются в source Atespace, и runtime identity должна иметь доступ именно туда. Scope Tag `PUBLISHED` разрешает использование из других Atespaces на уровне API; он не превращает bucket в анонимно публичный и не делает содержимое безопасным для любого tenant.

## Где проходит граница завершённой точки

Suspend обновляет metadata и пишет payload в object storage. Единой транзакции между ними нет, поэтому [workflow](https://github.com/agent-substrate/substrate/blob/ccecc788a327dc11dcd6c21ee153f3d0cbb5cc97/cmd/ateapi/internal/controlapi/workflow_suspend.go) сохраняет промежуточное состояние. Actor получает `SUSPENDING` и `inProgressSnapshotUri`; прежний `externalSnapshot` пока обозначает предыдущую завершённую точку.

На стороне [atelet](https://github.com/agent-substrate/substrate/blob/ccecc788a327dc11dcd6c21ee153f3d0cbb5cc97/cmd/atelet/main.go) сначала загружаются payload files, затем `manifest.json`. Manifest перечисляет состав snapshot и служит маркером завершения upload. После успешного checkpoint и detach workflow освобождает assignment, записывает новый `externalSnapshot`, очищает промежуточные поля и фиксирует `SUSPENDED`. Удаление заменённой actor-owned точки выполняется после этого и может завершиться ошибкой независимо от нового commit.

Рассмотрим отказ после загрузки manifest, но до финального обновления metadata. В storage лежит пригодный на вид набор, однако Actor всё ещё указывает на старую completed точку. Автоматический выбор «новейшего каталога» обошёл бы незавершённую lifecycle operation. Вначале нужно выяснить её судьбу и сохранить evidence; вручную переписывать status на найденный prefix опасно.

Если payload есть, а manifest отсутствует, upload мог оборваться раньше. Если metadata уже указывает на новую completed точку, но старые objects остались, вероятен незавершённый cleanup. Во втором случае лишние bytes не следует считать поддерживаемой историей версий: Actor хранит одну текущую external snapshot, а старые файлы могут быть собраны позднее.

Повторный suspend использует сохранённый progress; уже suspended Actor не создаёт новую точку от одного повтора вызова. Однако ошибка checkpoint способна привести к `CRASHED`. Выбор следующего действия требует чтения status, текущей completed точки и границ допустимой потери. Возможность повторно войти в workflow не означает восстановление любых повреждённых данных.

## Что делать после timeout CreateTag

У [CreateTag](https://github.com/agent-substrate/substrate/blob/ccecc788a327dc11dcd6c21ee153f3d0cbb5cc97/cmd/ateapi/internal/controlapi/workflow_tag.go) три этапа: reservation имени и storage location, копирование snapshot, затем финализация `status.snapshot`. Source Actor должен быть suspended. Lease Actor защищает копирование от одновременного suspend, который мог бы удалить исходные bytes.

Если ответ потерялся, первым действием должно быть чтение Tag. В v0.3.0 повтор создания под занятым именем получает `AlreadyExists`, включая pending Tag после сбоя. Это не продолжение незавершённой копии. Состояние нужно разбирать по сохранённой записи.

| Что обнаружено после timeout | Следующий шаг |
| --- | --- |
| Ready Tag с ожидаемым source/template | Сверить provenance и доступность payload; не создавать копию повторно |
| Pending Tag, исходный вызов ещё может работать | Сохранить evidence и исключить конкурентное продолжение до cleanup |
| Подтверждённо оставленная pending запись | Guarded DeleteTag для этой lifetime, затем новая попытка при сохранённом source |
| Tag с другим UID или происхождением | Остановиться и выяснить, какой ресурс сейчас занимает имя |

DeleteTag сначала очищает свой prefix, затем удаляет запись. Повтор удаления может закончить частичный cleanup. Такой порядок сохраняет handle на данные при ошибке. У готового Tag это удалит настоящую recovery point, поэтому превращать любой `AlreadyExists` в автоматическое delete/recreate нельзя.

Новая попытка снимет копию той точки, которую A держит к этому моменту. Если A успел снова resume/suspend, это уже другой checkpoint. Для сохранения конкретной диагностической точки удерживайте source suspended до финализации Tag и проверки provenance. Lease отдельного RPC не резервирует S1 навсегда.

## Как проверять совместимость восстановления

Совместимость удобно разбирать по вопросам. С каким template захвачена среда? Какой SandboxConfig разрешит текущий runtime? Понимает ли он формат snapshot на целевой architecture? Сохраняются ли image, volumes и mount layout? Умеет ли новое приложение прочитать старую schema checkpoint? Совпадение одного UID не отвечает на остальные вопросы.

ActorTemplate immutable для клиентов. Перепривязать Actor к другому template разрешено в `SUSPENDED`; при доступном прежнем template UpdateActor проверяет одинаковый SandboxConfig, volumes и mounts общих containers. Удалённый старый template лишает эту проверку части материала для сравнения, поэтому наличие разрешённого API update само по себе не доказывает совместимость.

В external restore несовпадение записанного template UID с текущим приводит к запросу Data restore вместо прежнего Full memory image. Это позволяет не запускать старую память как среду нового template, но не мигрирует файлы. Совместимость schema и восстановление смысла работы остаются обязанностью приложения. Tag, созданный после repoint со старой snapshot, сохраняет template identity захваченного guest state, а не маскирует его новой ссылкой Actor.

Storage location также нужно учитывать отдельно. В UpdateActor этого среза нет отдельного запрета на её изменение через допустимую смену template: старая точка остаётся по прежнему URI, новые записи могут уйти в другой prefix. Для переходного периода нужны доступ и retention в обоих местах.

Manifest содержит сведения о sandbox assets, в том числе SHA-256, но [Restore atelet](https://github.com/agent-substrate/substrate/blob/ccecc788a327dc11dcd6c21ee153f3d0cbb5cc97/cmd/atelet/main.go) берёт runtime assets из запроса, разрешённого через текущий SandboxConfig. Старый manifest не выбирает автоматически старый binary. До cluster upgrade сохраните нужные assets и проверьте restore старых точек на целевой среде. Для gVisor существенны также [CPU features](https://gvisor.dev/docs/user_guide/checkpoint_restore/#checkpoint--restore-with-different-cpu-features); переносимость нельзя выводить из того, что оба Worker называются Linux nodes.

## Практика проверки плана восстановления

[examples/ch39\_restore\_preflight.py](https://github.com/5UN5H1N3/ax-agent-infrastructure-book/blob/main/examples/ch39_restore_preflight.py) моделирует проверку перед restore, или preflight. На входе - подготовленный inventory Actor, Tag, template, snapshot и независимая квитанция операции. Программа выбирает зависимости и следующий прикладной шаг. Она не подключается к Substrate, не читает настоящие runtime images и не исполняет restore или удаление.

В упражнении node недоступна, поэтому рассматривается external recovery point. Возврат из доступного local checkpoint требует отдельного пути; реальный resume отдаёт local snapshot приоритет, когда он присутствует. Перед выбором более старой external точки оператор также принимает потерю изменений после её capture.

Учебные dataclasses - нормализованное представление для упражнения. Поля `runtime_profile`, `complete`, `ready` и условные URI `fixture://` не являются форматом Substrate API. Реальный collector должен получить эти сведения из доверенных источников. Даже `available_files` здесь только набор имён; его проверка не доказывает целостность файлов или подлинность manifest.

Запуск из корня репозитория:

```
python3 examples/ch39_restore_preflight.py
python3 -m unittest discover -s examples -p 'test_ch39_restore_preflight.py'
```

Исходные данные соответствуют такому эпизоду. A сохранил Data snapshot, из него сделали Tag T и создали B. Индивидуальный checkpoint говорит `DISPATCHING`: в момент его записи приложение ещё не знало исход операции. В независимом ledger уже лежит `APPLIED` для того же run, `op-17` и digest действия. Таким образом, старый checkpoint не отменяет установленный в главе 38 факт.

B здесь только получатель сохранённого состояния для проверки плана продолжения старого run. Digest берётся из того же `Action.digest()`, что в примере главы 37. Передача владения run в модели не реализована; закрытый mutation gate сохраняет эту границу даже при успешном preflight.

Сначала программа идёт по committed `external_uri`. Поле in-progress может указывать на более новый набор, но для выбора точки не используется. Затем проверяются владелец, готовность Tag, наличие manifest и перечисленных payload, template и выбранный профиль среды. Для Data-on-golden добавляется вторая зависимость - Full baseline нужного template. На прикладном уровне проверяются schema checkpoint и совпадение identity операции с независимой квитанцией.

Ключевые поля результата для заимствованной точки:

```
{
  "decision": "PREFLIGHT_ONLY",
  "restore_path": "DATA_ON_GOLDEN",
  "payload_owner": "TAG:tag-copy",
  "captured_actor_uid": "actor-a",
  "local_operation_view": "DISPATCHING",
  "authoritative_operation": "APPLIED",
  "next_step": "VERIFY_GOAL",
  "mutation_gate": "CLOSED",
  "automatic_mutations": 0
}
```

Здесь одновременно видны происхождение A и владение T. Проверка проходит даже без живого source Actor A: Tag уже имеет свою копию. Следующее действие приложения - проверить цель, а не заново отправлять сетевую запись. Mutation gate остаётся закрытым: предварительная проверка не выдаёт полномочий на изменение инфраструктуры.

Вторая часть demo подставляет inventory после собственного успешного suspend B. Это подготовленная fixture следующего состояния, не выполненный вызов SuspendActor. B теперь читает actor-owned точку, и T больше не имеет прямого borrower. Но golden Tag G остаётся зависимостью template. Проверка удаления возвращает для T `MANUAL_REVIEW_NO_KNOWN_REFERENCES`, а для G - `HOLD`. Даже первый ответ означает только отсутствие известных ссылок при заявленном полном inventory и барьере создания, а не разрешение удалить ресурс.

Тесты добавляют повреждённые и неоднозначные входы: manifest без payload, payload без manifest, pending Tag, удалённого owner, чужую actor-owned точку, неизвестный scope, несовпавшую schema и квитанцию другой операции. Если внешний исход неизвестен, план останавливается с `resolve_operation_before_recovery`; автоматический retry не предлагается.

### Границы учебной проверки

Модель проверяет локальные правила выбора, а не переносимость бинарного snapshot. Равный `runtime_profile` - доверенная метка fixture; CPU, формат runtime, фактические mounts, IAM и криптографическое происхождение данных не испытываются. Полнота inventory и барьер создания также считаются входными условиями, а не реализованными распределёнными гарантиями.

При смене template пример требует отдельного review и не имитирует все fallback реального resume workflow. Это намеренно консервативная политика проверки. Поддерживаемая API операция и автоматически разрешённый приложением recovery имеют разные требования. Результат `PREFLIGHT_ONLY` никогда не следует называть успешным restore или доказанной совместимостью стенда.

## Как продолжить приложение после отката

В нашем примере snapshot возвращает старое представление операции, а внешний ledger подтверждает её применение. Harness должен сопоставить run, operation ID и точное действие, принять подтверждённый результат и перейти к проверке цели. Если подтверждение относится к другому действию с тем же коротким именем, использовать его нельзя. Поэтому identity операции включает контекст и нормализованный digest, как в главе 37.

Ledger только внутри `/workspace` откатится вместе с Data. Он помогает продолжить локальные шаги, но не даёт независимого доказательства внешнего commit. Для критичных записей authoritative record должен переживать откат Actor. Если исход остаётся неизвестным, сначала ищут квитанцию или достоверно сверяют target по контракту инструмента; право на повтор не появляется от отсутствия результата в старом файле.

Full требует ещё одного пути входа в recovery: процесс может продолжить с захваченной инструкции, минуя обычный entrypoint. Проверку credentials, владения lease и ожидаемой версии target нужно выполнять перед следующей mutation или через поддерживаемый restore-aware механизм. Старый token в памяти не возвращает истёкшие полномочия. Соединения и DNS caches также требуют перепроверки там, где это влияет на действие.

External CSI volumes и удалённые databases не откатываются вместе с Actor snapshot. Получается сочетание старого process state и более новых внешних данных. Перед capture приложение должно завершать локальные записи по своему протоколу и сохранять пригодный checkpoint; после restore - согласовывать schema, транзакции и операции. Один успешный запуск runner эту согласованность не подтверждает.

## Как выбрать baseline и оценить его цену

Для нового restartable harness полезно начать с Data cold boot: меньше скрытого состояния, понятный старт и явное чтение checkpoint. Затем измерьте, сколько занимает общая подготовка. Если она повторяется у сотен Actors, golden baseline может окупить дополнительную зависимость. Full стоит выбирать ради конкретной экономии или нужной непрерывности, которую вы умеете проверять после восстановления.

Golden должен быть безопасен для многократного старта независимых executions. В общий Full baseline нельзя помещать tenant credentials, уникальное владение lock, pending external write или готовую identity пользовательского run. Удаление secret-файла перед capture не очищает автоматически память процесса. Надёжнее не выдавать пользовательский secret на этапе общей подготовки, а получать актуальные полномочия после restore или перед использованием.

Готовность golden тоже входит в deployment gate. Actor, созданный до появления готового baseline, не получает новую исходную ссылку автоматически просто от изменения статуса template. Для Data-on-golden resume нужны готовый Tag, нужный template и доступ к его данным; отсутствие baseline приводит к отказу этого пути. Безусловно переключать его на cold boot в recovery automation нельзя: приложение должно заранее поддерживать такой старт.

Измеряйте время до полезного шага, а не только до `RUNNING`. Сначала идут ожидание capacity, mounts и manifests; затем download/decompression и подготовка assets, часть которых может выполняться параллельно; после них - restore, readiness и recovery harness. При анализе traces складывайте последовательные участки и смотрите критический путь параллельных, иначе можно завысить итоговую latency.

Маленький индивидуальный Data snapshot не гарантирует дешёвый cold-node restore: может понадобиться загрузка большого golden baseline. Например, при условных 2 GiB и эффективных 256 MiB/s только передача займёт около восьми секунд. Десять таких восстановлений означают 20 GiB трафика через общий канал; эти числа - расчёт по заданным допущениям, не benchmark Substrate.

Сравнивайте local Full, external Full, Data cold boot и Data-on-golden отдельно, на warm и cold nodes, при разной конкуренции. Фиксируйте p50/p95/p99 до полезного результата, пик RAM и disk, CPU decompression и фактические cache hits. Если golden не даёт нужного выигрыша, цена сопровождения и восстановления зависимостей может оказаться выше экономии старта.

## Как хранить и удалять recovery points

Начните с графа ссылок. Для Actor нужны текущая и промежуточная точки; для clone - source Tag до собственного suspend; для Data-on-golden - baseline template; для будущих запусков - сам template с его golden reference. Поэтому inventory только живых Actors не даёт полной картины retention. Старый Tag может оставаться необходимым ещё не запущенным задачам.

Штатное удаление должно проходить через lifecycle API с проверками нужной lifetime. Массовая очистка bucket по возрасту обходит этот механизм. При расследовании abandoned upload учитывайте незавершённые workflows; при удалении Tag исключайте создание новых consumers. Даже полный список, прочитанный минуту назад, не является барьером для нового clone.

Snapshot хранит чувствительные данные: Full может содержать secrets в RAM, Data - в workspace, логах и checkout. Нужны scoped credentials, encryption и аудит read/write/delete для выбранного backend. SHA-256 runtime assets в manifest не означает подпись всего snapshot или наличие проверяемого digest у каждого payload. Защита от подмены требует доверенного происхождения metadata и выбранного integrity contract.

Для backup сохраняйте согласованный набор metadata, payload prefixes, Tags, templates, images, SandboxConfig assets и key versions. В AX добавьте Redis resource records и mapping Task к Actor. Replication может повторить ошибочное удаление; версия object или отдельная recovery copy помогает только при существующей policy удержания и проверенном способе вернуть весь набор.

RPO задаёт допустимое окно потери изменений, RTO - время до оговорённого восстановления. Completed external snapshot ограничивает точку возврата Actor, но ledger может содержать более новые подтверждённые действия. Согласованное восстановление соединяет их через application recovery, а не пытается сделать все файлы одинаково старыми.

Проверять backup безопаснее в изолированной среде без production mutation credentials и с ограниченным egress. Сначала проверяются данные и совместимость, затем checkpoint и внешние операции. Полномочия на новые записи выдаются по отдельному решению. Этот порядок делает rehearsal проверкой восстановления, а не повторным исполнением сохранённых production намерений.

## Как закончить расследование потери Worker

Для `lab/network-017` итоговый recovery record должен называть Actor UID, выбранную completed точку, её owner и provenance, фактические зависимости и выбранный restore path. Сохраните также in-progress references, template, runtime assets, schema checkpoint и состояние `op-17`. Так следующий инженер увидит основания решения, даже если прежняя node уже недоступна.

До новых lifecycle calls посмотрите, не изменился ли Actor в результате другого recovery. В AX `EnsureActor` пытается revert существующего crashed Actor, а при неудаче может удалить и создать его заново. Прежнее имя в карточке не доказывает прежнюю lifetime. Именно поэтому сопоставление наблюдений из главы 38 и проверка владельца snapshot должны опираться на актуальный inventory.

Перенос локального preflight на стенд требует отдельных испытаний. В изолированном Atespace проверьте собственный snapshot B после заимствования, отказ при потере golden, прерывание CreateTag, потерю node после local pause и после external suspend. Восстановите старый checkpoint при более новом ledger и убедитесь, что уже применённая операция не отправляется повторно. Для runtime upgrade отдельно восстановите старые Full и Data на целевых Workers; локальные тесты planner этого не заменяют.

После успешного восстановления приложения ещё нужно проверить разрешённые обращения к model API и инструментам. Среда может быть пригодной, но иметь устаревший trust bundle, неверный DNS или недостаточную egress policy. В главе 40 продолжим с восстановленным `lab/network-017` и разберём, как диагностировать этот путь, сохраняя границы доступа.

### Источники и дальнейшее чтение

- [Substrate v0.3.0 API](https://github.com/agent-substrate/substrate/blob/ccecc788a327dc11dcd6c21ee153f3d0cbb5cc97/pkg/proto/ateapipb/ateapi.proto): snapshot scope, Tag и Data resume policy.
- [Actor create и update](https://github.com/agent-substrate/substrate/blob/ccecc788a327dc11dcd6c21ee153f3d0cbb5cc97/cmd/ateapi/internal/controlapi/actor.go): borrowing, template checks и external volumes.
- [Tag workflow](https://github.com/agent-substrate/substrate/blob/ccecc788a327dc11dcd6c21ee153f3d0cbb5cc97/cmd/ateapi/internal/controlapi/workflow_tag.go) и [owner URI](https://github.com/agent-substrate/substrate/blob/ccecc788a327dc11dcd6c21ee153f3d0cbb5cc97/internal/resources/snapshot.go): reservation, copy, provenance и deletion.
- [Suspend workflow](https://github.com/agent-substrate/substrate/blob/ccecc788a327dc11dcd6c21ee153f3d0cbb5cc97/cmd/ateapi/internal/controlapi/workflow_suspend.go), [resume workflow](https://github.com/agent-substrate/substrate/blob/ccecc788a327dc11dcd6c21ee153f3d0cbb5cc97/cmd/ateapi/internal/controlapi/workflow_resume.go), [atelet](https://github.com/agent-substrate/substrate/blob/ccecc788a327dc11dcd6c21ee153f3d0cbb5cc97/cmd/atelet/main.go) и [sandbox manifests](https://github.com/agent-substrate/substrate/blob/ccecc788a327dc11dcd6c21ee153f3d0cbb5cc97/cmd/atelet/sandbox_assets.go).
- [AX template construction и EnsureActor](https://github.com/google/ax/blob/ac2332829f22360ff97b0ba34d94dd0dd782f17e/internal/substrate/client.go); [gVisor checkpoint/restore](https://gvisor.dev/docs/user_guide/checkpoint_restore/).
