"""Manual field overrides — the "toggle which fields are sensitive" feature.

This is requirement #4 (not present in the original script). A YAML file lets a
human force a column to be treated as sensitive (with a specific rule) or
explicitly mark it as safe, overriding every automatic layer. Overrides are
matched in priority order:

  1. exact ``schema.table.column``
  2. ``table.column``
  3. ``column`` (applies everywhere with that column name)
  4. glob/regex column-name patterns

See ``config/datamask.fields.example.yaml`` for the file format.
"""
from __future__ import annotations

import fnmatch
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import yaml

from datamask.detection.result import Decision, Sensitivity


@dataclass
class OverrideEntry:
    sensitive: bool
    rule: Optional[str] = None
    note: str = ""


class FieldOverrides:
    """Loads and applies user-defined sensitivity toggles."""

    def __init__(self) -> None:
        self.exact: dict[str, OverrideEntry] = {}
        self.table_column: dict[str, OverrideEntry] = {}
        self.column: dict[str, OverrideEntry] = {}
        self.patterns: list[tuple[str, OverrideEntry]] = []

    # -- loading --------------------------------------------------------------
    @classmethod
    def load(cls, path: Optional[str | Path]) -> "FieldOverrides":
        obj = cls()
        if not path:
            return obj
        path = Path(path)
        if not path.exists():
            raise FileNotFoundError(f"Overrides file not found: {path}")
        with path.open("r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}
        obj._ingest(data)
        return obj

    def _ingest(self, data: dict) -> None:
        for raw in data.get("sensitive", []) or []:
            self._add(raw, sensitive=True)
        for raw in data.get("not_sensitive", []) or []:
            self._add(raw, sensitive=False)

    def _add(self, raw, *, sensitive: bool) -> None:
        if isinstance(raw, str):
            target, entry = raw, OverrideEntry(sensitive=sensitive)
        else:
            target = raw.get("match") or raw.get("column") or raw.get("name")
            entry = OverrideEntry(
                sensitive=sensitive,
                rule=raw.get("rule"),
                note=raw.get("note", ""),
            )
        if not target:
            return
        target = str(target).lower()
        if target.startswith("pattern:"):
            self.patterns.append((target[len("pattern:"):], entry))
        elif target.count(".") >= 2:
            self.exact[target] = entry
        elif target.count(".") == 1:
            self.table_column[target] = entry
        else:
            self.column[target] = entry

    # -- lookup ---------------------------------------------------------------
    def lookup(self, schema: str, table: str, column: str) -> Optional[OverrideEntry]:
        s, t, c = schema.lower(), table.lower(), column.lower()
        for key, store in (
            (f"{s}.{t}.{c}", self.exact),
            (f"{t}.{c}", self.table_column),
            (c, self.column),
        ):
            if key in store:
                return store[key]
        for pattern, entry in self.patterns:
            if fnmatch.fnmatch(c, pattern.lower()):
                return entry
        return None

    def decide(self, database: str, schema: str, table: str, column: str) -> Optional[Decision]:
        entry = self.lookup(schema, table, column)
        if entry is None:
            return None
        return Decision(
            database=database,
            schema=schema,
            table=table,
            column=column,
            sensitivity=Sensitivity.SENSITIVE if entry.sensitive else Sensitivity.NOT_SENSITIVE,
            rule=entry.rule if entry.sensitive else None,
            source="override",
            confidence=1.0,
            detail=entry.note or "Manual field override",
        )
