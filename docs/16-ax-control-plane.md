# AX: архитектура control plane и реальная граница с Substrate

**Статус:** `IMPLEMENTED / VERIFIED` · `MAIN DELTA where noted` · `ROADMAP kept separate`

![AX Task: путь от декларации до выполняемого агента](.gitbook/assets/diagrams/16-15.png)  
*AX Task: путь от декларации до выполняемого агента*

AX даёт Kubernetes-подобный declarative UX для agent workloads, но не является Kubernetes API extension. Это принципиальная деталь архитектуры.

### Release baseline и current main

Design document описывает `ax` CLI, `ax-server` и `ax-task-runner`, причём `ax-server` валидирует manifests, сохраняет state в Redis и сам reconciles с Agent Substrate. Current source tree уже содержит отдельный `ax-controller`: он читает Redis stream consumer group, поднимает `TaskReconciler` и вызывает Substrate client. CONTRIBUTING также говорит о deployment `Redis + controller + server`. Следовательно, актуальная operational модель - разделённые API и reconciliation paths, а `DESIGN.md` частично отстаёт.

Это не мелочь для эксплуатации. API replicas и controller replicas имеют разные bottlenecks. Публичный issue current main указывает, что один controller worker способен reconciliate tasks последовательно, то есть throughput конкретного pod ограничен одной цепочкой reconcile. Масштабировать API без controller capacity бессмысленно.

### State path

Пользователь применяет `ax.io/v1alpha1` manifest через CLI. API валидирует имя/spec и сохраняет resource state в Redis. Событие попадает в controller processing. Controller разрешает ссылки на Workspace/Gateway/Model, формирует необходимые Substrate resources/policies, создаёт или восстанавливает Actor, следит за readiness и записывает Task status/conditions. Клиент получает eventual status через get/watch.

### Почему Redis, а не CRD per Task

AX design аргументирует это ожидаемой population миллионов короткоживущих tasks и ограничениями etcd/API server. Даже без веры в headline «billions of tasks» rationale разумен: Kubernetes declarative API хорошо подходит для относительно небольших, человекочитаемых configuration objects, а не для высокочастотного application state. Redis позволяет AX построить собственную более узкую consistency/reconciliation model.

### Failure boundary

Redis потерян - AX может потерять desired/status layer даже если Substrate actors продолжают существовать. Substrate control plane/DB потерян - AX specs остаются, но runtime placement/snapshot state недоступен. Поэтому backup и DR этих слоёв рассматриваются раздельно.

### Источники и дальнейшее чтение

- [AX DESIGN](https://github.com/google/ax/blob/main/DESIGN.md)
- [Current ax-controller](https://github.com/google/ax/blob/main/cmd/ax-controller/main.go)
- [AX CONTRIBUTING](https://github.com/google/ax/blob/main/CONTRIBUTING.md)
