
"""
FINAL F&O STOCK IDENTIFICATION
Version 2.8.0

Automatic Historical + Pre-Market + Live Intelligence.
Reuses validated existing engines.
No automatic orders.
No strike selection.
No technical entry indicators.
"""

import os
import io
import json
import hashlib
import importlib.util

from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd
import streamlit as st

import integrated_live_engine as original_core
import automatic_premarket_engine as morning

from historical_live_bridge import (
    scan_market as historical_scan_market,
    load_archive
)

VERSION = "2.8.0"
IST = ZoneInfo("Asia/Kolkata")

MIN_BASELINE_COVERAGE = 90.0
MIN_BASELINE_DAYS = 5

STATE_KEY = "final_v280_confirmation"
RESULT_KEY = "final_v280_result"
JOURNAL_KEY = "final_v280_journal"


def now():
    return datetime.now(IST)


def safe_error(error, token=""):

    message = str(error)

    if token:
        message = message.replace(
            token, "[REDACTED]"
        )

    return message[:500]


# =====================================
# ISOLATED EXISTING ENGINE
# =====================================

def create_core():

    path = Path(
        original_core.__file__
    )

    spec = importlib.util.spec_from_file_location(
        "_final_v280_core",
        path
    )

    if spec is None or spec.loader is None:
        raise RuntimeError(
            "Existing core engine unavailable"
        )

    module = importlib.util.module_from_spec(
        spec
    )

    spec.loader.exec_module(module)

    module.VERSION = VERSION
    module.STATE_KEY = STATE_KEY
    module.RESULT_KEY = RESULT_KEY
    module.JOURNAL_KEY = JOURNAL_KEY

    return module


core = create_core()

original_evaluate = core.evaluate_stock
original_show = core.show_result


# =====================================
# HISTORICAL LIVE BASELINE
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

    coverage = core.number(
        summary.get(
            "Historical Live Coverage %"
        )
    )

    enabled = (
        summary.get(
            "Archive Hash Verified"
        ) is True
        and summary.get(
            "Historical Baseline Status"
        ) == "AVAILABLE"
        and coverage is not None
        and coverage >= MIN_BASELINE_COVERAGE
    )

    summary[
        "Historical Participation Enabled"
    ] = enabled

    if not enabled:

        frame["Same Time RVOL"] = float("nan")

        frame[
            "Same Time Turnover Ratio"
        ] = float("nan")

        frame["Intraday Baseline Days"] = 0

    return frame, summary, sectors


def evaluate_stock(
    live,
    history,
    morning_context,
    coverage,
    market_active
):

    live = dict(live)

    days = core.number(
        live.get(
            "Intraday Baseline Days"
        )
    )

    baseline_valid = (
        live.get(
            "Historical Baseline Status"
        ) == "AVAILABLE"
        and days is not None
        and days >= MIN_BASELINE_DAYS
    )

    if not baseline_valid:

        live["Same Time RVOL"] = None

        live[
            "Same Time Turnover Ratio"
        ] = None

        live["Intraday Baseline Days"] = 0

    result = original_evaluate(
        live,
        history,
        morning_context,
        coverage,
        market_active
    )

    result.update({

        "Historical Baseline Valid":
            baseline_valid,

        "Historical Comparison Approximate":
            True,

        "Preopen Volume Reconciled":
            False,

        "Automatic Morning Context":
            bool(morning_context),

        "Morning Context Is Live Confirmation":
            False,

        "Prediction Accuracy Verified":
            False

    })

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

        result["Direction"] = "UNCONFIRMED"

        result["Rank"] = 7

        result["Confirmation Count"] = 0

    return result


core.scan_market = scan_market
core.evaluate_stock = evaluate_stock


# =====================================
# AUTOMATIC MORNING CONTEXT
# =====================================

def automatic_context():

    try:

        _, history = core.historical_context()

        historical_date = history[
            "Report Date"
        ]

        raw = morning.load_live_context(
            historical_date
        )

        frame = pd.read_csv(
            io.BytesIO(raw)
        )

        if not frame[
            "Collection Date"
        ].astype(str).eq(
            now().date().isoformat()
        ).all():

            raise ValueError(
                "Morning archive date mismatch"
            )

        return raw, {

            "Status": "AVAILABLE",

            "Stocks": len(frame),

            "Historical Date":
                historical_date,

            "Collection Date":
                now().date().isoformat(),

            "SHA256":
                hashlib.sha256(raw).hexdigest(),

            "Used As Live Confirmation":
                False

        }

    except Exception as error:

        return None, {

            "Status": "UNAVAILABLE",

            "Reason": safe_error(error),

            "Used As Live Confirmation":
                False

        }


# =====================================
# HISTORICAL ARCHIVE STATUS
# =====================================

def archive_status():

    try:

        stocks, report = load_archive()

        days = [

            item["days"]

            for records in stocks.values()

            for item in records.values()

        ]

        return {

            "Status": (

                "VERIFIED"

                if report.get(
                    "Archive Hash Verified"
                ) is True

                else "UNVERIFIED"

            ),

            "Stocks": len(stocks),

            "Minimum Sessions":
                min(days) if days else 0,

            "Maximum Sessions":
                max(days) if days else 0,

            "Archive Session Label":
                report.get(
                    "Archive Session Label"
                ),

            "Independent NSE Cross Check":
                False

        }

    except Exception as error:

        return {

            "Status": "UNAVAILABLE",

            "Stocks": 0,

            "Reason": safe_error(error)

        }


# =====================================
# MASTER IDENTIFICATION SCAN
# =====================================

def integrated_scan(token, previous_state):

    morning_raw, morning_report = (
        automatic_context()
    )

    frame, report, state = (
        core.integrated_scan(
            token,
            morning_raw,
            previous_state
        )
    )

    report["Version"] = VERSION

    report["Automatic Pre-Market"] = (
        morning_report
    )

    report["Historical Intraday Archive"] = (
        archive_status()
    )

    report["Permanent Live Collector"] = (
        "SEPARATE V2.6 WORKFLOW"
    )

    report["Permanent Live Storage Verified"] = (
        False
    )

    report["Prediction Accuracy Verified"] = (
        False
    )

    report["Trade Signal"] = "NOT GENERATED"

    return frame, report, state


# =====================================
# HISTORICAL CONTEXT PANEL
# =====================================

def show_historical():

    st.subheader(
        "Previous Session Intelligence"
    )

    try:

        frame, report = (
            core.historical_context()
        )

        a, b = st.columns(2)

        a.metric(
            "Historical Stocks",
            len(frame)
        )

        b.metric(
            "Historical Date",
            report["Report Date"]
        )

        columns = [

            "Symbol",
            "Category",
            "Close",
            "Change %",
            "Volume Ratio",
            "Turnover Ratio",
            "Delivery Quantity Ratio",
            "Futures OI Change %",
            "Historical Opportunity"

        ]

        columns = [

            col for col in columns

            if col in frame.columns

        ]

        st.dataframe(
            frame[columns],
            hide_index=True,
            use_container_width=True
        )

        with st.expander(
            "Historical Diagnostics"
        ):

            st.json(report)

    except Exception as error:

        st.warning(
            safe_error(error)
        )


# =====================================
# AUTOMATIC PRE-MARKET PANEL
# =====================================

def show_morning():

    st.subheader(
        "Automatic Pre-Market Intelligence"
    )

    try:

        frame, news, announcements, report = (
            morning.load_latest()
        )

        _, context_report = (
            automatic_context()
        )

        if context_report["Status"] != "AVAILABLE":

            st.warning(
                "Archive available, but not "
                "eligible for automatic live context."
            )

        else:

            st.success(
                "Verified morning archive loaded"
            )

        a, b, c = st.columns(3)

        a.metric(
            "Stocks",
            len(frame)
        )

        b.metric(
            "News Records",
            len(news)
        )

        c.metric(
            "Announcements",
            len(announcements)
        )

        st.caption(

            "Collection Mode: "

            + str(
                report.get(
                    "Collection Mode", ""
                )
            )

            + " | Generated: "

            + str(
                report.get(
                    "Automatic Completed At", ""
                )
            )

        )

        st.warning(
            "Archived morning evidence is "
            "preliminary research, not a "
            "confirmed live direction."
        )

        columns = [

            "Symbol",
            "Pre-Market Category",
            "Morning Direction",
            "Change %",
            "Volume Ratio",
            "Preopen Gap %",
            "Buy Sell Ratio",
            "Sell Buy Ratio",
            "Latest Overnight News",
            "Latest Overnight Announcement"

        ]

        columns = [

            col for col in columns

            if col in frame.columns

        ]

        st.dataframe(
            frame[columns],
            hide_index=True,
            use_container_width=True
        )

        with st.expander(
            "Overnight News"
        ):

            st.dataframe(
                news,
                hide_index=True,
                use_container_width=True
            )

        with st.expander(
            "Corporate Announcements"
        ):

            st.dataframe(
                announcements,
                hide_index=True,
                use_container_width=True
            )

        with st.expander(
            "Pre-Market Diagnostics"
        ):

            st.json(report)

    except Exception as error:

        st.info(
            safe_error(error)
        )


# =====================================
# LIVE DASHBOARD
# =====================================

def priority_history(frame=None, report=None):
    """Retain observed priority classifications on this app instance."""
    import sqlite3
    import tempfile
    db_path = Path(tempfile.gettempdir()) / "v28_priority_history.sqlite3"
    with sqlite3.connect(str(db_path), timeout=10) as db:
        db.execute("CREATE TABLE IF NOT EXISTS priority (day TEXT, symbol TEXT, direction TEXT, first_at TEXT, last_at TEXT, first_price REAL, last_price REAL, current_category TEXT, PRIMARY KEY(day,symbol,direction))")
        db.execute("CREATE TABLE IF NOT EXISTS priority_session (day TEXT PRIMARY KEY)")
        if frame is not None and report.get("Status") == "LIVE RESEARCH AVAILABLE" and not frame.empty:
            day=now().date().isoformat()
            observed=report["Completed At"]
            db.execute("INSERT OR IGNORE INTO priority_session VALUES (?)",(day,))
            for row in frame.to_dict("records"):
                if not core.truth(row.get("Fresh")) or not core.fresh_timestamp(row.get("Quote Time")):
                    continue
                symbol=row["Symbol"]
                category=row["Live Category"]
                price=core.number(row.get("LTP"))
                db.execute("UPDATE priority SET last_price=?,current_category=? WHERE day=? AND symbol=?",(price,category,day,symbol))
                if category in ("PRIORITY BULLISH","PRIORITY BEARISH"):
                    db.execute("INSERT INTO priority VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(day,symbol,direction) DO UPDATE SET last_at=excluded.last_at,last_price=excluded.last_price,current_category=excluded.current_category",(day,symbol,category,observed,observed,price,price,category))
        day=db.execute("SELECT MAX(day) FROM priority_session").fetchone()[0]
        cursor = db.execute("SELECT symbol AS Symbol,direction AS Priority,first_at AS 'First Seen',last_at AS 'Last Qualified',first_price AS 'First LTP',last_price AS 'Last Observed LTP',current_category AS 'Last Observed Category' FROM priority WHERE day=? ORDER BY first_at",(day,))
        return day,pd.DataFrame(cursor.fetchall(), columns=[col[0] for col in cursor.description])


def show_priority_history():
    st.subheader("Session Priority History — Bullish / Bearish")
    try:
        day,history=priority_history()
        st.caption("Session: "+str(day or "Not yet scanned")+" | Recorded observations, not live quotes. Retained until next successful live session scan.")
        if history.empty:
            st.info("No priority stocks recorded for this session.")
        else:
            st.dataframe(history,hide_index=True,use_container_width=True)
            st.bar_chart(history.groupby("Priority")["Symbol"].nunique())
    except Exception as error:
        st.warning("Priority history storage unavailable: " + type(error).__name__)


def show_live(token):

    auto = st.toggle(

        "Automatic Live Scan Every 30 Seconds",

        value=False,

        key="final_v280_auto"

    )

    if not token:

        show_priority_history()
        st.info(
            "Enter Upstox Analytics Token"
        )

        return

    token_id = hashlib.sha256(
        token.encode("utf-8")
    ).hexdigest()

    if st.session_state.get(
        "final_v280_token_id"
    ) != token_id:

        st.session_state[STATE_KEY] = {}

        st.session_state.pop(
            RESULT_KEY, None
        )

        st.session_state.pop(
            JOURNAL_KEY, None
        )

        st.session_state[
            "final_v280_token_id"
        ] = token_id

    @st.fragment(run_every=30)
    def live_panel():

        active = core.market_window()

        st.caption(
            "Current IST: "
            + now().strftime(
                "%d-%m-%Y %H:%M:%S"
            )
        )

        manual = st.button(

            "SCAN FULL F&O UNIVERSE",

            type="primary",

            disabled=not active,

            key="final_v280_scan"

        )

        if not active:

            st.session_state[STATE_KEY] = {}

            st.info(
                "Outside normal NSE trading "
                "window. Live scan is disabled."
            )

            show_priority_history()
            return

        if manual or auto:

            try:

                with st.spinner(
                    "Checking full F&O universe..."
                ):

                    frame, report, state = (
                        integrated_scan(
                            token,
                            st.session_state.get(
                                STATE_KEY, {}
                            )
                        )
                    )

                st.session_state[
                    STATE_KEY
                ] = state

                st.session_state[
                    RESULT_KEY
                ] = (frame, report)

                core.update_journal(
                    frame, report
                )
                try:
                    priority_history(frame, report)
                except Exception:
                    st.warning("Priority history could not be saved.")

            except Exception as error:

                st.session_state[
                    STATE_KEY
                ] = {}

                st.session_state.pop(
                    RESULT_KEY, None
                )

                st.error(
                    safe_error(error, token)
                )

                show_priority_history()
                return

        show_priority_history()
        saved = st.session_state.get(
            RESULT_KEY
        )

        if saved is None:

            st.caption(
                "Press scan or enable "
                "automatic live scanning."
            )

            return

        frame, report = saved

        context = report.get(
            "Automatic Pre-Market", {}
        )

        st.caption(

            "Automatic Morning Context: "

            + context.get(
                "Status", "UNAVAILABLE"
            )

        )

        summary = report.get(
            "Live Summary", {}
        )

        coverage = core.number(
            summary.get(
                "Historical Live Coverage %"
            )
        )

        a, b = st.columns(2)

        a.metric(

            "Historical Baseline Coverage",

            f"{coverage:.2f}%"

            if coverage is not None

            else "N/A"

        )

        b.metric(

            "Historical Participation",

            "ENABLED"

            if summary.get(
                "Historical Participation Enabled"
            ) is True

            else "RESTRICTED"

        )

        original_show(
            frame,
            report
        )

    live_panel()


# =====================================
# FINAL MAIN INTERFACE
# =====================================

def render_final_dashboard():

    st.header(
        "F&O STOCK IDENTIFICATION V" + VERSION
    )

    st.caption(
        "Historical | Overnight | Pre-Market | Live"
    )

    st.warning(
        "Research and stock identification only. "
        "No automatic orders, option strikes "
        "or confirmed trade entries."
    )

    archive = archive_status()

    _, context = automatic_context()

    a, b, c = st.columns(3)

    a.metric(
        "Historical Stocks",
        archive.get("Stocks", 0)
    )

    b.metric(

        "Historical Sessions",

        str(
            archive.get(
                "Minimum Sessions", 0
            )
        )

        + "-"

        + str(
            archive.get(
                "Maximum Sessions", 0
            )
        )

    )

    c.metric(

        "Morning Context",

        context["Status"]

    )

    st.caption(

        "Historical Archive: "

        + archive["Status"]

        + " | Session: "

        + str(
            archive.get(
                "Archive Session Label", ""
            )
        )

    )

    section = st.radio(

        "Select Intelligence",

        [

            "Live Stock Identification",

            "Automatic Pre-Market",

            "Previous Session",

            "System Diagnostics"

        ],

        horizontal=True,

        key="final_v280_section"

    )

    if section == "Previous Session":

        show_historical()

        return

    if section == "Automatic Pre-Market":

        show_morning()

        return

    if section == "System Diagnostics":

        st.json({

            "Version": VERSION,

            "Historical Archive": archive,

            "Automatic Morning Context": context,

            "Permanent Collector":
                "V2.6 separate workflow",

            "Permanent Storage Verified":
                False,

            "Independent NSE Cross Check":
                False,

            "Preopen Volume Reconciled":
                False,

            "Prediction Accuracy Verified":
                False,

            "Automatic Orders":
                False

        })

        return

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

            key="final_v280_token"

        )

    token = str(token or "").strip()

    show_live(token)


if __name__ == "__main__":

    st.set_page_config(

        page_title="F&O Stock Identification V2.8",

        layout="wide"

    )

    render_final_dashboard()
