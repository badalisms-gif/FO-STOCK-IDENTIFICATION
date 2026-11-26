
"""
INTEGRATED LIVE STOCK IDENTIFICATION
Version 2.4.0

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

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pandas as pd
import streamlit as st

from live_market_engine import scan_market

import premarket_intelligence_engine as pre

VERSION = "2.4.0"

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

    return str(value).strip().lower() in (
        "true", "1", "yes"
    )


def timestamp(value):

    return pre.parse_timestamp(value)


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


# =====================================
# HISTORICAL CONTEXT
# =====================================

@st.cache_data(ttl=300, show_spinner=False)
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

    return frame, {
        "Report Date": report_date,
        "Historical Stocks": len(frame),
        "History Status": status,
        "Archive Diagnostics": diagnostics,
        "Option Exchange Time Verified": False
    }


# =====================================
# OPTIONAL PRE-MARKET WATCHLIST
# =====================================

def load_premarket_csv(raw, historical_date):

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

    if "Report Date" in frame.columns:

        dates = pd.to_datetime(
            frame["Report Date"],
            errors="coerce"
        )

        if (
            dates.notna().all()
            and dates.dt.strftime(
                "%Y-%m-%d"
            ).eq(historical_date).all()
        ):

            date_verified = True

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
        "Morning Timestamp Verified": False,
        "Used As Live Confirmation": False
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
        "Status",
        "LTP",
        "Quote Time"
    ]

    missing = [
        col for col in required
        if col not in frame.columns
    ]

    if missing:

        raise ValueError(
            "Missing live columns: "
            + ", ".join(missing)
        )

    result = frame.copy()

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
# DIRECTIONAL EVIDENCE
# =====================================

def evaluate_stock(
    live,
    history,
    morning,
    coverage,
    market_active
):

    symbol = text(
        live.get("Symbol")
    )

    ltp = number(
        live.get("LTP")
    )

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
        live.get(
            "Same Time Turnover Ratio"
        )
    )

    baseline = number(
        live.get(
            "Intraday Baseline Days"
        )
    )

    oi = number(
        live.get(
            "Futures OI 5m Change %"
        )
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
        morning.get(
            "Pre-Market Category"
        )
    ).upper()

    previous_change = number(
        history.get("Change %")
    )

    previous_volume = number(
        history.get("Volume Ratio")
    )

    bullish = (
        move1 is not None
        and move3 is not None
        and move5 is not None
        and relative is not None
        and move1 > 0
        and move3 > 0
        and move5 >= MIN_MOVE_5M
        and relative >= MIN_RELATIVE
    )

    bearish = (
        move1 is not None
        and move3 is not None
        and move5 is not None
        and relative is not None
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

    if move5 is None:
        missing.append("5 MINUTE HISTORY")

    if move3 is None:
        missing.append("3 MINUTE HISTORY")

    if move1 is None:
        missing.append("1 MINUTE HISTORY")

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
        "Previous Category": previous_category,
        "Previous Change %": previous_change,
        "Previous Volume Ratio": previous_volume,
        "Pre-Market Category": morning_category,
        "Historical Alignment": context_aligned,
        "Pre-Market Alignment": morning_aligned,
        "Extended": extended,
        "Reasons": " | ".join(reasons),
        "Missing Evidence": " | ".join(missing),
        "Confirmation Count": 0,
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
        ):

            if quote > last_quote:

                count = (
                    int(
                        previous.get(
                            "Count", 0
                        )
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

    morning_report = {}

    try:

        historical, history_report = (
            historical_context()
        )

        morning_map, morning_report = (
            load_premarket_csv(
                morning_raw,
                history_report["Report Date"]
            )
        )

    except Exception as error:

        history_report["Context Error"] = (
            str(error)
        )

    live, summary = scan_market(token)

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
            "Historical Context": history_report,
            "Pre-Market Context": morning_report,
            "Trade Signal": "NOT GENERATED",
            "Completed At": now().isoformat()
        }, {}

    live, fresh_count, coverage = (
        validate_live(
            live, summary
        )
    )

    if live.empty:

        return live, {
            "Version": VERSION,
            "Status": "NO LIVE DATA",
            "Live Summary": summary,
            "Trade Signal": "NOT GENERATED",
            "Completed At": now().isoformat()
        }, {}

    history_map = {}

    if not historical.empty:

        history_map = {
            row["Symbol"]: row.to_dict()
            for _, row
            in historical.iterrows()
        }

    active = market_window()

    rows = []

    for _, row in live.iterrows():

        symbol = row["Symbol"]

        rows.append(
            evaluate_stock(
                row.to_dict(),
                history_map.get(symbol, {}),
                morning_map.get(symbol, {}),
                coverage,
                active
            )
        )

    frame = pd.DataFrame(rows)

    current = now()

    frame, confirmation_state = (
        apply_confirmation(
            frame,
            previous_state,
            current,
            active
        )
    )

    # Recheck quote freshness after processing.

    stale = ~frame["Quote Time"].map(
        lambda value: fresh_timestamp(
            value, now()
        )
    )

    frame.loc[
        stale, "Live Category"
    ] = "DATA UNAVAILABLE"

    frame.loc[
        stale, "Fresh"
    ] = False

    frame.loc[
        stale, "Rank"
    ] = 9

    frame.loc[
        stale, "Confirmation Count"
    ] = 0

    for symbol in frame.loc[
        stale, "Symbol"
    ]:

        confirmation_state.pop(
            symbol, None
        )

    universe = int(
        summary["universe_size"]
    )

    fresh_count = int(
        frame["Fresh"].sum()
    )

    coverage = fresh_count / universe

    if (
        coverage < MIN_COVERAGE
        or not market_window()
    ):

        priority = frame[
            "Live Category"
        ].isin([
            "PRIORITY BULLISH",
            "PRIORITY BEARISH",
            "CONFIRMATION PENDING"
        ])

        frame.loc[
            priority, "Live Category"
        ] = "VALIDATION FAILED - WAIT"

        frame.loc[
            priority, "Rank"
        ] = 9

        confirmation_state = {}

    frame["Absolute Relative Strength"] = (
        frame["Relative Strength %"]
        .abs()
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
            True, False, False,
            False, False, False
        ],
        na_position="last"
    ).reset_index(drop=True)

    counts = frame[
        "Live Category"
    ].value_counts().to_dict()

    report = {
        "Version": VERSION,
        "Status": (
            "LIVE RESEARCH AVAILABLE"
            if coverage >= MIN_COVERAGE
            and market_window()
            else "LIVE DATA INSUFFICIENT"
        ),
        "Universe": universe,
        "Processed Stocks": len(frame),
        "Fresh Stocks": fresh_count,
        "Coverage %": round(
            coverage * 100, 2
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
            frame[
                "Intraday Baseline Days"
            ].ge(
                MIN_BASELINE_DAYS
            ).sum()
        ),
        "Comparable Futures OI Stocks": int(
            (
                frame["Futures Fresh"]
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
        "Historical Context": history_report,
        "Pre-Market Context": morning_report,
        "Live Summary": summary,
        "Independent NSE Cross Check": False,
        "Prediction Accuracy Verified": False,
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

    key = "integrated_v24_journal"

    journal = st.session_state.get(
        key, []
    )

    current = report["Completed At"]

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

        item["Observed At"] = current

        journal.append(item)

    # Bound session memory usage.

    st.session_state[key] = (
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

    a, b, c = st.columns(3)

    a.metric(
        "Universe",
        report["Universe"]
    )

    b.metric(
        "Fresh Stocks",
        report["Fresh Stocks"]
    )

    c.metric(
        "Coverage",
        str(report["Coverage %"]) + "%"
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

    for index, values in enumerate(filters):

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
        "integrated_live_v24.csv",
        mime="text/csv"
    )

    st.download_button(
        "DOWNLOAD DIAGNOSTICS",
        json.dumps(
            report,
            indent=2,
            default=str
        ),
        "integrated_live_diagnostics_v24.json",
        mime="application/json"
    )

    journal = st.session_state.get(
        "integrated_v24_journal",
        []
    )

    if journal:

        st.download_button(
            "DOWNLOAD SESSION RESEARCH JOURNAL",
            pd.DataFrame(
                journal
            ).to_csv(index=False),
            "live_research_journal_v24.csv",
            mime="text/csv"
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
        "UPSTOX_ACCESS_TOKEN", ""
    )

    if not token:

        try:

            token = st.secrets.get(
                "UPSTOX_ACCESS_TOKEN", ""
            )

        except Exception:
            token = ""

    if not token:

        token = st.text_input(
            "Upstox Analytics Token",
            type="password",
            key="integrated_v24_token"
        )

    uploaded = st.file_uploader(
        "Pre-Market V2.3.1 Watchlist CSV "
        "(Optional)",
        type=["csv"]
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
            + str(error)
        )

    auto = st.toggle(
        "Auto Refresh Every 30 Seconds",
        value=False
    )

    st.caption(
        "Current IST: "
        + now().strftime(
            "%d-%m-%Y %H:%M:%S"
        )
    )

    if not token.strip():

        st.info(
            "Enter Upstox Analytics Token"
        )

        return

    if not market_window():

        st.session_state[
            "integrated_v24_confirmation"
        ] = {}

        st.info(
            "Outside normal NSE trading "
            "hours. Live scanning disabled."
        )

        return

    @st.fragment(
        run_every=(
            REFRESH_SECONDS
            if auto else None
        )
    )
    def live_panel():

        manual = st.button(
            "SCAN FULL F&O UNIVERSE",
            type="primary"
        )

        if not market_window():

            st.session_state[
                "integrated_v24_confirmation"
            ] = {}

            st.info(
                "Market session ended"
            )

            return

        if not auto and not manual:

            st.caption(
                "Press scan or enable "
                "automatic refresh."
            )

            return

        try:

            with st.spinner(
                "Validating live market..."
            ):

                frame, report, state = (
                    integrated_scan(
                        token.strip(),
                        morning_raw,
                        st.session_state.get(
                            "integrated_v24_confirmation",
                            {}
                        )
                    )
                )

            st.session_state[
                "integrated_v24_confirmation"
            ] = state

            update_journal(
                frame, report
            )

            show_result(
                frame, report
            )

        except Exception as error:

            st.session_state[
                "integrated_v24_confirmation"
            ] = {}

            st.error(
                "Live scan failed: "
                + str(error)
            )

    live_panel()
