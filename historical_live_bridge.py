
"""
HISTORICAL LIVE BASELINE BRIDGE
Version 2.5.2

Full F&O universe.
Historical archive integrity verification.
Same-time volume comparison.
Estimated turnover comparison.
No automatic orders.
No technical entry indicators.
"""

import json
import gzip
import math
import hashlib

from datetime import date, timedelta

import pandas as pd
import streamlit as st

from stock_intelligence_engine import (
    ROOT,
    now,
    parse_timestamp
)

from live_market_engine import (
    scan_market as original_scan_market
)


VERSION = "2.5.2"

MIN_SESSIONS = 5
MIN_COVERAGE = 0.90
MAX_ARCHIVE_AGE = 7
MAX_QUOTE_AGE = 60

BASELINE_ROOT = ROOT / "intraday_baseline"


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


def ratio(current, historical):

    current = number(current)
    historical = number(historical)

    if current is None or historical is None:

        return None

    if current < 0 or historical <= 0:

        return None

    return round(current / historical, 4)


# =====================================
# HISTORICAL ARCHIVE VALIDATION
# =====================================

def load_archive():

    diagnostics_path = (
        BASELINE_ROOT
        / "latest_diagnostics.json"
    )

    if not diagnostics_path.exists():

        raise ValueError(
            "Historical diagnostics unavailable"
        )

    diagnostics = json.loads(
        diagnostics_path.read_text()
    )

    if diagnostics.get("Status") not in (
        "COMPLETE",
        "PARTIAL"
    ):

        raise ValueError(
            "Historical collection unsuccessful"
        )

    sessions = diagnostics.get(
        "Requested Sessions", []
    )

    if not sessions:

        raise ValueError(
            "Historical session dates missing"
        )

    session = date.fromisoformat(
        max(sessions)
    )

    age = (
        now().date() - session
    ).days

    if age < 1 or age > MAX_ARCHIVE_AGE:

        raise ValueError(
            "Historical archive date invalid or stale"
        )

    archive_path = (
        BASELINE_ROOT
        / session.isoformat()
        / "baseline.json.gz"
    )

    if not archive_path.exists():

        raise ValueError(
            "Historical baseline archive missing"
        )

    expected_hash = diagnostics.get(
        "Archive SHA256"
    )

    if not expected_hash:

        raise ValueError(
            "Archive checksum missing"
        )

    return read_verified_archive(
        str(archive_path),
        expected_hash,
        session.isoformat(),
        diagnostics.get(
            "Baseline Ready Stocks"
        )
    )


@st.cache_data(
    ttl=300,
    show_spinner=False
)
def read_verified_archive(
    archive_path,
    expected_hash,
    expected_session,
    expected_count
):

    from pathlib import Path

    compressed = Path(
        archive_path
    ).read_bytes()

    actual_hash = hashlib.sha256(
        compressed
    ).hexdigest()

    if actual_hash != expected_hash:

        raise ValueError(
            "Historical archive checksum mismatch"
        )

    payload = json.loads(
        gzip.decompress(compressed)
    )

    if payload.get("Version") != "2.5.1":

        raise ValueError(
            "Unsupported baseline archive version"
        )

    if payload.get(
        "Latest Session"
    ) != expected_session:

        raise ValueError(
            "Historical archive date mismatch"
        )

    if payload.get(
        "Turnover Estimated"
    ) is not True:

        raise ValueError(
            "Unknown historical turnover method"
        )

    stocks = payload.get("Stocks")

    if not isinstance(stocks, dict):

        raise ValueError(
            "Invalid historical stock records"
        )

    if len(stocks) != expected_count:

        raise ValueError(
            "Historical stock count mismatch"
        )

    validated = {}

    for symbol, records in stocks.items():

        if not isinstance(records, dict):

            continue

        clean = {}

        for minute, values in records.items():

            try:

                minute = int(minute)

                if not 556 <= minute <= 930:

                    continue

                days = number(
                    values.get("days")
                )

                volume = number(
                    values.get("volume")
                )

                turnover = number(
                    values.get("turnover")
                )

                if (
                    days is None
                    or days < MIN_SESSIONS
                    or days > 20
                    or days != int(days)
                    or volume is None
                    or volume <= 0
                    or turnover is None
                    or turnover <= 0
                ):

                    continue

                clean[minute] = {
                    "days": int(days),
                    "volume": volume,
                    "turnover": turnover
                }

            except Exception:

                continue

        if clean:

            validated[symbol] = clean

    return validated, {
        "Version": VERSION,
        "Archive Session Label": expected_session,
        "Archive Hash Verified": True,
        "Archive Stocks": len(stocks),
        "Validated Baseline Stocks": len(validated),
        "Turnover Estimated": True,
        "Independent NSE Cross Check": False
    }


# =====================================
# LIVE QUOTE TIME VALIDATION
# =====================================

def valid_quote_time(value):

    try:

        stamp = parse_timestamp(value)

        if stamp is None:

            return None

        if stamp.tzinfo is None:

            return None

        stamp = stamp.astimezone(
            now().tzinfo
        )

        current = now()

        if stamp.date() != current.date():

            return None

        age = (
            current - stamp
        ).total_seconds()

        if not 0 <= age <= MAX_QUOTE_AGE:

            return None

        minute = (
            stamp.hour * 60
            + stamp.minute
        )

        if not 555 <= minute < 930:

            return None

        return stamp

    except Exception:

        return None


# =====================================
# SAME-TIME BASELINE
# =====================================

def historical_at_time(
    records,
    stamp
):

    minute = (
        stamp.hour * 60
        + stamp.minute
    )

    # Wait for the first completed minute.

    if minute < 556:

        return None

    previous = records.get(minute)

    if previous is None:

        return None

    fraction = (
        stamp.second
        + stamp.microsecond / 1000000
    ) / 60

    if fraction == 0:

        return previous

    following = records.get(
        minute + 1
    )

    if following is None:

        return None

    if (
        following["volume"]
        < previous["volume"]

        or following["turnover"]
        < previous["turnover"]
    ):

        return None

    # Estimate the historical cumulative
    # activity at the exact live quote time.
    # Intraminute interpolation is approximate.

    volume = (
        previous["volume"]
        + fraction * (
            following["volume"]
            - previous["volume"]
        )
    )

    turnover = (
        previous["turnover"]
        + fraction * (
            following["turnover"]
            - previous["turnover"]
        )
    )

    return {
        "days": min(
            previous["days"],
            following["days"]
        ),
        "volume": volume,
        "turnover": turnover
    }


# =====================================
# INTEGRATED LIVE MARKET SCAN
# =====================================

def scan_market(token):

    frame, summary, sectors = (
        original_scan_market(token)
    )

    summary = dict(summary)

    summary["Historical Bridge Version"] = (
        VERSION
    )

    if (
        frame.empty
        or summary.get("status") != "LIVE"
    ):

        return frame, summary, sectors

    frame = frame.copy()

    # Never mix an unverified baseline
    # with the historical archive.

    frame["Same Time RVOL"] = float("nan")

    frame["Same Time Turnover Ratio"] = (
        float("nan")
    )

    frame["Intraday Baseline Days"] = 0

    frame["Historical Expected Volume"] = (
        float("nan")
    )

    frame["Historical Expected Turnover Cr"] = (
        float("nan")
    )

    frame["Historical Baseline Status"] = (
        "NOT VALIDATED"
    )

    frame["Historical Baseline Method"] = (
        "UNAVAILABLE"
    )

    frame["Historical Comparison Approximate"] = (
        True
    )

    try:

        stocks, archive_report = (
            load_archive()
        )

    except Exception as error:

        summary["Historical Baseline Status"] = (
            "UNAVAILABLE"
        )

        summary["Historical Baseline Error"] = (
            str(error)[:300]
        )

        frame["Historical Baseline Status"] = (
            "ARCHIVE UNAVAILABLE"
        )

        return frame, summary, sectors

    summary.update(archive_report)

    required = {
        "Symbol",
        "Status",
        "Quote Time",
        "Volume",
        "Estimated Turnover Cr"
    }

    if not required.issubset(
        frame.columns
    ):

        summary["Historical Baseline Status"] = (
            "LIVE FIELDS MISSING"
        )

        summary["Missing Live Fields"] = sorted(
            required - set(frame.columns)
        )

        return frame, summary, sectors

    ready = 0

    for index, row in frame.iterrows():

        if row.get("Status") != "FRESH":

            frame.at[
                index,
                "Historical Baseline Status"
            ] = "STALE LIVE QUOTE"

            continue

        stamp = valid_quote_time(
            row.get("Quote Time")
        )

        if stamp is None:

            frame.at[
                index,
                "Historical Baseline Status"
            ] = "INVALID QUOTE TIME"

            continue

        symbol = str(
            row.get("Symbol", "")
        ).strip().upper()

        records = stocks.get(symbol)

        if not records:

            frame.at[
                index,
                "Historical Baseline Status"
            ] = "STOCK BASELINE MISSING"

            continue

        historical = historical_at_time(
            records,
            stamp
        )

        if historical is None:

            frame.at[
                index,
                "Historical Baseline Status"
            ] = "MINUTE BASELINE UNAVAILABLE"

            continue

        live_volume = number(
            row.get("Volume")
        )

        live_turnover_cr = number(
            row.get("Estimated Turnover Cr")
        )

        historical_turnover_cr = (
            historical["turnover"]
            / 10000000
        )

        rvol = ratio(
            live_volume,
            historical["volume"]
        )

        turnover_ratio = ratio(
            live_turnover_cr,
            historical_turnover_cr
        )

        if (
            rvol is None
            or turnover_ratio is None
        ):

            frame.at[
                index,
                "Historical Baseline Status"
            ] = "INVALID LIVE ACTIVITY"

            continue

        frame.at[
            index,
            "Same Time RVOL"
        ] = rvol

        frame.at[
            index,
            "Same Time Turnover Ratio"
        ] = turnover_ratio

        frame.at[
            index,
            "Intraday Baseline Days"
        ] = historical["days"]

        frame.at[
            index,
            "Historical Expected Volume"
        ] = round(
            historical["volume"], 2
        )

        frame.at[
            index,
            "Historical Expected Turnover Cr"
        ] = round(
            historical_turnover_cr, 4
        )

        frame.at[
            index,
            "Historical Baseline Status"
        ] = "AVAILABLE"

        frame.at[
            index,
            "Historical Baseline Method"
        ] = "HISTORICAL MINUTE MEDIAN"

        ready += 1

    universe = number(
        summary.get("universe_size")
    )

    coverage = (
        ready / universe
        if universe and universe > 0
        else 0
    )

    summary.update({

        "Historical Baseline Ready Stocks":
            ready,

        "Historical Live Coverage %":
            round(coverage * 100, 2),

        "Historical Baseline Status": (
            "AVAILABLE"
            if coverage >= MIN_COVERAGE
            else "PARTIAL"
        ),

        "Historical Comparison Approximate":
            True,

        "Intraminute Interpolation":
            True,

        "Live Turnover Method":
            "CUMULATIVE VOLUME X AVERAGE PRICE",

        "Historical Turnover Method":
            "MINUTE OHLC ESTIMATE",

        "Preopen Volume Reconciled":
            False,

        "Prediction Accuracy Verified":
            False

    })

    # Prevent historical participation
    # confirmation on low baseline coverage.

    if coverage < MIN_COVERAGE:

        frame["Same Time RVOL"] = float("nan")

        frame["Same Time Turnover Ratio"] = (
            float("nan")
        )

        frame["Intraday Baseline Days"] = 0

        frame.loc[
            frame["Historical Baseline Status"]
            == "AVAILABLE",
            "Historical Baseline Status"
        ] = "LOW BASELINE COVERAGE"

    return frame, summary, sectors
