
import streamlit as st
import pandas as pd

from datetime import datetime
from zoneinfo import ZoneInfo

from deal_data_engine import (
    fetch_report,
    parse_report,
    match_reports
)


st.set_page_config(
    page_title="Deal Data Engine V1",
    layout="wide"
)

st.title(
    "F&O Stock Identification | Deal Data V1"
)

day = st.date_input(
    "Requested trading date",
    datetime.now(
        ZoneInfo("Asia/Kolkata")
    ).date()
)

master = st.file_uploader(
    "Existing F&O universe CSV (Symbol column)",
    type="csv"
)


if st.button("Fetch three NSE reports"):

    st.session_state.reports = {}
    st.session_state.errors = {}

    for k in ["bulk", "block", "short"]:

        try:

            st.session_state.reports[k] = (
                fetch_report(k, day)
            )

        except Exception as e:

            st.session_state.errors[k] = str(e)


frames = []
audit = []


for k in ["bulk", "block", "short"]:

    u = st.file_uploader(
        f"{k} CSV fallback",
        type="csv",
        key=k
    )

    d = st.session_state.get(
        "reports", {}
    ).get(k)

    origin = "NSE automatic"

    error = st.session_state.get(
        "errors", {}
    ).get(k, "")

    if u:

        try:

            d = parse_report(
                u.getvalue(),
                k
            )

            origin = "Manual upload"

        except Exception as e:

            d = None
            error = str(e)

    if d is not None:

        actual = d["Report Date"].iloc[0]

        status = (
            "DATE MATCH"
            if actual == day
            else "DATE MISMATCH — historical only"
        )

        audit.append(dict(
            Source=k,
            Origin=origin,
            Status=status,
            Records=len(d),
            Actual=str(actual),
            Requested=str(day),
            Error=error
        ))

        st.dataframe(
            d,
            use_container_width=True
        )

        frames.append(d)

    else:

        audit.append(dict(
            Source=k,
            Status="UNAVAILABLE",
            Error=error
        ))


a = pd.DataFrame(audit)

st.dataframe(a)

st.download_button(
    "Download diagnostics",
    a.to_csv(index=False),
    "deal_diagnostics.csv"
)


if frames and master:

    try:

        universe = pd.read_csv(master)

        symbols = (
            universe["Symbol"]
            .dropna()
            .astype(str)
            .str.strip()
            .str.upper()
            .unique()
        )

        if not len(symbols):

            raise ValueError(
                "Empty universe"
            )

        out = match_reports(
            frames,
            symbols
        )

        st.subheader(
            "F&O matched disclosures — not trade signals"
        )

        st.dataframe(out)

        st.caption(
            "Cross-report matches are flagged, "
            "not summed. Dates remain separate."
        )

        st.download_button(
            "Download matched data",
            out.to_csv(index=False),
            "fo_deals.csv"
        )

    except Exception as e:

        st.error(str(e))
