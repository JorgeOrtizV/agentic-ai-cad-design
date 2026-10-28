"""
Define generic schemas for part and report so they can be used as structure for each specific case
"""

from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field, computed_field
from pydantic.json_schema import SkipJsonSchema


class Feature(BaseModel):
    """A geometric feature applied to the base body (hole, bore, fillet, slot, ...)."""

    type: str = Field(description="Feature kind in snake_case, e.g. 'through_hole', 'center_bore', 'fillet', 'slot'")
    count: int = Field(default=1, ge=1, description="How many instances of this feature exist")
    parameters: dict[str, float] = Field(
        default_factory=dict,
        description="Numeric dimensions of the feature in snake_case, e.g. {'diameter': 6}",
    )
    location: str | None = Field(
        default=None,
        description="Where the feature is placed, quoted or paraphrased from the spec, e.g. 'two in each leg'",
    )


class PartSpec(BaseModel):
    part_type: str = Field(description="Short snake_case name of the part, e.g. 'l_bracket', 'wheel_spacer'")
    description: str = Field(description="One sentence summary of the part")
    units: Literal["mm", "in"] = "mm"
    parameters: dict[str, float] = Field(
        description="Numeric dimensions of the base body in snake_case, e.g. {'thickness': 5, 'width': 40}",
    )
    features: list[Feature] = Field(default_factory=list)
    missing_parameters: list[str] = Field(
        default_factory=list,
        description="Dimensions required to build the part that the spec does not give",
    )
    assumptions: list[str] = Field(
        default_factory=list,
        description="Every value or interpretation not stated explicitly in the spec, with its justification",
    )
    # Filled by the parser after LLM output, never by the LLM itself
    warnings: SkipJsonSchema[list[str]] = Field(default_factory=list)

    @property
    def is_complete(self) -> bool:
        return not self.missing_parameters


class ExecutionResult(BaseModel):
    success: bool
    attempt: int
    candidate: int | None = Field(default=None, description="Which of the scripts generated in parallel in this attempt")
    code_path: Path
    model_path: Path | None = None
    error: str | None = None
    stdout: str = ""
    duration_s: float = 0.0


class Check(BaseModel):
    """One measurable comparison between the generated part and the spec."""

    name: str
    passed: bool
    expected: Any = None
    actual: Any = None
    severity: Literal["error", "warning"] = "error"
    message: str = Field(description="Explanation shown to the LLM when the check fails")


class ValidationReport(BaseModel):
    valid_solid: bool
    solid_count: int
    volume: float
    bounding_box: tuple[float, float, float]
    checks: list[Check] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    unverified: list[str] = Field(default_factory=list, description="Spec items the validator cannot measure")

    @computed_field  # type: ignore[prop-decorator]
    @property
    def passed(self) -> bool:
        return not self.errors


class AgentResult(BaseModel):
    success: bool
    attempts: int
    run_dir: Path
    message: str
    model_path: Path | None = None
    code_path: Path | None = None
    validation: ValidationReport | None = None


class LineEdit(BaseModel):
    """Replace lines first_line..last_line (1-based, inclusive) of a script."""

    first_line: int = Field(ge=1)
    last_line: int = Field(ge=1)
    replacement: str = Field(description="New code for these lines; an empty string deletes them")


class RepairPlan(BaseModel):
    """The LLM's answer to a repair prompt: first the diagnosis, then the line edits that fix it."""

    diagnosis: str = Field(description="For each problem: the line that causes it and why")
    edits: list[LineEdit]
