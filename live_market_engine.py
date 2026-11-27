
"""
F&O LIVE MARKET INTELLIGENCE
Version 1.7.1

Previous Close Correction
Full Universe Monitoring
Stock Identification Only
No Automatic Orders
"""

import os
import math
import json
import time
import sqlite3
import gzip
import pandas as pd
import streamlit as st

from datetime import timedelta
from concurrent.futures import ThreadPoolExecutor

from stock_intelligence_engine import (
    get_json,
    secure_get,
    get_universe,
    parse_timestamp,
    MASTER,
    ROOT,
    UP,
    now
)


VERSION = "1.7.1"

DB_PATH = ROOT / "live_market.sqlite3"

NIFTY = "NSE_INDEX|Nifty 50"

SECTOR_INDICES = {
    "BANK": "NSE_INDEX|Nifty Bank",
    "IT": "NSE_INDEX|Nifty IT",
    "AUTO": "NSE_INDEX|Nifty Auto",
    "PHARMA": "NSE_INDEX|Nifty Pharma",
    "METAL": "NSE_INDEX|Nifty Metal",
    "FMCG": "NSE_INDEX|Nifty FMCG",
    "REALTY": "NSE_INDEX|Nifty Realty",
    "ENERGY": "NSE_INDEX|Nifty Energy",
    "FINANCIAL": "NSE_INDEX|Nifty Fin Service",
    "MEDIA": "NSE_INDEX|Nifty Media",
    "PSU BANK": "NSE_INDEX|Nifty PSU Bank",
    "OIL GAS": "NSE_INDEX|Nifty Oil and Gas"
}

QUOTE_BATCH = 40
MAX_QUOTE_AGE = 60
MIN_BASELINE_DAYS = 5
REFRESH_SECONDS = 30


# =====================================
# NUMERIC VALIDATION
# =====================================

def number(value):

    try:

        value = float(value)

        if math.isfinite(value):
            return value

    except (ValueError, TypeError):
        pass

    return None


def positive(value):

    value = number(value)

    if value is not None and value > 0:
        return value

    return None


def percentage(current, previous):

    current = number(current)
    previous = positive(previous)

    if current is None or previous is None:
        return None

    return (current / previous - 1) * 100


# =====================================
# MARKET STATUS
# =====================================

def market_status(token):

    try:

        payload = get_json(
            UP + "/v2/market/status/NSE",
            token=token
        )

        if payload.get("status") != "success":
            return "UNVERIFIED"

        return str(
            payload.get("data", {})
            .get("status", "UNVERIFIED")
        ).upper()

    except Exception:

        return "UNVERIFIED"


def market_open(token):

    current = now()

    if current.weekday() >= 5:
        return False

    minute = current.hour * 60 + current.minute

    if not 555 <= minute < 930:
        return False

    return market_status(token) == "NORMAL_OPEN"


# =====================================
# CURRENT F&O FUTURES
# =====================================

@st.cache_data(ttl=3600, show_spinner=False)
def load_instruments():

    content = secure_get(MASTER)

    if content[:2] == b"\x1f\x8b":
        content = gzip.decompress(content)

    instruments = json.loads(content)

    universe = get_universe()

    reverse = {
        key: symbol
        for symbol, key in universe.items()
    }

    futures = {}

    for item in instruments:

        if item.get("segment") != "NSE_FO":
            continue

        if item.get("instrument_type") != "FUT":
            continue

        underlying = item.get("underlying_key")

        if underlying not in reverse:
            continue

        expiry = parse_timestamp(
            item.get("expiry")
        )

        if expiry is None:
            continue

        if expiry.date() < now().date():
            continue

        key = item.get("instrument_key")

        if not key:
            continue

        symbol = reverse[underlying]

        futures.setdefault(symbol, []).append({

            "key": key,

            "expiry": expiry.date().isoformat(),

            "lot_size": positive(
                item.get("lot_size")
            )

        })

    selected = {}

    for symbol, contracts in futures.items():

        contracts.sort(
            key=lambda x: x["expiry"]
        )

        selected[symbol] = contracts[0]

    return universe, selected


# =====================================
# LIVE QUOTE API
# =====================================

def quote_batch(token, keys):

    try:

        payload = get_json(

            UP + "/v2/market-quote/quotes",

            {
                "instrument_key": ",".join(keys)
            },

            token

        )

        if payload.get("status") != "success":

            raise ValueError(
                "Quote API unsuccessful"
            )

        mapping = payload.get("data")

        if not isinstance(mapping, dict):

            raise ValueError(
                "Invalid quote response"
            )

        result = {}

        for response_key, item in mapping.items():

            if not isinstance(item, dict):
                continue

            key = item.get("instrument_token")

            if key not in keys:

                if response_key in keys:
                    key = response_key

                else:
                    continue

            result[key] = item

        return result, ""

    except Exception as error:

        message = str(error)

        if token:

            message = message.replace(
                token,
                "[REDACTED]"
            )

        return {}, message[:500]


def fetch_quotes(token, keys):

    keys = list(dict.fromkeys(keys))

    batches = [

        keys[i:i + QUOTE_BATCH]

        for i in range(
            0, len(keys), QUOTE_BATCH
        )

    ]

    quotes = {}
    errors = []

    with ThreadPoolExecutor(
        max_workers=2
    ) as executor:

        results = executor.map(

            lambda batch: quote_batch(
                token, batch
            ),

            batches

        )

        for data, error in results:

            quotes.update(data)

            if error:
                errors.append(error)

    return quotes, errors


# =====================================
# QUOTE FRESHNESS
# =====================================

def quote_timestamp(quote):

    return parse_timestamp(

        quote.get("timestamp")

        or quote.get("last_trade_time")

    )


def fresh_quote(quote):

    if not quote:
        return False

    timestamp = quote_timestamp(quote)

    if timestamp is None:
        return False

    age = (
        now() - timestamp
    ).total_seconds()

    return (

        timestamp.date() == now().date()

        and 0 <= age <= MAX_QUOTE_AGE

        and positive(
            quote.get("last_price")
        ) is not None

    )


# =====================================
# CORRECT PREVIOUS CLOSE - V1.7.1
# =====================================

def previous_close(quote):

    if not isinstance(quote, dict):
        return None

    price = positive(
        quote.get("last_price")
    )

    if price is None:
        return None

    explicit_close = positive(
        quote.get("prev_close_price")
    )

    net_change = number(
        quote.get("net_change")
    )

    calculated_close = None

    if net_change is not None:

        calculated_close = positive(
            price - net_change
        )

    if explicit_close is not None:

        if calculated_close is not None:

            difference = abs(
                explicit_close
                - calculated_close
            )

            tolerance = max(
                0.02,
                explicit_close * 0.0001
            )

            if difference > tolerance:
                return None

        return explicit_close

    return calculated_close


# =====================================
# BID ASK SPREAD
# =====================================

def spread_percent(quote):

    depth = quote.get("depth") or {}

    bids = depth.get("buy") or []
    asks = depth.get("sell") or []

    if not bids or not asks:
        return None

    bid = positive(
        bids[0].get("price")
    )

    ask = positive(
        asks[0].get("price")
    )

    if bid is None or ask is None:
        return None

    if ask < bid:
        return None

    midpoint = (bid + ask) / 2

    return (
        (ask - bid) / midpoint
    ) * 100


# =====================================
# HISTORICAL DAILY BASELINE
# =====================================

@st.cache_data(ttl=900, show_spinner=False)
def load_daily_baseline():

    folder = ROOT / "cash_bhavcopy"

    frames = []

    if not folder.exists():
        return {}

    today = now().date().isoformat()

    for path in sorted(
        folder.glob("*/latest.csv")
    ):

        if path.parent.name >= today:
            continue

        try:

            frame = pd.read_csv(path)

            eligible = (

                frame["Eligible For Analysis"]

                .astype(str)

                .str.lower()

                .eq("true")

            )

            frame = frame[eligible].copy()

            frame["Volume"] = pd.to_numeric(
                frame["Volume"],
                errors="coerce"
            )

            frame["Turnover"] = pd.to_numeric(
                frame["Turnover"],
                errors="coerce"
            )

            frames.append(frame)

        except Exception:
            continue

    if not frames:
        return {}

    data = pd.concat(
        frames,
        ignore_index=True
    )

    data = data.drop_duplicates(

        subset=["Symbol", "Report Date"],

        keep="last"

    )

    data = data.sort_values(
        "Report Date"
    )

    result = {}

    for symbol, group in data.groupby(
        "Symbol"
    ):

        group = group.tail(20)

        if len(group) < MIN_BASELINE_DAYS:
            continue

        result[symbol] = {

            "days": len(group),

            "daily_volume": positive(
                group["Volume"].median()
            ),

            "daily_turnover": positive(
                group["Turnover"].median()
            )

        }

    return result


# =====================================
# INTRADAY STORAGE
# =====================================

def connect_db():

    DB_PATH.parent.mkdir(
        parents=True,
        exist_ok=True
    )

    connection = sqlite3.connect(
        DB_PATH,
        timeout=20
    )

    connection.execute(

        """
        CREATE TABLE IF NOT EXISTS snapshots (

            session_date TEXT NOT NULL,

            symbol TEXT NOT NULL,

            minute INTEGER NOT NULL,

            timestamp REAL NOT NULL,

            price REAL NOT NULL,

            volume REAL,

            estimated_turnover REAL,

            future_key TEXT,

            future_oi REAL,

            PRIMARY KEY (
                session_date,
                symbol,
                minute
            )
        )
        """

    )

    connection.commit()

    return connection


def save_live_rows(rows):

    if not rows:
        return

    connection = connect_db()

    try:

        connection.executemany(

            """
            INSERT INTO snapshots VALUES (
                ?, ?, ?, ?, ?, ?, ?, ?, ?
            )

            ON CONFLICT (
                session_date,
                symbol,
                minute
            )

            DO UPDATE SET

                timestamp = excluded.timestamp,

                price = excluded.price,

                volume = excluded.volume,

                estimated_turnover =
                    excluded.estimated_turnover,

                future_key = excluded.future_key,

                future_oi = excluded.future_oi

            WHERE excluded.timestamp
                >= snapshots.timestamp
            """,

            rows

        )

        connection.commit()

    finally:

        connection.close()


# =====================================
# SAME TIME HISTORICAL BASELINE
# =====================================

def same_time_baseline(
    connection,
    symbol,
    minute,
    session_date
):

    rows = connection.execute(

        """
        SELECT
            session_date,
            volume,
            estimated_turnover

        FROM snapshots

        WHERE symbol = ?

        AND minute = ?

        AND session_date < ?

        ORDER BY session_date DESC

        LIMIT 20
        """,

        (
            symbol,
            minute,
            session_date
        )

    ).fetchall()

    frame = pd.DataFrame(

        rows,

        columns=[
            "Date",
            "Volume",
            "Turnover"
        ]

    )

    volumes = pd.to_numeric(

        frame["Volume"],

        errors="coerce"

    ).dropna()

    volumes = volumes[volumes > 0]

    turnover = pd.to_numeric(

        frame["Turnover"],

        errors="coerce"

    ).dropna()

    turnover = turnover[turnover > 0]

    volume_baseline = None
    turnover_baseline = None

    if len(volumes) >= MIN_BASELINE_DAYS:

        volume_baseline = positive(
            volumes.median()
        )

    if len(turnover) >= MIN_BASELINE_DAYS:

        turnover_baseline = positive(
            turnover.median()
        )

    return {

        "days": len(volumes),

        "volume": volume_baseline,

        "turnover": turnover_baseline

    }


# =====================================
# RECENT PRICE AND OI HISTORY
# =====================================

def previous_snapshot(

    connection,
    symbol,
    session_date,
    timestamp,
    minutes

):

    target = timestamp - minutes * 60

    return connection.execute(

        """
        SELECT
            price,
            volume,
            future_key,
            future_oi,
            timestamp

        FROM snapshots

        WHERE symbol = ?

        AND session_date = ?

        AND timestamp BETWEEN ? AND ?

        ORDER BY ABS(timestamp - ?)

        LIMIT 1
        """,

        (

            symbol,

            session_date,

            target - 35,

            target + 35,

            target

        )

    ).fetchone()


# =====================================
# LIVE MARKET INTELLIGENCE
# =====================================

def scan_market(token):

    if not token:

        raise ValueError(
            "Upstox token required"
        )

    if not market_open(token):

        return pd.DataFrame(), {

            "status": "MARKET CLOSED OR UNVERIFIED"

        }, pd.DataFrame()

    started = time.monotonic()

    universe, futures = load_instruments()

    daily = load_daily_baseline()

    keys = list(universe.values())

    keys += [

        contract["key"]

        for contract in futures.values()

    ]

    keys += [NIFTY]

    keys += list(
        SECTOR_INDICES.values()
    )

    quotes, errors = fetch_quotes(
        token,
        keys
    )

    if not market_open(token):

        return pd.DataFrame(), {

            "status": "MARKET CLOSED DURING SCAN"

        }, pd.DataFrame()

    # =================================
    # NIFTY CHANGE - CORRECTED
    # =================================

    nifty = quotes.get(NIFTY, {})

    nifty_change = None
    nifty_previous = None

    if fresh_quote(nifty):

        nifty_previous = previous_close(nifty)

        nifty_change = percentage(

            nifty.get("last_price"),

            nifty_previous

        )

    # =================================
    # SECTOR STRENGTH - CORRECTED
    # =================================

    sectors = []

    for name, key in SECTOR_INDICES.items():

        quote = quotes.get(key, {})

        fresh = fresh_quote(quote)

        change = None
        reference = None

        if fresh:

            reference = previous_close(quote)

            change = percentage(

                quote.get("last_price"),

                reference

            )

        sectors.append({

            "Sector": name,

            "Change %": change,

            "Fresh": fresh,

            "Previous Close": reference,

            "Reference Available":
                reference is not None

        })

    rows = []
    snapshots = []

    reference_available = 0
    reference_missing = 0
    zero_change_stocks = 0

    connection = connect_db()

    try:

        for symbol, key in universe.items():

            quote = quotes.get(key, {})

            fresh = fresh_quote(quote)

            if not fresh:

                rows.append({

                    "Symbol": symbol,

                    "Status": "STALE OR MISSING"

                })

                continue

            stamp = quote_timestamp(quote)

            timestamp = stamp.timestamp()

            session_date = stamp.date().isoformat()

            minute = (
                stamp.hour * 60
                + stamp.minute
            )

            price = positive(
                quote.get("last_price")
            )

            # =========================
            # CORRECT STOCK CHANGE
            # =========================

            previous = previous_close(quote)

            change = percentage(
                price,
                previous
            )

            if previous is not None:
                reference_available += 1

            else:
                reference_missing += 1

            if change is not None and change == 0:
                zero_change_stocks += 1

            # =========================
            # VOLUME AND TURNOVER
            # =========================

            volume = number(
                quote.get("volume")
            )

            if volume is not None and volume < 0:
                volume = None

            average_price = positive(
                quote.get("average_price")
            )

            turnover = None

            if (

                volume is not None

                and average_price is not None

            ):

                turnover = volume * average_price

            # =========================
            # FUTURES OPEN INTEREST
            # =========================

            contract = futures.get(symbol, {})

            future_key = contract.get("key")

            future = quotes.get(
                future_key, {}
            )

            future_fresh = fresh_quote(future)

            oi = None
            oi_change = None

            if future_fresh:

                oi = number(
                    future.get("oi")
                )

                if oi is not None and oi < 0:
                    oi = None

                previous_oi = positive(
                    future.get("prev_oi")
                )

                oi_change = percentage(
                    oi,
                    previous_oi
                )

            # =========================
            # PRICE AND OI MOMENTUM
            # =========================

            moves = {}

            recent_oi_change = None
            recent_volume = None

            for period in [1, 3, 5]:

                old = previous_snapshot(

                    connection,

                    symbol,

                    session_date,

                    timestamp,

                    period

                )

                moves[period] = None

                if old is None:
                    continue

                moves[period] = percentage(

                    price,

                    old[0]

                )

                if period == 5:

                    old_volume = number(old[1])

                    if (

                        volume is not None

                        and old_volume is not None

                        and volume >= old_volume

                    ):

                        recent_volume = (
                            volume - old_volume
                        )

                    if (

                        future_fresh

                        and old[2] == future_key

                    ):

                        recent_oi_change = percentage(
                            oi,
                            old[3]
                        )

            # =========================
            # HISTORICAL PARTICIPATION
            # =========================

            baseline = same_time_baseline(

                connection,

                symbol,

                minute,

                session_date

            )

            relative_volume = None

            if (

                volume is not None

                and baseline["volume"] is not None

            ):

                relative_volume = (
                    volume / baseline["volume"]
                )

            relative_turnover = None

            if (

                turnover is not None

                and baseline["turnover"] is not None

            ):

                relative_turnover = (
                    turnover / baseline["turnover"]
                )

            # =========================
            # CORRECT RELATIVE STRENGTH
            # =========================

            relative_strength = None

            if (

                change is not None

                and nifty_change is not None

            ):

                relative_strength = (
                    change - nifty_change
                )

            daily_reference = daily.get(
                symbol, {}
            )

            daily_volume = daily_reference.get(
                "daily_volume"
            )

            daily_progress = None

            if (

                volume is not None

                and daily_volume is not None

            ):

                daily_progress = (
                    volume / daily_volume * 100
                )

            # =========================
            # FULL STOCK INTELLIGENCE
            # =========================

            rows.append({

                "Symbol": symbol,

                "Status": "FRESH",

                "LTP": price,

                "Previous Close": previous,

                "Previous Close Available":
                    previous is not None,

                "API Net Change": number(
                    quote.get("net_change")
                ),

                "Change %": change,

                "Move 1m %": moves[1],

                "Move 3m %": moves[3],

                "Move 5m %": moves[5],

                "Relative Strength %":
                    relative_strength,

                "Volume": volume,

                "Volume Last 5m": recent_volume,

                "Estimated Turnover Cr": (

                    turnover / 10000000

                    if turnover is not None

                    else None

                ),

                "Daily Volume Progress %":
                    daily_progress,

                "Same Time RVOL":
                    relative_volume,

                "Same Time Turnover Ratio":
                    relative_turnover,

                "Intraday Baseline Days":
                    baseline["days"],

                "Futures OI": oi,

                "Futures OI Change %":
                    oi_change,

                "Futures OI 5m Change %":
                    recent_oi_change,

                "Futures Fresh":
                    future_fresh,

                "Futures Expiry":
                    contract.get("expiry"),

                "Spread %":
                    spread_percent(quote),

                "Quote Time":
                    stamp.isoformat()

            })

            snapshots.append((

                session_date,

                symbol,

                minute,

                timestamp,

                price,

                volume,

                turnover,

                future_key,

                oi

            ))

    finally:

        connection.close()

    save_live_rows(snapshots)

    frame = pd.DataFrame(rows)

    fresh_count = int(

        frame["Status"].eq("FRESH").sum()

    )

    # =================================
    # PREVIOUS CLOSE DIAGNOSTICS
    # =================================

    reference_coverage = (

        reference_available / fresh_count * 100

        if fresh_count > 0

        else 0.0

    )

    zero_change_percentage = (

        zero_change_stocks / reference_available * 100

        if reference_available > 0

        else None

    )

    reference_warnings = []

    if nifty_previous is None:

        reference_warnings.append(
            "Nifty previous close unavailable"
        )

    if reference_missing > 0:

        reference_warnings.append(

            f"Previous close unavailable for "
            f"{reference_missing} fresh stocks"

        )

    if (

        reference_available >= 20

        and zero_change_percentage is not None

        and zero_change_percentage >= 90

    ):

        reference_warnings.append(

            "Suspicious zero-change distribution. "
            "Independent market comparison required."

        )

    if reference_warnings:

        reference_status = "REVIEW REQUIRED"

    elif reference_available > 0:

        reference_status = "REFERENCE AVAILABLE"

    else:

        reference_status = "UNAVAILABLE"

    # =================================
    # COLLECTION SUMMARY
    # =================================

    summary = {

        "version": VERSION,

        "status": "LIVE",

        "universe_size": len(universe),

        "fresh_stocks": fresh_count,

        "missing_or_stale":
            len(universe) - fresh_count,

        "futures_contracts":
            len(futures),

        "nifty_change": nifty_change,

        "nifty_previous_close":
            nifty_previous,

        "Previous Close Method":
            "EXPLICIT PREVIOUS CLOSE OR LTP MINUS NET CHANGE",

        "Previous Close Available Stocks":
            reference_available,

        "Previous Close Missing Stocks":
            reference_missing,

        "Previous Close Coverage %":
            round(reference_coverage, 2),

        "Zero Change Stocks":
            zero_change_stocks,

        "Zero Change Stocks %": (

            round(zero_change_percentage, 2)

            if zero_change_percentage is not None

            else None

        ),

        "Previous Close Status":
            reference_status,

        "Previous Close Warnings":
            reference_warnings,

        "Previous Close Independently Verified":
            False,

        "Sector Reference Available": sum(

            item["Reference Available"]

            for item in sectors

        ),

        "scan_seconds": round(

            time.monotonic() - started,

            2

        ),

        "checked_at": now().isoformat(),

        "errors": errors

    }

    return (

        frame,

        summary,

        pd.DataFrame(sectors)

    )


# =====================================
# STREAMLIT LIVE DASHBOARD
# =====================================

def render_live_market(token=""):

    st.header(
        "F&O LIVE MARKET INTELLIGENCE V"
        + VERSION
    )

    st.caption(

        "Stock Identification Only | "

        "Full Universe Monitoring | "

        "No Automatic Orders"

    )

    if not token:

        token = os.environ.get(

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

            key="live_v17_token"

        )

    if not token:

        st.info(
            "Configure Upstox token to start."
        )

        return

    running = st.toggle(

        "Start Live Market Monitoring",

        value=False,

        key="live_v17_running"

    )

    st.caption(

        "Refresh interval: 30 seconds. "

        "Only verified open-market sessions."

    )

    @st.fragment(

        run_every=(
            "30s" if running else None
        )

    )
    def live_panel():

        if not running:

            st.info(
                "Live monitoring stopped."
            )

            return

        try:

            frame, summary, sectors = scan_market(
                token
            )

        except Exception as error:

            st.error(

                str(error).replace(

                    token,

                    "[REDACTED]"

                )

            )

            return

        if summary.get("status") != "LIVE":

            st.warning(
                summary.get("status")
            )

            return

        a, b, c, d = st.columns(4)

        a.metric(

            "F&O Universe",

            summary["universe_size"]

        )

        b.metric(

            "Fresh Stocks",

            summary["fresh_stocks"]

        )

        c.metric(

            "Stale / Missing",

            summary["missing_or_stale"]

        )

        nifty = summary.get(
            "nifty_change"
        )

        d.metric(

            "Nifty Change",

            f"{nifty:.2f}%"

            if nifty is not None

            else "Unavailable"

        )

        st.caption(

            "Last scan: "

            + summary["checked_at"]

            + " | Duration: "

            + str(summary["scan_seconds"])

            + " seconds"

        )

        # =============================
        # REFERENCE PRICE DIAGNOSTICS
        # =============================

        p1, p2, p3 = st.columns(3)

        p1.metric(

            "Previous Close Available",

            summary[
                "Previous Close Available Stocks"
            ]

        )

        p2.metric(

            "Previous Close Coverage",

            f"{summary['Previous Close Coverage %']:.2f}%"

        )

        p3.metric(

            "Zero Change Stocks",

            summary["Zero Change Stocks"]

        )

        for warning in summary.get(

            "Previous Close Warnings",

            []

        ):

            st.warning(warning)

        if summary["errors"]:

            st.warning(

                "Some quote requests failed. "

                "Missing data is not treated as zero."

            )

            with st.expander(
                "API Diagnostics"
            ):

                st.json(summary["errors"])

        # =============================
        # SECTOR STRENGTH
        # =============================

        st.subheader(
            "Sector Market Strength"
        )

        st.dataframe(

            sectors.sort_values(

                "Change %",

                ascending=False,

                na_position="last"

            ),

            use_container_width=True,

            hide_index=True

        )

        # =============================
        # FULL STOCK UNIVERSE
        # =============================

        st.subheader(
            "Full F&O Stock Intelligence"
        )

        if frame.empty:

            st.warning(
                "No live stock records."
            )

            return

        fresh = frame[

            frame["Status"].eq("FRESH")

        ].copy()

        if fresh.empty:

            st.warning(
                "No fresh quotes available."
            )

        else:

            sort_options = [

                "Symbol",

                "Change %",

                "Relative Strength %",

                "Move 5m %",

                "Same Time RVOL",

                "Futures OI Change %",

                "Futures OI 5m Change %",

                "Estimated Turnover Cr"

            ]

            selected = st.selectbox(

                "Sort stocks by",

                sort_options,

                key="v17_sort"

            )

            ascending = st.checkbox(

                "Ascending order",

                value=(
                    selected == "Symbol"
                ),

                key="v17_ascending"

            )

            fresh = fresh.sort_values(

                selected,

                ascending=ascending,

                na_position="last"

            )

            st.dataframe(

                fresh,

                use_container_width=True,

                hide_index=True

            )

            st.download_button(

                "Download Live Intelligence",

                fresh.to_csv(index=False),

                "live_stock_intelligence.csv",

                mime="text/csv",

                key="v17_download"

            )

        missing = frame[

            frame["Status"].ne("FRESH")

        ]

        if not missing.empty:

            with st.expander(
                "Stale or Missing Stocks"
            ):

                st.dataframe(

                    missing,

                    hide_index=True

                )

        st.info(

            "Local same-time RVOL requires at least "

            "five prior observed sessions. "

            "The integrated historical bridge may "

            "supply its separate archived baseline."

        )

        with st.expander(
            "Collection Summary"
        ):

            st.json(summary)

    live_panel()


if __name__ == "__main__":

    st.set_page_config(

        page_title="F&O Live Intelligence",

        layout="wide"

    )

    render_live_market()
