"""Pydantic models for SCF validation and lowering responses."""
from typing import Optional

from pydantic import BaseModel, Field

from openeo_fastapi.api.types import Error
from openeo_fastapi.api.models import ValidationIssue


class SCFValidationResponse(BaseModel):
    errors: list[Error] = Field(default_factory=list)
    warnings: list[ValidationIssue] = Field(default_factory=list)
    required_decisions: list[ValidationIssue] = Field(default_factory=list)
    scf_hash: Optional[str] = None


class SCFPlanValidationResponse(BaseModel):
    errors: list[Error] = Field(default_factory=list)
    warnings: list[ValidationIssue] = Field(default_factory=list)
    required_decisions: list[ValidationIssue] = Field(default_factory=list)
    scf_hash: Optional[str] = None
    typed_plan: Optional[dict] = None


class SCFLoweringResponse(BaseModel):
    errors: list[Error] = Field(default_factory=list)
    warnings: list[ValidationIssue] = Field(default_factory=list)
    required_decisions: list[ValidationIssue] = Field(default_factory=list)
    scf_hash: Optional[str] = None
    pg_hash: Optional[str] = None
    process_graph: Optional[dict] = None


class SCFCompileResponse(SCFLoweringResponse):
    pass
