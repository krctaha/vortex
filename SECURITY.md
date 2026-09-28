# Security

This research snapshot has not received an independent security audit. It has no
published security support policy or response-time guarantee yet. Real order
execution is not supported by its documented entrypoint.

## Reporting

Use GitHub's private vulnerability reporting channel for
[krctaha/vortex](https://github.com/krctaha/vortex/security/advisories).
Private vulnerability reporting is enabled. Do not put exploit details,
credentials or sensitive personal information in a public issue.

Reports should include affected version, minimal reproduction, impact and a
redacted example. Do not access other users' data or test a live deployment without
permission. A local reproduction is preferred.

## Operating safely

- Use `run_smc.py`, a fresh local configuration, and unique random secrets.
- Keep exchange API keys absent for public market-data research.
- Use an unprivileged service account, HTTPS and a reverse proxy for deployment.
- Bind the application to loopback behind the proxy; restrict firewall access.
- Keep SQLite, environment files, logs, backups and SSH keys out of the repository.
- Do not rely on a UI-hidden button as authorization; enforce roles server-side.
- Review dependencies and rotate any exposed secret before publishing history.

`scripts/check_release.py` is a lightweight guard, not a complete secret scanner
or a security audit. Independent dependency and secret scanning remains a release
gate. Legacy execution code and public access require extra review.
