
import gzip
import json
import math
import requests
import pandas as pd
import streamlit as st

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from concurrent.futures import ThreadPoolExecutor

from deal_data_engine import (
    fetch_report,
    parse_report,
    match_reports
)


VERSION = "1.2"

IST = ZoneInfo("Asia/Kolkata")

MASTER_URL = (
    "https://assets.upstox.com/"
    "market-quote/instruments/"
    "exchange/NSE.json.gz"
)

SOURCES = ["bulk", "block", "short"]

LABELS = {
    "bulk": "Bulk Deals",
    "block": "Block Deals",
    "short": "Short Selling"
}


st.set_page_config(
    page_title="F&O Stock Identification V1.2",
    layout="wide"
)


def now():

    return datetime.now(IST)


# =====================================
# AUTOMATIC F&O UNIVERSE
# =====================================

@st.cache_data(ttl=21600, show_spinner=False)
def get_fo_universe():

    response = requests.get(
        MASTER_URL,
        timeout=(10, 40)
    )

    response.raise_for_status()

    raw = response.content

    if raw[:2] == b"\x1f\x8b":

        raw = gzip.decompress(raw)

    instruments = json.loads(raw)

    if not isinstance(instruments, list):

        raise ValueError(
            "Invalid instrument master"
        )

    today = now().date()

    equity = {}

    for item in instruments:

        if (
            item.get("segment") == "NSE_EQ"
            and item.get("instrument_key")
            and item.get("trading_symbol")
        ):

            equity[
                item["instrument_key"]
            ] = str(
                item["trading_symbol"]
            ).strip().upper()

    option_keys = {}

    for item in instruments:

        if item.get("segment") != "NSE_FO":

            continue

        side = item.get("instrument_type")

        if side not in ("CE", "PE"):

            continue

        key = item.get("underlying_key")

        if key not in equity:

            continue

        expiry = item.get("expiry")

        try:

            expiry = float(expiry)

            if expiry > 100000000000:

                expiry /= 1000

            expiry_date = (
                datetime.fromtimestamp(
                    expiry,
                    IST
                ).date()
            )

            if expiry_date < today:

                continue

        except (
            ValueError,
            TypeError,
            OverflowError,
            OSError
        ):

            continue

        option_keys.setdefault(
            key, set()
        ).add(side)

    rows = []

    for key, sides in option_keys.items():

        if not {"CE", "PE"} <= sides:

            continue

        rows.append({
            "Symbol": equity[key],
            "Instrument Key": key
        })

    universe = pd.DataFrame(rows)

    if universe.empty:

        raise ValueError(
            "No valid F&O stocks found"
        )

    universe = (
        universe
        .drop_duplicates("Symbol")
        .sort_values("Symbol")
        .reset_index(drop=True)
    )

    if not 100 <= len(universe) <= 500:

        raise ValueError(
            f"Unexpected universe size: "
            f"{len(universe)}"
        )

    return universe, now().isoformat()


# =====================================
# DATE AND REPORT COLLECTION
# =====================================

def collect_one(kind, day):

    try:

        data = fetch_report(kind, day)

        return {
            "kind": kind,
            "data": data,
            "error": "",
            "origin": "NSE automatic",
            "checked": now().isoformat()
        }

    except Exception as e:

        return {
            "kind": kind,
            "data": None,
            "error": str(e),
            "origin": "NSE automatic",
            "checked": now().isoformat()
        }


def collect_all(day):

    with ThreadPoolExecutor(
        max_workers=3
    ) as pool:

        results = list(
            pool.map(
                lambda k: collect_one(k, day),
                SOURCES
            )
        )

    return {
        r["kind"]: r
        for r in results
    }


def report_status(actual, requested):

    if actual == requested:

        return "DATE MATCH"

    if actual < requested:

        return "OLDER REPORT"

    return "NEWER REPORT"


# =====================================
# STOCK-WISE DISCLOSURE SUMMARY
# =====================================

def build_summary(data):

    rows = []

    if data.empty:

        return pd.DataFrame()

    groups = data.groupby(
        ["Report Date", "Symbol"],
        sort=True
    )

    for (day, symbol), group in groups:

        bulk = group[
            group.Source == "bulk"
        ]

        block = group[
            group.Source == "block"
        ]

        short = group[
            group.Source == "short"
        ]

        row = {
            "Report Date": day,
            "Symbol": symbol,
            "Bulk Records": len(bulk),
            "Block Records": len(block),
            "Short Records": len(short)
        }

        for name, frame in [
            ("Bulk", bulk),
            ("Block", block)
        ]:

            if (
                frame.empty
                or "Side" not in frame.columns
            ):

                buy = 0.0
                sell = 0.0

            else:

                buy = frame.loc[
                    frame.Side == "BUY",
                    "Value INR"
                ].sum()

                sell = frame.loc[
                    frame.Side == "SELL",
                    "Value INR"
                ].sum()

            row[f"{name} Buy Cr"] = round(
                buy / 10000000, 4
            )

            row[f"{name} Sell Cr"] = round(
                sell / 10000000, 4
            )

            row[f"{name} Net Cr"] = round(
                (buy - sell) / 10000000, 4
            )

        row["Short Quantity"] = int(
            short.Quantity.sum()
        )

        row["Cross Report Matches"] = int(
            group["Cross Report Match"].sum()
        )

        tags = []

        if not bulk.empty:

            tags.append("BULK")

        if not block.empty:

            tags.append("BLOCK")

        if not short.empty:

            tags.append("SHORT")

        row["Disclosure Types"] = (
            " + ".join(tags)
        )

        rows.append(row)

    return pd.DataFrame(rows)


# =====================================
# CLIENT-WISE ANALYSIS
# =====================================

def client_summary(data):

    deals = data[
        data.Source.isin(
            ["bulk", "block"]
        )
    ].copy()

    if deals.empty:

        return pd.DataFrame()

    rows = []

    groups = deals.groupby([
        "Report Date",
        "Symbol",
        "Source",
        "Client"
    ])

    for keys, frame in groups:

        day, symbol, source, client = keys

        buy = frame.loc[
            frame.Side == "BUY",
            "Quantity"
        ].sum()

        sell = frame.loc[
            frame.Side == "SELL",
            "Quantity"
        ].sum()

        buy_value = frame.loc[
            frame.Side == "BUY",
            "Value INR"
        ].sum()

        sell_value = frame.loc[
            frame.Side == "SELL",
            "Value INR"
        ].sum()

        rows.append({
            "Report Date": day,
            "Symbol": symbol,
            "Source": source,
            "Client": client,
            "Buy Quantity": int(buy),
            "Sell Quantity": int(sell),
            "Net Quantity": int(buy - sell),
            "Buy Cr": round(
                buy_value / 10000000, 4
            ),
            "Sell Cr": round(
                sell_value / 10000000, 4
            ),
            "Net Cr": round(
                (buy_value - sell_value)
                / 10000000, 4
            ),
            "Both Sides": bool(
                buy > 0 and sell > 0
            ),
            "Cross Report Match": bool(
                frame[
                    "Cross Report Match"
                ].any()
            )
        })

    return pd.DataFrame(rows)


# =====================================
# SESSION STATE
# =====================================

if "v12_reports" not in st.session_state:

    st.session_state.v12_reports = {}

if "v12_requested" not in st.session_state:

    st.session_state.v12_requested = None


# =====================================
# DASHBOARD HEADER
# =====================================

st.title(
    "F&O Stock Identification V1.2"
)

st.caption(
    "Phase 1 | Market Intelligence "
    "| NSE Disclosures"
)

st.info(
    "Stock identification only. "
    "No technical indicators, "
    "option selection or trade signals."
)


# =====================================
# LOAD UNIVERSE
# =====================================

universe = None

try:

    with st.spinner(
        "Loading automatic F&O universe..."
    ):

        universe, universe_time = (
            get_fo_universe()
        )

    st.success(
        f"Automatic F&O Universe: "
        f"{len(universe)} stocks"
    )

    st.caption(
        f"Universe loaded: {universe_time}"
    )

    with st.expander(
        "View complete F&O universe"
    ):

        st.dataframe(
            universe,
            use_container_width=True
        )

        st.download_button(
            "Download F&O universe",
            universe.to_csv(index=False),
            "fo_universe_v12.csv"
        )

except Exception as e:

    st.error(
        f"F&O universe unavailable: {e}"
    )

    st.warning(
        "Reports can still be collected. "
        "F&O matching is disabled until "
        "the universe is available."
    )


# =====================================
# COLLECTION CONTROLS
# =====================================

today = now().date()

day = st.date_input(
    "Requested report date",
    value=today,
    max_value=today
)

st.caption(
    "Bulk and Block endpoints provide "
    "the available latest report. "
    "Short Selling uses a dated URL. "
    "Actual report dates are verified."
)

if st.button(
    "Refresh F&O universe"
):

    get_fo_universe.clear()

    st.rerun()


if st.button(
    "Fetch All Three NSE Reports",
    type="primary",
    use_container_width=True
):

    with st.spinner(
        "Downloading NSE reports..."
    ):

        st.session_state.v12_reports = (
            collect_all(day)
        )

        st.session_state.v12_requested = day


if (
    st.session_state.v12_requested
    is not None
    and st.session_state.v12_requested != day
):

    st.warning(
        "Selected date has changed. "
        "Press Fetch again for a new request."
    )


# =====================================
# REPORT VALIDATION AND COVERAGE
# =====================================

frames = []

audit = []

reports = st.session_state.v12_reports

for kind in SOURCES:

    result = reports.get(kind, {})

    data = result.get("data")

    error = result.get("error", "")

    origin = result.get(
        "origin",
        "Not collected"
    )

    checked = result.get(
        "checked", ""
    )

    if data is None:

        audit.append({
            "Source": LABELS[kind],
            "Status": (
                "UNAVAILABLE"
                if error
                else "NOT FETCHED"
            ),
            "Records": None,
            "Actual Date": None,
            "Requested Date": (
                st.session_state.v12_requested
            ),
            "Origin": origin,
            "Checked": checked,
            "Error": error
        })

        continue

    actual = data["Report Date"].iloc[0]

    requested = (
        st.session_state.v12_requested
    )

    status = report_status(
        actual, requested
    )

    frames.append(data)

    audit.append({
        "Source": LABELS[kind],
        "Status": status,
        "Records": len(data),
        "Actual Date": actual,
        "Requested Date": requested,
        "Origin": origin,
        "Checked": checked,
        "Error": error
    })


# =====================================
# SOURCE STATUS
# =====================================

st.subheader("NSE Source Coverage")

cols = st.columns(3)

for i, kind in enumerate(SOURCES):

    result = reports.get(kind, {})

    data = result.get("data")

    with cols[i]:

        st.metric(
            LABELS[kind],
            len(data)
            if data is not None
            else "—"
        )

        if data is not None:

            st.caption(
                str(
                    data["Report Date"].iloc[0]
                )
            )


audit_df = pd.DataFrame(audit)

st.dataframe(
    audit_df,
    use_container_width=True,
    hide_index=True
)

st.download_button(
    "Download diagnostics",
    audit_df.to_csv(index=False),
    "deal_diagnostics_v12.csv"
)


if frames:

    st.metric(
        "Total validated records",
        sum(len(d) for d in frames)
    )

    actual_dates = sorted({
        d["Report Date"].iloc[0]
        for d in frames
    })

    if len(actual_dates) > 1:

        st.warning(
            "Reports have different dates. "
            "They will be analyzed separately."
        )

    if any(
        d < today for d in actual_dates
    ):

        st.warning(
            "Historical disclosures available. "
            "Do not interpret them as "
            "today's live market activity."
        )


# =====================================
# F&O MATCHING
# =====================================

if frames and universe is not None:

    matched = match_reports(
        frames,
        universe.Symbol.tolist()
    )

    st.subheader(
        "F&O Disclosure Coverage"
    )

    c1, c2 = st.columns(2)

    c1.metric(
        "F&O matched records",
        len(matched)
    )

    c2.metric(
        "Unique F&O stocks",
        matched.Symbol.nunique()
    )

    coverage = []

    for kind in SOURCES:

        original = reports.get(
            kind, {}
        ).get("data")

        selected = matched[
            matched.Source == kind
        ]

        coverage.append({
            "Source": LABELS[kind],
            "All Records": (
                len(original)
                if original is not None
                else None
            ),
            "F&O Records": (
                len(selected)
                if original is not None
                else None
            ),
            "F&O Stocks": (
                selected.Symbol.nunique()
                if original is not None
                else None
            )
        })

    st.dataframe(
        pd.DataFrame(coverage),
        hide_index=True,
        use_container_width=True
    )

    if not matched.empty:

        summary = build_summary(matched)

        # Missing source is not zero activity.

        for kind in SOURCES:

            available_dates = set()

            for frame in frames:

                if frame.Source.iloc[0] == kind:

                    available_dates.update(
                        frame["Report Date"]
                    )

            missing = ~summary[
                "Report Date"
            ].isin(available_dates)

            if kind == "bulk":

                columns = [
                    "Bulk Records",
                    "Bulk Buy Cr",
                    "Bulk Sell Cr",
                    "Bulk Net Cr"
                ]

            elif kind == "block":

                columns = [
                    "Block Records",
                    "Block Buy Cr",
                    "Block Sell Cr",
                    "Block Net Cr"
                ]

            else:

                columns = [
                    "Short Records",
                    "Short Quantity"
                ]

            summary.loc[
                missing, columns
            ] = None

        selected_date = st.selectbox(
            "Analyze actual report date",
            sorted(
                summary[
                    "Report Date"
                ].unique(),
                reverse=True
            )
        )

        daily = summary[
            summary["Report Date"]
            == selected_date
        ].copy()

        st.subheader(
            "Stock-wise Disclosure Summary"
        )

        st.caption(
            "Values are reported disclosures, "
            "not total institutional positions. "
            "Bulk and Block values remain separate."
        )

        st.dataframe(
            daily.sort_values("Symbol"),
            hide_index=True,
            use_container_width=True
        )

        st.download_button(
            "Download Stock Summary",
            daily.to_csv(index=False),
            "fo_stock_summary_v12.csv"
        )

        st.subheader(
            "Stock Details"
        )

        symbol = st.selectbox(
            "Select F&O stock",
            sorted(
                daily.Symbol.unique()
            )
        )

        selected = matched[
            (matched.Symbol == symbol)
            &
            (
                matched["Report Date"]
                == selected_date
            )
        ]

        st.dataframe(
            selected,
            use_container_width=True,
            hide_index=True
        )

        clients = client_summary(
            selected
        )

        if not clients.empty:

            st.subheader(
                "Client-wise Buy and Sell"
            )

            st.dataframe(
                clients,
                hide_index=True,
                use_container_width=True
            )

        st.download_button(
            "Download All F&O Disclosures",
            matched.to_csv(index=False),
            "fo_deals_v12.csv"
        )

    else:

        st.info(
            "No F&O symbols matched "
            "the available disclosures."
        )


# =====================================
# ORIGINAL REPORTS
# =====================================

for frame in frames:

    kind = frame.Source.iloc[0]

    with st.expander(
        f"Original {LABELS[kind]} "
        f"({len(frame)} records)"
    ):

        st.dataframe(
            frame,
            use_container_width=True
        )

        st.download_button(
            f"Download {LABELS[kind]}",
            frame.to_csv(index=False),
            f"{kind}_validated_v12.csv",
            key=f"download_{kind}"
        )


# =====================================
# IMPORTANT LIMITATIONS
# =====================================

st.divider()

st.caption(
    "Bulk and Block cross-report matches "
    "are flagged, not added together. "
    "Short Selling quantity is not "
    "outstanding short interest. "
    "Disclosure activity alone does not "
    "establish bullish or bearish direction."
)

st.caption(
    "V1.2 | Automatic F&O Universe "
    "| NSE Disclosure Intelligence "
    "| No automatic orders"
)
