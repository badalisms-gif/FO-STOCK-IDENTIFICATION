
"""
F&O HISTORICAL INTRADAY COLLECTOR
Version 2.5.1

Historical minute candles.
Full F&O Universe.
Verified session dates.
Permanent historical baseline archive.
No fabricated data.
No automatic orders.
"""

import os
import json
import math
import time
import gzip
import hashlib
import statistics

from datetime import datetime, timedelta
from urllib.parse import quote

from stock_intelligence_engine import (
    ROOT,
    UP,
    now,
    get_json,
    get_universe,
    parse_timestamp
)


VERSION = "2.5.1"

MIN_SESSIONS = 5
MAX_SESSIONS = 20

OUTPUT = ROOT / "intraday_baseline"


# =====================================
# NUMERIC VALIDATION
# =====================================

def number(value):

    try:

        value = float(value)

        if math.isfinite(value):

            return value

    except (TypeError, ValueError):

        pass

    return None


# =====================================
# SAFE FILE STORAGE
# =====================================

def atomic_write(path, content):

    path.parent.mkdir(
        parents=True,
        exist_ok=True
    )

    temp = path.with_suffix(
        path.suffix + ".tmp"
    )

    temp.write_bytes(content)

    temp.replace(path)


# =====================================
# VERIFIED HISTORICAL SESSION DATES
# =====================================

def trading_dates():

    folder = ROOT / "cash_bhavcopy"

    dates = []

    for path in folder.glob("*/latest.csv"):

        try:

            day = datetime.strptime(
                path.parent.name,
                "%Y-%m-%d"
            ).date()

            if day >= now().date():

                continue

            metadata = json.loads(

                (
                    path.parent / "metadata.json"
                ).read_text()

            )

            digest = hashlib.sha256(

                path.read_bytes()

            ).hexdigest()

            if metadata.get("sha256") != digest:

                continue

            if str(

                metadata.get("report_date")

            ) != day.isoformat():

                continue

            dates.append(day)

        except Exception:

            continue

    return sorted(

        set(dates)

    )[-MAX_SESSIONS:]


# =====================================
# UPSTOX HISTORICAL CANDLE API
# =====================================

def fetch_candles(
    token,
    instrument,
    first,
    last
):

    key = quote(
        instrument,
        safe=""
    )

    url = (

        UP

        + "/v3/historical-candle/"

        + key

        + "/minutes/1/"

        + last.isoformat()

        + "/"

        + first.isoformat()

    )

    payload = get_json(

        url,

        token=token

    )

    if payload.get("status") != "success":

        raise ValueError(

            "Historical API unsuccessful"

        )

    candles = (

        payload.get("data") or {}

    ).get("candles")

    if not isinstance(candles, list):

        raise ValueError(

            "Invalid candle response"

        )

    return candles


# =====================================
# CANDLE DATA QUALITY VALIDATION
# =====================================

def validate_candles(candles, dates):

    allowed = set(dates)

    sessions = {}

    invalid_dates = set()

    for candle in candles:

        if (

            not isinstance(candle, list)

            or len(candle) < 6

        ):

            continue

        try:

            stamp = parse_timestamp(
                candle[0]
            )

            if stamp is None:

                continue

            if stamp.tzinfo is None:

                continue

            stamp = stamp.astimezone(
                now().tzinfo
            )

        except Exception:

            continue

        day = stamp.date()

        if day not in allowed:

            continue

        minute = (

            stamp.hour * 60

            + stamp.minute

        )

        if not 555 <= minute < 930:

            continue

        o, h, l, c, v = [

            number(x)

            for x in candle[1:6]

        ]

        if (

            any(

                x is None

                for x in [o, h, l, c, v]

            )

            or min(o, h, l, c) <= 0

            or v < 0

            or h < max(o, l, c)

            or l > min(o, h, c)

        ):

            invalid_dates.add(day)

            continue

        session = sessions.setdefault(

            day, {}

        )

        if minute in session:

            invalid_dates.add(day)

            continue

        session[minute] = (

            o, h, l, c, v

        )

    valid = {}

    expected = set(

        range(555, 930)

    )

    for day, records in sessions.items():

        if day in invalid_dates:

            continue

        # Require all 375 normal-session
        # minute candles.
        # Missing candles are not invented.

        if set(records) != expected:

            continue

        valid[day] = records

    return valid


# =====================================
# SAME-TIME HISTORICAL BASELINE
# =====================================

def build_baseline(sessions):

    by_minute = {}

    for day, records in sessions.items():

        cumulative_volume = 0.0

        cumulative_turnover = 0.0

        for minute in sorted(records):

            o, h, l, c, v = records[minute]

            # Estimated traded average price.
            # This is not exact exchange turnover.

            typical_price = (

                o + h + l + c

            ) / 4

            cumulative_volume += v

            cumulative_turnover += (

                typical_price * v

            )

            # Compare completed candles only.

            completed_minute = minute + 1

            by_minute.setdefault(

                completed_minute, []

            ).append((

                cumulative_volume,

                cumulative_turnover

            ))

    result = {}

    for minute, records in by_minute.items():

        if len(records) < MIN_SESSIONS:

            continue

        volume = statistics.median(

            x[0] for x in records

        )

        turnover = statistics.median(

            x[1] for x in records

        )

        if volume <= 0 or turnover <= 0:

            continue

        result[str(minute)] = {

            "days": len(records),

            "volume": volume,

            "turnover": turnover

        }

    return result


# =====================================
# MASTER HISTORICAL COLLECTION
# =====================================

def main():

    token = os.environ.get(

        "UPSTOX_ACCESS_TOKEN",

        ""

    ).strip()

    if not token:

        raise RuntimeError(

            "Upstox token missing"

        )

    universe = get_universe()

    dates = trading_dates()

    if not universe:

        raise RuntimeError(

            "F&O universe unavailable"

        )

    if len(dates) < MIN_SESSIONS:

        raise RuntimeError(

            "Insufficient verified report dates"

        )

    started = now()

    baselines = {}

    errors = []

    coverage = []

    total = len(universe)

    attempted = 0

    print(

        "HISTORICAL INTRADAY COLLECTOR V"

        + VERSION,

        flush=True

    )

    print(

        f"F&O Universe: {total}",

        flush=True

    )

    print(

        f"Historical Sessions: {len(dates)}",

        flush=True

    )

    for index, (

        symbol, instrument

    ) in enumerate(

        sorted(universe.items()), 1

    ):

        attempted += 1

        try:

            candles = fetch_candles(

                token,

                instrument,

                dates[0],

                dates[-1]

            )

            sessions = validate_candles(

                candles,

                dates

            )

            baseline = build_baseline(

                sessions

            )

            if baseline:

                baselines[symbol] = baseline

            coverage.append({

                "Symbol": symbol,

                "Candles Received": len(candles),

                "Valid Sessions": len(sessions),

                "Baseline Ready": bool(baseline)

            })

            print(

                f"{index}/{total} "

                f"{symbol}: "

                f"{len(sessions)} valid sessions",

                flush=True

            )

        except Exception as error:

            message = str(error).replace(

                token,

                "[REDACTED]"

            )

            errors.append({

                "Symbol": symbol,

                "Error": message[:300]

            })

            print(

                f"{index}/{total} "

                f"{symbol}: FAILED",

                flush=True

            )

            # Stop early if every initial
            # request fails.
            # Prevent unnecessary API requests.

            if (

                index >= 5

                and len(errors) == index

            ):

                print(

                    "Systematic API failure detected.",

                    flush=True

                )

                break

        time.sleep(0.5)

    report = {

        "Version": VERSION,

        "Universe": total,

        "Attempted Stocks": attempted,

        "Unattempted Stocks":

            total - attempted,

        "Requested Sessions": [

            x.isoformat()

            for x in dates

        ],

        "Baseline Ready Stocks":

            len(baselines),

        "Minimum Required Sessions":

            MIN_SESSIONS,

        "Coverage %": round(

            len(baselines) / total * 100,

            2

        ),

        "Turnover Type":

            "ESTIMATED FROM MINUTE OHLC",

        "Started At":

            started.isoformat(),

        "Completed At":

            now().isoformat(),

        "Independent NSE Cross Check":

            False,

        "Status": (

            "COMPLETE"

            if len(baselines) == total

            else "PARTIAL"

            if baselines

            else "FAILED"

        ),

        "Errors": errors,

        "Stock Coverage": coverage

    }

    OUTPUT.mkdir(

        parents=True,

        exist_ok=True

    )

    # Save only validated baseline records.

    if baselines:

        data = {

            "Version": VERSION,

            "Latest Session":

                dates[-1].isoformat(),

            "Generated At":

                now().isoformat(),

            "Turnover Estimated": True,

            "Stocks": baselines

        }

        raw = json.dumps(

            data,

            separators=(",", ":"),

            allow_nan=False

        ).encode("utf-8")

        compressed = gzip.compress(raw)

        report["Archive SHA256"] = (

            hashlib.sha256(

                compressed

            ).hexdigest()

        )

        archive_path = (

            OUTPUT

            / dates[-1].isoformat()

            / "baseline.json.gz"

        )

        atomic_write(

            archive_path,

            compressed

        )

        report["Archive Path"] = str(

            archive_path

        )

    # Always preserve collection diagnostics.

    atomic_write(

        OUTPUT / "latest_diagnostics.json",

        json.dumps(

            report,

            indent=2,

            allow_nan=False

        ).encode("utf-8")

    )

    print(

        json.dumps(

            report,

            indent=2

        ),

        flush=True

    )

    if not baselines:

        raise SystemExit(1)


if __name__ == "__main__":

    main()
