# sgp.fyi (rebuilt)

A local rebuild of the defunct **sgp.fyi** HDB resale-price explorer: search any
HDB block, see its full resale history back to 2000, price/PSF trends, every
transaction, and how it compares with blocks within 600 m.

Runs on **CT 204 (side-projects)** at `/opt/sgpfyi`, on port 80:

- http://sgp.lan/ or http://flats.lan/  (home network, via the AdGuard rewrite)
- http://192.168.50.254/                (home network, any DNS)
- https://side-projects.tail4fe555.ts.net:8444/  (tailnet, `tailscale serve`)

DNS: AdGuard Home (CT 202) has static rewrites `sgp.lan` / `flats.lan` ->
192.168.50.254 under `filtering.rewrites` (they need `enabled: true`, or AdGuard
writes `enabled: false` back on restart and the name silently NXDOMAINs).
Devices using the router (192.168.50.1) as DNS do not see those names — use the
IP there.

## Stack

No dependencies — Python 3.12 standard library only, SQLite, vanilla JS,
Chart.js (vendored), OSM raster tiles proxied + cached by the app.

```
app.py        HTTP server + JSON API + tile proxy     (systemd: sgpfyi.service)
ingest.py     builds data/sgpfyi.db from data/raw/*   (systemd: sgpfyi-ingest.timer)
download.py   pulls the raw datasets from data.gov.sg + OneMap dump
static/       index.html, app.js, style.css, chart.umd.js
data/sgpfyi.db          ~700k transactions, 9,967 blocks
data/raw/*.csv          source snapshots
data/tiles/             cached OSM tiles
```

## API

| endpoint | returns |
|---|---|
| `GET /api/meta` | row counts, month range, build time |
| `GET /api/suggest?q=` | block autocomplete (postal / block / street / town) |
| `GET /api/block?key=BLOCK\|STREET` | everything for one block |
| `GET /api/tile/z/x/y.png` | cached OSM tile |
| `GET /api/health` | liveness |

`key` is `BLOCK|STREET` with the street in its long OneMap form, e.g.
`176|BISHAN STREET 13` (URL-encode the pipe as `%7C`).

## Data

- **Transactions**: HDB resale flat prices on data.gov.sg, 5 datasets
  (2000–Feb 2012, Mar 2012–2014, 2015–2016, 2017–now). HDB republishes after
  the 1st working day of each month.
- **Postal codes + coordinates**: `buildings.json`, a community mirror of the
  OneMap building dump (`xkjyeah/singapore-postal-codes`). 98.8% of blocks
  match, so a handful of blocks have no map pin.
- **Tiles**: `tile.openstreetmap.de` (the main `tile.openstreetmap.org` cluster
  serves a blank "access blocked" PNG to datacenter IPs). Cached on disk for
  7 days at a time.

## Operations

```bash
systemctl status sgpfyi sgpfyi-ingest.timer     # is it up?
systemctl restart sgpfyi                        # after editing app.py/static
journalctl -u sgpfyi -n 50                      # app log /var/log/sgpfyi.log
systemctl start sgpfyi-ingest                   # manual monthly refresh (~2 min)
tail -f /var/log/sgpfyi-ingest.log
tailscale serve status                          # where is it published?
```

The timer fires on the **3rd of every month at 09:30 SGT** (`Persistent=true`,
so a missed run catches up), rebuilds the DB and restarts the app.

### Deploying a change

From CT 203 (or wherever), copy files to `/opt/sgpfyi` and restart:

```bash
rsync -av app.py ingest.py download.py static/ side-projects:/opt/sgpfyi/
ssh side-projects 'systemctl restart sgpfyi'
```

`index.html` references `static/app.js?v={{v}}`; the server stamps `{{v}}` with
a hash of the asset mtimes at request time, so browsers never run stale JS.

## Notes / gotchas

- `pkill -f app.py` over ssh kills the ssh session too (the pattern matches the
  remote shell's own command line). Use `pkill -f "[a]pp.py"`.
- data.gov.sg downloads: `GET api-open.data.gov.sg/v1/public/api/datasets/{id}/initiate-download`
  then `GET .../poll-download`. Space the calls out or you get `data: null`.
- Street names differ between HDB (`BISHAN ST 13`) and OneMap
  (`BISHAN STREET 13`); `ABBREV` in both scripts normalises them. Keep the two
  copies in sync if you add entries.
- "Nearby blocks" is sorted nearest-first and compares the block's dominant
  flat type where a neighbour has >= 3 such transactions in 18 months;
  otherwise it falls back to all types and the row is labelled `mixed`.
- Theme: light/dark toggle in the top bar. Persisted in `localStorage`
  (`sgpfyi-theme`), defaults to the OS `prefers-color-scheme`. Chart colours are
  read from CSS variables at render time, so a theme switch destroys and
  redraws the charts; map tiles are CSS-inverted in dark mode.

## Run it yourself

Needs Python 3.11+ and about 400 MB of disk. No third-party packages.

```bash
python3 download.py     # ~110 MB: four resale CSVs + the OneMap building dump
python3 ingest.py       # builds data/sgpfyi.db (~22 s, ~155 MB, ~700k rows)
python3 app.py          # http://localhost:8090  (SGPFYI_PORT / SGPFYI_HOST)
```

The monthly refresh is the same three steps; `deploy/` has the systemd units and
timer used in production (adjust paths/ports to taste).

## Licence & attribution

Code is MIT — see `LICENSE`.

The **data is not** covered by that licence and is not mine to license:

- HDB resale transactions: © Housing & Development Board, published under the
  [Singapore Open Data Licence v1.0](https://data.gov.sg/open-data-licence).
- Postal codes and coordinates: OneMap, © Singapore Land Authority (via a
  community mirror of OneMap's building dump).
- Map tiles: © OpenStreetMap contributors, proxied and cached by `app.py`.
- Chart.js: MIT.

If you redeploy this, keep the attribution in the page footer and be gentle with
the tile proxy — it caches to disk rather than hammering the tile server.

This is an independent project. It is not affiliated with, endorsed by, or
supported by HDB, SLA or any government agency, and the prices it shows are
indicative, not a valuation.

