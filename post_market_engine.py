
"""
F&O POST MARKET INTELLIGENCE
Version 1.9.0

Historical Stock Identification
Evidence Based Watchlist
No Automatic Orders
"""

import json
import math
import hashlib
import pandas as pd
import streamlit as st

from datetime import timedelta

from stock_intelligence_engine import (
    ROOT,
    now,
    parse_timestamp,
    save_snapshot
)

VERSION = "1.9.0"

BASELINE_DAYS = 20
MIN_BASELINE_DAYS = 10
NEWS_HOURS = 24

VOLUME_RATIO = 1.5
TURNOVER_RATIO = 1.5
DELIVERY_RATIO = 1.5
MIN_PRICE_MOVE = 0.75


# =====================================
# NUMERIC VALIDATION
# =====================================

def number(value):

    try:

        value = float(value)

        if math.isfinite(value):

            return value

    except (ValueError, TypeError):

        pass

    return None


def ratio(value, baseline):

    value = number(value)
    baseline = number(baseline)

    if value is None:

        return None

    if baseline is None or baseline <= 0:

        return None

    return value / baseline


# =====================================
# VERIFIED HISTORICAL STORAGE
# =====================================

def read_snapshot(source, day):

    folder = ROOT / source / str(day)

    path = folder / "latest.csv"

    metadata_path = folder / "metadata.json"

    if not path.exists():

        raise ValueError(
            f"{source}: snapshot missing"
        )

    if not metadata_path.exists():

        raise ValueError(
            f"{source}: metadata missing"
        )

    metadata = json.loads(
        metadata_path.read_text()
    )

    content = path.read_bytes()

    digest = hashlib.sha256(
        content
    ).hexdigest()

    if digest != metadata.get("sha256"):

        raise ValueError(
            f"{source}: checksum mismatch"
        )

    if metadata.get("report_date") != str(day):

        raise ValueError(
            f"{source}: date mismatch"
        )

    if metadata.get("source") != source:

        raise ValueError(
            f"{source}: source mismatch"
        )

    frame = pd.read_csv(path)

    if len(frame) != metadata.get("records"):

        raise ValueError(
            f"{source}: record count mismatch"
        )

    if "Eligible For Analysis" not in frame:

        raise ValueError(
            f"{source}: validation missing"
        )

    eligible = (
        frame["Eligible For Analysis"]
        .astype(str)
        .str.strip()
        .str.lower()
        .eq("true")
    )

    frame = frame[eligible].copy()

    return frame, metadata


def available_dates(source):

    folder = ROOT / source

    if not folder.exists():

        return []

    dates = []

    for path in folder.iterdir():

        if not path.is_dir():

            continue

        try:

            day = pd.Timestamp(
                path.name
            ).date()

        except Exception:

            continue

        if day <= now().date():

            dates.append(day.isoformat())

    return sorted(
        set(dates),
        reverse=True
    )


# =====================================
# UNIVERSE
# =====================================

def load_universe():

    path = ROOT / "latest_universe.json"

    payload = json.loads(
        path.read_text()
    )

    universe = payload.get("stocks")

    if not isinstance(universe, dict):

        raise ValueError(
            "Invalid stored universe"
        )

    if len(universe) != payload.get("count"):

        raise ValueError(
            "Universe count mismatch"
        )

    if not 100 <= len(universe) <= 500:

        raise ValueError(
            "Unexpected universe size"
        )

    return universe, payload.get("checked_at")


# =====================================
# LATEST MATCHED REPORT DATE
# =====================================

def latest_report_date():

    cash = set(
        available_dates("cash_bhavcopy")
    )

    delivery = set(
        available_dates("delivery")
    )

    common = sorted(
        cash & delivery,
        reverse=True
    )

    if not common:

        raise ValueError(
            "No matched cash and delivery reports"
        )

    return common[0]


# =====================================
# HISTORICAL BASELINE
# =====================================

def historical_baseline(source, day):

    dates = [

        value

        for value in available_dates(source)

        if value < day

    ][:BASELINE_DAYS]

    frames = []
    errors = []

    for report_date in dates:

        try:

            frame, metadata = read_snapshot(
                source,
                report_date
            )

            if metadata.get(
                "coverage_verified"
            ) is not True:

                raise ValueError(
                    "Coverage unverified"
                )

            if not frame["Report Date"].astype(
                str
            ).eq(report_date).all():

                raise ValueError(
                    "Historical date mismatch"
                )

            if frame["Symbol"].duplicated().any():

                raise ValueError(
                    "Duplicate symbols"
                )

            frames.append(frame)

        except Exception as error:

            errors.append(
                f"{report_date}: {error}"
            )

    if not frames:

        return pd.DataFrame(), errors

    history = pd.concat(
        frames,
        ignore_index=True
    )

    history = history.drop_duplicates(
        ["Symbol", "Report Date"]
    )

    return history, errors


def build_baseline(history, columns):

    result = {}

    if history.empty:

        return result

    for name, group in history.groupby(
        "Symbol"
    ):

        values = {}

        for column in columns:

            series = pd.to_numeric(
                group[column],
                errors="coerce"
            )

            series = series[
                series.map(
                    lambda x: number(x) is not None
                )
            ]

            series = series[series > 0]

            values[column + " Days"] = len(series)

            values[column] = (

                float(series.median())

                if len(series) >= MIN_BASELINE_DAYS

                else None

            )

        result[name] = values

    return result


# =====================================
# NEWS AND ANNOUNCEMENTS
# =====================================

def load_catalysts(source, cutoff):

    dates = available_dates(source)

    frames = []
    errors = []

    start = cutoff - timedelta(
        hours=NEWS_HOURS
    )

    for day in dates:

        if day < start.date().isoformat():

            continue

        try:

            frame, metadata = read_snapshot(
                source,
                day
            )

            if not frame.empty:

                frames.append(frame)

        except Exception as error:

            errors.append(str(error))

    if not frames:

        return {}, pd.DataFrame(), errors

    frame = pd.concat(
        frames,
        ignore_index=True
    )

    frame["_published"] = frame[
        "Published"
    ].map(parse_timestamp)

    frame = frame[
        frame["_published"].map(
            lambda value: (
                value is not None
                and pd.notna(value)
                and start <= value <= cutoff
            )
        )
    ].copy()

    frame = frame.drop_duplicates(
        ["Symbol", "Heading", "Published"]
    )

    frame = frame.sort_values(
        "_published",
        ascending=False
    )

    result = {}

    for name, group in frame.groupby(
        "Symbol"
    ):

        result[name] = {

            "count": len(group),

            "heading": str(
                group.iloc[0]["Heading"]
            )

        }

    return result, frame.drop(
        columns=["_published"]
    ), errors


# =====================================
# UPCOMING EVENTS
# =====================================

def load_events(cutoff):

    dates = available_dates(
        "corporate_events"
    )

    if not dates:

        return {}, "UNAVAILABLE"

    try:

        frame, metadata = read_snapshot(
            "corporate_events",
            dates[0]
        )

        if frame.empty:

            return {}, "COVERAGE UNVERIFIED"

        event_dates = pd.to_datetime(
            frame["Event Date"],
            errors="coerce"
        ).dt.date

        frame = frame[

            (event_dates >= cutoff.date())

            &

            (
                event_dates
                <= cutoff.date() + timedelta(days=3)
            )

        ]

        result = {}

        for name, group in frame.groupby(
            "Symbol"
        ):

            result[name] = " | ".join(

                (
                    group["Event Date"].astype(str)

                    + ": "

                    + group["Purpose"].fillna("")
                    .astype(str)

                ).unique()

            )

        return result, "COVERAGE UNVERIFIED"

    except Exception as error:

        return {}, str(error)


# =====================================
# POST MARKET STOCK IDENTIFICATION
# =====================================

def build_watchlist():

    cutoff = now()

    universe, universe_time = load_universe()

    day = latest_report_date()

    cash, cash_meta = read_snapshot(
        "cash_bhavcopy",
        day
    )

    delivery, delivery_meta = read_snapshot(
        "delivery",
        day
    )

    for frame, metadata in [

        (cash, cash_meta),

        (delivery, delivery_meta)

    ]:

        if metadata.get(
            "coverage_verified"
        ) is not True:

            raise ValueError(
                "Required source coverage unverified"
            )

        if frame["Symbol"].duplicated().any():

            raise ValueError(
                "Duplicate stock records"
            )

        if not frame["Report Date"].astype(
            str
        ).eq(day).all():

            raise ValueError(
                "Report date mismatch"
            )

        if set(frame["Symbol"]) != set(universe):

            raise ValueError(
                "Stored universe coverage mismatch"
            )

    cash_history, cash_errors = historical_baseline(
        "cash_bhavcopy",
        day
    )

    delivery_history, delivery_errors = (
        historical_baseline(
            "delivery",
            day
        )
    )

    cash_base = build_baseline(

        cash_history,

        [
            "Volume",
            "Turnover"
        ]

    )

    delivery_base = build_baseline(

        delivery_history,

        [
            "Delivery Quantity",
            "Delivery %"
        ]

    )

    news, news_frame, news_errors = (
        load_catalysts(
            "company_news",
            cutoff
        )
    )

    announcements, announcement_frame, ann_errors = (
        load_catalysts(
            "announcements",
            cutoff
        )
    )

    events, event_status = load_events(
        cutoff
    )

    cash = cash.set_index("Symbol")

    delivery = delivery.set_index("Symbol")

    market_change = pd.to_numeric(

        cash["Change %"],

        errors="coerce"

    ).median()

    rows = []

    for name in sorted(universe):

        current = cash.loc[name]

        delivered = delivery.loc[name]

        baseline = cash_base.get(
            name, {}
        )

        delivery_ref = delivery_base.get(
            name, {}
        )

        change = number(
            current["Change %"]
        )

        volume_ratio = ratio(

            current["Volume"],

            baseline.get("Volume")

        )

        turnover_ratio = ratio(

            current["Turnover"],

            baseline.get("Turnover")

        )

        delivery_ratio = ratio(

            delivered["Delivery Quantity"],

            delivery_ref.get(
                "Delivery Quantity"
            )

        )

        relative_strength = None

        if change is not None:

            if number(market_change) is not None:

                relative_strength = (
                    change - market_change
                )

        news_item = news.get(name, {})

        announcement = announcements.get(
            name, {}
        )

        news_count = news_item.get(
            "count", 0
        )

        announcement_count = announcement.get(
            "count", 0
        )

        event = events.get(name, "")

        reasons = []

        participation = False

        if (

            volume_ratio is not None

            and volume_ratio >= VOLUME_RATIO

        ):

            reasons.append(
                f"Volume {volume_ratio:.2f}x"
            )

            participation = True

        if (

            turnover_ratio is not None

            and turnover_ratio >= TURNOVER_RATIO

        ):

            reasons.append(
                f"Turnover {turnover_ratio:.2f}x"
            )

            participation = True

        if (

            delivery_ratio is not None

            and delivery_ratio >= DELIVERY_RATIO

        ):

            reasons.append(
                f"Delivery {delivery_ratio:.2f}x"
            )

        if news_count:

            reasons.append(
                "Recent company news"
            )

        if announcement_count:

            reasons.append(
                "Recent NSE announcement"
            )

        if event:

            reasons.append(
                "Upcoming corporate event"
            )

        catalyst = bool(

            news_count

            or announcement_count

            or event

        )

        watchlist = "NO SETUP"

        direction = "UNCONFIRMED"

        if (

            participation

            and change is not None

            and relative_strength is not None

        ):

            if (

                change >= MIN_PRICE_MOVE

                and relative_strength > 0

            ):

                watchlist = "BULLISH WATCH"

                direction = "UPWARD BIAS"

            elif (

                change <= -MIN_PRICE_MOVE

                and relative_strength < 0

            ):

                watchlist = "BEARISH WATCH"

                direction = "DOWNWARD BIAS"

            else:

                watchlist = "ACTIVITY WATCH"

        elif catalyst:

            watchlist = "NEWS / EVENT WATCH"

        elif reasons:

            watchlist = "ACTIVITY WATCH"

        if watchlist in {

            "BULLISH WATCH",

            "BEARISH WATCH"

        }:

            reasons.append(
                f"Closing move {change:.2f}%"
            )

        if catalyst and participation:

            priority = "CATALYST + ACTIVITY"

        elif participation:

            priority = "MARKET ACTIVITY"

        elif catalyst:

            priority = "CATALYST REVIEW"

        else:

            priority = "OBSERVATION"

        rows.append({

            "Report Date": day,

            "Symbol": name,

            "Watchlist": watchlist,

            "Directional Bias": direction,

            "Priority": priority,

            "Close": current["Close"],

            "Change %": change,

            "Relative Strength vs Universe %":
                relative_strength,

            "Volume Ratio": volume_ratio,

            "Turnover Ratio": turnover_ratio,

            "Delivery Quantity Ratio":
                delivery_ratio,

            "Delivery %":
                delivered["Delivery %"],

            "Volume Baseline Days":
                baseline.get("Volume Days", 0),

            "Delivery Baseline Days":
                delivery_ref.get(
                    "Delivery Quantity Days", 0
                ),

            "News Records": news_count,

            "Announcement Records":
                announcement_count,

            "Latest News":
                news_item.get("heading", ""),

            "Latest Announcement":
                announcement.get("heading", ""),

            "Upcoming Event": event,

            "Futures OI": "NOT INTEGRATED",

            "Option Chain": "NOT INTEGRATED",

            "Greeks": "NOT INTEGRATED",

            "Bulk / Block Deals": "NOT INTEGRATED",

            "Short Selling": "NOT INTEGRATED",

            "Reasons": " | ".join(reasons),

            "Live Confirmation Required": True

        })

    frame = pd.DataFrame(rows)

    watch = frame[
        frame["Watchlist"].ne("NO SETUP")
    ].copy()

    summary = {

        "version": VERSION,

        "generated_at": cutoff.isoformat(),

        "report_date": day,

        "universe_size": len(universe),

        "universe_checked_at": universe_time,

        "cash_coverage_verified": True,

        "delivery_coverage_verified": True,

        "watchlist_count": len(watch),

        "news_coverage": "UNVERIFIED",

        "announcement_coverage": "UNVERIFIED",

        "corporate_event_status": event_status,

        "historical_errors":
            cash_errors + delivery_errors,

        "supporting_errors":
            news_errors + ann_errors,

        "status": "PRELIMINARY WATCHLIST",

        "live_confirmation_required": True

    }

    return (

        frame,

        summary,

        news_frame,

        announcement_frame

    )


# =====================================
# DASHBOARD
# =====================================

def render_post_market():

    st.header(
        "F&O POST MARKET INTELLIGENCE V"
        + VERSION
    )

    st.caption(

        "Full Universe Research | "

        "Preliminary Watchlist | "

        "No Automatic Orders"

    )

    st.warning(

        "Historical activity does not guarantee "

        "the next session's direction. "

        "Live confirmation is required."

    )

    if st.button(

        "GENERATE POST MARKET WATCHLIST",

        type="primary",

        key="post_market_v19_generate"

    ):

        try:

            with st.spinner(
                "Analyzing stored F&O intelligence..."
            ):

                result = build_watchlist()

                st.session_state[
                    "post_market_v19_result"
                ] = result

        except Exception as error:

            st.session_state.pop(
                "post_market_v19_result",
                None
            )

            st.error(str(error))

    result = st.session_state.get(
        "post_market_v19_result"
    )

    if result is None:

        st.info(
            "Generate watchlist from stored reports."
        )

        return

    frame, summary, news, announcements = result

    st.caption(

        "Report date: "

        + summary["report_date"]

        + " | Generated: "

        + summary["generated_at"]

    )

    if summary["report_date"] != now().date().isoformat():

        st.warning(
            "Using an earlier trading session report."
        )

    a, b, c, d = st.columns(4)

    a.metric(
        "F&O Universe",
        len(frame)
    )

    b.metric(
        "Bullish Watch",
        int(
            frame["Watchlist"]
            .eq("BULLISH WATCH").sum()
        )
    )

    c.metric(
        "Bearish Watch",
        int(
            frame["Watchlist"]
            .eq("BEARISH WATCH").sum()
        )
    )

    d.metric(
        "Total Watchlist",
        summary["watchlist_count"]
    )

    tabs = st.tabs([

        "Bullish",

        "Bearish",

        "Activity",

        "News / Events",

        "All Stocks",

        "Source Diagnostics"

    ])

    categories = [

        "BULLISH WATCH",

        "BEARISH WATCH",

        "ACTIVITY WATCH",

        "NEWS / EVENT WATCH"

    ]

    columns = [

        "Symbol",

        "Priority",

        "Close",

        "Change %",

        "Relative Strength vs Universe %",

        "Volume Ratio",

        "Turnover Ratio",

        "Delivery Quantity Ratio",

        "Reasons",

        "Latest News",

        "Latest Announcement",

        "Upcoming Event"

    ]

    for index, category in enumerate(
        categories
    ):

        with tabs[index]:

            selected = frame[
                frame["Watchlist"].eq(category)
            ].copy()

            if selected.empty:

                st.info(
                    "No qualifying stocks."
                )

            else:

                if category == "BEARISH WATCH":

                    selected = selected.sort_values(
                        "Change %",
                        ascending=True
                    )

                elif category == "BULLISH WATCH":

                    selected = selected.sort_values(
                        "Change %",
                        ascending=False
                    )

                else:

                    selected = selected.sort_values(
                        "Volume Ratio",
                        ascending=False,
                        na_position="last"
                    )

                st.dataframe(

                    selected[columns],

                    hide_index=True,

                    use_container_width=True

                )

    with tabs[4]:

        st.dataframe(

            frame,

            hide_index=True,

            use_container_width=True

        )

    with tabs[5]:

        st.json(summary)

        st.warning(

            "News and announcements have "

            "unverified source completeness. "

            "Zero received records does not "

            "prove absence of news."

        )

        st.write(
            "Received News"
        )

        st.dataframe(
            news,
            use_container_width=True
        )

        st.write(
            "Received Announcements"
        )

        st.dataframe(
            announcements,
            use_container_width=True
        )

    watch = frame[
        frame["Watchlist"].ne("NO SETUP")
    ]

    st.download_button(

        "Download Post Market Watchlist",

        watch.to_csv(index=False),

        "post_market_watchlist_v19.csv",

        mime="text/csv"

    )

    if st.button(
        "Save Watchlist Locally",
        key="post_market_v19_save"
    ):

        try:

            details = save_snapshot(

                "post_market_watchlist",

                now().date(),

                frame,

                summary

            )

            st.success(
                details["status"]
            )

            st.caption(

                "Local storage only. "

                "GitHub persistence requires "

                "a collection workflow."

            )

        except Exception as error:

            st.error(str(error))


if __name__ == "__main__":

    st.set_page_config(

        page_title="Post Market Intelligence",

        layout="wide"

    )

    render_post_market()
