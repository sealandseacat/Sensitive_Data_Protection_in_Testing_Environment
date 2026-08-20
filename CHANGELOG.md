# Changelog

All notable changes to this project are documented in this file.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and the project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

Nothing yet.

## [0.1.0] - 2026-08-20

First public release.

### Changed
- **Repositioned the README** from test-data masking to what the engine
  actually is: discovering, masking and validating sensitive data in any
  database, for every environment production data flows to. Added use-cases
  and roadmap sections (data-governance integration first among them).
  Prompted by [#1](https://github.com/sealandseacat/dbmask/issues/1) —
  thanks @dbwhizard.
- **Renamed the package from `datamask` to `dbmask`.** The name `datamask`
  is already taken on PyPI by an existing project in the same space, so the
  package needed a new, non-colliding name before its first release.
  Everything follows the new name: the import (`import dbmask`), the CLI
  (`dbmask scan ...`), the config file names (`config/dbmask.config.yaml`),
  the seed-salt environment variable (`DBMASK_SEED_SALT`), the default store
  files (`dbmask_history.db`, `dbmask_seedmap.db`) and their table names.
- Masking applies in key-ordered pages (keyset pagination) instead of writing
  into a live streaming read; reads and writes no longer overlap.

### Fixed
- **Masking tables larger than one batch no longer fails with
  `database is locked` on SQLite**
  ([#2](https://github.com/sealandseacat/dbmask/issues/2)).
  The engine used to stream-read the table
  while writing batches back on a second connection; the in-flight read held
  a SHARED lock that blocked every write commit. Applies now read one page,
  write it back, then read the next.
- **Bundled dictionaries crashed on Python 3.9** (`TypeError` from
  `importlib.resources.files()` anchored on a namespace package;
  [#3](https://github.com/sealandseacat/dbmask/issues/3)). Resource
  loading is now anchored on a regular package and works on all supported
  Pythons (3.9–3.14). Loader breakage now also fails loudly instead of
  silently degrading every fake name to the same fallback value.

### Added
- Keyset pagination API for connectors (`Connector.iter_pages`), with a
  portable expanded row-value comparison for composite primary keys.
- `py.typed` marker: type checkers now use the package's inline annotations.
- CI: tests on Python 3.9–3.14 (Ubuntu) and Windows, plus sdist/wheel build
  validation with `twine check --strict` on every push and pull request.
- `CONTRIBUTING.md`, `SECURITY.md`, and this changelog.

[Unreleased]: https://github.com/sealandseacat/dbmask/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/sealandseacat/dbmask/releases/tag/v0.1.0
