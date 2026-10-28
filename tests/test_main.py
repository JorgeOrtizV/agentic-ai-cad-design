"""End-to-end loop with a fake LLM, but real CadQuery execution and validation."""

import json
import logging

from fakes import FakeLLM, repair_answer

from llm_client import LLMTimeoutError
from main import run_agent

SPEC_TEXT = "Wheel spacer disc 150 mm diameter, 20 mm thick, center bore 65 mm diameter, 5 bolt holes each 15 mm diameter"

SPEC_JSON = json.dumps(
    {
        "part_type": "wheel_spacer",
        "description": "Disc with a center bore and five bolt holes",
        "units": "mm",
        "parameters": {"diameter": 150, "thickness": 20},
        "features": [
            {"type": "center_bore", "count": 1, "parameters": {"diameter": 65}},
            {"type": "through_hole", "count": 5, "parameters": {"diameter": 15}},
        ],
        "missing_parameters": ["bolt circle diameter"],
        "assumptions": [],
    }
)


def script(bolt_holes: int) -> str:
    return f"""```python
import cadquery as cq

diameter = 150.0
thickness = 20.0
bolt_circle_diameter = 114.3  # ASSUMED: common 5x114.3 pattern

result = (
    cq.Workplane("XY").circle(diameter / 2).extrude(thickness)
    .faces(">Z").workplane().hole(65.0)
    .faces(">Z").workplane().polarArray(bolt_circle_diameter / 2, 0, 360, {bolt_holes}).hole(15.0)
)
```"""


BROKEN_SCRIPT = "```python\nimport cadquery as cq\nresult = cq.Workplane().box(10, 10, 10).edges().fillet(50)\n```"


def code_of(answer: str) -> str:
    return answer.removeprefix("```python\n").removesuffix("\n```")


def bolt_count_edit(base_holes: int, new_holes: int) -> tuple[int, int, str]:
    """The line edit that changes the number of bolt holes of script(base_holes) to new_holes."""
    lines = code_of(script(base_holes)).splitlines()
    number = next(i for i, line in enumerate(lines, start=1) if "polarArray" in line)
    return (number, number, code_of(script(new_holes)).splitlines()[number - 1])


def test_succeeds_on_first_attempt(tmp_path, caplog):
    caplog.set_level(logging.DEBUG)  # what setup_logging does for the CLI

    result = run_agent(SPEC_TEXT, embedder=None, name="ws", output_dir=tmp_path, llm=FakeLLM(SPEC_JSON, script(5)))

    assert result.success
    assert result.attempts == 1  # the first candidate passed: no other candidate is generated
    assert result.model_path == result.run_dir / "final.step"
    assert (result.run_dir / "attempt_1" / "candidate_1" / "trace.json").exists()
    assert (result.run_dir / "spec.json").exists()
    assert (result.run_dir / "result.json").exists()
    assert "Attempt 1/4: generating 3 candidate scripts" in (result.run_dir / "run.log").read_text()
    recipes = json.loads((result.run_dir / "recipes.json").read_text())
    assert recipes[0]["name"] == "round_flange_bolt_circle"


def test_best_of_the_candidates_is_kept(tmp_path):
    worse = script(2).replace(".extrude(thickness)", ".extrude(thickness + 10)")  # 2 errors
    llm = FakeLLM(SPEC_JSON, BROKEN_SCRIPT, worse, script(4))  # execution error, 2 errors, 1 error

    result = run_agent(SPEC_TEXT, embedder=None, output_dir=tmp_path, max_attempts=1, llm=llm)

    assert not result.success
    assert "best attempt (1, candidate 3) with 1 error(s)" in result.message
    assert (result.run_dir / "final.py").read_text() == code_of(script(4))
    # the first candidate is the most likely script, the others are sampled freely to differ
    assert llm.temperatures[1] < llm.temperatures[2] == llm.temperatures[3]


def test_repair_edits_the_lines_named_by_the_trace(tmp_path):
    llm = FakeLLM(SPEC_JSON, script(4), repair_answer(bolt_count_edit(4, 5), diagnosis="polarArray makes 4 holes"))

    result = run_agent(SPEC_TEXT, embedder=None, output_dir=tmp_path, candidates=1, llm=llm)

    assert result.success
    assert result.attempts == 2
    repair_prompt = llm.calls[2][0]["content"]
    number = bolt_count_edit(4, 5)[0]
    assert f'{number:>3}|     .faces(">Z").workplane().polarArray(bolt_circle_diameter / 2, 0, 360, 4).hole(15.0)' in repair_prompt
    assert f"Holes of diameter 15: line {number}" in repair_prompt and "1 more are needed" in repair_prompt
    log = json.loads((result.run_dir / "attempt_2" / "log.json").read_text())
    assert log["diagnosis"] == "polarArray makes 4 holes"
    assert log["validation"]["passed"]


def test_repairs_an_execution_error(tmp_path):
    llm = FakeLLM(SPEC_JSON, BROKEN_SCRIPT, repair_answer((2, 2, code_of(script(5)).split("\n", 1)[1])))

    result = run_agent(SPEC_TEXT, embedder=None, output_dir=tmp_path, candidates=1, llm=llm)

    assert result.success
    assert "StdFail_NotDone" in llm.calls[2][0]["content"]


def test_repair_starts_from_the_best_part_when_a_repair_makes_it_worse(tmp_path):
    # attempt 2 turns 4 bolt holes into a thicker disc (2 errors): attempt 3 must repair attempt 1 again
    thicker = (8, 8, code_of(script(4)).splitlines()[7].replace(".extrude(thickness)", ".extrude(thickness + 10)"))
    llm = FakeLLM(SPEC_JSON, script(4), repair_answer(thicker), repair_answer(bolt_count_edit(4, 5)))

    result = run_agent(SPEC_TEXT, embedder=None, output_dir=tmp_path, candidates=1, llm=llm)

    assert result.success
    assert result.attempts == 3
    third_prompt = llm.calls[3][0]["content"]
    assert "extrude(thickness + 10)" not in third_prompt
    assert "found 4" in third_prompt


def test_stalled_repairs_start_a_fresh_round_of_candidates(tmp_path):
    # Two repairs in a row that do not reduce the errors (4 -> 2 -> 1 bolt holes), then a new approach that works
    llm = FakeLLM(
        SPEC_JSON,
        script(4),
        script(3),
        repair_answer(bolt_count_edit(4, 2)),
        repair_answer(bolt_count_edit(2, 1)),
        script(5),
    )

    result = run_agent(SPEC_TEXT, embedder=None, output_dir=tmp_path, candidates=2, max_attempts=4, llm=llm)

    assert result.success
    assert result.attempts == 4
    fresh_prompt = llm.calls[5][0]["content"]
    assert "# A previous approach failed" in fresh_prompt
    assert "found 1" in fresh_prompt  # the problems of the last script it gave up on
    assert (result.run_dir / "attempt_4" / "candidate_1" / "part.py").exists()


def test_require_complete_stops_before_generating(tmp_path):
    llm = FakeLLM(SPEC_JSON)

    result = run_agent(SPEC_TEXT, embedder=None, output_dir=tmp_path, require_complete=True, llm=llm)

    assert not result.success
    assert result.attempts == 0
    assert "bolt circle diameter" in result.message


def test_llm_timeout_counts_as_a_failed_attempt(tmp_path):
    responses = iter([SPEC_JSON, LLMTimeoutError("too slow"), script(5)])

    def llm(**kwargs):
        response = next(responses)
        if isinstance(response, Exception):
            raise response
        return response

    result = run_agent(SPEC_TEXT, embedder=None, output_dir=tmp_path, candidates=1, llm=llm)

    assert result.success
    assert result.attempts == 2
    log = json.loads((result.run_dir / "attempt_1" / "candidate_1" / "log.json").read_text())
    assert "too slow" in log["generation_error"]


def test_recipes_and_api_reference_reach_the_prompt(tmp_path):
    llm = FakeLLM(SPEC_JSON, script(5))

    result = run_agent(SPEC_TEXT, embedder=None, output_dir=tmp_path, llm=llm)

    prompt = llm.calls[1][0]["content"]
    best, second = json.loads((result.run_dir / "recipes.json").read_text())
    assert best["name"] == "round_flange_bolt_circle"
    assert "## Example 2: round part" in prompt
    assert best["title"] not in prompt  # already shown as the round example, not repeated
    assert second["title"] in prompt
    assert "Workplane.workplane(offset=0.0" in prompt


def test_runtime_api_error_feedback_includes_the_real_signature(tmp_path):
    # Through a list the checker cannot follow the chain, so the wrong argument only fails at run time
    hidden = (
        "```python\nimport cadquery as cq\nparts = [cq.Workplane().box(10, 10, 10)]\nresult = parts[0].faces('>Z').workplane(centerX=1)\n```"
    )
    llm = FakeLLM(SPEC_JSON, hidden, repair_answer((1, 3, code_of(script(5)))))

    result = run_agent(SPEC_TEXT, embedder=None, output_dir=tmp_path, candidates=1, llm=llm)

    assert result.success
    repair_prompt = llm.calls[2][0]["content"]
    assert "unexpected keyword argument 'centerX'" in repair_prompt
    assert "Workplane.center(x, y)" in repair_prompt
