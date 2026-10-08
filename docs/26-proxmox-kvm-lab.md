# Лабораторный стенд на Proxmox/KVM: от первого запуска до production rehearsal

**Снимок главы:** **AX main `ac233282` · Substrate main `df78882` · проверено 2026-10-08**. Команды и API относятся к этим снимкам; оба проекта pre-1.0, поэтому перед повторением сверяйте актуальные README и release notes.

Лабораторный стенд нужен не для того, чтобы один раз увидеть зелёные Pods. Его задача — дать инженеру среду, в которой можно воспроизводимо ответить на вопросы: где проходит граница между AX, Substrate и Kubernetes; что переживает перезапуск; как Actor просыпается; где действует network policy; что происходит при нехватке Worker capacity; и какой слой сломался, когда Task перестал продвигаться.

Если собрать всё в одной VM без явной модели, первый успешный запуск создаёт ложное чувство понимания. Затем ошибка DNS выглядит как проблема AX, заполненный диск — как дефект snapshot, а отсутствие `/dev/kvm` — как неисправный Kubernetes manifest. Поэтому в этой главе мы сначала проектируем *учебный эксперимент*, затем выбираем Kind, k3s или kubeadm и только после этого устанавливаем платформу.

![Три лабораторные топологии: растёт не «мощность Kubernetes», а реалистичность отказов и операционная цена](.gitbook/assets/diagrams/26-24.png)  
*Три лабораторные топологии: растёт не «мощность Kubernetes», а реалистичность отказов и операционная цена*

## Что именно мы строим

Proxmox, KVM, guest VM, container runtime, Kubernetes и agent platform — не синонимы и не один «кластер». Это вложенные уровни с разными обязанностями.

| Уровень | Что он предоставляет | Что проверять первым | Типичная ложная диагностика |
| --- | --- | --- | --- |
| Физический host | CPU, RAM, SSD/NVMe, NIC и аппаратную виртуализацию | pressure, I/O latency, firmware virtualization, время | «Kubernetes тормозит», хотя host ушёл в swap или storage saturated |
| Proxmox/KVM | VM lifecycle, virtual disks, bridges, snapshots, passthrough | VM limits, bridge/VLAN, disk mode, CPU type | «Pod не видит сеть», хотя пакет не выходит из virtual bridge |
| Guest Linux | kernel, systemd, filesystem, firewall, clock, container runtime | DNS, routes, free space/inodes, cgroups, kernel modules | «Image сломан», хотя guest не резолвит registry |
| Kubernetes distribution | API, scheduling, Pod/service network, storage integration | Nodes, CoreDNS, CNI, StorageClass, events | «AX не создаёт Task», хотя базовый Pod не стартует |
| Substrate | Actor lifecycle, WorkerPool, routing, suspend/resume | control API, atelet, workers, router, PostgreSQL/object store | «Runner завис», хотя Actor не получил Worker |
| AX | Task/Workspace/Model и orchestration поверх Substrate | Redis, control plane, Task conditions, Actor mapping | «LLM плохая», хотя Task ждёт инфраструктуру |

Полезное правило: **не устанавливайте следующий уровень, пока не доказана работоспособность предыдущего**. До Substrate обычный Pod должен запускаться, резолвить Service и записывать данные в выбранный volume. До AX counter demo должен пройти create → route → suspend → resume. Иначе каждый новый компонент умножает число гипотез.

## Сначала вопрос, потом distribution

Kind, k3s и kubeadm не являются тремя ступенями качества. Это инструменты для разных экспериментов.

| Критерий | Kind | k3s | kubeadm |
| --- | --- | --- | --- |
| Модель nodes | Containers внутри одной Linux-системы | Обычные Linux hosts/VM; server и agent | Обычные Linux hosts/VM; явный control plane и workers |
| Главная сила | Быстро создать, удалить и воспроизвести cluster | Небольшая операционная цена постоянного кластера | Явно показывает стандартные mechanics Kubernetes |
| Что выбирает за инженера | Почти всю node plumbing для локального теста | Много packaged defaults и компонентов | Мало: CNI, ingress, CSI, LB и HA проектируете сами |
| Что хорошо проверять | Manifests, APIs, controllers, demos, reset/recreate | Homelab, edge, длительную работу, небольшой multi-node | Node lifecycle, network/storage choices, version skew, upgrades |
| Чего он не доказывает | VM/node failure domains, production storage и сеть | Что ваши решения совпадут с обычным upstream cluster | Production readiness приложения без дополнительных tests |
| Цена ошибки | Удалить cluster и повторить | Восстановить persistent lab | Разобрать control-plane/node/storage последствия |

### Kind: лаборатория control-plane поведения

Kind запускает Kubernetes nodes как containers и использует подготовленные node images с Kubernetes-компонентами. Это не «игрушечный API»: controllers, scheduler, kubelet и Service abstractions настоящие. Но физическая граница node подменена container boundary. Несколько Kind workers в одной VM не становятся несколькими независимыми machines: отказ guest kernel, Docker daemon, диска или bridge затронет их одновременно.

Kind выбирают, когда главный вопрос звучит так: «правильны ли manifests и lifecycle?» Он особенно полезен здесь, потому что Substrate на зафиксированном снимке имеет поддерживаемый development path: скрипт создаёт Kind cluster и local registry, другой устанавливает Substrate, PostgreSQL и RustFS. Среду можно удалить целиком и убедиться, что инструкция воспроизводима.

Kind неудобен, если эксперимент зависит от реального node reboot, отдельного NIC, production CSI, GPU topology или microVM. `extraPortMappings` и `extraMounts` полезны, но не превращают container-node в VM. Multi-node Kind следует использовать для scheduler/topology tests, а не как доказательство HA.

### k3s: долгоживущий кластер с меньшей операционной ценой

K3s упаковывает Kubernetes distribution с практичными defaults и может работать на небольших Linux nodes. Это разумный второй стенд: его не приходится пересоздавать после каждого эксперимента, к нему проще подключить persistent storage и ingress, а VM уже являются настоящими failure domains.

Удобство имеет цену: packaged CNI, ingress, ServiceLB, datastore и другие choices могут отличаться от целевой production-среды. Инженер обязан выписать, какие bundled components включены, какие отключены и чем заменены. Иначе фраза «на k3s работает» мало говорит о переносимости. K3s выбирают для homelab/edge и непрерывных integration tests, но не для обучения каждой детали bootstrap.

### kubeadm: конструктор, а не готовая платформа

Kubeadm создаёт minimum viable conformant cluster и управляет bootstrap/upgrade mechanics. Он намеренно не решает весь stack: container runtime готовится отдельно, Pod network устанавливается отдельно, а storage, load balancer, ingress, certificates и backup требуют выбранных реализаций.

Это лучший вариант, когда учебная цель — понять, почему CoreDNS не станет Ready до CNI, чем `control-plane-endpoint` отличается от адреса конкретного API server, как присоединяется node и что означает version skew. Но kubeadm — плохой первый шаг для изучения AX: слишком легко потратить неделю на CNI и ни разу не создать Task.

## Рекомендуемая лестница стендов

Не пытайтесь сделать один cluster одновременно быстрым scratchpad, стабильным homelab и production rehearsal. Эти цели конфликтуют.

1. **Lab A — disposable Kind.** Одна VM. Проверяет сборку, manifests, Substrate API, AX lifecycle и observability contract. Reset — штатная операция.
2. **Lab B — persistent k3s.** Одна или три VM. Проверяет длительную работу, storage, ingress, egress, backups и node maintenance с небольшой ops-ценой.
3. **Lab C — kubeadm rehearsal.** Не менее трёх VM для meaningful failure tests; topology зависит от цели. Проверяет CNI/CSI/LB choices, upgrades, drain, loss of node и recovery.

В книге базовым путём остаётся Lab A: **Proxmox → Debian/Ubuntu VM → Docker → Kind → Substrate → AX**. Это не рекомендация для production. Это кратчайший путь от чистого host до измеряемого Task без скрытого облачного control plane.

## Проектирование VM на Proxmox

### CPU, память и overcommit

Выделенные VM resources — верхняя граница, а не гарантия производительности. Если Proxmox host перегружен соседними VM, восемь vCPU могут давать меньше работы, чем четыре свободных core. Для лаборатории важнее стабильный baseline, чем красивое число vCPU.

Практические стартовые профили, а не обещание:

| Профиль | Стартовая VM | Что помещаем | Когда увеличивать |
| --- | --- | --- | --- |
| Control-plane learning | 8 vCPU, 16 GiB RAM, 100 GiB SSD | Kind, Substrate, AX, лёгкий demo; inference вне VM | OOM, eviction, долгий image unpack, нехватка места |
| Lifecycle/snapshot tests | 12–16 vCPU, 24–32 GiB RAM, 200+ GiB SSD/NVMe | Несколько workers, repeated suspend/resume, telemetry | restore queue, I/O saturation, page cache pressure |
| Multi-node rehearsal | Отдельные VM для control plane и workers | k3s/kubeadm, реальные node boundaries | По измеренному workload и выбранной HA topology |

Не запускайте большую local LLM в той же VM на первом этапе. Inference заберёт RAM/VRAM, page cache и disk bandwidth, а инженер станет одновременно отлаживать orchestration и model server. Подключите внешний model API или отдельную GPU VM; объединяйте слои только когда каждый имеет собственный baseline.

### Диск и snapshots

На одном virtual disk смешиваются container layers, image registry, PostgreSQL, object storage, logs и Actor snapshots. Заполненный диск может остановить сразу все planes. Для долговременного стенда полезно разделить хотя бы OS/container cache и stateful data либо задать квоты и отдельные alerts.

Proxmox snapshot фиксирует состояние VM/block device, но не гарантирует application-consistent backup PostgreSQL, Redis или object metadata. Он удобен как быстрый recovery point перед опасным упражнением; backup strategy всё равно проверяется средствами приложений. Снимок всей VM также не объясняет, какие именно данные требуются для восстановления платформы — этому посвящена следующая глава.

### Сеть

До установки выпишите CIDR на бумаге. В Lab A одновременно существуют как минимум:

- LAN/VLAN Proxmox и адрес guest VM;
- Docker network, в которой живут Kind nodes;
- Kubernetes Pod CIDR;
- Kubernetes Service CIDR;
- возможно, VPN, corporate proxy и сети внешних MCP/model endpoints.

Пересечение диапазонов создаёт ошибки, похожие на случайный packet loss: маршрут выбирается не туда, Service доступен из одного namespace и недоступен с host, proxy перехватывает внутренний адрес. Для kubeadm Pod network CIDR должен быть согласован с CNI и не пересекаться с host networks. Для Kind при proxy-настройке проверьте `NO_PROXY` для cluster-local и node ranges.

Не публикуйте все control-plane ports на `0.0.0.0` ради удобства. Для первого доступа достаточно `kubectl port-forward` или port mapping, привязанного к management address. Затем явно спроектируйте ingress, TLS и firewall. «Открылось из браузера» не равно корректной service exposure.

### Nested virtualization

Kind внутри VM не требует nested KVM: его nodes — containers. MicroVM runtime требует, чтобы аппаратная виртуализация прошла через Proxmox в guest и внутри был доступен `/dev/kvm`. Проверка должна быть отдельным gate:

```
test -e /dev/kvm
ls -l /dev/kvm
grep -E -m1 'vmx|svm' /proc/cpuinfo
```

Наличие флага CPU ещё не доказывает, что процесс имеет permission открыть устройство. Проверьте группу/ownership и выполните маленький runtime-specific smoke test. Для gVisor KVM не является универсальным обязательным условием: требования зависят от выбранной platform `runsc`. Всегда проверяйте конкретный runtime contract, а не слово «sandbox».

## Воспроизводимость: стенд как продукт

Хорошая лаборатория собирается из пустой VM вторым инженером без устных подсказок. Для этого нужен небольшой bill of materials:

- версия и configuration VM template;
- guest OS и kernel;
- container runtime;
- Kind/k3s/Kubernetes version;
- Substrate и AX commit SHA;
- image digests, а не только mutable tags;
- Pod/Service/LAN CIDR и DNS assumptions;
- storage classes и пути данных;
- способ выдачи credentials без сохранения secrets в Git;
- acceptance tests и способ полного teardown.

Cloud-init template, declarative VM provisioning и install scripts уменьшают drift, но не заменяют verification. Скрипт может завершиться с кодом 0, а нужный webhook ещё не готов. Каждый этап должен иметь наблюдаемый postcondition.

## Lab A: путь от чистой VM до Substrate

Ниже — маршрут, а не вечный install script. Команды Substrate привязаны к снимку `df78882`; перед запуском прочитайте соответствующий README.

### Gate 0. Guest готов

```
uname -a
timedatectl status
df -h
df -i
ip route
getent hosts registry.k8s.io
docker info
```

Здесь нас интересуют не сами строки вывода, а факты: часы синхронизированы, root filesystem и inodes имеют запас, default route и DNS работают, Docker использует ожидаемые cgroups и может скачать/запустить тестовый image.

### Gate 1. Kubernetes работает без платформы

На зафиксированном Substrate quickstart создание cluster и local registry автоматизировано:

```
hack/create-kind-cluster.sh
kubectl cluster-info
kubectl get nodes -o wide
kubectl get pods -A
kubectl wait --for=condition=Ready node --all --timeout=5m
```

Запустите отдельный smoke Pod, проверьте DNS Service и исходящий HTTPS. Если используется volume, запишите marker, пересоздайте Pod и докажите ожидаемую persistence semantics. Только после этого fault считается выше уровня Kubernetes.

### Gate 2. Substrate проходит собственный lifecycle

```
hack/install-ate-kind.sh \
  --deploy-ate-system \
  --credential-provider='{"name":"k8s.io"}'

hack/install-ate-kind.sh --deploy-demo-counter
go install ./cmd/kubectl-ate

kubectl ate create actor my-counter-1 \
  -a ate-demo-counter \
  --template counter

kubectl port-forward -n ate-system svc/atenet-router 8000:80
```

В другом terminal запрос к router должен попасть в нужный Actor:

```
curl -X POST \
  -H 'ate-target-actor: ate-demo-counter/my-counter-1' \
  -i http://127.0.0.1:8000/
```

Не останавливайтесь на первом HTTP 200. Зафиксируйте Actor state, Worker assignment и response; выполните suspend/resume; повторите запрос и проверьте сохранение counter state. Затем удалите Actor и убедитесь, что маршрутизация больше не выдаёт старый процесс. Это превращает demo в acceptance test.

Зафиксированный quickstart разворачивает development PostgreSQL и RustFS. Их присутствие демонстрирует зависимости Substrate, но не делает их HA или backup-ready. Пустая default-deny policy credential provider также означает, что secret injection не «появится сама»: namespace access нужно разрешать отдельно.

### Gate 3. AX добавляется только поверх здорового Substrate

На снимке AX `ac233282` prerequisites требуют работающий Substrate API, Go, `kubectl`, `ko` и registry, доступный cluster. Проверка границы начинается с Service:

```
kubectl get svc api -n ate-system
make deploy AX_IMAGE_REPO=<registry-доступный-из-cluster>
kubectl get pods -n ax-system
ax ctx
```

После установки примените минимальный Workspace/Model/Task manifest и пройдите цикл:

```
ax apply -f examples/task.yaml
ax get tasks
ax watch task <task-name>
ax ssh <task-name> -- ls -la /workspace
ax suspend task <task-name>
ax resume task <task-name>
ax delete task <task-name>
```

`AX_IMAGE_REPO` — частая граница ошибки. Image может существовать на host, но быть недоступным Kind node. Либо используйте registry, который резолвится и доступен из node containers, либо загрузите pinned image в cluster осознанно. Mutable `latest` затрудняет повторяемость и взаимодействует с `imagePullPolicy`; для лабораторного протокола сохраняйте digest.

## Минимальная наблюдаемость с первого запуска

Глава 25 закончилась telemetry contract; здесь его нужно проверить. До первого Task сохраните:

- VM CPU/memory/disk/network и time sync;
- Kubernetes events, Pod status/restarts и node conditions;
- Substrate component logs, Actor state и Worker assignment;
- AX Task conditions/events и соответствующий Actor identity;
- latency model/tool calls, если они участвуют в сценарии;
- точные commit SHA и image digests.

В disposable lab допустим простой backend и короткая retention. Недопустимо только отсутствие correlation: по одному Task инженер должен пройти от AX status к Actor, Worker, Pod и node. Сначала докажите golden path, затем инъецируйте один fault за раз.

## Fault exercises, которые чему-то учат

| Эксперимент | Что он проверяет | Ожидаемое доказательство |
| --- | --- | --- |
| Удалить AX control-plane Pod | Reconciliation и separation control/data state | Task не дублируется; control plane восстанавливает view |
| Заполнить WorkerPool | Parking/backpressure и capacity signals | Запрос ждёт/отклоняется по документированной semantics |
| Suspend/resume Actor | Snapshot, routing и state continuity | Состояние сохраняется, новая activation связана с прежним Actor |
| Остановить model endpoint | Timeout/retry budget и видимость ошибки | Нет бесконечного retry; Task condition объясняет причину |
| Сломать DNS только в Pod | Разделение host/guest/cluster network | Host может резолвить, Pod — нет; fault локализован до CoreDNS/CNI/policy |
| Перезагрузить VM | Что реально durable в одноузловом стенде | Составлен список восстановившихся и потерянных состояний |

В Kind перезагрузка VM — общий отказ всех псевдо-nodes. Для проверки независимого node failure переходите к нескольким VM. Не делайте из результата неверный вывод, будто три Kind worker доказали устойчивость к потере hardware node.

## Диагностика снизу вверх

Когда «AX не работает», не начинайте с переустановки. Сужайте fault domain.

1. **Host/VM:** VM Running? Есть pressure, disk latency, свободные blocks/inodes, корректное время?
2. **Guest:** Работают route, DNS, TLS к registries, container runtime и firewall?
3. **Kubernetes:** Nodes Ready? CoreDNS Running? Есть CNI/CSI errors, Pending Pods, failed mounts и scheduling events?
4. **Substrate:** Control API доступен? WorkerPool имеет capacity? atelet и router здоровы? Actor получил Worker?
5. **AX:** Redis/control plane Ready? Каковы Task conditions? Создан ли соответствующий Actor?
6. **Agent path:** Доступны model, MCP и egress? Не ждёт ли Task approval? Есть ли verified outcome?

Собирайте evidence до destructive reset. Для Kind экспорт cluster logs полезнее скриншота терминала; для systemd-based k3s/kubeadm сохраняйте journal конкретного unit и Kubernetes events. После сбора у стенда должен быть короткий и проверенный teardown path.

## Критерии готовности лаборатории

Стенд готов не тогда, когда dashboard зелёный, а когда выполняются проверяемые условия:

- чистая VM разворачивается по документу без ручных исправлений;
- версии и images pinned, а secrets не попали в Git и shell history;
- обычный Kubernetes smoke test проходит до установки платформы;
- Substrate counter переживает suspend/resume с подтверждённым state;
- AX Task проходит create/watch/ssh/suspend/resume/delete;
- по Task ID можно найти Actor, Worker, Pod и node;
- один capacity fault и один network fault диагностируются по runbook;
- teardown освобождает cluster, registry volumes и временные credentials;
- повторная сборка даёт тот же результат либо drift объяснён.

## Итог

Выбор между Kind, k3s и kubeadm — это выбор требуемой *fidelity*, а не соревнование дистрибутивов. Kind даёт быстрый и воспроизводимый путь к API/lifecycle experiments. K3s полезен как постоянный небольшой cluster. Kubeadm заставляет инженера владеть network, storage, endpoint, node и upgrade decisions и потому подходит для production rehearsal.

Базовый стенд книги начинается с Kind в одной VM, потому что он минимизирует число неизвестных и совпадает с development quickstart Substrate. Но мы не выдаём container-nodes за независимые machines, Proxmox snapshot — за application backup, а первый успешный Task — за production readiness. Стенд считается полезным, когда он воспроизводится, наблюдается, ломается контролируемо и объясняет, какой слой отвечает за результат.

Следующая глава поднимет тот же вопрос на production-уровень: какие stateful planes требуют HA и backup, почему дополнительные replicas сами по себе не дают доступность, как планировать upgrades и как доказать восстановление через DR drill.

### Источники и дальнейшее чтение

- [Substrate development quickstart на снимке df78882](https://github.com/agent-substrate/substrate/blob/df788825e6fc13dd7aa5fd283c8f14bd4c818247/README.md)
- [AX quick start на снимке ac233282](https://github.com/google/ax/blob/ac2332829f22360ff97b0ba34d94dd0dd782f17e/README.md)
- [Kind Quick Start](https://kind.sigs.k8s.io/docs/user/quick-start/)
- [Kind configuration: nodes, mounts, ports и networking](https://kind.sigs.k8s.io/docs/user/configuration/)
- [K3s architecture](https://docs.k3s.io/architecture)
- [K3s requirements](https://docs.k3s.io/installation/requirements)
- [Создание cluster с kubeadm](https://kubernetes.io/docs/setup/production-environment/tools/kubeadm/create-cluster-kubeadm/)
- [Установка kubeadm и host prerequisites](https://kubernetes.io/docs/setup/production-environment/tools/kubeadm/install-kubeadm/)
- [Proxmox VE: Nested Virtualization](https://pve.proxmox.com/wiki/Nested_Virtualization)
