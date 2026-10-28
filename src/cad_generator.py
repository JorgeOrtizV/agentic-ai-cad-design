"""
Turn a structured PartSpec into a CadQuery script.

generate_cad_code writes a new script:
1. Build a prompt from the spec, including the recipes retrieved for the spec and the real signatures of the CadQuery
   methods they use. The generation prompt always holds two generic examples as well, so the model has worked
   examples even when no recipe is retrieved
2. Ask the LLM for a script and extract the code block
3. Run cheap static checks before anything is executed: syntax, imports, `result` assigned, and every CadQuery
   method call checked against the installed library (cadquery_api)
4. If a static check fails, or the script was already tried, feed that back to the LLM and retry

repair_cad_code fixes an existing script: the LLM sees it with line numbers, the problems, the lines that cause them
(build_trace) and answers with a diagnosis and line edits (JSON), which are applied and checked like a new script.
"""

import argparse
import ast
import itertools
import logging
import re
from collections.abc import Callable, Collection, Sequence
from pathlib import Path

from pydantic import ValidationError

from cadquery_api import api_reference, check_api_calls
from llm_client import DEFAULT_MODEL, ask_llm
from recipe_book import Recipe
from schemas import LineEdit, PartSpec, RepairPlan

logger = logging.getLogger(__name__)

PROMPTS_DIR = Path(__file__).resolve().parent.parent / "prompts"
GENERATE_PROMPT_PATH = PROMPTS_DIR / "generate_cad_code.md"
REPAIR_PROMPT_PATH = PROMPTS_DIR / "repair_cad_code.md"

MAX_GENERATION_ATTEMPTS = 2

# Recipes that are already in the generation prompt as its generic examples: showing them again would repeat them
EXAMPLE_RECIPES = {"rectangular_plate_corner_holes", "round_flange_bolt_circle"}
# The first answer is near-deterministic; retries sample more freely so the model does not repeat the same script
TEMPERATURE = 0.2
RETRY_TEMPERATURE = 0.7
# Candidates generated side by side should differ: sample them freely
CANDIDATE_TEMPERATURE = 0.8

ALLOWED_IMPORTS = {"cadquery", "math"}
FORBIDDEN_CALLS = {"open", "exec", "eval", "compile", "__import__", "input", "print", "show", "show_object"}

# Parse obtained python block
CODE_BLOCK_PATTERN = re.compile(r"```(?:python|py)?\s*\n(.*?)```", re.DOTALL)
# Line numbers of the repair prompt that a model may copy into its replacement code
LINE_NUMBER_PREFIX = re.compile(r"^\s*\d+\| ?")

LLMCallable = Callable[..., str | None]


class CodeGenerationError(Exception):
    """Raised when the LLM does not produce a script that passes the static checks."""


def extract_code(raw: str | None) -> str:
    """Return the first fenced code block, or the whole response if there is none."""
    if not raw:
        return ""
    match = CODE_BLOCK_PATTERN.search(raw)
    return (match.group(1) if match else raw).strip()


def check_code(code: str) -> list[str]:
    """
    Static checks run before executing the script. This is a guard against obvious mistakes,
    not a security sandbox: the script still runs as ordinary Python in cad_execute.
    """
    if not code:
        return ["The response did not contain any code"]

    # Transform code to abstract syntax tree and evaluate every part individually
    try:
        tree = ast.parse(code)
    except SyntaxError as exc:
        return [f"Syntax error on line {exc.lineno}: {exc.msg}"]

    errors: list[str] = []
    assigns_result = False

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules = [alias.name.split(".")[0] for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            modules = [(node.module or "").split(".")[0]]
        else:
            modules = []
        errors.extend(
            f"Import of '{module}' is not allowed, only {sorted(ALLOWED_IMPORTS)}"
            for module in modules
            if module not in ALLOWED_IMPORTS
        )

        if isinstance(node, ast.Call):
            func = node.func
            name = func.id if isinstance(func, ast.Name) else func.attr if isinstance(func, ast.Attribute) else None
            if name in FORBIDDEN_CALLS or name == "export":
                errors.append(f"Call to '{name}' is not allowed, only build the geometry")

        if isinstance(node, ast.Assign | ast.AnnAssign | ast.AugAssign):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            assigns_result |= any(isinstance(target, ast.Name) and target.id == "result" for target in targets)

    if not any(isinstance(node, ast.Import) and any(a.name == "cadquery" for a in node.names) for node in tree.body):
        errors.append("The script must start with `import cadquery as cq`")
    if not assigns_result:
        errors.append("The final part must be assigned to a variable named `result`")

    return errors + check_api_calls(tree)


def _spec_json(spec: PartSpec) -> str:
    return spec.model_dump_json(indent=2, exclude={"warnings"})


def _format_feedback(feedback: str | list[str]) -> str:
    if isinstance(feedback, str):
        return feedback
    return "\n".join(f"- {item}" for item in feedback)


def build_prompt(spec: PartSpec, recipes: Sequence[Recipe] = (), failed_problems: Sequence[str] = ()) -> str:
    """
    The generation prompt. failed_problems are the problems of an earlier approach that repairs could not fix: the
    model is asked for a different approach.
    """
    reference = api_reference([recipe.code for recipe in recipes])
    extra_recipes = [recipe for recipe in recipes if recipe.name not in EXAMPLE_RECIPES]
    recipes_text = "\n\n".join(recipe.to_prompt() for recipe in extra_recipes) or "No recipe beyond the examples above."
    template = GENERATE_PROMPT_PATH.read_text(encoding="utf-8")
    prompt = template.replace("{recipes}", recipes_text).replace("{api_reference}", reference).replace("{specification}", _spec_json(spec))
    if failed_problems:
        # Just before the final "Script:" (the examples have their own "Script:" lines)
        head, _, tail = prompt.rpartition("\nScript:")
        note = (
            "\n# A previous approach failed\nAn earlier script for this part kept these problems after several repairs. "
            f"Use a different approach:\n{_format_feedback(list(failed_problems))}\n"
        )
        prompt = f"{head}{note}\nScript:{tail}"
    return prompt


def number_lines(code: str) -> str:
    """The script with line numbers, as shown in the repair prompt: '  7| result = ...'."""
    return "\n".join(f"{number:>3}| {line}" for number, line in enumerate(code.splitlines(), start=1))


def build_repair_prompt(  # noqa: PLR0913 - one argument per section of the prompt
    spec: PartSpec,
    code: str,
    problems: Sequence[str],
    *,
    locations: Sequence[str] = (),
    trace: str = "",
    recipes: Sequence[Recipe] = (),
) -> str:
    recipes_text = "\n\n".join(recipe.to_prompt() for recipe in recipes) or "No recipe available for a similar part."
    template = REPAIR_PROMPT_PATH.read_text(encoding="utf-8")
    # The script goes in last, so text inside it is never mistaken for a placeholder
    return (
        template.replace("{specification}", _spec_json(spec))
        .replace("{problems}", _format_feedback(list(problems)))
        .replace("{locations}", _format_feedback(list(locations)) or "Not known: find the lines yourself.")
        .replace("{trace}", trace or "Not available.")
        .replace("{recipes}", recipes_text)
        .replace("{api_reference}", api_reference([code, *(recipe.code for recipe in recipes)]))
        .replace("{numbered_code}", number_lines(code))
    )


def fingerprint(code: str) -> str:
    """The script without comments and formatting, so a copy with only cosmetic changes counts as the same script."""
    try:
        return ast.dump(ast.parse(code))
    except SyntaxError:
        return code.strip()


ALREADY_TRIED = "This script is the same as one that was already executed, so it has the same problems."


def generate_cad_code(  # noqa: PLR0913 - keyword-only knobs for model choice and test injection
    spec: PartSpec,
    *,
    model: str = DEFAULT_MODEL,
    max_attempts: int = MAX_GENERATION_ATTEMPTS,
    llm: LLMCallable = ask_llm,
    recipes: Sequence[Recipe] = (),
    tried: Collection[str] = (),
    temperature: float = TEMPERATURE,
    failed_problems: Sequence[str] = (),
) -> str:
    """
    Return a new CadQuery script for the spec.
    `recipes` are reference scripts for similar parts (see recipe_book.find_recipes), shown to the LLM as examples.
    `tried` are scripts already executed: returning one of them again is rejected, since it would fail the same way.
    `temperature` of the first answer: higher values give more varied candidates; retries use at least RETRY_TEMPERATURE.

    Raises CodeGenerationError if no script passes the static checks within max_attempts.
    """
    messages = [{"role": "user", "content": build_prompt(spec, recipes, failed_problems)}]
    tried_fingerprints = {fingerprint(script) for script in tried}
    errors: list[str] = []

    for attempt in range(1, max_attempts + 1):
        raw = llm(model=model, messages=messages, temperature=temperature if attempt == 1 else max(temperature, RETRY_TEMPERATURE))
        code = extract_code(raw)
        errors = check_code(code)
        if not errors and fingerprint(code) in tried_fingerprints:
            errors = [f"{ALREADY_TRIED} Write a different script."]
        if not errors:
            logger.info("Generation attempt %d: code passed static checks", attempt)
            return code

        logger.info("Generation attempt %d: %d static check errors", attempt, len(errors))
        messages.append({"role": "assistant", "content": raw or ""})
        correction = f"The script has these problems:\n{_format_feedback(errors)}\nReturn the complete corrected script in a ```python block."
        messages.append({"role": "user", "content": correction})

    raise CodeGenerationError(f"No valid script after {max_attempts} attempts: {errors}")


class EditError(ValueError):
    """The line edits of a repair cannot be applied to the script."""


def _clean_replacement(text: str) -> str:
    """Remove code fences and copied line-number prefixes ('  7| ') that small models sometimes add."""
    if match := CODE_BLOCK_PATTERN.search(text):
        text = match.group(1)
    return "\n".join(LINE_NUMBER_PREFIX.sub("", line) for line in text.rstrip("\n").split("\n")) if text.strip() else ""


def apply_edits(code: str, edits: Sequence[LineEdit]) -> str:
    """Apply line edits whose numbers all refer to the original script. Raises EditError for invalid or overlapping ranges."""
    lines = code.splitlines()
    if not edits:
        raise EditError("The answer contains no edits. Give at least one edit that fixes a problem.")
    ordered = sorted(edits, key=lambda edit: edit.first_line)
    for edit in ordered:
        if not 1 <= edit.first_line <= edit.last_line <= len(lines):
            raise EditError(
                f"Edit of lines {edit.first_line}-{edit.last_line} is outside the script (lines 1-{len(lines)}), "
                "or its last_line is before its first_line."
            )
    for previous, current in itertools.pairwise(ordered):
        if current.first_line <= previous.last_line:
            raise EditError(f"Edits of lines {previous.first_line}-{previous.last_line} and {current.first_line}-{current.last_line} overlap.")
    # From the bottom up, so the line numbers of the edits still to apply do not move
    for edit in reversed(ordered):
        replacement = _clean_replacement(edit.replacement)
        lines[edit.first_line - 1 : edit.last_line] = replacement.split("\n") if replacement else []
    return "\n".join(lines)


def repair_cad_code(  # noqa: PLR0913 - keyword-only knobs for model choice and test injection
    spec: PartSpec,
    code: str,
    problems: Sequence[str],
    *,
    locations: Sequence[str] = (),
    trace: str = "",
    model: str = DEFAULT_MODEL,
    max_attempts: int = MAX_GENERATION_ATTEMPTS,
    llm: LLMCallable = ask_llm,
    recipes: Sequence[Recipe] = (),
    tried: Collection[str] = (),
) -> tuple[str, str]:
    """
    Fix the lines of `code` that cause `problems` and return (repaired script, the LLM's diagnosis).

    `locations` say which lines cause which problem and `trace` what each line did to the part (see build_trace).
    The LLM answers with JSON constrained to RepairPlan: a diagnosis first, then line edits. Editing lines instead of
    rewriting the script keeps everything that already works, and stops a small model from copying the script unchanged.

    Raises CodeGenerationError if no valid repair is produced within max_attempts.
    """
    messages = [{"role": "user", "content": build_repair_prompt(spec, code, problems, locations=locations, trace=trace, recipes=recipes)}]
    schema = RepairPlan.model_json_schema()
    tried_fingerprints = {fingerprint(script) for script in tried}
    errors: list[str] = []

    for attempt in range(1, max_attempts + 1):
        raw = llm(model=model, messages=messages, schema=schema, temperature=TEMPERATURE if attempt == 1 else RETRY_TEMPERATURE)
        plan: RepairPlan | None = None
        try:
            plan = RepairPlan.model_validate_json(raw or "")
            repaired = apply_edits(code, plan.edits)
            errors = check_code(repaired)
            if not errors and fingerprint(repaired) in tried_fingerprints:
                errors = [f"{ALREADY_TRIED} The edits must change the operations that cause the problems."]
        except ValidationError:
            errors = ['The answer must be JSON with "diagnosis" (text) and "edits" (a list of first_line, last_line, replacement).']
        except EditError as exc:
            errors = [str(exc)]
        if not errors and plan is not None:
            logger.info("Repair attempt %d: edits passed static checks. Diagnosis: %s", attempt, plan.diagnosis)
            return repaired, plan.diagnosis

        logger.info("Repair attempt %d: %d problem(s) with the edits", attempt, len(errors))
        messages.append({"role": "assistant", "content": raw or ""})
        correction = (
            f"After applying your edits, the script has these problems:\n{_format_feedback(errors)}\n"
            "Answer again with the JSON. Line numbers always refer to the ORIGINAL numbered script above."
        )
        messages.append({"role": "user", "content": correction})

    raise CodeGenerationError(f"No valid repair after {max_attempts} attempts: {errors}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate a CadQuery script from a parsed specification")
    parser.add_argument("spec_file", type=Path, help="JSON spec produced by specification_parser, e.g. outputs/bracket.spec.json")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING)

    spec = PartSpec.model_validate_json(args.spec_file.read_text(encoding="utf-8"))
    print(generate_cad_code(spec, model=args.model))


if __name__ == "__main__":
    main()
