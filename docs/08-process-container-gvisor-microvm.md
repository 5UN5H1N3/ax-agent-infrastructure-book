# От Linux process к gVisor и microVM

![Лестница изоляции: усиление boundary обычно увеличивает стоимость и сложность](.gitbook/assets/diagrams/08-09.png)  
*Лестница изоляции: усиление boundary обычно увеличивает стоимость и сложность*

Изоляция agent workload важнее, чем у обычного внутреннего микросервиса: агент способен генерировать и запускать код, клонировать внешние repository, подключать инструменты и реагировать на потенциально hostile content. Поэтому «он же в контейнере» - недостаточный threat model.

### Linux process и container

Namespaces разделяют представления PID, mount, network, user и других ресурсов. cgroups ограничивают/учитывают CPU, memory, I/O и число процессов. Linux capabilities дробят привилегии root на отдельные права. seccomp фильтрует системные вызовы. `no_new_privileges` запрещает процессу получать новые privileges через exec. Вместе эти механизмы значительно уменьшают blast radius, но обычный container всё ещё разделяет kernel host'а.

### gVisor

gVisor вставляет userspace application kernel (`Sentry`) между приложением и host kernel. OCI runtime `runsc` позволяет использовать эту модель через Docker/Kubernetes. Большая часть Linux interface переимплементируется в userspace, поэтому hostile process имеет существенно меньшую прямую поверхность host syscalls. Цена - compatibility/performance overhead, который зависит от workload.

### microVM

microVM использует аппаратную виртуализацию и отдельный guest kernel. Boundary обычно сильнее, но появляются VMM, guest image, virtual devices, boot/snapshot complexity и требования к KVM. На Proxmox/KVM lab nested virtualization должна быть доступна VM, иначе microVM backend не будет рабочим практическим вариантом.

### Выбор

Для первого стенда разумнее gVisor: меньше движущихся частей. microVM стоит изучать как второй backend для более сильной изоляции. Выбор должен опираться на threat model и benchmark именно вашего workload, а не на абстрактную таблицу «безопаснее/быстрее».

### Источники и дальнейшее чтение

- [gVisor architecture](https://gvisor.dev/docs/architecture_guide/)
- [gVisor runsc](https://gvisor.dev/docs/user_guide/quick_start/docker/)
