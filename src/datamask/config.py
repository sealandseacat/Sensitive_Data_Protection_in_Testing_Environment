"""Configuration loading and schema.

Configuration is plain YAML so it is easy to read, diff and version-control.
Secrets (passwords, API keys) can be referenced via ``${ENV_VAR}`` placeholders
so you never have to commit credentials. See ``config/datamask.config.example.yaml``.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import yaml

_ENV_PATTERN = re.compile(r"\$\{([^}^{]+)\}")


def _expand_env(value: Any) -> Any:
    """Recursively expand ``${VAR}`` placeholders using environment variables."""
    if isinstance(value, str):
        def repl(match: re.Match) -> str:
            var = match.group(1)
            default = ""
            if ":" in var:  # ${VAR:default}
                var, default = var.split(":", 1)
            return os.getenv(var.strip(), default)

        return _ENV_PATTERN.sub(repl, value)
    if isinstance(value, dict):
        return {k: _expand_env(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_expand_env(v) for v in value]
    return value


@dataclass
class DatabaseConfig:
    """How to connect to the database you want to analyze/mask."""

    # Either provide a full SQLAlchemy URL ...
    url: Optional[str] = None
    # ... or the individual parts and we build the URL for you.
    dialect: Optional[str] = None  # e.g. postgresql, mysql, mssql, oracle, sqlite
    driver: Optional[str] = None   # e.g. psycopg2, pymysql, pyodbc, oracledb
    host: Optional[str] = None
    port: Optional[int] = None
    username: Optional[str] = None
    password: Optional[str] = None
    database: Optional[str] = None
    # Extra connect args passed straight to SQLAlchemy create_engine(connect_args=...)
    connect_args: dict[str, Any] = field(default_factory=dict)
    # Logical name used in history/reports. Falls back to `database` if unset.
    name: Optional[str] = None
    # Restrict analysis to these schemas (empty = all visible schemas).
    schemas: list[str] = field(default_factory=list)


@dataclass
class LLMConfig:
    """Optional LLM fallback used only when history/patterns are inconclusive."""

    enabled: bool = False
    provider: str = "openai"          # "openai" | "local"
    model: str = "gpt-4o-mini"
    api_key: Optional[str] = None
    base_url: Optional[str] = None    # OpenAI-compatible / local endpoint
    temperature: float = 0.0
    max_tokens_budget: int = 1_000_000  # hard stop to control cost
    sample_size: int = 50             # distinct values sent per column
    timeout: int = 60


@dataclass
class DetectionConfig:
    """Tuning knobs for the detection pipeline."""

    sample_size: int = 100            # rows sampled per column for pattern matching
    use_patterns: bool = True
    use_history: bool = True
    # Path to the field override file (sensitive/not-sensitive toggles).
    overrides_file: Optional[str] = None
    # Skip columns/tables by name regex (rebuilt-from-scratch, fully optional).
    skip_column_patterns: list[str] = field(default_factory=list)
    skip_table_patterns: list[str] = field(default_factory=list)


@dataclass
class HistoryConfig:
    """Where past decisions are stored so masking is reproducible/consistent."""

    enabled: bool = True
    # Any SQLAlchemy URL; defaults to a local SQLite file.
    url: str = "sqlite:///datamask_history.db"


@dataclass
class MaskingConfig:
    """ETL / masking behaviour."""

    # Per-column override (HIGHEST priority): force a specific strategy for a
    # given column. Keys may be "column", "table.column" or
    # "schema.table.column". This is where a user decides, e.g., to BLANK a long
    # `notes` field instead of randomizing it.
    #   e.g. {"notes": "blank", "public.users.bio": "redact"}
    column_strategies: dict[str, str] = field(default_factory=dict)
    # Map a detected rule name -> masking strategy name.
    # e.g. {"email": "fake_email", "full_name": "fake_name", "ssn": "format_random"}
    rule_strategies: dict[str, str] = field(default_factory=dict)
    # Default strategy when a rule has no explicit mapping.
    default_strategy: str = "format_random"
    # Deterministic seed so the same input always maps to the same output
    # (this is what keeps masking consistent across runs/tables).
    seed: Optional[str] = "datamask"
    # If True, write masked values back; if False, only produce a report/preview.
    dry_run: bool = True


@dataclass
class ValidationConfig:
    """Post-masking validation: verify the masked (target) DB against the
    original (source) DB.

    Validation answers "did masking actually work?" via three checks:
      * row counts match per table,
      * structural elements match (indexes, constraints, FKs, ...),
      * every sensitive value was really masked (row-based completeness check).
    """

    enabled: bool = False
    # Which checks to run.
    check_row_counts: bool = True
    check_schema_elements: bool = True
    check_masking_completeness: bool = True

    # -- masking-completeness tuning -----------------------------------------
    # Max distinct values pulled per column when looking for common values.
    distinct_value_limit: int = 5000
    # Max common values actually drilled into with a full-row comparison.
    max_common_values: int = 100
    # Max rows fetched per common value on each side during the row compare.
    max_rows_per_value: int = 50
    # Stop a column early once this many unmasked rows are confirmed.
    unmasked_evidence_threshold: int = 10
    # Skip the value-level test-data noise (single chars, 'test', 'n/a', ...).
    ignore_test_data: bool = True

    # Which sensitive columns to validate. If empty, validation will reuse the
    # history store / a fresh scan to find sensitive columns automatically.
    # Otherwise list "schema.table.column" entries explicitly.
    columns: list[str] = field(default_factory=list)


@dataclass
class Config:
    # The MASKED database (the one produced by `mask`). Kept as `database` for
    # backwards compatibility with scan/mask.
    database: DatabaseConfig = field(default_factory=DatabaseConfig)
    # The ORIGINAL/source database, used only by validation to compare against.
    source_database: DatabaseConfig = field(default_factory=DatabaseConfig)
    detection: DetectionConfig = field(default_factory=DetectionConfig)
    history: HistoryConfig = field(default_factory=HistoryConfig)
    llm: LLMConfig = field(default_factory=LLMConfig)
    masking: MaskingConfig = field(default_factory=MaskingConfig)
    validation: ValidationConfig = field(default_factory=ValidationConfig)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Config":
        data = _expand_env(data or {})
        return cls(
            database=DatabaseConfig(**(data.get("database") or {})),
            source_database=DatabaseConfig(**(data.get("source_database") or {})),
            detection=DetectionConfig(**(data.get("detection") or {})),
            history=HistoryConfig(**(data.get("history") or {})),
            llm=LLMConfig(**(data.get("llm") or {})),
            masking=MaskingConfig(**(data.get("masking") or {})),
            validation=ValidationConfig(**(data.get("validation") or {})),
        )


    @classmethod
    def load(cls, path: str | Path) -> "Config":
        path = Path(path)
        if not path.exists():
            raise FileNotFoundError(f"Config file not found: {path}")
        with path.open("r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}
        return cls.from_dict(data)
