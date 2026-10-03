# Kubernetes: необходимый минимум для понимания AX/Substrate

![Kubernetes reconciliation loop - mental model, который нужен для AX/Substrate](.gitbook/assets/diagrams/09-09.png)  
*Kubernetes reconciliation loop - mental model, который нужен для AX/Substrate*

Kubernetes здесь нужен не как отдельный предмет, а как substrate инфраструктуры. Достаточно уверенно понимать несколько mental models.

**Pod** - минимальная deployable unit Kubernetes, а не VM. **Deployment** поддерживает desired number stateless Pods. **DaemonSet** гарантирует Pod на подходящих nodes и поэтому естественен для node-level supervisor. **Service** даёт стабильную сетевую точку над changing Pods. **Namespace** разделяет имена и policy scope. **Secret/ConfigMap** поставляют configuration, хотя Secret сам по себе не является HSM. **RBAC** определяет права на Kubernetes API. **NetworkPolicy** ограничивает сетевые потоки при поддержке CNI.

### Control plane

`kube-apiserver` принимает API operations. `etcd` хранит cluster state. `kube-scheduler` назначает unscheduled Pods nodes. Controllers постоянно сравнивают desired и actual state. На node `kubelet` обеспечивает запуск Pod через container runtime.

Главный transferable concept - **reconciliation**. Пользователь не просит «создай контейнер прямо сейчас», а объявляет desired state. Controller снова и снова стремится привести actual state к desired. AX внешне использует похожую declarative модель, но сохраняет высокочастотное task state вне Kubernetes API.

### Почему не хранить миллионы Tasks в CRD

Kubernetes официально рекомендует не использовать Custom Resources как обычное application/end-user/monitoring data store и предупреждает, что большое количество CRs нагружает API storage. AX design идёт в ту же сторону: небольшие декларативные control-plane объекты Kubernetes остаются там, а population короткоживущих agent tasks хранится в специализированном store.

### Что изучать отдельно

Если после этой главы непонятны Pod lifecycle, Services/DNS, RBAC или CNI, стоит пройти официальные Kubernetes Concepts и отдельный lab до AX. Для AX не требуется становиться Kubernetes developer, но попытка диагностировать Substrate без понимания `kubectl get/describe/logs`, readiness и scheduling почти гарантированно приводит к путанице.

### Источники и дальнейшее чтение

- [Kubernetes components](https://kubernetes.io/docs/concepts/overview/components/)
- [Kubernetes custom resources](https://kubernetes.io/docs/concepts/extend-kubernetes/api-extension/custom-resources/)
