import json


class FakeLLM:
    """Returns canned responses in order and records the conversations it receives."""

    def __init__(self, *responses: str | None):
        self.responses = list(responses)
        self.calls: list[list[dict[str, str]]] = []
        self.temperatures: list[float | None] = []

    def __call__(self, **kwargs) -> str | None:
        self.calls.append([dict(m) for m in kwargs["messages"]])
        self.temperatures.append(kwargs.get("temperature"))
        return self.responses.pop(0)


def repair_answer(*edits: tuple[int, int, str], diagnosis: str = "the cause") -> str:
    """A repair answer as the LLM gives it: JSON with a diagnosis and (first_line, last_line, replacement) edits."""
    return json.dumps({"diagnosis": diagnosis, "edits": [{"first_line": a, "last_line": b, "replacement": r} for a, b, r in edits]})
