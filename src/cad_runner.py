"""
Executed in a separate process by cad_execute: runs a generated CadQuery script and exports its `result` to STEP.
Keeping this out of the agent process means a crash or hang in OpenCascade cannot take the agent down.

The script runs with tracing (build_trace): what each line did to the part is written to trace.json next to the
STEP file, also when the script fails, so the feedback can point at the line that causes a problem.

Usage: python cad_runner.py <code_path> <step_path>
Exit codes: 0 success, 1 the script raised an exception, 2 `result` is missing or not a solid
"""

import sys
import traceback
from pathlib import Path

# Started with `python -I`, so the folder of this file is not on sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent))

from build_trace import TRACE_FILE, run_traced, save_trace


def fail(message: str, code: int) -> int:
    print(message, file=sys.stderr)
    return code


def main() -> int:
    code_path, step_path = sys.argv[1], sys.argv[2]
    source = Path(code_path).read_text(encoding="utf-8")
    namespace: dict = {"__name__": "__main__", "__file__": code_path}
    trace_path = Path(step_path).parent / TRACE_FILE

    try:
        save_trace(run_traced(source, code_path, namespace), trace_path)
    except Exception as exc:
        save_trace(getattr(exc, "trace_records", []), trace_path)
        # Keep only the frames from the generated script so the feedback points at its lines, not CadQuery internals
        frames = [frame for frame in traceback.extract_tb(exc.__traceback__) if frame.filename == code_path]
        lines = traceback.format_list(frames) + traceback.format_exception_only(exc)
        return fail("Traceback (generated script only):\n" + "".join(lines).replace(code_path, "part.py"), 1)

    import cadquery as cq  # noqa: PLC0415 - only needed once the script has run

    result = namespace.get("result")
    if result is None:
        return fail("The script did not assign the final part to a variable named `result`", 2)

    if isinstance(result, cq.Workplane):
        shapes = [value for value in result.vals() if isinstance(value, cq.Shape)]
        if not shapes:
            return fail("`result` is a Workplane with no shape on its stack", 2)
        shape = shapes[0] if len(shapes) == 1 else cq.Compound.makeCompound(shapes)
    elif isinstance(result, cq.Shape):
        shape = result
    else:
        return fail(f"`result` must be a cq.Workplane or cq.Shape, got {type(result).__name__}", 2)

    if not shape.Solids():
        return fail(f"`result` contains no solid, only {type(shape).__name__} geometry (missing extrude?)", 2)

    cq.exporters.export(shape, step_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
