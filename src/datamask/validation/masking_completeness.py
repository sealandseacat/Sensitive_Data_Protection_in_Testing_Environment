"""Masking-completeness validation (check #3) — the row-based approach.

This is the clever check. A naive "are there common values?" test produces false
positives: with dictionary-based masking, a real value can be replaced by another
real value that also exists in the data. Example: the dictionary contains both
``Tesla`` and ``Apple`` and so does the data; after masking, ``Tesla`` -> ``Apple``
and ``Apple`` -> ``Tesla``. The *column* still contains "Tesla" and "Apple", so a
column-level check would wrongly scream "unmasked!". But the data **is** masked.

The fix (from the original script): operate at the **row** level.

  1. INTERSECT the sensitive column between source and target to get the set of
     **common values** (done in Python so it works across different databases).
  2. Drop obvious test-data values (single chars, ``test``, ``n/a`` ...).
  3. For each remaining common value, fetch the **whole rows** that contain it
     from both source and target.
  4. If an **entire row** (all comparable columns) is identical on both sides,
     that row truly bypassed masking — a genuine transformation failure.
     If only the single value coincides but the rest of the row differs, the row
     was masked correctly (the Tesla/Apple swap case) and is fine.

Doing the comparison in Python keeps it database-agnostic: source and target can
even be different engines (e.g. Oracle -> PostgreSQL).
"""
from __future__ import annotations

from datamask.config import ValidationConfig
from datamask.connectors.base import Connector
from datamask.validation.result import Status, ValidationIssue
from datamask.validation.testdata import is_test_data

# Column types we cannot (or should not) compare directly.
_NONCOMPARABLE_TYPES = ("LOB", "CLOB", "BLOB", "NCLOB", "LONG", "BYTEA", "IMAGE", "XML")


class MaskingCompletenessValidator:
    check_name = "masking_completeness"

    def __init__(self, config: ValidationConfig):
        self.config = config

    def _comparable_columns(self, source: Connector, target: Connector, schema: str, table: str) -> list[str]:
        """Columns present in BOTH source and target, excluding LOB-like types."""
        try:
            src_cols = source.schema_elements(schema, table)["columns"]
            tgt_cols = target.schema_elements(schema, table)["columns"]
        except Exception:  # noqa: BLE001 - fall back to a plain column list
            common = [c for c in source.list_columns(schema, table)
                      if c in set(target.list_columns(schema, table))]
            return common

        common = []
        for name, type_str in src_cols.items():
            if name not in tgt_cols:
                continue
            upper = str(type_str).upper()
            if any(t in upper for t in _NONCOMPARABLE_TYPES):
                continue
            common.append(name)
        return common

    def validate_column(
        self,
        source: Connector,
        target: Connector,
        schema: str,
        table: str,
        column: str,
    ) -> ValidationIssue:
        cfg = self.config
        try:
            # 1) Common values (INTERSECT done in Python).
            src_vals = set(source.distinct_values(schema, table, column, cfg.distinct_value_limit))
            tgt_vals = set(target.distinct_values(schema, table, column, cfg.distinct_value_limit))
            common = {v for v in (src_vals & tgt_vals) if v is not None}

            # 2) Drop test-data noise.
            if cfg.ignore_test_data:
                common = {v for v in common if not is_test_data(v)}

            if not common:
                return ValidationIssue(
                    check=self.check_name,
                    status=Status.PASS,
                    schema=schema, table=table, column=column,
                    message="No common values between source and target — fully masked.",
                )

            comparable = self._comparable_columns(source, target, schema, table)
            if len(comparable) < 2:
                return ValidationIssue(
                    check=self.check_name,
                    status=Status.WARNING,
                    schema=schema, table=table, column=column,
                    message=(
                        f"{len(common)} common values, but table has <2 comparable "
                        "columns; cannot run a reliable row-level check."
                    ),
                    detail={"common_values": len(common)},
                )

            # 3) + 4) Row-level comparison for each common value.
            unmasked_rows = 0
            samples: list = []
            checked = 0
            for value in list(common)[: cfg.max_common_values]:
                checked += 1
                src_rows = {
                    tuple(r) for r in source.fetch_rows_where(
                        schema, table, comparable, column, value, cfg.max_rows_per_value
                    )
                }
                if not src_rows:
                    continue
                tgt_rows = {
                    tuple(r) for r in target.fetch_rows_where(
                        schema, table, comparable, column, value, cfg.max_rows_per_value
                    )
                }
                identical = src_rows & tgt_rows
                if identical:
                    unmasked_rows += len(identical)
                    if len(samples) < 5:
                        samples.append(value)
                    if unmasked_rows >= cfg.unmasked_evidence_threshold:
                        break

            if unmasked_rows > 0:
                return ValidationIssue(
                    check=self.check_name,
                    status=Status.FAIL,
                    schema=schema, table=table, column=column,
                    message=(
                        f"Found {unmasked_rows}+ identical row(s) across source and "
                        "target — these rows bypassed masking (transformation failure)."
                    ),
                    detail={
                        "unmasked_rows": unmasked_rows,
                        "sample_values": [str(s)[:50] for s in samples],
                        "common_values_checked": checked,
                        "comparable_columns": len(comparable),
                    },
                )

            return ValidationIssue(
                check=self.check_name,
                status=Status.PASS,
                schema=schema, table=table, column=column,
                message=(
                    f"{len(common)} common value(s) exist, but no identical rows — "
                    "values coincide yet rows are masked (expected, e.g. dictionary swaps)."
                ),
                detail={"common_values": len(common), "common_values_checked": checked},
            )

        except Exception as exc:  # noqa: BLE001
            return ValidationIssue(
                check=self.check_name,
                status=Status.ERROR,
                schema=schema, table=table, column=column,
                message=f"Masking-completeness check failed: {exc}",
            )
