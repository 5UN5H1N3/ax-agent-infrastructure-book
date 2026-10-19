# Substrate networking: routing, request parking и безопасный egress

Пользователь возвращается к `review-42` из главы 14 и открывает результат ревью. Actor сейчас suspended: процесса на Worker нет, хотя его имя и сохранённые данные существуют. Сервис должен найти эту сессию, восстановить среду и доставить запрос. А когда агент продолжит работу, ему понадобятся исходящие вызовы к model API и сервису публикации. Эти два направления выглядят как обычная сеть, но решают разные задачи и доверяют разным участникам.

Ingress - входящий путь к Actor. Здесь нужно установить, кому адресован запрос и где этот Actor исполняется сейчас. Egress - исходящий путь из Actor. Здесь недоверенный код сам выбирает, куда обратиться, а платформа должна ограничить этот выбор. Открытый ingress не разрешает Actor выходить куда угодно; разрешённый egress не даёт пользователю доступ к чужой сессии.

**Срез реализации Substrate:** [v0.3.0 · ccecc788](https://github.com/agent-substrate/substrate/tree/ccecc788a327dc11dcd6c21ee153f3d0cbb5cc97). Далее конкретные defaults и поля относятся к этому commit и описанному Envoy-пути. HTTPS interception требует режима установки с sdsmint, выпуском сертификатов gateway; credential injection включается отдельно. Альтернативный dataplane нельзя считать эквивалентным без проверки. Experimental authz AX из главы 24 - другой профиль.

**Статус:** `ACTOR-AWARE ROUTING` · `BOUNDED PARKING` · `DEFAULT-DENY EGRESS`

## Что читатель сможет сделать после главы

- Проследить запрос от logical Actor ref до текущего Worker, не подменяя identity адресом.
- Объяснить, что удерживает request parking и почему очередь не создаёт capacity.
- Разделить бюджет повторов, срок ожидания клиента и время уже начатого resume.
- Выбрать нужный слой защиты: caller authorization, сетевую policy или контроль tool-операции.
- Составить минимальный egress policy body и проверить исполнимость его правил.
- Отличить policy deny, TLS failure, provider failure и ответ приложения.

## Имя выбирает Actor но не даёт право доступа

Pod IP отвечает на вопрос «куда послать пакет сейчас». Имя Actor отвечает на вопрос «чья это сессия». После suspend Actor может вообще не занимать Worker; после resume он может оказаться на другой node. IP, сохранённый клиентом вчера, не становится правильным оттого, что сегодня по нему кто-то отвечает.

Входящий HTTP request несёт `ate-target-actor: team-a/review-42`. Это routing key - ключ выбора адресата. HTTP `Host` остаётся authority приложения и не выбирает Actor. Router обращается к control plane через `ResumeActor`: для running Actor получает состояние и assignment, для допустимого остановленного состояния запускает восстановление. Assignment сообщает текущий Worker, а не заменяет долгоживущую identity.

Имя тоже не является вечной identity. Если удалить Actor и создать другой с тем же именем, UID будет новым. Пользовательский доступ к сессии, audit и привязки длительных операций должны учитывать эту границу жизни объекта. Нельзя переносить разрешение старому Actor на новую incarnation только из-за совпадения `atespace/name`.

Главное ограничение ingress handler этого среза: клиентский заголовок не доказывает право обращения. Авторизация вызывающего пользователя должна быть отдельной функцией доверенного front door или прикладного слоя. Сначала аутентифицируйте caller и разрешите ему конкретную сессию, затем сопоставьте её Actor ref. Не принимайте произвольный внешний target header как полномочие. mTLS к Worker аутентифицирует инфраструктурный путь, а не пользователя браузера.

## Как запрос доходит до текущего исполнения

Envoy - proxy, который принимает и передаёт трафик. `atenet-router` принимает решения, для которых нужна модель Actor. Envoy вызывает его через `ext_proc`, external processing: передаёт метаданные запроса и ждёт результата обработки заголовков. Сам HTTP workload не выполняется в этом processor.

Для `review-42` путь состоит из следующих действий:

1. Доверенный вход платформы проверяет caller и выбирает адресуемую сессию.
2. Envoy передаёт заголовки ingress processor. Router извлекает Actor ref.
3. Router вызывает `ResumeActor`. Control plane проверяет lifecycle и возвращает assignment либо выполняет его подготовку и restore.
4. Router сообщает Envoy адрес `atunnel` на Worker, обычно `WorkerPodIP:443`, и целевой port приложения Actor, по умолчанию 80.
5. Envoy отправляет workload через TLS-аутентифицированный инфраструктурный путь. `atunnel` доставляет его в private network Actor.
6. Ответ проходит обратно. Прикладной статус и содержимое ответа нужно проверять отдельно от успеха routing.

Это не означает, что router сам открывает все соединения или что успешный lookup навсегда закрепил Worker. Решение принято для текущего пути запроса. Между lookup и передачей могут произойти relocation, сбой tunnel или остановка приложения. Ошибку после начала передачи нельзя автоматически считать безопасным поводом повторить изменяющую операцию. Для публикации ревью по-прежнему нужны operation ID и сверка результата из главы 14.

`RUNNING` также не заменяет health check. Быстрый путь `ResumeActor` для уже работающего Actor не выполняет заново все wakeup probes. Полученный assignment означает возможность попытаться маршрутизировать запрос, а не доказанный успех бизнес-действия.

### Зачем здесь отдельный router

Kubernetes Service удобен, когда любой из выбранных Pods может обслужить запрос. Stateful Actor - адресат с собственной сессией, который может сейчас отсутствовать на Worker. Нужно совместить lookup конкретного объекта с lifecycle call, а затем выбрать его место исполнения. Один Service к пулу Workers этого не делает.

Разделение полезно и для безопасности. Сетевые ограничения кластера должны не позволять недоверенным клиентам обходить предусмотренный gateway и обращаться напрямую к инфраструктурным listeners. Но наличие NetworkPolicy в YAML само по себе не доказывает, что сеть конкретного sandbox и CNI закрывает такой обход. Проверяйте фактическую topology и enforcement. NetworkPolicy ограничивает сетевой путь; доступ пользователя к `review-42` остаётся отдельной задачей.

## Parking удерживает запрос до возможности resume

В burst свободных Workers может временно не хватать. Немедленный 503 превращает короткий дефицит в ошибку пользователя; бесконечное ожидание превращает его в неограниченную очередь. Request parking выбирает промежуточный вариант: router сохраняет ожидание входящего запроса и повторяет *resume*, пока это имеет смысл в пределах бюджета.

Пока запрос ждёт решение `ext_proc`, он ещё не стал полезной работой Actor. Это не durable job queue: нет обещания сохранить запрос при падении router, пережить отключение клиента или повторно доставить его после восстановления. Для задачи, которую обязательно нужно принять и выполнить позже, нужен прикладной job/workflow contract с записью intent и отдельным статусом. Parking предназначен для короткой паузы живого HTTP request.

В этом срезе включённый parking по умолчанию имеет бюджет 5 секунд и lot на 1024 parked requests. Повторы начинаются с интервала 100 ms, factor 1.1 и jitter 0.1; jitter разносит попытки во времени, чтобы ожидающие запросы не создавали синхронную нагрузку. Это defaults, а не рекомендуемая ёмкость любого кластера.

### Какие ответы стоит ждать

`ResourceExhausted` означает нехватку ресурсов, `Unavailable` - недоступность control plane, `Aborted` - конфликт конкурентной операции. `FailedPrecondition` может сопровождать состояние, которое другая операция ещё изменит. Router повторяет эти коды при включённом parking, но не знает, станет ли условие успешным через секунду. Постоянно сломанная предпосылка тоже может потратить весь бюджет.

| Результат ResumeActor | Решение router | Что увидит клиент при окончательном отказе |
| --- | --- | --- |
| Успех | Маршрутизировать workload | Дальше действует прикладной HTTP contract |
| ResourceExhausted, FailedPrecondition, Unavailable, Aborted | Повторять до исчерпания бюджета | Обычно 503 с причиной соответствующего слоя |
| NotFound | Не ждать появления объекта | 404 |
| PermissionDenied или Unauthenticated | Не лечить повтором | 403 или 401 |
| DeadlineExceeded | Завершить ожидание этой операции | 504 |

Здесь 503 из-за отсутствия Worker - не то же самое, что 429 из-за лимита конкретного пользователя. И статус без emitter неоднозначен: 503 может вернуть front proxy, router, credential provider или origin. Нужны correlated logs, Actor ref и код control-plane результата, а не один общий счётчик неудач.

При `--parked-request-max=0` parking выключен. Остаются повторы `Aborted` в отдельном 15-секундном бюджете; остальные перечисленные временные ошибки возвращаются сразу. Выключение parking не превращает HTTP в очередь с другим размером.

## Один flight не означает один RPC на весь кластер

Flight - одна совместная операция resume, к которой присоединяются несколько ожидающих запросов. В одном экземпляре `ActorResumer` registry связывает Actor ref с незавершённым flight. Поэтому десять клиентов одного Actor могут ждать одну текущую попытку RPC, а не десять независимых restore.

Но этот registry находится в памяти процесса router. Запросы на другой replica могут создать другой flight и другой RPC. Конкурентные lifecycle изменения затем регулирует control plane. Singleflight снижает локальный шторм; он не является distributed lock и не доказывает «один resume RPC на Actor во всём кластере».

Бюджет начинается с первого caller flight, не с каждого присоединившегося запроса. Если поздний клиент пришёл на отметке 4.9 секунды, до границы новых повторов остаётся 0.1 секунды. Его небольшое собственное время ожидания до `budget_exhausted` не обязательно означает ошибку таймера.

Отмена первого клиента не отменяет общий resume. Даже если ушли все callers, начатая попытка может продолжить восстановление. Следующий request на этот router присоединится к ещё живому flight. В логах нельзя переводить «клиент получил timeout» в «Actor не был восстановлен».

## У ожидания несколько разных таймеров

**Бюджет повторов ограничивает запуск новых попыток.** Уже начатый RPC отделён от cancellation клиента и от budget timer. Попытка, начавшаяся до границы, может закончиться после неё. Поздний успех всё ещё пригоден для routing, если клиент и его stream ещё ждут. Поздняя retryable ошибка завершит flight без нового RPC.

**Stream timeout ограничивает ожидание processor.** В описанном Envoy ingress-пути при включённом parking `ext_proc` message timeout получает бюджет плюс 5 секунд: при defaults это 10 секунд. Потеря stream или deadline клиента может закончить его ожидание раньше завершения resume. Сам flight не получает этим автоматически сигнал остановить начатую работу; остаётся server-side RPC limit control plane.

**Route timeout относится к переданному workload.** В xDS этого среза default равен 5 минутам и не включает предшествующий resume/parking. Client end-to-end deadline, connect timeout и stream idle timeout - ещё другие ограничения. Нельзя объявить «ответ всегда за 5 секунд» на основании park budget или сложить несвязанные p95 всех этапов и назвать сумму измеренной p95 запроса.

Для `review-42` сначала определите продуктовый срок: сколько пользователь готов ждать открытия ревью и сколько может идти generation. Затем измерьте весь путь и отдельные stages. Если штатный restore часто не помещается в окно processor, выбирайте более дешёвый resume, prewarming, другое окно ожидания или асинхронное принятие задачи. Простое увеличение клиентского timeout не изменит короткий timeout proxy.

## Lot ограничивает callers а не все операции

Parking lot - ограничитель числа запросов, уже перешедших к повторному ожиданию. Slot берётся после первой retryable ошибки flight. Успех первой попытки slot не занимает, даже если lot заполнен. Каждый parked caller занимает собственный slot: один hot Actor способен заполнить lot множеством клиентов при небольшом числе RPC.

Первая медленная попытка тоже ещё не занимает parking slot, хотя держит `ext_proc` stream и другие ресурсы. Поэтому lot нельзя считать полным лимитом всех одновременно выполняющихся resume, всей памяти router или всех HTTP connections.

Envoy circuit breaker, ограничитель активных запросов к processor, даёт другой предел. Auto-значение в этом срезе - `max(1024, 2 * parked-request-max)`: для lot 1024 получается 2048. Startup validation запрещает явный breaker меньше lot. Однако breaker ровно равный lot не оставляет резерва для быстрых requests. Другие listeners, соединения и ресурсы всё равно имеют собственные пределы.

Для начальной оценки можно использовать размерность «скорость входа в parking × среднее время occupancy». Например, при заданных 200 park transitions в секунду и среднем удержании 2 секунды получается около 400 занятых slots в устойчивом режиме. Это не гарантия для burst, skew одного Actor или хвостов restore. Сравнивайте распределение по replicas, время ожидания и rejection, а не только среднее.

При заполнении lot router возвращает 503 `router at capacity`. Увеличить lot - значит разрешить больше ожидания, не больше Workers. Если причина `Unavailable`, увеличение WorkerPool вообще не лечит недоступность control plane. Если причина `ResourceExhausted`, выясните, кто занимает пул и как быстро capacity освобождается.

## Практический пример границы parking

В `examples/ch15_parking_boundary_demo.py` есть маленькая модель с явно заданной временной шкалой. Python 3.10+ и стандартной библиотеки достаточно. Она не запускает network, Envoy или Substrate: methods вручную моделируют прибытие callers, ответ попытки и освобождение slot. Для наглядности lot равен двум, а бюджет - 5000 ms.

В начале `r1` и `r2` ждут одну попытку для `review-42`. На 100 ms она возвращает `ResourceExhausted`: оба callers занимают lot. `r3` не помещается и получает отказ. При этом запрос `fast` к уже работающему другому Actor успешно проходит первую попытку, не занимая lot. `r1` отключается, освобождая slot, но не уничтожая общий flight.

Следующая попытка начинается на 4800 ms. Клиент `late` приходит на 4900 ms и получает оставшиеся 100 ms бюджета новых повторов. На 5100 ms RPC всё ещё выполняется; модель не запускает третий и не отменяет второй. Успех приходит на 6200 ms и доставляется остающимся callers. Эти времена заданы для разбора, не измерены на кластере.

```
python3 examples/ch15_parking_boundary_demo.py
python3 -m unittest discover -s examples -p 'test_ch15*.py'
```

Основное решение отделяет начатую работу от возможности новой попытки:

```
def retry_action(last_result, *, elapsed_ms, budget_ms, in_flight):
    for value in (elapsed_ms, budget_ms):
        if type(value) is not int or value < 0:
            raise ValueError("times must be nonnegative integer milliseconds")
    if budget_ms == 0:
        raise ValueError("budget must be positive")
    if in_flight:
        return "WAIT_FOR_IN_FLIGHT"
    if last_result == "OK":
        return "ROUTE"
    if last_result not in RETRYABLE:
        return "FAIL_FAST"
    if elapsed_ms >= budget_ms:
        return "STOP_RETRIES"
    return "START_NEXT_ATTEMPT"
```

### Чтение результата

Полный вывод demo:

```
{
  "source": "supplied_timeline_and_process_local_model",
  "substrate_contacted": false,
  "review_flight_attempts": 2,
  "action_after_budget": "WAIT_FOR_IN_FLIGHT",
  "late_join_remaining_budget_ms": 100,
  "completed_ms": 6200,
  "results": {
    "fast": "ROUTE",
    "late": "ROUTE",
    "r1": "CALLER_CANCELED",
    "r2": "ROUTE",
    "r3": "ROUTER_AT_CAPACITY"
  },
  "parked_after_completion": 0,
  "other_router_rpc_starts": 1
}
```

У `review-42` две последовательные попытки, а не один RPC за всё время flight и не отдельный RPC для каждого клиента. `r1` не получает поздний успех, потому что перестал ждать. Второй экземпляр `RouterLab` начинает собственный RPC того же Actor: локальный registry не является общекластерным.

Модель показывает управление ожиданием, но не проверяет scheduling goroutines, порядок реального admission, jitter, xDS, server deadline, disconnect обнаружение, restart или body buffering. Она не обещает FIFO и сама не запускает таймер клиента: уход задаётся явно через `leave`. Для стенда нужны concurrent requests, controlled capacity failure и correlated RPC/stream traces. `ROUTE` в выводе означает решение модели направить запрос, не ответ приложения 200.

## CONNECT и tunnel решают разные задачи

HTTP CONNECT создаёт туннель, внутри которого идут другие байты. В книге есть два разных CONNECT-пути. На ingress клиент может выбрать нестандартный port Actor через CONNECT authority; Envoy завершает внешний tunnel и повторно обрабатывает внутренние HTTP requests через actor-aware routing. Так новый request внутри долгого tunnel может получить актуальный assignment после relocation.

На egress CONNECT формирует уже `atunnel`, а не доверенное решение приложения: передаёт PEP исходный `IP:port` перехваченного соединения. Оба механизма используют одно имя HTTP method, но разные identities и policy boundaries.

Arbitrary-port ingress этого среза обслуживает HTTP(S), а не любой raw TCP protocol. Требование SSH, database wire protocol, WebSocket или другого transport нужно проверять на выбранном пути до миграции. Открытый port и успешно созданный CONNECT ещё не доказывают поддержку внутренних bytes и прикладных semantics.

## Egress ограничивает действия недоверенного Actor

PEP, policy enforcement point, - место исполнения решения о доступе. В Substrate TCP Actor, кроме DNS на destination port 53, перехватывается nftables и передаётся `atunnel`. Тот строит mTLS CONNECT к PEP: обе стороны проверяют сертификаты, а Actor-specific client certificate остаётся в доверенной инфраструктуре tunnel.

PEP проверяет сертификат, извлекает atespace/name Actor и сверяет его существование и RUNNING через control plane. В этом handler отдельной сверки Actor UID нет; эти проверки сами по себе не доказывают совпадение lifetime после удаления и повторного создания Actor. Actor не становится доверенным оттого, что его certificate корректен: identity нужна, чтобы применить именно его ограничения. Приватный ключ tunnel не нужно выдавать процессу агента.

Имя в HTTP Host или TLS SNI, Server Name Indication, выбирает сам Actor. Эти значения могут быть входом policy, но не доказательством того, что исходный произвольный IP принадлежит разрешённому сервису. На hostname-based HTTP-пути gateway сам разрешает и набирает разрешённое имя. Разрешить `api.example.test` и затем послать байты на любой original IP - ошибочная модель защиты.

При этом hostname allowlist не доказывает, что DNS разрешённого имени никогда не укажет на чувствительный внутренний address. Сеть и доступ самого gateway также требуют ограничений. И даже корректный model endpoint может принять конфиденциальные данные: destination restriction не является DLP или проверкой аргументов tool.

DNS по TCP/UDP 53 идёт через relay к configured resolvers Worker, вне внешнего PEP; per-Actor `EgressPolicy` не фильтрует эти запросы. Остальной UDP и другие IP protocols блокируются; Actor networking здесь IPv4-only. Поэтому «default-deny egress» нельзя переводить в «у Actor нет никакого канала наружу» или «HTTP/3 заработает после allow port 443». DNS, QUIC и TCP - разные пути.

## Сначала проверьте возможность затем пишите policy

`EgressPolicy` - вложенный ресурс конкретного Actor, максимум одна policy с именем `default`. Это не Kubernetes NetworkPolicy. Parent Actor задаётся отдельно в API request; metadata policy с одним atespace не выбирает его автоматически.

Rules являются набором allow, отсутствие совпадения означает deny. Более конкретное hostname имеет приоритет над wildcard, конкретный port - над всеми ports. `*.example.test` покрывает ровно один непустой label: `api.example.test`, но не `example.test` и не `a.b.example.test`. API проверяет конфигурации, включая неоднозначные правила; порядок списка не следует использовать как систему deny/allow precedence.

| Rule или требование | Что даёт описанный путь ccecc788 | Существенная граница |
| --- | --- | --- |
| http | Решение по каждому cleartext HTTP request | Не получает credential injection в cleartext |
| https | SNI-проверка и решение по authority после TLS interception | Нужны sdsmint gateway и доверие TLS клиента к его CA |
| tlsPassthrough | Поле существует в API, но не исполняется как allow этим Envoy classifier | SNIRules не включает такие rules; само поле не открывает tunnel |
| Raw TCP, WebSocket, forward-proxy CONNECT, обычный UDP | Не считать поддержанными обычным hostname allow | Нужна отдельная проверка transport; для UDP кроме DNS действует drop |

Идея TLS passthrough в других реализациях - передавать зашифрованный поток до origin без HTTP inspection. Она полезна, например, при необходимости origin certificate pinning, но не даёт gateway читать headers и внедрять credential. В данном срезе это *не рабочий обход* ограничений MITM. Если приложение требует passthrough, выберите совместимую реализацию и докажите этот путь, а не добавляйте правило и отключайте certificate verification.

На HTTPS MITM leg отдельно проверяются SNI connection и authority каждого decrypted request. Смена Host внутри уже открытого HTTP/2 соединения не должна обходить request policy. Port для решения берётся из исходного dial, переданного trusted CONNECT, а не заменяется произвольным port из Host. API принятие policy, gateway allow и успешная операция приложения - три отдельных результата.

### Policy для нужных зависимостей

Для `review-42` составьте inventory: модель, публикация ревью, telemetry; отдельно отметьте инфраструктурные операции вроде pull image. Agent-side telemetry тоже является egress клиентом. Она не получает разрешение только потому, что нужна observability.

Следующий эскиз - **body EgressPolicy**, не самостоятельный Kubernetes manifest и не команда запуска. Parent Actor `team-a/review-42` передаётся в API отдельно. Имена `.test` обозначают ваши контролируемые endpoints, не готовые сервисы:

```
metadata:
  atespace: team-a
  name: default
rules:
- https:
    hostnames: ["models.example.test", "review-api.example.test"]
    ports:
      numbers: [443]
- http:
    hostnames: ["otel-collector.ate-system.svc"]
    ports:
      numbers: [4318]
```

Cleartext telemetry допустима только при соответствующей модели сети и данных; чувствительный export требует защищённого пути и поддержки клиента. Hostname restriction здесь не задаёт URL path, method, доступ к конкретному repository или MCP tool. Эти права проверяет origin, harness или broker соответствующего протокола.

## HTTPS interception меняет цепочку доверия

MITM здесь - управляемое завершение TLS на gateway, а не отключение проверки TLS. Приложение проверяет leaf, выпущенный gateway для нужного SNI; gateway создаёт отдельное TLS-соединение к origin. mTLS CONNECT между `atunnel` и PEP - ещё одна session. Ошибка Actor trust и ошибка gateway trust к origin чинятся на разных границах.

Для этого режима CA material можно проецировать через `systemInfo` volume с `trustBundle.name: egress-mitm.ate.dev`. В filesystem оказывается публичный trust bundle, не private key gateway. Но одного PEM-файла недостаточно: используемая TLS library должна действительно его читать. Python requests, Node.js, Java и curl не обязаны использовать один store. Configure и probe нужны для реального SDK; `curl -k` убирает проверку, которую требуется подтвердить.

Interception нарушает обычное pinning origin leaf. Кроме того, Full restore может вернуть TLS client со старым trust store или session в памяти. Проекция anchors при Run/Restore и reload уже созданного клиента - не одна операция. Rotation требует overlap и отдельной проверки fresh boot, непрерывной работы и resume. Подробная диагностика DNS, TLS и SDK находится в главе 40.

## Credential injection не равна готовой безопасности

Gateway может добавить API credential после проверки разрешённого HTTPS request, не помещая его в env, filesystem и snapshot Actor. Credential provider - отдельный сервис, который решает, какие secrets разрешены этому Actor/atespace, и читает secret store. Gateway получает значение для request и подставляет его upstream. Сам gateway остаётся частью доверенной области, а разрешённый origin может увидеть credential.

В этом срезе поддержка требует Envoy MITM leg и включённого provider client; shell install flag `--experimental-egress-credential-injection` также включает sdsmint. Provider deployment и его authorizations остаются отдельной зависимостью. Наличие effects в policy не запускает provider и не доказывает готовность всей установки.

Например, отдельное `https` rule для review API может содержать:

```
effects:
  replaceHeaders:
  - header: Authorization
    prefix: "Bearer "
    credentialUri: ate-secret://k8s.io/default/ns1/review-api/token
```

В URI указаны provider class, provider name и его собственный namespace/secret/key. Это ссылка на credential, а не его значение. Reference Kubernetes provider применяет default-deny atespace-to-namespace mapping; широкий Kubernetes RBAC provider не даёт любому Actor право читать любой Secret.

| Ситуация в applyEffects этого среза | Реальное поведение | Что проверить инженеру |
| --- | --- | --- |
| HTTPS MITM, provider настроен, credential доступен | FetchSecret и header overwrite-or-add | Нужная identity origin без возврата token Actor |
| Нет исходного Authorization header | Нет надёжного opt-out по отсутствию placeholder; header может быть добавлен | Не строить permission boundary на presence header |
| Provider не настроен или leg cleartext | Injection пропускается; request идёт без добавленного credential | Различать отсутствие функции и отказ попытки её выполнения |
| FetchSecret получает NotFound или PermissionDenied | 403, request не отправляется как обещанный authenticated вызов | URI, namespace mapping и scope |
| Provider недоступен или истёк его deadline | 503, попытка injection fail closed | Availability provider; не расширять allowlist |

В частности, код `applyEffects` не проверяет presence placeholder перед FetchSecret и использует `OVERWRITE_IF_EXISTS_OR_ADD`. Отсутствие header нельзя использовать как «безопасный вызов без привилегий». И provider, отключённый в конфигурации, не равнозначен подключённому provider, который вернул отказ. Если продукт требует запретить любой request без успешной injection, такую обязательность нужно обеспечить и проверить отдельно.

«Actor не получил token напрямую» также не означает «Actor не может использовать его полномочия». Разрешённый request уже использует credential; echo/debug endpoint способен вернуть его в response. Тестируйте на собственном canary origin с тестовым secret и безопасным marker вместо reflected headers. Ограничивайте origin permissions и операции, а не только хранение token.

## Как выбирать сетевой инструмент под задачу

| Требование | Нужный слой | Чего недостаточно |
| --- | --- | --- |
| Пользователь обращается только к своей сессии | Caller authn/authz и привязка product session к Actor | Клиентского target header и mTLS Worker |
| Найти конкретный Actor после resume | Actor-aware router и актуальный assignment | Cached Pod IP или одного Service к WorkerPool |
| Обязательно выполнить задачу после ухода клиента | Durable job/workflow и operation ledger | Request parking |
| Ограничить hosts и ports исходящих HTTP requests | Поддерживаемый egress PEP плюс защита сети gateway | Поля policy без работающего dataplane |
| Запретить опасный tool или подтвердить публикацию | Harness/tool broker, origin permissions и при необходимости approval | Hostname allowlist |

Не обязательно строить stateful Actor ради каждого короткого job. Если процесс легко запускается заново, данные и результат доступны через durable records, обычная job/service модель может оказаться проще. Substrate router полезен там, где адресуемая сессия и её меняющееся исполнение действительно часть задачи.

## Что измерять и как менять конфигурацию

Parking metrics называются `atenet.router.parking.active`, `atenet.router.parking.wait.duration` и `atenet.router.parking.rejected`. Active считает parked callers, не Actor и не число Workers. Wait duration относится к собственному parked времени caller; outcome `served` подтверждает завершение ожидания routing, не бизнес-успех. Rejected считает lot overflow, но не все возможные Envoy-generated отказы.

`budget_exhausted` означает, что закончился бюджет при retryable условии. Коррелируйте его с последним gRPC code: возможны дефицит capacity, lifecycle contention и control-plane outage. `timeout` относится к контексту ожидания caller, `canceled` - к его отмене, `error` - к non-retryable завершению. Без этого разделения autoscaling Workers легко назначить лечением недоступного API.

Policy cache gateway по умолчанию живёт 10 секунд, TTL можно настроить или отключить. Новый сохранённый resource не означает мгновенное новое решение всех replicas. HTTP request проверяется по headers; уже разрешённый response stream не обязан остановиться при revoke. Для немедленного отзыва нужен отдельный план закрытия sessions или upstream credential revocation, а не обещание TTL отменить уже выполненную операцию.

Router drain координируется с Envoy, но ограничен собственным сроком. Default drain timeout складывается из park budget, 10-секундного route drain budget и 5-секундного margin: при defaults 20 секунд. Это не пять минут route timeout и не 30 минут workload shutdown из главы 14. Startup guard `drain-timeout >= park budget` предотвращает одну ошибку конфигурации, но не гарантирует, что любая длинная generation переживёт rollout. Согласуйте router drain, Pod grace и требования к active streams отдельно.

## Проверка полезного пути и границы deny

До rollout зафиксируйте image/version, dataplane mode, policy version, Actor UID и безопасные canary destinations. Проверка из admin Pod не воспроизводит path sandbox. Выполните allow и deny внутри реального Actor, с тем же SDK и после resume; сначала на gVisor или microVM class, который действительно выбран, затем на другом, если продукт обещает оба.

Для ingress нужны running fast path, suspended resume, несколько concurrent callers одного Actor на одной и разных replicas, retryable capacity/API failure, lot overflow и unknown Actor. Отдельно отключите caller во время RPC и подтвердите судьбу Actor; проведите rollout во время parked request и долгого response stream. Измеряйте реальные stream/RPC outcomes, не количество «ошибок» без причины.

Для egress выберите живой разрешённый endpoint и живой запрещённый canary. Подтвердите policy deny *и отсутствие request на denied origin*. Несуществующий hostname доказывает только ошибку DNS. Проверьте wildcard apex/nested boundaries, другой port, смену authority, TLS trust и unsupported transport. Для injection отдельно нужны configured provider, отсутствие provider, отсутствие placeholder, запрещённый scope и outage; production secret в тесте не нужен.

Readiness gateway не заменяет эту программу: она не доказывает конкретный DNS, origin CA, полномочия credential или завершение streaming. Успешный короткий GET тоже не доказывает, что model API отдаёт полезные chunks, а review API безопасно обрабатывает повтор публикации. Не устраняйте неизвестный отказ разрешением `*`, отключением TLS verification или безусловным retry изменяющего request.

## Практика для самостоятельного решения

В модели уменьшите lot до одного slot, добавьте ещё caller того же Actor и затем другого Actor. Объясните, почему один flight может занять весь lot и почему первый быстрый lookup ещё проходит. Не объявляйте порядок admission гарантией FIFO.

Замените поздний успех на `Unavailable`, затем на `NotFound`; сравните результат и число новых попыток после 5000 ms. Отмените всех callers до completion и проверьте, что модель не возвращает им успех задним числом. Для реального стенда запишите, какие таймеры и traces потребуются, чтобы проверить то же поведение.

Для `review-42` распределите требования по слоям: доступ пользователя к сессии, поиск Worker, публикация с operation ID, запрет чужого hostname, запрет конкретного tool, неотражаемый credential. Какое требование нельзя закрыть одной EgressPolicy? Что изменится, если SDK требует origin pinning или HTTP/3? И чем подтвердить, что новая policy уже действует после resume, а не просто сохранена в API?

## Итоги главы

Ingress соединяет logical Actor с текущим исполнением; caller authorization разрешает обращение к сессии. Parking сглаживает краткое retryable ожидание, но не создаёт capacity, не является durable queue и не отменяет уже начатый resume по park budget. Local flight, parking slot и Envoy stream - разные единицы учёта.

Egress начинает с недоверенного Actor, подтверждает его identity и ограничивает доступ по возможностям конкретного dataplane. Наличие policy field не доказывает passthrough или готовую injection. Даже правильно разрешённая сеть не определяет бизнес-смысл запроса и не отменяет безопасную сверку неизвестного результата.

В главе 16 поднимемся к AX control plane: как продуктовые Task, Workspace и Model превращаются в операции Substrate и кто отвечает за статус, intent и runtime. DNS/TLS диагностику уже работающего egress пути подробно продолжает глава 40.

### Источники и дальнейшее чтение

- [Ingress handler](https://github.com/agent-substrate/substrate/blob/ccecc788a327dc11dcd6c21ee153f3d0cbb5cc97/cmd/atenet/internal/router/ingress/ingress.go), [ActorResumer](https://github.com/agent-substrate/substrate/blob/ccecc788a327dc11dcd6c21ee153f3d0cbb5cc97/cmd/atenet/internal/router/ingress/resumer.go) и [parking](https://github.com/agent-substrate/substrate/blob/ccecc788a327dc11dcd6c21ee153f3d0cbb5cc97/cmd/atenet/internal/router/ingress/parking.go): routing, local flights и admission.
- [Request parking design](https://github.com/agent-substrate/substrate/blob/ccecc788a327dc11dcd6c21ee153f3d0cbb5cc97/docs/request-parking.md), [xDS](https://github.com/agent-substrate/substrate/blob/ccecc788a327dc11dcd6c21ee153f3d0cbb5cc97/cmd/atenet/internal/router/xds.go) и [router config](https://github.com/agent-substrate/substrate/blob/ccecc788a327dc11dcd6c21ee153f3d0cbb5cc97/cmd/atenet/internal/router/config.go): разные budgets и limits.
- [Network egress contract](https://github.com/agent-substrate/substrate/blob/ccecc788a327dc11dcd6c21ee153f3d0cbb5cc97/docs/network-egress.md), [policy evaluator](https://github.com/agent-substrate/substrate/blob/ccecc788a327dc11dcd6c21ee153f3d0cbb5cc97/internal/egresspolicy/egresspolicy.go) и [Envoy classifier](https://github.com/agent-substrate/substrate/blob/ccecc788a327dc11dcd6c21ee153f3d0cbb5cc97/cmd/dataplane/envoy/dynamic-modules/egress-policy/README.md): trust model, matching и отсутствие passthrough allow.
- [Egress request handling](https://github.com/agent-substrate/substrate/blob/ccecc788a327dc11dcd6c21ee153f3d0cbb5cc97/cmd/atenet/internal/router/egress/request.go), [credential implementation](https://github.com/agent-substrate/substrate/blob/ccecc788a327dc11dcd6c21ee153f3d0cbb5cc97/cmd/atenet/internal/router/egress/credentials.go), [injection tests](https://github.com/agent-substrate/substrate/blob/ccecc788a327dc11dcd6c21ee153f3d0cbb5cc97/cmd/atenet/internal/router/egress/credentials_test.go) и [policy cache](https://github.com/agent-substrate/substrate/blob/ccecc788a327dc11dcd6c21ee153f3d0cbb5cc97/cmd/atenet/internal/router/egress/policycache.go): actual behavior вместо устаревшего описания placeholder.
- [CA projection](https://github.com/agent-substrate/substrate/blob/ccecc788a327dc11dcd6c21ee153f3d0cbb5cc97/docs/egress-trust-bundle.md), [install flags](https://github.com/agent-substrate/substrate/blob/ccecc788a327dc11dcd6c21ee153f3d0cbb5cc97/hack/install-ate.sh) и [Envoy/sdsmint manifest](https://github.com/agent-substrate/substrate/blob/ccecc788a327dc11dcd6c21ee153f3d0cbb5cc97/manifests/ate-install/atenet-egress-with-sdsmint.yaml): prerequisites и protocol boundary.
