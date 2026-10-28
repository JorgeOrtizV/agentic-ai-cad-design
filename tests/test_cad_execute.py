"""These tests run real CadQuery in a subprocess, so each takes a few seconds."""

import json

import cadquery as cq

from cad_execute import execute_cad_code

WHEEL_SPACER_CODE = """import cadquery as cq

diameter = 150.0
thickness = 20.0
bore_diameter = 65.0
bolt_hole_diameter = 15.0
bolt_hole_count = 5
bolt_circle_diameter = 114.3  # ASSUMED: common 5x114.3 automotive pattern

result = (
    cq.Workplane("XY")
    .circle(diameter / 2)
    .extrude(thickness)
    .faces(">Z")
    .workplane()
    .hole(bore_diameter)
    .faces(">Z")
    .workplane()
    .polarArray(bolt_circle_diameter / 2, 0, 360, bolt_hole_count)
    .hole(bolt_hole_diameter)
)
"""


def test_valid_script_exports_step(tmp_path):
    execution = execute_cad_code(WHEEL_SPACER_CODE, attempt=1, run_dir=tmp_path)

    assert execution.success, execution.error
    assert execution.model_path == tmp_path / "attempt_1" / "part.step"
    assert execution.code_path.read_text() == WHEEL_SPACER_CODE

    shape = cq.importers.importStep(str(execution.model_path)).val()
    assert len(shape.Solids()) == 1
    assert abs(shape.BoundingBox().xlen - 150) < 1e-3


def test_exception_reports_line_in_generated_script(tmp_path):
    code = "import cadquery as cq\n\nresult = cq.Workplane().box(10, 10, 10)\nresult = result.edges().fillet(50)\n"

    execution = execute_cad_code(code, attempt=1, run_dir=tmp_path)

    assert not execution.success
    assert 'File "part.py", line 4' in execution.error
    assert "fillet(50)" in execution.error
    assert str(tmp_path) not in execution.error


def test_2d_only_result_is_rejected(tmp_path):
    code = "import cadquery as cq\nresult = cq.Workplane().circle(10)\n"

    execution = execute_cad_code(code, attempt=1, run_dir=tmp_path)

    assert not execution.success
    assert "no solid" in execution.error


def test_missing_result_is_rejected(tmp_path):
    execution = execute_cad_code("import cadquery as cq\npart = cq.Workplane().box(1, 1, 1)\n", attempt=1, run_dir=tmp_path)

    assert not execution.success
    assert "`result`" in execution.error


def test_timeout_is_reported(tmp_path):
    execution = execute_cad_code("while True:\n    pass\n", attempt=1, run_dir=tmp_path, timeout_s=2)

    assert not execution.success
    assert "timed out" in execution.error


def test_attempts_are_kept_side_by_side(tmp_path):
    execute_cad_code("raise ValueError('first try')\n", attempt=1, run_dir=tmp_path)
    second = execute_cad_code(WHEEL_SPACER_CODE, attempt=2, run_dir=tmp_path)

    assert second.success
    assert (tmp_path / "attempt_1" / "part.py").exists()
    assert not (tmp_path / "attempt_1" / "part.step").exists()


def test_trace_is_written_next_to_the_part(tmp_path):
    execution = execute_cad_code(WHEEL_SPACER_CODE, attempt=1, run_dir=tmp_path)

    records = json.loads((tmp_path / "attempt_1" / "trace.json").read_text())
    assert [record["method"] for record in records] == ["extrude", "hole", "hole"]
    assert records[-1]["after"]["holes"] == {"15": 5, "65": 1}
    assert execution.success


def test_trace_is_written_when_the_script_fails(tmp_path):
    code = "import cadquery as cq\nbody = cq.Workplane().box(10, 10, 10)\nresult = body.edges().fillet(50)\n"

    execute_cad_code(code, attempt=1, run_dir=tmp_path)

    records = json.loads((tmp_path / "attempt_1" / "trace.json").read_text())
    assert [record["method"] for record in records] == ["box"]


def test_candidates_get_their_own_folder(tmp_path):
    execution = execute_cad_code(WHEEL_SPACER_CODE, attempt=1, run_dir=tmp_path, candidate=2)

    assert execution.candidate == 2
    assert execution.model_path == tmp_path / "attempt_1" / "candidate_2" / "part.step"
