
"""
F&O STOCK OPPORTUNITY IDENTIFICATION
Version 1.8.0

Research Only
No Automatic Orders
No Technical Indicators
"""

import math
import pandas as pd
import streamlit as st

from live_market_engine import (
    scan_market,
    now
)

VERSION = "1.8.0"

MIN_COVERAGE = 0.90

MIN_RVOL = 1.50
MIN_TURNOVER_RATIO = 1.30
MIN_MOVE_5M = 0.30
MIN_RELATIVE_STRENGTH = 0.40
MIN_OI_CHANGE = 0.50
MAX_SPREAD = 0.20


def number(value):

    try:

        value = float(value)

        if math.isfinite(value):
            return value

    except (ValueError, TypeError):
        pass

    return None


def identify_opportunities(frame, summary):

    if frame.empty:
        return pd.DataFrame()

    if summary.get("status") != "LIVE":
        return pd.DataFrame()

    universe = summary.get("universe_size", 0)

    if not universe:
        return pd.DataFrame()

    coverage = (
        summary.get("fresh_stocks", 0)
        / universe
    )

    results = []

    for _, stock in frame.iterrows():

        symbol = stock.get("Symbol", "")

        record = {
            "Symbol": symbol,
            "LTP": stock.get("LTP"),
            "Change %": stock.get("Change %"),
            "Direction": "UNCONFIRMED",
            "Opportunity": "NO OPPORTUNITY",
            "Evidence Groups": 0,
            "Reasons": "",
            "Missing Evidence": "",
            "Market Coverage %": round(
                coverage * 100, 2
            ),
            "Checked At": now().isoformat()
        }

        if stock.get("Status") != "FRESH":

            record["Opportunity"] = (
                "DATA UNAVAILABLE"
            )

            results.append(record)
            continue

        move1 = number(
            stock.get("Move 1m %")
        )

        move3 = number(
            stock.get("Move 3m %")
        )

        move5 = number(
            stock.get("Move 5m %")
        )

        strength = number(
            stock.get("Relative Strength %")
        )

        rvol = number(
            stock.get("Same Time RVOL")
        )

        turnover = number(
            stock.get("Same Time Turnover Ratio")
        )

        oi = number(
            stock.get("Futures OI 5m Change %")
        )

        spread = number(
            stock.get("Spread %")
        )

        days = number(
            stock.get("Intraday Baseline Days")
        )

        reasons = []
        missing = []

        reaction = False
        participation = False
        derivatives = False
        liquidity = False

        # PRICE REACTION

        if (
            move3 is None
            or move5 is None
            or strength is None
        ):

            missing.append("Price history")

        else:

            bullish = (
                move5 >= MIN_MOVE_5M
                and move3 > 0
                and strength >= MIN_RELATIVE_STRENGTH
            )

            bearish = (
                move5 <= -MIN_MOVE_5M
                and move3 < 0
                and strength <= -MIN_RELATIVE_STRENGTH
            )

            if bullish:

                reaction = True

                record["Direction"] = "BULLISH"

                reasons.append(
                    "Positive price acceleration"
                )

            elif bearish:

                reaction = True

                record["Direction"] = "BEARISH"

                reasons.append(
                    "Negative price acceleration"
                )

        # MARKET PARTICIPATION

        if (
            days is None
            or days < 5
            or rvol is None
            or turnover is None
        ):

            missing.append(
                "Same-time volume baseline"
            )

        elif (
            rvol >= MIN_RVOL
            and turnover >= MIN_TURNOVER_RATIO
        ):

            participation = True

            reasons.append(
                "Abnormal volume and turnover"
            )

        # FUTURES PARTICIPATION

        future_fresh = (
            str(stock.get("Futures Fresh", False))
            .lower() == "true"
        )

        if not future_fresh or oi is None:

            missing.append(
                "Comparable futures OI"
            )

        elif abs(oi) >= MIN_OI_CHANGE:

            derivatives = True

            if oi > 0:

                reasons.append(
                    "Futures OI expansion"
                )

            else:

                reasons.append(
                    "Futures OI contraction"
                )

        # LIQUIDITY

        if spread is None:

            missing.append("Bid-ask spread")

        elif 0 <= spread <= MAX_SPREAD:

            liquidity = True

        else:

            reasons.append(
                "Wide bid-ask spread"
            )

        # SHORT-TERM CONFLICT

        conflict = False

        if move1 is not None:

            if (
                record["Direction"] == "BULLISH"
                and move1 < -0.20
            ):

                conflict = True

            if (
                record["Direction"] == "BEARISH"
                and move1 > 0.20
            ):

                conflict = True

        # OPPORTUNITY CLASSIFICATION

        groups = sum([
            reaction,
            participation,
            derivatives
        ])

        record["Evidence Groups"] = groups

        if coverage < MIN_COVERAGE:

            record["Opportunity"] = (
                "LOW MARKET COVERAGE"
            )

        elif conflict:

            record["Opportunity"] = (
                "CONFLICT - WAIT"
            )

        elif (
            reaction
            and participation
            and derivatives
            and liquidity
        ):

            record["Opportunity"] = (
                "PRIORITY WATCH"
            )

        elif (
            reaction
            and liquidity
            and (participation or derivatives)
        ):

            record["Opportunity"] = (
                "EMERGING WATCH"
            )

        elif reaction:

            record["Opportunity"] = (
                "OBSERVATION ONLY"
            )

        elif missing:

            record["Opportunity"] = (
                "INSUFFICIENT EVIDENCE"
            )

        record["Reasons"] = " | ".join(reasons)

        record["Missing Evidence"] = (
            " | ".join(missing)
        )

        record["Move 5m %"] = move5
        record["Relative Strength %"] = strength
        record["Same Time RVOL"] = rvol
        record["Turnover Ratio"] = turnover
        record["Futures OI 5m %"] = oi
        record["Spread %"] = spread

        results.append(record)

    output = pd.DataFrame(results)

    priority = {
        "PRIORITY WATCH": 1,
        "EMERGING WATCH": 2,
        "OBSERVATION ONLY": 3,
        "CONFLICT - WAIT": 4,
        "NO OPPORTUNITY": 5,
        "INSUFFICIENT EVIDENCE": 6,
        "LOW MARKET COVERAGE": 7,
        "DATA UNAVAILABLE": 8
    }

    output["_rank"] = (
        output["Opportunity"]
        .map(priority)
        .fillna(99)
    )

    return output.sort_values(
        ["_rank", "Evidence Groups", "Symbol"],
        ascending=[True, False, True]
    ).drop(columns="_rank")


def render_opportunities(token=""):

    import os

    st.header(
        "F&O STOCK OPPORTUNITY V" + VERSION
    )

    st.caption(
        "Full Universe | Research Only | "
        "No Automatic Orders"
    )

    if not token:

        token = os.environ.get(
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
            key="v18_token"
        )

    if not token:

        st.info("Upstox token required.")
        return

    running = st.toggle(
        "Start Opportunity Identification",
        value=False,
        key="v18_running"
    )

    @st.fragment(
        run_every="30s" if running else None
    )
    def panel():

        if not running:

            st.info("Monitoring stopped.")
            return

        try:

            frame, summary, sectors = (
                scan_market(token)
            )

            if summary.get("status") != "LIVE":

                st.warning(
                    summary.get("status")
                )

                return

            result = identify_opportunities(
                frame,
                summary
            )

        except Exception as error:

            st.error(
                str(error).replace(
                    token, "[REDACTED]"
                )
            )

            return

        if result.empty:

            st.info("No eligible data.")
            return

        priority = result[
            result["Opportunity"]
            .eq("PRIORITY WATCH")
        ]

        emerging = result[
            result["Opportunity"]
            .eq("EMERGING WATCH")
        ]

        watch = pd.concat(
            [priority, emerging],
            ignore_index=True
        )

        bullish = watch[
            watch["Direction"].eq("BULLISH")
        ]

        bearish = watch[
            watch["Direction"].eq("BEARISH")
        ]

        a, b, c, d = st.columns(4)

        a.metric(
            "Universe",
            summary["universe_size"]
        )

        b.metric(
            "Fresh Stocks",
            summary["fresh_stocks"]
        )

        c.metric(
            "Bullish Watch",
            len(bullish)
        )

        d.metric(
            "Bearish Watch",
            len(bearish)
        )

        st.caption(
            "Last Scan: "
            + summary.get("checked_at", "")
        )

        tab1, tab2, tab3, tab4 = st.tabs([
            "Bullish Watch",
            "Bearish Watch",
            "All Stocks",
            "Sector Strength"
        ])

        with tab1:

            if bullish.empty:

                st.info(
                    "No qualified bullish opportunity."
                )

            else:

                st.dataframe(
                    bullish,
                    hide_index=True,
                    use_container_width=True
                )

        with tab2:

            if bearish.empty:

                st.info(
                    "No qualified bearish opportunity."
                )

            else:

                st.dataframe(
                    bearish,
                    hide_index=True,
                    use_container_width=True
                )

        with tab3:

            st.dataframe(
                result,
                hide_index=True,
                use_container_width=True
            )

        with tab4:

            st.dataframe(
                sectors,
                hide_index=True,
                use_container_width=True
            )

        st.download_button(
            "Download Opportunity Report",
            result.to_csv(index=False),
            "stock_opportunity_v18.csv",
            mime="text/csv",
            key="v18_download"
        )

        with st.expander("Data Diagnostics"):

            st.json(summary)

        st.info(
            "Watchlist only. Missing evidence "
            "is never treated as zero. "
            "No entry or option contract signal."
        )

    panel()
