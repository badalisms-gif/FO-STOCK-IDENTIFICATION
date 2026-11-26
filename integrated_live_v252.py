
"""
INTEGRATED LIVE STOCK IDENTIFICATION
Version 2.5.2

Historical Intraday Baseline
Full F&O Universe
Pre-Market + Historical + Live Evidence

Research only.
No automatic orders.
No technical entry indicators.
No strike selection.
"""

import math
import pandas as pd
import streamlit as st

import integrated_live_engine as core

from historical_live_bridge import (
    scan_market as historical_scan_market,
    load_archive
)


VERSION = "2.5.2"

MIN_BASELINE_COVERAGE = 90.0
MIN_BASELINE_DAYS = 5


# =====================================
# PRESERVE EXISTING ENGINE FUNCTIONS
# =====================================

original_evaluate_stock = core.evaluate_stock
original_show_result = core.show_result


# =====================================
# SAFE NUMERIC VALIDATION
# =====================================

def number(value):

    try:

        value = float(value)

        if math.isfinite(value):

            return value

    except (TypeError, ValueError):

        pass

    return None


# =====================================
# HISTORICAL BASELINE LIVE INTEGRATION
# =====================================

def scan_market(token):

    frame, summary, sectors = (
        historical_scan_market(token)
    )

    summary = dict(summary)

    summary["Integrated Version"] = VERSION

    if (
        frame.empty
        or summary.get("status") != "LIVE"
    ):

        return frame, summary, sectors

    frame = frame.copy()

    coverage = number(
        summary.get(
            "Historical Live Coverage %"
        )
    )

    baseline_available = (

        summary.get(
            "Archive Hash Verified"
        ) is True

        and summary.get(
            "Historical Baseline Status"
        ) == "AVAILABLE"

        and coverage is not None

        and coverage >= MIN_BASELINE_COVERAGE

    )

    summary["Historical Participation Enabled"] = (
        baseline_available
    )

    if not baseline_available:

        frame["Same Time RVOL"] = (
            float("nan")
        )

        frame["Same Time Turnover Ratio"] = (
            float("nan")
        )

        frame["Intraday Baseline Days"] = 0

    return frame, summary, sectors


# =====================================
# STOCK EVIDENCE VALIDATION
# =====================================

def evaluate_stock(
    live,
    history,
    morning,
    coverage,
    market_active
):

    live = dict(live)

    baseline_status = str(
        live.get(
            "Historical Baseline Status",
            "UNAVAILABLE"
        )
    )

    baseline_days = number(
        live.get(
            "Intraday Baseline Days"
        )
    )

    baseline_valid = (

        baseline_status == "AVAILABLE"

        and baseline_days is not None

        and baseline_days >= MIN_BASELINE_DAYS

    )

    if not baseline_valid:

        live["Same Time RVOL"] = None

        live["Same Time Turnover Ratio"] = None

        live["Intraday Baseline Days"] = 0

    result = original_evaluate_stock(

        live,

        history,

        morning,

        coverage,

        market_active

    )

    result.update({

        "Historical Baseline Status":
            baseline_status,

        "Historical Baseline Valid":
            baseline_valid,

        "Historical Expected Volume":
            number(
                live.get(
                    "Historical Expected Volume"
                )
            ),

        "Historical Expected Turnover Cr":
            number(
                live.get(
                    "Historical Expected Turnover Cr"
                )
            ),

        "Historical Baseline Method":
            live.get(
                "Historical Baseline Method",
                "UNAVAILABLE"
            ),

        "Historical Comparison Approximate":
            True,

        "Preopen Volume Reconciled":
            False,

        "Prediction Accuracy Verified":
            False

    })

    # Never classify a stock as priority
    # using missing historical evidence.

    if (
        not baseline_valid

        and result["Live Category"] in (

            "CONFIRMATION PENDING",

            "PRIORITY BULLISH",

            "PRIORITY BEARISH"

        )
    ):

        result["Live Category"] = (
            "VALIDATION FAILED - WAIT"
        )

        result["Rank"] = 7

        result["Confirmation Count"] = 0

        result["Direction"] = "UNCONFIRMED"

    return result


# =====================================
# HISTORICAL ARCHIVE STATUS PANEL
# =====================================

def show_archive_status():

    try:

        stocks, report = load_archive()

        stock_count = len(stocks)

        session_counts = [

            values["days"]

            for records in stocks.values()

            for values in records.values()

        ]

        minimum = (

            min(session_counts)

            if session_counts

            else 0

        )

        maximum = (

            max(session_counts)

            if session_counts

            else 0

        )

        if (
            stock_count > 0

            and minimum >= MIN_BASELINE_DAYS

            and report.get(
                "Archive Hash Verified"
            ) is True
        ):

            st.success(

                "Historical Intraday Archive "

                "Verified"

            )

        else:

            st.warning(

                "Historical archive has "

                "insufficient validated data"

            )

        a, b, c = st.columns(3)

        a.metric(

            "Historical Stocks",

            stock_count

        )

        b.metric(

            "Valid Sessions",

            f"{minimum}-{maximum}"

        )

        c.metric(

            "Archive Integrity",

            "VERIFIED"

            if report.get(
                "Archive Hash Verified"
            ) is True

            else "UNVERIFIED"

        )

        st.caption(

            "Archive Session Label: "

            + str(
                report.get(
                    "Archive Session Label",
                    ""
                )
            )

        )

        st.caption(

            "Historical volume and turnover "

            "comparisons are approximate. "

            "Independent NSE verification "

            "and preopen reconciliation "

            "remain pending."

        )

        with st.expander(
            "Historical Archive Diagnostics"
        ):

            st.json(report)

    except Exception as error:

        st.warning(

            "Historical archive unavailable: "

            + str(error)[:300]

        )


# =====================================
# EXTENDED LIVE DASHBOARD OUTPUT
# =====================================

def show_result(frame, report):

    live_summary = report.get(

        "Live Summary", {}

    )

    if not frame.empty:

        st.subheader(

            "Historical Live Integration"

        )

        coverage = number(

            live_summary.get(

                "Historical Live Coverage %"

            )

        )

        a, b, c = st.columns(3)

        a.metric(

            "Historical Baseline Coverage",

            f"{coverage:.2f}%"

            if coverage is not None

            else "N/A"

        )

        b.metric(

            "Baseline Ready",

            report.get(

                "Baseline Ready Stocks", 0

            )

        )

        c.metric(

            "Participation",

            "ENABLED"

            if live_summary.get(

                "Historical Participation Enabled"

            ) is True

            else "RESTRICTED"

        )

        if not live_summary.get(

            "Historical Participation Enabled"

        ):

            st.warning(

                "Historical participation "

                "confirmation is restricted. "

                "Priority classification "

                "requires sufficient baseline "

                "coverage."

            )

        st.caption(

            "Historical turnover is estimated. "

            "Intraminute comparison is "

            "approximate. These metrics refer "

            "to the last scan."

        )

    original_show_result(

        frame,

        report

    )


# =====================================
# V2.5.2 ENGINE CONNECTION
# =====================================

def render_integrated_live():

    # Preserve the existing V2.4.1
    # engine and reuse its functions.

    core.VERSION = VERSION

    core.scan_market = scan_market

    core.evaluate_stock = evaluate_stock

    core.show_result = show_result

    core.STATE_KEY = (
        "integrated_v252_confirmation"
    )

    core.RESULT_KEY = (
        "integrated_v252_result"
    )

    core.JOURNAL_KEY = (
        "integrated_v252_journal"
    )

    st.subheader(

        "Historical Intraday Intelligence"

    )

    show_archive_status()

    core.render_integrated_live()


if __name__ == "__main__":

    st.set_page_config(

        page_title="Integrated Live V2.5.2",

        layout="wide"

    )

    render_integrated_live()
