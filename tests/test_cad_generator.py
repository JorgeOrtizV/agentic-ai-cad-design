import pytest
from fakes import FakeLLM, repair_answer

from cad_generator import (
    CODE_BLOCK_PATTERN,
    GENERATE_PROMPT_PATH,
    CodeGenerationError,
    EditError,
    apply_edits,
    build_prompt,
    build_repair_prompt,
    check_code,
    extract_code,
    generate_cad_code,
    repair_cad_code,
)
from recipe_book import load_recipes
from schemas import LineEdit, PartSpec

SPEC = PartSpec(
    part_type="wheel_spacer",
    description="Disc with a center bore and five bolt holes",
    parameters={"diameter": 150, "thickness": 20},
    features=[
        {"type": "center_bore", "parameters": {"diameter": 65}},
        {"type": "through_hole", "count": 5, "parameters": {"diameter": 15}},
    ],
    missing_parameters=["bolt circle diameter"],
    warnings=["parser-only note"],
)

GOOD_CODE = """import cadquery as cq

diameter = 150.0
thickness = 20.0
result = cq.Workplane("XY").circle(diameter / 2).extrude(thickness)"""


def fenced(code: str) -> str:
    return f"Here is the script:\n```python\n{code}\n```\nHope it helps."


def test_extract_code_from_fenced_and_bare_responses():
    assert extract_code(fenced(GOOD_CODE)) == GOOD_CODE
    assert extract_code(GOOD_CODE) == GOOD_CODE
    assert extract_code(None) == ""


def test_check_code_accepts_valid_script():
    assert check_code(GOOD_CODE) == []


@pytest.mark.parametrize(
    ("code", "expected"),
    [
        ("import cadquery as cq\nresult = (", "Syntax error"),
        ("import cadquery as cq\npart = cq.Workplane().box(1, 1, 1)", "`result`"),
        ("import os\nimport cadquery as cq\nresult = 1", "Import of 'os'"),
        ("import cadquery as cq\nresult = cq.Workplane().box(1, 1, 1)\ncq.exporters.export(result, 'a.step')", "'export'"),
        ("from math import pi\nresult = pi", "import cadquery as cq"),
        ("import cadquery as cq\nresult = cq.Workplane().box(1, 1, 1)\nprint(result.val().BoundingBox())", "'print'"),
    ],
)
def test_check_code_rejects_bad_scripts(code, expected):
    assert any(expected in error for error in check_code(code))


def test_generation_prompt_contains_spec_but_not_parser_warnings():
    prompt = build_prompt(SPEC)

    assert '"bolt circle diameter"' in prompt
    assert "parser-only note" not in prompt
    assert "{specification}" not in prompt


def test_repair_prompt_has_numbered_script_problems_and_their_causes():
    prompt = build_repair_prompt(
        SPEC,
        GOOD_CODE,
        ["Expected one solid, got 2"],
        locations=["line 5 `result = ...`: after this line the part is 2 separate solids"],
        trace="- line 5 `result = ...` -> size 150 x 150 x 20",
    )

    assert '  5| result = cq.Workplane("XY").circle(diameter / 2).extrude(thickness)' in prompt
    assert "- Expected one solid, got 2" in prompt
    assert "- line 5 `result = ...`: after this line the part is 2 separate solids" in prompt
    assert "-> size 150 x 150 x 20" in prompt


def test_generate_returns_code_on_first_valid_response():
    llm = FakeLLM(fenced(GOOD_CODE))

    assert generate_cad_code(SPEC, llm=llm) == GOOD_CODE
    assert len(llm.calls) == 1


def test_generate_retries_with_static_check_feedback():
    llm = FakeLLM(fenced("import cadquery as cq\npart = 1"), fenced(GOOD_CODE))

    assert generate_cad_code(SPEC, llm=llm) == GOOD_CODE
    assert "`result`" in llm.calls[1][-1]["content"]


def test_generate_raises_after_max_attempts():
    llm = FakeLLM("no code here", "still no code")

    with pytest.raises(CodeGenerationError):
        generate_cad_code(SPEC, llm=llm, max_attempts=2)


def recipe(name: str):
    return next(recipe for recipe in load_recipes() if recipe.name == name)


def test_prompts_include_recipes_and_api_reference():
    tube = recipe("tube_hollow_cylinder")

    for prompt in (build_prompt(SPEC, recipes=[tube]), build_repair_prompt(SPEC, GOOD_CODE, ["error"], recipes=[tube])):
        assert tube.title in prompt
        assert tube.code in prompt
        assert "Workplane.workplane(offset=0.0" in prompt
        assert "{recipes}" not in prompt
        assert "{api_reference}" not in prompt


def test_generation_prompt_always_has_the_generic_examples():
    for prompt in (build_prompt(SPEC), build_prompt(SPEC, recipes=[recipe("tube_hollow_cylinder")])):
        assert "## Example 1: rectangular part" in prompt
        assert "## Example 2: round part" in prompt
    assert "No recipe beyond the examples above." in build_prompt(SPEC)


def test_recipe_already_shown_as_example_is_not_repeated_in_generation():
    flange, tube = recipe("round_flange_bolt_circle"), recipe("tube_hollow_cylinder")

    generation = build_prompt(SPEC, recipes=[flange, tube])
    repair = build_repair_prompt(SPEC, GOOD_CODE, ["error"], recipes=[flange, tube])

    assert flange.title not in generation
    assert tube.title in generation
    assert flange.title in repair  # the repair prompt has no examples, so it keeps every recipe
    assert "Workplane.polarArray(radius, startAngle, angle, count" in generation  # its methods stay in the reference


def test_repair_prompt_without_recipes_says_so():
    assert "No recipe available" in build_repair_prompt(SPEC, GOOD_CODE, ["error"])


def test_api_error_is_fixed_in_the_cheap_static_retry():
    wrong = 'import cadquery as cq\nresult = cq.Workplane("XY").box(1, 1, 1).faces(">Z").workplane(centered=True)'
    llm = FakeLLM(fenced(wrong), fenced(GOOD_CODE))

    assert generate_cad_code(SPEC, llm=llm) == GOOD_CODE
    assert "Workplane.center(x, y)" in llm.calls[1][-1]["content"]


def test_generated_script_identical_to_a_tried_one_is_rejected():
    fixed = GOOD_CODE.replace("20.0", "21.0")
    llm = FakeLLM(fenced("# only a new comment\n" + GOOD_CODE), fenced(fixed))

    code = generate_cad_code(SPEC, llm=llm, tried=[GOOD_CODE])

    assert code == fixed
    assert "same as one that was already executed" in llm.calls[1][-1]["content"]
    assert llm.temperatures[1] > llm.temperatures[0]


def test_failed_approach_is_shown_in_a_fresh_generation():
    prompt = build_prompt(SPEC, failed_problems=["Expected 5 complete hole(s) of diameter 15, found 0."])

    assert "# A previous approach failed" in prompt
    assert prompt.index("# A previous approach failed") > prompt.index("# Specification")
    assert prompt.rstrip().endswith("Script:")


# --- Repairs with line edits ---

SCRIPT = """import cadquery as cq

thickness = 20.0
result = cq.Workplane().box(10, 10, thickness)
result = result.faces('>Z').workplane().hole(2)"""


def test_apply_edits_uses_the_original_line_numbers():
    edits = [
        LineEdit(first_line=5, last_line=5, replacement="result = result.faces('>Z').workplane().hole(3)"),
        LineEdit(first_line=3, last_line=3, replacement="thickness = 20.0\nwidth = 10.0"),  # one line becomes two
    ]

    lines = apply_edits(SCRIPT, edits).splitlines()

    assert lines[2:4] == ["thickness = 20.0", "width = 10.0"]
    assert lines[-1].endswith("hole(3)")


def test_apply_edits_cleans_copied_line_numbers_and_fences():
    edit = LineEdit(first_line=5, last_line=5, replacement="```python\n  5| result = result.faces('|Z').workplane().hole(3)\n```")

    assert apply_edits(SCRIPT, [edit]).splitlines()[-1] == "result = result.faces('|Z').workplane().hole(3)"


def test_apply_edits_deletes_lines_with_an_empty_replacement():
    assert "thickness = 20.0" not in apply_edits(SCRIPT, [LineEdit(first_line=3, last_line=3, replacement="")])


@pytest.mark.parametrize(
    ("edits", "message"),
    [
        ([], "no edits"),
        ([LineEdit(first_line=4, last_line=9, replacement="x")], "outside the script"),
        ([LineEdit(first_line=4, last_line=3, replacement="x")], "outside the script"),
        ([LineEdit(first_line=3, last_line=4, replacement="x"), LineEdit(first_line=4, last_line=5, replacement="y")], "overlap"),
    ],
)
def test_invalid_edits_are_rejected(edits, message):
    with pytest.raises(EditError, match=message):
        apply_edits(SCRIPT, edits)


def test_repair_applies_the_edits_and_returns_the_diagnosis():
    llm = FakeLLM(repair_answer((5, 5, "result = result.faces('>Z').workplane().hole(3)"), diagnosis="line 5 cuts a 2 mm hole"))

    code, diagnosis = repair_cad_code(SPEC, SCRIPT, ["Expected 1 hole of diameter 3"], llm=llm)

    assert code.splitlines()[-1].endswith("hole(3)")
    assert diagnosis == "line 5 cuts a 2 mm hole"
    assert len(llm.calls) == 1


def test_repair_retries_after_invalid_json_or_edits():
    fix = repair_answer((5, 5, "result = result.faces('>Z').workplane().hole(3)"))
    llm = FakeLLM("not json", repair_answer((7, 7, "x = 1")), fix)

    code, _ = repair_cad_code(SPEC, SCRIPT, ["problem"], llm=llm, max_attempts=3)

    assert code.endswith("hole(3)")
    assert '"diagnosis"' in llm.calls[1][-1]["content"]
    assert "outside the script" in llm.calls[2][-1]["content"]
    assert "ORIGINAL numbered script" in llm.calls[2][-1]["content"]


def test_repair_that_breaks_the_script_gets_the_static_errors_back():
    broken = repair_answer((4, 4, "result = cq.Workplane().box(10, 10, thickness, centerX=1)"))
    llm = FakeLLM(broken, repair_answer((5, 5, "result = result.faces('>Z').workplane().hole(3)")))

    repair_cad_code(SPEC, SCRIPT, ["problem"], llm=llm)

    assert "has no argument 'centerX'" in llm.calls[1][-1]["content"]


def test_repair_that_returns_a_tried_script_is_rejected():
    """The 3B model returned the previous script unchanged in every repair, wasting the attempts."""
    unchanged = repair_answer((3, 3, "thickness = 20.0  # same value"))
    llm = FakeLLM(unchanged, repair_answer((5, 5, "result = result.faces('>Z').workplane().hole(3)")))

    code, _ = repair_cad_code(SPEC, SCRIPT, ["problem"], llm=llm, tried=[SCRIPT])

    assert code.endswith("hole(3)")
    assert "same as one that was already executed" in llm.calls[1][-1]["content"]


def test_repair_raises_when_no_valid_edits_are_produced():
    with pytest.raises(CodeGenerationError):
        repair_cad_code(SPEC, SCRIPT, ["problem"], llm=FakeLLM("no", "still no"), max_attempts=2)


def test_generic_examples_in_the_prompt_pass_the_checks_and_build():
    examples = CODE_BLOCK_PATTERN.findall(GENERATE_PROMPT_PATH.read_text(encoding="utf-8"))

    assert len(examples) == 2
    for code in examples:
        assert check_code(code) == []
        namespace: dict = {}
        exec(compile(code, "example", "exec"), namespace)
        assert namespace["result"].val().isValid()
