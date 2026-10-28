"""
Entry point:
1. Parse the natural language specification to a structured PartSpec
2. Think-Act-Observe loop:
   - Think: attempt 1 generates several candidate scripts; later attempts repair the best part so far with line edits,
     guided by the lines the build trace blames for each problem. When repairs stop improving the part, a fresh round
     of candidates is generated instead
   - Act: execute each script in a separate process, export the part to STEP and trace what each line did to it
   - Observe: validate the part against the spec; its errors and their causes become the feedback for the next attempt
3. Return the first part that passes validation, or the best part when out of attempts

Usage:
    python src/main.py specs/wheel_spacer.txt -v --stream
    python src/main.py --text "Rectangular plate 100 x 50 mm, 5 mm thick" --name plate
"""

import argparse
import json
import logging
import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

import llm_client
import recipe_book
from build_trace import TRACE_FILE, format_trace, load_trace, locate_problems
from cad_execute import OUTPUT_DIR, attempt_directory, execute_cad_code, new_run_dir
from cad_generator import CANDIDATE_TEMPERATURE, TEMPERATURE, CodeGenerationError, generate_cad_code, repair_cad_code
from cadquery_api import explain_runtime_error
from llm_client import DEFAULT_MODEL, LLMTimeoutError, ask_llm
from recipe_book import Embedder, OllamaEmbedder, Recipe, find_recipes
from schemas import AgentResult, ExecutionResult, PartSpec, ValidationReport
from specification_parser import LLMCallable, SpecParseError, parse_specification
from validator import validate_model

logger = logging.getLogger(__name__)

LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"

MAX_ATTEMPTS = 4
CANDIDATES = 3  # scripts generated in a fresh round; the best one is kept and repaired
STALL_LIMIT = 2  # repairs in a row without improvement before a fresh round of candidates


def save_attempt_log(  # noqa: PLR0913 - one argument per piece of the attempt
    attempt_dir: Path,
    feedback: str | list[str] | None,
    *,
    execution: ExecutionResult | None = None,
    validation: ValidationReport | None = None,
    generation_error: str | None = None,
    diagnosis: str | None = None,
    locations: list[str] | None = None,
) -> None:
    """Write <attempt_dir>/log.json with the feedback that produced this script and what came out of it."""
    attempt_dir.mkdir(parents=True, exist_ok=True)
    log = {
        "feedback_received": feedback,
        "diagnosis": diagnosis,
        "generation_error": generation_error,
        "execution": execution.model_dump(mode="json") if execution else None,
        "validation": validation.model_dump(mode="json") if validation else None,
        "problem_locations": locations,
    }
    (attempt_dir / "log.json").write_text(json.dumps(log, indent=2), encoding="utf-8")


@dataclass
class Outcome:
    """One executed script and everything learned from it."""

    code: str
    execution: ExecutionResult
    report: ValidationReport | None = None
    problems: list[str] = field(default_factory=list)  # errors and warnings, or the traceback
    locations: list[str] = field(default_factory=list)  # the lines that cause the problems, from the build trace
    trace: str = ""  # what each line did to the part

    @property
    def score(self) -> tuple[int, int, int]:
        """Lower is better: a part beats a failed execution, then fewer errors, then fewer warnings."""
        if self.report is None:
            return (1, 0, 0)
        return (0, len(self.report.errors), len(self.report.warnings))

    @property
    def label(self) -> str:
        candidate = self.execution.candidate
        return f"{self.execution.attempt}" + (f", candidate {candidate}" if candidate is not None else "")


def _execution_feedback(execution: ExecutionResult) -> str:
    feedback = f"Running the script failed:\n{execution.error}"
    # A wrong method or argument name: show the real signature instead of letting the LLM guess another one
    if api_help := explain_runtime_error(execution.error or ""):
        feedback += f"\n{api_help}"
    return feedback


def _retrieve_recipes(spec: PartSpec, run_dir: Path, count: int | None, embedder: Embedder | None) -> list[Recipe]:
    """The recipes most similar to the spec; their names and similarities are saved to run_dir/recipes.json."""
    logger.info("Looking up recipes for similar parts")
    ranked = find_recipes(spec, count, embedder=embedder)
    summary = [{"name": recipe.name, "title": recipe.title, "similarity": round(score, 4)} for recipe, score in ranked]
    (run_dir / "recipes.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    return [recipe for recipe, _ in ranked]


def _log_report(label: str, report: ValidationReport, locations: list[str]) -> None:
    logger.warning("%s: %d validation error(s)", label, len(report.errors))
    for message in report.errors:
        logger.warning("  error: %s", message)
    for message in report.warnings:
        logger.warning("  warning: %s", message)
    for message in locations:
        logger.info("  cause: %s", message)


def _feedback_from(report: ValidationReport) -> list[str]:
    # Warnings do not fail the part on their own, but they often explain the errors, so the LLM sees them too
    return report.errors + [f"(warning) {message}" for message in report.warnings]


def _finish(run_dir: Path, result: AgentResult) -> AgentResult:
    """Copy the selected attempt to run_dir/final.{step,py} and write run_dir/result.json."""
    if result.model_path and result.code_path:
        result = result.model_copy(
            update={
                "model_path": shutil.copy(result.model_path, run_dir / "final.step"),
                "code_path": shutil.copy(result.code_path, run_dir / "final.py"),
            }
        )
    (run_dir / "result.json").write_text(result.model_dump_json(indent=2), encoding="utf-8")
    return result


def run_agent(  # noqa: PLR0913 - keyword-only knobs for configuration and test injection
    natural_language_spec: str,
    *,
    name: str = "part",
    model: str = DEFAULT_MODEL,
    max_attempts: int = MAX_ATTEMPTS,
    candidates: int = CANDIDATES,
    require_complete: bool = False,
    output_dir: Path = OUTPUT_DIR,
    llm: LLMCallable = ask_llm,
    recipe_count: int | None = None,
    embedder: Embedder | Literal["ollama"] | None = "ollama",
) -> AgentResult:
    """
    Run the agent in a new folder under output_dir/runs. Everything that is logged also goes to <run folder>/run.log.

    max_attempts: attempts of the loop; a round of candidates counts as one attempt.
    candidates: scripts generated in a fresh round (attempt 1, and after repairs stall); the best one is repaired.
    recipe_count: how many similar recipes to show the LLM (default recipe_book.settings.top_k, 0 disables them).
    embedder: "ollama" embeds with recipe_book.settings.embedding_model; None ranks recipes with TF-IDF only.
    """
    if embedder == "ollama":
        embedder = OllamaEmbedder(recipe_book.settings.embedding_model)
    run_dir = new_run_dir(name, output_dir)
    (run_dir / "input.txt").write_text(natural_language_spec, encoding="utf-8")

    file_handler = logging.FileHandler(run_dir / "run.log", encoding="utf-8")
    file_handler.setFormatter(logging.Formatter(LOG_FORMAT))
    logging.getLogger().addHandler(file_handler)
    logger.info("Run folder: %s", run_dir)
    try:
        return _run_loop(
            natural_language_spec,
            run_dir,
            model=model,
            max_attempts=max_attempts,
            candidates=candidates,
            require_complete=require_complete,
            llm=llm,
            recipe_count=recipe_count,
            embedder=embedder,
        )
    finally:
        logging.getLogger().removeHandler(file_handler)
        file_handler.close()


@dataclass
class _Context:
    """What every step of the loop needs."""

    spec: PartSpec
    run_dir: Path
    model: str
    llm: LLMCallable
    recipes: list[Recipe]
    tried: list[str] = field(default_factory=list)  # executed scripts: generating one of them again is rejected


def _evaluate(  # noqa: PLR0913 - where the script comes from, for the attempt log
    context: _Context,
    code: str,
    attempt: int,
    *,
    candidate: int | None = None,
    feedback: list[str] | None = None,
    diagnosis: str | None = None,
) -> Outcome:
    """Act and observe: execute the script, validate the part and find the lines that cause its problems."""
    label = f"Attempt {attempt}" + (f" candidate {candidate}" if candidate is not None else "")
    context.tried.append(code)
    execution = execute_cad_code(code, attempt, context.run_dir, candidate=candidate)
    trace_records = load_trace(execution.code_path.parent / TRACE_FILE)
    outcome = Outcome(code=code, execution=execution, trace=format_trace(trace_records, code))

    if not execution.success or execution.model_path is None:
        logger.warning("%s: execution failed:\n%s", label, execution.error)
        outcome.problems = [_execution_feedback(execution)]
    else:
        try:
            outcome.report = validate_model(execution.model_path, context.spec, code)
        except Exception as exc:  # the CAD kernel can fail on odd geometry; treat it as feedback, not a crash
            logger.warning("%s: validation failed to run: %s", label, exc)
            outcome.problems = [f"The exported part could not be analysed ({exc}). Simplify the geometry."]
        else:
            outcome.problems = _feedback_from(outcome.report)
            outcome.locations = locate_problems(outcome.report, trace_records, code)
            if outcome.report.passed:
                logger.info("%s: part passed validation", label)
            else:
                _log_report(label, outcome.report, outcome.locations)

    save_attempt_log(
        execution.code_path.parent,
        feedback,
        execution=execution,
        validation=outcome.report,
        diagnosis=diagnosis,
        locations=outcome.locations,
    )
    return outcome


def _candidate_round(context: _Context, attempt: int, count: int, failed: Outcome | None) -> list[Outcome]:
    """Generate up to `count` scripts and evaluate them, stopping early when one passes."""
    outcomes: list[Outcome] = []
    for candidate in range(1, count + 1):
        # The first candidate of the first round is the model's most likely script, the others explore
        temperature = TEMPERATURE if candidate == 1 and failed is None else CANDIDATE_TEMPERATURE
        try:
            code = generate_cad_code(
                context.spec,
                model=context.model,
                llm=context.llm,
                recipes=context.recipes,
                tried=context.tried,
                temperature=temperature,
                failed_problems=failed.problems if failed else (),
            )
        except (CodeGenerationError, LLMTimeoutError) as exc:
            logger.warning("Attempt %d candidate %d: generation failed: %s", attempt, candidate, exc)
            save_attempt_log(attempt_directory(context.run_dir, attempt, candidate), None, generation_error=str(exc))
            continue
        outcome = _evaluate(context, code, attempt, candidate=candidate)
        outcomes.append(outcome)
        if outcome.report and outcome.report.passed:
            break
    return outcomes


def _repair(context: _Context, attempt: int, base: Outcome) -> Outcome | None:
    """Fix the lines of the best script that cause its problems."""
    try:
        code, diagnosis = repair_cad_code(
            context.spec,
            base.code,
            base.problems,
            locations=base.locations,
            trace=base.trace,
            model=context.model,
            llm=context.llm,
            recipes=context.recipes,
            tried=context.tried,
        )
    except (CodeGenerationError, LLMTimeoutError) as exc:
        logger.warning("Attempt %d: repair failed: %s", attempt, exc)
        save_attempt_log(attempt_directory(context.run_dir, attempt), base.problems, generation_error=str(exc))
        return None
    return _evaluate(context, code, attempt, feedback=base.problems, diagnosis=diagnosis)


def _run_loop(  # noqa: PLR0913 - same knobs as run_agent
    natural_language_spec: str,
    run_dir: Path,
    *,
    model: str,
    max_attempts: int,
    candidates: int,
    require_complete: bool,
    llm: LLMCallable,
    recipe_count: int | None,
    embedder: Embedder | None,
) -> AgentResult:
    logger.info("Parsing the specification")
    spec = parse_specification(natural_language_spec, model=model, llm=llm)
    (run_dir / "spec.json").write_text(spec.model_dump_json(indent=2), encoding="utf-8")
    for warning in spec.warnings:
        logger.warning("Spec: %s", warning)

    if spec.missing_parameters:
        missing = "; ".join(spec.missing_parameters)
        if require_complete:
            return _finish(run_dir, AgentResult(success=False, attempts=0, run_dir=run_dir, message=f"Specification is incomplete: {missing}"))
        logger.warning("Spec is missing: %s. The generator will choose and declare these values.", missing)

    context = _Context(spec=spec, run_dir=run_dir, model=model, llm=llm, recipes=_retrieve_recipes(spec, run_dir, recipe_count, embedder))
    best: Outcome | None = None
    stalled = 0  # repairs in a row that did not improve on the best part

    for attempt in range(1, max_attempts + 1):
        if best is None or stalled >= STALL_LIMIT:
            # Think broadly: several independent scripts; after a stall, ask for a different approach
            restart = best is not None
            approach = " with a new approach" if restart else ""
            logger.info("Attempt %d/%d: generating %d candidate scripts%s", attempt, max_attempts, candidates, approach)
            outcomes = _candidate_round(context, attempt, candidates, best if restart else None)
            stalled = 0
        else:
            # Think locally: fix the lines of the best script that cause its problems
            logger.info("Attempt %d/%d: repairing the best script (attempt %s)", attempt, max_attempts, best.label)
            repaired = _repair(context, attempt, best)
            outcomes = [repaired] if repaired else []
            stalled += 1

        passed = next((outcome for outcome in outcomes if outcome.report and outcome.report.passed), None)
        if passed:
            return _finish(run_dir, _result(passed, run_dir, attempt, success=True, message="Part passed validation"))

        round_best = min(outcomes, key=lambda outcome: outcome.score, default=None)
        if round_best is None:
            continue
        if best is None or round_best.score < best.score:
            best, stalled = round_best, 0
            logger.info("Attempt %d: best part so far is attempt %s (%d error(s))", attempt, best.label, best.score[1])
        elif round_best.score == best.score:
            best = round_best  # same quality: continue from the newer script, so the next repair explores further
        else:
            logger.warning("Attempt %d made the part worse, the next step starts again from attempt %s", attempt, best.label)

    if best is None or best.report is None:
        return _finish(run_dir, AgentResult(success=False, attempts=max_attempts, run_dir=run_dir, message="No attempt produced a part"))
    message = f"Maximum attempts reached, returning the best attempt ({best.label}) with {len(best.report.errors)} error(s)"
    return _finish(run_dir, _result(best, run_dir, max_attempts, success=False, message=message))


def _result(outcome: Outcome, run_dir: Path, attempts: int, *, success: bool, message: str) -> AgentResult:
    return AgentResult(
        success=success,
        attempts=attempts,
        run_dir=run_dir,
        message=message,
        model_path=outcome.execution.model_path,
        code_path=outcome.execution.code_path,
        validation=outcome.report,
    )


def print_summary(result: AgentResult) -> None:
    print(f"\n{'SUCCESS' if result.success else 'FAILED'}: {result.message}")
    print(f"Attempts: {result.attempts}")
    print(f"Run folder: {result.run_dir}")
    if result.model_path:
        print(f"Model: {result.model_path}")
        print(f"Code: {result.code_path}")
    if result.validation:
        x, y, z = result.validation.bounding_box
        print(f"Bounding box: {x:g} x {y:g} x {z:g}, volume {result.validation.volume:.1f}")
        for label, items in (("Errors", result.validation.errors), ("Warnings", result.validation.warnings)):
            for item in items:
                print(f"  {label[:-1]}: {item}")
        if result.validation.unverified:
            print(f"  Not verified: {', '.join(result.validation.unverified)}")


def setup_logging(verbose: bool) -> None:
    """Terminal shows INFO with -v (WARNING otherwise); run.log always gets everything down to DEBUG, prompts included."""
    console = logging.StreamHandler()
    console.setLevel(logging.INFO if verbose else logging.WARNING)
    console.setFormatter(logging.Formatter(LOG_FORMAT, datefmt="%H:%M:%S"))
    root = logging.getLogger()
    root.setLevel(logging.DEBUG)
    root.addHandler(console)
    # HTTP request lines and library internals add noise without information
    for noisy in ("httpx", "httpcore"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def main() -> int:
    parser = argparse.ArgumentParser(description="Turn a natural language part specification into a validated CAD model")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("spec_file", nargs="?", type=Path, help="Text file with the specification, e.g. specs/bracket.txt")
    source.add_argument("--text", help="Specification given directly on the command line")
    parser.add_argument("--name", help="Name of the run folder (default: spec file name or 'part')")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--max-attempts", type=int, default=MAX_ATTEMPTS, help="Attempts of the loop; a round of candidates counts as one")
    parser.add_argument("--candidates", type=int, default=CANDIDATES, help="Scripts generated per fresh round; the best one is repaired")
    parser.add_argument("--require-complete", action="store_true", help="Stop instead of assuming values for missing parameters")
    parser.add_argument("--stream", action="store_true", help="Show the LLM answers in the terminal as they are generated")
    parser.add_argument("--llm-timeout", type=float, default=llm_client.settings.timeout_s, help="Max seconds for one LLM answer")
    parser.add_argument("--max-tokens", type=int, default=llm_client.settings.max_tokens, help="Max length of one LLM answer")
    parser.add_argument("--recipes", type=int, default=recipe_book.settings.top_k, help="Number of similar recipes shown to the LLM (0: none)")
    parser.add_argument("--embed-model", default=recipe_book.settings.embedding_model, help="Ollama embedding model used to rank recipes")
    parser.add_argument("--no-embeddings", action="store_true", help="Rank recipes with TF-IDF instead of embeddings")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    setup_logging(args.verbose)
    llm_client.settings.stream_to_terminal = args.stream
    llm_client.settings.timeout_s = args.llm_timeout
    llm_client.settings.max_tokens = args.max_tokens
    recipe_book.settings.embedding_model = args.embed_model

    text = args.text if args.text else args.spec_file.read_text(encoding="utf-8")
    name = args.name or (args.spec_file.stem if args.spec_file else "part")

    try:
        result = run_agent(
            text,
            name=name,
            model=args.model,
            max_attempts=args.max_attempts,
            candidates=args.candidates,
            require_complete=args.require_complete,
            recipe_count=args.recipes,
            embedder=None if args.no_embeddings else "ollama",
        )
    except SpecParseError as exc:
        print(f"Could not parse the specification: {exc}", file=sys.stderr)
        return 2
    except LLMTimeoutError as exc:
        print(f"The LLM did not answer in time while parsing the specification: {exc}", file=sys.stderr)
        return 2
    except ConnectionError as exc:
        print(f"Could not reach the LLM, is `ollama serve` running? ({exc})", file=sys.stderr)
        return 2

    print_summary(result)
    return 0 if result.success else 1


if __name__ == "__main__":
    sys.exit(main())
