# Self-hosting (research only)

First validate locally using the README. For a public server:

1. Create an unprivileged service account and install into a directory it can use.
2. Create a unique `.env` with `VORTEX_HOST=127.0.0.1`, a random secret and live data
   mode. Keep exchange keys blank. Optional Telegram tokens must remain private.
3. Run the virtual environment's Python with `run_smc.py` under your supervisor.
4. Configure your own domain and an HTTPS reverse proxy to `127.0.0.1:8000`.
   Restrict the application port from public access and set up the first account.
5. Check `/saglik` for readiness, data mode and feed freshness. Verify the scanner,
   reconnect/outage behavior, authorization and research-only order protection.
6. Back up the private SQLite database consistently and keep encrypted backups
   separate from source releases. Plan a tested rollback before upgrades.

The staging package intentionally contains no operator IP, SSH key, production
domain, existing user database or deployment-specific service configuration.
These are not installed by an automatic script. Choose access controls and backup
procedures appropriate to your environment; do not expose the old entrypoint.
