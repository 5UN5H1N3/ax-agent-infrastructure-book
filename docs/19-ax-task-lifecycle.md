# Полный lifecycle Task: от YAML до suspend/resume

![AX Task: путь от декларации до выполняемого агента](.gitbook/assets/diagrams/19-16.png)  
*AX Task: путь от декларации до выполняемого агента*

Разберём Task как распределённую state machine, а не как «контейнер, который стартовал».

### Create path

1. `ax apply` разбирает multi-document manifest.
2. `ax-server` валидирует resource schemas/refs и сохраняет desired state.
3. Controller получает event из store/stream.
4. Разрешаются Workspace/Gateway/Model и runtime requirements.
5. Формируется/выбирается Substrate ActorTemplate и Actor.
6. Runtime назначает Worker и поднимает sandbox.
7. Runner стартует PID 1.
8. Workspace bootstrap выполняется один раз.
9. `/readyz` становится 200.
10. Controller выставляет `WorkspaceReady`/`Ready`.
11. Agent command выполняет loop.

Каждый пункт - отдельный failure domain. Поэтому «Task Pending» и «Task Running but useless» требуют разных playbooks.

### Suspend path

Пользователь или policy инициирует suspend. AX переводит lifecycle вниз в Substrate, runner получает termination signal и обязан flush state. Substrate/AX сохраняют durable workspace/runtime snapshot согласно текущей integration semantics, освобождают physical Worker и обновляют status. Suspended Task остаётся logical resource.

### Resume path

Task снова получает Worker. Durable `/workspace` возвращается, runner запускается заново, видит markers и пропускает destructive re-bootstrap, затем запускает agent command. Harness должен определить, где остановилась работа, и продолжить по durable journal/state. Именно здесь хорошо спроектированный state machine превосходит prompt-only memory.

### Delete

Delete должен освобождать AX resource, Substrate Actor/snapshot ownership и связанные runtime artifacts согласно lifecycle. External side effects - Git commits, tickets, конфигурации устройств - не исчезают. Поэтому delete Task никогда не является бизнес rollback.

### Operational invariant

На каждом этапе задавайте три вопроса: **какой слой владеет desired state? какой слой владеет runtime state? какой сигнал доказывает readiness?** Если ответ нельзя сформулировать, система будет плохо диагностироваться.
