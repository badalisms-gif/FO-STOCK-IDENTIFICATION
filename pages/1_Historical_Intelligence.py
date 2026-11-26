
import pandas as pd
import streamlit as st

from historical_engine import (

    VERSION,

    SOURCES,

    SOURCE_NAMES,

    load_history,

    coverage_table,

    stock_summary,

    client_summary,

    short_trend,

    refresh_history

)


st.set_page_config(

    page_title="Historical Intelligence",

    page_icon="📊",

    layout="wide"

)


st.title(

    f"Historical Intelligence V{VERSION}"

)

st.caption(

    "F&O Stock Identification | "
    "Historical NSE Disclosures"

)

st.info(

    "Stock identification only. "
    "No technical indicators, "
    "option selection or trade signals."

)


# =====================================
# CONTROLS
# =====================================

period = st.selectbox(

    "Historical Analysis Period",

    [7, 30, 90, 365],

    index=1,

    format_func=lambda x: f"Last {x} days"

)

if st.button(

    "Refresh Historical Data",

    type="primary",

    use_container_width=True

):

    refresh_history()


# =====================================
# LOAD DATA
# =====================================

try:

    with st.spinner(

        "Reading and validating "
        "stored historical reports..."

    ):

        history = load_history(period)

except Exception as error:

    st.error(

        f"Historical data unavailable: {error}"

    )

    st.stop()


data = history["data"]

all_data = history["all_data"]

diagnostics = history["diagnostics"]

universe = history["universe"]

latest = history["latest_collection"]


# =====================================
# COLLECTION STATUS
# =====================================

st.subheader("Latest Collection Status")

if latest.get("error"):

    st.warning(latest["error"])

else:

    st.write(

        "Last Collection:",

        latest.get("finished", "Unknown")

    )

    st.write(

        "Successful Sources:",

        latest.get(

            "successful_sources",

            []

        )

    )

    missing = latest.get(

        "missing_sources",

        []

    )

    if missing:

        st.warning(

            f"Missing sources: {missing}"

        )

    else:

        st.success(

            "Latest collector reports "
            "all required sources available."

        )

    with st.expander(

        "Collection Diagnostics"

    ):

        st.json(latest)


# =====================================
# COVERAGE METRICS
# =====================================

st.subheader("Historical Data Coverage")

verified = (

    diagnostics[

        diagnostics.Status == "VERIFIED"

    ]

    if not diagnostics.empty

    else pd.DataFrame()

)

available_days = (

    verified.Date.nunique()

    if not verified.empty

    else 0

)

unique_stocks = (

    data.Symbol.nunique()

    if not data.empty

    else 0

)

c1, c2, c3 = st.columns(3)

c1.metric(

    "F&O Universe",

    len(universe)

)

c2.metric(

    "Available Report Days",

    available_days

)

c3.metric(

    "F&O Stocks With Disclosures",

    unique_stocks

)

c4, c5, c6 = st.columns(3)

c4.metric(

    "Verified Reports",

    len(verified)

)

c5.metric(

    "All Disclosure Records",

    len(all_data)

)

c6.metric(

    "F&O Disclosure Records",

    len(data)

)

st.caption(

    f"Requested period: "
    f"{history['start']} to "
    f"{history['end']}"

)

st.caption(

    f"F&O universe snapshot: "
    f"{history['universe_date']}"

)

st.caption(

    f"Repository revision: "
    f"{history['commit'][:10]}"

)

st.warning(

    "Analysis uses the latest stored F&O "
    "universe, not historical membership "
    "on each report date. Missing reports "
    "are not treated as zero activity."

)

if available_days < period:

    st.info(

        f"Reports are available for "
        f"{available_days} calendar dates "
        f"within the requested {period}-day "
        f"period. Weekends and exchange "
        f"holidays may have no reports."

    )


# =====================================
# SOURCE COVERAGE
# =====================================

rows = []

for kind in SOURCES:

    source_reports = (

        verified[

            verified.Source == kind

        ]

        if not verified.empty

        else pd.DataFrame()

    )

    matched = (

        data[data.Source == kind]

        if not data.empty

        else pd.DataFrame()

    )

    rows.append({

        "Source": SOURCE_NAMES[kind],

        "Verified Reports":

            len(source_reports),

        "Latest Report":

            source_reports.Date.max()

            if not source_reports.empty

            else None,

        "F&O Records":

            len(matched)

            if not source_reports.empty

            else None,

        "Unique F&O Stocks":

            matched.Symbol.nunique()

            if not matched.empty

            else (

                0

                if not source_reports.empty

                else None

            )

    })

st.dataframe(

    pd.DataFrame(rows),

    use_container_width=True,

    hide_index=True

)

with st.expander(

    "Date-wise Report Coverage"

):

    coverage = coverage_table(history)

    st.dataframe(

        coverage,

        use_container_width=True,

        hide_index=True

    )

    st.caption(

        "NOT STORED does not establish "
        "whether the exchange published "
        "a report. Holidays are not "
        "independently classified."

    )

    st.download_button(

        "Download Coverage CSV",

        coverage.to_csv(index=False),

        "historical_coverage.csv",

        "text/csv"

    )

with st.expander(

    "Report Validation Diagnostics"

):

    st.dataframe(

        diagnostics,

        use_container_width=True

    )


if data.empty:

    st.warning(

        "No verified F&O disclosures "
        "available for this period."

    )

    st.stop()


# =====================================
# STOCK ACTIVITY
# =====================================

st.subheader(

    "F&O Stock Disclosure Activity"

)

st.caption(

    "Ranked by frequency of reported "
    "activity, not trading opportunity "
    "or bullish/bearish probability."

)

summary = stock_summary(data)

sort_by = st.selectbox(

    "Sort Stocks By",

    [

        "Disclosure Days",

        "Bulk Days",

        "Block Days",

        "Short Selling Days",

        "Short Quantity",

        "Bulk BUY Cr",

        "Bulk SELL Cr",

        "Block BUY Cr",

        "Block SELL Cr"

    ]

)

summary = summary.sort_values(

    sort_by,

    ascending=False,

    na_position="last"

)

st.dataframe(

    summary,

    use_container_width=True,

    hide_index=True

)

st.download_button(

    "Download Historical Stock Summary",

    summary.to_csv(index=False),

    "historical_stock_summary.csv",

    "text/csv"

)


# =====================================
# REPEATED DISCLOSURES
# =====================================

st.subheader(

    "Repeated Bulk and Block Activity"

)

repeated = summary[

    (summary["Bulk Days"] >= 2)

    |

    (summary["Block Days"] >= 2)

]

if repeated.empty:

    st.info(

        "No repeated activity across "
        "two or more available report "
        "dates yet."

    )

else:

    st.dataframe(

        repeated,

        use_container_width=True,

        hide_index=True

    )


# =====================================
# CLIENT ANALYSIS
# =====================================

st.subheader("Client-wise Deal Activity")

clients = client_summary(data)

if clients.empty:

    st.info(

        "No client-level deals available."

    )

else:

    source_filter = st.selectbox(

        "Deal Type",

        ["All", "Bulk Deals", "Block Deals"]

    )

    if source_filter == "Bulk Deals":

        clients = clients[

            clients.Source == "bulk"

        ]

    elif source_filter == "Block Deals":

        clients = clients[

            clients.Source == "block"

        ]

    search = st.text_input(

        "Search Client or Stock"

    )

    if search.strip():

        mask = (

            clients.Client.str.contains(

                search,

                case=False,

                regex=False,

                na=False

            )

            |

            clients.Symbol.str.contains(

                search,

                case=False,

                regex=False,

                na=False

            )

        )

        clients = clients[mask]

    st.dataframe(

        clients,

        use_container_width=True,

        hide_index=True

    )

    st.download_button(

        "Download Client Activity",

        clients.to_csv(index=False),

        "historical_client_activity.csv",

        "text/csv"

    )


# =====================================
# INDIVIDUAL STOCK ANALYSIS
# =====================================

st.subheader("Individual Stock History")

symbol = st.selectbox(

    "Select F&O Stock",

    universe

)

stock = data[

    data.Symbol == symbol

].copy()

if stock.empty:

    st.info(

        f"No stored disclosure activity "
        f"found for {symbol}."

    )

else:

    stock = stock.sort_values(

        "Report Date",

        ascending=False

    )

    st.dataframe(

        stock,

        use_container_width=True,

        hide_index=True

    )

    st.subheader(

        f"{symbol} — Short Selling History"

    )

    trend = short_trend(

        data,

        symbol

    )

    if trend.empty:

        st.info(

            "No short selling disclosures "
            "available for this stock."

        )

    else:

        trend["Report Date"] = (

            trend["Report Date"]

            .astype(str)

        )

        st.bar_chart(

            trend.set_index(

                "Report Date"

            )["Quantity"]

        )

        st.dataframe(

            trend,

            use_container_width=True,

            hide_index=True

        )

    st.download_button(

        "Download Stock History",

        stock.to_csv(index=False),

        f"{symbol}_historical.csv",

        "text/csv"

    )


# =====================================
# COMPLETE EXPORT
# =====================================

st.divider()

st.download_button(

    "Download All Historical F&O Data",

    data.to_csv(index=False),

    "all_historical_fo_data.csv",

    "text/csv",

    use_container_width=True

)

st.caption(

    "Bulk and Block deal values remain "
    "separate. Reported buy and sell "
    "values are not unique market "
    "turnover or institutional holdings. "
    "Short selling disclosures do not "
    "represent outstanding short interest. "
    "Disclosure activity alone does not "
    "establish future stock direction."

)

st.caption(

    "V1.4 | Historical Intelligence | "
    "No automatic orders"

)
