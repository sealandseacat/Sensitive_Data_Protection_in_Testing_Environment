"""Validation result types shared by all validators."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional


class Status(str, Enum):
    """Outcome of a single validation check."""

    PASS = "pass"        # everything matched / fully masked
    FAIL = "fail"        # a real problem (counts differ, unmasked rows, ...)
    WARNING = "warning"  # worth a look but not necessarily wrong
    SKIPPED = "skipped"  # not run (disabled, too large, unsupported, ...)
    ERROR = "error"      # the check itself blew up


@dataclass
class ValidationIssue:
    """One finding produced by a validator."""

    check: str                 # "row_count" | "schema_elements" | "masking_completeness"
    status: Status
    schema: str = ""
    table: str = ""
    column: str = ""
    message: str = ""
    detail: dict[str, Any] = field(default_factory=dict)

    @property
    def location(self) -> str:
        parts = [p for p in (self.schema, self.table, self.column) if p]
        return ".".join(parts) if parts else "(database)"

    def to_dict(self) -> dict[str, Any]:
        return {
            "check": self.check,
            "status": self.status.value,
            "location": self.location,
            "message": self.message,
            "detail": self.detail,
        }


@dataclass
class ValidationReport:
    """Aggregated results from every validator that ran."""

    issues: list[ValidationIssue] = field(default_factory=list)

    def add(self, issue: ValidationIssue) -> None:
        self.issues.append(issue)

    def extend(self, issues: list[ValidationIssue]) -> None:
        self.issues.extend(issues)

    def by_status(self, status: Status) -> list[ValidationIssue]:
        return [i for i in self.issues if i.status == status]

    @property
    def failures(self) -> list[ValidationIssue]:
        return self.by_status(Status.FAIL)

    @property
    def passed(self) -> bool:
        """True when nothing failed or errored."""
        return not any(i.status in (Status.FAIL, Status.ERROR) for i in self.issues)

    def summary(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for issue in self.issues:
            counts[issue.status.value] = counts.get(issue.status.value, 0) + 1
        return counts
