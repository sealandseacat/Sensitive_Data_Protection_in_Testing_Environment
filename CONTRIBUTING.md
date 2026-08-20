# Contributing to dbmask

Thanks for your interest! Issues, bug reports, and pull requests are all
welcome — including small ones (a typo, a confusing error message, a missing
test case).

## Development setup

```bash
git clone https://github.com/sealandseacat/dbmask.git
cd dbmask
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
```

Run the test suite:

```bash
pytest
```

The suite needs no external services — everything runs against throwaway
SQLite databases in a temp directory. `dbmask` supports Python 3.9+, and CI
runs the suite on every supported version, so please keep new code 3.9-clean
(no `match` statements, no `X | Y` type unions in runtime code).

A quick end-to-end sanity check without any setup:

```bash
python examples/quickstart.py
```

## Project layout

| Directory | What lives there |
|---|---|
| `src/dbmask/connectors/` | database access (one SQLAlchemy code path for every dialect) |
| `src/dbmask/detection/`  | "is this column sensitive?" — overrides, patterns, history, LLM |
| `src/dbmask/masking/`    | strategies, dictionaries, seed map, the ETL engine |
| `src/dbmask/validation/` | post-masking verification |
| `tests/`                 | pytest suite (regression tests welcome with every bug fix) |

## The two easiest extension points

**A new masking strategy** — a callable `(value, context) -> masked_value`,
registered at runtime:

```python
from dbmask.masking.rules import register_strategy

def strat_fixed_domain(value, ctx):
    if value is None:
        return None
    return value.split("@")[0] + "@masked.example"

register_strategy("fixed_domain", strat_fixed_domain)
```

Strategies must be deterministic (derive randomness from
`seeded_rng(value, ctx.seed)`), must keep `None` as `None`, and should
preserve the value's format where that matters.

**A new connector** — subclass `dbmask.connectors.base.Connector` for
non-SQL sources. Note the contract on `iter_pages()`: the masking engine
writes each page back before requesting the next, so implementations for
transactional stores must not hold a read transaction or live cursor open
across a page boundary (see `SQLConnector.iter_pages` for the keyset-paginated
reference implementation).

## Pull requests

- Every bug fix should come with a regression test that fails on the old code.
- Keep commits focused; a short imperative subject line
  (`fix: ...`, `feat: ...`, `docs: ...`) matches the existing history.
- CI (tests on 3.9–3.14 + packaging checks) must be green.
- If you're planning something large, please open an issue first so we can
  agree on the direction before you invest the time.

## Reporting security issues

Please do **not** open a public issue — see [SECURITY.md](SECURITY.md).
