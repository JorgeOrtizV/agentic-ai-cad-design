"""The build trace on small versions of real failed attempts: each problem must be blamed on the line that causes it."""

import cadquery as cq
import pytest

from build_trace import format_trace, locate_problems, run_traced
from schemas import PartSpec
from validator import validate_model

PLATE = PartSpec(
    part_type="plate",
    description="plate with holes",
    parameters={"length": 80, "width": 40, "thickness": 5},
    features=[{"type": "through_hole", "count": 2, "parameters": {"diameter": 6}}],
)
DISC = PartSpec(
    part_type="disc",
    description="disc with bolt holes",
    parameters={"diameter": 150, "thickness": 20},
    features=[{"type": "through_hole", "count": 5, "parameters": {"diameter": 15}}],
)


@pytest.fixture
def diagnose(tmp_path):
    """Run a script with tracing, validate its part and return (causes, trace text)."""

    def _diagnose(code: str, spec: PartSpec) -> tuple[list[str], str]:
        namespace: dict = {}
        records = run_traced(code, "part.py", namespace)
        result = namespace["result"]
        path = tmp_path / "part.step"
        cq.exporters.export(result.val() if isinstance(result, cq.Workplane) else result, str(path))
        report = validate_model(path, spec, code)
        assert not report.passed
        return locate_problems(report, records, code), format_trace(records, code)

    return _diagnose


def test_tracing_does_not_change_the_part():
    code = "import cadquery as cq\nresult = cq.Workplane().box(80, 40, 5).faces('>Z').workplane().pushPoints([(-20, 0), (20, 0)]).hole(6)"
    plain: dict = {}
    traced: dict = {}

    exec(compile(code, "part.py", "exec"), plain)
    records = run_traced(code, "part.py", traced)

    assert traced["result"].val().Volume() == pytest.approx(plain["result"].val().Volume())
    assert [record["method"] for record in records] == ["box", "hole"]


def test_holes_filled_by_a_union_are_blamed_on_the_union(diagnose):
    code = """import cadquery as cq
holes = cq.Workplane().box(80, 40, 5).faces(">Z").workplane().pushPoints([(-20, 0), (20, 0)]).hole(6)
cover = cq.Workplane().box(80, 40, 5)
result = holes.union(cover)"""

    causes, _ = diagnose(code, PLATE)

    assert any(cause.startswith("line 4 `result = holes.union(cover)`: 2 complete hole(s) of diameter 6 disappeared") for cause in causes)


def test_holes_breaking_out_of_a_small_body_are_blamed_on_the_hole(diagnose):
    """bracket_20261028_002715: 6 mm holes cut in a 5 mm wide leg."""
    code = """import cadquery as cq
plate = cq.Workplane().box(80, 40, 5)
leg = cq.Workplane().box(20, 5, 5).translate((60, 0, 0))
leg = leg.faces(">Z").workplane(centerOption="CenterOfBoundBox").hole(6)
result = plate.union(leg)"""

    causes, _ = diagnose(code, PLATE)

    assert any(cause.startswith("line 4") and "break out" in cause and "20 x 5 x 5" in cause for cause in causes)


def test_extra_extrude_is_blamed_for_the_wrong_size(diagnose):
    """wheel_spacer_20261028_005439: an extra extrude made the disc 30 mm thick."""
    code = """import cadquery as cq
result = (
    cq.Workplane("XY")
    .circle(75)
    .extrude(20)
    .faces(">Z")
    .workplane()
    .circle(32.5)
    .extrude(10)
    .faces(">Z")
    .workplane()
    .polarArray(57, 0, 360, 5)
    .hole(15)
)"""

    causes, trace = diagnose(code, DISC)

    assert "line 9 `.extrude(10)`: changes the size from 150 x 150 x 20 to 150 x 150 x 30" in "\n".join(causes)
    assert "- line 5 `.extrude(20)` -> size 150 x 150 x 20" in trace
    assert "- line 9 `.extrude(10)` -> size 150 x 150 x 30" in trace


def test_cut_that_removes_nothing_and_union_that_adds_nothing_are_reported(diagnose):
    """bracket_20261028_004247: holes placed outside the body, and a second leg lying inside the first."""
    code = """import cadquery as cq
leg_1 = cq.Workplane().box(80, 40, 5).faces(">Z").workplane().pushPoints([(100, 100)]).hole(6)
leg_2 = cq.Workplane().box(40, 40, 5)
result = leg_1.union(leg_2)"""

    causes, trace = diagnose(code, PLATE)

    assert any(cause.startswith("line 2") and "removes no material" in cause for cause in causes)
    assert any(cause.startswith("line 4") and "adds no material" in cause and "(40 x 40 x 5)" in cause for cause in causes)
    assert "No line of the script makes a complete hole of diameter 6." in causes
    assert "-> no change to the part" in trace


def test_too_many_holes_names_the_lines_that_make_them(diagnose):
    code = """import cadquery as cq
result = cq.Workplane().box(80, 40, 5).faces(">Z").workplane().pushPoints([(-20, 0), (20, 0)]).hole(6)
result = result.faces(">Z").workplane().pushPoints([(0, 10)]).hole(6)"""

    causes, _ = diagnose(code, PLATE)

    assert any("line 2" in cause and "makes 2" in cause and "line 3" in cause and "1 too many" in cause for cause in causes)


def test_separate_solids_are_blamed_on_the_line_that_splits_the_part(diagnose):
    code = """import cadquery as cq
a = cq.Workplane().box(80, 40, 5).faces(">Z").workplane().pushPoints([(-20, 0), (20, 0)]).hole(6)
result = a.union(cq.Workplane().box(10, 10, 5).translate((200, 0, 0)))"""

    causes, _ = diagnose(code, PLATE)

    assert any(cause.startswith("line 3") and "2 separate solids" in cause for cause in causes)


def test_trace_is_kept_when_the_script_fails():
    code = "import cadquery as cq\nbody = cq.Workplane().box(10, 10, 10)\nresult = body.edges().fillet(50)"

    with pytest.raises(Exception) as error:
        run_traced(code, "part.py", {})

    assert [record["method"] for record in error.value.trace_records] == ["box"]
