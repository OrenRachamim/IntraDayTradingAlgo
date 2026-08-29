"""Download and prepare all data needed for backtesting.

1. paperswithbacktest/Stocks-Daily-Price (Hugging Face) -> data/raw/*.parquet
   NOTE: check the dataset's license/terms on Hugging Face before commercial use.
2. Repartition into per-symbol files       -> data/stocks/{SYMBOL}.parquet
3. Build a coverage index                  -> data/symbols_index.csv
4. SPY full history (Yahoo chart API)      -> data/benchmark/SPY.parquet

Usage: python scripts/prepare_data.py [--skip-download]
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.request
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
HF_BASE = ("https://huggingface.co/datasets/paperswithbacktest/"
           "Stocks-Daily-Price/resolve/main/data")
SHARDS = [f"train-0000{i}-of-00004.parquet" for i in range(4)]


def download_shards() -> None:
    raw = DATA / "raw"
    raw.mkdir(parents=True, exist_ok=True)
    for shard in SHARDS:
        dst = raw / shard
        if dst.exists() and dst.stat().st_size > 1e6:
            print(f"exists: {shard}")
            continue
        print(f"downloading {shard} ...")
        urllib.request.urlretrieve(f"{HF_BASE}/{shard}", dst)


def repartition() -> None:
    import pyarrow.parquet as pq
    import pyarrow as pa
    tabs = [pq.read_table(DATA / "raw" / s) for s in SHARDS]
    df = pa.concat_tables(tabs).to_pandas()
    print(f"loaded {len(df):,} rows")
    df["date"] = pd.to_datetime(df["date"])
    df = df.sort_values(["symbol", "date"])
    outdir = DATA / "stocks"
    outdir.mkdir(parents=True, exist_ok=True)
    meta = []
    for sym, g in df.groupby("symbol", sort=True):
        g = g.drop(columns=["symbol"]).drop_duplicates(subset="date").reset_index(drop=True)
        g.to_parquet(outdir / f"{sym.replace('/', '_')}.parquet", index=False)
        meta.append((sym, str(g.date.iloc[0].date()), str(g.date.iloc[-1].date()),
                     len(g), float(g.adj_close.iloc[-1]) if g.adj_close.notna().any() else None))
    pd.DataFrame(meta, columns=["symbol", "first_date", "last_date", "rows",
                                "last_adj_close"]).to_csv(DATA / "symbols_index.csv", index=False)
    print(f"wrote {len(meta)} symbol files")


def download_spy() -> None:
    (DATA / "benchmark").mkdir(parents=True, exist_ok=True)
    url = (f"https://query1.finance.yahoo.com/v8/finance/chart/SPY"
           f"?period1=0&period2={int(time.time())}&interval=1d")
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    data = json.load(urllib.request.urlopen(req, timeout=120))
    r = data["chart"]["result"][0]
    q = r["indicators"]["quote"][0]
    df = pd.DataFrame({
        "date": pd.to_datetime(r["timestamp"], unit="s", utc=True)
                  .tz_convert("America/New_York").normalize().tz_localize(None),
        "open": q["open"], "high": q["high"], "low": q["low"], "close": q["close"],
        "volume": q["volume"],
        "adj_close": r["indicators"]["adjclose"][0]["adjclose"],
    }).dropna().drop_duplicates(subset="date").sort_values("date").reset_index(drop=True)
    df.to_parquet(DATA / "benchmark" / "SPY.parquet", index=False)
    print(f"SPY: {len(df)} rows  {df.date.min().date()} -> {df.date.max().date()}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--skip-download", action="store_true")
    ap.add_argument("--if-missing", action="store_true",
                    help="do nothing when the prepared dataset already exists (CI cache)")
    args = ap.parse_args()
    if args.if_missing and (DATA / "symbols_index.csv").exists() \
            and (DATA / "stocks").is_dir():
        print("dataset already prepared; nothing to do")
        sys.exit(0)
    if not args.skip_download:
        download_shards()
        download_spy()
    repartition()
    print("done", file=sys.stderr)
