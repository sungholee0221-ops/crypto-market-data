"""Binance spot klines → parquet (1d, 4h, 15m) for the top USDT pairs.

Runs in GitHub Actions. Official Binance endpoints only, tried in order; the first that answers is used:
  1. https://data-api.binance.vision  (public market-data-only endpoint)
  2. https://api.binance.com
If none answers (e.g. HTTP 451 restricted location), the run writes probe.json and stops: nothing is circumvented.

Layout written to $OUT_DIR (published as the `data` branch):
  universe.csv            symbol, base, rank, quote_volume_24h_usdt, added (UTC date)
  1d/<SYMBOL>.parquet     open_time (UTC), open, high, low, close, volume, quote_volume, trades
  4h/<SYMBOL>.parquet
  15m/<SYMBOL>.parquet    last FIFTEEN_DAYS_BACK days only (size)
  meta.json, probe.json

Incremental: existing files from $PREV_DIR (the previous data branch, if any) are extended, not re-downloaded.
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import requests

BASES = ["https://data-api.binance.vision", "https://api.binance.com"]
OUT = Path(os.environ.get("OUT_DIR", "out"))
PREV = Path(os.environ.get("PREV_DIR", "prev"))
TOP_N = int(os.environ.get("TOP_N", "100"))
FIFTEEN_DAYS_BACK = int(os.environ.get("FIFTEEN_DAYS_BACK", "730"))
START = {"1d": "2017-08-01", "4h": "2017-08-01"}
INTERVAL_MS = {"1d": 86_400_000, "4h": 14_400_000, "15m": 900_000}
STABLE = {"USDC", "FDUSD", "TUSD", "BUSD", "DAI", "USDP", "USDD", "PYUSD", "EUR", "EURI", "AEUR", "GBP", "TRY", "BRL",
          "USD1", "XUSD", "USDE", "BFUSD", "RLUSD", "PAXG", "WBTC", "WBETH", "BNSOL"}
S = requests.Session()
S.headers["User-Agent"] = "crypto-market-data (GitHub Actions)"


def probe() -> dict:
    res = {}
    for b in BASES:
        try:
            r = S.get(f"{b}/api/v3/klines", params={"symbol": "BTCUSDT", "interval": "1d", "limit": 2}, timeout=20)
            res[b] = {"status": r.status_code, "body": r.text[:200]}
        except Exception as e:  # noqa: BLE001
            res[b] = {"status": None, "error": str(e)[:200]}
    return res


def get(base, path, params, tries=6):
    for k in range(tries):
        r = S.get(f"{base}{path}", params=params, timeout=30)
        if r.status_code == 200:
            used = int(r.headers.get("x-mbx-used-weight-1m", "0") or 0)
            if used > 4800:
                time.sleep(15)
            return r.json()
        if r.status_code in (418, 429):
            time.sleep(int(r.headers.get("Retry-After", "30")) + 1)
            continue
        if r.status_code >= 500:
            time.sleep(2 ** k)
            continue
        r.raise_for_status()
    raise RuntimeError(f"failed {path} {params}")


def universe(base) -> pd.DataFrame:
    info = get(base, "/api/v3/exchangeInfo", {"permissions": "SPOT"})
    ok = {s["symbol"]: s["baseAsset"] for s in info["symbols"]
          if s["quoteAsset"] == "USDT" and s["status"] == "TRADING" and s.get("isSpotTradingAllowed", True)}
    tick = get(base, "/api/v3/ticker/24hr", {})
    rows = []
    for t in tick:
        sym = t["symbol"]
        if sym not in ok:
            continue
        b = ok[sym]
        if b in STABLE or b.endswith(("UP", "DOWN", "BULL", "BEAR")):
            continue
        rows.append((sym, b, float(t["quoteVolume"])))
    df = pd.DataFrame(rows, columns=["symbol", "base", "quote_volume_24h_usdt"]).sort_values("quote_volume_24h_usdt", ascending=False)
    df = df.head(TOP_N).reset_index(drop=True)
    df["rank"] = df.index + 1
    return df


def klines(base, sym, interval, start_ms, end_ms) -> pd.DataFrame:
    rows, cur = [], start_ms
    while cur < end_ms:
        k = get(base, "/api/v3/klines", {"symbol": sym, "interval": interval, "startTime": cur, "endTime": end_ms, "limit": 1000})
        if not k:
            break
        rows += k
        nxt = k[-1][0] + INTERVAL_MS[interval]
        if nxt <= cur or len(k) < 1000:
            break
        cur = nxt
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows).iloc[:, [0, 1, 2, 3, 4, 5, 6, 7, 8]]
    df.columns = ["open_time", "open", "high", "low", "close", "volume", "close_time", "quote_volume", "trades"]
    df = df[df["close_time"] < end_ms]  # closed candles only
    out = pd.DataFrame({"open_time": pd.to_datetime(df["open_time"], unit="ms", utc=True)})
    dt = "float64" if interval != "15m" else "float32"  # 15m: float32 to keep the data branch small
    for c in ("open", "high", "low", "close", "volume", "quote_volume"):
        out[c] = df[c].astype(dt)
    out["trades"] = df["trades"].astype("int64")
    return out


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    pr = probe()
    (OUT / "probe.json").write_text(json.dumps({"at": datetime.now(timezone.utc).isoformat(), "result": pr}, indent=1))
    base = next((b for b, v in pr.items() if v.get("status") == 200), None)
    print("probe:", json.dumps(pr)[:600])
    if base is None:
        print("no Binance endpoint reachable from this runner; keeping previous data, no new bars")
        if (PREV / "meta.json").exists():  # republish previous data unchanged so the data branch is not emptied
            for f in PREV.rglob("*"):
                if f.is_file() and ".git" not in f.parts and f.name not in ("probe.json", "meta.json"):
                    dst = OUT / f.relative_to(PREV)
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(f, dst)
            m = json.loads((PREV / "meta.json").read_text())
            m["status"] = "stale_no_endpoint"
            (OUT / "meta.json").write_text(json.dumps(m, indent=1))
        else:
            (OUT / "meta.json").write_text(json.dumps({"status": "no_endpoint"}, indent=1))
        return 0
    now_ms = int(time.time() * 1000)
    # universe: keep previous members, add new top-N members (no removals → stable history)
    uni = universe(base)
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    prev_u = PREV / "universe.csv"
    if prev_u.exists():
        old = pd.read_csv(prev_u)
        new = uni[~uni.symbol.isin(old.symbol)].assign(added=today)
        u = pd.concat([old, new], ignore_index=True)
    else:
        u = uni.assign(added=today)
    u.to_csv(OUT / "universe.csv", index=False)
    stats = {}
    for iv in ("1d", "4h", "15m"):
        (OUT / iv).mkdir(exist_ok=True)
        n_rows = 0
        for sym in u.symbol:
            f_prev = PREV / iv / f"{sym}.parquet"
            old = pd.read_parquet(f_prev) if f_prev.exists() else pd.DataFrame()
            if iv == "15m":
                floor = pd.Timestamp(now_ms - FIFTEEN_DAYS_BACK * 86_400_000, unit="ms", tz="UTC")
            else:
                floor = pd.Timestamp(START[iv], tz="UTC")
            start = (old["open_time"].max() + pd.Timedelta(milliseconds=INTERVAL_MS[iv])) if len(old) else floor
            try:
                new = klines(base, sym, iv, int(start.timestamp() * 1000), now_ms)
            except Exception as e:  # noqa: BLE001
                print(f"{sym} {iv}: {e}")
                new = pd.DataFrame()
            df = pd.concat([old, new], ignore_index=True) if len(new) else old
            if len(df) == 0:
                continue
            df = df.drop_duplicates("open_time").sort_values("open_time")
            df = df[df["open_time"] >= floor].reset_index(drop=True)
            df.to_parquet(OUT / iv / f"{sym}.parquet", index=False, compression="zstd")
            n_rows += len(df)
        stats[iv] = n_rows
        print(iv, n_rows, flush=True)
    btc = pd.read_parquet(OUT / "1d" / "BTCUSDT.parquet")
    meta = {"status": "ok", "endpoint": base, "built_at": datetime.now(timezone.utc).isoformat(),
            "last_daily_close": str(btc["open_time"].max().date()), "symbols": int(len(u)), "rows": stats,
            "fifteen_min_days": FIFTEEN_DAYS_BACK}
    (OUT / "meta.json").write_text(json.dumps(meta, indent=1))
    print(json.dumps(meta))
    return 0


if __name__ == "__main__":
    sys.exit(main())
