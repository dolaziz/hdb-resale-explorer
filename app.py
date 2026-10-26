#!/usr/bin/env python3
"""sgp.fyi — HDB resale transaction explorer.

Stdlib-only HTTP server (no dependencies):
  GET /                    single page app
  GET /static/<file>       assets
  GET /api/meta            dataset freshness
  GET /api/suggest?q=      block/postal/street autocomplete
  GET /api/block?key=      everything for one block
"""
import gzip
import json
import math
import os
import re
import sqlite3
import threading
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlparse

HERE = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(HERE, "data", "sgpfyi.db")
STATIC = os.path.join(HERE, "static")
PORT = int(os.environ.get("SGPFYI_PORT", "8090"))
HOST = os.environ.get("SGPFYI_HOST", "0.0.0.0")

ABBREV = {
    "AVE": "AVENUE", "AV": "AVENUE", "ST": "STREET", "RD": "ROAD", "DR": "DRIVE",
    "CRES": "CRESCENT", "CL": "CLOSE", "PK": "PARK", "JLN": "JALAN", "BT": "BUKIT",
    "NTH": "NORTH", "STH": "SOUTH", "CTRL": "CENTRAL", "PL": "PLACE",
    "TER": "TERRACE", "LK": "LINK", "GDNS": "GARDENS", "HTS": "HEIGHTS",
    "KG": "KAMPONG", "LOR": "LORONG", "UPP": "UPPER", "LWR": "LOWER",
    "MT": "MOUNT", "FT": "FORT", "EXT": "EXTENSION", "BDR": "BOUNDARY",
}

_local = threading.local()
_blocks = []
_meta = {}


def norm_street(s):
    s = (s or "").upper().replace(".", " ")
    s = re.sub(r"[^A-Z0-9' ]+", " ", s)
    return " ".join(ABBREV.get(w, w) for w in s.split())


def db():
    con = getattr(_local, "con", None)
    if con is None:
        con = sqlite3.connect(DB, check_same_thread=False)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA query_only=ON")
        _local.con = con
    return con


def load_index():
    """Load the block index + dataset metadata into memory (called at boot)."""
    global _blocks, _meta
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    out = []
    for r in con.execute(
        "SELECT addr_key, block, street, town, postal, lat, lon, lease_year,"
        " n_tx, first_month, last_month FROM blocks"
    ):
        b = dict(r)
        b["street_norm"] = norm_street(b["street"])
        b["label"] = f"{b['block']} {b['street']}"
        b["words"] = set(
            f"{b['block']} {b['street']} {b['street_norm']} {b['postal'] or ''} {b['town'] or ''}".upper().split()
        )
        out.append(b)
    _blocks = out
    row = con.execute(
        "SELECT MIN(month), MAX(month), COUNT(*) FROM tx"
    ).fetchone()
    _meta = {
        "first_month": row[0],
        "last_month": row[1],
        "transactions": row[2],
        "blocks": len(out),
        "built_at": datetime.now().astimezone().isoformat(timespec="seconds"),
    }
    mt = os.path.join(HERE, "data", "meta.json")
    if os.path.exists(mt):
        try:
            m = json.load(open(mt))
            _meta["ingested_at"] = m.get("built_at")
            _meta["dataset_last_month"] = m.get("last_month")
        except Exception:  # noqa: BLE001
            pass
    con.close()


# ---------------------------------------------------------------- helpers
def median(vals):
    vals = [v for v in vals if v is not None]
    if not vals:
        return None
    return round(statistics_median(vals), 2)


def statistics_median(vals):
    s = sorted(vals)
    n = len(s)
    if n == 0:
        return 0
    if n % 2:
        return s[n // 2]
    return (s[n // 2 - 1] + s[n // 2]) / 2


def pct(a, b):
    if a is None or not b:
        return None
    return round((a - b) / b * 100, 1)


def month_add(m, delta):
    y, mo = int(m[:4]), int(m[5:7])
    y += (mo - 1 + delta) // 12
    mo = (mo - 1 + delta) % 12 + 1
    return f"{y:04d}-{mo:02d}"


def quarter(m):
    return f"{m[:4]} Q{(int(m[5:7]) - 1) // 3 + 1}"


def haversine(lat1, lon1, lat2, lon2):
    r = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = p2 - p1
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def token_hit(tok, words):
    """Word match: exact, or prefix for tokens of 3+ chars.

    Short numeric tokens ("5", "13") must match a whole word — otherwise
    "Tampines Ave 5" matches block 803 because its postal starts with 520...
    """
    if tok in words:
        return True
    return len(tok) >= 3 and any(w.startswith(tok) for w in words)


FLAT_ORDER = ["1 ROOM", "2 ROOM", "3 ROOM", "4 ROOM", "5 ROOM", "EXECUTIVE",
              "MULTI-GENERATION", "MULTI GENERATION"]


def flat_rank(t):
    try:
        return FLAT_ORDER.index(t)
    except ValueError:
        return len(FLAT_ORDER)


# ---------------------------------------------------------------- suggest
def suggest(q, limit=15):
    q = (q or "").strip().upper()
    if not q:
        return []
    digits = re.sub(r"[^0-9]", "", q)
    tokens = [t for t in re.split(r"\s+", q) if t]
    scored = []
    for b in _blocks:
        s = None
        if len(digits) == 6 and not re.search(r"[A-Z]", q):
            if b["postal"] == digits:
                s = 0
            elif b["postal"].startswith(digits):
                s = 2
        elif len(digits) >= 3 and not re.search(r"[A-Z]", q):
            # partial postal (rare) — fall through to token match too
            if b["postal"].startswith(digits):
                s = 2
        if s is None:
            if all(token_hit(t, b["words"]) for t in tokens):
                label = b["label"].upper()
                if label.startswith(q):
                    s = 3
                elif b["block"] == tokens[0]:
                    s = 4
                elif tokens[0] in b["street_norm"].split() or b["street_norm"].startswith(tokens[0]):
                    s = 5
                else:
                    s = 6
        if s is not None:
            scored.append((s, -b["n_tx"], b))
    scored.sort(key=lambda x: (x[0], x[1]))
    out = []
    for s, _, b in scored[:limit]:
        out.append({
            "key": b["addr_key"],
            "label": b["label"],
            "block": b["block"],
            "street": b["street"],
            "town": b["town"] or "",
            "postal": b["postal"] or "",
            "lease_year": b["lease_year"],
            "n_tx": b["n_tx"],
        })
    return out


# ---------------------------------------------------------------- block page
def block_payload(key):
    key = unquote(key or "").strip()
    con = db()
    b = con.execute(
        "SELECT * FROM blocks WHERE addr_key = ?", (key,)
    ).fetchone()
    if b is None:
        # tolerate "BLOCK|STREET" typed by hand, or block + street args
        parts = key.split("|")
        if len(parts) == 2:
            blk, st = parts[0].strip().upper(), parts[1].strip().upper()
            for candidate in (st, norm_street(st)):
                b = con.execute(
                    "SELECT * FROM blocks WHERE block = ? AND street = ?",
                    (blk, candidate),
                ).fetchone()
                if b is not None:
                    break
    if b is None:
        return None

    rows = con.execute(
        "SELECT month, flat_type, storey_range, storey_lo, storey_hi, area, model,"
        " lease_year, remaining, price, psf FROM tx WHERE addr_key = ?"
        " ORDER BY month DESC, price DESC",
        (b["addr_key"],),
    ).fetchall()
    if not rows:
        return None

    last_month = rows[0]["month"]
    c12 = month_add(last_month, -11)
    c24 = month_add(last_month, -23)

    def within(m, start):
        return m >= start

    recent = [r for r in rows if within(r["month"], c12)]
    prior = [r for r in rows if c24 <= r["month"] < c12]

    types = sorted({r["flat_type"] for r in rows}, key=flat_rank)

    # quarterly median series, overall + per flat type
    def series(subset, keyname="price"):
        buckets = {}
        for r in subset:
            buckets.setdefault(quarter(r["month"]), []).append(r)
        out = []
        for q in sorted(buckets):
            rs = buckets[q]
            out.append({
                "q": q,
                "n": len(rs),
                "price": median([r["price"] for r in rs]),
                "psf": median([r["psf"] for r in rs]),
                "area": median([r["area"] for r in rs]),
            })
        return out

    by_type = {}
    for t in types:
        sub = [r for r in rows if r["flat_type"] == t]
        if len(sub) >= 3:
            by_type[t] = series(sub)

    # yearly medians (long view, every flat type combined)
    yearly = {}
    for r in rows:
        yearly.setdefault(r["month"][:4], []).append(r)
    year_series = [{
        "year": y,
        "n": len(rs),
        "price": median([r["price"] for r in rs]),
        "psf": median([r["psf"] for r in rs]),
    } for y, rs in sorted(yearly.items())]

    # per flat type stats table
    type_stats = []
    for t in types:
        sub = [r for r in rows if r["flat_type"] == t]
        rec = [r for r in sub if within(r["month"], c12)]
        prev = [r for r in sub if c24 <= r["month"] < c12]
        med_price = median([r["price"] for r in rec]) or median([r["price"] for r in sub])
        med_psf = median([r["psf"] for r in rec]) or median([r["psf"] for r in sub])
        type_stats.append({
            "flat_type": t,
            "n": len(sub),
            "n_12m": len(rec),
            "median_area": median([r["area"] for r in rec or sub]),
            "median_storey": median([r["storey_lo"] for r in rec or sub]),
            "median_price": med_price,
            "median_psf": med_psf,
            "yoy_price": pct(med_price, median([r["price"] for r in prev])),
            "yoy_psf": pct(med_psf, median([r["psf"] for r in prev])),
            "p25_price": percentile([r["price"] for r in rec or sub], 0.25),
            "p75_price": percentile([r["price"] for r in rec or sub], 0.75),
        })

    overall = {
        "latest_month": last_month,
        "n_total": len(rows),
        "n_12m": len(recent),
        "median_price_12m": median([r["price"] for r in recent]) or median([r["price"] for r in rows]),
        "median_psf_12m": median([r["psf"] for r in recent]) or median([r["psf"] for r in rows]),
        "median_price_prev": median([r["price"] for r in prior]),
        "median_psf_prev": median([r["psf"] for r in prior]),
    }
    overall["yoy_price"] = pct(overall["median_price_12m"], overall["median_price_prev"])
    overall["yoy_psf"] = pct(overall["median_psf_12m"], overall["median_psf_prev"])
    overall["last_price"] = rows[0]["price"]
    overall["last_tx"] = {
        "month": rows[0]["month"],
        "flat_type": rows[0]["flat_type"],
        "storey_range": rows[0]["storey_range"],
        "area": rows[0]["area"],
        "price": rows[0]["price"],
        "psf": rows[0]["psf"],
    }

    # town context: median psf last 12 months for the whole town
    town_row = con.execute(
        "SELECT COUNT(*) AS n, AVG(psf) AS mean FROM tx WHERE town = ? AND month >= ?",
        (b["town"], c12),
    ).fetchone()
    town_med = None
    if town_row and town_row["n"]:
        vals = [r[0] for r in con.execute(
            "SELECT psf FROM tx WHERE town = ? AND month >= ? LIMIT 40000", (b["town"], c12)
        )]
        town_med = median(vals)

    # nearby blocks — compare like with like: same flat type where possible
    nearby = []
    if b["lat"] and b["lon"]:
        c18 = month_add(last_month, -17)
        # this block's dominant flat type (last 12 months, else all-time)
        counts = {}
        for r in recent or rows:
            counts[r["flat_type"]] = counts.get(r["flat_type"], 0) + 1
        dominant = max(counts, key=counts.get) if counts else None
        ref_psf = median([r["psf"] for r in rows if r["flat_type"] == dominant
                          and r["month"] >= c12]) if dominant else None
        if ref_psf is None:
            ref_psf = overall["median_psf_12m"]

        cands = []
        for o in _blocks:
            if o["addr_key"] == b["addr_key"] or not o["lat"] or not o["lon"]:
                continue
            d = haversine(b["lat"], b["lon"], o["lat"], o["lon"])
            if d <= 600:
                cands.append((d, o))
        cands.sort(key=lambda x: x[0])
        cands = cands[:40]
        keys = [o["addr_key"] for _, o in cands]
        if keys:
            qs = ",".join("?" * len(keys))
            agg = {}
            for r in con.execute(
                f"SELECT addr_key, price, psf, month, flat_type FROM tx"
                f" WHERE addr_key IN ({qs}) AND month >= ?", (*keys, c18)
            ):
                agg.setdefault(r["addr_key"], []).append(r)
            for d, o in cands:
                rs = agg.get(o["addr_key"])
                if not rs or len(rs) < 3:
                    continue
                same = [r for r in rs if r["flat_type"] == dominant]
                use = same if len(same) >= 3 else rs
                psf_med = median([r["psf"] for r in use])
                nearby.append({
                    "key": o["addr_key"],
                    "block": o["block"],
                    "street": o["street"],
                    "postal": o["postal"] or "",
                    "dist_m": int(round(d)),
                    "n": len(use),
                    "flat_type": dominant if len(same) >= 3 else "mixed",
                    "match": len(same) >= 3,
                    "median_price": median([r["price"] for r in use]),
                    "median_psf": psf_med,
                    "vs_this": pct(psf_med, ref_psf),
                    "lease_year": o["lease_year"],
                })
    nearby.sort(key=lambda x: x["dist_m"])  # nearest first

    tx_rows = [{
        "month": r["month"],
        "flat_type": r["flat_type"],
        "storey_range": r["storey_range"],
        "storey_lo": r["storey_lo"],
        "area": r["area"],
        "model": r["model"],
        "lease_year": r["lease_year"],
        "remaining": r["remaining"],
        "price": r["price"],
        "psf": r["psf"],
    } for r in rows[:600]]

    remaining_now = None
    if b["lease_year"]:
        remaining_now = round(max(0.0, 99 - (int(last_month[:4]) - int(b["lease_year"]))), 1)

    return {
        "key": b["addr_key"],
        "block": b["block"],
        "street": b["street"],
        "town": b["town"],
        "postal": b["postal"],
        "lat": b["lat"],
        "lon": b["lon"],
        "lease_year": b["lease_year"],
        "remaining_lease": remaining_now,
        "first_month": b["first_month"],
        "last_month": b["last_month"],
        "overall": overall,
        "town_median_psf": town_med,
        "types": types,
        "type_stats": type_stats,
        "series": {"by_type": by_type, "yearly": year_series, "all": series(rows)},
        "transactions": tx_rows,
        "tx_truncated": len(rows) > 600,
        "nearby": nearby,
        "nearby_ref": {
            "flat_type": dominant if b["lat"] else None,
            "median_psf": ref_psf if b["lat"] else None,
        },
    }


def percentile(vals, p):
    vals = sorted(v for v in vals if v is not None)
    if not vals:
        return None
    if len(vals) == 1:
        return round(vals[0], 2)
    k = (len(vals) - 1) * p
    f = math.floor(k)
    c = math.ceil(k)
    if f == c:
        return round(vals[int(k)], 2)
    return round(vals[f] * (c - k) + vals[c] * (k - f), 2)


# ---------------------------------------------------------------- tiles
TILE_DIR = os.path.join(HERE, "data", "tiles")
TILE_UA = "sgpfyi-homelab/1.0 (personal HDB price explorer; cached proxy)"


def get_tile(z, x, y):
    """Raster tile proxy with on-disk cache (polite to openstreetmap.org)."""
    if not (0 <= z <= 19 and 0 <= x < 2 ** z and 0 <= y < 2 ** z):
        return None
    path = os.path.join(TILE_DIR, str(z), str(x), f"{y}.png")
    if os.path.exists(path):
        return path
    import urllib.request

    for host in ("tile.openstreetmap.de",):
        # NOTE: tile.openstreetmap.org serves a blank "access blocked" PNG to
        # datacenter IPs; the German OSM style server works and is still OSM data.
        try:
            req = urllib.request.Request(
                f"https://{host}/{z}/{x}/{y}.png", headers={"User-Agent": TILE_UA}
            )
            with urllib.request.urlopen(req, timeout=15) as r:
                data = r.read()
            if not data:
                continue
            os.makedirs(os.path.dirname(path), exist_ok=True)
            tmp = path + ".part"
            with open(tmp, "wb") as f:
                f.write(data)
            os.replace(tmp, path)
            return path
        except Exception:  # noqa: BLE001
            continue
    return None


# ---------------------------------------------------------------- http
class Handler(BaseHTTPRequestHandler):
    server_version = "sgpfyi/1.0"
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):  # quieter logs
        if "/api/" in (self.path or ""):
            return
        super().log_message(fmt, *args)

    def _send(self, code, body, ctype="application/json; charset=utf-8", cache=None):
        if isinstance(body, str):
            body = body.encode()
        hdrs = [("Content-Type", ctype),
                ("X-Content-Type-Options", "nosniff")]
        if cache:
            hdrs.append(("Cache-Control", cache))
        ae = self.headers.get("Accept-Encoding", "")
        if "gzip" in ae and len(body) > 900:
            body = gzip.compress(body, 6)
            hdrs.append(("Content-Encoding", "gzip"))
        hdrs.append(("Content-Length", str(len(body))))
        self.send_response(code)
        for k, v in hdrs:
            self.send_header(k, v)
        self.end_headers()
        try:
            self.wfile.write(body)
        except BrokenPipeError:
            pass

    def _json(self, obj, code=200):
        self._send(code, json.dumps(obj, separators=(",", ":")), cache="no-store")

    def do_GET(self):
        u = urlparse(self.path)
        path = u.path
        qs = parse_qs(u.query)
        try:
            if path == "/api/meta":
                return self._json(_meta)
            if path == "/api/suggest":
                return self._json({"results": suggest((qs.get("q") or [""])[0])})
            if path == "/api/block":
                key = (qs.get("key") or [""])[0]
                payload = block_payload(key)
                if payload is None:
                    return self._json({"error": "block not found"}, 404)
                return self._json(payload)
            if path.startswith("/api/tile/"):
                parts = path.strip("/").split("/")
                if len(parts) != 5:
                    return self._send(404, "bad tile", "text/plain")
                z, x, y = (int(v.split(".")[0]) for v in parts[2:5])
                tile = get_tile(z, x, y)
                if not tile:
                    return self._send(404, "no tile", "text/plain")
                with open(tile, "rb") as f:
                    return self._send(200, f.read(), "image/png",
                                      "public, max-age=604800, immutable")
            if path == "/api/health":
                return self._json({"ok": True, "blocks": len(_blocks)})
            return self._static(path)
        except Exception as e:  # noqa: BLE001
            self._json({"error": str(e)}, 500)

    def _static(self, path):
        if path in ("", "/"):
            path = "/index.html"
        rel = path.lstrip("/")
        if rel.startswith("static/"):
            rel = rel[len("static/"):]
        full = os.path.normpath(os.path.join(STATIC, rel))
        if not full.startswith(STATIC) or not os.path.isfile(full):
            return self._send(404, "not found", "text/plain; charset=utf-8")
        if full.endswith(".html"):
            import hashlib
            h = hashlib.sha1()
            for f in ("app.js", "style.css", "chart.umd.js"):
                fp = os.path.join(STATIC, f)
                if os.path.exists(fp):
                    h.update(str(os.path.getmtime(fp)).encode())
            ver = h.hexdigest()[:8]
            with open(full, "rb") as fh:
                body = fh.read().replace(b"{{v}}", ver.encode())
            return self._send(200, body, "text/html; charset=utf-8", "no-cache")
        ctype = {
            ".html": "text/html; charset=utf-8",
            ".js": "application/javascript; charset=utf-8",
            ".css": "text/css; charset=utf-8",
            ".svg": "image/svg+xml",
            ".png": "image/png",
            ".ico": "image/x-icon",
            ".json": "application/json",
            ".webmanifest": "application/manifest+json",
        }.get(os.path.splitext(full)[1], "application/octet-stream")
        with open(full, "rb") as f:
            body = f.read()
        cache = "public, max-age=300" if ctype.startswith(("application/javascript", "text/css")) else "no-cache"
        return self._send(200, body, ctype, cache)


def main():
    load_index()
    print(f"sgp.fyi serving {_meta['transactions']:,} transactions / "
          f"{_meta['blocks']:,} blocks on {HOST}:{PORT}", flush=True)
    srv = ThreadingHTTPServer((HOST, PORT), Handler)
    srv.daemon_threads = True
    srv.serve_forever()


if __name__ == "__main__":
    main()
