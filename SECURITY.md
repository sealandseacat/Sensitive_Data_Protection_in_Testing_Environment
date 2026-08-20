# Security Policy

`dbmask` exists to keep sensitive data out of places it doesn't belong, so
security reports get priority attention.

## Reporting a vulnerability

Please report vulnerabilities **privately** via GitHub:
**Security → Report a vulnerability** on the
[repository page](https://github.com/sealandseacat/dbmask/security/advisories/new),
or by email to <sealseacat@gmail.com> if you can't use GitHub.

Please do **not** open a public issue for anything you believe is a
vulnerability. You can expect an acknowledgement within a few days. Once a fix
ships, the report and credit (if you want it) are published in the advisory
and the changelog.

## Supported versions

Until 1.0, only the latest released version receives security fixes.

## What counts as a vulnerability here

Reports especially welcome for anything that undermines the tool's core
guarantees, for example:

- **Masked output leaking source data** — any input where a strategy returns
  the original value (or a trivially reversible transformation of it) while
  reporting success.
- **Seed map re-identification** — the seed map stores only salted hashes of
  original values; anything that recovers originals from a seed map database
  (with or without the salt) is a serious bug.
- **Validation false positives** — inputs that make `dbmask validate` report
  "fully masked" while sensitive values in fact survived.
- Classic issues in dependencies/usage: SQL injection through configuration
  values, credentials written to disk or logs, etc.

## Deployment notes for users

- Set the seed-map salt via an environment variable
  (`salt: ${DBMASK_SEED_SALT}`) so it is never written to disk; the store
  alone is then not enough to probe whether a given value was masked.
- Treat the history and seed-map databases as sensitive operational data:
  they contain no original values, but they do reveal which columns hold
  sensitive data and how values map between environments.
- Run masking against a **copy** of production, never in place against the
  production database itself.
