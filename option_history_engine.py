
"""
F&O HISTORICAL COMPARISON INTELLIGENCE
Version 2.2.0

Uses existing validated historical archives.
No new API requests.
No automatic orders.
"""

import io
import json
import math
import hashlib
import zipfile

from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd
import numpy as np
import streamlit as st


VERSION = "2.2.0"

IST = ZoneInfo("Asia/Kolkata")

ROOT = Path(__file__).resolve().parent

DATA = (
    ROOT
    / "historical_data"
    / "option_intelligence"
)

MIN_MATCHED_COVERAGE = 0.90

MIN_OI_CHANGE = 5.0

MIN_VOLUME_RATIO = 1.5

MIN_CASH_PARTICIPATION = 1.5

MAX_EARLY_MOVE = 1.0

EXTENDED_MOVE = 3.0

MAX_ARCHIVE_BYTES = 100_000_000


# =====================================
# UTILITIES
# =====================================

def now():

    return datetime.now(IST)


def number(value):

    try:

        value = float(value)

        if math.isfinite(value):

            return value

    except (TypeError, ValueError):

        pass

    return None


def ratio(a, b):

    a = number(a)
    b = number(b)

    if a is None or b is None or b <= 0:

        return None

    return a / b


def change_pct(current, previous):

    value = ratio(current, previous)

    if value is None:

        return None

    return (value - 1) * 100


def difference(current, previous):

    current = number(current)
    previous = number(previous)

    if current is None or previous is None:

        return None

    return current - previous


def sha256(content):

    return hashlib.sha256(content).hexdigest()


def truth(value):

    return str(value).strip().lower() in [

        "true", "1", "yes"

    ]


def parse_time(value):

    stamp = pd.Timestamp(value)

    if pd.isna(stamp):

        raise ValueError("Missing timestamp")

    if stamp.tzinfo is None:

        raise ValueError("Timezone missing")

    return stamp.tz_convert(IST)


def parse_date(value):

    return datetime.strptime(

        str(value),

        "%Y-%m-%d"

    ).date()


# =====================================
# VERIFIED HISTORICAL ARCHIVES
# =====================================

def read_archive(path, expected_hash=None):

    if path.stat().st_size > MAX_ARCHIVE_BYTES:

        raise ValueError("Archive too large")

    content = path.read_bytes()

    digest = sha256(content)

    if expected_hash and digest != expected_hash:

        raise ValueError("Archive SHA256 mismatch")

    with zipfile.ZipFile(

        io.BytesIO(content)

    ) as archive:

        if sum(

            item.file_size

            for item in archive.infolist()

        ) > MAX_ARCHIVE_BYTES:

            raise ValueError(

                "Uncompressed archive too large"

            )

        if len(archive.namelist()) != len(

            set(archive.namelist())

        ):

            raise ValueError(

                "Duplicate archive members"

            )

        manifest = json.loads(

            archive.read("manifest.json")

        )

        frames = {}

        for name in [

            "stock_summary.csv",

            "validated_contracts.csv",

            "rejected_contracts.csv"

        ]:

            raw = archive.read(name)

            metadata = manifest["Files"][name]

            if sha256(raw) != metadata["SHA256"]:

                raise ValueError(

                    f"File hash mismatch: {name}"

                )

            if len(raw) != metadata["Bytes"]:

                raise ValueError(

                    f"File size mismatch: {name}"

                )

            frames[name] = pd.read_csv(raw_buffer(raw))

    summary = frames["stock_summary.csv"]

    contracts = frames["validated_contracts.csv"]

    rejected = frames["rejected_contracts.csv"]

    if len(contracts) != manifest["Validated Contracts"]:

        raise ValueError(

            "Validated contract count mismatch"

        )

    if len(rejected) != manifest["Rejected Contracts"]:

        raise ValueError(

            "Rejected contract count mismatch"

        )

    if len(summary) != manifest["Requested Stocks"]:

        raise ValueError(

            "Stock summary count mismatch"

        )

    if summary["Symbol"].isna().any():

        raise ValueError("Missing summary symbol")

    if summary["Symbol"].duplicated().any():

        raise ValueError("Duplicate summary symbol")

    if contracts["Instrument Key"].isna().any():

        raise ValueError("Missing instrument key")

    if contracts["Instrument Key"].duplicated().any():

        raise ValueError("Duplicate instrument key")

    retrieved = parse_time(

        manifest["Retrieved At"]

    )

    cash_date = parse_date(

        manifest["Cash Report Date"]

    )

    if cash_date > retrieved.date():

        raise ValueError("Invalid report date")

    if not set(contracts["Symbol"]).issubset(

        set(summary["Symbol"])

    ):

        raise ValueError("Unknown contract symbols")

    manifest["Archive SHA256"] = digest

    manifest["Archive Path"] = str(

        path.relative_to(ROOT)

    )

    return {

        "summary": summary,

        "contracts": contracts,

        "manifest": manifest,

        "retrieved": retrieved,

        "cash_date": cash_date

    }


def raw_buffer(content):

    return io.BytesIO(content)


def load_history():

    snapshots = {}

    diagnostics = []

    if not DATA.exists():

        return snapshots, diagnostics

    for folder in sorted(DATA.iterdir()):

        if not folder.is_dir():

            continue

        try:

            folder_date = parse_date(folder.name)

        except Exception:

            continue

        try:

            metadata = json.loads(

                (folder / "latest.json")

                .read_text(encoding="utf-8")

            )

            filename = metadata["Archive"]

            if Path(filename).name != filename:

                raise ValueError(

                    "Invalid archive filename"

                )

            path = folder / filename

            snapshot = read_archive(

                path,

                metadata["SHA256"]

            )

            if snapshot["retrieved"].date() != folder_date:

                raise ValueError(

                    "Retrieval folder date mismatch"

                )

            manifest = snapshot["manifest"]

            cash_date = snapshot["cash_date"]

            if cash_date != folder_date:

                raise ValueError(

                    "Cash date differs from retrieval date"

                )

            if snapshot["retrieved"].hour < 16:

                raise ValueError(

                    "Not an after-market retrieval"

                )

            if manifest["Validated Returned Chains"] != (

                manifest["Universe"]

            ):

                raise ValueError(

                    "Incomplete universe snapshot"

                )

            if manifest["Requested Stocks"] != (

                manifest["Universe"]

            ):

                raise ValueError(

                    "Partial universe requested"

                )

            existing = snapshots.get(cash_date)

            if (

                existing is None

                or snapshot["retrieved"]

                > existing["retrieved"]

            ):

                snapshots[cash_date] = snapshot

            diagnostics.append({

                "Date": folder.name,

                "Cash Report Date": str(cash_date),

                "Status": "ARCHIVE VERIFIED",

                "Stocks": len(snapshot["summary"]),

                "Contracts": len(snapshot["contracts"]),

                "Exchange Time Verified": False

            })

        except Exception as error:

            diagnostics.append({

                "Date": folder.name,

                "Status": "REJECTED",

                "Reason": str(error)

            })

    return snapshots, diagnostics


# =====================================
# MATCH SAME OPTION CONTRACTS
# =====================================

def prepare_contracts(frame):

    required = [

        "Instrument Key",

        "Symbol",

        "Expiry",

        "Side",

        "Strike",

        "OI",

        "Volume",

        "IV",

        "Greeks Usable",

        "Research Ready"

    ]

    missing = set(required) - set(frame.columns)

    if missing:

        raise ValueError(

            f"Missing columns: {sorted(missing)}"

        )

    result = frame[required].copy()

    for column in [

        "Strike", "OI", "Volume", "IV"

    ]:

        result[column] = pd.to_numeric(

            result[column],

            errors="coerce"

        ).replace(

            [np.inf, -np.inf],

            np.nan

        )

    result["Greeks Usable"] = (

        result["Greeks Usable"].map(truth)

    )

    result["Research Ready"] = (

        result["Research Ready"].map(truth)

    )

    result = result[

        result["Side"].isin(["CE", "PE"])

        & result["Strike"].gt(0)

        & result["OI"].ge(0)

        & result["Volume"].ge(0)

    ]

    return result


def match_contracts(current, previous):

    keys = [

        "Instrument Key",

        "Symbol",

        "Expiry",

        "Side",

        "Strike"

    ]

    return current.merge(

        previous,

        on=keys,

        how="inner",

        suffixes=("_Current", "_Previous"),

        validate="one_to_one"

    )


# =====================================
# SAME EXPIRY SIDE COMPARISON
# =====================================

def side_comparison(

    current,

    previous,

    matched,

    side

):

    c = current[current["Side"].eq(side)]

    p = previous[previous["Side"].eq(side)]

    m = matched[matched["Side"].eq(side)]

    prefix = "Call" if side == "CE" else "Put"

    coverage = (

        len(m) / max(len(c), len(p))

        if max(len(c), len(p)) > 0

        else 0

    )

    result = {

        prefix + " Current Contracts": len(c),

        prefix + " Previous Contracts": len(p),

        prefix + " Matched Contracts": len(m),

        prefix + " Matched Coverage %":

            round(coverage * 100, 2),

        prefix + " Comparable":

            coverage >= MIN_MATCHED_COVERAGE

            and len(m) > 0

    }

    if not result[prefix + " Comparable"]:

        return result

    current_oi = float(m["OI_Current"].sum())

    previous_oi = float(m["OI_Previous"].sum())

    current_volume = float(

        m["Volume_Current"].sum()

    )

    previous_volume = float(

        m["Volume_Previous"].sum()

    )

    result.update({

        prefix + " Matched Current OI":

            current_oi,

        prefix + " Matched Previous OI":

            previous_oi,

        prefix + " Historical OI Change":

            current_oi - previous_oi,

        prefix + " Historical OI Change %":

            change_pct(

                current_oi,

                previous_oi

            ),

        prefix + " Matched Current Volume":

            current_volume,

        prefix + " Matched Previous Volume":

            previous_volume,

        prefix + " Historical Volume Ratio":

            ratio(

                current_volume,

                previous_volume

            )

    })

    return result


def compare_iv(matched, spot, side):

    if spot is None or spot <= 0:

        return None, None

    rows = matched[

        matched["Side"].eq(side)

        & matched["Greeks Usable_Current"]

        & matched["Greeks Usable_Previous"]

        & matched["IV_Current"].gt(0)

        & matched["IV_Previous"].gt(0)

    ].copy()

    if rows.empty:

        return None, None

    rows["Distance"] = (

        rows["Strike"] - spot

    ).abs()

    rows = rows.sort_values(

        ["Distance", "Strike"]

    )

    row = rows.iloc[0]

    return (

        float(row["Strike"]),

        difference(

            row["IV_Current"],

            row["IV_Previous"]

        )

    )


# =====================================
# STOCK CLASSIFICATION
# =====================================

def classify(item):

    price = number(item.get("Change %"))

    volume = number(item.get("Volume Ratio"))

    turnover = number(item.get("Turnover Ratio"))

    delivery = number(

        item.get("Delivery Quantity Ratio")

    )

    category = str(

        item.get("Category", "")

    ).upper()

    futures_oi = number(

        item.get("Futures OI Change %")

    )

    call_oi = number(

        item.get("Call Historical OI Change %")

    )

    put_oi = number(

        item.get("Put Historical OI Change %")

    )

    call_volume = number(

        item.get("Call Historical Volume Ratio")

    )

    put_volume = number(

        item.get("Put Historical Volume Ratio")

    )

    cash_activity = (

        volume is not None

        and turnover is not None

        and volume >= MIN_CASH_PARTICIPATION

        and turnover >= MIN_CASH_PARTICIPATION

    )

    delivery_activity = (

        delivery is not None

        and delivery >= MIN_CASH_PARTICIPATION

    )

    futures_activity = (

        futures_oi is not None

        and abs(futures_oi) >= 3

    )

    options_activity = any([

        call_oi is not None

        and abs(call_oi) >= MIN_OI_CHANGE,

        put_oi is not None

        and abs(put_oi) >= MIN_OI_CHANGE,

        call_volume is not None

        and call_volume >= MIN_VOLUME_RATIO,

        put_volume is not None

        and put_volume >= MIN_VOLUME_RATIO

    ])

    reasons = []

    if cash_activity:

        reasons.append("CASH PARTICIPATION")

    if delivery_activity:

        reasons.append("DELIVERY ACTIVITY")

    if futures_activity:

        reasons.append("FUTURES OI ACTIVITY")

    if options_activity:

        reasons.append("OPTIONS ACTIVITY")

    if "BULLISH" in category:

        direction = "BULLISH WATCH"

    elif "BEARISH" in category:

        direction = "BEARISH WATCH"

    else:

        direction = "UNCONFIRMED"

    if price is not None and abs(price) >= EXTENDED_MOVE:

        status = "EXTENDED - DO NOT CHASE"

        rank = 5

    elif item.get("Historical Status") != (

        "COMPARABLE RETRIEVAL SNAPSHOTS"

    ):

        status = "HISTORICAL DATA NOT COMPARABLE"

        rank = 6

    elif (

        price is not None

        and abs(price) <= MAX_EARLY_MOVE

        and cash_activity

        and (

            futures_activity

            or options_activity

        )

    ):

        status = "EARLY ACTIVITY WATCH"

        rank = 1

    elif (

        category in [

            "PRIORITY BULLISH",

            "PRIORITY BEARISH"

        ]

        and options_activity

    ):

        status = "DIRECTIONAL ACTIVITY WATCH"

        rank = 2

    elif (

        cash_activity

        and options_activity

    ):

        status = "COMBINED ACTIVITY WATCH"

        rank = 3

    elif options_activity or futures_activity:

        status = "DERIVATIVES ACTIVITY REVIEW"

        rank = 4

    else:

        status = "OBSERVATION ONLY"

        rank = 7

    item.update({

        "Historical Opportunity": status,

        "Historical Rank": rank,

        "Direction Watch": direction,

        "Cash Activity": cash_activity,

        "Delivery Activity": delivery_activity,

        "Futures Activity": futures_activity,

        "Options Activity": options_activity,

        "Historical Evidence":

            " | ".join(reasons),

        "Live Confirmation Required": True,

        "Option Direction Confirmed": False,

        "Trade Signal": "NOT GENERATED"

    })

    return item


# =====================================
# FULL HISTORICAL COMPARISON
# =====================================

def compare_snapshots(current, previous):

    c_summary = current["summary"]

    p_summary = previous["summary"]

    c_contracts = prepare_contracts(

        current["contracts"]

    )

    p_contracts = prepare_contracts(

        previous["contracts"]

    )

    previous_map = {

        row["Symbol"]: row.to_dict()

        for _, row in p_summary.iterrows()

    }

    output = []

    for _, row in c_summary.iterrows():

        item = row.to_dict()

        symbol = item["Symbol"]

        old = previous_map.get(symbol)

        item.update({

            "Current Report Date":

                str(current["cash_date"]),

            "Previous Report Date":

                str(previous["cash_date"]),

            "Current Retrieval":

                current["retrieved"].isoformat(),

            "Previous Retrieval":

                previous["retrieved"].isoformat(),

            "Historical Status":

                "PREVIOUS STOCK UNAVAILABLE",

            "Exchange Timestamp Verified": False,

            "Independent NSE Cross Check": False

        })

        if old is None:

            output.append(classify(item))

            continue

        expiry = str(

            item.get("Option Expiry", "")

        )

        previous_expiry = str(

            old.get("Option Expiry", "")

        )

        item["Previous Option Expiry"] = (

            previous_expiry

        )

        if expiry != previous_expiry:

            item["Historical Status"] = (

                "EXPIRY ROLLOVER - NOT COMPARABLE"

            )

            output.append(classify(item))

            continue

        c = c_contracts[

            c_contracts["Symbol"].eq(symbol)

            & c_contracts["Expiry"].eq(expiry)

        ]

        p = p_contracts[

            p_contracts["Symbol"].eq(symbol)

            & p_contracts["Expiry"].eq(expiry)

        ]

        matched = match_contracts(c, p)

        call = side_comparison(

            c, p, matched, "CE"

        )

        put = side_comparison(

            c, p, matched, "PE"

        )

        item.update(call)

        item.update(put)

        if not (

            call["Call Comparable"]

            and put["Put Comparable"]

        ):

            item["Historical Status"] = (

                "INSUFFICIENT MATCHED CONTRACTS"

            )

            output.append(classify(item))

            continue

        current_pcr = ratio(

            put["Put Matched Current OI"],

            call["Call Matched Current OI"]

        )

        previous_pcr = ratio(

            put["Put Matched Previous OI"],

            call["Call Matched Previous OI"]

        )

        spot = number(item.get("Spot"))

        ce_strike, ce_iv = compare_iv(

            matched, spot, "CE"

        )

        pe_strike, pe_iv = compare_iv(

            matched, spot, "PE"

        )

        item.update({

            "Historical Status":

                "COMPARABLE RETRIEVAL SNAPSHOTS",

            "Matched Current PCR":

                current_pcr,

            "Matched Previous PCR":

                previous_pcr,

            "Historical PCR Change":

                difference(

                    current_pcr,

                    previous_pcr

                ),

            "CE IV Compared Strike":

                ce_strike,

            "PE IV Compared Strike":

                pe_strike,

            "CE Historical IV Change":

                ce_iv,

            "PE Historical IV Change":

                pe_iv,

            "Previous Cash Close":

                old.get("Close"),

            "Cash Close Change %":

                change_pct(

                    item.get("Close"),

                    old.get("Close")

                ),

            "Historical Matched Contracts":

                len(matched)

        })

        output.append(classify(item))

    frame = pd.DataFrame(output)

    frame = frame.sort_values(

        ["Historical Rank", "Symbol"]

    ).reset_index(drop=True)

    comparable = int(

        frame["Historical Status"].eq(

            "COMPARABLE RETRIEVAL SNAPSHOTS"

        ).sum()

    )

    rollover = int(

        frame["Historical Status"].eq(

            "EXPIRY ROLLOVER - NOT COMPARABLE"

        ).sum()

    )

    report = {

        "Version": VERSION,

        "Current Date":

            str(current["cash_date"]),

        "Previous Date":

            str(previous["cash_date"]),

        "Universe": len(frame),

        "Comparable Stocks": comparable,

        "Comparable Coverage %":

            round(

                comparable / len(frame) * 100,

                2

            ) if len(frame) else 0,

        "Expiry Rollover Stocks": rollover,

        "Early Activity Stocks":

            int(

                frame["Historical Opportunity"]

                .eq("EARLY ACTIVITY WATCH")

                .sum()

            ),

        "Directional Activity Stocks":

            int(

                frame["Historical Opportunity"]

                .eq("DIRECTIONAL ACTIVITY WATCH")

                .sum()

            ),

        "Current Archive SHA256":

            current["manifest"]["Archive SHA256"],

        "Previous Archive SHA256":

            previous["manifest"]["Archive SHA256"],

        "Exchange Timestamp Verified": False,

        "Consecutive Exchange Sessions Verified": False,

        "Independent NSE Cross Check": False,

        "Trade Signal": "NOT GENERATED"

    }

    return frame, report


# =====================================
# STREAMLIT DASHBOARD
# =====================================

def render_history():

    st.header(

        "HISTORICAL INTELLIGENCE V" + VERSION

    )

    st.caption(

        "Cash + Futures + Options | "

        "Same Contract Comparison | "

        "No New API Requests"

    )

    st.warning(

        "Historical archives contain retrieval "

        "snapshots, not independently verified "

        "exchange closing option quotes."

    )

    snapshots, diagnostics = load_history()

    dates = sorted(

        snapshots.keys(),

        reverse=True

    )

    a, b = st.columns(2)

    a.metric(

        "Verified Archive Dates",

        len(dates)

    )

    b.metric(

        "Latest Report",

        str(dates[0]) if dates else "Unavailable"

    )

    with st.expander(

        "Historical Archive Diagnostics"

    ):

        st.dataframe(

            pd.DataFrame(diagnostics),

            hide_index=True,

            use_container_width=True

        )

    if not dates:

        st.error(

            "No eligible historical archives found"

        )

        return

    if len(dates) < 2:

        st.info(

            "FIRST HISTORICAL SNAPSHOT AVAILABLE. "

            "Waiting for a second distinct "

            "report date."

        )

        latest = snapshots[dates[0]]

        st.json(latest["manifest"])

        st.dataframe(

            latest["summary"],

            hide_index=True,

            use_container_width=True

        )

        return

    current_date = st.selectbox(

        "Current Report Date",

        dates,

        index=0

    )

    previous_dates = [

        day for day in dates

        if day < current_date

    ]

    if not previous_dates:

        st.info(

            "No earlier snapshot available"

        )

        return

    previous_date = st.selectbox(

        "Previous Report Date",

        previous_dates,

        index=0

    )

    try:

        frame, report = compare_snapshots(

            snapshots[current_date],

            snapshots[previous_date]

        )

    except Exception as error:

        st.error(str(error))

        return

    a, b, c = st.columns(3)

    a.metric(

        "Universe",

        report["Universe"]

    )

    b.metric(

        "Comparable Stocks",

        report["Comparable Stocks"]

    )

    c.metric(

        "Comparable Coverage",

        f'{report["Comparable Coverage %"]}%'

    )

    a, b, c = st.columns(3)

    a.metric(

        "Early Activity",

        report["Early Activity Stocks"]

    )

    b.metric(

        "Directional Activity",

        report["Directional Activity Stocks"]

    )

    c.metric(

        "Expiry Rollover",

        report["Expiry Rollover Stocks"]

    )

    columns = [

        "Symbol",

        "Historical Opportunity",

        "Direction Watch",

        "Change %",

        "Volume Ratio",

        "Turnover Ratio",

        "Delivery Quantity Ratio",

        "Futures OI Change %",

        "Call Historical OI Change %",

        "Put Historical OI Change %",

        "Call Historical Volume Ratio",

        "Put Historical Volume Ratio",

        "Historical PCR Change",

        "CE Historical IV Change",

        "PE Historical IV Change",

        "Historical Evidence",

        "Historical Status"

    ]

    columns = [

        c for c in columns

        if c in frame.columns

    ]

    tabs = st.tabs([

        "Early Activity",

        "Bullish Watch",

        "Bearish Watch",

        "Derivatives Activity",

        "Extended",

        "Rollover / Incomplete",

        "All Stocks",

        "Diagnostics"

    ])

    with tabs[0]:

        st.dataframe(

            frame[

                frame["Historical Opportunity"]

                .eq("EARLY ACTIVITY WATCH")

            ][columns],

            hide_index=True,

            use_container_width=True

        )

    with tabs[1]:

        st.dataframe(

            frame[

                frame["Direction Watch"]

                .eq("BULLISH WATCH")

            ][columns],

            hide_index=True,

            use_container_width=True

        )

    with tabs[2]:

        st.dataframe(

            frame[

                frame["Direction Watch"]

                .eq("BEARISH WATCH")

            ][columns],

            hide_index=True,

            use_container_width=True

        )

    with tabs[3]:

        st.dataframe(

            frame[

                frame["Options Activity"]

                | frame["Futures Activity"]

            ][columns],

            hide_index=True,

            use_container_width=True

        )

    with tabs[4]:

        st.dataframe(

            frame[

                frame["Historical Opportunity"]

                .eq("EXTENDED - DO NOT CHASE")

            ][columns],

            hide_index=True,

            use_container_width=True

        )

    with tabs[5]:

        st.dataframe(

            frame[

                ~frame["Historical Status"].eq(

                    "COMPARABLE RETRIEVAL SNAPSHOTS"

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

        st.json(report)

    st.download_button(

        "DOWNLOAD HISTORICAL INTELLIGENCE",

        frame.to_csv(index=False),

        file_name=(

            "historical_intelligence_v22_"

            + str(current_date)

            + ".csv"

        ),

        mime="text/csv"

    )

    st.download_button(

        "DOWNLOAD HISTORICAL DIAGNOSTICS",

        json.dumps(

            {

                "summary": report,

                "archives": diagnostics

            },

            indent=2,

            default=str

        ),

        file_name="historical_diagnostics_v22.json",

        mime="application/json"

    )
