
import json
import streamlit as st

from datetime import timedelta

from automatic_premarket_engine import (
    load_latest,
    now
)


st.set_page_config(
    page_title="Automatic Pre-Market V2.7",
    layout="wide"
)

st.header(
    "AUTOMATIC PRE-MARKET V2.7"
)

st.caption(
    "Overnight News | Announcements | "
    "Historical Evidence | Preopen Activity"
)

st.warning(
    "Preliminary stock research only. "
    "Live confirmation required."
)

selected_date = st.date_input(

    "Select Archive Date",

    value=now().date(),

    max_value=now().date(),

    min_value=(
        now().date() - timedelta(days=365)
    )

)

if st.button(
    "REFRESH AUTOMATIC ARCHIVE"
):

    st.rerun()

try:

    frame, news, announcements, report = (
        load_latest(selected_date)
    )

except Exception as error:

    st.info(
        str(error)
    )

    st.caption(
        "Automatic archives are generated "
        "by GitHub Actions."
    )

    st.stop()

st.success(
    "Archive SHA256 Verified"
)

st.caption(

    "Generated: "

    + str(
        report.get(
            "Automatic Completed At", ""
        )
    )

)

st.info(

    "Collection Mode: "

    + str(
        report.get(
            "Collection Mode", ""
        )
    )

)

a, b, c = st.columns(3)

a.metric(
    "Stocks",
    len(frame)
)

b.metric(
    "Preopen Coverage",
    str(
        report.get(
            "Preopen Coverage %", 0
        )
    ) + "%"
)

c.metric(
    "Fresh Catalysts",
    report.get(
        "Fresh Catalyst Stocks", 0
    )
)

verification = report.get(
    "Historical Verification", {}
)

a, b, c = st.columns(3)

a.metric(
    "Historical Coverage",
    str(
        verification.get(
            "Historical Coverage %", "N/A"
        )
    ) + "%"
)

b.metric(
    "Early Activity",
    report.get(
        "Early Activity Stocks", 0
    )
)

c.metric(
    "Extended Stocks",
    report.get(
        "Extended Stocks", 0
    )
)

if not verification.get(
    "Archive Integrity Verified"
):

    st.warning(
        "Historical source verification "
        "incomplete."
    )

st.caption(
    "Archived preopen evidence is not "
    "a current live quote."
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

    "Preopen Gap %",

    "Preopen Timestamp",

    "Buy Sell Ratio",

    "Sell Buy Ratio",

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

    "Early Activity",

    "Fresh Catalysts",

    "Direction Watch",

    "Extended / Conflict",

    "All Stocks",

    "News",

    "Announcements",

    "Diagnostics"

])

filters = [

    ["EARLY ACTIVITY WATCH"],

    ["FRESH CATALYST WATCH"],

    [

        "PREVIOUS DIRECTION WATCH",

        "PREOPEN DIRECTION WATCH"

    ],

    [

        "EXTENDED MOVEMENT - WAIT",

        "EXTENDED GAP - WAIT",

        "CONFLICT - WAIT"

    ]

]

for index, values in enumerate(filters):

    with tabs[index]:

        selected = frame[

            frame[
                "Pre-Market Category"
            ].isin(values)

        ]

        st.dataframe(

            selected[columns],

            hide_index=True,

            use_container_width=True

        )

with tabs[4]:

    st.dataframe(

        frame[columns],

        hide_index=True,

        use_container_width=True

    )

with tabs[5]:

    st.dataframe(

        news,

        hide_index=True,

        use_container_width=True

    )

with tabs[6]:

    st.dataframe(

        announcements,

        hide_index=True,

        use_container_width=True

    )

with tabs[7]:

    st.json(report)

st.download_button(

    "DOWNLOAD AUTOMATIC WATCHLIST",

    frame.to_csv(index=False),

    "automatic_premarket_v27.csv",

    mime="text/csv"

)

st.download_button(

    "DOWNLOAD DIAGNOSTICS",

    json.dumps(
        report,
        indent=2,
        default=str
    ),

    "automatic_premarket_diagnostics.json",

    mime="application/json"

)
