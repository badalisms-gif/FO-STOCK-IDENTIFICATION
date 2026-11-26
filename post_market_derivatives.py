
"""
F&O ADVANCED POST MARKET INTELLIGENCE
Version 1.9.2

Corrected Futures Integration
NSE Historical Fallback
Bulk / Block / Short Selling
Preliminary + Futures Confirmed Watchlist

Research Only
No Automatic Orders
"""

import io
import json
import math
import re
import hashlib
import pandas as pd
import streamlit as st

from daily_collector import DATA

from post_market_engine import (
    build_watchlist,
    available_dates,
    number,
    now
)

from futures_intelligence_engine import (
    fetch_futures,
    analyse_futures
)

VERSION = "1.9.2"

MIN_VOLUME_RATIO = 1.5
MIN_TURNOVER_RATIO = 1.5
MIN_DELIVERY_RATIO = 1.5

MIN_PRICE_MOVE = 0.50
MIN_RELATIVE_STRENGTH = 0.30

MIN_FUTURES_MOVE = 0.50
MIN_OI_CHANGE = 3.0

MAX_EARLY_MOVE = 3.0

FUTURES_COLUMNS = [

    "Report Date",
    "Symbol",
    "Expiry",
    "Close",
    "OI",
    "Volume"

]


# =====================================
# BASIC UTILITIES
# =====================================

def normalize(value):

    return re.sub(

        r"[^a-z0-9]",

        "",

        str(value).lower()

    )


def find_column(frame, aliases):

    mapping = {

        normalize(column): column

        for column in frame.columns

    }

    for alias in aliases:

        key = normalize(alias)

        if key in mapping:

            return mapping[key]

    return None


def numeric(series):

    return pd.to_numeric(

        series.astype(str)
        .str.replace(",", "", regex=False)
        .str.strip(),

        errors="coerce"

    )


def date_values(series):

    return pd.to_datetime(

        series.astype(str),

        format="mixed",

        dayfirst=True,

        errors="coerce"

    ).dt.strftime("%Y-%m-%d")


def finite(value):

    return number(value) is not None


def meets(value, threshold):

    value = number(value)

    return (

        value is not None

        and value >= threshold

    )


# =====================================
# VERIFIED LOCAL STORAGE
# =====================================

def read_verified(path, expected_date=None):

    metadata_path = path.parent / "metadata.json"

    if not path.exists():

        raise ValueError("Report missing")

    if not metadata_path.exists():

        raise ValueError("Metadata missing")

    metadata = json.loads(

        metadata_path.read_text()

    )

    content = path.read_bytes()

    digest = hashlib.sha256(

        content

    ).hexdigest()

    if digest != metadata.get("sha256"):

        raise ValueError("Checksum mismatch")

    frame = pd.read_csv(

        io.BytesIO(content)

    )

    if len(frame) != metadata.get("records"):

        raise ValueError("Record count mismatch")

    if frame.columns.duplicated().any():

        raise ValueError("Duplicate columns")

    if expected_date:

        stored_date = metadata.get(

            "report_date"

        )

        if stored_date is not None:

            if str(stored_date) != str(expected_date):

                raise ValueError(

                    "Metadata date mismatch"

                )

    eligible = find_column(

        frame,

        ["Eligible For Analysis"]

    )

    if eligible:

        frame = frame[

            frame[eligible]
            .astype(str)
            .str.strip()
            .str.lower()
            .eq("true")

        ].copy()

    return frame, metadata


# =====================================
# REPORT DATE VALIDATION
# =====================================

def validate_date(frame, day):

    date_col = find_column(

        frame,

        [

            "Report Date",

            "Trade Date",

            "Trading Date",

            "Date",

            "TRADDT"

        ]

    )

    if date_col is None:

        raise ValueError(

            "Report date column missing"

        )

    if not date_values(

        frame[date_col]

    ).eq(str(day)).all():

        raise ValueError(

            "Report date mismatch"

        )

    return frame


# =====================================
# EXACT FUTURES SCHEMA
# =====================================

def validate_futures(frame, day, universe):

    missing = set(

        FUTURES_COLUMNS

    ) - set(frame.columns)

    if missing:

        raise ValueError(

            "Missing futures columns: "

            + ", ".join(sorted(missing))

        )

    frame = frame[

        FUTURES_COLUMNS

    ].copy()

    validate_date(frame, day)

    frame["Symbol"] = (

        frame["Symbol"]

        .astype(str)

        .str.strip()

        .str.upper()

    )

    frame["Expiry"] = date_values(

        frame["Expiry"]

    )

    if frame["Expiry"].isna().any():

        raise ValueError(

            "Invalid expiry date"

        )

    for column in [

        "Close",

        "OI",

        "Volume"

    ]:

        frame[column] = numeric(

            frame[column]

        )

        if not frame[column].map(finite).all():

            raise ValueError(

                f"Invalid {column}"

            )

        if frame[column].lt(0).any():

            raise ValueError(

                f"Negative {column}"

            )

    if frame.duplicated(

        ["Symbol", "Expiry"]

    ).any():

        raise ValueError(

            "Duplicate futures contracts"

        )

    frame = frame[

        frame["Symbol"].isin(universe)

        &

        frame["Expiry"].ge(str(day))

    ].copy()

    if frame.empty:

        raise ValueError(

            "No eligible stock futures"

        )

    return frame


# =====================================
# FUTURES REPORT WITH NSE FALLBACK
# =====================================

@st.cache_data(ttl=600, show_spinner=False)
def fetch_archive_futures(day):

    report_date = pd.Timestamp(

        day

    ).date()

    frame, url = fetch_futures(

        report_date

    )

    return frame, url


def load_futures(day, universe):

    path = (

        DATA

        / "nse"

        / "futures"

        / str(day)

        / "latest.csv"

    )

    logs = []

    try:

        frame, metadata = read_verified(

            path,

            expected_date=day

        )

        if metadata.get("source") != "futures":

            raise ValueError(

                "Futures source mismatch"

            )

        frame = validate_futures(

            frame,

            day,

            universe

        )

        logs.append({

            "Source": "Futures",

            "Report Date": str(day),

            "Status": "LOCAL VERIFIED",

            "Contracts": len(frame),

            "Stocks": frame["Symbol"].nunique(),

            "File": str(path)

        })

        return frame, logs

    except Exception as error:

        logs.append({

            "Source": "Futures",

            "Report Date": str(day),

            "Status": "LOCAL UNAVAILABLE",

            "Reason": str(error),

            "File": str(path)

        })

    # Use existing NSE collector.
    # No new API token required.

    try:

        frame, url = fetch_archive_futures(

            str(day)

        )

        frame = validate_futures(

            frame,

            day,

            universe

        )

        logs.append({

            "Source": "Futures",

            "Report Date": str(day),

            "Status": "NSE FALLBACK RECEIVED",

            "Contracts": len(frame),

            "Stocks": frame["Symbol"].nunique(),

            "URL": url

        })

        return frame, logs

    except Exception as error:

        logs.append({

            "Source": "Futures",

            "Report Date": str(day),

            "Status": "NSE FALLBACK FAILED",

            "Reason": str(error)

        })

    return pd.DataFrame(), logs


# =====================================
# PREVIOUS SESSION
# =====================================

def previous_session(day):

    dates = [

        value

        for value in available_dates(

            "cash_bhavcopy"

        )

        if value < str(day)

    ]

    if not dates:

        return None

    return max(dates)


# =====================================
# REUSE EXISTING FUTURES ANALYSIS
# =====================================

def futures_intelligence(day, universe):

    diagnostics = []

    previous_day = previous_session(day)

    if previous_day is None:

        return {}, diagnostics, {

            "Status": "PREVIOUS SESSION UNKNOWN",

            "Comparable Stocks": 0

        }

    current, logs = load_futures(

        day,

        universe

    )

    diagnostics.extend(logs)

    previous, logs = load_futures(

        previous_day,

        universe

    )

    diagnostics.extend(logs)

    if current.empty or previous.empty:

        return {}, diagnostics, {

            "Status": "REPORT UNAVAILABLE",

            "Current Date": day,

            "Previous Date": previous_day,

            "Comparable Stocks": 0

        }

    # Existing collector analysis:
    # Total OI across expiries.
    # Same-expiry price comparison.
    # Rollover review.

    analysis = analyse_futures(

        current,

        previous

    )

    result = {}

    for _, row in analysis.iterrows():

        name = row["Symbol"]

        price_change = number(

            row["Price Change %"]

        )

        oi_change = number(

            row["OI Change %"]

        )

        if price_change is None:

            continue

        if oi_change is None:

            continue

        activity = str(

            row["Activity"]

        )

        if activity == "ROLLOVER REVIEW":

            buildup = "ROLLOVER REVIEW"

        elif (

            abs(price_change) < MIN_FUTURES_MOVE

            or abs(oi_change) < MIN_OI_CHANGE

        ):

            buildup = "NEUTRAL"

        else:

            buildup = activity

        result[name] = {

            "Futures OI":

                row["Current Total OI"],

            "Previous Futures OI":

                row["Previous Total OI"],

            "Futures OI Change %":

                oi_change,

            "Futures Price Change %":

                price_change,

            "Futures Volume":

                row["Total Futures Volume"],

            "Futures Expiry":

                row["Compared Expiry"],

            "Futures Buildup":

                buildup,

            "Futures Previous Date":

                previous_day

        }

    coverage = (

        len(result) / len(universe) * 100

        if universe

        else 0

    )

    status = {

        "Status": (

            "AVAILABLE"

            if result

            else "NO COMPARABLE CONTRACTS"

        ),

        "Current Date": day,

        "Previous Date": previous_day,

        "Comparable Stocks": len(result),

        "Universe": len(universe),

        "Coverage %": round(coverage, 2)

    }

    diagnostics.append({

        "Source": "Futures Analysis",

        **status

    })

    return result, diagnostics, status


# =====================================
# DEAL REPORT DISCOVERY
# =====================================

def discover_deal_sources():

    result = {

        "bulk": [],

        "block": [],

        "short": []

    }

    root = DATA / "nse"

    if not root.exists():

        return result

    for path in root.rglob("latest.csv"):

        parts = [

            normalize(part)

            for part in path.relative_to(

                root

            ).parts[:-2]

        ]

        name = "/".join(parts)

        if "bulk" in name:

            result["bulk"].append(path)

        elif "block" in name:

            result["block"].append(path)

        elif (

            "shortselling" in name

            or "short" in parts

        ):

            result["short"].append(path)

    return result


# =====================================
# DEAL REPORT ANALYSIS
# =====================================

def deal_intelligence(

    paths,

    day,

    universe,

    kind

):

    result = {}

    diagnostics = []

    available = sorted({

        path.parent.name

        for path in paths

        if path.parent.name <= str(day)

    }, reverse=True)

    if not available:

        return result, diagnostics, {

            "Status": "REPORT MISSING"

        }

    if kind == "short":

        report_day = available[0]

    else:

        report_day = str(day)

        if report_day not in available:

            return result, diagnostics, {

                "Status": "LATEST REPORT MISSING",

                "Latest Available": available[0]

            }

    matching = [

        path

        for path in paths

        if path.parent.name == report_day

    ]

    if len(matching) != 1:

        return result, diagnostics, {

            "Status": "AMBIGUOUS SOURCE",

            "Report Date": report_day

        }

    path = matching[0]

    try:

        frame, metadata = read_verified(

            path,

            expected_date=report_day

        )

        validate_date(

            frame,

            report_day

        )

        symbol_col = find_column(

            frame,

            [

                "Symbol",

                "TCKRSYMB"

            ]

        )

        quantity_col = find_column(

            frame,

            [

                "Quantity",

                "Quantity Traded",

                "Short Selling Quantity",

                "Short Quantity",

                "Short Sell Quantity"

            ]

        )

        if not symbol_col or not quantity_col:

            raise ValueError(

                "Symbol or quantity column missing"

            )

        frame = frame.copy()

        frame["_symbol"] = (

            frame[symbol_col]

            .astype(str)

            .str.strip()

            .str.upper()

        )

        frame["_qty"] = numeric(

            frame[quantity_col]

        )

        frame = frame[

            frame["_symbol"].isin(universe)

        ].copy()

        if not frame["_qty"].map(finite).all():

            raise ValueError(

                "Invalid deal quantity"

            )

        if frame["_qty"].lt(0).any():

            raise ValueError(

                "Negative deal quantity"

            )

        frame = frame.drop_duplicates()

        if kind == "short":

            for name, group in frame.groupby(

                "_symbol"

            ):

                result[name] = {

                    "Short Selling Quantity":

                        float(group["_qty"].sum()),

                    "Short Selling Date":

                        report_day

                }

        else:

            side_col = find_column(

                frame,

                [

                    "Side",

                    "Buy Sell",

                    "Buy/Sell",

                    "Transaction Type"

                ]

            )

            if side_col is None:

                raise ValueError(

                    "Deal side missing"

                )

            frame["_side"] = (

                frame[side_col]

                .astype(str)

                .str.strip()

                .str.upper()

                .replace({

                    "B": "BUY",

                    "S": "SELL"

                })

            )

            if not frame["_side"].isin(

                ["BUY", "SELL"]

            ).all():

                raise ValueError(

                    "Unknown deal side"

                )

            prefix = kind.title()

            for name, group in frame.groupby(

                "_symbol"

            ):

                buy = group.loc[

                    group["_side"].eq("BUY"),

                    "_qty"

                ].sum()

                sell = group.loc[

                    group["_side"].eq("SELL"),

                    "_qty"

                ].sum()

                result[name] = {

                    prefix + " Buy Quantity":

                        float(buy),

                    prefix + " Sell Quantity":

                        float(sell),

                    prefix + " Records":

                        len(group),

                    prefix + " Report Date":

                        report_day

                }

        status = {

            "Status": (

                "RECEIVED"

                if report_day == str(day)

                else "OLDER REPORT - CONTEXT ONLY"

            ),

            "Report Date": report_day,

            "Records": len(frame),

            "Matched Stocks": len(result)

        }

        diagnostics.append({

            "Source": kind,

            "File": str(path),

            **status

        })

        return result, diagnostics, status

    except Exception as error:

        diagnostics.append({

            "Source": kind,

            "Status": "REJECTED",

            "File": str(path),

            "Reason": str(error)

        })

        return result, diagnostics, {

            "Status": "REJECTED",

            "Reason": str(error)

        }


# =====================================
# SOURCE STATUS PER STOCK
# =====================================

def stock_source_status(status, matched):

    if matched:

        return "RECORD FOUND"

    if status.get("Status") == "RECEIVED":

        return "NO MATCHED RECORD"

    if status.get("Status") == (

        "OLDER REPORT - CONTEXT ONLY"

    ):

        return "OLDER REPORT - NO MATCH"

    return "UNAVAILABLE"


# =====================================
# FINAL STOCK IDENTIFICATION
# =====================================

def build_advanced_watchlist():

    base, summary, news, announcements = (

        build_watchlist()

    )

    day = summary["report_date"]

    universe = set(

        base["Symbol"]

    )

    futures, diagnostics, futures_status = (

        futures_intelligence(

            day,

            universe

        )

    )

    sources = discover_deal_sources()

    deals = {}

    statuses = {

        "Futures": futures_status

    }

    for kind in [

        "bulk",

        "block",

        "short"

    ]:

        data, logs, status = (

            deal_intelligence(

                sources[kind],

                day,

                universe,

                kind

            )

        )

        deals[kind] = data

        statuses[kind] = status

        diagnostics.extend(logs)

    rows = []

    for _, row in base.iterrows():

        item = row.to_dict()

        name = item["Symbol"]

        item.update({

            "Futures OI": None,

            "Previous Futures OI": None,

            "Futures OI Change %": None,

            "Futures Price Change %": None,

            "Futures Volume": None,

            "Futures Expiry": None,

            "Futures Buildup": "UNAVAILABLE",

            "Futures Previous Date": None,

            "Bulk Buy Quantity": None,

            "Bulk Sell Quantity": None,

            "Block Buy Quantity": None,

            "Block Sell Quantity": None,

            "Short Selling Quantity": None,

            "Short Selling Date": None

        })

        item.update(

            futures.get(name, {})

        )

        for kind in deals:

            item.update(

                deals[kind].get(name, {})

            )

        item["Bulk Status"] = (

            stock_source_status(

                statuses["bulk"],

                name in deals["bulk"]

            )

        )

        item["Block Status"] = (

            stock_source_status(

                statuses["block"],

                name in deals["block"]

            )

        )

        item["Short Selling"] = (

            stock_source_status(

                statuses["short"],

                name in deals["short"]

            )

        )

        move = number(

            item.get("Change %")

        )

        strength = number(

            item.get(

                "Relative Strength vs Universe %"

            )

        )

        volume = number(

            item.get("Volume Ratio")

        )

        turnover = number(

            item.get("Turnover Ratio")

        )

        delivery = number(

            item.get(

                "Delivery Quantity Ratio"

            )

        )

        participation = (

            meets(volume, MIN_VOLUME_RATIO)

            and meets(turnover, MIN_TURNOVER_RATIO)

        )

        delivery_activity = meets(

            delivery,

            MIN_DELIVERY_RATIO

        )

        catalyst = bool(

            item.get("News Records", 0)

            or item.get("Announcement Records", 0)

            or item.get("Upcoming Event", "")

        )

        deal_activity = (

            name in deals["bulk"]

            or name in deals["block"]

        )

        buildup = item["Futures Buildup"]

        bullish_reaction = (

            move is not None

            and strength is not None

            and move >= MIN_PRICE_MOVE

            and strength >= MIN_RELATIVE_STRENGTH

        )

        bearish_reaction = (

            move is not None

            and strength is not None

            and move <= -MIN_PRICE_MOVE

            and strength <= -MIN_RELATIVE_STRENGTH

        )

        extended = (

            move is not None

            and abs(move) >= MAX_EARLY_MOVE

        )

        reasons = []

        if participation:

            reasons.append(

                "Abnormal volume and turnover"

            )

        if delivery_activity:

            reasons.append(

                "Abnormal delivery quantity"

            )

        if bullish_reaction:

            reasons.append(

                "Positive relative strength"

            )

        if bearish_reaction:

            reasons.append(

                "Negative relative strength"

            )

        if buildup not in [

            "UNAVAILABLE",

            "NEUTRAL"

        ]:

            reasons.append(buildup)

        if catalyst:

            reasons.append(

                "News / event review required"

            )

        if deal_activity:

            reasons.append(

                "Disclosed deal activity"

            )

        if name in deals["short"]:

            reasons.append(

                "Reported short selling - "

                + str(

                    item["Short Selling Date"]

                )

            )

        category = "NO PRIORITY"

        bias = "UNCONFIRMED"

        confirmation = "PENDING"

        # Do not force a directional signal
        # when price and futures disagree.

        conflict = (

            bullish_reaction

            and buildup in [

                "SHORT BUILDUP",

                "LONG UNWINDING"

            ]

        ) or (

            bearish_reaction

            and buildup in [

                "LONG BUILDUP",

                "SHORT COVERING"

            ]

        )

        if extended and (

            participation

            or catalyst

            or deal_activity

            or buildup not in [

                "UNAVAILABLE",

                "NEUTRAL"

            ]

        ):

            category = "EXTENDED MOVE - REVIEW"

            reasons.append(

                "Large closing move - avoid chasing"

            )

        elif conflict:

            category = "CONFLICT - WAIT"

            reasons.append(

                "Cash and futures evidence conflict"

            )

        elif (

            participation

            and bullish_reaction

            and buildup == "LONG BUILDUP"

        ):

            category = "PRIORITY BULLISH"

            bias = "UPWARD BIAS"

            confirmation = "FUTURES ALIGNED"

        elif (

            participation

            and bearish_reaction

            and buildup == "SHORT BUILDUP"

        ):

            category = "PRIORITY BEARISH"

            bias = "DOWNWARD BIAS"

            confirmation = "FUTURES ALIGNED"

        elif participation and bullish_reaction:

            category = "PRELIMINARY BULLISH"

            bias = "UPWARD BIAS"

            confirmation = "FUTURES NOT CONFIRMED"

        elif participation and bearish_reaction:

            category = "PRELIMINARY BEARISH"

            bias = "DOWNWARD BIAS"

            confirmation = "FUTURES NOT CONFIRMED"

        elif participation:

            category = "EMERGING ACTIVITY"

        elif buildup in [

            "LONG BUILDUP",

            "SHORT BUILDUP",

            "SHORT COVERING",

            "LONG UNWINDING"

        ]:

            category = "FUTURES WATCH"

        elif catalyst:

            category = "EVENT WATCH"

        elif deal_activity:

            category = "DEAL REVIEW"

        elif delivery_activity:

            category = "DELIVERY WATCH"

        elif buildup == "ROLLOVER REVIEW":

            category = "ROLLOVER REVIEW"

        # Count distinct evidence groups.
        # This is not a probability score.

        evidence_groups = sum([

            bool(participation),

            bool(delivery_activity),

            bool(

                bullish_reaction

                or bearish_reaction

            ),

            bool(

                confirmation == "FUTURES ALIGNED"

            )

        ])

        item.update({

            "Category": category,

            "Directional Bias": bias,

            "Confirmation": confirmation,

            "Evidence Groups": evidence_groups,

            "Evidence": " | ".join(reasons),

            "Live Confirmation Required": True,

            "Option Chain": "NOT INTEGRATED",

            "Greeks": "NOT INTEGRATED"

        })

        rows.append(item)

    frame = pd.DataFrame(rows)

    ranks = {

        "PRIORITY BULLISH": 1,

        "PRIORITY BEARISH": 1,

        "PRELIMINARY BULLISH": 2,

        "PRELIMINARY BEARISH": 2,

        "EMERGING ACTIVITY": 3,

        "FUTURES WATCH": 4,

        "EVENT WATCH": 5,

        "DEAL REVIEW": 6,

        "DELIVERY WATCH": 7,

        "CONFLICT - WAIT": 8,

        "ROLLOVER REVIEW": 9,

        "EXTENDED MOVE - REVIEW": 10,

        "NO PRIORITY": 11

    }

    frame["_rank"] = (

        frame["Category"].map(ranks)

    )

    frame = frame.sort_values(

        [

            "_rank",

            "Evidence Groups",

            "Volume Ratio",

            "Symbol"

        ],

        ascending=[

            True,

            False,

            False,

            True

        ],

        na_position="last"

    ).drop(columns=["_rank"])

    summary.update({

        "version": VERSION,

        "status": "RESEARCH WATCHLIST",

        "derivatives_status": statuses,

        "futures_comparable_stocks":

            len(futures),

        "futures_coverage_percent":

            round(

                len(futures)

                / len(universe)

                * 100,

                2

            ) if universe else 0,

        "category_counts":

            frame["Category"]

            .value_counts()

            .to_dict(),

        "option_chain_status":

            "NOT INTEGRATED",

        "greeks_status":

            "NOT INTEGRATED"

    })

    return frame, summary, diagnostics


# =====================================
# STREAMLIT DASHBOARD
# =====================================

def render_derivatives():

    st.header(

        "POST MARKET INTELLIGENCE V"

        + VERSION

    )

    st.caption(

        "Full F&O Universe | "

        "Historical Research | "

        "No Automatic Orders"

    )

    st.warning(

        "Post-market watchlist is not "

        "a confirmed next-session trade signal."

    )

    if st.button(

        "GENERATE ADVANCED WATCHLIST",

        type="primary",

        key="advanced_watchlist_v192"

    ):

        try:

            with st.spinner(

                "Checking historical reports "

                "and analyzing F&O stocks..."

            ):

                st.session_state["v192_result"] = (

                    build_advanced_watchlist()

                )

        except Exception as error:

            st.session_state.pop(

                "v192_result",

                None

            )

            st.error(str(error))

    result = st.session_state.get(

        "v192_result"

    )

    if result is None:

        st.info(

            "Generate watchlist to begin."

        )

        return

    frame, summary, diagnostics = result

    st.caption(

        "Report Date: "

        + summary["report_date"]

        + " | Generated: "

        + summary["generated_at"]

    )

    if summary["report_date"] != (

        now().date().isoformat()

    ):

        st.warning(

            "Using earlier trading session data."

        )

    categories = frame["Category"]

    a, b, c = st.columns(3)

    a.metric(

        "F&O Universe",

        len(frame)

    )

    b.metric(

        "Futures Comparable",

        summary["futures_comparable_stocks"]

    )

    c.metric(

        "Futures Coverage",

        str(

            summary["futures_coverage_percent"]

        ) + "%"

    )

    a, b = st.columns(2)

    a.metric(

        "Priority Bullish",

        int(

            categories.eq(

                "PRIORITY BULLISH"

            ).sum()

        )

    )

    b.metric(

        "Priority Bearish",

        int(

            categories.eq(

                "PRIORITY BEARISH"

            ).sum()

        )

    )

    a, b = st.columns(2)

    a.metric(

        "Preliminary Bullish",

        int(

            categories.eq(

                "PRELIMINARY BULLISH"

            ).sum()

        )

    )

    b.metric(

        "Preliminary Bearish",

        int(

            categories.eq(

                "PRELIMINARY BEARISH"

            ).sum()

        )

    )

    st.metric(

        "Emerging Activity",

        int(

            categories.eq(

                "EMERGING ACTIVITY"

            ).sum()

        )

    )

    if summary["futures_comparable_stocks"] == 0:

        st.warning(

            "Futures unavailable. "

            "Preliminary cash-market watchlists "

            "remain available where qualified."

        )

    elif summary["futures_coverage_percent"] < 90:

        st.warning(

            "Partial futures coverage. "

            "Review source diagnostics."

        )

    with st.expander(

        "DATA SOURCE STATUS",

        expanded=True

    ):

        for source, status in (

            summary["derivatives_status"].items()

        ):

            st.write(

                source.upper()

                + ": "

                + status.get(

                    "Status",

                    "UNKNOWN"

                )

            )

            st.json(status)

    tabs = st.tabs([

        "Bullish",

        "Bearish",

        "Emerging",

        "Events",

        "Deals",

        "Futures",

        "Extended / Conflict",

        "All Stocks",

        "Diagnostics"

    ])

    columns = [

        "Symbol",

        "Category",

        "Directional Bias",

        "Confirmation",

        "Evidence Groups",

        "Close",

        "Change %",

        "Relative Strength vs Universe %",

        "Volume Ratio",

        "Turnover Ratio",

        "Delivery Quantity Ratio",

        "Futures OI Change %",

        "Futures Price Change %",

        "Futures Buildup",

        "Futures Expiry",

        "Bulk Buy Quantity",

        "Bulk Sell Quantity",

        "Block Buy Quantity",

        "Block Sell Quantity",

        "Short Selling Quantity",

        "Short Selling Date",

        "Evidence",

        "Latest News",

        "Latest Announcement"

    ]

    groups = [

        [

            "PRIORITY BULLISH",

            "PRELIMINARY BULLISH"

        ],

        [

            "PRIORITY BEARISH",

            "PRELIMINARY BEARISH"

        ],

        [

            "EMERGING ACTIVITY",

            "DELIVERY WATCH"

        ],

        [

            "EVENT WATCH"

        ],

        [

            "DEAL REVIEW"

        ],

        [

            "FUTURES WATCH",

            "ROLLOVER REVIEW"

        ],

        [

            "EXTENDED MOVE - REVIEW",

            "CONFLICT - WAIT"

        ]

    ]

    for index, group in enumerate(groups):

        with tabs[index]:

            selected = frame[

                frame["Category"].isin(group)

            ]

            if selected.empty:

                st.info(

                    "No qualifying stocks."

                )

            else:

                st.dataframe(

                    selected[columns],

                    hide_index=True,

                    use_container_width=True

                )

    with tabs[4]:

        st.subheader(

            "All Stocks With Disclosed Deals"

        )

        deal_rows = frame[

            frame["Bulk Status"].eq(

                "RECORD FOUND"

            )

            |

            frame["Block Status"].eq(

                "RECORD FOUND"

            )

        ]

        st.dataframe(

            deal_rows[columns],

            hide_index=True,

            use_container_width=True

        )

    with tabs[7]:

        st.dataframe(

            frame,

            hide_index=True,

            use_container_width=True

        )

    with tabs[8]:

        st.json(summary)

        st.dataframe(

            pd.DataFrame(diagnostics),

            use_container_width=True,

            hide_index=True

        )

        st.caption(

            "Older short-selling reports "

            "are context only. "

            "Missing data is not zero activity."

        )

    watchlist = frame[

        frame["Category"].ne(

            "NO PRIORITY"

        )

    ]

    st.download_button(

        "DOWNLOAD ADVANCED WATCHLIST",

        watchlist.to_csv(index=False),

        "post_market_watchlist_v192.csv",

        mime="text/csv"

    )

    st.download_button(

        "DOWNLOAD FULL UNIVERSE",

        frame.to_csv(index=False),

        "full_universe_v192.csv",

        mime="text/csv"

    )

    st.download_button(

        "DOWNLOAD SOURCE DIAGNOSTICS",

        pd.DataFrame(

            diagnostics

        ).to_csv(index=False),

        "source_diagnostics_v192.csv",

        mime="text/csv"

    )
