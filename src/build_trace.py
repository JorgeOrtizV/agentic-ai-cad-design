"""
Trace how a generated script builds its part, so the feedback can point at the lines that cause a problem.

1. instrument: the runner executes a rewritten copy of the script in which every method call `x.m(a)` becomes
   `__trace_after__(__trace_before__(x, i).m(__trace_arg__(a, i)), i)`. The script behaves exactly the same, but the
   Tracer measures the solid going into each call and coming out of it: size, volume, number of solids and holes.
   Calls that change the geometry are saved to trace.json next to the part.
2. locate_problems: after validation, each failed check is mapped to the steps that explain it, e.g.
   "line 33 `result = base_plate.union(leg_holes)`: 2 complete hole(s) of diameter 6 disappeared here".
3. format_trace: a short list of what each line did to the part, shown to the LLM in the repair prompt.

Small models cannot go from "found 0 holes" to the line that removed them; given the line they can fix it.
"""

import ast
import json
from collections import Counter
from pathlib import Path
from typing import Any

import cadquery as cq

from schemas import Check, ValidationReport
from validator import find_concave_cylinders, is_close

TRACE_FILE = "trace.json"
MAX_RECORDS = 300  # a loop in the script could otherwise produce a huge trace
MAX_TRACE_LINES = 20
MAX_SOURCE_CHARS = 80
# Names whose method calls never involve geometry: not worth wrapping
UNTRACED_NAMES = {"cq", "math"}
# Operations that should always change the solid: when they do not, that is reported too
CUTTING_METHODS = {"hole", "cboreHole", "cskHole", "cutBlind", "cutThruAll", "cut"}
JOINING_METHODS = {"union"}

State = dict[str, Any]
Record = dict[str, Any]


# --- Instrumentation (runs inside the runner subprocess) ---


class _Instrumenter(ast.NodeTransformer):
    def __init__(self) -> None:
        self.steps: list[tuple[int, str]] = []  # step id -> (line of the method name, method name)

    def visit_Call(self, node: ast.Call) -> ast.AST:
        self.generic_visit(node)
        func = node.func
        if not isinstance(func, ast.Attribute) or (isinstance(func.value, ast.Name) and func.value.id in UNTRACED_NAMES):
            return node

        step = len(self.steps)
        # In a multi-line chain the call starts on the first line of the chain; the method name is on its own line
        self.steps.append((func.end_lineno or node.lineno, func.attr))
        func.value = self._wrap("__trace_before__", func.value, step)
        node.args = [arg if isinstance(arg, ast.Starred) else self._wrap("__trace_arg__", arg, step) for arg in node.args]
        for keyword in node.keywords:
            keyword.value = self._wrap("__trace_arg__", keyword.value, step)
        return ast.copy_location(self._wrap("__trace_after__", node, step), node)

    @staticmethod
    def _wrap(function: str, value: ast.expr, step: int) -> ast.Call:
        call = ast.Call(func=ast.Name(id=function, ctx=ast.Load()), args=[value, ast.Constant(step)], keywords=[])
        return ast.copy_location(call, value)


def instrument(source: str) -> tuple[ast.Module, list[tuple[int, str]]]:
    """The script with every method call wrapped by the tracer, and the (line, method) of each wrapped call."""
    instrumenter = _Instrumenter()
    tree = ast.fix_missing_locations(instrumenter.visit(ast.parse(source)))
    return tree, instrumenter.steps


def _solid_of(obj: object) -> cq.Shape | None:
    if isinstance(obj, cq.Workplane):
        try:
            return obj.findSolid()  # searches the stack and then the parents, so it also works after .faces(...)
        except Exception:  # no solid yet
            return None
    if isinstance(obj, cq.Shape) and obj.Solids():
        return obj
    return None


def _count(diameters: list[float]) -> dict[str, int]:
    return {f"{diameter:g}": count for diameter, count in sorted(Counter(round(d, 3) for d in diameters).items())}


def measure(obj: object) -> State | None:
    """Size, volume, solids, complete holes and broken-out cuts of the solid an object holds, or None."""
    shape = _solid_of(obj)
    if shape is None:
        return None
    bbox = shape.BoundingBox()
    cylinders = find_concave_cylinders(shape)
    return {
        "size": [round(bbox.xlen, 3), round(bbox.ylen, 3), round(bbox.zlen, 3)],
        "volume": round(shape.Volume(), 1),
        "solids": len(shape.Solids()),
        "holes": _count([diameter for diameter, full in cylinders if full]),
        "cuts": _count([diameter for diameter, full in cylinders if not full]),
    }


class Tracer:
    """Receives the calls of the instrumented script and records the steps that change the geometry."""

    def __init__(self, steps: list[tuple[int, str]]):
        self.steps = steps
        self.records: list[Record] = []
        self._pending: dict[int, list[tuple[State | None, list[State]]]] = {}  # step -> stack of (receiver, arguments)

    def _measure(self, obj: object) -> State | None:
        if not isinstance(obj, cq.Workplane | cq.Shape) or len(self.records) >= MAX_RECORDS:
            return None
        try:
            return measure(obj)
        except Exception:  # tracing must never break the script it observes
            return None

    def before(self, obj: object, step: int) -> object:
        self._pending.setdefault(step, []).append((self._measure(obj), []))
        return obj

    def argument(self, obj: object, step: int) -> object:
        stack = self._pending.get(step)
        state = self._measure(obj) if stack else None
        if stack and state is not None:
            stack[-1][1].append(state)
        return obj

    def after(self, obj: object, step: int) -> object:
        stack = self._pending.get(step)
        before, arguments = stack.pop() if stack else (None, [])
        after = self._measure(obj)
        line, method = self.steps[step]
        if after is not None and (after != before or arguments or (before is not None and method in CUTTING_METHODS)):
            self.records.append({"line": line, "method": method, "before": before, "arguments": arguments, "after": after})
        return obj

    def namespace(self) -> dict[str, object]:
        return {"__trace_before__": self.before, "__trace_arg__": self.argument, "__trace_after__": self.after}


def run_traced(source: str, filename: str, namespace: dict) -> list[Record]:
    """Execute the script in namespace with tracing; the records are also attached to exceptions as `trace_records`."""
    tree, steps = instrument(source)
    tracer = Tracer(steps)
    namespace.update(tracer.namespace())
    try:
        exec(compile(tree, filename, "exec"), namespace)
    except Exception as exc:
        exc.trace_records = tracer.records  # type: ignore[attr-defined]
        raise
    return tracer.records


def save_trace(records: list[Record], path: Path) -> None:
    path.write_text(json.dumps(records, indent=1), encoding="utf-8")


def load_trace(path: Path) -> list[Record]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []


# --- Reading the trace (runs in the agent) ---


def _size_text(state: State | None) -> str:
    return " x ".join(f"{value:g}" for value in state["size"]) if state else "nothing"


def _amount(counts: dict[str, int], diameter: float) -> int:
    return sum(count for key, count in counts.items() if is_close(float(key), diameter))


def _holes_before(record: Record, kind: str, diameter: float) -> int:
    states = [state for state in (record["before"], *record["arguments"]) if state]
    return sum(_amount(state[kind], diameter) for state in states)


def _where(record: Record, lines: list[str]) -> str:
    source = lines[record["line"] - 1].strip() if 0 < record["line"] <= len(lines) else f".{record['method']}(...)"
    if len(source) > MAX_SOURCE_CHARS:
        source = source[: MAX_SOURCE_CHARS - 3] + "..."
    return f"line {record['line']} `{source}`"


def _hole_causes(diameter: float, check: Check, records: list[Record], lines: list[str]) -> list[str]:
    causes, makers = [], []
    for record in records:
        holes_in, holes_out = _holes_before(record, "holes", diameter), _amount(record["after"]["holes"], diameter)
        cuts_in, cuts_out = _holes_before(record, "cuts", diameter), _amount(record["after"]["cuts"], diameter)
        where = _where(record, lines)
        if holes_out < holes_in:
            causes.append(
                f"{where}: {holes_in - holes_out} complete hole(s) of diameter {diameter:g} disappeared here: the body joined "
                "here fills them. Cut the holes after joining the bodies."
            )
        if cuts_out > cuts_in:
            causes.append(
                f"{where}: makes {cuts_out - cuts_in} cut(s) of diameter {diameter:g} that break out through the edge of the "
                f"body it cuts (that body measures {_size_text(record['before'])}). Move them inside the body, or cut them "
                "in a body that is large enough."
            )
        if holes_out > holes_in:
            makers.append((record, holes_out - holes_in))

    made = ", ".join(f"{_where(record, lines)} makes {count}" for record, count in makers)
    expected, actual = check.expected, check.actual
    if not makers and not causes:
        causes.append(f"No line of the script makes a complete hole of diameter {diameter:g}.")
    elif isinstance(expected, int) and isinstance(actual, int) and makers and actual > expected:
        causes.append(f"Holes of diameter {diameter:g}: {made}. That is {actual - expected} too many.")
    elif isinstance(expected, int) and isinstance(actual, int) and makers and not causes and actual < expected:
        causes.append(f"Holes of diameter {diameter:g}: {made}. {expected - actual} more are needed.")
    return causes


def _size_causes(report: ValidationReport, records: list[Record], lines: list[str]) -> list[str]:
    unexplained_check = next((check for check in report.checks if check.name == "size:unexplained" and not check.passed), None)
    unexplained: list[float] = []
    if unexplained_check and isinstance(unexplained_check.expected, list):
        unexplained = [e for e in report.bounding_box if not any(is_close(e, v) for v in unexplained_check.expected)]

    causes = []
    for value in unexplained:
        first = next(
            (
                record
                for record in records
                if any(is_close(value, e) for e in record["after"]["size"])
                and not (record["before"] and any(is_close(value, e) for e in record["before"]["size"]))
            ),
            None,
        )
        if first:
            causes.append(
                f"{_where(first, lines)}: changes the size from {_size_text(first['before'])} to {_size_text(first['after'])}. "
                f"The {value:g} it introduces matches no dimension of the specification."
            )
    if not causes:
        changes = [r for r in records if not r["before"] or r["before"]["size"] != r["after"]["size"]][:6]
        causes += [f"{_where(r, lines)}: size {_size_text(r['before'])} -> {_size_text(r['after'])}" for r in changes]
    return causes


def _useless_steps(records: list[Record], lines: list[str]) -> list[str]:
    """Cuts that remove nothing and unions that add nothing: almost always a wrong position or size."""
    causes = []
    for record in records:
        before, after = record["before"], record["after"]
        if not before or before["volume"] != after["volume"]:
            continue
        if record["method"] in CUTTING_METHODS:
            causes.append(f"{_where(record, lines)}: removes no material at all: the positions of this cut are outside the body.")
        elif record["method"] in JOINING_METHODS and record["arguments"]:
            causes.append(
                f"{_where(record, lines)}: adds no material: the joined body ({_size_text(record['arguments'][0])}) lies "
                f"completely inside the other one ({_size_text(before)}). Move or rotate it to where it belongs."
            )
    return causes


def locate_problems(report: ValidationReport, records: list[Record], code: str) -> list[str]:
    """For each failed check, the lines of the script that cause it according to the trace."""
    if not records:
        return []
    lines = code.splitlines()
    causes: list[str] = []
    failed = [check for check in report.checks if not check.passed]

    for check in failed:
        kind, _, value = check.name.partition(":")
        if kind in {"holes", "extra_holes"}:
            causes += _hole_causes(float(value), check, records, lines)
    if any(check.name.startswith("size:") for check in failed):
        causes += _size_causes(report, records, lines)
    if any(check.name == "single_solid" for check in failed):
        split = next((r for r in records if r["after"]["solids"] > 1 and (not r["before"] or r["before"]["solids"] <= 1)), None)
        if split:
            solids = split["after"]["solids"]
            causes.append(f"{_where(split, lines)}: after this line the part is {solids} separate solids: the bodies do not touch.")
    if failed:
        causes += _useless_steps(records, lines)
    return list(dict.fromkeys(causes))


def _state_text(state: State) -> str:
    holes = ", ".join(f"{count} x {diameter}" for diameter, count in state["holes"].items()) or "none"
    text = f"size {_size_text(state)}, volume {state['volume']:g}, holes {holes}"
    if state["cuts"]:
        text += ", broken-out cuts " + ", ".join(f"{count} x {diameter}" for diameter, count in state["cuts"].items())
    if state["solids"] > 1:
        text += f", {state['solids']} separate solids"
    return text


def format_trace(records: list[Record], code: str, limit: int = MAX_TRACE_LINES) -> str:
    """One line per geometry-changing step: what the body looks like after it."""
    lines = code.splitlines()
    text = []
    for record in records[:limit]:
        unchanged = record["before"] is not None and record["before"]["volume"] == record["after"]["volume"]
        effect = "no change to the part" if unchanged else _state_text(record["after"])
        text.append(f"- {_where(record, lines)} -> {effect}")
    if len(records) > limit:
        text.append(f"- ... {len(records) - limit} more steps")
    return "\n".join(text)
