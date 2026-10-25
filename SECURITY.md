# Security Policy

## Supported versions

Security fixes are applied to the `main` branch. Older commits and forks are
not maintained; reproduce issues against the latest `main` before reporting.

## Reporting a vulnerability

Do **not** open a public issue for a suspected vulnerability.

Use the repository's **Security** tab and choose **Report a vulnerability** to
open a private GitHub security advisory. If that option is unavailable, open a
minimal public issue asking the maintainers for a private channel, without
technical details, exploit steps, credentials, or screenshots containing them.

Please include:

- affected commit or version, component (API / Web / Desktop / bot / data
  provider / script), and deployment mode;
- a minimal reproduction and the observed impact;
- whether the issue is already public or actively exploited.

Never paste real API keys, Feishu credentials, session cookies, `.env` contents
or personal portfolio data into a report. If a secret was exposed, rotate it
first; assume it is compromised.

## Scope

In scope: this repository's application code, API endpoints, authentication and
session handling, data-provider integrations, desktop/Web clients, deployment
scripts and CI workflows.

Out of scope: vulnerabilities in upstream dependencies (report them to the
upstream project; we track them via dependency updates), social engineering,
physical access, and volumetric denial-of-service against third-party services.

## Process and disclosure

Maintainers triage reports on a best-effort basis and do not promise a fixed
response time. We may ask for more evidence or a proof of concept. Please allow
a reasonable remediation window before public disclosure; coordinated disclosure
credit is available if desired.

Good-faith research that follows this policy and does not access other users'
data or disrupt production is welcome.
