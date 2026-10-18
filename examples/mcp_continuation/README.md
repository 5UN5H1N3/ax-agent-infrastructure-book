# Продолжение MCP вызова и наблюдение долгой работы

Это проверяемый разбор главы 36: подготовленные JSON-RPC messages и ledger host.
Он показывает, как запросить interface через MRTR, продолжить исходный вызов,
а затем отдельно наблюдать task по handle. Программа читает только соседний
`traces.json`. MCP server, устройство, модель и сеть не запускаются.

Файлы опубликованы отдельным закреплённым срезом. Ссылки на него и команды
получения полной копии находятся во входе «Продолжение вызова MCP» главы 0.
Старые закреплённые срезы каталога 51572b91 и дополнений L01–L04 не содержат
этого разбора; новые файлы не относятся к прежним commit.
Старый `examples/ch36_mcp_stdio.py` и его tests не изменены.

Его discovery — сокращённый учебный ответ. Новые transcripts содержат полный
DiscoverResult, включая обязательные cacheScope и ttlMs, и отдельно сверяются
с официальной schema. Успех старых tests не объявляется доказательством полного
schema conformance старого server.

## Запуск

Проверено на Linux x86_64 и Python 3.13.5. Нужна только стандартная библиотека.
Из корня репозитория книги:

```sh
python3 -m examples.mcp_continuation.trace
python3 -m examples.mcp_continuation.trace --case mrtr --messages
python3 -m examples.mcp_continuation.trace --case tasks --messages
python3 -m examples.mcp_continuation.trace --case task_input --messages
python3 -m examples.mcp_continuation.trace --case cancel_race --messages
python3 -m unittest discover -s examples -p 'test_ch36_mcp_continuation.py' -v
```

Без `--case` проверяются все восемь сценариев. `--messages` добавляет полные
подготовленные request/response, включая metadata каждого request. Это вывод
fixture, не запись сеанса с настоящим peer. После каждого exchange ledger
показывает RPC ID, method, виртуальное время, operation ID, наличие opaque state,
неотвеченные ключи, taskId и состояние host. Успешный replay даёт exit 0, в том
числе для ожидаемого отказа. Неожиданный переход даёт `fixture_check: FAILED`
и exit 2. Исход `fixture_summary` обозначает принятую подготовленную сводку.

| Сценарий | Разобранный путь | Конечный исход checker |
| --- | --- | --- |
| mrtr | 41 → вопрос → 42 с ответом → сводка | fixture_summary |
| decline | Явный отказ от формы | decline, захват не начат |
| cancel | Форма закрыта без подтверждения | cancel, захват не начат |
| unsupported_form | Пустые client capabilities | unsupported, -32021 |
| unsupported_tasks | Клиент не объявил Tasks | unsupported, -32021 |
| tasks | 51 → handle; 52 → working; 53 → completed | fixture_summary |
| task_input | Вопрос по task; tasks/update; затем tasks/get | fixture_summary |
| cancel_race | Ack отмены; следующее наблюдение completed | fixture_summary, cancel_requested=true |

## Что хранится между шагами

В MRTR host удерживает исходные name/arguments, вопрос, ключ `pick_interface`
и точную opaque строку. Request 42 — новый RPC с прежним `capture-op-01`.
Ответ находится в `params.inputResponses`, state — в `params.requestState`,
а не внутри arguments. Состояние принадлежит этому вызову и server.
`fixture-state-01` — метка; в ней нет подписанного или зашифрованного payload.
Сравнение строк не проверяет server-side integrity, principal binding или TTL.

В Tasks host сохраняет уже полученный handle. JSON-RPC ID меняется между polls,
taskId остаётся. Он принадлежит MCP server; operation ID harness и identity
AX Task — отдельные имена, соответствие которых нужно хранить явно.
`CreateTaskResult` имеет поля task прямо в result; schema этого
профиля не требует обёртки `task`. У `tasks/get` внешний resultType=complete
может сопровождаться status=working. Конечное содержимое tool появляется во
вложенном result у completed; вложенный isError всё равно надо проверить.

В `task_input` ключ `choose_format` принадлежит task. Ответ отправляется методом
tasks/update, без переноса MRTR requestState. Ack подтверждает приём ответа,
не завершение работы. Повторно видимый вопрос с уже отвеченным ключом не требует
ещё одной формы: observable status может обновиться позже ack. Дополнительные
tests проверяют повторные polls до ответа и временно прежнее состояние после ack.

У настоящего host нужны также server/owner binding, защищённое хранение handles
и сверка состояния после reconnect. Здесь всё хранится в памяти checker; этот
replay не демонстрирует durable creation, restart или фактический reconnect.

## Границы выбранного contract

`Policy` — доверенная конфигурация упражнения, не данные от server и не результат
аутентификации. Она разрешает только `lab-edge-1/eth1`, до 12 packets, 4096 bytes
и 120000 ms. Форме разрешено предлагать eth0 и eth1, но выбор eth0 нарушает policy.
Смена clientInfo.name на admin не выдаёт прав. Ответ action=accept заполняет поле,
не утверждает config diff и не разрешает произвольный захват.

Checker принимает максимум восемь exchanges и два раунда ввода. Общий deadline
130000 ms отсчитывается от начала trace. JSON message ограничен 16384 bytes,
opaque state — 256 bytes; загрузка fixture ограничена 128 KiB до JSON parsing.
Схемы двух форм фиксированы в коде. Времена, summaries и task statuses подготовлены;
таймер и scheduler не работают. Для Tasks выбран конечный TTL до 300000 ms.
Проверка этих пределов служит упражнением, не оценкой production defaults.

Это узкий checker finite traces, а не общий MCP validator. Например, он заканчивает
учебный путь на первом конечном состоянии; это не запрет повторно читать конечную
task в настоящем протоколе. Пример не реализует transport, HTTP/SSE, OAuth,
sampling, SDK, OS isolation, пакетный захват, HMAC/AEAD и backend storage.
Он не запускает AX/Substrate и не меняет исход `lab33-net-001`.

## Упражнение и ключ

Сначала прочитайте messages, не запуская checker. Для каждого шага укажите,
что уже известно host и что ещё нельзя объявить завершённым. Затем в отдельной
копии fixture изменяйте по одному факту; повторный запуск проверит расхождение.

1. Замените id 42 на 41 в request и response. Новый MRTR retry обязан иметь другой
   RPC ID. Operation ID при этом должен остаться прежним. Checker сообщает
   `reused_request_id`.
2. Измените `requestState` только у retry. Host должен вернуть точную строку:
   `opaque_state_not_echoed_exactly`. Эта проверка не доказывает защиту token
   на server; её надо испытывать отдельно.
3. Отправьте interface=eth0. Form schema его допускает, host policy — нет:
   `interface_outside_host_scope`. Расширить permission можно только отдельно.
4. Передвиньте poll 53 с 6000 на 5999 ms. После ответа на 52 интервал равен
   5000 ms; ближайший poll — в 1000+5000. Получится `poll_too_early`.
5. Прочитайте ack tasks/update в `task_input`. Он не содержит task status или
   результата capture; следующий tasks/get ещё нужен. Повторный вопрос с тем же
   смыслом не предъявляют заново. Повторное использование ключа для другого
   вопроса checker отклоняет.
6. Прочитайте `cancel_race`. После ack отмены host знает только о запросе отмены.
   Последний completed означает, что подготовленный захват успел завершиться;
   отмена не является rollback. Закрытие формы в сценарии cancel — другая операция.
7. Представьте потерю ответа на start до получения handle. Новое RPC имя ничего
   не говорит о созданной работе. Нужен lookup/dedup contract по application ID;
   делать новый start вслепую нельзя. Такой сетевой отказ здесь не исполняется.

## Закреплённые источники

Профиль — MCP 2026-07-28 и отдельное Tasks extension того же revision.
URL, commit и SHA256 прочитанных snapshots перечислены в `SOURCES.json`.

- [MRTR на 1155ca46](https://github.com/modelcontextprotocol/modelcontextprotocol/blob/1155ca460e5097019f32769b38dcba194432e2fb/docs/specification/2026-07-28/basic/patterns/mrtr.mdx).
- [Form elicitation на том же commit](https://github.com/modelcontextprotocol/modelcontextprotocol/blob/1155ca460e5097019f32769b38dcba194432e2fb/docs/specification/2026-07-28/client/elicitation.mdx).
- [Core JSON Schema](https://github.com/modelcontextprotocol/modelcontextprotocol/blob/1155ca460e5097019f32769b38dcba194432e2fb/schema/2026-07-28/schema.json).
- [Tasks specification на 899c1bbc](https://github.com/modelcontextprotocol/ext-tasks/blob/899c1bbc869567670093face3591b39c28ce3219/specification/2026-07-28/tasks.md).
- [Tasks JSON Schema](https://github.com/modelcontextprotocol/ext-tasks/blob/899c1bbc869567670093face3591b39c28ce3219/schema/2026-07-28/schema.json).

При редакционной проверке packets отдельно сверяются с официальными schemas.
Shape validation не заменяет нормативные правила, policy и проверку переходов;
runtime checker не скачивает schemas и не требует jsonschema.
