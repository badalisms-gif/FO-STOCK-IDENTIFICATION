"""Research only. Run from repository root: python -m research_v3.collect.
Current F&O universe, not historical membership. No fabricated candles."""
import os
import json
import gzip
import hashlib
import time
from datetime import timedelta
from pathlib import Path
from intraday_history_engine import fetch_candles, validate_candles, atomic_write
from stock_intelligence_engine import get_universe, now

OUT = Path("research_output")
def main():
    token = os.environ["UPSTOX_ACCESS_TOKEN"].strip()
    if not token:
        raise RuntimeError("Missing token")
    universe = get_universe()
    if not universe:
        raise RuntimeError("Empty universe")
    end = now().date() - timedelta(days=1)
    start = end - timedelta(days=119)
    windows = []
    cursor = start
    while cursor <= end:
        last = min(end, cursor + timedelta(days=26))
        windows.append((cursor, last))
        cursor = last + timedelta(days=1)
    report = {"universe_type": "current membership; survivorship bias",
              "requested_start": str(start), "requested_end": str(end),
              "universe": len(universe), "stocks": {}, "errors": []}
    for symbol, instrument in sorted(universe.items()):
        sessions = {}
        for first, last in windows:
            try:
                days = [first + timedelta(days=i) for i in range((last-first).days+1)]
                raw = fetch_candles(token, instrument, first, last)
                sessions.update(validate_candles(raw, days))
            except Exception as exc:
                report["errors"].append({"symbol": symbol, "start": str(first),
                                         "error_type": type(exc).__name__})
            time.sleep(0.5)
        dates = sorted(sessions)[-60:]
        data = {"symbol": symbol, "instrument": instrument,
                "sessions": {str(d): sessions[d] for d in dates}}
        blob = gzip.compress(json.dumps(data, allow_nan=False, separators=(",", ":")).encode())
        name = hashlib.sha256(instrument.encode()).hexdigest() + ".json.gz"
        atomic_write(OUT / name, blob)
        report["stocks"][symbol] = {"sessions": len(dates), "file": name,
            "dates": [str(d) for d in dates], "sha256": hashlib.sha256(blob).hexdigest()}
        atomic_write(OUT / "manifest.json", json.dumps(report, indent=2).encode())
        print(symbol, len(dates), flush=True)
    report["complete_stocks"] = sum(v["sessions"] == 60 for v in report["stocks"].values())
    report["status"] = "COMPLETE" if report["complete_stocks"] == len(universe) else "PARTIAL"
    atomic_write(OUT / "manifest.json", json.dumps(report, indent=2).encode())
    if report["status"] != "COMPLETE":
        raise SystemExit(1)

if __name__ == "__main__":
    main()
