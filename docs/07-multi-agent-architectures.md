# Multi-agent архитектуры без лишней магии

![Практика: software-engineering multi-agent workflow](.gitbook/assets/diagrams/07-08.png)  
*Практика: software-engineering multi-agent workflow*

Multi-agent architecture оправдана, когда разделение даёт хотя бы одно из трёх: меньший context, разные полномочия/tools или параллельное выполнение. Если этих преимуществ нет, один хорошо ограниченный agent часто проще и дешевле.

### Основные топологии

**Supervisor/worker.** Supervisor хранит high-level цель и делегирует ограниченные subtasks. Worker не должен получать больше authority, чем нужно его роли. **Hierarchical delegation** продолжает этот pattern на несколько уровней, но требует budget propagation и ограничения глубины. **Fan-out/fan-in** запускает несколько независимых проверок и агрегирует вывод. **Specialist routing** выбирает агента по типу инцидента или ресурса. **Evaluator gate** проверяет результат перед следующей стадией.

### Coordination state

Не стоит передавать всё через естественный язык. Надёжнее иметь structured task state: ID, parent ID, input artifact refs, expected output schema, deadline, allowed tools, budget, status. Текстовая summary может быть частью context, но не должна заменять machine-readable contract.

### Failure propagation

Если child Task упал, parent должен знать: retryable ли ошибка, был ли side effect, можно ли повторить с тем же idempotency key, стоит ли отменять siblings. В infrastructure workflow особенно важно отличать «не смог прочитать telemetry» от «изменил конфигурацию, но потерял ответ».

### AX и дерево задач

AX концептуально допускает, что один Task создаёт другие и таким образом формирует большое дерево. При этом сама платформа не обязана моделировать бизнес-семантику дерева за вас. Это сознательное решение: Task остаётся дешёвой изолированной единицей, а relation/budget/authority должны проектироваться на уровне agentic application до тех пор, пока соответствующие primitives не появятся в стабильной спецификации.
