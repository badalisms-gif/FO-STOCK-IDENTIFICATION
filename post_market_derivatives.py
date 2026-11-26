
"""
F&O POST MARKET DERIVATIVES
Version 1.9.1

Futures OI + Bulk Deals + Block Deals
+ Short Selling Intelligence

Research Only
"""

import re
import json
import math
import hashlib
import pandas as pd
import streamlit as st

from daily_collector import DATA

from post_market_engine import (
    build_watchlist,
    number,
    now
)

VERSION = "1.9.1"

MIN_OI_CHANGE = 3.0
MIN_FUTURES_MOVE = 0.50
MAX_EARLY_MOVE = 3.0

MIN_VOLUME_RATIO = 1.5
MIN_TURNOVER_RATIO = 1.5


# =====================================
# SCHEMA UTILITIES
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


def dates(series):

    return pd.to_datetime(

        series.astype(str),

        format="mixed",

        dayfirst=True,

        errors="coerce"

    ).dt.strftime("%Y-%m-%d")


# =====================================
# HISTORICAL SOURCE DISCOVERY
# =====================================

def discover_sources():

    result = {

        "futures": [],

        "bulk": [],

        "block": [],

        "short": []

    }

    for path in DATA.rglob("latest.csv"):

        parts = [

            normalize(part)

            for part in path.relative_to(DATA).parts[:-2]

        ]

        name = "/".join(parts)

        if "revision" in name:

            continue

        if "watchlist" in name:

            continue

        if any(

            word in name

            for word in [

                "participant",

                "institutional",

                "companynews",

                "optionchain"

            ]

        ):

            continue

        if "bulk" in name:

            result["bulk"].append(path)

        elif "block" in name:

            result["block"].append(path)

        elif "shortselling" in name or "short" in parts:

            result["short"].append(path)

        elif any(

            word in name

            for word in [

                "futures",

                "fobhavcopy",

                "derivativesbhavcopy"

            ]

        ):

            result["futures"].append(path)

    return result


# =====================================
# SNAPSHOT VALIDATION
# =====================================

def read_verified(path):

    meta_path = path.parent / "metadata.json"

    if not meta_path.exists():

        raise ValueError("Metadata missing")

    meta = json.loads(
        meta_path.read_text()
    )

    content = path.read_bytes()

    digest = hashlib.sha256(
        content
    ).hexdigest()

    if digest != meta.get("sha256"):

        raise ValueError("Checksum mismatch")

    frame = pd.read_csv(path)

    if len(frame) != meta.get("records"):

        raise ValueError("Record count mismatch")

    if frame.columns.duplicated().any():

        raise ValueError("Duplicate columns")

    eligible = find_column(

        frame,

        ["Eligible For Analysis"]

    )

    if eligible:

        frame = frame[

            frame[eligible]
            .astype(str)
            .str.lower()
            .str.strip()
            .eq("true")

        ].copy()

    return frame, meta


def dated_records(frame, day):

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

        raise ValueError("Report date column missing")

    if not dates(frame[date_col]).eq(day).all():

        raise ValueError("Report date mismatch")

    return frame


# =====================================
# FUTURES DATA NORMALIZATION
# =====================================

def normalize_futures(frame, day, universe):

    frame = dated_records(frame, day)

    aliases = {

        "Symbol": [

            "Symbol",

            "Ticker Symbol",

            "TCKRSYMB"

        ],

        "Expiry": [

            "Expiry",

            "Expiry Date",

            "ExpiryDate",

            "XPRYDT",

            "FinInstrmActlXpryDt"

        ],

        "Close": [

            "Close",

            "Close Price",

            "Closing Price",

            "Futures Close",

            "CLSPRIC"

        ],

        "OI": [

            "Open Interest",

            "OI",

            "OPNINTRST"

        ]

    }

    columns = {

        name: find_column(frame, options)

        for name, options in aliases.items()

    }

    missing = [

        name

        for name, column in columns.items()

        if column is None

    ]

    if missing:

        raise ValueError(

            "Missing futures columns: "

            + ", ".join(missing)

        )

    instrument = find_column(

        frame,

        [

            "Instrument",

            "Instrument Type",

            "InstrumentType",

            "FinInstrmTp"

        ]

    )

    if instrument:

        values = frame[instrument].map(normalize)

        frame = frame[

            values.isin([

                "futstk",

                "stf",

                "fut",

                "stockfuture",

                "stockfutures"

            ])

        ].copy()

    else:

        option_side = find_column(

            frame,

            [

                "Option Type",

                "Option Side",

                "OptnTp"

            ]

        )

        if option_side:

            frame = frame[

                ~frame[option_side]
                .astype(str)
                .str.upper()
                .isin(["CE", "PE"])

            ].copy()

        else:

            raise ValueError(

                "Futures instrument type unverified"

            )

    output = pd.DataFrame({

        "Symbol":

            frame[columns["Symbol"]]
            .astype(str)
            .str.strip()
            .str.upper(),

        "Expiry":

            dates(frame[columns["Expiry"]]),

        "Close":

            numeric(frame[columns["Close"]]),

        "OI":

            numeric(frame[columns["OI"]])

    })

    output = output[

        output["Symbol"].isin(universe)

        &

        output["Expiry"].notna()

        &

        output["Expiry"].ge(day)

        &

        output["Close"].map(

            lambda x: number(x) is not None

        )

        &

        output["OI"].map(

            lambda x: number(x) is not None

        )

        &

        output["Close"].gt(0)

        &

        output["OI"].ge(0)

    ].copy()

    if output.duplicated(

        ["Symbol", "Expiry"]

    ).any():

        raise ValueError(

            "Duplicate futures contracts"

        )

    return output


# =====================================
# FUTURES OI INTELLIGENCE
# =====================================

def futures_intelligence(paths, day, universe):

    diagnostics = []

    reports = {}

    for path in paths:

        report_date = path.parent.name

        if report_date > day:

            continue

        if report_date in reports:

            continue

        try:

            frame, meta = read_verified(path)

            output = normalize_futures(

                frame,

                report_date,

                universe

            )

            if output.empty:

                raise ValueError(

                    "No valid futures contracts"

                )

            reports[report_date] = output

        except Exception as error:

            diagnostics.append({

                "Source": "Futures",

                "File": str(path),

                "Status": "REJECTED",

                "Reason": str(error)

            })

    if day not in reports:

        return {}, diagnostics, "LATEST REPORT MISSING"

    previous_dates = sorted(

        [

            value

            for value in reports

            if value < day

        ],

        reverse=True

    )

    if not previous_dates:

        return {}, diagnostics, "PREVIOUS REPORT MISSING"

    previous_day = previous_dates[0]

    current = reports[day]

    previous = reports[previous_day]

    # Compare the same expiry contracts.
    # Do not compare two different expiries.

    nearest = current.sort_values(

        ["Symbol", "Expiry"]

    ).drop_duplicates("Symbol")

    merged = nearest.merge(

        previous,

        on=["Symbol", "Expiry"],

        how="inner",

        suffixes=("", "_Previous")

    )

    result = {}

    for _, row in merged.iterrows():

        previous_oi = number(

            row["OI_Previous"]

        )

        previous_close = number(

            row["Close_Previous"]

        )

        if previous_oi is None or previous_oi <= 0:

            continue

        if previous_close is None or previous_close <= 0:

            continue

        oi_change = (

            row["OI"] / previous_oi - 1

        ) * 100

        price_change = (

            row["Close"] / previous_close - 1

        ) * 100

        buildup = "NEUTRAL"

        if (

            price_change >= MIN_FUTURES_MOVE

            and oi_change >= MIN_OI_CHANGE

        ):

            buildup = "LONG BUILDUP"

        elif (

            price_change <= -MIN_FUTURES_MOVE

            and oi_change >= MIN_OI_CHANGE

        ):

            buildup = "SHORT BUILDUP"

        elif (

            price_change >= MIN_FUTURES_MOVE

            and oi_change <= -MIN_OI_CHANGE

        ):

            buildup = "SHORT COVERING"

        elif (

            price_change <= -MIN_FUTURES_MOVE

            and oi_change <= -MIN_OI_CHANGE

        ):

            buildup = "LONG UNWINDING"

        result[row["Symbol"]] = {

            "Futures Expiry": row["Expiry"],

            "Futures OI": row["OI"],

            "Futures OI Change %": oi_change,

            "Futures Price Change %": price_change,

            "Futures Buildup": buildup,

            "Futures Previous Date": previous_day

        }

    diagnostics.append({

        "Source": "Futures",

        "Status": "RECEIVED",

        "Report Date": day,

        "Previous Date": previous_day,

        "Comparable Stocks": len(result),

        "Universe": len(universe),

        "Coverage %":

            round(len(result) / len(universe) * 100, 2)

    })

    return result, diagnostics, "AVAILABLE"


# =====================================
# BULK / BLOCK / SHORT SELLING
# =====================================

def deal_intelligence(paths, day, universe, kind):

    diagnostics = []

    result = {}

    matching = [

        path

        for path in paths

        if path.parent.name == day

    ]

    if not matching:

        return result, diagnostics, "REPORT MISSING"

    if len(matching) > 1:

        return result, diagnostics, "MULTIPLE SOURCES - REVIEW"

    path = matching[0]

    try:

        frame, meta = read_verified(path)

        frame = dated_records(frame, day)

        symbol_col = find_column(

            frame,

            ["Symbol", "TCKRSYMB"]

        )

        qty_col = find_column(

            frame,

            [

                "Quantity",

                "Quantity Traded",

                "Short Selling Quantity",

                "Short Quantity"

            ]

        )

        if not symbol_col or not qty_col:

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

            frame[qty_col]

        )

        frame = frame[

            frame["_symbol"].isin(universe)

        ].copy()

        if not frame["_qty"].map(

            lambda x: number(x) is not None and x >= 0

        ).all():

            raise ValueError(

                "Invalid deal quantities"

            )

        frame = frame.drop_duplicates()

        if kind == "short":

            for name, group in frame.groupby("_symbol"):

                result[name] = {

                    "Short Selling Quantity":

                        float(group["_qty"].sum())

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

            for name, group in frame.groupby("_symbol"):

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

                        len(group)

                }

        diagnostics.append({

            "Source": kind,

            "Status": "RECEIVED",

            "Report Date": day,

            "Records": len(frame),

            "Matched Stocks": len(result),

            "File": str(path)

        })

        return result, diagnostics, "RECEIVED"

    except Exception as error:

        diagnostics.append({

            "Source": kind,

            "Status": "REJECTED",

            "File": str(path),

            "Reason": str(error)

        })

        return {}, diagnostics, "REJECTED"


# =====================================
# FINAL STOCK IDENTIFICATION
# =====================================

def build_derivatives_watchlist():

    base, summary, news, announcements = (

        build_watchlist()

    )

    day = summary["report_date"]

    universe = set(base["Symbol"])

    sources = discover_sources()

    futures, diagnostics, futures_status = (

        futures_intelligence(

            sources["futures"],

            day,

            universe

        )

    )

    deals = {}

    statuses = {

        "Futures": futures_status

    }

    for kind in ["bulk", "block", "short"]:

        data, logs, status = deal_intelligence(

            sources[kind],

            day,

            universe,

            kind

        )

        deals[kind] = data

        statuses[kind] = status

        diagnostics.extend(logs)

    rows = []

    for _, row in base.iterrows():

        item = row.to_dict()

        name = item["Symbol"]

        future = futures.get(name, {})

        item.update({

            "Futures OI": None,

            "Futures OI Change %": None,

            "Futures Price Change %": None,

            "Futures Expiry": None,

            "Futures Buildup": "UNAVAILABLE",

            "Futures Previous Date": None,

            "Bulk Buy Quantity": None,

            "Bulk Sell Quantity": None,

            "Block Buy Quantity": None,

            "Block Sell Quantity": None,

            "Short Selling Quantity": None

        })

        item.update(future)

        for kind in deals:

            item.update(

                deals[kind].get(name, {})

            )

        item["Bulk / Block Deals"] = (

            "RECORD FOUND"

            if name in deals["bulk"]

            or name in deals["block"]

            else "NO MATCHED RECORD"

        )

        item["Short Selling"] = (

            "RECORD FOUND"

            if name in deals["short"]

            else "NO MATCHED RECORD"

        )

        move = number(item.get("Change %"))

        volume = number(item.get("Volume Ratio"))

        turnover = number(item.get("Turnover Ratio"))

        strength = number(

            item.get("Relative Strength vs Universe %")

        )

        participation = (

            volume is not None

            and turnover is not None

            and volume >= MIN_VOLUME_RATIO

            and turnover >= MIN_TURNOVER_RATIO

        )

        catalyst = bool(

            item.get("News Records", 0)

            or item.get("Announcement Records", 0)

            or item.get("Upcoming Event", "")

        )

        buildup = item["Futures Buildup"]

        deal_activity = (

            name in deals["bulk"]

            or name in deals["block"]

        )

        reasons = []

        if participation:

            reasons.append(

                "Abnormal volume and turnover"

            )

        if buildup not in ["UNAVAILABLE", "NEUTRAL"]:

            reasons.append(buildup)

        if catalyst:

            reasons.append("Catalyst review required")

        if deal_activity:

            reasons.append("Disclosed deal activity")

        if name in deals["short"]:

            reasons.append("Short selling reported")

        category = "NO PRIORITY"

        bias = "UNCONFIRMED"

        if move is not None and abs(move) >= MAX_EARLY_MOVE:

            if participation or catalyst:

                category = "EXTENDED MOVE - REVIEW"

        elif (

            participation

            and buildup == "LONG BUILDUP"

            and move is not None

            and strength is not None

            and move > 0

            and strength > 0

        ):

            category = "PRIORITY BULLISH"

            bias = "UPWARD BIAS"

        elif (

            participation

            and buildup == "SHORT BUILDUP"

            and move is not None

            and strength is not None

            and move < 0

            and strength < 0

        ):

            category = "PRIORITY BEARISH"

            bias = "DOWNWARD BIAS"

        elif participation:

            category = "EMERGING ACTIVITY"

        elif catalyst:

            category = "EVENT WATCH"

        elif deal_activity:

            category = "DEAL REVIEW"

        elif buildup not in ["NEUTRAL", "UNAVAILABLE"]:

            category = "FUTURES WATCH"

        item["V1.9.1 Category"] = category

        item["V1.9.1 Bias"] = bias

        item["V1.9.1 Evidence"] = " | ".join(reasons)

        item["Live Confirmation Required"] = True

        rows.append(item)

    result = pd.DataFrame(rows)

    ranks = {

        "PRIORITY BULLISH": 1,

        "PRIORITY BEARISH": 1,

        "EMERGING ACTIVITY": 2,

        "EVENT WATCH": 3,

        "DEAL REVIEW": 4,

        "FUTURES WATCH": 5,

        "EXTENDED MOVE - REVIEW": 6,

        "NO PRIORITY": 7

    }

    result["_rank"] = (

        result["V1.9.1 Category"].map(ranks)

    )

    result = result.sort_values(

        ["_rank", "Volume Ratio"],

        ascending=[True, False],

        na_position="last"

    ).drop(columns=["_rank"])

    summary.update({

        "version": VERSION,

        "derivatives_status": statuses,

        "futures_comparable_stocks": len(futures),

        "bulk_matched_stocks": len(deals["bulk"]),

        "block_matched_stocks": len(deals["block"]),

        "short_matched_stocks": len(deals["short"]),

        "option_chain_status": "NOT INTEGRATED",

        "greeks_status": "NOT INTEGRATED",

        "status": "PRELIMINARY RESEARCH"

    })

    return result, summary, diagnostics


# =====================================
# STREAMLIT DASHBOARD
# =====================================

def render_derivatives():

    st.header(

        "POST MARKET INTELLIGENCE V" + VERSION

    )

    st.caption(

        "Futures OI | Deals | Short Selling | "

        "Full Universe Research"

    )

    st.warning(

        "Watchlist only. Futures positioning and "

        "disclosed deals do not guarantee direction."

    )

    if st.button(

        "GENERATE ADVANCED WATCHLIST",

        type="primary",

        key="advanced_watchlist_v191"

    ):

        try:

            with st.spinner(

                "Analyzing historical market data..."

            ):

                st.session_state["v191_result"] = (

                    build_derivatives_watchlist()

                )

        except Exception as error:

            st.session_state.pop(

                "v191_result",

                None

            )

            st.error(str(error))

    result = st.session_state.get("v191_result")

    if result is None:

        return

    frame, summary, diagnostics = result

    st.caption(

        "Report Date: " + summary["report_date"]

        + " | Generated: " + summary["generated_at"]

    )

    if summary["report_date"] != now().date().isoformat():

        st.warning("Earlier session report in use.")

    categories = [

        "PRIORITY BULLISH",

        "PRIORITY BEARISH",

        "EMERGING ACTIVITY",

        "EVENT WATCH",

        "DEAL REVIEW",

        "FUTURES WATCH",

        "EXTENDED MOVE - REVIEW"

    ]

    a, b, c, d = st.columns(4)

    a.metric(

        "Universe",

        len(frame)

    )

    b.metric(

        "Priority Bullish",

        int(

            frame["V1.9.1 Category"]

            .eq("PRIORITY BULLISH").sum()

        )

    )

    c.metric(

        "Priority Bearish",

        int(

            frame["V1.9.1 Category"]

            .eq("PRIORITY BEARISH").sum()

        )

    )

    d.metric(

        "Futures Comparable",

        summary["futures_comparable_stocks"]

    )

    st.subheader("Source Status")

    st.json(summary["derivatives_status"])

    tabs = st.tabs([

        "Bullish",

        "Bearish",

        "Emerging",

        "Events",

        "Deals",

        "Futures",

        "Extended",

        "All Stocks",

        "Diagnostics"

    ])

    columns = [

        "Symbol",

        "V1.9.1 Category",

        "V1.9.1 Bias",

        "Close",

        "Change %",

        "Volume Ratio",

        "Turnover Ratio",

        "Delivery Quantity Ratio",

        "Futures OI Change %",

        "Futures Price Change %",

        "Futures Buildup",

        "Bulk Buy Quantity",

        "Bulk Sell Quantity",

        "Block Buy Quantity",

        "Block Sell Quantity",

        "Short Selling Quantity",

        "V1.9.1 Evidence",

        "Latest News",

        "Latest Announcement"

    ]

    for index, category in enumerate(categories):

        with tabs[index]:

            selected = frame[

                frame["V1.9.1 Category"].eq(category)

            ]

            if selected.empty:

                st.info("No qualifying stocks.")

            else:

                st.dataframe(

                    selected[columns],

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

            use_container_width=True

        )

        st.caption(

            "Missing reports are not zero activity. "

            "Option Chain and Greeks are pending."

        )

    watchlist = frame[

        frame["V1.9.1 Category"].ne("NO PRIORITY")

    ]

    st.download_button(

        "DOWNLOAD ADVANCED WATCHLIST",

        watchlist.to_csv(index=False),

        "post_market_watchlist_v191.csv",

        mime="text/csv"

    )

