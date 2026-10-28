"""
Execute a generated CadQuery script and export the resulting part.

The script runs in a separate Python process (cad_runner.py) with a timeout, so a segfault or an
infinite loop in the generated code is reported as an error instead of killing the agent.
Note this isolates crashes, it is not a security sandbox.
"""

import argparse
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

from schemas import ExecutionResult

PROJECT_ROOT = Path(__file__).resolve().parent.parent
OUTPUT_DIR = PROJECT_ROOT / "outputs"
RUNNER_PATH = Path(__file__).resolve().parent / "cad_runner.py"

EXECUTION_TIMEOUT_S = 120


def new_run_dir(name: str, output_dir: Path = OUTPUT_DIR) -> Path:
    """Create a fresh directory for one agent run, e.g. outputs/runs/wheel_spacer_20261027_153000."""
    run_dir = output_dir / "runs" / f"{name}_{datetime.now():%Y%m%d_%H%M%S}"
    run_dir.mkdir(parents=True, exist_ok=True)
    return run_dir


def attempt_directory(run_dir: Path, attempt: int, candidate: int | None = None) -> Path:
    directory = run_dir / f"attempt_{attempt}"
    return directory / f"candidate_{candidate}" if candidate is not None else directory


def execute_cad_code(
    code: str,
    attempt: int,
    run_dir: Path = OUTPUT_DIR / "runs" / "latest",
    timeout_s: float = EXECUTION_TIMEOUT_S,
    *,
    candidate: int | None = None,
) -> ExecutionResult:
    """
    Write the script to run_dir/attempt_<n>/part.py, run it and export run_dir/attempt_<n>/part.step.
    A candidate (one of several scripts generated in the same attempt) goes to run_dir/attempt_<n>/candidate_<k>/.
    The runner also writes trace.json in that folder: what each line of the script did to the part.
    """
    attempt_dir = attempt_directory(run_dir, attempt, candidate)
    attempt_dir.mkdir(parents=True, exist_ok=True)
    code_path = attempt_dir / "part.py"
    model_path = attempt_dir / "part.step"
    code_path.write_text(code, encoding="utf-8")
    # Remove if already existing
    model_path.unlink(missing_ok=True)
    start = time.perf_counter()

    def failed(error: str, stdout: str = "") -> ExecutionResult:
        return ExecutionResult(
            success=False,
            attempt=attempt,
            candidate=candidate,
            code_path=code_path,
            error=error,
            stdout=stdout,
            duration_s=time.perf_counter() - start,
        )

    try:
        process = subprocess.run(
            [sys.executable, "-I", str(RUNNER_PATH), str(code_path), str(model_path)],
            capture_output=True,
            text=True,
            timeout=timeout_s,
            cwd=attempt_dir,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return failed(f"Execution timed out after {timeout_s:g} s (infinite loop or very heavy geometry?)")

    if process.returncode < 0:
        return failed(f"The script crashed the CAD kernel (signal {-process.returncode})", process.stdout)
    if process.returncode != 0:
        return failed(process.stderr.strip() or f"The script failed with exit code {process.returncode}", process.stdout)
    if not model_path.exists():
        return failed("The script ran but no model file was exported", process.stdout)

    return ExecutionResult(
        success=True,
        attempt=attempt,
        candidate=candidate,
        code_path=code_path,
        model_path=model_path,
        stdout=process.stdout,
        duration_s=time.perf_counter() - start,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Execute a CadQuery script and export the part to STEP")
    parser.add_argument("script", type=Path)
    args = parser.parse_args()

    result = execute_cad_code(args.script.read_text(encoding="utf-8"), attempt=1, run_dir=new_run_dir(args.script.stem))
    print(result.model_dump_json(indent=2))


if __name__ == "__main__":
    main()
