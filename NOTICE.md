# Data notice

The MIT licence in `LICENSE` covers the **source code** in this repository. It
does **not** cover the data the code downloads, which is not mine to license:

| Data | Source | Terms |
|---|---|---|
| HDB resale transactions (2000–now) | data.gov.sg, Housing & Development Board | [Singapore Open Data Licence v1.0](https://data.gov.sg/open-data-licence) |
| Postal codes, block coordinates | OneMap, Singapore Land Authority (via community mirror) | Singapore Open Data Licence v1.0 |
| Map tiles | © OpenStreetMap contributors | [ODbL](https://www.openstreetmap.org/copyright) |

Chart.js is bundled under its own MIT licence (`static/chart.umd.js`).

If you deploy this, keep the attribution in the page footer, and let the tile
proxy keep caching — it is designed not to hammer the tile server.

This is an independent project. It is not affiliated with, endorsed by, or
supported by HDB, SLA or any government agency, and the figures shown are
indicative only — not a valuation.
