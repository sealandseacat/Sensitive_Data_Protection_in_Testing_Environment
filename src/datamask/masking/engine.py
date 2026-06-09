"""ETL / masking engine — applies strategies to real rows.

Given the detections from the pipeline and the masking config, the engine:
  * resolves each sensitive column to a concrete strategy,
  * masks rows (optionally in batches), preserving format/consistency,
  * either previews the changes (``dry_run``) or writes them back.

Non-sensitive columns are passed through untouched.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Optional

from datamask.config import MaskingConfig
from datamask.connectors.base import Connector
from datamask.detection.result import Decision
from datamask.masking.rules import (
    DEFAULT_RULE_STRATEGIES,
    STRATEGIES,
    MaskContext,
    get_strategy,
)


@dataclass
class ColumnPlan:
    """How a single column will be masked."""

    schema: str
    table: str
    column: str
    rule: Optional[str]
    strategy_name: str


@dataclass
class TableMaskResult:
    schema: str
    table: str
    rows_scanned: int = 0
    rows_written: int = 0
    columns: list[ColumnPlan] = field(default_factory=list)
    preview: list[dict] = field(default_factory=list)


class MaskingEngine:
    def __init__(self, config: MaskingConfig):
        self.config = config
        # Normalize per-column overrides to lowercase keys for matching.
        self._column_strategies = {
            k.lower(): v for k, v in (config.column_strategies or {}).items()
        }

    # -- planning -------------------------------------------------------------
    def resolve_strategy(self, decision: Decision) -> str:
        """Pick the strategy name for a sensitive column.

        Priority (first match wins):
          1. ``column_strategies`` — an explicit per-column choice the user made
             (e.g. blank a long ``notes`` field). Matched as
             ``schema.table.column`` -> ``table.column`` -> ``column``.
          2. ``rule_strategies`` — the user's mapping for the detected rule
             (e.g. ``email`` -> ``fake_email``).
          3. Built-in default strategy for that rule.
          4. The rule name itself, if it happens to be a valid strategy
             (lets an override set ``rule: blank`` and have it just work).
          5. ``default_strategy`` — the catch-all.
        """
        # 1) per-column user choice (most specific)
        for key in (
            f"{decision.schema}.{decision.table}.{decision.column}",
            f"{decision.table}.{decision.column}",
            decision.column,
        ):
            if key.lower() in self._column_strategies:
                return self._column_strategies[key.lower()]

        rule = (decision.rule or "").lower()
        # 2) user mapping for the detected rule
        if rule in self.config.rule_strategies:
            return self.config.rule_strategies[rule]
        # 3) built-in default for the detected rule
        if rule in DEFAULT_RULE_STRATEGIES:
            return DEFAULT_RULE_STRATEGIES[rule]
        # 4) the rule may itself name a strategy (e.g. "blank", "null")
        if rule in STRATEGIES:
            return rule
        # 5) catch-all
        return self.config.default_strategy

    def plan_table(self, decisions: Iterable[Decision]) -> list[ColumnPlan]:
        plans: list[ColumnPlan] = []
        for d in decisions:
            if not d.is_sensitive:
                continue
            plans.append(
                ColumnPlan(
                    schema=d.schema,
                    table=d.table,
                    column=d.column,
                    rule=d.rule,
                    strategy_name=self.resolve_strategy(d),
                )
            )
        return plans

    # -- value masking --------------------------------------------------------
    def mask_value(self, value, plan: ColumnPlan):
        strategy = get_strategy(plan.strategy_name)
        ctx = MaskContext(column=plan.column, rule=plan.rule, seed=self.config.seed)
        return strategy(value, ctx)

    def mask_row(self, row: dict, plans: list[ColumnPlan]) -> dict:
        masked = dict(row)
        for plan in plans:
            if plan.column in masked:
                masked[plan.column] = self.mask_value(masked[plan.column], plan)
        return masked

    # -- table-level ETL ------------------------------------------------------
    def mask_table(
        self,
        connector: Connector,
        schema: str,
        table: str,
        decisions: Iterable[Decision],
        key_columns: Optional[list[str]] = None,
        batch_size: int = 1000,
        preview_limit: int = 10,
    ) -> TableMaskResult:
        plans = self.plan_table(decisions)
        result = TableMaskResult(schema=schema, table=table, columns=plans)
        if not plans:
            return result  # nothing sensitive here

        keys = key_columns or connector.primary_key_columns(schema, table)

        batch: list[dict] = []
        for row in connector.iter_rows(schema, table, batch_size=batch_size):
            result.rows_scanned += 1
            masked = self.mask_row(row, plans)

            if len(result.preview) < preview_limit:
                result.preview.append(
                    {
                        "before": {p.column: row.get(p.column) for p in plans},
                        "after": {p.column: masked.get(p.column) for p in plans},
                    }
                )

            if not self.config.dry_run:
                batch.append(masked)
                if len(batch) >= batch_size:
                    result.rows_written += connector.update_rows(schema, table, keys, batch)
                    batch.clear()

        if not self.config.dry_run and batch:
            result.rows_written += connector.update_rows(schema, table, keys, batch)

        return result
