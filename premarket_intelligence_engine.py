
"""
F&O INTEGRATED PRE-MARKET INTELLIGENCE
Version 2.3.0

Full Universe Stock Identification
Historical Cash + Futures + Options
Fresh Announcements + NSE Pre-Open

No automatic orders.
No technical entry signals.
No strike recommendations.
"""

import io
import os
import json
import math
import time

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import requests
import pandas as pd
import streamlit as st

from option_history_engine import (
    load_history,
    compare_snapshots
)

VERSION = "2.3.0"

IST = ZoneInfo("Asia/Kolkata")

NSE = "https://www.nseindia.com"

UPSTOX = "https://api.upstox.com"

MIN_GAP = 0.40

EXTENDED_GAP = 2.50

MIN_CASH_RATIO = 1.50

MIN_FUTURES_OI = 3.0

MIN_DEMAND_RATIO = 1.50

MIN_PREOPEN_COVERAGE = 0.90

MAX_PREOPEN_AGE = 180

MIN_MATCHED_QUANTITY = 1


# =====================================
# UTILITIES
# =====================================

def now():
    return datetime.now(IST)


def number(value):

    try:

        if isinstance(value, str):

            value = value.replace(",", "")

        result = float(value)

        if math.isfinite(result):

            return result

    except (ValueError, TypeError):

        pass

    return None


def ratio(a, b):

    a = number(a)
    b = number(b)

    if a is None or b is None or b <= 0:

        return None

    return a / b


def parse_timestamp(value):

    if value is None:

        return None

    try:

        if isinstance(value, (int, float)):

            if value > 1e11:

                value = value / 1000

            return datetime.fromtimestamp(
                value, IST
            )

        value = str(value).strip()

        if len(value) >= 10 and (
            value[4:5] == "-"
            and value[7:8] == "-"
        ):

            stamp = pd.to_datetime(
                value,
                errors="coerce",
                dayfirst=False
            )

        else:

            stamp = pd.to_datetime(
                value,
                errors="coerce",
                dayfirst=True
            )

        if pd.isna(stamp):

            return None

        if stamp.tzinfo is None:

            stamp = stamp.tz_localize(IST)

        else:

            stamp = stamp.tz_convert(IST)

        return stamp.to_pydatetime()

    except Exception:

        return None


def preopen_window():

    current = now()

    minute = (
        current.hour * 60
        + current.minute
    )

    return (
        current.weekday() < 5
        and 540 <= minute < 555
    )


def clean_text(value):

    if value is None:

        return ""

    try:

        if pd.isna(value):

            return ""

    except Exception:

        pass

    return str(value).strip()


# =====================================
# NSE CONNECTION
# =====================================

def create_nse_session():

    session = requests.Session()

    session.headers.update({

        "User-Agent":
            "Mozilla/5.0",

        "Accept":
            "application/json,text/html,*/*",

        "Accept-Language":
            "en-IN,en;q=0.9",

        "Referer":
            NSE + "/"

    })

    return session


def nse_get(session, path, params=None):

    response = session.get(

        NSE + path,

        params=params,

        timeout=(8, 25),

        allow_redirects=False

    )

    response.raise_for_status()

    if response.status_code != 200:

        raise ValueError(
            "Unexpected NSE response"
        )

    if len(response.content) > 30_000_000:

        raise ValueError(
            "NSE response too large"
        )

    try:

        return response.json()

    except ValueError:

        raise ValueError(
            "NSE did not return JSON"
        )


# =====================================
# LOAD VERIFIED HISTORICAL DATA
# =====================================

def load_base():

    snapshots, diagnostics = load_history()

    dates = sorted(
        snapshots.keys(),
        reverse=True
    )

    if not dates:

        raise ValueError(
            "No verified historical archive"
        )

    today = now().date()

    dates = [

        day for day in dates

        if day < today

    ]

    if not dates:

        raise ValueError(
            "No previous-day historical archive"
        )

    latest = snapshots[dates[0]]

    base = latest["summary"].copy()

    history_status = (
        "ONE HISTORICAL SNAPSHOT"
    )

    if len(dates) >= 2:

        try:

            base, history_report = (
                compare_snapshots(
                    latest,
                    snapshots[dates[1]]
                )
            )

            history_status = (
                "HISTORICAL COMPARISON AVAILABLE"
            )

        except Exception as error:

            history_status = (
                "HISTORICAL COMPARISON FAILED: "
                + str(error)
            )

    return (

        base,

        latest,

        diagnostics,

        history_status

    )


# =====================================
# OVERNIGHT CORPORATE ANNOUNCEMENTS
# =====================================

def classify_event(heading):

    heading = heading.lower()

    groups = {

        "RESULTS": [

            "financial result",
            "quarterly result",
            "audited result"

        ],

        "ORDER / CONTRACT": [

            "order received",
            "award of order",
            "letter of award",
            "contract",
            "work order"

        ],

        "ACQUISITION / MERGER": [

            "acquisition",
            "merger",
            "amalgamation"

        ],

        "REGULATORY": [

            "penalty",
            "investigation",
            "regulatory",
            "show cause"

        ],

        "FUND RAISING": [

            "fund raising",
            "qualified institutional",
            "preferential issue"

        ],

        "CORPORATE ACTION": [

            "dividend",
            "buyback",
            "stock split",
            "bonus issue"

        ],

        "MANAGEMENT CHANGE": [

            "resignation",
            "appointment",
            "management change"

        ]

    }

    for event, words in groups.items():

        if any(

            word in heading

            for word in words

        ):

            return event

    return "OTHER DISCLOSURE"


def fetch_announcements(
    session,
    symbols,
    cutoff
):

    current = now()

    data = nse_get(

        session,

        "/api/corporate-announcements",

        {

            "index": "equities",

            "from_date":
                cutoff.strftime("%d-%m-%Y"),

            "to_date":
                current.strftime("%d-%m-%Y")

        }

    )

    if not isinstance(data, list):

        raise ValueError(
            "Unexpected announcement format"
        )

    rows = []

    for item in data:

        symbol = clean_text(

            item.get("symbol")

        ).upper()

        if symbol not in symbols:

            continue

        published = parse_timestamp(

            item.get("an_dt")

            or item.get("sort_date")

        )

        if published is None:

            continue

        if not cutoff < published <= current:

            continue

        heading = clean_text(

            item.get("desc")

        )

        details = clean_text(

            item.get("attchmntText")

        )

        combined = (

            heading + " " + details

        ).strip()

        if not combined:

            continue

        rows.append({

            "Symbol": symbol,

            "Published":
                published.isoformat(),

            "Heading": heading,

            "Details": details,

            "Event Type":
                classify_event(combined),

            "Attachment":
                clean_text(
                    item.get("attchmntFile")
                ),

            "Impact":
                "MANUAL REVIEW REQUIRED"

        })

    columns = [

        "Symbol",
        "Published",
        "Heading",
        "Details",
        "Event Type",
        "Attachment",
        "Impact"

    ]

    frame = pd.DataFrame(
        rows,
        columns=columns
    )

    if not frame.empty:

        frame = frame.drop_duplicates(

            subset=[

                "Symbol",
                "Published",
                "Heading",
                "Details"

            ]

        )

    return frame


# =====================================
# OVERNIGHT NEWS
# =====================================

def fetch_news(
    token,
    symbols,
    cutoff
):

    from option_chain_intelligence import (
        build_instrument_map
    )

    if not token:

        raise ValueError(
            "Upstox token unavailable"
        )

    instruments = build_instrument_map(
        list(symbols)
    )

    key_map = {

        item["Instrument Key"]: symbol

        for symbol, item
        in instruments.items()

    }

    keys = list(key_map)

    if not keys:

        raise ValueError(
            "News instrument mapping empty"
        )

    rows = []

    returned_keys = set()

    errors = []

    current = now()

    session = requests.Session()

    session.headers.update({

        "Authorization":
            f"Bearer {token}",

        "Accept":
            "application/json"

    })

    try:

        for start in range(
            0, len(keys), 30
        ):

            batch = keys[start:start + 30]

            try:

                for page in range(1, 21):

                    response = session.get(

                        UPSTOX + "/v2/news",

                        params={

                            "category":
                                "instrument_keys",

                            "instrument_keys":
                                ",".join(batch),

                            "page": page,

                            "page_size": 100

                        },

                        timeout=(8, 25),

                        allow_redirects=False

                    )

                    response.raise_for_status()

                    if response.status_code != 200:

                        raise ValueError(
                            "Unexpected news response"
                        )

                    payload = response.json()

                    if payload.get("status") != "success":

                        raise ValueError(
                            "News API unsuccessful"
                        )

                    data = payload.get("data")

                    if not isinstance(data, dict):

                        raise ValueError(
                            "Unexpected news format"
                        )

                    count = 0

                    for key, articles in data.items():

                        if key not in batch:

                            continue

                        returned_keys.add(key)

                        if not isinstance(
                            articles, list
                        ):

                            continue

                        count += len(articles)

                        for article in articles:

                            published = parse_timestamp(

                                article.get(
                                    "published_time"
                                )

                            )

                            if published is None:

                                continue

                            if not (
                                cutoff < published
                                <= current
                            ):

                                continue

                            heading = clean_text(

                                article.get("heading")

                                or article.get("title")

                            )

                            if not heading:

                                continue

                            rows.append({

                                "Symbol":
                                    key_map[key],

                                "Published":
                                    published.isoformat(),

                                "Heading":
                                    heading,

                                "Summary":
                                    clean_text(
                                        article.get(
                                            "summary"
                                        )
                                    ),

                                "URL":
                                    clean_text(
                                        article.get(
                                            "article_link"
                                        )
                                    ),

                                "Impact":
                                    "MANUAL REVIEW REQUIRED"

                            })

                    if count < 100:

                        break

                    if page == 20:

                        errors.append(
                            "Pagination limit reached"
                        )

                    time.sleep(0.35)

            except Exception as error:

                errors.append(
                    str(error)
                )

    finally:

        session.close()

    columns = [

        "Symbol",
        "Published",
        "Heading",
        "Summary",
        "URL",
        "Impact"

    ]

    frame = pd.DataFrame(
        rows,
        columns=columns
    )

    if not frame.empty:

        frame = frame.drop_duplicates(

            subset=[

                "Symbol",
                "Published",
                "Heading"

            ]

        )

    report = {

        "Requested Symbols":
            len(symbols),

        "Mapped Symbols":
            len(keys),

        "Returned Instrument Keys":
            len(returned_keys),

        "News Records":
            len(frame),

        "Coverage Verified":
            False,

        "Errors":
            errors

    }

    return frame, report


# =====================================
# NSE PRE-OPEN DATA
# =====================================

def fetch_preopen(
    session,
    symbols
):

    if not preopen_window():

        raise ValueError(

            "Outside 09:00-09:15 IST "
            "pre-open window"

        )

    data = nse_get(

        session,

        "/api/market-data-pre-open",

        {"key": "FO"}

    )

    if not isinstance(data, dict):

        raise ValueError(
            "Invalid pre-open response"
        )

    records = data.get("data")

    if not isinstance(records, list):

        raise ValueError(
            "Missing pre-open records"
        )

    rows = []

    current = now()

    for item in records:

        meta = item.get("metadata") or {}

        detail = item.get("detail") or {}

        pre = detail.get(
            "preOpenMarket"
        ) or {}

        symbol = clean_text(

            meta.get("symbol")

        ).upper()

        if symbol not in symbols:

            continue

        timestamp = parse_timestamp(

            pre.get("lastUpdateTime")

            or meta.get("lastUpdateTime")

            or data.get("timestamp")

        )

        previous = number(

            meta.get("previousClose")

        )

        price = number(

            pre.get("finalPrice")

        )

        if price is None or price <= 0:

            price = number(
                meta.get("iep")
            )

        buy = number(

            pre.get("totalBuyQuantity")

        )

        sell = number(

            pre.get("totalSellQuantity")

        )

        matched = number(

            pre.get("totalTradedVolume")

        )

        age = None

        if timestamp is not None:

            age = (

                current - timestamp

            ).total_seconds()

        timestamp_ok = (

            timestamp is not None

            and timestamp.date()
                == current.date()

            and age is not None

            and 0 <= age
                <= MAX_PREOPEN_AGE

        )

        price_ok = (

            previous is not None

            and previous > 0

            and price is not None

            and price > 0

        )

        quantity_ok = (

            buy is not None

            and sell is not None

            and buy >= 0

            and sell >= 0

        )

        gap = (

            (price / previous - 1) * 100

            if price_ok else None

        )

        rows.append({

            "Symbol": symbol,

            "Preopen Timestamp":

                timestamp.isoformat()

                if timestamp else None,

            "Preopen Age Seconds":
                age,

            "Preopen Previous Close":
                previous,

            "Preopen Price":
                price,

            "Preopen Gap %":
                gap,

            "Preopen Buy Quantity":
                buy,

            "Preopen Sell Quantity":
                sell,

            "Preopen Matched Quantity":
                matched,

            "Buy Sell Ratio":
                ratio(buy, sell),

            "Sell Buy Ratio":
                ratio(sell, buy),

            "Preopen Fresh":
                bool(timestamp_ok),

            "Preopen Valid":

                bool(
                    timestamp_ok
                    and price_ok
                    and quantity_ok
                )

        })

    frame = pd.DataFrame(rows)

    if frame.empty:

        raise ValueError(
            "No pre-open stocks returned"
        )

    if frame["Symbol"].duplicated().any():

        raise ValueError(
            "Duplicate pre-open symbols"
        )

    return frame


# =====================================
# CATALYST MAP
# =====================================

def catalyst_map(frame):

    result = {}

    if frame.empty:

        return result

    frame = frame.sort_values(

        "Published",

        ascending=False

    )

    for symbol, group in frame.groupby(

        "Symbol"

    ):

        first = group.iloc[0]

        result[symbol] = {

            "Count": len(group),

            "Latest Heading":
                first.get("Heading", ""),

            "Latest Published":
                first.get("Published", ""),

            "Event Type":
                first.get("Event Type", "")

        }

    return result


# =====================================
# COMBINED STOCK IDENTIFICATION
# =====================================

def classify_stock(
    item,
    market_coverage,
    active_window,
    base_session_verified
):

    category = clean_text(

        item.get("Category")

    ).upper()

    price = number(

        item.get("Change %")

    )

    volume = number(

        item.get("Volume Ratio")

    )

    turnover = number(

        item.get("Turnover Ratio")

    )

    futures_oi = number(

        item.get("Futures OI Change %")

    )

    gap = number(

        item.get("Preopen Gap %")

    )

    buy_sell = number(

        item.get("Buy Sell Ratio")

    )

    sell_buy = number(

        item.get("Sell Buy Ratio")

    )

    matched = number(

        item.get(
            "Preopen Matched Quantity"
        )

    )

    cash_activity = (

        volume is not None

        and turnover is not None

        and volume >= MIN_CASH_RATIO

        and turnover >= MIN_CASH_RATIO

    )

    futures_activity = (

        futures_oi is not None

        and abs(futures_oi)
            >= MIN_FUTURES_OI

    )

    historical_activity = (

        clean_text(

            item.get(
                "Historical Opportunity"
            )

        ) == "EARLY ACTIVITY WATCH"

    )

    news_count = int(

        number(

            item.get("Overnight News Count")

        ) or 0

    )

    announcement_count = int(

        number(

            item.get(
                "Overnight Announcement Count"
            )

        ) or 0

    )

    catalyst = (

        news_count > 0

        or announcement_count > 0

    )

    previous_bullish = category in [

        "PRIORITY BULLISH",

        "PRELIMINARY BULLISH"

    ]

    previous_bearish = category in [

        "PRIORITY BEARISH",

        "PRELIMINARY BEARISH"

    ]

    valid = (

        item.get("Preopen Valid") is True

        and matched is not None

        and matched >= MIN_MATCHED_QUANTITY

    )

    old_close = number(

        item.get("Close")

    )

    preopen_close = number(

        item.get("Preopen Previous Close")

    )

    close_match = (

        old_close is not None

        and old_close > 0

        and preopen_close is not None

        and abs(

            preopen_close / old_close - 1

        ) <= 0.001

    )

    morning_bullish = (

        valid

        and gap is not None

        and gap >= MIN_GAP

        and buy_sell is not None

        and buy_sell >= MIN_DEMAND_RATIO

    )

    morning_bearish = (

        valid

        and gap is not None

        and gap <= -MIN_GAP

        and sell_buy is not None

        and sell_buy >= MIN_DEMAND_RATIO

    )

    evidence = []

    if cash_activity:

        evidence.append(
            "CASH PARTICIPATION"
        )

    if futures_activity:

        evidence.append(
            "FUTURES OI ACTIVITY"
        )

    if historical_activity:

        evidence.append(
            "HISTORICAL EARLY ACTIVITY"
        )

    if catalyst:

        evidence.append(
            "OVERNIGHT CATALYST"
        )

    if morning_bullish:

        evidence.append(
            "BULLISH PREOPEN REACTION"
        )

    if morning_bearish:

        evidence.append(
            "BEARISH PREOPEN REACTION"
        )

    direction = "UNCONFIRMED"

    priority_allowed = (

        active_window

        and market_coverage
            >= MIN_PREOPEN_COVERAGE

        and valid

        and close_match

        and base_session_verified

    )

    if (

        valid

        and gap is not None

        and abs(gap) >= EXTENDED_GAP

    ):

        status = "EXTENDED GAP - WAIT"

        rank = 5

    elif (

        valid

        and (

            previous_bullish
            and morning_bearish

            or previous_bearish
            and morning_bullish

        )

    ):

        status = "CONFLICT - WAIT"

        rank = 5

    elif (

        priority_allowed

        and previous_bullish

        and morning_bullish

    ):

        status = "PRIORITY BULLISH"

        direction = "BULLISH WATCH"

        rank = 1

    elif (

        priority_allowed

        and previous_bearish

        and morning_bearish

    ):

        status = "PRIORITY BEARISH"

        direction = "BEARISH WATCH"

        rank = 1

    elif catalyst:

        status = "FRESH CATALYST WATCH"

        rank = 2

    elif (

        historical_activity

        or (

            cash_activity

            and futures_activity

            and price is not None

            and abs(price) <= 1

        )

        or category == "EMERGING ACTIVITY"

    ):

        status = "EARLY ACTIVITY WATCH"

        rank = 3

    elif (

        previous_bullish

        or previous_bearish

    ):

        status = "PREVIOUS DIRECTION WATCH"

        direction = (

            "BULLISH WATCH"

            if previous_bullish

            else "BEARISH WATCH"

        )

        rank = 4

    else:

        status = "OBSERVATION ONLY"

        rank = 6

    item.update({

        "Pre-Market Category":
            status,

        "Pre-Market Rank":
            rank,

        "Morning Direction":
            direction,

        "Morning Evidence":
            " | ".join(evidence),

        "Evidence Groups":
            len(evidence),

        "Previous Close Match":
            bool(close_match),

        "Priority Allowed":
            bool(priority_allowed),

        "Live Confirmation Required":
            True,

        "Trade Signal":
            "NOT GENERATED"

    })

    return item


# =====================================
# MASTER PRE-MARKET SCANNER
# =====================================

def scan_premarket(
    token,
    base_session_verified=False
):

    started = now()

    base, latest, history_diag, history_status = (
        load_base()
    )

    symbols = set(

        base["Symbol"].tolist()

    )

    cash_date = latest["cash_date"]

    cutoff = datetime.combine(

        cash_date,

        datetime.min.time(),

        tzinfo=IST

    ).replace(

        hour=15,

        minute=30

    )

    diagnostics = {

        "Version": VERSION,

        "Cash Report Date":
            str(cash_date),

        "Previous Session Verified":

            bool(base_session_verified),

        "Universe":
            len(symbols),

        "History Status":
            history_status,

        "Archive Diagnostics":
            history_diag,

        "Catalyst Cutoff":
            cutoff.isoformat(),

        "Errors": []

    }

    news = pd.DataFrame()

    announcements = pd.DataFrame()

    preopen = pd.DataFrame()

    session = create_nse_session()

    try:

        try:

            session.get(

                NSE + "/",

                timeout=(8, 15),

                allow_redirects=False

            )

            announcements = (
                fetch_announcements(
                    session,
                    symbols,
                    cutoff
                )
            )

            diagnostics[
                "Announcements"
            ] = {

                "Status": "RECEIVED",

                "Records":
                    len(announcements),

                "Coverage Verified":
                    False

            }

        except Exception as error:

            diagnostics["Errors"].append({

                "Source": "ANNOUNCEMENTS",

                "Error": str(error)

            })

        try:

            news, news_report = fetch_news(

                token,

                symbols,

                cutoff

            )

            diagnostics["News"] = (
                news_report
            )

        except Exception as error:

            diagnostics["Errors"].append({

                "Source": "NEWS",

                "Error": str(error)

            })

        if preopen_window():

            try:

                preopen = fetch_preopen(

                    session,

                    symbols

                )

            except Exception as error:

                diagnostics["Errors"].append({

                    "Source": "PREOPEN",

                    "Error": str(error)

                })

        else:

            diagnostics["Preopen Status"] = (

                "OUTSIDE PREOPEN WINDOW"

            )

    finally:

        session.close()

    active_window = preopen_window()

    if preopen.empty:

        coverage = 0.0

        valid_count = 0

    else:

        valid_count = int(

            preopen[
                "Preopen Valid"
            ].sum()

        )

        coverage = (

            valid_count / len(symbols)

        )

    news_map = catalyst_map(news)

    ann_map = catalyst_map(
        announcements
    )

    preopen_map = {

        row["Symbol"]: row.to_dict()

        for _, row in preopen.iterrows()

    }

    results = []

    for _, row in base.iterrows():

        item = row.to_dict()

        symbol = item["Symbol"]

        item.update(

            preopen_map.get(

                symbol,

                {

                    "Preopen Fresh": False,

                    "Preopen Valid": False

                }

            )

        )

        n = news_map.get(symbol, {})

        a = ann_map.get(symbol, {})

        item.update({

            "Overnight News Count":
                n.get("Count", 0),

            "Latest Overnight News":
                n.get("Latest Heading", ""),

            "Overnight Announcement Count":
                a.get("Count", 0),

            "Latest Overnight Announcement":
                a.get("Latest Heading", ""),

            "Overnight Event Type":
                a.get("Event Type", "")

        })

        results.append(

            classify_stock(

                item,

                coverage,

                active_window,

                base_session_verified

            )

        )

    frame = pd.DataFrame(results)

    frame = frame.sort_values(

        [

            "Pre-Market Rank",

            "Evidence Groups",

            "Symbol"

        ],

        ascending=[True, False, True]

    ).reset_index(drop=True)

    diagnostics.update({

        "Started At":
            started.isoformat(),

        "Completed At":
            now().isoformat(),

        "Preopen Window Active":
            active_window,

        "Valid Preopen Stocks":
            valid_count,

        "Preopen Coverage %":
            round(coverage * 100, 2),

        "Priority Bullish":

            int(

                frame["Pre-Market Category"]

                .eq("PRIORITY BULLISH")

                .sum()

            ),

        "Priority Bearish":

            int(

                frame["Pre-Market Category"]

                .eq("PRIORITY BEARISH")

                .sum()

            ),

        "Fresh Catalyst Stocks":

            int(

                frame["Pre-Market Category"]

                .eq("FRESH CATALYST WATCH")

                .sum()

            ),

        "Early Activity Stocks":

            int(

                frame["Pre-Market Category"]

                .eq("EARLY ACTIVITY WATCH")

                .sum()

            ),

        "Status": (

            "PREOPEN RESEARCH AVAILABLE"

            if active_window

            and coverage >= MIN_PREOPEN_COVERAGE

            and base_session_verified

            else "PRELIMINARY RESEARCH ONLY"

        ),

        "Option Closing Timestamp Verified":
            False,

        "Independent NSE Cross Check":
            False,

        "Trade Signal":
            "NOT GENERATED"

    })

    return (

        frame,

        news,

        announcements,

        diagnostics

    )


# =====================================
# STREAMLIT INTERFACE
# =====================================

def render_premarket():

    st.header(

        "INTEGRATED PRE-MARKET V"

        + VERSION

    )

    st.caption(

        "Full F&O Universe | "

        "Cash + Futures + Options + Catalysts"

    )

    st.warning(

        "Pre-market identification is not "

        "a confirmed buy or sell signal. "

        "Live confirmation is required."

    )

    st.caption(

        "Current IST: "

        + now().strftime(

            "%d-%m-%Y %H:%M:%S"

        )

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

            key="premarket_v23_token"

        )

    try:

        base, latest, _, _ = load_base()

        st.info(

            "Historical Base Date: "

            + str(latest["cash_date"])

        )

    except Exception as error:

        st.info(str(error))

    verified = st.checkbox(

        "I have verified that the displayed "

        "historical base date is the latest "

        "completed NSE trading session.",

        value=False

    )

    if not preopen_window():

        st.info(

            "Outside NSE pre-open window. "

            "Overnight research remains available. "

            "Morning priority signals are disabled."

        )

    if st.button(

        "GENERATE PRE-MARKET INTELLIGENCE",

        type="primary"

    ):

        st.session_state.pop(

            "premarket_v23_result",

            None

        )

        try:

            with st.spinner(

                "Analyzing full F&O universe..."

            ):

                st.session_state[

                    "premarket_v23_result"

                ] = scan_premarket(

                    token.strip(),

                    verified

                )

        except Exception as error:

            st.error(str(error))

    result = st.session_state.get(

        "premarket_v23_result"

    )

    if result is None:

        return

    frame, news, announcements, report = result

    st.caption(

        "Generated: "

        + report["Completed At"]

    )

    st.info(report["Status"])

    a, b, c = st.columns(3)

    a.metric(

        "Universe",

        report["Universe"]

    )

    b.metric(

        "Preopen Coverage",

        f'{report["Preopen Coverage %"]}%'

    )

    c.metric(

        "Fresh Catalysts",

        report["Fresh Catalyst Stocks"]

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

        "Early Activity",

        report["Early Activity Stocks"]

    )

    columns = [

        "Symbol",

        "Pre-Market Category",

        "Morning Direction",

        "Category",

        "Close",

        "Change %",

        "Volume Ratio",

        "Turnover Ratio",

        "Delivery Quantity Ratio",

        "Futures OI Change %",

        "Futures Buildup",

        "PCR OI",

        "Historical Opportunity",

        "Preopen Price",

        "Preopen Gap %",

        "Buy Sell Ratio",

        "Sell Buy Ratio",

        "Preopen Matched Quantity",

        "Preopen Fresh",

        "Overnight News Count",

        "Overnight Announcement Count",

        "Latest Overnight News",

        "Latest Overnight Announcement",

        "Morning Evidence"

    ]

    columns = [

        col for col in columns

        if col in frame.columns

    ]

    tabs = st.tabs([

        "Bullish",

        "Bearish",

        "Early Activity",

        "Fresh Catalysts",

        "Conflict / Extended",

        "Previous Watch",

        "All Stocks",

        "News",

        "Announcements",

        "Diagnostics"

    ])

    categories = frame[

        "Pre-Market Category"

    ]

    with tabs[0]:

        st.dataframe(

            frame[

                categories.eq(

                    "PRIORITY BULLISH"

                )

            ][columns],

            hide_index=True,

            use_container_width=True

        )

    with tabs[1]:

        st.dataframe(

            frame[

                categories.eq(

                    "PRIORITY BEARISH"

                )

            ][columns],

            hide_index=True,

            use_container_width=True

        )

    with tabs[2]:

        st.dataframe(

            frame[

                categories.eq(

                    "EARLY ACTIVITY WATCH"

                )

            ][columns],

            hide_index=True,

            use_container_width=True

        )

    with tabs[3]:

        st.dataframe(

            frame[

                categories.eq(

                    "FRESH CATALYST WATCH"

                )

            ][columns],

            hide_index=True,

            use_container_width=True

        )

    with tabs[4]:

        st.dataframe(

            frame[

                categories.isin([

                    "CONFLICT - WAIT",

                    "EXTENDED GAP - WAIT"

                ])

            ][columns],

            hide_index=True,

            use_container_width=True

        )

    with tabs[5]:

        st.dataframe(

            frame[

                categories.eq(

                    "PREVIOUS DIRECTION WATCH"

                )

            ][columns],

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

        st.dataframe(

            news,

            hide_index=True,

            use_container_width=True

        )

    with tabs[8]:

        st.dataframe(

            announcements,

            hide_index=True,

            use_container_width=True

        )

    with tabs[9]:

        st.json(report)

    st.download_button(

        "DOWNLOAD PRE-MARKET WATCHLIST",

        frame.to_csv(index=False),

        "premarket_watchlist_v23.csv",

        mime="text/csv"

    )

    st.download_button(

        "DOWNLOAD OVERNIGHT NEWS",

        news.to_csv(index=False),

        "overnight_news_v23.csv",

        mime="text/csv"

    )

    st.download_button(

        "DOWNLOAD ANNOUNCEMENTS",

        announcements.to_csv(index=False),

        "overnight_announcements_v23.csv",

        mime="text/csv"

    )

    st.download_button(

        "DOWNLOAD PRE-MARKET DIAGNOSTICS",

        json.dumps(

            report,

            indent=2,

            default=str

        ),

        "premarket_diagnostics_v23.json",

        mime="application/json"

    )
