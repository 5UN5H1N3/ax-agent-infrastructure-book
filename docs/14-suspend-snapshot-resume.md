# Suspend, snapshot, resume и golden snapshot

![Resume on demand: запрос может разбудить suspended actor](.gitbook/assets/diagrams/14-13.png)  
*Resume on demand: запрос может разбудить suspended actor*

Checkpointing - центральный механизм density, но его семантику нужно понимать точнее, чем «поставили VM на паузу».

### Golden snapshot

При создании ActorTemplate временный golden actor проходит boot и initialization. Затем его suspend создаёт baseline state, опубликованный через golden Tag. Новый Actor может стартовать из этого состояния и избегать повторной expensive initialization. Полезный pattern: загрузить runtime, библиотеки и тяжёлые read-only assets **до** golden snapshot; per-actor secrets и identity, наоборот, должны появляться после cloning безопасным способом.

### Actor snapshot

`SuspendActor` фиксирует runtime state и durable data в external snapshot и освобождает Worker. Следующий resume назначает свободный Worker и восстанавливает state. Snapshot связан с ActorTemplate UID: если template изменился, memory image старого sandbox не обязательно совместим. Current API guide описывает fallback, при котором durable data может быть восстановлена, а guest boot выполнен заново.

### Network и external resources

Нельзя считать открытые TCP sessions частью надёжно переносимого business contract. Даже если file descriptor присутствует в memory image, remote endpoint не обязан считать соединение живым. Приложение должно уметь reconnect. Аналогично temporary credentials, DNS answers и leases могут протухнуть.

### Eviction и data-loss window

Current Substrate guide указывает grace path для eviction: Actor получает `SIGTERM` и ограниченное время на suspend; если checkpoint не завершён, Actor может перейти в CRASHED, а изменения после последнего snapshot потеряются. Поэтому long-running active actor должен либо checkpoint'иться периодически, либо хранить critical progress вне snapshot.

### AX nuance

В current AX runner semantics `/workspace` восстанавливается, но container/process tree создаётся заново. Это значит, что agent command должен уметь reconstruct progression. Не проектируйте AX Task так, будто Python heap гарантированно продолжится с той же instruction pointer только потому, что underlying Substrate вообще умеет RAM snapshots.

### Источники и дальнейшее чтение

- [Substrate API guide](https://github.com/agent-substrate/substrate/blob/main/docs/api-guide.md)
- [AX runner](https://github.com/google/ax/blob/main/docs/runner.md)
