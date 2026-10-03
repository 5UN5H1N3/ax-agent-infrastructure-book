# Identity, authorization и Human-in-the-loop

![Controlled action pattern: observe → diagnose → propose → approve → execute → verify](.gitbook/assets/diagrams/24-21.svg)  
*Controlled action pattern: observe → diagnose → propose → approve → execute → verify*

Authorization для agents должна отвечать не «кто запустил Pod», а «какой logical Task сейчас просит какое действие над каким объектом». Это сложнее обычного service account, потому что один agent способен породить дерево child tasks.

### Разделяем identities

- Human/operator identity - кто инициировал или approved.
- Platform/workload identity - какой Task/Actor выполняется.
- Tool identity - каким credential агент обращается к внешней системе.
- Resource authority - над каким subset objects допустимо действие.

Хорошая система связывает их в audit event, но не делает один вечный token универсальным ключом ко всему.

### Approval gate

Approval нужен не для каждого tool call, иначе система становится ручным workflow. Его ставят на transition authority: переход из read-only диагностики к write, повышение scope, destructive operation, превышение budget или доступ к sensitive data.

Approval object должен содержать конкретный action plan, targets, expected changes, expiry и hash/ID параметров. Подтверждение «разрешить агенту работать дальше» слишком широкое.

### Read / write / destructive

Практический policy taxonomy:

| Класс | Пример | Default |
| --- | --- | --- |
| Read | show route, logs, metrics | allow при scoped identity |
| Bounded write | создать branch/ticket, изменить один объект | policy + optional approval |
| High-impact | BGP policy, delete VM, firewall rollout | explicit approval + pre/post checks |
| Destructive/irreversible | wipe data, revoke root PKI | отдельная процедура / возможно запрет агенту |

### Verification after action

Успешный tool response не равен успешному outcome. После write всегда отдельный verification step: прочитать новое состояние из authoritative system, проверить invariants и записать result. Желательно, чтобы verifier был логически отделён от executor и не доверял его natural-language summary.
