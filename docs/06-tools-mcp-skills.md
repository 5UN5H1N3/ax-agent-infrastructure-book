# Tools, MCP и Skills

![MCP trust boundary: протокол не заменяет авторизацию](.gitbook/assets/diagrams/06-07.png)  
*MCP trust boundary: протокол не заменяет авторизацию*

Tool - это формализованная операция, доступная agent loop. Он может быть локальной функцией, HTTP API, shell command или MCP tool. Главное свойство production tool - не «модель умеет его вызвать», а наличие чёткой схемы, проверяемых полномочий, predictable side effects и наблюдаемого результата.

### MCP в версии 2026-07-28

Текущая спецификация MCP существенно отличается от ранних туториалов. Ядро стало stateless: убраны protocol-level sessions и старый `initialize/initialized` handshake. Каждый запрос несёт protocol version и client capabilities в `_meta`. Сервер должен поддерживать `server/discover`, позволяющий узнать версии, capabilities и identity. Для совместимости со старыми STDIO servers discovery может использоваться как probe.

Практически это улучшает работу через gateways, WAF и rate limiters: routing и metering меньше зависят от долгоживущей сессии. Но security всё равно остаётся задачей deployment'а. Self-reported `serverInfo` нельзя использовать как security proof.

В MCP полезно помнить ownership semantics: **Prompts** обычно user-controlled, **Resources** application-controlled, **Tools** model-controlled с точки зрения выбора вызова. «Model-controlled» не означает «безусловно разрешённый»; policy enforcement находится снаружи.

### Skills

Skill - не transport protocol. Это переиспользуемый пакет инструкций, процедур и вспомогательных материалов, который помогает agent harness выполнить класс задач. Tool даёт capability, Skill объясняет способ применения capability. MCP стандартизует взаимодействие между client/server; Skill может ссылаться на MCP tools, но не является их сетевым эквивалентом.

### Что делает AX Workspace

Workspace может описывать Git repositories, files, MCP servers/registries и skill registries/path. Тем самым Workspace становится декларативным описанием рабочей среды, а не просто volume. Но security implication очевиден: MCP server или skill registry - часть supply chain. Подключение неизвестного registry должно проходить не менее строгий review, чем новый package repository.

### Источники и дальнейшее чтение

- [MCP 2026-07-28 release](https://blog.modelcontextprotocol.io/posts/2026-07-28/)
- [MCP discovery](https://modelcontextprotocol.io/specification/2026-07-28/server/discover)
- [AX Workspace concepts](https://github.com/google/ax/blob/main/docs/concepts.md)
