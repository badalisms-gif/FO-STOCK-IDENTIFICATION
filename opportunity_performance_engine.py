
"""
OPPORTUNITY PERFORMANCE INTELLIGENCE
Version 2.9.0

Verified V2.6 archive replay.
Directional performance research.
No automatic orders.
No option strike selection.
"""

import json
import gzip
import math
import hashlib
import os

from pathlib import Path
from datetime import datetime, date, timedelta
from zoneinfo import ZoneInfo
from bisect import bisect_left

import pandas as pd

import integrated_live_engine as core


VERSION = "2.9.0"

IST = ZoneInfo("Asia/Kolkata")

ROOT = Path(__file__).resolve().parent

SOURCE = ROOT / "historical_data" / "live_market"

OUTPUT = (
    ROOT / "historical_data"
    / "opportunity_performance"
)

HORIZONS = [5, 15, 30, 60]

MIN_COVERAGE = 0.90
MAX_QUOTE_AGE = 60
MAX_CONFIRMATION_GAP = 120
MAX_HORIZON_DELAY = 90
MAX_PATH_GAP = 120

CATEGORIES = {
    "PRIORITY BULLISH",
    "PRIORITY BEARISH",
    "EMERGING WATCH",
    "EARLY ACTIVITY WATCH"
}


def now():
    return datetime.now(IST)


def number(value):

    try:
        value = float(value)

        if math.isfinite(value):
            return value

    except (ValueError, TypeError):
        pass

    return None


def truth(value):

    return str(value).strip().lower() in (
        "true", "1", "yes"
    )


def stamp(value):

    try:

        result = datetime.fromisoformat(
            str(value).replace("Z", "+00:00")
        )

        if result.tzinfo is None:
            return None

        return result.astimezone(IST)

    except Exception:
        return None


def atomic_write(path, raw):

    path.parent.mkdir(
        parents=True, exist_ok=True
    )

    temporary = path.with_suffix(
        path.suffix + ".tmp"
    )

    temporary.write_bytes(raw)
    temporary.replace(path)


def session_time(day, hour, minute):

    return datetime.combine(
        date.fromisoformat(day),
        datetime.min.time(),
        tzinfo=IST
    ).replace(
        hour=hour,
        minute=minute
    )


# =====================================
# VERIFIED V2.6 ARCHIVE READER
# =====================================

def load_scans(day):

    date.fromisoformat(day)

    folder = SOURCE / day

    if not folder.exists():
        raise ValueError(
            "No permanent live archive for " + day
        )

    scans = []
    accepted = 0
    rejected = 0
    seen_hashes = set()

    for path in sorted(
        folder.rglob("*.json.gz.metadata.json")
    ):

        try:

            meta = json.loads(
                path.read_text()
            )

            if meta.get("Version") != "2.6.0":
                raise ValueError("Version mismatch")

            if meta.get("Session Date") != day:
                raise ValueError("Session mismatch")

            name = meta["Archive"]

            if Path(name).name != name:
                raise ValueError("Invalid archive name")

            raw = (
                path.parent / name
            ).read_bytes()

            digest = hashlib.sha256(
                raw
            ).hexdigest()

            if digest != meta["SHA256"]:
                raise ValueError("Checksum mismatch")

            if digest in seen_hashes:
                continue

            records = json.loads(
                gzip.decompress(raw)
            )

            if not isinstance(records, list):
                raise ValueError("Invalid archive")

            if len(records) != meta["Scans"]:
                raise ValueError("Scan count mismatch")

            seen_hashes.add(digest)
            accepted += 1

            scans.extend(records)

        except Exception:
            rejected += 1

    if not accepted:
        raise ValueError(
            "No verified live archives"
        )

    unique = {}

    for scan in scans:

        completed = stamp(
            scan.get("Completed At")
        )

        if completed is None:
            continue

        if completed.date().isoformat() != day:
            continue

        if not (
            session_time(day, 9, 15)
            <= completed
            <= session_time(day, 15, 31)
        ):
            continue

        key = completed.isoformat()

        if key not in unique:
            unique[key] = scan

        elif unique[key] != scan:

            raise ValueError(
                "Conflicting duplicate scan"
            )

    result = sorted(
        unique.values(),
        key=lambda row: stamp(
            row["Completed At"]
        )
    )

    return result, {

        "Verified Archives": accepted,

        "Rejected Archives": rejected,

        "Total Archived Scans": len(result),

        "Archive Integrity Checked": True

    }


# =====================================
# HISTORICAL QUOTE VALIDATION
# =====================================

def valid_quotes(scan, day):

    completed = stamp(
        scan.get("Completed At")
    )

    if (
        completed is None
        or scan.get("Status") != "LIVE"
        or scan.get("Session Date") != day
    ):
        return {}, 0.0

    summary = scan.get("Summary", {})

    universe = number(
        summary.get("universe_size")
    )

    if (
        universe is None
        or universe <= 0
        or not universe.is_integer()
    ):
        return {}, 0.0

    rows = scan.get("Stocks", [])

    if not isinstance(rows, list):
        return {}, 0.0

    if len(rows) > universe:
        return {}, 0.0

    quotes = {}
    seen = set()

    for row in rows:

        symbol = str(
            row.get("Symbol", "")
        ).strip().upper()

        if not symbol or symbol in seen:
            return {}, 0.0

        seen.add(symbol)

        quote = stamp(
            row.get("Quote Time")
        )

        price = number(row.get("LTP"))

        if (
            quote is None
            or price is None
            or price <= 0
        ):
            continue

        age = (
            completed - quote
        ).total_seconds()

        if not (
            quote.date().isoformat() == day
            and 0 <= age <= MAX_QUOTE_AGE
            and session_time(day, 9, 15)
                <= quote
                < session_time(day, 15, 30)
            and str(
                row.get("Status", "")
            ).upper() == "FRESH"
        ):
            continue

        quotes[symbol] = dict(row)

    return quotes, len(quotes) / universe


# =====================================
# REPLAY IDENTIFICATION
# =====================================

def replay(day, scans):

    observations = {}
    events = []

    confirmation = {}
    active = {}

    eligible_scans = 0

    for scan in scans:

        completed = stamp(
            scan.get("Completed At")
        )

        quotes, coverage = valid_quotes(
            scan, day
        )

        # Valid individual prices remain
        # useful for outcome measurement.

        for symbol, row in quotes.items():

            quote = stamp(
                row["Quote Time"]
            )

            price = number(row["LTP"])

            series = observations.setdefault(
                symbol, {}
            )

            if quote in series:

                if series[quote] != price:

                    raise ValueError(
                        "Conflicting quote: "
                        + symbol
                    )

            else:
                series[quote] = price

        if coverage < MIN_COVERAGE:

            confirmation = {}
            active = {}

            continue

        eligible_scans += 1

        summary = scan.get("Summary", {})

        baseline_coverage = number(
            summary.get(
                "Historical Live Coverage %"
            )
        )

        baseline_available = (

            summary.get(
                "Archive Hash Verified"
            ) is True

            and summary.get(
                "Historical Baseline Status"
            ) == "AVAILABLE"

            and baseline_coverage is not None

            and baseline_coverage >= 90

        )

        next_confirmation = {}
        next_active = {}

        for symbol, row in quotes.items():

            live = dict(row)

            live["Validated Fresh"] = True

            days = number(
                live.get(
                    "Intraday Baseline Days"
                )
            )

            if not (

                baseline_available

                and live.get(
                    "Historical Baseline Status"
                ) == "AVAILABLE"

                and days is not None

                and days >= 5

            ):

                live["Same Time RVOL"] = None

                live[
                    "Same Time Turnover Ratio"
                ] = None

                live[
                    "Intraday Baseline Days"
                ] = 0

            # Reuse existing stock logic.
            # No future or current-day
            # historical context is injected.

            result = core.evaluate_stock(
                live,
                {},
                {},
                coverage,
                True
            )

            category = result[
                "Live Category"
            ]

            direction = result[
                "Direction"
            ]

            quote = stamp(
                row["Quote Time"]
            )

            expiry = str(
                row.get("Futures Expiry", "")
            )

            if category == "CONFIRMATION PENDING":

                previous = confirmation.get(
                    symbol, {}
                )

                count = 1

                if previous:

                    elapsed = (
                        completed
                        - previous["Scan"]
                    ).total_seconds()

                    quote_gap = (
                        quote
                        - previous["Quote"]
                    ).total_seconds()

                    if (

                        previous["Direction"]
                        == direction

                        and previous["Expiry"]
                        == expiry

                        and 0 <= elapsed
                            <= MAX_CONFIRMATION_GAP

                        and 0 <= quote_gap
                            <= MAX_CONFIRMATION_GAP

                    ):

                        count = previous["Count"]

                        if quote_gap > 0:
                            count += 1

                next_confirmation[symbol] = {

                    "Direction": direction,

                    "Expiry": expiry,

                    "Scan": completed,

                    "Quote": quote,

                    "Count": count

                }

                if count >= 2:

                    category = (
                        "PRIORITY BULLISH"
                        if direction == "BULLISH"
                        else "PRIORITY BEARISH"
                    )

            if category not in CATEGORIES:
                continue

            key = (
                symbol,
                direction,
                category
            )

            previous_seen = active.get(key)

            next_active[key] = completed

            if previous_seen is not None:

                gap = (
                    completed - previous_seen
                ).total_seconds()

                if 0 <= gap <= MAX_CONFIRMATION_GAP:
                    continue

            events.append({

                "Session Date": day,

                "Symbol": symbol,

                "Category": category,

                "Direction": direction,

                "Identified At":
                    completed.isoformat(),

                "Reference Quote Time":
                    quote.isoformat(),

                "Reference Price":
                    number(row["LTP"]),

                "Coverage %":
                    round(coverage * 100, 2),

                "Evidence Groups":
                    result["Evidence Groups"],

                "Same Time RVOL":
                    number(
                        live.get("Same Time RVOL")
                    ),

                "Futures OI 5m %":
                    number(
                        live.get(
                            "Futures OI 5m Change %"
                        )
                    ),

                "Source":
                    "ARCHIVE REPLAY",

                "Actual V2.8 Signal": False

            })

        confirmation = next_confirmation
        active = next_active

    series = {

        symbol: sorted(values.items())

        for symbol, values
        in observations.items()

    }

    return events, series, eligible_scans


# =====================================
# PERFORMANCE CALCULATION
# =====================================

def directional_return(
    entry, price, direction
):

    if direction not in (
        "BULLISH", "BEARISH"
    ):
        return None

    sign = (
        1 if direction == "BULLISH"
        else -1
    )

    return round(
        sign * (price / entry - 1) * 100,
        6
    )


def outcome(event, observations):

    result = dict(event)

    identified = stamp(
        event["Identified At"]
    )

    entry = event["Reference Price"]

    direction = event["Direction"]

    day = event["Session Date"]

    closing = session_time(day, 15, 30)

    series = observations.get(
        event["Symbol"], []
    )

    times = [
        item[0] for item in series
    ]

    for minutes in HORIZONS:

        label = str(minutes) + "m"

        result[label + " Price"] = None

        result[label + " Return %"] = None

        result[label + " Outcome"] = (
            "MISSING DATA"
        )

        result[label + " Quote Time"] = None

        if direction not in (
            "BULLISH", "BEARISH"
        ):

            result[label + " Outcome"] = (
                "NO DIRECTION"
            )

            continue

        target = identified + timedelta(
            minutes=minutes
        )

        if target >= closing:

            result[label + " Outcome"] = (
                "HORIZON OUTSIDE SESSION"
            )

            continue

        index = bisect_left(
            times, target
        )

        if index >= len(series):
            continue

        observed, price = series[index]

        delay = (
            observed - target
        ).total_seconds()

        if delay > MAX_HORIZON_DELAY:
            continue

        change = directional_return(
            entry, price, direction
        )

        result[label + " Price"] = price

        result[label + " Quote Time"] = (
            observed.isoformat()
        )

        result[label + " Return %"] = change

        result[label + " Outcome"] = (

            "CORRECT" if change > 0

            else "INCORRECT" if change < 0

            else "FLAT"

        )

    # Use last observed near-close quote.
    # This is not the official NSE close.

    result["Near Close Price"] = None

    result["Near Close Return %"] = None

    result["Near Close Quote Time"] = None

    result["Near Close Outcome"] = (
        "MISSING DATA"
    )

    if direction not in (
        "BULLISH", "BEARISH"
    ):

        result["Near Close Outcome"] = (
            "NO DIRECTION"
        )

    elif series:

        observed, price = series[-1]

        if (

            observed > identified

            and 0 <= (
                closing - observed
            ).total_seconds() <= 120

        ):

            change = directional_return(
                entry, price, direction
            )

            result["Near Close Price"] = price

            result["Near Close Quote Time"] = (
                observed.isoformat()
            )

            result["Near Close Return %"] = change

            result["Near Close Outcome"] = (

                "CORRECT" if change > 0

                else "INCORRECT" if change < 0

                else "FLAT"

            )

    path = [

        (observed, price)

        for observed, price in series

        if identified < observed < closing

    ]

    returns = [

        directional_return(
            entry, price, direction
        )

        for _, price in path

    ]

    returns = [
        value for value in returns
        if value is not None
    ]

    result["Observed MFE %"] = (

        max([0.0] + returns)

        if returns else None

    )

    result["Observed MAE %"] = (

        min([0.0] + returns)

        if returns else None

    )

    result["Outcome Observations"] = len(path)

    path_times = (
        [identified]
        + [item[0] for item in path]
        + [closing]
    )

    maximum_gap = max(

        (

            (b - a).total_seconds()

            for a, b in zip(
                path_times,
                path_times[1:]
            )

        ),

        default=0

    )

    result["Maximum Path Gap Seconds"] = (
        maximum_gap
    )

    result["Path Coverage Sufficient"] = (

        bool(path)

        and maximum_gap <= MAX_PATH_GAP

    )

    return result


# =====================================
# PERFORMANCE SUMMARY
# =====================================

def summarize(frame):

    rows = []

    if frame.empty:
        return rows

    for category, group in frame.groupby(
        "Category"
    ):

        for label in [
            "5m", "15m", "30m",
            "60m", "Near Close"
        ]:

            values = group[
                label + " Outcome"
            ]

            correct = int(
                values.eq("CORRECT").sum()
            )

            incorrect = int(
                values.eq("INCORRECT").sum()
            )

            flat = int(
                values.eq("FLAT").sum()
            )

            measured = (
                correct + incorrect + flat
            )

            average = pd.to_numeric(

                group[label + " Return %"],

                errors="coerce"

            ).mean()

            rows.append({

                "Category": category,

                "Horizon": label,

                "Opportunities": len(group),

                "Measured": measured,

                "Correct": correct,

                "Incorrect": incorrect,

                "Flat": flat,

                "Unmeasured":
                    len(group) - measured,

                "Directional Accuracy %": (

                    round(
                        correct / measured * 100,
                        2
                    )

                    if measured else None

                ),

                "Average Directional Return %":
                    number(average)

            })

    return rows


# =====================================
# PERMANENT PERFORMANCE ARCHIVE
# =====================================

def build_performance(day):

    if now() < session_time(day, 15, 35):

        raise ValueError(
            "Wait until session completion"
        )

    scans, diagnostics = load_scans(day)

    events, observations, eligible = replay(
        day, scans
    )

    if not eligible:

        raise ValueError(
            "No sufficiently covered live scans"
        )

    results = [

        outcome(event, observations)

        for event in events

    ]

    frame = pd.DataFrame(results)

    report = {

        "Version": VERSION,

        "Session Date": day,

        "Generated At": now().isoformat(),

        "Status": "REPLAY COMPLETED",

        "Source": "VERIFIED V2.6 ARCHIVES",

        "Evaluation Type":
            "RETROSPECTIVE LIVE-ONLY REPLAY",

        "Actual V2.8 Performance": False,

        "Eligible Identification Scans":
            eligible,

        "Identified Opportunities":
            len(events),

        "Stocks With Valid Quotes":
            len(observations),

        "Official Closing Price Used":
            False,

        "Independent NSE Cross Check":
            False,

        "Prediction Accuracy Verified":
            False,

        "Trade Signal": "NOT GENERATED",

        "Diagnostics": diagnostics,

        "Performance Summary":
            summarize(frame)

    }

    payload = {

        "Version": VERSION,

        "Session Date": day,

        "Report": report,

        "Opportunities": results

    }

    raw = gzip.compress(

        json.dumps(

            payload,

            ensure_ascii=False,

            allow_nan=False,

            default=str

        ).encode("utf-8")

    )

    folder = OUTPUT / day

    digest = hashlib.sha256(
        raw
    ).hexdigest()

    filename = digest + ".json.gz"

    atomic_write(
        folder / filename,
        raw
    )

    atomic_write(

        folder / "metadata.json",

        json.dumps({

            "Version": VERSION,

            "Session Date": day,

            "SHA256": digest,

            "Archive": filename,

            "Records": len(results)

        }, indent=2).encode("utf-8")

    )

    print(

        json.dumps(
            report,
            indent=2,
            default=str
        ),

        flush=True

    )

    return frame, report


# =====================================
# VERIFIED PERFORMANCE READER
# =====================================

def load_performance(day):

    date.fromisoformat(day)

    folder = OUTPUT / day

    metadata = json.loads(

        (folder / "metadata.json")
        .read_text()

    )

    name = metadata["Archive"]

    if Path(name).name != name:

        raise ValueError(
            "Invalid archive name"
        )

    raw = (
        folder / name
    ).read_bytes()

    if hashlib.sha256(
        raw
    ).hexdigest() != metadata["SHA256"]:

        raise ValueError(
            "Performance checksum mismatch"
        )

    payload = json.loads(
        gzip.decompress(raw)
    )

    if (

        payload["Version"] != VERSION

        or payload["Session Date"] != day

        or metadata["Session Date"] != day

        or len(payload["Opportunities"])
            != metadata["Records"]

    ):

        raise ValueError(
            "Performance archive mismatch"
        )

    report = payload["Report"]

    report["Performance Archive Verified"] = True

    return (

        pd.DataFrame(
            payload["Opportunities"]
        ),

        report

    )


def main():

    day = os.getenv(

        "PERFORMANCE_DATE",

        ""

    ).strip() or now().date().isoformat()

    build_performance(day)


if __name__ == "__main__":
    main()
