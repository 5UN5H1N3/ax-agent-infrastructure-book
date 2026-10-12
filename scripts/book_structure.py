"""Shared print/web navigation; chapter titles remain in the canonical HTML."""

SLUGS = [
    "00-reading-guide", "01-agent-stack-map", "02-llm-as-compute",
    "03-agent-loop-and-boundaries", "04-agent-harness",
    "05-context-state-memory-snapshot", "06-tools-mcp-skills",
    "07-multi-agent-architectures", "08-process-container-gvisor-microvm",
    "09-kubernetes-minimum", "10-control-plane-distributed-systems",
    "11-actor-model", "12-agent-substrate-architecture", "13-substrate-resources",
    "14-suspend-snapshot-resume", "15-substrate-networking", "16-ax-control-plane",
    "17-ax-primitives", "18-ax-runner", "19-ax-task-lifecycle", "20-local-inference",
    "21-inference-performance", "22-multi-agent-on-ax", "23-security-architecture",
    "24-identity-authorization-hitl", "25-observability", "26-proxmox-kvm-lab",
    "27-production-engineering", "28-performance-scaling",
    "29-failure-modes-troubleshooting", "30-when-to-use-ax", "31-antipatterns",
    "32-labs", "33-reference-multi-agent-architectures",
    "34-mental-models-glossary-cheatsheets", "35-context-engineering",
    "36-mcp-2026", "37-safe-tool-execution", "38-ax-state-event-flow",
    "39-substrate-snapshot-ownership", "40-gateway-egress",
    "41-local-inference-sizing", "42-production-security-dr", "43-operations-runbook",
    "appendix-a-audit", "appendix-b-production-readiness", "appendix-c-sources-map",
]

GROUPS = [
    ("Начало", [0]),
    ("I. Фундамент агентных систем", list(range(1, 8))),
    ("II. Runtime, isolation и Agent Substrate", list(range(8, 16))),
    ("III. Google AX", list(range(16, 20))),
    ("IV. Local inference и multi-agent execution", list(range(20, 23))),
    ("V. Security, observability и эксплуатация", list(range(23, 32))),
    ("VI. Практика и reference architectures", list(range(32, 35))),
    ("VII. Deep dives", list(range(35, 44))),
    ("Приложения", list(range(44, 47))),
]

ANCHORS = [f"ch-{i}" for i in range(44)] + ["appendix", "appendix-b", "appendix-c"]


def navigation_title(index, title):
    return f"{index}. {title}" if index < 44 else title


def passport_markdown(soup, converter):
    """Reuse the canonical print passport in the web landing page."""
    passport = soup.find(id="edition-passport")
    if passport is None:
        raise ValueError("Missing canonical edition passport")
    return converter(str(passport), heading_style="ATX", bullets="-",
                     strip=["section", "div", "span"]).strip()
