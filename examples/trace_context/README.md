# Реальный trace context между mini-harness и локальным tool

Этот опыт продолжает главу 25. Mini-harness делает один известный read-only
HTTP-вызов, а tool использует неизменённый `read_file` из L01 и читает собственный
временный `batching.py`. OpenTelemetry SDK создаёт и экспортирует настоящие spans.
Так можно проверить перенос context независимо от модели, AX и collector.

Дополнения нет в старых срезах 51572b91, 7d927c1a и 9072815a. Отдельный
закреплённый commit и ссылки на код, зависимости и тест указаны во входе
«Живой trace context» главы 0. Используйте полную копию книги на этом commit.
Прежние 39 программ и L01–L03 не изменены. Новый SDK нужен только этому маршруту;
тесты лежат в отдельном каталоге и запускаются явно после установки зависимостей.

## Окружение и запуск

Проверено: Linux x86_64, Python 3.13.5, API/SDK OpenTelemetry 1.45.1.
Требуются POSIX и разрешение слушать 127.0.0.1 на свободном порту.
`requirements.txt` закрепляет четыре pure-Python wheels с SHA256. Среди них
semantic-conventions 0.66b1 — зависимость этого SDK; наши `learning.*` attributes
являются учебной schema, не объявлены стандартными или GenAI conventions.

Из корня полной копии книги, в новом каталоге venv:

```sh
set -eu
test ! -e .venv-trace-l04
python3 -m venv .venv-trace-l04
.venv-trace-l04/bin/python -m pip install \
  --only-binary=:all: --require-hashes \
  -r examples/trace_context/requirements.txt
.venv-trace-l04/bin/python -m unittest discover \
  -s examples/trace_context -p 'test_*.py' -v
.venv-trace-l04/bin/python -m examples.trace_context.demo
```

Только установка пакетов обращается к внешней сети. Пример сам не устанавливает
зависимости и не принимает внешний endpoint или пользовательский root.
Он создаёт собственный временный fixture и удаляет его после выполнения.
Повторный запуск примера не требует повторного создания venv.
Путь `.venv-trace-l04/` исключён из Git только для этого учебного окружения.

По умолчанию выполняются четыре режима. Для одного используйте
`--scenario propagated`, `dropped`, `invalid` или `resumed`.
CLI выводит массив JSON-пакетов: `summary`, реально полученный `wire`, `spans` и
границу опыта. `OTEL_SDK_DISABLED=true` останавливает запуск до создания сервера.
Профиль требует указанные API/SDK версии; другая версия — новый проверяемый опыт.

## Что делает instrumentation

Два компонента работают в одном процессе, но в разных потоках с отдельными
`TracerProvider`: resources называются `learning-harness` и `learning-tool`.
Между ними настоящий loopback HTTP, не прямой вызов функции. На входе сервера
`extract(..., context=Context())` начинает с пустого context, поэтому общий
процесс не может случайно скрыть потерянный заголовок ambient span.

Providers явно используют `ALWAYS_ON`, конечные limits и `Resource` без
автоматического переноса `OTEL_RESOURCE_ATTRIBUTES`. Глобальный provider не
заменяется. `SimpleSpanProcessor` синхронно передаёт законченные `ReadableSpan`
в `ConsoleSpanExporter`. Exporter пишет в отдельный StringIO компонента, затем
программа объединяет записи и выводит JSON. Formatter сохраняет настоящие IDs,
parent, links, kinds, статус, время и выбранные attributes. Это не подготовленные
span fixtures. Порядок строк экспорта не используется для вывода о parent.

В production синхронный export способен задерживать приложение; batching,
очередь, shutdown и безопасность транспорта требуют отдельного выбора.
Наличие локального вывода не доказывает OTLP-доставку или полноту backend.

## Прочитать четыре результата

### Propagated

Один trace и четыре spans:

```text
harness.attempt A
  tool.read.client C, parent A
    tool.read.server S, parent C (remote)
      file.read F, parent S
```

В первом элементе `wire` найдите `received_traceparent`. Его trace ID совпадает
с `tool.read.client.trace_id`, parent-id — с `tool.read.client.span_id`.
У server тот же trace ID, `parent_span_id` равен ID client и `remote_parent=true`.
Следовательно, заголовок был внедрён внутри client span и извлечён сервером.

`trace-flags` — маска. Выбранный SDK использует `03`: sampled 0x01 и random ID
0x02. Sampled проверяют через `flags & 0x01`, а не равенством всего байта `01`.
Эта поддержка зафиксирована в SDK; Level 2 документ W3C ниже имеет статус CR Draft.

### Dropped

После `inject` tracing carrier намеренно очищается. Operation/attempt headers
сохраняются. Файл читается правильно, но у server нет parent и появляется второй
trace. Client остаётся ребёнком harness, file.read — ребёнком server.
`same_client_server_trace=false`, `server_parent_is_client=false`.

По общему `learning.operation.id` можно найти две части работы. Получить из него
точную утраченную parent-связь нельзя: внутри операции могли быть повторы или
параллельные вызовы. Не назначайте parent по ближайшему timestamp.
Код выхода 0 здесь означает успешное воспроизведение намеренного разрыва;
`summary.meaning` называет это прямо. Неожиданный граф завершит запуск кодом 2.
При ошибке проверки графа реальные wire/spans остаются в диагностическом JSON:
их можно разобрать, даже когда ожидаемое дерево не получилось.

### Invalid

Вместо удаления отправляется `traceparent` с нулевыми trace/span IDs.
Настоящий propagator отвергает его. Наблюдается тот же разрыв, но wire отличает
отсутствующий header от полученного непригодного header.

### Resumed

Первая попытка получает состояние waiting и завершается. Её context сохраняется
в словарь. Следующий attempt начинает новый root trace с `Link` на предыдущий
span; один обычный HTTP tool call выполняется уже во втором attempt.
Итого пять spans и два traces. У нового root parent пустой, в links — точные
trace/span IDs первого attempt. Operation ID общий, attempt IDs различаются.

Словарь checkpoint — только вариация связывания. Длительного ожидания, записи
на диск, перезапуска процесса и AX restore нет. Для настоящего recovery нужны
устойчивый checkpoint и отдельная проверка его чтения после отказа.

## Своё упражнение

Перенесите `inject` за пределы client span. Предскажите graph до запуска, затем
сравните header с IDs attempt и client. Почему server теперь связан с другим
уровнем? Далее удалите operation header и объясните, почему текущая schema
отклоняет запрос, хотя tracing-header может оставаться корректным.

Ключ самопроверки: если `inject` выполняется перед созданием client span, но
внутри attempt, server получит parent=attempt. Trace ID останется общим, а
client и server станут соседними детьми attempt. Это другой граф, хотя поиск
по trace ID всё ещё находит все четыре spans. Проверка parent должна отвергнуть
такую instrumentation и сохранить wire/spans для разбора.

Для переноса в свой harness инструментируйте принадлежащие вам model/tool
adapters и controller attempts. В этом примере нет model loop и не выполнена
автоматическая instrumentation существующего L01: используется его read_file,
а остальной код специально сведён к одной транспортной границе.

## Границы

HTTP-tool — локальный учебный listener без authentication. Не открывайте его
на внешнем интерфейсе. Body ограничен 512 bytes, ответы — 16384, ожидания sockets
— 2 s, нет retries, proxy и redirects. Это не полноценный общий deadline и не
защита от недоверенного клиента или hostile OS user. Каталог fixture доверенный.
Сам `read_file` сохраняет лимиты и файловые проверки L01.

В telemetry выбраны только имена, synthetic operation/attempt IDs, hash и размер
fixture и состояние waiting. Текст кода, credentials, arguments и произвольный
baggage не экспортируются. Это ограниченная schema, не универсальный DLP.
`service.name`, trace context и application IDs обеспечивают correlation,
а доверие к отправителю и полномочия требуют своих механизмов.

Не запускаются модель, application tests, shell tools, OTLP, collector/backend,
дисковые очереди и AX/Substrate. Новые spans не меняют ledger или знаменатель
старого `ch25_observability_contract.py`.

## Первичные источники профиля

- [SDK/API 1.45.1, source commit 14971cef](https://github.com/open-telemetry/opentelemetry-python/tree/14971cef9c84c2354cbaf6871090896fad0f9a1a).
- [PyPI SDK 1.45.1](https://pypi.org/project/opentelemetry-sdk/1.45.1/).
- [TraceFlags выбранного API](https://github.com/open-telemetry/opentelemetry-python/blob/14971cef9c84c2354cbaf6871090896fad0f9a1a/opentelemetry-api/src/opentelemetry/trace/span.py).
- [Python propagation](https://opentelemetry.io/docs/languages/python/propagation/), [exporters](https://opentelemetry.io/docs/languages/python/exporters/), [links](https://opentelemetry.io/docs/languages/python/instrumentation/#adding-links).
- [W3C Trace Context](https://www.w3.org/TR/trace-context/), [Level 2 CR Draft, snapshot 28.03.2024](https://www.w3.org/TR/2024/CRD-trace-context-2-20240328/).
