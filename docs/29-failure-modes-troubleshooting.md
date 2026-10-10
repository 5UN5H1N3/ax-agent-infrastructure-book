# Failure modes и troubleshooting: как локализовать нарушенный контракт

**Снимок главы:** **AX main `ac233282` · Substrate main `df78882` · проверено 2026-10-10**. Команды и поля status относятся к этим снимкам и должны сверяться с закреплённой версией deployment. Issues ниже - наблюдения из конкретных окружений, а не доказательство, что любой похожий симптом имеет ту же причину.

Когда агент «не работает», перед инженером находится не одна программа, а цепочка контрактов: Kubernetes должен дать здоровый node и Pod; Substrate - выбрать Worker, восстановить Actor и провести трафик; AX - reconcile'ить декларацию; runner - подготовить workspace и запустить процесс; model и tools - ответить в пределах deadline; harness - правильно обработать результат. Один пользовательский симптом может возникнуть на любом из этих слоёв.

Troubleshooting - это не перебор команд и не искусство угадывать знакомую ошибку. Его цель - найти **последнюю подтверждённо исправную границу**, сформулировать одну проверяемую гипотезу о следующей границе и получить evidence, которое эту гипотезу подтверждает или опровергает. Такой подход одновременно сокращает MTTR и не даёт случайным рестартам уничтожить причину.

## Сначала отделите симптом, состояние и причину

| Термин | Пример | Что он доказывает |
| --- | --- | --- |
| Пользовательский симптом | «Task не дал первый ответ за 60 секунд» | Нарушен outcome/SLO, но слой ещё неизвестен |
| Наблюдаемое состояние | `Task Ready=True`, Actor `RUNNING`, model queue растёт | Компоненты сообщили свои локальные claims |
| Механизм отказа | Child process завершился, runner остался PID 1 | Как именно перестал выполняться контракт |
| Root cause | Необработанное исключение после несовместимого MCP response | Почему механизм возник и что нужно изменить системно |
| Contributing factor | Нет liveness signal приложения и terminal Task phase | Почему отказ долго не обнаруживался или усилился |

Не каждое расследование обязано сразу найти организационную «первопричину». Во время incident достаточно локализовать механизм, безопасно восстановить сервис и сохранить evidence. Root-cause analysis выполняется после стабилизации. И наоборот, фраза «Pod был перезапущен» описывает событие, а не причину: нужны termination reason, exit code, node pressure и предшествующий таймлайн.

## Метод последней подтверждённой границы

Идите не механически «снизу вверх», а от пользовательского запроса по его реальному пути. На каждой границе задавайте один и тот же вопрос: *какое наблюдение докажет, что вход был принят, работа действительно выполнена, а результат передан дальше?*

1. **Определите outcome.** Не «Task сломан», а «запрос `request_id=...` не вернул первый token до deadline» или «после resume отсутствует файл X».
2. **Зафиксируйте окно времени.** Переведите timestamps в UTC, отметьте последнюю успешную операцию, первую неуспешную и изменения deployment в этом окне.
3. **Постройте короткий timeline.** Client send → AX accepted → reconcile → Actor assignment → restore → runner ready → model/tool call → response.
4. **Найдите последний доказанный переход.** HTTP 200 от AX доказывает приём запроса, но не готовность workspace; `WorkspaceReady=True` может не доказать наличие ожидаемого checkout.
5. **Разделите путь независимыми probes.** Проверьте model endpoint без агента, MCP с той же identity, TLS из sandbox, readiness приложения внутри Actor.
6. **Изменяйте одну переменную.** Другой pool, clean Actor, cold snapshot или прямой endpoint - по одному, иначе результат не объясняет причину.
7. **Перед recovery сохраните evidence.** Перезапуск, delete/recreate и revert могут стереть logs, failed state и локальный filesystem.

Полезная запись гипотезы выглядит так: «Task ждёт не model, а Worker assignment, потому что возраст reconcile event растёт, Actor остаётся `SUSPENDED`, а scheduler возвращает `ResourceExhausted`; добавление свободного Worker в тот же selector должно завершить assignment». Она указывает слой, evidence и предсказание. «Наверное Kubernetes» не проверяется.

## Первые десять минут: сохранить факты, не чинить вслепую

До рестартов и удаления ресурсов соберите минимальный incident bundle. Конкретные команды зависят от версии и способа установки, но состав данных стабилен.

- точное время, timezone, request/task/actor/workspace identity и пользовательский deadline;
- commit, release, image digest и манифесты AX, Substrate, runner, sandbox runtime и model server;
- AX Task/Workspace/Gateway spec и полный status с conditions, reasons и timestamps;
- Substrate Actor, ActorTemplate, WorkerPool, Worker assignment, operation state и snapshot reference;
- Kubernetes Pod/Deployment/DaemonSet/Node state, recent Events, restarts, termination reason и resource pressure;
- логи control plane, controller, runner, `ateapi`, `atelet`, `ateom`, `atenet` и зависимостей в одном окне;
- trace/correlation IDs, latency milestones, queue age и relevant metric snapshots;
- результаты positive и negative probes без секретов: DNS, TCP, TLS, model, MCP и filesystem;
- последние изменения: rollout, policy, image, snapshot, secret, DNS, node drain, autoscaling.

Используйте `kubectl describe` и Events как дополнение, а не как единственный источник: Events имеют ограниченное retention, могут агрегироваться и не являются журналом всех переходов. Логи без version/digest тоже недостаточны - одинаковое имя image tag может скрывать разные binaries.

## Status - это утверждение компонента, а не end-to-end truth

Condition отвечает только на вопрос, который реализовал её producer. AX issue #346 показывает Task с `phase=Running` и `Ready=True` после завершения command и даже при `ACTOR_STATE_CRASHED` на нижнем слое. Issue #347 показывает `WorkspaceReady=True` при созданном, но пустом Git repository. Issue #345 описывает `GatewayReady=True`, когда hostname allowlist не пропускал ожидаемый TLS-трафик в исследованной версии.

Из этого не следует, что conditions бесполезны. Они полезны как локальные checkpoints. Для каждого operational claim нужен отдельный outcome probe:

| Claim | Что он обычно подтверждает | Как проверить пользовательский outcome |
| --- | --- | --- |
| `Ready=True` | Controller завершил предусмотренные reconcile steps | Проверить child process, application heartbeat и полезный response |
| `WorkspaceReady=True` | Runner считает bootstrap завершённым | Проверить expected ref/commit, файлы и доступность repo для процесса |
| `GatewayReady=True` | Policy resources применены | Positive probe разрешённого и negative probe запрещённого destination |
| Actor `RUNNING` | Actor получил Worker и lifecycle достиг running state | Проверить listener/readiness и первый state access после restore |
| Model HTTP 200 | Endpoint вернул transport-level success | Проверить schema, finish reason, token usage, quality и latency budget |

## Быстрая карта симптомов

| Симптом | Вероятная граница | Первое различающее evidence | Не делать первым |
| --- | --- | --- | --- |
| `ax apply` отклонён | Client/API/schema | HTTP/gRPC code, field path, server validation log | Перезапускать Workers |
| Task долго без Actor | AX reconcile | Возраст event, controller log, создан ли Actor | Увеличивать model replicas |
| Actor не получает Worker | Selector/capacity/scheduler | Pool labels, free slots, assignment outcome | Удалять snapshot |
| Actor застрял в resume | Snapshot/runtime/readiness | Restore phase timings, runtime log, probe result | Считать это только storage latency |
| Workspace «готов», но неверен | Runner/bootstrap | Expected commit против фактического HEAD/files | Анализировать prompt |
| Task Running, ответа нет | Child/model/tool | Process tree, heartbeat, active request trace | Доверять одному Task status |
| Только TLS egress не работает | DNS/SNI/policy/router | Раздельные DNS, TCP и TLS probes + atenet flags | Расширять allowlist до `*` в production |
| Model timeout | Queue/prefill/route | Server receive time, queue time, TTFT, GPU memory | Повторять без retry budget |
| Resume быстрый, первая команда медленная | Lazy restore/cache | Первый RAM/filesystem touch и restore breakdown | Смотреть только Resume RPC |
| Действие выполнено дважды | Tool retry/idempotency | Action key, attempt journal, remote audit log | Повторять вручную «для проверки» |

## Playbook 1: Task не создаётся или застрял до Running

Сначала разделите четыре разных случая: API не принял spec; API принял, но controller не увидел event; controller увидел, но не создал Actor; Actor создан, но не активирован. У них одинаковый внешний симптом «Task не стартовал», но разные owners и действия.

1. Сохраните client response целиком: transport code, validation field, Task identity и server request ID.
2. Прочитайте сохранённый spec с сервера и сравните с отправленным. Так отделяется client-side rendering/defaulting от server state.
3. Проверьте references: Workspace, Model, Gateway, image и имена/atespace. «Объект существует» недостаточно, если reference указывает другую область или версию.
4. Сопоставьте generation/spec revision с observed status и временем последнего reconcile. Старый status может относиться к предыдущей spec.
5. Проверьте возраст очереди controller и конкретный reconcile trace. Наличие здорового controller Pod не доказывает, что нужное событие обработано.
6. Если Actor создан, перенесите расследование на Substrate и больше не называйте проблему «AX API».

Для повторного reconcile сначала убедитесь, что операция идемпотентна и исходный attempt завершён или fencing исключает старого owner. Два одновременно работающих recovery path опаснее медленного одного.

## Playbook 2: Actor не resume или долго ждёт Worker

Resume состоит из нескольких стадий: выбрать подходящий WorkerPool, получить slot, назначить Worker, найти snapshot, восстановить runtime, дождаться readiness и сделать Actor routable. `no free workers`, несовместимый sandbox и повреждённый snapshot требуют разных решений.

1. **Eligibility:** совпадают ли ActorTemplate selector, labels pool, sandbox class, version pin и node placement?
2. **Capacity:** есть ли реально свободный Worker с достаточным CPU/RAM/actor budget, а не просто Running Pod?
3. **Assignment:** какой outcome и error type вернул scheduler; сколько времени заняла стадия?
4. **Snapshot lookup:** существует ли reference, принадлежит ли snapshot Actor/Tag, доступен ли object store и совместим ли формат с runtime?
5. **Restore:** разделите download, unpack/restore, sandbox start, first page/file touch и readiness.
6. **Application compatibility:** повторяется ли проблема на cold boot или только после checkpoint/restore? Работает ли другой минимальный runtime в том же pool?

Substrate request parking может удерживать входящий запрос при временном исчерпании pool до bounded deadline. Поэтому пользователь видит длинный request, а не мгновенный `503`. Смотрите одновременно parking duration, assignment failures и освобождение Workers. Увеличение client timeout не создаёт capacity и способно накопить ещё больше ожидающих запросов.

Если Actor в `CRASHED`, сначала определите, потеряна ли работа после последнего external snapshot. Revert возвращает к сохранённой точке и потому является data-loss decision, а не безобидным restart. Зафиксируйте snapshot version и получите согласие owner-а данных до отката.

## Playbook 3: workspace объявлен готовым, но содержимое неверно

Проверка «каталог существует» слишком слаба. Для Git-workspace contract должен включать как минимум ожидаемый remote, ref/commit, ненулевой checkout и доступ процесса к файлам. Для generated workspace нужны manifest или контрольные файлы, а не только readiness runner-а.

- сравните запрошенный branch/tag/SHA с `HEAD` и refs внутри workspace;
- проверьте exit status fetch/checkout, stderr и размер `FETCH_HEAD`, не публикуя credentials;
- выполните `git ls-remote` из той же sandbox identity и через тот же Gateway;
- проверьте mount path, ownership, permissions и то, что agent process видит тот же namespace;
- отделите пустой корректный repository от неуспешного checkout явным acceptance invariant;
- после resume сравните durable path и ephemeral path: данные могли быть записаны не в snapshot-managed filesystem.

AX issue #347 полезен не как универсальный диагноз, а как пример **ложноположительной readiness**: bootstrap step объявлен завершённым при неполном результате. Исправление класса проблемы - сделать postcondition проверяемой и failure loud, а не добавить sleep перед запуском агента.

## Playbook 4: Task Running, но полезный процесс умер или завис

Runner как PID 1 и child agent - разные процессы и разные health contracts. Живой runner может обслуживать debug/readiness, пока command давно завершился. И наоборот, долгий model call может выглядеть как hang, хотя процесс жив и ждёт внешний dependency.

1. Проверьте process tree, PID child, start time, state, exit code или zombie; не ограничивайтесь PID 1.
2. Проверьте application heartbeat/progress marker: последний завершённый agent step, model/tool request и budget.
3. Получите stack/thread dump безопасным для runtime способом; перед kill сохраните stderr и core/diagnostic data, если policy разрешает.
4. Сопоставьте cgroup OOM/throttling и node eviction с application logs. Exit без traceback может быть внешним kill.
5. Для hang проверьте deadline и cancellation propagation: client мог уйти, а tool/model request продолжает занимать ресурс.
6. Определите terminal semantics. Если процесс должен завершаться, успешный exit не является failure; если это service, exit 0 всё равно нарушает availability contract.

Issue AX #346 показывает gap конкретной версии: Task status не позволял отличить выполняющийся command от завершившегося. Пока platform status не несёт этот сигнал, добавьте собственную метрику/heartbeat, alert на отсутствие progress и контролируемый supervisor policy. Бесконечный auto-restart без ограничения может скрыть crash loop и повторить side effects.

## Playbook 5: сеть и egress - проверять по уровням

«Интернет не работает» объединяет минимум DNS, route, TCP, TLS, HTTP, proxy policy и authorization приложения. Один `curl` не всегда показывает, где отказ.

1. **DNS:** какое имя запросил процесс, какой resolver ответил, какие A/AAAA/CNAME получены, совпадает ли namespace/search domain?
2. **TCP:** устанавливается ли соединение к конкретному IP:port; нет ли IPv6/IPv4 расхождения?
3. **TLS:** какой SNI отправлен, какой certificate chain получен, доверяет ли ему sandbox CA store, где возник alert?
4. **Policy/router:** какое правило выбрано, какой destination увидел `atenet`, почему соединение разрешено или отклонено?
5. **HTTP/application:** status, redirect target, proxy/auth headers и server-side request ID.

Всегда делайте пару probes: разрешённый destination должен пройти, запрещённый - предсказуемо не пройти. Только positive probe не доказывает enforcement; только negative не отличает строгую policy от полностью сломанной сети. Issue AX #345 показал случай, где `GatewayReady=True` означал применение resources, но не успешный TLS outcome для hostname rule в исследованной версии.

Не оставляйте wildcard egress как «временное» production-исправление. Для диагностики его можно использовать только в изолированном test Task с коротким временем жизни и зафиксированным сравнением. Иначе вы одновременно меняете network path и снимаете security boundary.

## Playbook 6: model или MCP медленные и ошибочно выглядят как проблема AX

Если Actor и приложение живы, вынесите dependency из agent loop. Выполните минимальный запрос из того же network namespace, с той же identity, route и timeout. Запрос с ноутбука инженера проверяет другой путь.

| Dependency | Разделить latency | Проверить correctness |
| --- | --- | --- |
| Model | DNS/TLS, server queue, prefill/TTFT, decode/ITL | Model ID, context limit, schema, finish reason, token count |
| MCP | Connect/initialize, discovery, tool execution, downstream API | Protocol/version, tool schema, identity, authorization, result shape |
| Direct tool/API | Client queue, transport, server processing, async completion | Idempotency key, resource version, remote audit record |

Timeout означает «клиент не дождался», а не «операция не выполнилась». Перед retry write-action проверьте remote system по idempotency key или operation ID. Retry без этого превращает сетевую неопределённость в duplicate side effect.

## Playbook 7: suspend/resume и snapshot path

Разделите control-plane duration и пользовательский recovery. Быстрый `ResumeActor` может вернуть assignment раньше, чем lazy pages или filesystem blocks понадобятся приложению; реальная задержка проявится на первом полезном доступе.

- запишите snapshot kind, owner, version, размер, время создания и runtime/build compatibility;
- сравните cold boot, golden restore и Actor-owned restore на одном image/runtime;
- снимите phase timings: lookup, download, restore, readiness и first touch;
- проверьте object-store latency/errors, bandwidth, node cache и concurrent restores;
- проверьте, что приложение tolerates checkpoint/restore: sockets, threads, clocks и external leases могут устареть;
- после resume проверяйте semantic state - не только PID, но и ожидаемый файл, memory counter или способность обслужить запрос.

Если golden snapshot строится, а Actor-owned restore ломается, сравните изменения state после первого запуска. Если cold boot работает, а любой restore нет, подозревайте runtime/checkpoint compatibility. Если все варианты медленны только одновременно, возвращайтесь к capacity chapter: вероятен shared storage/network bottleneck, а не повреждение отдельного snapshot.

## Playbook 8: runaway child Tasks и повторные действия

Здесь инфраструктура часто здорова: failure находится в harness policy. Coordinator не видит ACK, считает child потерянным и создаёт новый; tool выполнил действие, но response потерялся; retry снова вызывает write. Симптомом становится рост Tasks, затрат или внешних объектов.

1. Остановите дальнейший fan-out через admission/circuit breaker, сохранив уже созданные Tasks для evidence.
2. Соберите parent/root Task ID, spawn reason, attempt number, idempotency key, depth и budget для каждого child.
3. Отделите дубликат вычисления от дубликата внешнего эффекта; второй требует проверки remote audit log.
4. Проверьте, различает ли harness timeout, explicit failure, cancellation и unknown outcome.
5. Восстанавливайте работу от durable journal/checkpoint, а не из памяти coordinator-а.
6. Добавьте bounded depth/fan-out, global budget, deduplication и propagation cancellation до повторного включения.

## Recovery: минимальное обратимое действие

Правильное recovery устраняет активный механизм отказа и сохраняет возможность понять его позже. Выбирайте наименьшее действие, которое проверяет гипотезу:

- отменить один зависший request раньше, чем рестартовать весь control plane;
- дать один совместимый Worker нужному pool раньше, чем масштабировать все pools;
- создать clean test Actor раньше, чем удалять проблемный Actor и его snapshot;
- переключить canary на известный digest раньше, чем массово откатывать deployment;
- изолировать background class раньше, чем отключать admission для всех;
- revert snapshot только после явной оценки потерянного интервала.

После действия проверьте не только исчезновение alert, но и первоначальный user outcome, отсутствие backlog, прекращение retries, восстановление error budget и сохранение security policy. «Pods зелёные» не закрывает incident, если Tasks по-прежнему не дают полезный результат.

## Как оформить escalation без пересказа на час

Хороший handoff позволяет следующему инженеру продолжить с текущей границы, а не повторять всё расследование.

- **Impact:** какие workload classes, сколько Tasks, с какого времени, какой SLO нарушен.
- **Known good / first bad:** версии, timestamps и последняя успешная операция.
- **Timeline:** 5-10 ключевых событий с correlation IDs.
- **Last proven boundary:** что точно работает и каким probe доказано.
- **Current hypothesis:** evidence за/против и предсказание следующей проверки.
- **Actions:** что уже меняли, exact result и что нельзя делать из-за риска state/side effects.
- **Artifacts:** sanitized logs, specs/status, metrics, traces, digests и reproduction.

## Антипаттерны troubleshooting

- **Restart first.** Симптом исчезает вместе с volatile evidence, причина остаётся.
- **Grepping только слово error.** Полезный signal может быть timeout, cancellation, queue age или отсутствие события.
- **Один огромный log bundle без timeline.** Данные есть, но причинный порядок потерян.
- **Проверка из ноутбука инженера.** Она обходит identity, DNS, Gateway и sandbox path workload-а.
- **Доверие одному Ready.** Локальный claim выдаётся за пользовательский outcome.
- **Одновременная смена image, pool и policy.** Успех невозможно связать с причиной.
- **Retry write после timeout.** Unknown outcome превращается в duplicate effect.
- **Delete/recreate stateful Actor.** Recovery уничтожает snapshot и точку расследования.
- **Увеличение timeout вместо устранения queue.** Система дольше удерживает бесполезную работу.
- **Объявить root cause по совпадению.** Корреляция с rollout полезна, но механизм нужно подтвердить.

## Итог

Диагностика AX/Substrate начинается с точного пользовательского outcome и проходит по цепочке контрактов. Сохраняйте evidence до recovery, стройте timeline, ищите последнюю подтверждённую границу и проверяйте следующую независимым probe. Status, лог и metric по отдельности редко дают end-to-end truth; сильный вывод появляется, когда control-plane state, runtime evidence и пользовательская проверка согласованы.

Следующая глава использует накопившееся operational понимание для решения более раннего вопроса: нужен ли конкретному workload сам дополнительный control plane AX/Substrate. Чем дороже система в диагностике, upgrades и on-call expertise, тем убедительнее должна быть польза от isolation, durable Actors и oversubscription.

### Источники и дальнейшее чтение

- [AX issue #346: Task остаётся Running/Ready после завершения command](https://github.com/google/ax/issues/346)
- [AX issue #347: пустой Git checkout при WorkspaceReady](https://github.com/google/ax/issues/347)
- [AX issue #345: hostname egress allowlist и TLS outcome](https://github.com/google/ax/issues/345)
- [Substrate architecture: assignment, hydration и snapshot lifecycle](https://github.com/agent-substrate/substrate/blob/df788825e6fc13dd7aa5fd283c8f14bd4c818247/docs/architecture.md)
- [Substrate API guide: WorkerPool, ActorTemplate и recovery semantics](https://github.com/agent-substrate/substrate/blob/df788825e6fc13dd7aa5fd283c8f14bd4c818247/docs/api-guide.md)
- [Substrate observability: lifecycle, scheduler, restore и WorkerPool signals](https://github.com/agent-substrate/substrate/blob/df788825e6fc13dd7aa5fd283c8f14bd4c818247/docs/observability.md)
- [Substrate request parking: bounded wait при pool saturation](https://github.com/agent-substrate/substrate/blob/df788825e6fc13dd7aa5fd283c8f14bd4c818247/docs/request-parking.md)
- [Kubernetes: debugging running Pods](https://kubernetes.io/docs/tasks/debug/debug-application/debug-running-pod/)
