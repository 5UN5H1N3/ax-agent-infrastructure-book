# Substrate networking: routing, wake-up и egress

Stateful Actor может менять физический Worker, поэтому адрес Pod/Worker нельзя использовать как долговременную identity. Routing layer должен разрешить logical Actor → current assignment и при необходимости инициировать resume.

### Request parking

High-level flow: запрос приходит в `atenet`, router выясняет состояние Actor, если он suspended - удерживает запрос, control plane назначает Worker, `atelet/ateom` восстанавливают sandbox, после readiness открывается authenticated tunnel и исходный request продолжается. Это скрывает relocation от клиента, но добавляет resume latency в first request.

### Egress

Agent workload имеет необычно высокий риск exfiltration и supply-chain calls. Хорошая default posture - deny egress и разрешать конкретные destinations. В Substrate v0.2.0+ egress policy стала заметной частью security model. AX `Gateway` переводит пользовательскую декларацию в runtime network policy.

### L7 и TLS reality

Hostname allowlist сложнее CIDR: DNS меняется, TLS шифрует HTTP payload, SNI имеет свои ограничения, а transparent interception создаёт PKI risk. В AX v0.3.0 был публично задокументирован bug, где hostname allowlist ломал TLS egress, в то время как CIDR/wildcard работали. Это хороший operational lesson: condition `GatewayReady=True` означает, что policy применена, а не что реальный end-to-end запрос успешен.

### Как тестировать

После policy deployment делайте positive и negative probes **из sandbox**: разрешённый DNS, разрешённый TCP/TLS, запрещённый destination, MCP endpoint и model endpoint. Логи egress proxy должны входить в incident playbook.

### Источники и дальнейшее чтение

- [AX Gateway issue #345](https://github.com/google/ax/issues/345)
- [Substrate release notes](https://github.com/agent-substrate/substrate/releases)
