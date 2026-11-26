
import json
import pandas as pd
import streamlit as st

from opportunity_performance_engine import (
    OUTPUT,
    load_performance
)

st.set_page_config(
    page_title="Opportunity Performance V2.9",
    layout="wide"
)

st.header(
    "OPPORTUNITY PERFORMANCE V2.9"
)

st.caption(
    "Permanent Archive | Directional Performance "
    "| 5m | 15m | 30m | 60m"
)

st.warning(
    "Historical live-only replay. "
    "Not actual V2.8 signals or trading returns."
)

if st.button(
    "REFRESH PERFORMANCE ARCHIVES"
):

    st.rerun()

dates = []

if OUTPUT.exists():

    dates = sorted(

        [

            path.name

            for path in OUTPUT.iterdir()

            if path.is_dir()

            and (
                path / "metadata.json"
            ).exists()

        ],

        reverse=True

    )

if not dates:

    st.info(
        "Performance archive not available yet. "
        "Wait for permanent live collection "
        "and the daily performance workflow."
    )

    st.stop()

selected = st.selectbox(
    "Select Trading Session",
    dates
)

try:

    frame, report = load_performance(
        selected
    )

except Exception as error:

    st.error(str(error))

    st.stop()

st.success(
    "Performance Archive Verified"
)

a, b, c = st.columns(3)

a.metric(

    "Opportunities",

    report.get(
        "Identified Opportunities", 0
    )

)

b.metric(

    "Eligible Scans",

    report.get(
        "Eligible Identification Scans", 0
    )

)

c.metric(

    "Stocks Observed",

    report.get(
        "Stocks With Valid Quotes", 0
    )

)

if frame.empty:

    st.info(
        "No qualifying opportunities "
        "during eligible archived scans."
    )

    st.json(report)

    st.stop()

summary = pd.DataFrame(

    report.get(
        "Performance Summary", []
    )

)

tabs = st.tabs([

    "Performance Summary",

    "Priority Bullish",

    "Priority Bearish",

    "Emerging",

    "Early Activity",

    "All Opportunities",

    "Diagnostics"

])

with tabs[0]:

    st.dataframe(

        summary,

        hide_index=True,

        use_container_width=True

    )

    if not summary.empty:

        horizon = st.selectbox(

            "Performance Horizon",

            [
                "5m", "15m", "30m",
                "60m", "Near Close"
            ],

            index=2

        )

        chart = summary[

            summary["Horizon"].eq(
                horizon
            )

        ].dropna(

            subset=[
                "Directional Accuracy %"
            ]

        )

        if not chart.empty:

            st.bar_chart(

                chart.set_index(
                    "Category"
                )["Directional Accuracy %"]

            )

    st.caption(

        "Accuracy = correct outcomes divided "
        "by all measured outcomes, including flat. "
        "Missing data is excluded."

    )

categories = [

    "PRIORITY BULLISH",

    "PRIORITY BEARISH",

    "EMERGING WATCH",

    "EARLY ACTIVITY WATCH"

]

for index, category in enumerate(
    categories, start=1
):

    with tabs[index]:

        selected_frame = frame[

            frame["Category"].eq(
                category
            )

        ]

        if selected_frame.empty:

            st.info(
                "No qualifying opportunities"
            )

        else:

            st.dataframe(

                selected_frame,

                hide_index=True,

                use_container_width=True

            )

with tabs[5]:

    st.dataframe(

        frame,

        hide_index=True,

        use_container_width=True

    )

with tabs[6]:

    st.json(report)

st.download_button(

    "DOWNLOAD PERFORMANCE CSV",

    frame.to_csv(index=False),

    "opportunity_performance_"
    + selected + ".csv",

    mime="text/csv"

)

st.download_button(

    "DOWNLOAD PERFORMANCE SUMMARY",

    summary.to_csv(index=False),

    "performance_summary_"
    + selected + ".csv",

    mime="text/csv"

)

st.download_button(

    "DOWNLOAD DIAGNOSTICS",

    json.dumps(
        report,
        indent=2,
        default=str
    ),

    "performance_diagnostics_"
    + selected + ".json",

    mime="application/json"

)

st.caption(

    "Near-close price is not official NSE close. "
    "MFE and MAE are based on observed snapshots. "
    "Incomplete price paths may miss extremes. "
    "Returns exclude costs and option premium effects."

)
