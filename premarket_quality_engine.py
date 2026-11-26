
"""
F&O PRE-MARKET QUALITY INTELLIGENCE
Version 2.3.1

Reuses V2.3 working functions.
No automatic orders.
No strike selection.
Stock identification only.
"""

import os
import re
import json
import math
import unicodedata

from datetime import datetime
from concurrent.futures import ThreadPoolExecutor

import pandas as pd
import requests
import streamlit as st

import premarket_intelligence_engine as old


VERSION = "2.3.1"

EXTENDED_MOVE = 3.0
EXTENDED_GAP = 2.5
MAX_PREOPEN_AGE = 180
MIN_PREOPEN_COVERAGE = 0.90

NSE_HOSTS = [
    "https://www.nseindia.in",
    "https://www.nseindia.com"
]

GENERAL_NEWS = re.compile(
    r"\b("
    r"market wrap|market outlook|"
    r"stocks to watch|stocks in focus|"
    r"top gainers|top losers|"
    r"market today|market closing|"
    r"share market live|"
    r"stocks to buy|"
    r"nifty and sensex|"
    r"sensex and nifty|"
    r"market highlights|"
    r"stock market news"
    r")\b",
    re.I
)

CATALYST_WORDS = re.compile(
    r"\b("
    r"order|contract|acquisition|merger|"
    r"results|profit|loss|revenue|"
    r"penalty|approval|investigation|"
    r"buyback|dividend|resignation|"
    r"stake|fundraising|downgrade|upgrade|"
    r"guidance|default|expansion|"
    r"investment|partnership|"
    r"regulatory|recall|"
    r"joint venture|fund raising"
    r")\b",
    re.I
)

# Conservative aliases.
# Unmapped company names remain
# available for manual review.

ALIASES = {
    "APLAPOLLO": ["APL Apollo"],
    "KEI": ["KEI Industries"],
    "POLYCAB": ["Polycab"],
    "SONACOMS": ["Sona Comstar", "Sona BLW"],
    "PHOENIXLTD": ["Phoenix Mills"],
    "RELIANCE": ["Reliance Industries"],
    "HDFCBANK": ["HDFC Bank"],
    "ICICIBANK": ["ICICI Bank"],
    "SBIN": ["State Bank of India", "SBI"],
    "INFY": ["Infosys"],
    "TCS": ["Tata Consultancy Services"],
    "HCLTECH": ["HCL Tech", "HCL Technologies"],
    "PRESTIGE": ["Prestige Estates"],
    "PAYTM": ["Paytm", "One97 Communications"],
    "DIXON": ["Dixon Technologies"],
    "KFINTECH": ["KFin Technologies"],
    "TORNTPHARM": ["Torrent Pharma"],
    "COFORGE": ["Coforge"],
    "CONCOR": ["Container Corporation"],
    "HINDUNILVR": ["Hindustan Unilever"],
    "GODREJCP": ["Godrej Consumer"],
    "HDFCLIFE": ["HDFC Life"],
    "MCX": ["Multi Commodity Exchange"],
    "BSE": ["BSE Limited", "BSE Ltd"]
}


def now():
    return old.now()


def number(value):
    return old.number(value)


def text(value):
    return old.clean_text(value)


def normalize(value):

    value = unicodedata.normalize(
        "NFKC", text(value)
    ).casefold()

    value = re.sub(
        r"[^\w\s]", " ", value
    )

    return " ".join(value.split())


def truth(value):

    return str(value).strip().lower() in (
        "true", "1", "yes"
    )


def company_mentioned(symbol, heading):

    aliases = ALIASES.get(symbol, [])

    if len(symbol) >= 4:
        aliases = aliases + [symbol]

    heading = normalize(heading)

    for alias in aliases:

        pattern = (
            r"(?<!\w)"
            + re.escape(normalize(alias))
            + r"(?!\w)"
        )

        if re.search(pattern, heading):
            return True

    return False


# =====================================
# NEWS QUALITY FILTER
# =====================================

def filter_news(frame):

    if frame.empty:

        return frame.copy(), {
            "Raw Records": 0,
            "Unique Headlines": 0,
            "Stock Specific Records": 0,
            "Manual Review Records": 0,
            "General Market Records": 0
        }

    result = frame.copy()

    result["Normalized Heading"] = (
        result["Heading"].map(normalize)
    )

    result = result.sort_values(
        "Published",
        ascending=False
    )

    result = result.drop_duplicates(
        subset=[
            "Symbol",
            "Normalized Heading"
        ]
    )

    unique_headlines = (
        result["Normalized Heading"].nunique()
    )

    groups = result.groupby(
        "Normalized Heading"
    )["Symbol"].nunique()

    result["Headline Stock Count"] = (
        result["Normalized Heading"].map(groups)
    )

    def classify(row):

        heading = text(row["Heading"])
        symbol = text(row["Symbol"])

        if GENERAL_NEWS.search(heading):
            return "GENERAL MARKET NEWS"

        if row["Headline Stock Count"] >= 5:
            return "MULTI STOCK NEWS - REVIEW"

        if not company_mentioned(
            symbol, heading
        ):
            return "RELEVANCE UNVERIFIED"

        if not CATALYST_WORDS.search(
            heading
        ):
            return "COMPANY NEWS - REVIEW"

        return "STOCK SPECIFIC CATALYST"

    result["News Quality"] = result.apply(
        classify, axis=1
    )

    accepted = result[
        result["News Quality"].eq(
            "STOCK SPECIFIC CATALYST"
        )
    ].copy()

    general = int(
        result["News Quality"].eq(
            "GENERAL MARKET NEWS"
        ).sum()
    )

    report = {
        "Raw Records": len(frame),
        "Deduplicated Records": len(result),
        "Unique Headlines": unique_headlines,
        "Stock Specific Records": len(accepted),
        "General Market Records": general,
        "Manual Review Records": (
            len(result) - len(accepted) - general
        ),
        "Impact Direction Verified": False
    }

    return result, report


# =====================================
# NSE CONNECTION WITH HOST FALLBACK
# =====================================

def nse_session(host):

    session = requests.Session()

    session.headers.update({
        "User-Agent": (
            "Mozilla/5.0 "
            "(Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 "
            "Chrome/131.0.0.0 Safari/537.36"
        ),
        "Accept": "application/json,text/html,*/*",
        "Accept-Language": "en-IN,en;q=0.9",
        "Referer": host + "/"
    })

    return session


def nse_request(path, params):

    attempts = []

    for host in NSE_HOSTS:

        session = nse_session(host)

        try:

            home = session.get(
                host + "/",
                timeout=(5, 10),
                allow_redirects=False
            )

            response = session.get(
                host + path,
                params=params,
                timeout=(5, 15),
                allow_redirects=False
            )

            attempt = {
                "Host": host,
                "Homepage HTTP": home.status_code,
                "API HTTP": response.status_code,
                "Content Type": response.headers.get(
                    "Content-Type", ""
                )
            }

            attempts.append(attempt)

            if response.status_code != 200:
                continue

            if len(response.content) > 30_000_000:
                attempt["Error"] = "Response too large"
                continue

            try:
                payload = response.json()

            except ValueError:
                attempt["Error"] = "Non JSON response"
                continue

            return payload, attempts

        except Exception as error:

            attempts.append({
                "Host": host,
                "Error": str(error)
            })

        finally:
            session.close()

    return None, attempts


# =====================================
# FRESH CORPORATE ANNOUNCEMENTS
# =====================================

def fetch_announcements(symbols, cutoff):

    payload, attempts = nse_request(
        "/api/corporate-announcements",
        {
            "index": "equities",
            "from_date": cutoff.strftime(
                "%d-%m-%Y"
            ),
            "to_date": now().strftime(
                "%d-%m-%Y"
            )
        }
    )

    columns = [
        "Symbol",
        "Published",
        "Heading",
        "Details",
        "Event Type",
        "Attachment",
        "Impact",
        "Catalyst Eligible"
    ]

    if not isinstance(payload, list):

        return pd.DataFrame(
            columns=columns
        ), {
            "Status": "FAILED",
            "Attempts": attempts,
            "Coverage Verified": False
        }

    rows = []
    rejected = 0
    current = now()

    for item in payload:

        if not isinstance(item, dict):
            rejected += 1
            continue

        symbol = text(
            item.get("symbol")
        ).upper()

        if symbol not in symbols:
            continue

        published = old.parse_timestamp(
            item.get("an_dt")
            or item.get("sort_date")
        )

        if published is None:
            rejected += 1
            continue

        if not cutoff < published <= current:
            continue

        heading = text(item.get("desc"))

        details = text(
            item.get("attchmntText")
        )

        combined = (
            heading + " " + details
        ).strip()

        if not combined:
            rejected += 1
            continue

        event = old.classify_event(combined)

        rows.append({
            "Symbol": symbol,
            "Published": published.isoformat(),
            "Heading": heading,
            "Details": details,
            "Event Type": event,
            "Attachment": text(
                item.get("attchmntFile")
            ),
            "Impact": "MANUAL REVIEW REQUIRED",
            "Catalyst Eligible": (
                event != "OTHER DISCLOSURE"
            )
        })

    frame = pd.DataFrame(
        rows, columns=columns
    ).drop_duplicates(
        subset=[
            "Symbol",
            "Published",
            "Heading",
            "Details"
        ]
    )

    report = {
        "Status": "RECEIVED",
        "Raw Records": len(payload),
        "Relevant Records": len(frame),
        "Rejected Records": rejected,
        "Catalyst Records": int(
            frame["Catalyst Eligible"].sum()
        ),
        "Coverage Verified": False,
        "Attempts": attempts
    }

    return frame, report


# =====================================
# PREOPEN FIRST
# =====================================

def collect_preopen(symbols):

    if not old.preopen_window():

        return pd.DataFrame(), {
            "Status": "OUTSIDE PREOPEN WINDOW"
        }

    payload, attempts = nse_request(
        "/api/market-data-pre-open",
        {"key": "FO"}
    )

    if not isinstance(payload, dict):

        return pd.DataFrame(), {
            "Status": "FAILED",
            "Attempts": attempts
        }

    records = payload.get("data")

    if not isinstance(records, list):

        return pd.DataFrame(), {
            "Status": "INVALID RESPONSE",
            "Attempts": attempts
        }

    rows = []
    current = now()

    for item in records:

        if not isinstance(item, dict):
            continue

        meta = item.get("metadata") or {}

        pre = (
            item.get("detail") or {}
        ).get("preOpenMarket") or {}

        symbol = text(
            meta.get("symbol")
        ).upper()

        if symbol not in symbols:
            continue

        timestamp = old.parse_timestamp(
            pre.get("lastUpdateTime")
            or meta.get("lastUpdateTime")
        )

        previous = number(
            meta.get("previousClose")
        )

        price = number(
            pre.get("finalPrice")
        )

        if price is None or price <= 0:
            price = number(meta.get("iep"))

        buy = number(
            pre.get("totalBuyQuantity")
        )

        sell = number(
            pre.get("totalSellQuantity")
        )

        matched = number(
            pre.get("totalTradedVolume")
        )

        age = (
            (current - timestamp).total_seconds()
            if timestamp is not None
            else None
        )

        fresh = (
            timestamp is not None
            and timestamp.date() == current.date()
            and age is not None
            and 0 <= age <= MAX_PREOPEN_AGE
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
            and matched is not None
            and buy >= 0
            and sell >= 0
            and matched > 0
        )

        rows.append({
            "Symbol": symbol,
            "Preopen Timestamp": (
                timestamp.isoformat()
                if timestamp else None
            ),
            "Preopen Age Seconds": age,
            "Preopen Previous Close": previous,
            "Preopen Price": price,
            "Preopen Gap %": (
                (price / previous - 1) * 100
                if price_ok else None
            ),
            "Preopen Buy Quantity": buy,
            "Preopen Sell Quantity": sell,
            "Preopen Matched Quantity": matched,
            "Buy Sell Ratio": old.ratio(buy, sell),
            "Sell Buy Ratio": old.ratio(sell, buy),
            "Preopen Fresh": bool(fresh),
            "Preopen Valid": bool(
                fresh and price_ok and quantity_ok
            )
        })

    frame = pd.DataFrame(rows)

    if frame.empty:

        return frame, {
            "Status": "NO RECORDS",
            "Attempts": attempts
        }

    if frame["Symbol"].duplicated().any():

        return pd.DataFrame(), {
            "Status": "DUPLICATE SYMBOLS",
            "Attempts": attempts
        }

    return frame, {
        "Status": "RECEIVED",
        "Records": len(frame),
        "Attempts": attempts
    }


# =====================================
# FINAL FRESHNESS RECHECK
# =====================================

def recheck_preopen(frame):

    if frame.empty:
        return frame

    result = frame.copy()

    current = now()

    for index, row in result.iterrows():

        stamp = old.parse_timestamp(
            row["Preopen Timestamp"]
        )

        age = (
            (current - stamp).total_seconds()
            if stamp is not None else None
        )

        fresh = (
            stamp is not None
            and stamp.date() == current.date()
            and age is not None
            and 0 <= age <= MAX_PREOPEN_AGE
            and old.preopen_window()
        )

        result.at[
            index, "Preopen Age Seconds"
        ] = age

        result.at[
            index, "Preopen Fresh"
        ] = bool(fresh)

        result.at[
            index, "Preopen Valid"
        ] = bool(
            fresh
            and truth(row["Preopen Valid"])
        )

    return result


# =====================================
# NEWS COLLECTION SAFETY
# =====================================

def collect_news(token, symbols, cutoff):

    if not token:

        return pd.DataFrame(), {
            "Status": "TOKEN MISSING",
            "Coverage Verified": False
        }

    try:

        frame, report = old.fetch_news(
            token,
            symbols,
            cutoff
        )

        report["Status"] = (
            "PARTIAL"
            if report.get("Errors")
            else "RECEIVED"
        )

        return frame, report

    except Exception as error:

        return pd.DataFrame(), {
            "Status": "FAILED",
            "Error": str(error),
            "Coverage Verified": False
        }


# =====================================
# EXTENDED MOVEMENT PROTECTION
# =====================================

def classify_stock(
    item,
    coverage,
    active,
    verified
):

    item["Preopen Valid"] = truth(
        item.get("Preopen Valid")
    )

    item = old.classify_stock(
        item,
        coverage,
        active,
        verified
    )

    change = number(
        item.get("Change %")
    )

    gap = number(
        item.get("Preopen Gap %")
    )

    combined_move = None

    if (
        change is not None
        and gap is not None
        and item["Preopen Valid"]
    ):

        combined_move = (
            (1 + change / 100)
            * (1 + gap / 100)
            - 1
        ) * 100

    extended = (
        (
            change is not None
            and abs(change) >= EXTENDED_MOVE
        )
        or (
            item["Preopen Valid"]
            and gap is not None
            and abs(gap) >= EXTENDED_GAP
        )
        or (
            combined_move is not None
            and abs(combined_move) >= EXTENDED_MOVE
        )
    )

    if extended:

        item.update({
            "Pre-Market Category":
                "EXTENDED MOVEMENT - WAIT",
            "Pre-Market Rank": 5,
            "Morning Direction": "UNCONFIRMED",
            "Priority Allowed": False
        })

    item["Combined Move %"] = combined_move

    item["Extended Movement"] = bool(extended)

    item["Trade Signal"] = "NOT GENERATED"

    return item


# =====================================
# INTEGRATED QUALITY SCAN
# =====================================

def scan_market(token, verified=False):

    started = now()

    base, latest, archive_diag, history_status = (
        old.load_base()
    )

    if base["Symbol"].duplicated().any():
        raise ValueError(
            "Duplicate historical symbols"
        )

    symbols = set(base["Symbol"])

    if not symbols:
        raise ValueError(
            "Historical universe unavailable"
        )

    cash_date = latest["cash_date"]

    cutoff = datetime.combine(
        cash_date,
        datetime.min.time(),
        tzinfo=old.IST
    ).replace(hour=15, minute=30)

    # Time-sensitive source first.

    preopen, preopen_report = (
        collect_preopen(symbols)
    )

    # Independent sources run concurrently.

    with ThreadPoolExecutor(
        max_workers=2
    ) as pool:

        news_task = pool.submit(
            collect_news,
            token,
            symbols,
            cutoff
        )

        ann_task = pool.submit(
            fetch_announcements,
            symbols,
            cutoff
        )

        raw_news, news_report = (
            news_task.result()
        )

        announcements, ann_report = (
            ann_task.result()
        )

    news, quality_report = filter_news(
        raw_news
    )

    if news.empty:

        accepted_news = news

    else:

        accepted_news = news[
            news["News Quality"].eq(
                "STOCK SPECIFIC CATALYST"
            )
        ]

    accepted_ann = announcements[
        announcements["Catalyst Eligible"]
        .map(truth)
    ]

    # Refresh once if initial snapshot
    # became stale during news collection.

    preopen = recheck_preopen(preopen)

    valid_count = (
        int(preopen["Preopen Valid"].sum())
        if not preopen.empty else 0
    )

    coverage = valid_count / len(symbols)

    if (
        old.preopen_window()
        and coverage < MIN_PREOPEN_COVERAGE
    ):

        retry, retry_report = (
            collect_preopen(symbols)
        )

        preopen_report["Refresh Attempt"] = (
            retry_report
        )

        if not retry.empty:
            preopen = recheck_preopen(retry)

    valid_count = (
        int(preopen["Preopen Valid"].sum())
        if not preopen.empty else 0
    )

    coverage = valid_count / len(symbols)

    active = old.preopen_window()

    news_map = old.catalyst_map(
        accepted_news
    )

    ann_map = old.catalyst_map(
        accepted_ann
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
                active,
                verified
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

    counts = frame[
        "Pre-Market Category"
    ].value_counts().to_dict()

    report = {
        "Version": VERSION,
        "Cash Report Date": str(cash_date),
        "Previous Session Manually Verified":
            bool(verified),
        "Universe": len(symbols),
        "Processed Stocks": len(frame),
        "History Status": history_status,
        "Archive Diagnostics": archive_diag,
        "Catalyst Cutoff": cutoff.isoformat(),
        "News": news_report,
        "News Quality": quality_report,
        "Announcements": ann_report,
        "Preopen": preopen_report,
        "Preopen Window Active": active,
        "Valid Preopen Stocks": valid_count,
        "Preopen Coverage %": round(
            coverage * 100, 2
        ),
        "Category Counts": counts,
        "Priority Bullish": counts.get(
            "PRIORITY BULLISH", 0
        ),
        "Priority Bearish": counts.get(
            "PRIORITY BEARISH", 0
        ),
        "Fresh Catalyst Stocks": counts.get(
            "FRESH CATALYST WATCH", 0
        ),
        "Early Activity Stocks": counts.get(
            "EARLY ACTIVITY WATCH", 0
        ),
        "Extended Stocks": counts.get(
            "EXTENDED MOVEMENT - WAIT", 0
        ),
        "Status": (
            "PREOPEN RESEARCH AVAILABLE"
            if active
            and verified
            and coverage >= MIN_PREOPEN_COVERAGE
            else "PRELIMINARY RESEARCH ONLY"
        ),
        "Started At": started.isoformat(),
        "Completed At": now().isoformat(),
        "Option Closing Timestamp Verified": False,
        "Independent NSE Cross Check": False,
        "Trade Signal": "NOT GENERATED"
    }

    return frame, news, announcements, report


# =====================================
# STREAMLIT DASHBOARD
# =====================================

def render_premarket():

    st.header(
        "INTEGRATED PRE-MARKET V" + VERSION
    )

    st.caption(
        "Full Universe | Quality Catalysts | "
        "Early Opportunity | No Automatic Orders"
    )

    st.warning(
        "Research watchlist only. "
        "Live confirmation is required."
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
            key="premarket_v231_token"
        )

    try:

        base, latest, _, _ = old.load_base()

        st.info(
            "Historical Base Date: "
            + str(latest["cash_date"])
        )

    except Exception as error:

        st.error(str(error))
        return

    verified = st.checkbox(
        "I verified the historical base date "
        "is the latest completed NSE session.",
        value=False
    )

    st.caption(
        "Current IST: " + now().strftime(
            "%d-%m-%Y %H:%M:%S"
        )
    )

    if not old.preopen_window():

        st.info(
            "Outside pre-open window. "
            "Overnight research only."
        )

    if st.button(
        "GENERATE PRE-MARKET INTELLIGENCE",
        type="primary"
    ):

        st.session_state.pop(
            "premarket_v231_result",
            None
        )

        try:

            with st.spinner(
                "Checking full F&O universe..."
            ):

                st.session_state[
                    "premarket_v231_result"
                ] = scan_market(
                    token.strip(),
                    verified
                )

        except Exception as error:

            st.error(str(error))

    result = st.session_state.get(
        "premarket_v231_result"
    )

    if result is None:
        return

    frame, news, announcements, report = result

    st.caption(
        "Generated: " + report["Completed At"]
    )

    st.info(report["Status"])

    generated = old.parse_timestamp(
        report["Completed At"]
    )

    age = (
        (now() - generated).total_seconds()
        if generated is not None
        else float("inf")
    )

    stale_priority = (
        not old.preopen_window()
        or age > MAX_PREOPEN_AGE
        or age < 0
        or not verified
        or report["Cash Report Date"]
            != str(latest["cash_date"])
    )

    if stale_priority:

        frame = frame.copy()

        priority = frame[
            "Pre-Market Category"
        ].isin([
            "PRIORITY BULLISH",
            "PRIORITY BEARISH"
        ])

        frame.loc[
            priority, "Pre-Market Category"
        ] = "STALE PRIORITY - RESCAN"

        frame.loc[
            priority, "Morning Direction"
        ] = "UNCONFIRMED"

        frame["Priority Allowed"] = False

        st.caption(
            "Priority display disabled when "
            "session or freshness is invalid."
        )

    a, b, c = st.columns(3)

    a.metric(
        "Universe", report["Universe"]
    )

    b.metric(
        "Preopen Coverage",
        str(report["Preopen Coverage %"]) + "%"
    )

    c.metric(
        "Fresh Catalysts",
        report["Fresh Catalyst Stocks"]
    )

    a, b, c = st.columns(3)

    a.metric(
        "Priority Bullish",
        0 if stale_priority
        else report["Priority Bullish"]
    )

    b.metric(
        "Priority Bearish",
        0 if stale_priority
        else report["Priority Bearish"]
    )

    c.metric(
        "Early Activity",
        report["Early Activity Stocks"]
    )

    st.metric(
        "Extended Stocks - Wait",
        report["Extended Stocks"]
    )

    if report["Announcements"]["Status"] != "RECEIVED":

        st.warning(
            "Corporate announcements unavailable. "
            "Catalyst coverage is incomplete."
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
        "Historical Opportunity",
        "Preopen Gap %",
        "Combined Move %",
        "Buy Sell Ratio",
        "Sell Buy Ratio",
        "Preopen Age Seconds",
        "Overnight News Count",
        "Overnight Announcement Count",
        "Latest Overnight News",
        "Latest Overnight Announcement",
        "Morning Evidence"
    ]

    columns = [
        c for c in columns
        if c in frame.columns
    ]

    tabs = st.tabs([
        "Bullish",
        "Bearish",
        "Early Activity",
        "Fresh Catalysts",
        "Extended / Conflict",
        "Previous Watch",
        "All Stocks",
        "News Quality",
        "Announcements",
        "Diagnostics"
    ])

    category = frame["Pre-Market Category"]

    filters = [
        ["PRIORITY BULLISH"],
        ["PRIORITY BEARISH"],
        ["EARLY ACTIVITY WATCH"],
        ["FRESH CATALYST WATCH"],
        [
            "EXTENDED MOVEMENT - WAIT",
            "EXTENDED GAP - WAIT",
            "CONFLICT - WAIT",
            "STALE PRIORITY - RESCAN"
        ],
        ["PREVIOUS DIRECTION WATCH"]
    ]

    for index, values in enumerate(filters):

        with tabs[index]:

            st.dataframe(
                frame[
                    category.isin(values)
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

        st.json(report["News Quality"])

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
        "premarket_watchlist_v231.csv",
        mime="text/csv"
    )

    st.download_button(
        "DOWNLOAD NEWS QUALITY",
        news.to_csv(index=False),
        "news_quality_v231.csv",
        mime="text/csv"
    )

    st.download_button(
        "DOWNLOAD ANNOUNCEMENTS",
        announcements.to_csv(index=False),
        "announcements_v231.csv",
        mime="text/csv"
    )

    st.download_button(
        "DOWNLOAD DIAGNOSTICS",
        json.dumps(
            report,
            indent=2,
            default=str
        ),
        "premarket_diagnostics_v231.json",
        mime="application/json"
    )
