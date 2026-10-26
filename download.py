#!/usr/bin/env python3
"""Download HDB resale-price + HDB building datasets from data.gov.sg.

data.gov.sg download flow (v1 open API):
  GET api-open.data.gov.sg/v1/public/api/datasets/{id}/initiate-download
  GET api-open.data.gov.sg/v1/public/api/datasets/{id}/poll-download   -> signed S3 url (1h)
"""
import json
import os
import sys
import time
import urllib.request

DATASETS = {
    # resale transactions
    "resale_2000_2012": "d_43f493c6c50d54243cc1eab0df142d6a",
    "resale_2012_2014": "d_2d5ff9ea31397b66239f245f57751537",
    "resale_2015_2016": "d_ea9ed51da2787afaf8e51f827c304208",
    "resale_2017_now": "d_8b84c4ee58e3cfc0ece0d773c8ca6abc",
}

# OneMap building dump (block <-> postal code <-> lat/lon), refreshed from the
# community mirror of OneMap's own data.
BUILDINGS_URL = ("https://raw.githubusercontent.com/xkjyeah/"
                 "singapore-postal-codes/master/buildings.json")

RAW_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "raw")
UA = "sgpfyi-rebuild/1.0 (homelab; contact: local)"


def _get(url, timeout=60, tries=4):
    last = None
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read()
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(3 * (i + 1))
    raise RuntimeError(f"GET failed {url}: {last}")


def fetch_dataset(name, dsid, dest):
    base = f"https://api-open.data.gov.sg/v1/public/api/datasets/{dsid}"
    _get(f"{base}/initiate-download")
    url = None
    for _ in range(30):
        time.sleep(2)
        payload = json.loads(_get(f"{base}/poll-download"))
        data = payload.get("data") or {}
        if data.get("status") == "DOWNLOAD_SUCCESS" and data.get("url"):
            url = data["url"]
            break
    if not url:
        raise RuntimeError(f"{name}: download never became ready: {payload}")
    tmp = dest + ".part"
    with urllib.request.urlopen(
        urllib.request.Request(url, headers={"User-Agent": UA}), timeout=300
    ) as r, open(tmp, "wb") as f:
        while True:
            chunk = r.read(1 << 20)
            if not chunk:
                break
            f.write(chunk)
    os.replace(tmp, dest)
    size = os.path.getsize(dest)
    print(f"[ok] {name:24s} {size/1e6:8.1f} MB  {dest}", flush=True)
    return size


def fetch_buildings(dest):
    tmp = dest + ".part"
    with urllib.request.urlopen(
        urllib.request.Request(BUILDINGS_URL, headers={"User-Agent": UA}), timeout=600
    ) as r, open(tmp, "wb") as f:
        while True:
            chunk = r.read(1 << 20)
            if not chunk:
                break
            f.write(chunk)
    os.replace(tmp, dest)
    print(f"[ok] {'buildings':24s} {os.path.getsize(dest)/1e6:8.1f} MB  {dest}", flush=True)


def main():
    os.makedirs(RAW_DIR, exist_ok=True)
    only = sys.argv[1:] or list(DATASETS)
    for name in only:
        dsid = DATASETS[name]
        dest = os.path.join(RAW_DIR, f"{name}.csv")
        fetch_dataset(name, dsid, dest)
        time.sleep(2)
    if not sys.argv[1:]:
        fetch_buildings(os.path.join(RAW_DIR, "buildings.json"))


if __name__ == "__main__":
    main()
