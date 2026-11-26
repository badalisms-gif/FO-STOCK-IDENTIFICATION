
"""
INTEGRATED LIVE STOCK IDENTIFICATION
Version 2.4.1

Full F&O Universe
Historical + Pre-Market + Live Evidence

Research only.
No automatic orders.
No technical entry indicators.
No strike selection.
"""

import os
import io
import json
import math
import hashlib

from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd
import streamlit as st

from live_market_engine import scan_market
import premarket_intelligence_engine as pre


VERSION = "2.4.1"

IST = ZoneInfo("Asia/Kolkata")

MIN_COVERAGE = 0.90
MIN_RVOL = 1.50
MIN_TURNOVER = 1.30
MIN_MOVE_5M = 0.30
MIN_RELATIVE = 0.40
MIN_OI = 0.50

MAX_SPREAD = 0.20
MAX_QUOTE_AGE = 60
MAX_EXTENDED_MOVE = 3.0
MAX_MOVE_5M = 1.50

MIN_BASELINE_DAYS = 5
CONFIRMATION_SCANS = 2
REFRESH_SECONDS = 30
MAX_CONFIRMATION_GAP = 120

STATE_KEY = "integrated_v241_confirmation"
RESULT_KEY = "integrated_v241_result"
JOURNAL_KEY = "integrated_v241_journal"


# =====================================
# UTILITIES
# =====================================

def now():
    return datetime.now(IST)


def number(value):

    try:
        if isinstance(value, str):
            value = value.replace(",", "")

        value = float(value)

        if math.isfinite(value):
            return value

    except (ValueError, TypeError):
        pass

    return None


def text(value):

    if value is None:
        return ""

    try:
        if pd.isna(value):
            return ""
    except Exception:
        pass

    return str(value).strip()


def truth(value):

    return text(value).lower() in (
        "true", "1", "yes"
    )


def timestamp(value):

    if not text(value):
        return None

    try:

        stamp = pre.parse_timestamp(value)

        if stamp is None or pd.isna(stamp):
            return None

        if stamp.tzinfo is None:
            return None

        return stamp.astimezone(IST)

    except Exception:
        return None


def fresh_timestamp(value, current=None):

    current = current or now()

    stamp = timestamp(value)

    if stamp is None:
        return False

    age = (
        current - stamp
    ).total_seconds()

    return (
        stamp.date() == current.date()
        and 0 <= age <= MAX_QUOTE_AGE
    )


def market_window():

    current = now()

    minute = (
        current.hour * 60
        + current.minute
    )

    return (
        current.weekday() < 5
        and 555 <= minute < 930
    )


def safe_error(error, token=""):

    message = str(error)

    if token:
        message = message.replace(
            token, "[REDACTED]"
        )

    return message[:1000]


# =====================================
# HISTORICAL CONTEXT
# =====================================

@st.cache_data(
    ttl=300,
    show_spinner=False
)
def historical_context():

    base, latest, diagnostics, status = (
        pre.load_base()
    )

    if "Symbol" not in base.columns:
        raise ValueError(
            "Historical symbol column missing"
        )

    frame = base.copy()

    frame["Symbol"] = (
        frame["Symbol"]
        .fillna("")
        .astype(str)
        .str.strip()
        .str.upper()
    )

    frame = frame[
        frame["Symbol"].ne("")
        & frame["Symbol"].ne(
            "NO MATCHED RECORD"
        )
    ]

    if frame["Symbol"].duplicated().any():
        raise ValueError(
            "Duplicate historical symbols"
        )

    report_date = str(
        latest["cash_date"]
    )

    day = datetime.strptime(
        report_date,
        "%Y-%m-%d"
    ).date()

    if day >= now().date():
        raise ValueError(
            "Historical base must be "
            "from a previous session"
        )

    return frame, {

        "Report Date": report_date,

        "Historical Stocks": len(frame),

        "History Status": status,

        "Archive Diagnostics": diagnostics,

        "Latest Completed Session Verified":
            False,

        "Option Exchange Time Verified":
            False

    }


# =====================================
# PRE-MARKET CONTEXT
# =====================================

def load_premarket_csv(
    raw,
    historical_date
):

    if not raw:

        return {}, {
            "Status": "NOT PROVIDED"
        }

    if len(raw) > 20_000_000:
        raise ValueError(
            "Pre-market CSV too large"
        )

    frame = pd.read_csv(
        io.BytesIO(raw)
    )

    required = [
        "Symbol",
        "Pre-Market Category",
        "Morning Direction"
    ]

    missing = [
        col for col in required
        if col not in frame.columns
    ]

    if missing:
        raise ValueError(
            "Missing columns: "
            + ", ".join(missing)
        )

    frame["Symbol"] = (
        frame["Symbol"]
        .fillna("")
        .astype(str)
        .str.strip()
        .str.upper()
    )

    frame = frame[
        frame["Symbol"].ne("")
        & frame["Symbol"].ne(
            "NO MATCHED RECORD"
        )
    ]

    if frame["Symbol"].duplicated().any():
        raise ValueError(
            "Duplicate pre-market symbols"
        )

    date_verified = False

    if (
        "Report Date" in frame.columns
        and not frame.empty
    ):

        dates = pd.to_datetime(
            frame["Report Date"],
            errors="coerce"
        )

        date_verified = (
            dates.notna().all()
            and dates.dt.strftime(
                "%Y-%m-%d"
            ).eq(historical_date).all()
        )

    if not date_verified:

        return {}, {

            "Status":
                "DATE UNVERIFIED - NOT USED",

            "Records": len(frame)

        }

    mapping = {
        row["Symbol"]: row.to_dict()
        for _, row in frame.iterrows()
    }

    return mapping, {

        "Status": "CONTEXT LOADED",

        "Records": len(mapping),

        "Report Date": historical_date,

        "SHA256": hashlib.sha256(
            raw
        ).hexdigest(),

        "Morning Timestamp Verified":
            False,

        "Used As Live Confirmation":
            False

    }


# =====================================
# LIVE DATA VALIDATION
# =====================================

def validate_live(frame, summary):

    if not isinstance(frame, pd.DataFrame):
        raise ValueError(
            "Invalid live dataframe"
        )

    if frame.empty:
        return frame.copy(), 0, 0.0

    required = [
        "Symbol",
        "Status"
    ]

    for column in required:

        if column not in frame.columns:
            raise ValueError(
                "Missing live column: "
                + column
            )

    result = frame.copy()

    # V1.7 returns only Symbol and Status
    # when every stock is stale.
    # Preserve missing values.

    for column in ["LTP", "Quote Time"]:

        if column not in result.columns:
            result[column] = None

    result["Symbol"] = (
        result["Symbol"]
        .fillna("")
        .astype(str)
        .str.strip()
        .str.upper()
    )

    if result["Symbol"].eq("").any():
        raise ValueError(
            "Blank live symbol"
        )

    if result["Symbol"].duplicated().any():
        raise ValueError(
            "Duplicate live symbols"
        )

    universe = int(
        number(
            summary.get("universe_size")
        ) or 0
    )

    if universe <= 0:
        raise ValueError(
            "Live universe unavailable"
        )

    if len(result) > universe:
        raise ValueError(
            "Live universe count mismatch"
        )

    current = now()

    result["Validated Fresh"] = (
        result.apply(
            lambda row: (
                text(
                    row["Status"]
                ).upper() == "FRESH"

                and fresh_timestamp(
                    row["Quote Time"],
                    current
                )

                and (
                    number(row["LTP"]) or 0
                ) > 0
            ),
            axis=1
        )
    )

    fresh_count = int(
        result["Validated Fresh"].sum()
    )

    coverage = fresh_count / universe

    return result, fresh_count, coverage


# =====================================
# STOCK IDENTIFICATION
# =====================================

def evaluate_stock(
    live,
    history,
    morning,
    coverage,
    market_active
):

    symbol = text(live.get("Symbol"))

    ltp = number(live.get("LTP"))

    change = number(
        live.get("Change %")
    )

    move1 = number(
        live.get("Move 1m %")
    )

    move3 = number(
        live.get("Move 3m %")
    )

    move5 = number(
        live.get("Move 5m %")
    )

    relative = number(
        live.get("Relative Strength %")
    )

    rvol = number(
        live.get("Same Time RVOL")
    )

    turnover = number(
        live.get("Same Time Turnover Ratio")
    )

    baseline = number(
        live.get("Intraday Baseline Days")
    )

    oi = number(
        live.get("Futures OI 5m Change %")
    )

    spread = number(
        live.get("Spread %")
    )

    fresh = truth(
        live.get("Validated Fresh")
    )

    futures_fresh = truth(
        live.get("Futures Fresh")
    )

    previous_category = text(
        history.get("Category")
    ).upper()

    morning_category = text(
        morning.get("Pre-Market Category")
    ).upper()

    previous_change = number(
        history.get("Change %")
    )

    previous_volume = number(
        history.get("Volume Ratio")
    )

    price_ready = all(
        value is not None
        for value in [
            move1,
            move3,
            move5,
            relative
        ]
    )

    bullish = (
        price_ready
        and move1 > 0
        and move3 > 0
        and move5 >= MIN_MOVE_5M
        and relative >= MIN_RELATIVE
    )

    bearish = (
        price_ready
        and move1 < 0
        and move3 < 0
        and move5 <= -MIN_MOVE_5M
        and relative <= -MIN_RELATIVE
    )

    participation = (
        baseline is not None
        and baseline >= MIN_BASELINE_DAYS
        and rvol is not None
        and turnover is not None
        and rvol >= MIN_RVOL
        and turnover >= MIN_TURNOVER
    )

    derivatives = (
        futures_fresh
        and oi is not None
        and oi >= MIN_OI
    )

    oi_reduction = (
        futures_fresh
        and oi is not None
        and oi <= -MIN_OI
    )

    liquidity = (
        spread is not None
        and 0 <= spread <= MAX_SPREAD
    )

    extended = (
        (
            change is not None
            and abs(change)
                >= MAX_EXTENDED_MOVE
        )
        or (
            move5 is not None
            and abs(move5)
                >= MAX_MOVE_5M
        )
    )

    direction = "UNCONFIRMED"

    if bullish:
        direction = "BULLISH"

    elif bearish:
        direction = "BEARISH"

    previous_bull = (
        "BULLISH" in previous_category
    )

    previous_bear = (
        "BEARISH" in previous_category
    )

    context_aligned = (
        (bullish and previous_bull)
        or (bearish and previous_bear)
    )

    context_conflict = (
        (bullish and previous_bear)
        or (bearish and previous_bull)
    )

    morning_aligned = (
        (
            bullish
            and morning_category
                == "PRIORITY BULLISH"
        )
        or (
            bearish
            and morning_category
                == "PRIORITY BEARISH"
        )
    )

    reasons = []
    missing = []

    if bullish or bearish:
        reasons.append(
            "ALIGNED LIVE PRICE REACTION"
        )

    if participation:
        reasons.append(
            "ABNORMAL LIVE PARTICIPATION"
        )

    if derivatives:
        reasons.append(
            "FUTURES OI EXPANSION"
        )

    if oi_reduction:
        reasons.append(
            "FUTURES OI REDUCTION"
        )

    if context_aligned:
        reasons.append(
            "HISTORICAL CONTEXT ALIGNED"
        )

    if morning_aligned:
        reasons.append(
            "PREMARKET CONTEXT ALIGNED"
        )

    if context_conflict:
        reasons.append(
            "HISTORICAL DIRECTION CONFLICT"
        )

    if not fresh:
        missing.append("FRESH QUOTE")

    if move1 is None:
        missing.append("1 MINUTE HISTORY")

    if move3 is None:
        missing.append("3 MINUTE HISTORY")

    if move5 is None:
        missing.append("5 MINUTE HISTORY")

    if relative is None:
        missing.append("RELATIVE STRENGTH")

    if (
        baseline is None
        or baseline < MIN_BASELINE_DAYS
    ):
        missing.append(
            "SAME TIME HISTORICAL BASELINE"
        )

    if rvol is None:
        missing.append("RELATIVE VOLUME")

    if turnover is None:
        missing.append("TURNOVER RATIO")

    if not futures_fresh or oi is None:
        missing.append(
            "COMPARABLE FRESH FUTURES OI"
        )

    if spread is None:
        missing.append("CASH SPREAD")

    reaction = bullish or bearish

    evidence_groups = (
        int(reaction)
        + int(participation)
        + int(derivatives)
    )

    status = "NO OPPORTUNITY"
    rank = 8

    if not market_active:

        status = "MARKET INACTIVE"
        rank = 9

    elif not fresh:

        status = "DATA UNAVAILABLE"
        rank = 9

    elif coverage < MIN_COVERAGE:

        status = "LOW MARKET COVERAGE"
        rank = 9

    elif extended:

        status = "EXTENDED MOVEMENT - WAIT"
        rank = 7

    elif context_conflict and reaction:

        status = "CONFLICT - REVIEW"
        rank = 6

    elif (
        reaction
        and participation
        and derivatives
        and liquidity
    ):

        status = "CONFIRMATION PENDING"
        rank = 2

    elif (
        reaction
        and liquidity
        and (
            participation
            or derivatives
        )
    ):

        status = "EMERGING WATCH"
        rank = 3

    elif (
        participation
        and derivatives
        and liquidity
        and not reaction
    ):

        status = "EARLY ACTIVITY WATCH"
        rank = 4

    elif reaction and liquidity:

        status = "DIRECTIONAL OBSERVATION"
        rank = 5

    elif missing:

        status = "INSUFFICIENT EVIDENCE"
        rank = 8

    if (
        not fresh
        or not market_active
        or coverage < MIN_COVERAGE
    ):
        direction = "UNCONFIRMED"

    return {

        "Symbol": symbol,

        "LTP": ltp,

        "Change %": change,

        "Direction": direction,

        "Live Category": status,

        "Rank": rank,

        "Evidence Groups": evidence_groups,

        "Move 1m %": move1,

        "Move 3m %": move3,

        "Move 5m %": move5,

        "Relative Strength %": relative,

        "Same Time RVOL": rvol,

        "Same Time Turnover Ratio": turnover,

        "Intraday Baseline Days": baseline,

        "Futures OI 5m %": oi,

        "Futures Fresh": futures_fresh,

        "Futures Expiry": live.get(
            "Futures Expiry"
        ),

        "Spread %": spread,

        "Quote Time": live.get(
            "Quote Time"
        ),

        "Fresh": fresh,

        "Previous Category":
            previous_category,

        "Previous Change %":
            previous_change,

        "Previous Volume Ratio":
            previous_volume,

        "Pre-Market Category":
            morning_category,

        "Historical Alignment":
            context_aligned,

        "Pre-Market Alignment":
            morning_aligned,

        "Extended": extended,

        "Reasons": " | ".join(reasons),

        "Missing Evidence":
            " | ".join(missing),

        "Confirmation Count": 0,

        "First Seen": "",

        "Trade Signal": "NOT GENERATED"

    }


# =====================================
# CONSECUTIVE SCAN CONFIRMATION
# =====================================

def apply_confirmation(
    frame,
    previous_state,
    current,
    market_active
):

    new_state = {}

    if not market_active:
        return frame, new_state

    for index, row in frame.iterrows():

        if (
            row["Live Category"]
            != "CONFIRMATION PENDING"
        ):
            continue

        symbol = row["Symbol"]

        quote = timestamp(
            row["Quote Time"]
        )

        if quote is None:
            continue

        previous = previous_state.get(
            symbol, {}
        )

        count = 1

        first_seen = current.isoformat()

        last_seen = timestamp(
            previous.get("Last Seen")
        )

        last_quote = timestamp(
            previous.get("Quote Time")
        )

        same_direction = (
            previous.get("Direction")
            == row["Direction"]
        )

        same_expiry = (
            previous.get("Futures Expiry")
            == text(row["Futures Expiry"])
        )

        if (
            same_direction
            and same_expiry
            and last_seen is not None
            and last_quote is not None
            and last_seen.date()
                == current.date()
            and 0 <= (
                current - last_seen
            ).total_seconds()
                <= MAX_CONFIRMATION_GAP
            and 0 <= (
                quote - last_quote
            ).total_seconds()
                <= MAX_CONFIRMATION_GAP
        ):

            if quote > last_quote:

                count = (
                    int(
                        previous.get("Count", 0)
                    ) + 1
                )

                first_seen = previous.get(
                    "First Seen",
                    first_seen
                )

            elif quote == last_quote:

                count = int(
                    previous.get("Count", 1)
                )

                first_seen = previous.get(
                    "First Seen",
                    first_seen
                )

        frame.at[
            index,
            "Confirmation Count"
        ] = count

        frame.at[
            index,
            "First Seen"
        ] = first_seen

        new_state[symbol] = {

            "Direction": row["Direction"],

            "Count": count,

            "Quote Time": quote.isoformat(),

            "First Seen": first_seen,

            "Last Seen": current.isoformat(),

            "Futures Expiry": text(
                row["Futures Expiry"]
            )

        }

        if count >= CONFIRMATION_SCANS:

            frame.at[
                index,
                "Live Category"
            ] = (
                "PRIORITY BULLISH"
                if row["Direction"] == "BULLISH"
                else "PRIORITY BEARISH"
            )

            frame.at[index, "Rank"] = 1

    return frame, new_state


# =====================================
# FINAL FRESHNESS VALIDATION
# =====================================

def final_validation(frame, universe):

    frame = frame.copy()

    current = now()

    fresh = (
        frame["Fresh"].map(truth)
        & frame["Quote Time"].map(
            lambda value: fresh_timestamp(
                value,
                current
            )
        )
    )

    frame["Fresh"] = fresh

    stale = ~fresh

    frame.loc[
        stale, "Live Category"
    ] = "DATA UNAVAILABLE"

    frame.loc[
        stale, "Direction"
    ] = "UNCONFIRMED"

    frame.loc[
        stale, "Rank"
    ] = 9

    frame.loc[
        stale, "Confirmation Count"
    ] = 0

    fresh_count = int(fresh.sum())

    coverage = (
        fresh_count / universe
        if universe > 0
        else 0.0
    )

    active = market_window()

    if coverage < MIN_COVERAGE or not active:

        frame.loc[
            fresh, "Live Category"
        ] = (
            "LOW MARKET COVERAGE"
            if active
            else "MARKET INACTIVE"
        )

        frame["Direction"] = "UNCONFIRMED"

        frame["Rank"] = 9

        frame["Confirmation Count"] = 0

    return (
        frame,
        fresh_count,
        coverage,
        active
    )


# =====================================
# MASTER SCAN
# =====================================

def integrated_scan(
    token,
    morning_raw=None,
    previous_state=None
):

    started = now()

    previous_state = (
        previous_state or {}
    )

    historical = pd.DataFrame()

    history_report = {}

    morning_map = {}

    morning_report = {
        "Status": "NOT PROVIDED"
    }

    try:

        historical, history_report = (
            historical_context()
        )

    except Exception as error:

        history_report["Context Error"] = (
            safe_error(error, token)
        )

    if history_report.get("Report Date"):

        try:

            morning_map, morning_report = (
                load_premarket_csv(
                    morning_raw,
                    history_report["Report Date"]
                )
            )

        except Exception as error:

            morning_report = {

                "Status": "INVALID CONTEXT",

                "Error": safe_error(
                    error, token
                )

            }

    # =================================
    # CRITICAL V2.4.1 CORRECTION
    # V1.7 returns THREE objects.
    # =================================

    live, summary, sectors = scan_market(
        token
    )

    status = text(
        summary.get("status")
    ).upper()

    if (
        status != "LIVE"
        or not market_window()
    ):

        return pd.DataFrame(), {

            "Version": VERSION,

            "Status": status or "NOT LIVE",

            "Live Summary": summary,

            "Historical Context":
                history_report,

            "Pre-Market Context":
                morning_report,

            "Trade Signal": "NOT GENERATED",

            "Completed At":
                now().isoformat()

        }, {}

    live, fresh_count, coverage = (
        validate_live(
            live,
            summary
        )
    )

    if live.empty:

        return live, {

            "Version": VERSION,

            "Status": "NO LIVE DATA",

            "Live Summary": summary,

            "Trade Signal": "NOT GENERATED",

            "Completed At":
                now().isoformat()

        }, {}

    history_map = {}

    if not historical.empty:

        history_map = {

            row["Symbol"]: row.to_dict()

            for _, row in
                historical.iterrows()

        }

    active = market_window()

    rows = []

    for _, row in live.iterrows():

        symbol = row["Symbol"]

        rows.append(

            evaluate_stock(

                row.to_dict(),

                history_map.get(
                    symbol, {}
                ),

                morning_map.get(
                    symbol, {}
                ),

                coverage,

                active

            )

        )

    frame = pd.DataFrame(rows)

    universe = int(
        summary["universe_size"]
    )

    frame, fresh_count, coverage, active = (
        final_validation(
            frame,
            universe
        )
    )

    frame, confirmation_state = (
        apply_confirmation(

            frame,

            previous_state,

            now(),

            active
            and coverage >= MIN_COVERAGE

        )
    )

    # Recheck after confirmation.

    frame, fresh_count, coverage, active = (
        final_validation(
            frame,
            universe
        )
    )

    if (
        coverage < MIN_COVERAGE
        or not active
    ):

        confirmation_state = {}

    else:

        valid_symbols = set(

            frame.loc[

                frame["Fresh"]
                & frame["Live Category"].isin([
                    "PRIORITY BULLISH",
                    "PRIORITY BEARISH",
                    "CONFIRMATION PENDING"
                ]),

                "Symbol"

            ]

        )

        confirmation_state = {

            symbol: value

            for symbol, value in
                confirmation_state.items()

            if symbol in valid_symbols

        }

    numeric_columns = [

        "Relative Strength %",

        "Same Time RVOL",

        "Same Time Turnover Ratio",

        "Intraday Baseline Days",

        "Futures OI 5m %"

    ]

    for column in numeric_columns:

        frame[column] = pd.to_numeric(

            frame[column],

            errors="coerce"

        )

    frame["Absolute Relative Strength"] = (
        frame["Relative Strength %"].abs()
    )

    frame = frame.sort_values(

        [

            "Rank",

            "Evidence Groups",

            "Historical Alignment",

            "Pre-Market Alignment",

            "Same Time RVOL",

            "Absolute Relative Strength"

        ],

        ascending=[

            True,
            False,
            False,
            False,
            False,
            False

        ],

        na_position="last"

    ).reset_index(drop=True)

    counts = frame[
        "Live Category"
    ].value_counts().to_dict()

    sector_records = []

    if isinstance(sectors, pd.DataFrame):

        sector_records = json.loads(
            sectors.to_json(
                orient="records"
            )
        )

    report = {

        "Version": VERSION,

        "Status": (

            "LIVE RESEARCH AVAILABLE"

            if coverage >= MIN_COVERAGE
            and active

            else "LIVE DATA INSUFFICIENT"

        ),

        "Universe": universe,

        "Processed Stocks": len(frame),

        "Fresh Stocks": fresh_count,

        "Coverage %": round(
            coverage * 100,
            2
        ),

        "Historical Matched Stocks": int(

            frame["Symbol"].isin(
                history_map
            ).sum()

        ),

        "Pre-Market Matched Stocks": int(

            frame["Symbol"].isin(
                morning_map
            ).sum()

        ),

        "Baseline Ready Stocks": int(

            (
                frame["Fresh"]
                & frame[
                    "Intraday Baseline Days"
                ].ge(MIN_BASELINE_DAYS)
                & frame[
                    "Same Time RVOL"
                ].notna()
                & frame[
                    "Same Time Turnover Ratio"
                ].notna()
            ).sum()

        ),

        "Comparable Futures OI Stocks": int(

            (
                frame["Fresh"]
                & frame["Futures Fresh"]
                & frame[
                    "Futures OI 5m %"
                ].notna()
            ).sum()

        ),

        "Category Counts": counts,

        "Priority Bullish": counts.get(
            "PRIORITY BULLISH", 0
        ),

        "Priority Bearish": counts.get(
            "PRIORITY BEARISH", 0
        ),

        "Emerging": counts.get(
            "EMERGING WATCH", 0
        ),

        "Early Activity": counts.get(
            "EARLY ACTIVITY WATCH", 0
        ),

        "Historical Context":
            history_report,

        "Pre-Market Context":
            morning_report,

        "Live Summary": summary,

        "Sector Strength": sector_records,

        "Independent NSE Cross Check":
            False,

        "Futures Timestamp Rechecked":
            False,

        "Prediction Accuracy Verified":
            False,

        "Trade Signal": "NOT GENERATED",

        "Started At": started.isoformat(),

        "Completed At": now().isoformat()

    }

    return (
        frame,
        report,
        confirmation_state
    )


# =====================================
# SESSION RESEARCH JOURNAL
# =====================================

def update_journal(frame, report):

    if frame.empty:
        return

    if (
        report.get("Status")
        != "LIVE RESEARCH AVAILABLE"
    ):
        return

    journal = st.session_state.get(
        JOURNAL_KEY, []
    )

    candidates = frame[

        frame["Live Category"].isin([

            "PRIORITY BULLISH",

            "PRIORITY BEARISH",

            "EMERGING WATCH",

            "EARLY ACTIVITY WATCH"

        ])

    ]

    for _, row in candidates.iterrows():

        item = row.to_dict()

        item["Observed At"] = report[
            "Completed At"
        ]

        journal.append(item)

    st.session_state[JOURNAL_KEY] = (
        journal[-50000:]
    )


# =====================================
# DASHBOARD OUTPUT
# =====================================

def show_result(frame, report):

    st.info(
        report.get("Status", "")
    )

    st.caption(
        "Last Scan: "
        + report.get("Completed At", "")
    )

    if frame.empty:

        st.json(report)
        return

    # Stored results are historical records.
    # Do not present expired quotes as live.

    completed = timestamp(
        report.get("Completed At")
    )

    age = (

        (now() - completed).total_seconds()

        if completed is not None

        else float("inf")

    )

    universe = int(
        report.get("Universe", 0)
    )

    display_fresh = (

        frame["Fresh"].map(truth)

        & frame["Quote Time"].map(
            fresh_timestamp
        )

    )

    display_coverage = (

        int(display_fresh.sum()) / universe

        if universe > 0

        else 0.0

    )

    candidates = frame[

        frame["Live Category"].isin([

            "PRIORITY BULLISH",

            "PRIORITY BEARISH",

            "CONFIRMATION PENDING",

            "EMERGING WATCH",

            "EARLY ACTIVITY WATCH"

        ])

    ]

    candidate_quotes_fresh = (

        candidates["Quote Time"]
        .map(fresh_timestamp)
        .all()

    )

    if (

        not 0 <= age <= MAX_QUOTE_AGE

        or not market_window()

        or display_coverage < MIN_COVERAGE

        or not candidate_quotes_fresh

    ):

        st.warning(

            "Previous scan only. "

            "Quotes are expired or market "

            "coverage is insufficient. "

            "Run a new scan."

        )

        with st.expander(
            "Previous Scan Archive"
        ):

            st.dataframe(

                frame,

                hide_index=True,

                use_container_width=True

            )

            st.json(report)

        return

    a, b, c = st.columns(3)

    a.metric(
        "Universe",
        report["Universe"]
    )

    b.metric(
        "Fresh Stocks",
        int(display_fresh.sum())
    )

    c.metric(

        "Coverage",

        f"{display_coverage * 100:.2f}%"

    )

    a, b, c = st.columns(3)

    a.metric(
        "Priority Bullish",
        report["Priority Bullish"]
    )

    b.metric(
        "Priority Bearish",
        report["Priority Bearish"]
    )

    c.metric(
        "Emerging",
        report["Emerging"]
    )

    a, b, c = st.columns(3)

    a.metric(
        "Early Activity",
        report["Early Activity"]
    )

    b.metric(
        "Baseline Ready",
        report["Baseline Ready Stocks"]
    )

    c.metric(
        "Comparable Futures OI",
        report[
            "Comparable Futures OI Stocks"
        ]
    )

    if (

        report["Baseline Ready Stocks"]

        < report["Universe"]

    ):

        st.warning(

            "Some stocks do not have "

            "sufficient same-time intraday "

            "historical data. Priority "

            "classification remains restricted."

        )

    if report.get("Sector Strength"):

        with st.expander(
            "Sector Market Strength"
        ):

            st.dataframe(

                pd.DataFrame(
                    report["Sector Strength"]
                ),

                hide_index=True,

                use_container_width=True

            )

    columns = [

        "Symbol",

        "Live Category",

        "Direction",

        "LTP",

        "Change %",

        "Move 1m %",

        "Move 3m %",

        "Move 5m %",

        "Relative Strength %",

        "Same Time RVOL",

        "Same Time Turnover Ratio",

        "Intraday Baseline Days",

        "Futures OI 5m %",

        "Spread %",

        "Confirmation Count",

        "Previous Category",

        "Pre-Market Category",

        "Reasons",

        "Missing Evidence",

        "Quote Time"

    ]

    columns = [

        col for col in columns

        if col in frame.columns

    ]

    tabs = st.tabs([

        "Priority Bullish",

        "Priority Bearish",

        "Emerging",

        "Early Activity",

        "Confirmation Pending",

        "Conflict / Extended",

        "All Stocks",

        "Diagnostics"

    ])

    category = frame["Live Category"]

    filters = [

        ["PRIORITY BULLISH"],

        ["PRIORITY BEARISH"],

        ["EMERGING WATCH"],

        ["EARLY ACTIVITY WATCH"],

        ["CONFIRMATION PENDING"],

        [

            "CONFLICT - REVIEW",

            "EXTENDED MOVEMENT - WAIT",

            "VALIDATION FAILED - WAIT"

        ]

    ]

    for index, values in enumerate(
        filters
    ):

        with tabs[index]:

            selected = frame[
                category.isin(values)
            ]

            if selected.empty:

                st.caption(
                    "No qualifying stocks"
                )

            else:

                st.dataframe(

                    selected[columns],

                    hide_index=True,

                    use_container_width=True

                )

    with tabs[6]:

        st.dataframe(

            frame[columns],

            hide_index=True,

            use_container_width=True

        )

    with tabs[7]:

        st.json(report)

    st.download_button(

        "DOWNLOAD LIVE WATCHLIST",

        frame.to_csv(index=False),

        "integrated_live_v241.csv",

        mime="text/csv",

        key="v241_watchlist_download"

    )

    st.download_button(

        "DOWNLOAD DIAGNOSTICS",

        json.dumps(

            report,

            indent=2,

            default=str

        ),

        "integrated_live_diagnostics_v241.json",

        mime="application/json",

        key="v241_diagnostics_download"

    )

    journal = st.session_state.get(
        JOURNAL_KEY, []
    )

    if journal:

        st.download_button(

            "DOWNLOAD SESSION RESEARCH JOURNAL",

            pd.DataFrame(
                journal
            ).to_csv(index=False),

            "live_research_journal_v241.csv",

            mime="text/csv",

            key="v241_journal_download"

        )


# =====================================
# STREAMLIT INTERFACE
# =====================================

def render_integrated_live():

    st.header(

        "INTEGRATED LIVE MARKET V"

        + VERSION

    )

    st.caption(

        "Full F&O Universe | "

        "Historical + Pre-Market + Live"

    )

    st.warning(

        "Stock identification only. "

        "No confirmed trade entries, "

        "option strikes or automatic orders."

    )

    token = os.getenv(

        "UPSTOX_ACCESS_TOKEN",

        ""

    )

    if not token:

        try:

            token = st.secrets.get(

                "UPSTOX_ACCESS_TOKEN",

                ""

            )

        except Exception:

            token = ""

    if not token:

        token = st.text_input(

            "Upstox Analytics Token",

            type="password",

            key="integrated_v241_token"

        )

    token = str(token or "").strip()

    uploaded = st.file_uploader(

        "Pre-Market V2.3.1 Watchlist CSV "

        "(Optional)",

        type=["csv"],

        key="v241_premarket_upload"

    )

    morning_raw = (

        uploaded.getvalue()

        if uploaded is not None

        else None

    )

    try:

        base, history = (
            historical_context()
        )

        st.info(

            "Historical Base Date: "

            + history["Report Date"]

            + " | Stocks: "

            + str(len(base))

        )

    except Exception as error:

        st.warning(

            "Historical context unavailable: "

            + safe_error(error, token)

        )

    auto = st.toggle(

        "Auto Refresh Every 30 Seconds",

        value=False,

        key="v241_auto_refresh"

    )

    if not token:

        st.info(
            "Enter Upstox Analytics Token"
        )

        return

    # Reset stored results when token or
    # optional pre-market context changes.

    context_id = hashlib.sha256(

        token.encode("utf-8")

        + b"\x00"

        + (morning_raw or b"")

    ).hexdigest()

    if st.session_state.get(
        "v241_context_id"
    ) != context_id:

        st.session_state[STATE_KEY] = {}

        st.session_state.pop(
            RESULT_KEY, None
        )

        st.session_state[
            "v241_context_id"
        ] = context_id

    @st.fragment(

        run_every=REFRESH_SECONDS

    )
    def live_panel():

        active = market_window()

        st.caption(

            "Current IST: "

            + now().strftime(
                "%d-%m-%Y %H:%M:%S"
            )

        )

        # Always show scan button.
        # Disable it outside market hours.

        manual = st.button(

            "SCAN FULL F&O UNIVERSE",

            type="primary",

            disabled=not active,

            key="v241_manual_scan"

        )

        if not active:

            st.session_state[STATE_KEY] = {}

            st.info(

                "Outside normal NSE trading "

                "hours. Scan button will "

                "activate during the normal "

                "market window."

            )

            st.caption(

                "Normal market window: "

                "09:15 - 15:30 IST. "

                "Actual exchange status is "

                "checked by the live engine."

            )

            return

        if not auto and not manual:

            saved = st.session_state.get(
                RESULT_KEY
            )

            if saved is not None:

                frame, report = saved

                show_result(
                    frame,
                    report
                )

            else:

                st.caption(

                    "Press scan or enable "

                    "automatic refresh."

                )

            return

        try:

            with st.spinner(

                "Validating full F&O universe..."

            ):

                frame, report, state = (

                    integrated_scan(

                        token,

                        morning_raw,

                        st.session_state.get(

                            STATE_KEY,

                            {}

                        )

                    )

                )

            st.session_state[
                STATE_KEY
            ] = state

            st.session_state[
                RESULT_KEY
            ] = (
                frame,
                report
            )

            update_journal(
                frame,
                report
            )

            show_result(
                frame,
                report
            )

        except Exception as error:

            st.session_state[
                STATE_KEY
            ] = {}

            st.session_state.pop(
                RESULT_KEY,
                None
            )

            st.error(

                "Live scan failed: "

                + safe_error(
                    error,
                    token
                )

            )

    live_panel()


if __name__ == "__main__":

    st.set_page_config(

        page_title="Integrated Live Market",

        layout="wide"

    )

    render_integrated_live()
