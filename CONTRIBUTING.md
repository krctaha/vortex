# Contributing

Contributions to this early research project are welcome at
https://github.com/krctaha/vortex. Project code uses the MIT license;
third-party notices must be preserved.

1. Use Python 3.12 and install `requirements-dev.txt` in an isolated environment.
2. Use local synthetic data or public market data; never use real exchange secrets
   or real orders for a regression test.
3. Keep changes focused and include regression tests for behavior changes.
4. Run `python -m pytest -q` and `python scripts/check_release.py`.
5. For UI changes, check desktop and mobile, keyboard access, empty/error/stale
   states and navigation without duplicate subscriptions.
6. Explain assumptions, test results and remaining limitations in your pull request.

Strategy changes must preserve closed-candle semantics and avoid look-ahead bias.
Do not silently substitute synthetic data in live mode, advertise scores as win
probabilities, or remove stale-data indicators. Outcome-based ranking changes must
document bounds, sample sizes and reproducibility. Do not submit third-party
proprietary indicators, broker credentials, customer data or material you cannot
license. Preserve attribution and vendored dependency notices.

Do not post exploitable security details in public issues; see `SECURITY.md`.
Never run account-management or notification tests against someone else's server.
