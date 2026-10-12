import json
from dataclasses import dataclass, field

REVISION = "fixture-v1"
FILES = {"app.py": "def total(items):\n    return items[0] + sum(items[1:])\n"}
TOOLS = {"repo.read_file", "tests.run", "finish"}


class Rejected(Exception):
    pass


@dataclass
class State:
    steps: int = 0
    evidence: dict = field(default_factory=dict)
    events: list = field(default_factory=list)

def parse(raw):
    if not isinstance(raw, str) or len(raw) > 4096:
        raise Rejected("Некорректный размер ответа")
    try:
        proposal = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise Rejected("Ожидался JSON") from exc
    if not isinstance(proposal, dict) or set(proposal) != {"tool", "args"}:
        raise Rejected("Ожидались только tool и args")
    tool, args = proposal["tool"], proposal["args"]
    if not isinstance(tool, str) or tool not in TOOLS:
        raise Rejected("Инструмент недоступен")
    if not isinstance(args, dict):
        raise Rejected("args должен быть объектом")
    return tool, args


def context(state):
    return {
        "goal": "Проверить обработку пустого списка; ничего не публиковать",
        "revision": REVISION,
        "tools": sorted(TOOLS),
        "observations": state.evidence,
        "steps_left": 4 - state.steps,
    }

def apply(state, raw):
    if state.steps >= 4:
        raise Rejected("Исчерпан лимит шагов")
    state.steps += 1  # Некорректные ответы тоже расходуют лимит.
    tool, args = parse(raw)
    if tool == "repo.read_file":
        if set(args) != {"path"} or args["path"] != "app.py":
            raise Rejected("Чтение разрешено только для app.py")
        state.evidence["file"] = {
            "revision": REVISION, "path": "app.py", "text": FILES["app.py"]
        }
    elif tool == "tests.run":
        if args != {"suite": "empty-list"}:
            raise Rejected("Этот набор тестов не разрешён")
        if "file" not in state.evidence:
            raise Rejected("Сначала прочитайте проверяемый файл")
        state.evidence["tests"] = {
            "revision": REVISION, "suite": "empty-list", "passed": False,
            "source": "simulated", "case": "total([]) == 0",
            "error": "IndexError"
        }
    else:
        if args:
            raise Rejected("finish не принимает аргументов")
        if not {"file", "tests"} <= state.evidence.keys():
            raise Rejected("Недостаточно оснований для завершения")
        state.events.append("result_built")
        return {
            "status": "completed", "revision": REVISION,
            "evidence": state.evidence,
            "limits": "Тест имитирован; безопасность изменения не доказана"
        }
    state.events.append(tool)
    return None

def run(replies):
    state = State()
    for raw in replies:
        context(state)  # Здесь настоящий adapter вызвал бы модель.
        result = apply(state, raw)
        if result is not None:
            return result, state
    raise Rejected("Ответы закончились до завершения задачи")


if __name__ == "__main__":
    replies = [
        '{"tool":"repo.read_file","args":{"path":"app.py"}}',
        '{"tool":"tests.run","args":{"suite":"empty-list"}}',
        '{"tool":"finish","args":{}}',
    ]
    result, state = run(replies)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    print("События:", state.events)
