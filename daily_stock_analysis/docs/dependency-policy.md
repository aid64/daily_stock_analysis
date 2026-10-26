# Dependency Governance and Vulnerability Audit

## Direct dependencies

- `daily_stock_analysis/requirements.txt` is the single direct-dependency list for the DSA project.
- CI, Docker, the desktop build and the daily GitHub Actions workflow install this list. There is **no lockfile yet**; reproducible cross-platform pins are a P1 follow-up.
- `quant_research` has its own dependency set and is under a feature freeze until approximately 2026-11-08.

## Upgrade policy

- Dependabot proposes grouped minor/patch updates weekly for DSA pip, npm, Actions and Docker.
- Major updates are ignored by Dependabot and require a manual compatibility review; they are never merged automatically.
- Docker base-image updates are limited to patch tags because minor/major jumps need a full packaging validation.
- quant_research version PRs are paused during the freeze (`open-pull-requests-limit: 0`).
- Runtime dependency changes must not be merged during an active deck close/first-build window; prefer after the close acceptance and with a full offline-suite run.

## Vulnerability audit

`.github/workflows/dependency-audit.yml` runs weekly (Monday 02:30 UTC) and on demand:

1. installs the declared requirements plus `pip-audit`;
2. writes `pip-audit.json` and a human-readable `pip-audit.txt`;
3. uploads both as workflow artifacts.

The job is **non-blocking**: it produces evidence for triage without turning an upstream CVE into a surprise CI outage. High/critical findings should be evaluated first; remediation should go through the normal upgrade policy above, not an ad-hoc unpinned bump.

## Known next steps

- Generate a universal lock/constraints file (for example with `uv pip compile --universal`), validate it in a clean environment, then use it in CI/Docker/daily tasks. Start from currently tested versions rather than letting the resolver jump major versions.
- Review the weekly audit artifact and track accepted risks explicitly.
- Once the dependency baseline is stable, consider making high/critical findings blocking (with an explicit allowlist for accepted advisories).
