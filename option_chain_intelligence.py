
"""
F&O OPTION CHAIN INTELLIGENCE V2.0

Full Universe Option Chain Research
Upstox API
Cash + Futures + Options Evidence
No Automatic Orders
"""

import os
import time
import math
import gzip
import json
import requests
import pandas as pd
import streamlit as st

from datetime import datetime
from zoneinfo import ZoneInfo

from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from post_market_derivatives import (
    build_advanced_watchlist
)

VERSION = "2.0.0"

IST = ZoneInfo("Asia/Kolkata")

API = "https://api.upstox.com"

MASTER = (
    "https://assets.upstox.com/"
    "market-quote/instruments/exchange/NSE.json.gz"
)

REQUEST_DELAY = 0.55

MAX_SPREAD = 2.0

MIN_OPTION_VOLUME = 1

MIN_OPTION_OI = 1

MIN_CHAIN_COVERAGE = 0.90


# =====================================
# UTILITIES
# =====================================

def now():

    return datetime.now(IST)


def number(value):

    try:

        result = float(value)

        if math.isfinite(result):

            return result

    except (ValueError, TypeError):

        pass

    return None


def ratio(a, b):

    a = number(a)
    b = number(b)

    if a is None or b is None or b <= 0:

        return None

    return a / b


def total(values):

    values = [

        number(v)

        for v in values

    ]

    values = [

        v for v in values

        if v is not None

    ]

    return sum(values) if values else None


def create_session():

    session = requests.Session()

    retry = Retry(

        total=2,

        backoff_factor=1,

        status_forcelist=[

            429, 500, 502, 503, 504

        ],

        allowed_methods=["GET"],

        respect_retry_after_header=True

    )

    session.mount(

        "https://",

        HTTPAdapter(max_retries=retry)

    )

    session.headers.update({

        "Accept": "application/json",

        "User-Agent": "FO-Research-V2"

    })

    return session


def api_get(session, path, token, params):

    time.sleep(REQUEST_DELAY)

    response = session.get(

        API + path,

        headers={

            "Authorization": f"Bearer {token}"

        },

        params=params,

        timeout=(8, 25),

        allow_redirects=False

    )

    response.raise_for_status()

    payload = response.json()

    if payload.get("status") != "success":

        raise ValueError(

            "Upstox API unsuccessful response"

        )

    return payload.get("data")


# =====================================
# INSTRUMENT MASTER
# =====================================

def parse_expiry(value):

    try:

        if isinstance(value, (int, float)):

            if value > 1e11:

                value /= 1000

            return datetime.fromtimestamp(

                value, IST

            ).date()

        return pd.Timestamp(value).date()

    except Exception:

        return None


@st.cache_data(ttl=3600, show_spinner=False)
def load_master():

    response = requests.get(

        MASTER,

        timeout=(10, 40),

        allow_redirects=False

    )

    response.raise_for_status()

    content = response.content

    if len(content) > 100_000_000:

        raise ValueError("Master file too large")

    if content[:2] == b"\x1f\x8b":

        content = gzip.decompress(content)

    instruments = json.loads(content)

    if not isinstance(instruments, list):

        raise ValueError("Invalid instrument master")

    return instruments


def build_instrument_map(symbols):

    instruments = load_master()

    symbols = set(symbols)

    equities = {}

    for item in instruments:

        if item.get("segment") != "NSE_EQ":

            continue

        symbol = str(

            item.get("trading_symbol", "")

        ).strip().upper()

        key = item.get("instrument_key")

        if symbol in symbols and key:

            equities[key] = symbol

    expiries = {}

    today = now().date()

    for item in instruments:

        if item.get("segment") != "NSE_FO":

            continue

        side = item.get("instrument_type")

        if side not in ["CE", "PE"]:

            continue

        key = item.get("underlying_key")

        if key not in equities:

            continue

        expiry = parse_expiry(

            item.get("expiry")

        )

        if expiry is None or expiry < today:

            continue

        expiries.setdefault(

            key, {}

        ).setdefault(

            expiry, set()

        ).add(side)

    result = {}

    for key, dates in expiries.items():

        eligible = sorted([

            expiry

            for expiry, sides in dates.items()

            if sides == {"CE", "PE"}

        ])

        if not eligible:

            continue

        result[equities[key]] = {

            "Instrument Key": key,

            "Expiry": eligible[0].isoformat(),

            "Available Expiries": [

                d.isoformat()

                for d in eligible

            ]

        }

    return result


# =====================================
# OPTION CHAIN API
# =====================================

def fetch_chain(session, token, key, expiry):

    data = api_get(

        session,

        "/v2/option/chain",

        token,

        {

            "instrument_key": key,

            "expiry_date": expiry

        }

    )

    if not isinstance(data, list) or not data:

        raise ValueError("Empty option chain")

    return data


def extract_contract(

    symbol,

    expiry,

    strike,

    side,

    contract,

    spot

):

    if not isinstance(contract, dict):

        return None

    key = contract.get("instrument_key")

    if not key:

        return None

    market = contract.get("market_data") or {}

    greeks = contract.get("option_greeks") or {}

    ltp = number(market.get("ltp"))

    bid = number(market.get("bid_price"))

    ask = number(market.get("ask_price"))

    oi = number(market.get("oi"))

    previous_oi = number(

        market.get("prev_oi")

    )

    volume = number(

        market.get("volume")

    )

    for value in [

        ltp, bid, ask,

        oi, previous_oi, volume

    ]:

        if value is not None and value < 0:

            raise ValueError(

                "Negative market data"

            )

    spread = None

    if (

        bid is not None

        and ask is not None

        and bid > 0

        and ask >= bid

    ):

        midpoint = (bid + ask) / 2

        spread = (

            (ask - bid)

            / midpoint

            * 100

        )

    oi_change = None

    oi_change_pct = None

    if oi is not None and previous_oi is not None:

        oi_change = oi - previous_oi

        if previous_oi > 0:

            oi_change_pct = (

                oi_change / previous_oi * 100

            )

    liquid = (

        ltp is not None

        and ltp > 0

        and spread is not None

        and spread <= MAX_SPREAD

        and volume is not None

        and volume >= MIN_OPTION_VOLUME

        and oi is not None

        and oi >= MIN_OPTION_OI

    )

    return {

        "Symbol": symbol,

        "Expiry": expiry,

        "Instrument Key": key,

        "Side": side,

        "Strike": strike,

        "Spot": spot,

        "LTP": ltp,

        "Bid": bid,

        "Ask": ask,

        "Spread %": spread,

        "OI": oi,

        "Previous OI": previous_oi,

        "OI Change": oi_change,

        "OI Change %": oi_change_pct,

        "Volume": volume,

        "IV": number(greeks.get("iv")),

        "Delta": number(greeks.get("delta")),

        "Gamma": number(greeks.get("gamma")),

        "Theta": number(greeks.get("theta")),

        "Vega": number(greeks.get("vega")),

        "Liquidity Screen": bool(liquid)

    }


# =====================================
# NORMALIZE AND VALIDATE CHAIN
# =====================================

def normalize_chain(symbol, expiry, data):

    rows = []

    strikes = set()

    spots = []

    for item in data:

        actual_expiry = parse_expiry(

            item.get("expiry")

        )

        if (

            actual_expiry is None

            or actual_expiry.isoformat() != expiry

        ):

            raise ValueError(

                "Option expiry mismatch"

            )

        strike = number(

            item.get("strike_price")

        )

        spot = number(

            item.get("underlying_spot_price")

        )

        if strike is None or strike <= 0:

            raise ValueError("Invalid strike")

        if strike in strikes:

            raise ValueError("Duplicate strike")

        strikes.add(strike)

        if spot is not None and spot > 0:

            spots.append(spot)

        for side, field in [

            ("CE", "call_options"),

            ("PE", "put_options")

        ]:

            row = extract_contract(

                symbol,

                expiry,

                strike,

                side,

                item.get(field),

                spot

            )

            if row is not None:

                rows.append(row)

    frame = pd.DataFrame(rows)

    if frame.empty:

        raise ValueError("No usable contracts")

    if frame["Instrument Key"].duplicated().any():

        raise ValueError(

            "Duplicate option instrument"

        )

    if not spots:

        raise ValueError(

            "Underlying price unavailable"

        )

    spot = float(pd.Series(spots).median())

    spot_variation = (

        (max(spots) - min(spots))

        / spot * 100

    )

    if spot_variation > 0.5:

        raise ValueError(

            "Inconsistent underlying prices"

        )

    frame["Spot"] = spot

    coverage = len(frame) / (len(strikes) * 2)

    oi_coverage = float(

        frame["OI"].notna().mean()

    )

    previous_oi_coverage = float(

        frame["Previous OI"].notna().mean()

    )

    volume_coverage = float(

        frame["Volume"].notna().mean()

    )

    greek_columns = [

        "IV", "Delta", "Gamma",

        "Theta", "Vega"

    ]

    greek_coverage = float(

        frame[greek_columns]

        .notna()

        .all(axis=1)

        .mean()

    )

    metadata = {

        "Spot": spot,

        "Strikes": len(strikes),

        "Contracts": len(frame),

        "Returned Pair Coverage %":

            round(coverage * 100, 2),

        "OI Coverage %":

            round(oi_coverage * 100, 2),

        "Previous OI Coverage %":

            round(previous_oi_coverage * 100, 2),

        "Volume Coverage %":

            round(volume_coverage * 100, 2),

        "Greeks Coverage %":

            round(greek_coverage * 100, 2)

    }

    return frame, metadata


# =====================================
# MAX PAIN
# =====================================

def calculate_max_pain(frame):

    if frame["OI"].isna().any():

        return None

    calls = frame[

        frame["Side"].eq("CE")

    ]

    puts = frame[

        frame["Side"].eq("PE")

    ]

    if calls.empty or puts.empty:

        return None

    if frame["OI"].sum() <= 0:

        return None

    strikes = sorted(

        frame["Strike"].unique()

    )

    payout = {}

    for settlement in strikes:

        call_payout = (

            (settlement - calls["Strike"])

            .clip(lower=0)

            * calls["OI"]

        ).sum()

        put_payout = (

            (puts["Strike"] - settlement)

            .clip(lower=0)

            * puts["OI"]

        ).sum()

        payout[settlement] = (

            call_payout + put_payout

        )

    minimum = min(payout.values())

    candidates = [

        strike

        for strike, value in payout.items()

        if math.isclose(value, minimum)

    ]

    if len(candidates) != 1:

        return None

    return candidates[0]


# =====================================
# OPTION CHAIN SUMMARY
# =====================================

def summarize_chain(frame, metadata):

    ce = frame[frame["Side"].eq("CE")]

    pe = frame[frame["Side"].eq("PE")]

    if ce.empty or pe.empty:

        raise ValueError(

            "Call or Put side missing"

        )

    pair_ok = (

        metadata["Returned Pair Coverage %"]

        >= MIN_CHAIN_COVERAGE * 100

    )

    oi_ok = (

        pair_ok

        and metadata["OI Coverage %"] == 100

    )

    previous_ok = (

        oi_ok

        and metadata["Previous OI Coverage %"]

        == 100

    )

    volume_ok = (

        pair_ok

        and metadata["Volume Coverage %"] == 100

    )

    call_oi = total(ce["OI"]) if oi_ok else None

    put_oi = total(pe["OI"]) if oi_ok else None

    call_volume = (

        total(ce["Volume"])

        if volume_ok else None

    )

    put_volume = (

        total(pe["Volume"])

        if volume_ok else None

    )

    call_change = (

        total(ce["OI Change"])

        if previous_ok else None

    )

    put_change = (

        total(pe["OI Change"])

        if previous_ok else None

    )

    spot = metadata["Spot"]

    common_strikes = sorted(

        set(ce["Strike"])

        & set(pe["Strike"])

    )

    atm = None

    atm_ce = {}

    atm_pe = {}

    if common_strikes:

        atm = min(

            common_strikes,

            key=lambda x: abs(x - spot)

        )

        atm_ce = ce[

            ce["Strike"].eq(atm)

        ].iloc[0].to_dict()

        atm_pe = pe[

            pe["Strike"].eq(atm)

        ].iloc[0].to_dict()

    max_pain = (

        calculate_max_pain(frame)

        if oi_ok

        and metadata["Returned Pair Coverage %"]

        == 100

        else None

    )

    result = {

        **metadata,

        "Call OI": call_oi,

        "Put OI": put_oi,

        "PCR OI": ratio(put_oi, call_oi),

        "Call OI Change": call_change,

        "Put OI Change": put_change,

        "Call OI Change %": (

            ratio(

                call_change,

                total(ce["Previous OI"])

            ) * 100

            if previous_ok

            and total(ce["Previous OI"]) > 0

            else None

        ),

        "Put OI Change %": (

            ratio(

                put_change,

                total(pe["Previous OI"])

            ) * 100

            if previous_ok

            and total(pe["Previous OI"]) > 0

            else None

        ),

        "Call Volume": call_volume,

        "Put Volume": put_volume,

        "PCR Volume": ratio(

            put_volume, call_volume

        ),

        "Max Pain": max_pain,

        "ATM Reference": atm,

        "ATM CE IV": atm_ce.get("IV"),

        "ATM PE IV": atm_pe.get("IV"),

        "ATM CE Delta": atm_ce.get("Delta"),

        "ATM PE Delta": atm_pe.get("Delta"),

        "ATM CE Gamma": atm_ce.get("Gamma"),

        "ATM PE Gamma": atm_pe.get("Gamma"),

        "ATM CE Theta": atm_ce.get("Theta"),

        "ATM PE Theta": atm_pe.get("Theta"),

        "ATM CE Vega": atm_ce.get("Vega"),

        "ATM PE Vega": atm_pe.get("Vega"),

        "ATM CE Spread %":

            atm_ce.get("Spread %"),

        "ATM PE Spread %":

            atm_pe.get("Spread %"),

        "Liquid CE Contracts":

            int(ce["Liquidity Screen"].sum()),

        "Liquid PE Contracts":

            int(pe["Liquidity Screen"].sum()),

        "Option Data Status": (

            "RETURNED CHAIN AVAILABLE"

            if oi_ok and previous_ok and volume_ok

            else "PARTIAL DATA"

        )

    }

    return result


# =====================================
# COMBINE WITH EXISTING WATCHLIST
# =====================================

def combine_evidence(base, options):

    item = dict(base)

    item.update(options)

    category = str(

        item.get("Category", "")

    )

    pcr = number(item.get("PCR OI"))

    call_change = number(

        item.get("Call OI Change")

    )

    put_change = number(

        item.get("Put OI Change")

    )

    reasons = []

    if pcr is not None:

        reasons.append(

            f"PCR OI {pcr:.2f}"

        )

    if call_change is not None:

        reasons.append(

            f"Call OI change {call_change:,.0f}"

        )

    if put_change is not None:

        reasons.append(

            f"Put OI change {put_change:,.0f}"

        )

    if category in [

        "PRIORITY BULLISH",

        "PRELIMINARY BULLISH"

    ]:

        direction = "CE WATCH"

    elif category in [

        "PRIORITY BEARISH",

        "PRELIMINARY BEARISH"

    ]:

        direction = "PE WATCH"

    elif category in [

        "EMERGING ACTIVITY",

        "FUTURES WATCH",

        "DELIVERY WATCH"

    ]:

        direction = "BOTH SIDES - OBSERVE"

    else:

        direction = "REVIEW ONLY"

    if item.get("Option Data Status") != (

        "RETURNED CHAIN AVAILABLE"

    ):

        review = "OPTION DATA INCOMPLETE"

    else:

        review = "OPTION POSITIONING AVAILABLE"

    item.update({

        "Stock Direction Watch": direction,

        "Option Review": review,

        "Option Evidence": " | ".join(reasons),

        "Option Direction Confirmed": False,

        "Live Confirmation Required": True,

        "Trade Signal": "NOT GENERATED"

    })

    return item


# =====================================
# FULL UNIVERSE SCANNER
# =====================================

def scan_options(token, mode, progress=None):

    started = now()

    base, summary, diagnostics = (

        build_advanced_watchlist()

    )

    if base.empty:

        raise ValueError(

            "Base watchlist unavailable"

        )

    if mode == "Priority + Emerging":

        selected = base[

            base["Category"].isin([

                "PRIORITY BULLISH",

                "PRIORITY BEARISH",

                "PRELIMINARY BULLISH",

                "PRELIMINARY BEARISH",

                "EMERGING ACTIVITY"

            ])

        ].copy()

    else:

        selected = base.copy()

    if selected.empty:

        raise ValueError("No selected stocks")

    instruments = build_instrument_map(

        selected["Symbol"].tolist()

    )

    results = []

    contracts = []

    errors = []

    session = create_session()

    count = len(selected)

    auth_failed = False

    try:

        for index, (_, row) in enumerate(

            selected.iterrows()

        ):

            item = row.to_dict()

            symbol = item["Symbol"]

            instrument = instruments.get(symbol)

            if progress:

                progress.progress(

                    index / count,

                    text=(

                        f"Scanning {symbol} "

                        f"({index + 1}/{count})"

                    )

                )

            try:

                if auth_failed:

                    raise ValueError(

                        "Scan stopped: authorization failed"

                    )

                if instrument is None:

                    raise ValueError(

                        "Option instrument unavailable"

                    )

                expiry = instrument["Expiry"]

                data = fetch_chain(

                    session,

                    token,

                    instrument["Instrument Key"],

                    expiry

                )

                frame, metadata = normalize_chain(

                    symbol,

                    expiry,

                    data

                )

                options = summarize_chain(

                    frame, metadata

                )

                retrieved = now().isoformat()

                options.update({

                    "Option Expiry": expiry,

                    "Option Retrieved At": retrieved,

                    "Option Exchange Time":

                        "NOT PROVIDED",

                    "Option Snapshot Status":

                        "RETRIEVAL TIME ONLY"

                })

                frame["Retrieved At"] = retrieved

                frame["Exchange Time Verified"] = False

                contracts.append(frame)

                results.append(

                    combine_evidence(item, options)

                )

            except Exception as error:

                if isinstance(

                    error, requests.HTTPError

                ):

                    response = error.response

                    if response is not None:

                        if response.status_code in [

                            401, 403

                        ]:

                            auth_failed = True

                reason = str(error)

                errors.append({

                    "Symbol": symbol,

                    "Status": "UNAVAILABLE",

                    "Reason": reason

                })

                results.append(

                    combine_evidence(

                        item,

                        {

                            "Option Data Status":

                                "UNAVAILABLE",

                            "Option Error": reason

                        }

                    )

                )

    finally:

        session.close()

    if progress:

        progress.progress(

            1.0,

            text="Scan completed"

        )

    result = pd.DataFrame(results)

    detail = (

        pd.concat(

            contracts,

            ignore_index=True

        )

        if contracts

        else pd.DataFrame()

    )

    successful = count - len(errors)

    complete = int(

        result["Option Data Status"]

        .eq("RETURNED CHAIN AVAILABLE")

        .sum()

    )

    report = {

        "Version": VERSION,

        "Cash Report Date":

            summary["report_date"],

        "Universe": len(base),

        "Requested Stocks": count,

        "Successful Stocks": successful,

        "Complete Returned Chains": complete,

        "Failed Stocks": len(errors),

        "Scan Coverage %": round(

            successful / count * 100, 2

        ),

        "Complete Chain Coverage %": round(

            complete / count * 100, 2

        ),

        "Full Universe Coverage %": round(

            successful / len(base) * 100, 2

        ),

        "Contracts Received": len(detail),

        "Started At": started.isoformat(),

        "Completed At": now().isoformat(),

        "Exchange Timestamp Verified": False,

        "Option Closing Date Verified": False,

        "Independent NSE Cross Check": False,

        "Mode": mode,

        "Status": (

            "COMPLETE REQUESTED SCAN"

            if complete == count

            else "PARTIAL SCAN"

        ),

        "Trade Signal": "NOT GENERATED"

    }

    return result, detail, report, errors


# =====================================
# STREAMLIT INTERFACE
# =====================================

def render_option_intelligence():

    st.header(

        "OPTION CHAIN INTELLIGENCE V"

        + VERSION

    )

    st.caption(

        "Cash + Futures + Options | "

        "Full Universe Research | "

        "No Automatic Orders"

    )

    st.warning(

        "Option OI does not independently "

        "confirm buyer or writer direction. "

        "Retrieved quotes are not verified "

        "exchange closing snapshots."

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

            "Upstox Analytics Access Token",

            type="password",

            key="option_v20_token"

        )

    mode = st.radio(

        "Scan Mode",

        [

            "Full F&O Universe",

            "Priority + Emerging"

        ],

        index=0

    )

    st.caption(

        "Nearest available expiry is used. "

        "Full universe scanning may take "

        "several minutes."

    )

    if st.button(

        "GENERATE OPTION INTELLIGENCE",

        type="primary",

        key="generate_option_v20"

    ):

        if not token:

            st.error(

                "Upstox access token required"

            )

        else:

            try:

                st.session_state.pop(

                    "option_v20_result", None

                )

                progress = st.progress(0)

                with st.spinner(

                    "Loading cash and futures evidence..."

                ):

                    st.session_state[

                        "option_v20_result"

                    ] = scan_options(

                        token.strip(),

                        mode,

                        progress

                    )

            except Exception as error:

                st.error(str(error))

    result = st.session_state.get(

        "option_v20_result"

    )

    if result is None:

        return

    frame, detail, report, errors = result

    a, b, c = st.columns(3)

    a.metric(

        "F&O Universe",

        report["Universe"]

    )

    b.metric(

        "Requested Stocks",

        report["Requested Stocks"]

    )

    c.metric(

        "Successful Stocks",

        report["Successful Stocks"]

    )

    a, b, c = st.columns(3)

    a.metric(

        "Scan Coverage",

        f'{report["Scan Coverage %"]}%'

    )

    b.metric(

        "Complete Chains",

        report["Complete Returned Chains"]

    )

    c.metric(

        "Option Contracts",

        report["Contracts Received"]

    )

    st.caption(

        "Cash Report Date: "

        + str(report["Cash Report Date"])

    )

    st.caption(

        "Option retrieval completed: "

        + report["Completed At"]

    )

    if report["Failed Stocks"] > 0:

        st.warning(

            "Some option chains are unavailable. "

            "See Diagnostics."

        )

    columns = [

        "Symbol",

        "Category",

        "Stock Direction Watch",

        "Change %",

        "Volume Ratio",

        "Futures OI Change %",

        "Futures Buildup",

        "Option Expiry",

        "PCR OI",

        "PCR Volume",

        "Call OI",

        "Put OI",

        "Call OI Change",

        "Put OI Change",

        "ATM Reference",

        "ATM CE IV",

        "ATM PE IV",

        "ATM CE Delta",

        "ATM PE Delta",

        "ATM CE Gamma",

        "ATM PE Gamma",

        "ATM CE Theta",

        "ATM PE Theta",

        "ATM CE Vega",

        "ATM PE Vega",

        "ATM CE Spread %",

        "ATM PE Spread %",

        "Max Pain",

        "Option Data Status",

        "Option Review"

    ]

    columns = [

        col for col in columns

        if col in frame.columns

    ]

    tabs = st.tabs([

        "Bullish",

        "Bearish",

        "Emerging",

        "All Stocks",

        "Option Details",

        "Diagnostics"

    ])

    with tabs[0]:

        st.dataframe(

            frame[

                frame["Stock Direction Watch"]

                .eq("CE WATCH")

            ][columns],

            hide_index=True,

            use_container_width=True

        )

    with tabs[1]:

        st.dataframe(

            frame[

                frame["Stock Direction Watch"]

                .eq("PE WATCH")

            ][columns],

            hide_index=True,

            use_container_width=True

        )

    with tabs[2]:

        st.dataframe(

            frame[

                frame["Stock Direction Watch"]

                .eq("BOTH SIDES - OBSERVE")

            ][columns],

            hide_index=True,

            use_container_width=True

        )

    with tabs[3]:

        st.dataframe(

            frame[columns],

            hide_index=True,

            use_container_width=True

        )

    with tabs[4]:

        if detail.empty:

            st.info(

                "No option contracts available"

            )

        else:

            symbol = st.selectbox(

                "Select Stock",

                sorted(

                    detail["Symbol"].unique()

                )

            )

            st.dataframe(

                detail[

                    detail["Symbol"].eq(symbol)

                ].sort_values([

                    "Strike", "Side"

                ]),

                hide_index=True,

                use_container_width=True

            )

    with tabs[5]:

        st.json(report)

        if errors:

            st.dataframe(

                pd.DataFrame(errors),

                hide_index=True,

                use_container_width=True

            )

        st.caption(

            "Coverage is measured against "

            "requested stocks and returned strikes. "

            "It is not independently verified "

            "exchange-wide strike coverage."

        )

    st.download_button(

        "DOWNLOAD OPTION INTELLIGENCE",

        frame.to_csv(index=False),

        "option_intelligence_v20.csv",

        mime="text/csv"

    )

    if not detail.empty:

        st.download_button(

            "DOWNLOAD ALL OPTION CONTRACTS",

            detail.to_csv(index=False),

            "option_contracts_v20.csv",

            mime="text/csv"

        )

    st.download_button(

        "DOWNLOAD SCAN DIAGNOSTICS",

        json.dumps(

            {

                "summary": report,

                "errors": errors

            },

            indent=2,

            default=str

        ),

        "option_diagnostics_v20.json",

        mime="application/json"

    )
