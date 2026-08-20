"""Post-masking validation: verify the masked database against the original.

Three checks:
  1. row counts match (``row_count``),
  2. structural elements match (``schema_elements``),
  3. every sensitive value was really masked (``masking_completeness``).
"""

from dbmask.validation.result import (
    Status,
    ValidationIssue,
    ValidationReport,
)
from dbmask.validation.validator import Validator

__all__ = ["Status", "ValidationIssue", "ValidationReport", "Validator"]
