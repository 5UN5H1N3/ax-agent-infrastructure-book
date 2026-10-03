# Практикум: 14 последовательных лабораторных работ

![Базовый учебный стенд: один VM, воспроизводимый путь до работающего AX](.gitbook/assets/diagrams/32-26.png)  
*Базовый учебный стенд: один VM, воспроизводимый путь до работающего AX*

Ниже - маршрут от чистой VM до двух multi-agent systems. Команды намеренно ориентированы на понимание процесса; exact flags текущего релиза необходимо сверить с pinned README/release notes перед запуском.

### Lab 1. Подготовка VM

Создайте Debian 13 или Ubuntu LTS VM в Proxmox. Включите достаточные CPU/RAM/disk, NTP и nested virtualization, если хотите microVM. Проверьте `virt-host-validate`/`/dev/kvm` по необходимости. Установите Docker, Git, Go версии, требуемой pinned AX/Substrate, `kubectl`, `ko`.

**Результат:** чистый snapshot VM `ax-lab-base`.

### Lab 2. Kind и Kubernetes minimum

Создайте Kind cluster. Отработайте `kubectl get nodes`, `get pods -A`, `describe`, `logs`, `exec`, Service/DNS, Secret, readiness probe. Намеренно сломайте image и readiness, чтобы увидеть events.

### Lab 3. Agent Substrate quickstart

Используйте pinned release scripts `hack/create-kind-cluster.sh` и `hack/install-ate-kind.sh --deploy-ate-system ...`. Убедитесь, что поднялись control components, PostgreSQL и RustFS. Установите `kubectl-ate` подходящей версии.

### Lab 4. Actor lifecycle

Создайте WorkerPool, ActorTemplate, Actor. Наблюдайте transition suspended/running, assignment и component logs. Цель - руками увидеть, что Actor identity не равна Pod identity.

### Lab 5. Snapshot/resume

Запишите state в durable directory и RAM/app state согласно demo; suspend; убедитесь, что Worker освобождён; resume на возможном другом Worker. Измерьте latency и размер snapshot. Повторите 10 раз.

### Lab 6. gVisor и microVM

Сравните RuntimeClass/SandboxConfig path. Для microVM проверьте KVM/nested virtualization. Выполните одинаковую filesystem/network/process workload и зафиксируйте compatibility/performance differences. Если microVM невозможно поднять в nested lab, архитектурный эксперимент считается завершённым после проверки constraints - не маскируйте отсутствие KVM обходом security.

### Lab 7. Установка AX

Checkout tag `v0.3.1`, настройте image registry, выполните deployment в `ax-system`. Проверьте Redis, server/controller components. Установите CLI той же версии, а не `@latest`.

### Lab 8. Первый Task + Workspace

Создайте Workspace с небольшим Git repo и file; Task с `sleep`/простым agent command. Наблюдайте `ax watch`, `WorkspaceReady`, filesystem. Suspend/resume и убедитесь, что workspace changes сохранены, а process PID изменился.

### Lab 9. Gateway

Начните с deny/явного allow. Проверьте DNS, HTTP, TLS для разрешённого и запрещённого destination из `ax ssh`. Сохраните egress proxy logs. Не используйте успешный status как единственную проверку.

### Lab 10. Cloud LLM

Подключите provider через безопасный secret path, доступный вашей pinned версии. Сделайте минимальный model adapter с timeout/retry и метриками tokens/TTFT. Не кладите API key literal в Task manifest.

### Lab 11. CPU local inference

На отдельной VM или host поднимите pinned llama.cpp server с небольшой GGUF model. Проверьте OpenAI-compatible endpoint, concurrency 1/2/4, context 1k/4k/8k. Снимите CPU utilization, RAM и latency. Это fallback lab, который воспроизводится без GPU.

### Lab 12. NVIDIA + vLLM + Qwen

На GPU host установите совместимые NVIDIA driver/CUDA/container toolkit. Запустите pinned `vLLM v0.30.0` image/server с выбранной Qwen model, начав с conservative context/concurrency. Измерьте TTFT/ITL/aggregate throughput и KV pressure. Затем увеличивайте concurrency до нарушения целевого SLO. Зафиксируйте точку saturation.

Примерный шаблон, не copy-paste для любой модели:

```
vllm serve <model-id> \
  --tensor-parallel-size 1 \
  --max-model-len <tested-context> \
  --gpu-memory-utilization <tested-value> \
  --api-key <service-key>
```

### Lab 13. Software-engineering multi-agent

Coordinator создаёт Researcher/Implementer/Tester/Reviewer Tasks. Каждая роль получает отдельный policy/Workspace view. Implementer пишет только branch, Reviewer не имеет write, merge остаётся human-approved. Измерьте число Tasks, tokens и wall time против single-agent baseline.

### Lab 14. Infrastructure multi-agent

Постройте pipeline `Coordinator → Telemetry → Network Diagnostics → Logs/Config → Validator → Action Planner → Approval → Restricted MCP → Verification`. Read-only agents работают без approval. Action tool выдаёт краткоживущую capability только на заранее согласованный объект/операцию. Verification повторно читает устройство/систему и подтверждает intended outcome.

**Критерий успеха:** agent не может расширить scope собственным prompt'ом и не может выполнить write до approval.

![Практика: software-engineering multi-agent workflow](.gitbook/assets/diagrams/32-27.png)  
*Практика: software-engineering multi-agent workflow*

![Практика: инфраструктурная диагностика с контролируемым изменением](.gitbook/assets/diagrams/32-28.png)  
*Практика: инфраструктурная диагностика с контролируемым изменением*
