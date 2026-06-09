"""The detection pipeline — orchestrates all layers in priority order.

For each column we try, in order:

  1. **Field overrides** (manual human toggle) — authoritative, always wins.
  2. **History** — reuse a prior decision so results stay consistent.
  3. **Pattern matching** — fast, free, deterministic value heuristics.
  4. **LLM** — only when everything above is inconclusive (and only if enabled).

Whatever layer decides, the result is written back to history so the next run
is faster and consistent. A token budget caps LLM spend.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

from datamask.config import Config
from datamask.connectors.base import Connector
from datamask.detection.overrides import FieldOverrides
from datamask.detection.patterns import PatternMatcher
from datamask.detection.result import Decision, Sensitivity
from datamask.history.store import HistoryStore
from datamask.llm.base import LLMProvider


class TokenBudgetExceeded(Exception):
    """Raised when LLM usage would exceed the configured budget."""


@dataclass
class PipelineStats:
    total: int = 0
    by_source: dict[str, int] = None  # type: ignore[assignment]
    sensitive: int = 0
    tokens: int = 0

    def __post_init__(self):
        if self.by_source is None:
            self.by_source = {}

    def record(self, decision: Decision) -> None:
        self.total += 1
        self.by_source[decision.source] = self.by_source.get(decision.source, 0) + 1
        if decision.is_sensitive:
            self.sensitive += 1
        self.tokens += decision.token_usage


class DetectionPipeline:
    def __init__(
        self,
        config: Config,
        history: Optional[HistoryStore] = None,
        overrides: Optional[FieldOverrides] = None,
        patterns: Optional[PatternMatcher] = None,
        llm: Optional[LLMProvider] = None,
    ):
        self.config = config
        self.history = history
        self.overrides = overrides or FieldOverrides()
        self.patterns = patterns or PatternMatcher()
        self.llm = llm
        self.stats = PipelineStats()
        self._skip_cols = [re.compile(p, re.IGNORECASE) for p in config.detection.skip_column_patterns]
        self._skip_tables = [re.compile(p, re.IGNORECASE) for p in config.detection.skip_table_patterns]

    # -- name-based skipping (fully optional, off by default) -----------------
    def should_skip_table(self, table: str) -> bool:
        return any(p.search(table) for p in self._skip_tables)

    def should_skip_column(self, column: str) -> bool:
        return any(p.search(column) for p in self._skip_cols)

    # -- main entry point -----------------------------------------------------
    def analyze_column(
        self, connector: Connector, schema: str, table: str, column: str
    ) -> Decision:
        db = connector.name

        # 1) Manual override wins outright.
        if decision := self.overrides.decide(db, schema, table, column):
            return self._finalize(decision)

        # 2) History (consistency / reproducibility).
        if self.config.detection.use_history and self.history is not None:
            prior = self.history.get(db, schema, table, column)
            if prior is not None:
                self.stats.record(prior)
                return prior  # already persisted; do not rewrite

        # Configurable name-based skip (e.g. *_ID, audit columns).
        if self.should_skip_column(column):
            return self._finalize(
                Decision(db, schema, table, column, Sensitivity.NOT_SENSITIVE,
                         source="skip", confidence=1.0, detail="Skipped by column name pattern")
            )

        sample = connector.sample_values(schema, table, column, self.config.detection.sample_size)

        # 3) Pattern matching.
        if self.config.detection.use_patterns and sample:
            match = self.patterns.match(sample)
            if match is not None:
                return self._finalize(
                    Decision(db, schema, table, column, Sensitivity.SENSITIVE,
                             rule=match.name, source="pattern", confidence=match.confidence,
                             detail=f"{match.ratio:.0%} of samples matched '{match.name}'")
                )

        # 4) LLM fallback.
        if self.llm is not None and sample:
            return self._finalize(self._classify_with_llm(db, schema, table, column, sample))

        # Nothing conclusive -> treat as not sensitive (recorded for next time).
        return self._finalize(
            Decision(db, schema, table, column, Sensitivity.NOT_SENSITIVE,
                     source="pattern" if sample else "no_data", confidence=0.5,
                     detail="No pattern matched and LLM disabled/unavailable")
        )

    def _classify_with_llm(self, db, schema, table, column, sample) -> Decision:
        if self.stats.tokens >= self.config.llm.max_tokens_budget:
            raise TokenBudgetExceeded(
                f"Token budget {self.config.llm.max_tokens_budget} reached."
            )
        trimmed = sample[: self.config.llm.sample_size]
        result = self.llm.classify(column, trimmed)  # type: ignore[union-attr]
        sensitivity = Sensitivity.SENSITIVE if result.sensitive else Sensitivity.NOT_SENSITIVE
        return Decision(
            database=db, schema=schema, table=table, column=column,
            sensitivity=sensitivity,
            rule=result.rule if result.sensitive else None,
            source="llm", confidence=result.confidence,
            detail="Classified by LLM", token_usage=result.token_usage,
        )

    def _finalize(self, decision: Decision) -> Decision:
        self.stats.record(decision)
        if self.history is not None and decision.source not in ("history",):
            self.history.save(decision)
        return decision
