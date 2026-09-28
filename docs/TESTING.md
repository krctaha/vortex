# Testing

Use an isolated Python 3.12 environment. Install `requirements-dev.txt` and run
`python -m pytest -q`. The suite uses fixtures/mocks and temporary test databases;
do not point tests at production state. No live order test belongs in this suite.

Browser scripts under `testler/ui_*.cjs` use Playwright and a locally installed
Chrome. They are developer regression tools, not a fully uniform CI suite.
Install Playwright separately and create `qa/` for local output. Some scripts
expect the isolated preview server (`python testler/preview_smc.py`); others read
login JSON from standard input. Inspect the script before running it. Fixed preview
credentials in that helper are local-only fixtures, never production credentials.

For `ui_chart_terminal.cjs`, set `PLAYWRIGHT_PATH` to the installed Playwright module
if necessary. `VORTEX_TEST_URL` defaults to localhost. Supply a **local account's**
`{"username":"...","password":"..."}` JSON on stdin without committing it.
The terminal test checks layout, timeframe, watchlist, indicators, zoom/fullscreen,
mobile drawer, time alignment and JavaScript errors.

**`ui_live_controls.cjs` creates/deletes test accounts** and other scripts may
change local settings. Run against an isolated local database only. Chart visual
tests deliberately intercept HTTP responses to exercise outage/retry rendering.
Browser scripts require network access for live data in some cases and are not
included in the Python unit-test count.

CI runs Python tests plus the lightweight release guard on Linux/Python 3.12.
Dependency installation is checked independently; neither test counts nor CI
replace a vulnerability audit or measured production latency/strategy validation.
