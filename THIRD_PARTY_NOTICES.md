# Third-party notices

Bundled components keep their original licenses independently of any proposed
VORTEX license. Do not remove chart attribution, license headers or notices.

| Component | Bundled location | Upstream license / source |
| --- | --- | --- |
| TradingView Lightweight Charts 4.2.3 | `static/vendor/lightweight-charts.standalone.production.js` | Apache-2.0; [tagged upstream](https://github.com/tradingview/lightweight-charts/tree/v4.2.3) |
| Inter font family | `static/vendor/fonts/inter-*.woff2` | SIL OFL 1.1; [upstream font distribution](https://github.com/google/fonts/tree/main/ofl/inter) |
| JetBrains Mono font family | `static/vendor/fonts/jetbrains-mono-*.woff2` | SIL OFL 1.1; [upstream font distribution](https://github.com/google/fonts/tree/main/ofl/jetbrainsmono) |
| Archivo font family | `static/vendor/fonts/archivo-*.woff2` | SIL OFL 1.1; [upstream font distribution](https://github.com/google/fonts/tree/main/ofl/archivo) |

Upstream license texts and the chart NOTICE are included in `licenses/`. The
vendored chart's header identifies 4.2.3. Bundled font name tables were inspected:
Inter reports `Version 4.001;git-66647c0bb`, JetBrains Mono `Version 2.211`,
and Archivo `Version 2.001`. Each reports an OFL license URL. The original download
URLs were not recorded in the inherited project; these embedded versions are
documented for traceability. This is not a complete licensing audit.

Python packages in `requirements*.txt` are installed separately and retain their
own licenses. They are not relicensed by the project's license. Review transitive
dependencies and vulnerabilities as part of each release.

The inherited downloaded coin-icon cache is excluded from this source package;
the application can generate monogram fallbacks instead. Any future downloaded
icons still need their own reuse review.

Exchange/coin logos, branding, feed content and remotely loaded media may have
separate trademark, attribution and reuse restrictions. VORTEX's brand assets and
inherited code require maintainer rights confirmation before publication. No
affiliation or endorsement by TradingView, Binance, OpenAI or trader communities
is implied. No paid proprietary trader-community indicator is claimed as bundled.
