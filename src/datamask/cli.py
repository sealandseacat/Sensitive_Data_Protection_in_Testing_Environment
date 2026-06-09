"""Command-line interface.

Examples
--------
    datamask scan   --config config/datamask.config.yaml
    datamask mask   --config config/datamask.config.yaml          # dry-run by default
    datamask mask   --config config/datamask.config.yaml --apply  # actually write back
    datamask history --config config/datamask.config.yaml
    datamask strategies
"""
from __future__ import annotations

import json
import sys

import click

from datamask import __version__
from datamask.config import Config
from datamask.runner import Runner


@click.group()
@click.version_option(__version__, prog_name="datamask")
def cli() -> None:
    """datamask — discover and mask sensitive data in any database."""


def _load(config_path: str) -> Config:
    try:
        return Config.load(config_path)
    except Exception as exc:  # noqa: BLE001
        click.echo(f"[error] Failed to load config '{config_path}': {exc}", err=True)
        sys.exit(2)


@cli.command()
@click.option("--config", "config_path", required=True, help="Path to config YAML.")
@click.option("--json", "as_json", is_flag=True, help="Emit decisions as JSON.")
def scan(config_path: str, as_json: bool) -> None:
    """Classify every column as sensitive or not (no data is modified)."""
    config = _load(config_path)
    with Runner(config) as runner:
        report = runner.scan()

    if as_json:
        click.echo(json.dumps([d.to_dict() for d in report.decisions], indent=2))
    else:
        for d in report.decisions:
            flag = "SENSITIVE" if d.is_sensitive else "ok"
            rule = f" -> {d.rule}" if d.rule else ""
            click.echo(f"[{flag:9}] {d.schema}.{d.table}.{d.column}{rule} "
                       f"({d.source}, conf={d.confidence:.2f})")
        s = runner.pipeline.stats
        click.echo("\n--- Summary ---")
        click.echo(f"Columns analyzed : {s.total}")
        click.echo(f"Sensitive found  : {s.sensitive}")
        click.echo(f"By source        : {s.by_source}")
        click.echo(f"LLM tokens used  : {s.tokens}")
    for err in report.errors:
        click.echo(f"[warn] {err}", err=True)


@cli.command()
@click.option("--config", "config_path", required=True, help="Path to config YAML.")
@click.option("--apply", "apply", is_flag=True,
              help="Write masked values back. Without this flag it's a dry-run preview.")
def mask(config_path: str, apply: bool) -> None:
    """Mask sensitive columns. Dry-run preview unless --apply is given."""
    config = _load(config_path)
    if apply:
        config.masking.dry_run = False

    with Runner(config) as runner:
        results = runner.mask()

    mode = "APPLIED" if apply else "DRY-RUN (no changes written)"
    click.echo(f"=== Masking {mode} ===")
    for res in results:
        click.echo(f"\n{res.schema}.{res.table}  "
                   f"(scanned={res.rows_scanned}, written={res.rows_written})")
        for plan in res.columns:
            click.echo(f"  - {plan.column}: rule={plan.rule} -> strategy={plan.strategy_name}")
        for sample in res.preview[:3]:
            click.echo(f"    before: {sample['before']}")
            click.echo(f"    after : {sample['after']}")
    if not apply:
        click.echo("\nRe-run with --apply to write these changes back.")


@cli.command()
@click.option("--config", "config_path", required=True, help="Path to config YAML.")
def history(config_path: str) -> None:
    """Show all decisions recorded in the history store."""
    config = _load(config_path)
    from datamask.history.store import HistoryStore

    if not config.history.enabled:
        click.echo("History is disabled in config.")
        return
    with HistoryStore(config.history.url) as store:
        for d in store.all_decisions():
            rule = f" -> {d.rule}" if d.rule else ""
            click.echo(f"{d.key}: {d.sensitivity.value}{rule} ({d.source})")


@cli.command()
def strategies() -> None:
    """List the available masking strategies."""
    from datamask.masking.rules import STRATEGIES

    for name in sorted(STRATEGIES):
        click.echo(name)


@cli.command()
@click.option("--config", "config_path", required=True, help="Path to config YAML.")
@click.option("--json", "as_json", is_flag=True, help="Emit the report as JSON.")
def validate(config_path: str, as_json: bool) -> None:
    """Validate the masked database against the original (source) database.

    Runs three checks: row counts, schema elements, and masking completeness.
    Exits non-zero if any check fails (handy for CI / Jenkins gates).
    """
    config = _load(config_path)
    with Runner(config) as runner:
        report = runner.validate()

    if as_json:
        click.echo(json.dumps([i.to_dict() for i in report.issues], indent=2))
    else:
        icons = {"pass": "✓", "fail": "✗", "warning": "!", "skipped": "-", "error": "E"}
        for issue in report.issues:
            icon = icons.get(issue.status.value, "?")
            click.echo(f"[{icon}] {issue.check:22} {issue.location}: {issue.message}")
        click.echo("\n--- Validation summary ---")
        for status, count in report.summary().items():
            click.echo(f"  {status:8}: {count}")
        click.echo("\nRESULT: " + ("PASSED ✓" if report.passed else "FAILED ✗"))

    if not report.passed:
        sys.exit(1)


if __name__ == "__main__":  # pragma: no cover
    cli()
