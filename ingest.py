#!/usr/bin/env python3
"""Build the sgp.fyi SQLite database from the raw data.gov.sg / OneMap dumps.

Inputs  (data/raw/*.csv, data/raw/buildings.json)
Output  (data/sgpfyi.db)

Run monthly by sgpfyi-ingest.timer, or by hand:  python3 ingest.py
"""
import csv
import json
import os
import re
import sqlite3
import statistics
import sys
import time
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
RAW = os.path.join(HERE, "data", "raw")
DB = os.path.join(HERE, "data", "sgpfyi.db")

RESALE_FILES = [
    "resale_2000_2012.csv",
    "resale_2012_2014.csv",
    "resale_2015_2016.csv",
    "resale_2017_now.csv",
]
BUILDINGS = "buildings.json"

SQM_TO_SQFT = 10.7639

# HDB writes short street names, OneMap writes long ones. Expand for matching.
ABBREV = {
    "AVE": "AVENUE", "AV": "AVENUE", "ST": "STREET", "RD": "ROAD", "DR": "DRIVE",
    "CRES": "CRESCENT", "CL": "CLOSE", "PK": "PARK", "JLN": "JALAN", "BT": "BUKIT",
    "NTH": "NORTH", "STH": "SOUTH", "CTRL": "CENTRAL", "C'WEALTH": "COMMONWEALTH",
    "COMMONWEALTH": "COMMONWEALTH", "PL": "PLACE", "TER": "TERRACE", "LK": "LINK",
    "GDNS": "GARDENS", "HTS": "HEIGHTS", "KG": "KAMPONG", "LOR": "LORONG",
    "SG": "SINGAPORE", "UPP": "UPPER", "LWR": "LOWER", "IND": "INDUSTRIAL",
    "IND'L": "INDUSTRIAL", "MT": "MOUNT", "FT": "FORT", "EXT": "EXTENSION",
    "CENTRE": "CENTRE", "CENTER": "CENTRE", "BDR": "BOUNDARY", "TWR": "TOWER",
    "CONDO": "CONDOMINIUM", "APT": "APARTMENT", "BLK": "BLOCK",
}


def norm_street(s: str) -> str:
    """Uppercase, strip punctuation, expand abbreviations -> canonical OneMap spelling."""
    s = (s or "").upper().replace(".", " ").replace("'", "'")
    s = re.sub(r"[^A-Z0-9' ]+", " ", s)
    words = [w for w in s.split() if w]
    return " ".join(ABBREV.get(w, w) for w in words)


def norm_street_raw(s: str) -> str:
    """Uppercase + strip punctuation, but keep HDB's own abbreviations (ST, AVE...)."""
    s = (s or "").upper().replace(".", " ")
    s = re.sub(r"[^A-Z0-9' ]+", " ", s)
    return " ".join(s.split())


def norm_block(b) -> str:
    return (b or "").strip().upper()


def parse_storey(s: str):
    m = re.findall(r"\d+", s or "")
    if len(m) >= 2:
        return int(m[0]), int(m[1])
    if len(m) == 1:
        return int(m[0]), int(m[0])
    return None, None


def parse_remaining_lease(s: str, lease_year):
    """Normalise '61 years 04 months' / '70' / '' to years (float)."""
    s = (s or "").strip()
    if s:
        y = re.search(r"(\d+)\s*year", s)
        mo = re.search(r"(\d+)\s*month", s)
        if y:
            return int(y.group(1)) + (int(mo.group(1)) / 12 if mo else 0)
        if re.fullmatch(r"\d+", s):
            return float(s)
    if lease_year:
        # fall back: 99-year lease from commencement
        return max(0.0, 99 - (datetime.now().year - int(lease_year)))
    return None


def load_buildings(path):
    """(block, canonical street) -> dict(postal, lat, lon).

    OneMap dumps contain several buildings per block; keep the first with a postal.
    """
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    out = {}
    for b in data:
        blk = norm_block(b.get("BLK_NO"))
        road = b.get("ROAD_NAME") or ""
        rd = norm_street(road)
        rdraw = norm_street_raw(road)
        if not blk or not rd:
            continue
        lat, lon = b.get("LATITUDE"), b.get("LONGITUDE") or b.get("LONGTITUDE")
        postal = (b.get("POSTAL") or "").strip()
        rec = {
            "postal": postal if re.fullmatch(r"\d{6}", postal) else "",
            "lat": float(lat) if lat else None,
            "lon": float(lon) if lon else None,
        }
        # index under every spelling we might be asked for
        for key in {(blk, rd), (blk, rdraw)}:
            cur = out.get(key)
            if cur is None:
                out[key] = dict(rec)
            elif not cur["postal"] and rec["postal"]:
                cur["postal"] = rec["postal"]
    return out


def main():
    t0 = time.time()
    os.makedirs(os.path.dirname(DB), exist_ok=True)
    tmp_db = DB + ".tmp"
    if os.path.exists(tmp_db):
        os.remove(tmp_db)

    print("loading buildings ...", flush=True)
    bpath = os.path.join(RAW, BUILDINGS)
    buildings = load_buildings(bpath) if os.path.exists(bpath) else {}
    print(f"  {len(buildings):,} building blocks", flush=True)

    con = sqlite3.connect(tmp_db)
    con.executescript(
        """
        PRAGMA journal_mode=OFF;
        PRAGMA synchronous=OFF;
        CREATE TABLE tx (
            month TEXT, town TEXT, flat_type TEXT, block TEXT, street TEXT,
            storey_lo INT, storey_hi INT, storey_range TEXT,
            area REAL, model TEXT, lease_year INT, remaining REAL,
            price INT, psf REAL, addr_key TEXT
        );
        CREATE TABLE blocks (
            addr_key TEXT PRIMARY KEY, block TEXT, street TEXT, town TEXT,
            postal TEXT, lat REAL, lon REAL, lease_year INT,
            n_tx INT, first_month TEXT, last_month TEXT, with_coords INT
        );
        """
    )

    rows = []
    seen = set()
    stats = {}
    for fn in RESALE_FILES:
        path = os.path.join(RAW, fn)
        if not os.path.exists(path):
            print(f"  !! missing {fn}", flush=True)
            continue
        n = 0
        with open(path, newline="", encoding="utf-8-sig") as f:
            for r in csv.DictReader(f):
                month = (r.get("month") or "").strip()
                block = norm_block(r.get("block"))
                street = (r.get("street_name") or "").strip().upper()
                if not month or not block or not street:
                    continue
                # the 2012-2014 and 2017+ files overlap the boundary months
                sig = (month, block, street, r.get("flat_type"), r.get("storey_range"),
                       r.get("floor_area_sqm"), r.get("resale_price"))
                if sig in seen:
                    continue
                seen.add(sig)
                try:
                    area = float(r.get("floor_area_sqm") or 0) or None
                    price = int(float(r.get("resale_price") or 0)) or None
                    lease_year = int(float(r.get("lease_commence_date") or 0)) or None
                except ValueError:
                    continue
                if not area or not price:
                    continue
                lo, hi = parse_storey(r.get("storey_range"))
                rem = parse_remaining_lease(r.get("remaining_lease"), lease_year)
                psf = round(price / (area * SQM_TO_SQFT), 1)
                rows.append(
                    (
                        month,
                        (r.get("town") or "").strip().upper(),
                        (r.get("flat_type") or "").strip().upper(),
                        block,
                        street,
                        lo,
                        hi,
                        (r.get("storey_range") or "").strip(),
                        area,
                        (r.get("flat_model") or "").strip(),
                        lease_year,
                        round(rem, 2) if rem is not None else None,
                        price,
                        psf,
                        f"{block}|{norm_street(street)}",
                    )
                )
                n += 1
        stats[fn] = n
        print(f"  {fn:24s} {n:>8,} rows", flush=True)

    print(f"total {len(rows):,} transactions", flush=True)

    con.executemany("INSERT INTO tx VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)

    # ---- blocks index ----
    blocks = {}
    for (month, town, _ft, block, street, _lo, _hi, _sr, _area, _model,
         lease_year, _rem, _price, _psf, key) in rows:
        b = blocks.get(key)
        if b is None:
            b = blocks[key] = {
                "block": block, "street": street, "town": town,
                "lease_year": lease_year, "n": 0,
                "first": month, "last": month,
            }
        b["n"] += 1
        if month < b["first"]:
            b["first"] = month
        if month > b["last"]:
            b["last"] = month
        if lease_year and (not b["lease_year"] or lease_year < b["lease_year"]):
            b["lease_year"] = lease_year
        if town and not b["town"]:
            b["town"] = town

    block_rows = []
    matched = 0
    for key, b in blocks.items():
        street = key.split("|", 1)[1]
        geo = (buildings.get((b["block"], norm_street_raw(street)))
               or buildings.get((b["block"], street)))
        block_rows.append(
            (
                key, b["block"], b["street"], b["town"],
                (geo or {}).get("postal", ""),
                (geo or {}).get("lat"),
                (geo or {}).get("lon"),
                b["lease_year"], b["n"], b["first"], b["last"],
                1 if geo and geo.get("lat") else 0,
            )
        )
        if geo and geo.get("lat"):
            matched += 1

    con.executemany("INSERT INTO blocks VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", block_rows)
    print(f"blocks: {len(block_rows):,}  (with coords: {matched:,} "
          f"= {100*matched/max(1,len(block_rows)):.1f}%)", flush=True)

    print("indexing ...", flush=True)
    con.executescript(
        """
        CREATE INDEX idx_tx_addr  ON tx(addr_key, month);
        CREATE INDEX idx_tx_street ON tx(street, block);
        CREATE INDEX idx_tx_town  ON tx(town, month);
        CREATE INDEX idx_tx_month ON tx(month);
        CREATE INDEX idx_blocks_street ON blocks(street);
        CREATE INDEX idx_blocks_town ON blocks(town);
        """
    )
    con.commit()
    con.execute("VACUUM")
    con.close()
    os.replace(tmp_db, DB)

    meta = {
        "built_at": datetime.now().isoformat(timespec="seconds"),
        "transactions": len(rows),
        "blocks": len(block_rows),
        "blocks_with_coords": matched,
        "first_month": min(r[0] for r in rows),
        "last_month": max(r[0] for r in rows),
        "files": stats,
    }
    with open(os.path.join(HERE, "data", "meta.json"), "w") as f:
        json.dump(meta, f, indent=2)
    print(json.dumps(meta, indent=2))
    print(f"done in {time.time()-t0:.1f}s -> {DB}", flush=True)


if __name__ == "__main__":
    sys.exit(main())
