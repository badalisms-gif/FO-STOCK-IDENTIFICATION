
import os
import io
import json
import gzip
import hashlib
import zipfile
import numpy as np
import pandas as pd
import streamlit as st

from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo

from option_chain_intelligence import (
    scan_options,
    summarize_chain
)

VERSION = "2.1.0"

IST = ZoneInfo("Asia/Kolkata")

ROOT = Path(__file__).resolve().parent

DATA = ROOT / "historical_data" / "option_intelligence"

IDENTITY = [
    "Symbol",
    "Expiry",
    "Instrument Key",
    "Side",
    "Strike",
    "Retrieved At"
]

GREEKS = [
    "IV",
    "Delta",
    "Gamma",
    "Theta",
    "Vega"
]

NUMERIC = [
    "Strike",
    "Spot",
    "LTP",
    "Bid",
    "Ask",
    "OI",
    "Previous OI",
    "Volume",
    "Spread %",
    *GREEKS
]


def now():

    return datetime.now(IST)


def sha256(content):

    return hashlib.sha256(content).hexdigest()


def percentage(series):

    return round(float(series.mean()) * 100, 2) \
        if len(series) else 0.0


def atomic_write(path, content):

    path.parent.mkdir(
        parents=True,
        exist_ok=True
    )

    temp = path.with_suffix(
        path.suffix + ".tmp"
    )

    temp.write_bytes(content)

    temp.replace(path)


def csv_bytes(frame):

    return frame.to_csv(
        index=False,
        lineterminator="\n"
    ).encode("utf-8")


def validate_contracts(detail):

    if detail.empty:

        raise ValueError(
            "No option contracts received"
        )

    required = set(
        IDENTITY + NUMERIC
    )

    missing = required - set(detail.columns)

    if missing:

        raise ValueError(
            f"Missing columns: {sorted(missing)}"
        )

    frame = detail.copy()

    frame["Original Row"] = np.arange(
        len(frame)
    )

    for column in IDENTITY:

        if column != "Strike":

            frame[column] = (
                frame[column]
                .astype("string")
                .str.strip()
            )

    for column in NUMERIC:

        frame[column] = pd.to_numeric(
            frame[column],
            errors="coerce"
        ).replace(
            [np.inf, -np.inf],
            np.nan
        )

    frame["Symbol"] = (
        frame["Symbol"].str.upper()
    )

    frame["Side"] = (
        frame["Side"].str.upper()
    )

    expiry = pd.to_datetime(
        frame["Expiry"],
        errors="coerce",
        format="%Y-%m-%d"
    )

    retrieved = pd.to_datetime(
        frame["Retrieved At"],
        errors="coerce",
        utc=True
    )

    identity_ok = pd.Series(
        True,
        index=frame.index
    )

    for column in IDENTITY:

        identity_ok &= (
            frame[column].notna()
        )

        if column != "Strike":

            identity_ok &= (
                frame[column].ne("")
            ).fillna(False)

    identity_ok &= (
        frame["Side"].isin(["CE", "PE"])
        & frame["Strike"].gt(0)
        & expiry.notna()
        & retrieved.notna()
    )

    identity_ok &= (
        frame["Instrument Key"]
        .str.startswith("NSE_FO|")
        .fillna(False)
    )

    duplicate = (
        frame["Instrument Key"]
        .duplicated(keep=False)
    )

    duplicate |= frame.duplicated(
        subset=[
            "Symbol",
            "Expiry",
            "Side",
            "Strike"
        ],
        keep=False
    )

    market_ok = pd.Series(
        True,
        index=frame.index
    )

    for column in [
        "LTP",
        "OI",
        "Previous OI",
        "Volume"
    ]:

        market_ok &= (
            frame[column].notna()
            & frame[column].ge(0)
        )

    market_ok &= frame["Spot"].gt(0)

    frame["Identity Valid"] = identity_ok

    frame["Duplicate Contract"] = duplicate

    frame["Market Data Valid"] = market_ok

    valid = (
        identity_ok
        & ~duplicate
        & market_ok
    )

    frame["Contract Valid"] = valid

    frame["Validation Reason"] = ""

    frame.loc[
        ~identity_ok,
        "Validation Reason"
    ] += "INVALID IDENTITY; "

    frame.loc[
        duplicate,
        "Validation Reason"
    ] += "DUPLICATE CONTRACT; "

    frame.loc[
        ~market_ok,
        "Validation Reason"
    ] += "INVALID MARKET DATA; "

    rejected = frame.loc[~valid].copy()

    clean = frame.loc[valid].copy()

    if clean.empty:

        raise ValueError(
            "No contracts passed validation"
        )

    clean["OI Change"] = (
        clean["OI"]
        - clean["Previous OI"]
    )

    clean["OI Change %"] = (
        clean["OI Change"]
        / clean["Previous OI"].where(
            clean["Previous OI"].gt(0)
        )
        * 100
    )

    received = (
        clean[GREEKS]
        .notna()
        .all(axis=1)
    )

    delta_ok = (
        (
            clean["Side"].eq("CE")
            & clean["Delta"].between(0, 1)
        )
        |
        (
            clean["Side"].eq("PE")
            & clean["Delta"].between(-1, 0)
        )
    )

    usable = (
        received
        & clean["IV"].gt(0)
        & delta_ok
        & clean["Gamma"].ge(0)
        & clean["Vega"].ge(0)
    )

    usable &= ~(
        clean["Gamma"].eq(0)
        & clean["Theta"].eq(0)
        & clean["Vega"].eq(0)
    )

    clean["Greeks Received"] = received

    clean["Greeks Usable"] = usable

    book_ok = (
        clean["Bid"].gt(0)
        & clean["Ask"].ge(clean["Bid"])
    )

    midpoint = (
        clean["Bid"] + clean["Ask"]
    ) / 2

    clean["Spread %"] = (
        (
            clean["Ask"] - clean["Bid"]
        )
        / midpoint.where(book_ok)
        * 100
    )

    clean["Liquidity Screen"] = (
        book_ok
        & clean["Spread %"].le(2)
        & clean["LTP"].gt(0)
        & clean["Volume"].gt(0)
        & clean["OI"].gt(0)
    )

    clean["Research Ready"] = (
        clean["Greeks Usable"]
        & clean["Liquidity Screen"]
    )

    clean["Exchange Time Verified"] = False

    clean["Live Tradable Verified"] = False

    return clean, rejected


def reconcile(summary, clean):

    if summary["Symbol"].isna().any():

        raise ValueError(
            "Summary contains missing symbols"
        )

    if summary["Symbol"].duplicated().any():

        raise ValueError(
            "Duplicate summary symbols"
        )

    output = []

    for _, row in summary.iterrows():

        item = row.to_dict()

        symbol = item["Symbol"]

        expiry = str(
            item.get("Option Expiry", "")
        )

        chain = clean[
            clean["Symbol"].eq(symbol)
            & clean["Expiry"].eq(expiry)
        ].copy()

        expected = pd.to_numeric(
            item.get("Contracts"),
            errors="coerce"
        )

        expected_strikes = pd.to_numeric(
            item.get("Strikes"),
            errors="coerce"
        )

        actual = len(chain)

        actual_strikes = (
            chain["Strike"].nunique()
        )

        count_ok = (
            pd.notna(expected)
            and expected == actual
            and pd.notna(expected_strikes)
            and expected_strikes == actual_strikes
        )

        pair_coverage = (
            actual / (2 * expected_strikes)
            if pd.notna(expected_strikes)
            and expected_strikes > 0
            else 0
        )

        complete = (
            actual > 0
            and count_ok
            and pair_coverage == 1
        )

        item.update({

            "Expected Contracts": expected,

            "Validated Contracts": actual,

            "Reconciliation Passed": count_ok,

            "Validated Pair Coverage %":
                round(pair_coverage * 100, 2),

            "Received Greeks %":
                percentage(
                    chain["Greeks Received"]
                ),

            "Usable Greeks %":
                percentage(
                    chain["Greeks Usable"]
                ),

            "Research Ready Contracts":
                int(
                    chain["Research Ready"].sum()
                ),

            "Qualified CE":
                int(
                    (
                        chain["Research Ready"]
                        & chain["Side"].eq("CE")
                    ).sum()
                ),

            "Qualified PE":
                int(
                    (
                        chain["Research Ready"]
                        & chain["Side"].eq("PE")
                    ).sum()
                ),

            "Quality Status": (
                "VALIDATED RETURNED CHAIN"
                if complete
                else "INCOMPLETE - REVIEW"
            ),

            "Exchange Closing Time Verified":
                False,

            "Trade Signal":
                "NOT GENERATED"

        })

        if complete:

            metadata = {

                "Spot": float(
                    chain["Spot"].median()
                ),

                "Strikes": actual_strikes,

                "Contracts": actual,

                "Returned Pair Coverage %": 100,

                "OI Coverage %": 100,

                "Previous OI Coverage %": 100,

                "Volume Coverage %": 100,

                "Greeks Coverage %":
                    item["Received Greeks %"]

            }

            item.update(
                summarize_chain(
                    chain,
                    metadata
                )
            )

        else:

            for column in [

                "PCR OI",
                "PCR Volume",
                "Max Pain",
                "Call OI",
                "Put OI",
                "Call OI Change",
                "Put OI Change",
                "Call OI Change %",
                "Put OI Change %",
                "Call Volume",
                "Put Volume",
                "ATM Reference",
                "Liquid CE Contracts",
                "Liquid PE Contracts"

            ]:

                item[column] = None

            for column in list(item):

                if column.startswith(
                    ("ATM CE ", "ATM PE ")
                ):

                    item[column] = None

            item["Option Evidence"] = (
                "INCOMPLETE CHAIN"
            )

            item["Option Data Status"] = (
                "INCOMPLETE - REVIEW"
            )

        item["Option Direction Confirmed"] = False

        item["Live Confirmation Required"] = True

        output.append(item)

    return pd.DataFrame(output)


def build_quality_report(
    summary,
    detail,
    original_report
):

    clean, rejected = validate_contracts(
        detail
    )

    reconciled = reconcile(
        summary,
        clean
    )

    report = {

        "Version": VERSION,

        "Cash Report Date":
            original_report.get(
                "Cash Report Date"
            ),

        "Snapshot Type":
            "RETRIEVAL TIME ONLY",

        "Retrieved At":
            original_report.get(
                "Completed At",
                now().isoformat()
            ),

        "Universe":
            original_report.get(
                "Universe",
                len(summary)
            ),

        "Requested Stocks":
            len(summary),

        "Raw Contracts":
            len(detail),

        "Validated Contracts":
            len(clean),

        "Rejected Contracts":
            len(rejected),

        "Reconciled Stocks":
            int(
                reconciled[
                    "Reconciliation Passed"
                ].sum()
            ),

        "Validated Returned Chains":
            int(
                reconciled[
                    "Quality Status"
                ].eq(
                    "VALIDATED RETURNED CHAIN"
                ).sum()
            ),

        "Received Greeks %":
            percentage(
                clean["Greeks Received"]
            ),

        "Usable Greeks %":
            percentage(
                clean["Greeks Usable"]
            ),

        "Liquidity Passed":
            int(
                clean["Liquidity Screen"].sum()
            ),

        "Research Ready Contracts":
            int(
                clean["Research Ready"].sum()
            ),

        "Exchange Timestamp Verified":
            False,

        "Independent NSE Cross Check":
            False,

        "Option Direction Confirmed":
            False,

        "Trade Signal":
            "NOT GENERATED"

    }

    return (
        reconciled,
        clean,
        rejected,
        report
    )


def create_archive(
    summary,
    clean,
    rejected,
    report
):

    files = {

        "stock_summary.csv":
            csv_bytes(summary),

        "validated_contracts.csv":
            csv_bytes(clean),

        "rejected_contracts.csv":
            csv_bytes(rejected)

    }

    manifest = {

        **report,

        "Files": {

            name: {

                "SHA256": sha256(content),

                "Bytes": len(content)

            }

            for name, content
            in files.items()

        }

    }

    files["manifest.json"] = json.dumps(
        manifest,
        indent=2,
        default=str
    ).encode("utf-8")

    buffer = io.BytesIO()

    with zipfile.ZipFile(
        buffer,
        "w",
        compression=zipfile.ZIP_DEFLATED
    ) as archive:

        for name, content in files.items():

            archive.writestr(
                name,
                content
            )

    return buffer.getvalue()


def save_snapshot(
    summary,
    clean,
    rejected,
    report
):

    retrieved = pd.Timestamp(
        report["Retrieved At"]
    )

    if retrieved.tzinfo is None:

        raise ValueError(
            "Snapshot timezone missing"
        )

    retrieved = retrieved.tz_convert(
        "Asia/Kolkata"
    )

    day = retrieved.date().isoformat()

    stamp = retrieved.strftime(
        "%H%M%S%f"
    )

    content = create_archive(
        summary,
        clean,
        rejected,
        report
    )

    digest = sha256(content)

    folder = DATA / day

    path = folder / (
        stamp + "_" + digest[:12] + ".zip"
    )

    atomic_write(path, content)

    metadata = {

        **report,

        "Archive":
            path.name,

        "SHA256":
            digest,

        "Storage":
            "LOCAL FILESYSTEM"

    }

    atomic_write(
        folder / "latest.json",
        json.dumps(
            metadata,
            indent=2,
            default=str
        ).encode("utf-8")
    )

    return path


def collect(token, progress=None):

    summary, detail, report, errors = (
        scan_options(
            token,
            "Full F&O Universe",
            progress
        )
    )

    result = build_quality_report(
        summary,
        detail,
        report
    )

    return result, errors


def render_quality():

    st.header(
        "OPTION DATA QUALITY V" + VERSION
    )

    st.caption(
        "Contract Validation | "
        "Greeks Quality | "
        "Historical Research"
    )

    st.warning(
        "Historical retrieval snapshots "
        "are not verified exchange closing "
        "or live executable quotes."
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
            key="quality_v21_token"
        )

    if st.button(
        "GENERATE VALIDATED SNAPSHOT",
        type="primary"
    ):

        if not token:

            st.error("Token required")

        else:

            try:

                st.session_state.pop(
                    "quality_v21_result",
                    None
                )

                result, errors = collect(
                    token.strip(),
                    st.progress(0)
                )

                st.session_state[
                    "quality_v21_result"
                ] = (result, errors)

            except Exception as error:

                st.error(str(error))

    saved = st.session_state.get(
        "quality_v21_result"
    )

    if saved is None:

        return

    result, errors = saved

    summary, clean, rejected, report = result

    a, b, c = st.columns(3)

    a.metric(
        "Universe",
        report["Universe"]
    )

    b.metric(
        "Validated Contracts",
        report["Validated Contracts"]
    )

    c.metric(
        "Rejected Contracts",
        report["Rejected Contracts"]
    )

    a, b, c = st.columns(3)

    a.metric(
        "Reconciled Stocks",
        report["Reconciled Stocks"]
    )

    b.metric(
        "Usable Greeks",
        f'{report["Usable Greeks %"]}%'
    )

    c.metric(
        "Research Ready",
        report["Research Ready Contracts"]
    )

    tabs = st.tabs([

        "Stock Intelligence",

        "Validated Contracts",

        "Rejected Contracts",

        "Diagnostics"

    ])

    with tabs[0]:

        columns = [

            "Symbol",
            "Category",
            "Change %",
            "Volume Ratio",
            "Futures OI Change %",
            "Futures Buildup",
            "PCR OI",
            "PCR Volume",
            "Validated Contracts",
            "Usable Greeks %",
            "Qualified CE",
            "Qualified PE",
            "Quality Status"

        ]

        columns = [

            c for c in columns
            if c in summary.columns

        ]

        st.dataframe(
            summary[columns],
            hide_index=True,
            use_container_width=True
        )

    with tabs[1]:

        symbol = st.selectbox(
            "Select Stock",
            sorted(
                clean["Symbol"].unique()
            )
        )

        st.dataframe(
            clean[
                clean["Symbol"].eq(symbol)
            ],
            hide_index=True,
            use_container_width=True
        )

    with tabs[2]:

        if rejected.empty:

            st.success(
                "No rejected contracts"
            )

        else:

            st.dataframe(
                rejected,
                hide_index=True,
                use_container_width=True
            )

    with tabs[3]:

        st.json(report)

        if errors:

            st.dataframe(
                pd.DataFrame(errors)
            )

    st.download_button(

        "DOWNLOAD VALIDATED HISTORICAL ARCHIVE",

        create_archive(
            summary,
            clean,
            rejected,
            report
        ),

        file_name=(
            "option_snapshot_v21_"
            + now().strftime("%Y%m%d_%H%M%S")
            + ".zip"
        ),

        mime="application/zip"

    )

    st.caption(
        "Permanent repository storage "
        "is handled by GitHub Actions. "
        "Streamlit local storage is temporary."
    )


def main():

    token = os.getenv(
        "UPSTOX_ACCESS_TOKEN", ""
    ).strip()

    if not token:

        raise RuntimeError(
            "UPSTOX_ACCESS_TOKEN missing"
        )

    result, errors = collect(token)

    summary, clean, rejected, report = result

    report["API Errors"] = errors

    path = save_snapshot(
        summary,
        clean,
        rejected,
        report
    )

    print(
        json.dumps(
            report,
            indent=2,
            default=str
        )
    )

    print(
        "Snapshot saved:",
        path
    )

    if errors or (
        report["Validated Returned Chains"]
        != report["Universe"]
    ):

        raise SystemExit(2)


if __name__ == "__main__":

    main()
