
import io
import json
import hashlib
import requests
import pandas as pd
import streamlit as st

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from concurrent.futures import ThreadPoolExecutor


VERSION = "1.4"

IST = ZoneInfo("Asia/Kolkata")

REPO = "badalisms-gif/FO-STOCK-IDENTIFICATION"

API = f"https://api.github.com/repos/{REPO}"

RAW = f"https://raw.githubusercontent.com/{REPO}"

SOURCES = ["bulk", "block", "short"]

SOURCE_NAMES = {
    "bulk": "Bulk Deals",
    "block": "Block Deals",
    "short": "Short Selling"
}


def today_ist():

    return datetime.now(IST).date()


def get_bytes(url):

    response = requests.get(
        url,
        timeout=(10, 30),
        headers={
            "User-Agent": "FO-Intelligence-V1.4"
        }
    )

    response.raise_for_status()

    if len(response.content) > 25_000_000:

        raise ValueError(
            "File exceeds size limit"
        )

    return response.content


def get_json(url):

    return json.loads(
        get_bytes(url)
    )


# =====================================
# GITHUB REPOSITORY INDEX
# =====================================

@st.cache_data(ttl=300, show_spinner=False)
def repository_index():

    commit = get_json(
        f"{API}/commits/main"
    )

    sha = commit["sha"]

    tree_sha = commit[
        "commit"
    ]["tree"]["sha"]

    tree = get_json(
        f"{API}/git/trees/"
        f"{tree_sha}?recursive=1"
    )

    if tree.get("truncated"):

        raise ValueError(
            "Repository index truncated"
        )

    paths = {

        item["path"]

        for item in tree["tree"]

        if item["type"] == "blob"

    }

    return sha, paths


@st.cache_data(ttl=3600, show_spinner=False)
def read_file(sha, path):

    return get_bytes(
        f"{RAW}/{sha}/{path}"
    )


def read_json_file(sha, path):

    return json.loads(
        read_file(sha, path)
    )


# =====================================
# DISCOVER STORED REPORTS
# =====================================

def discover_reports(paths):

    reports = []

    for path in sorted(paths):

        parts = path.split("/")

        if len(parts) != 5:

            continue

        if parts[0] != "historical_data":

            continue

        if parts[1] != "nse":

            continue

        if parts[2] not in SOURCES:

            continue

        if parts[4] != "latest.csv":

            continue

        try:

            day = datetime.strptime(
                parts[3],
                "%Y-%m-%d"
            ).date()

        except ValueError:

            continue

        reports.append({

            "Source": parts[2],

            "Date": day,

            "Path": path

        })

    return reports


# =====================================
# VERIFY AND READ SNAPSHOT
# =====================================

def verified_snapshot(sha, path):

    raw = read_file(
        sha,
        path
    )

    metadata_path = (

        path.rsplit("/", 1)[0]

        + "/metadata.json"

    )

    metadata = read_json_file(

        sha,

        metadata_path

    )

    digest = hashlib.sha256(
        raw
    ).hexdigest()

    if digest != metadata.get("sha256"):

        raise ValueError(
            "SHA256 mismatch"
        )

    frame = pd.read_csv(
        io.BytesIO(raw)
    )

    if len(frame) != int(
        metadata["records"]
    ):

        raise ValueError(
            "Record count mismatch"
        )

    return frame, metadata


# =====================================
# LOAD LATEST F&O UNIVERSE
# =====================================

def load_universe(sha, paths):

    candidates = []

    for path in paths:

        parts = path.split("/")

        if (

            len(parts) == 4

            and parts[0] == "historical_data"

            and parts[1] == "universe"

            and parts[3] == "latest.csv"

        ):

            try:

                day = datetime.strptime(
                    parts[2],
                    "%Y-%m-%d"
                ).date()

                if day <= today_ist():

                    candidates.append(
                        (day, path)
                    )

            except ValueError:

                continue

    if not candidates:

        raise ValueError(
            "No stored F&O universe"
        )

    day, path = max(candidates)

    frame, metadata = verified_snapshot(

        sha,

        path

    )

    if "Symbol" not in frame.columns:

        raise ValueError(
            "Symbol column missing"
        )

    symbols = (

        frame["Symbol"]

        .dropna()

        .astype(str)

        .str.strip()

        .str.upper()

    )

    universe = sorted(
        set(symbols) - {""}
    )

    if not 100 <= len(universe) <= 500:

        raise ValueError(
            "Unexpected universe size"
        )

    if str(metadata.get(
        "snapshot_date"
    )) != str(day):

        raise ValueError(
            "Universe date mismatch"
        )

    return universe, day


# =====================================
# REPORT VALIDATION
# =====================================

def load_report(sha, report):

    kind = report["Source"]

    day = report["Date"]

    frame, metadata = verified_snapshot(

        sha,

        report["Path"]

    )

    if str(metadata.get(
        "report_date"
    )) != str(day):

        raise ValueError(
            "Metadata report date mismatch"
        )

    required = {

        "Report Date",

        "Symbol",

        "Quantity"

    }

    if kind != "short":

        required.update({

            "Price",

            "Side",

            "Client"

        })

    missing = required - set(
        frame.columns
    )

    if missing:

        raise ValueError(
            f"Missing columns: {missing}"
        )

    dates = pd.to_datetime(

        frame["Report Date"],

        errors="coerce"

    )

    if dates.isna().any():

        raise ValueError(
            "Invalid report date"
        )

    if not dates.dt.date.eq(day).all():

        raise ValueError(
            "Report date mismatch"
        )

    frame["Report Date"] = dates.dt.date

    if frame["Symbol"].isna().any():

        raise ValueError(
            "Missing stock symbol"
        )

    frame["Symbol"] = (

        frame["Symbol"]

        .astype(str)

        .str.strip()

        .str.upper()

    )

    if frame["Symbol"].eq("").any():

        raise ValueError(
            "Empty stock symbol"
        )

    frame["Quantity"] = pd.to_numeric(

        frame["Quantity"],

        errors="coerce"

    )

    if (

        frame["Quantity"].isna().any()

        or (frame["Quantity"] < 0).any()

    ):

        raise ValueError(
            "Invalid quantity"
        )

    frame["Source"] = kind

    if kind == "short":

        frame["Price"] = float("nan")

        frame["Side"] = "SHORT DISCLOSURE"

        frame["Client"] = ""

        frame["Deal Value"] = float("nan")

    else:

        frame["Price"] = pd.to_numeric(

            frame["Price"],

            errors="coerce"

        )

        if (

            frame["Price"].isna().any()

            or (frame["Price"] <= 0).any()

        ):

            raise ValueError(
                "Invalid deal price"
            )

        frame["Side"] = (

            frame["Side"]

            .fillna("")

            .astype(str)

            .str.strip()

            .str.upper()

            .replace({

                "B": "BUY",

                "S": "SELL",

                "BOUGHT": "BUY",

                "SOLD": "SELL"

            })

        )

        if not frame["Side"].isin(

            ["BUY", "SELL"]

        ).all():

            raise ValueError(
                "Unknown deal side"
            )

        frame["Client"] = (

            frame["Client"]

            .fillna("")

            .astype(str)

            .str.strip()

        )

        frame["Deal Value"] = (

            frame["Quantity"]

            * frame["Price"]

        )

    return frame


# =====================================
# HISTORICAL DATA LOADER
# =====================================

@st.cache_data(ttl=300, show_spinner=False)
def load_history(days):

    sha, paths = repository_index()

    universe, universe_date = (

        load_universe(sha, paths)

    )

    end = today_ist()

    start = end - timedelta(
        days=days - 1
    )

    available = discover_reports(paths)

    selected = [

        report

        for report in available

        if start <= report["Date"] <= end

    ]

    frames = []

    diagnostics = []

    def task(report):

        try:

            frame = load_report(

                sha,

                report

            )

            return frame, {

                "Date": report["Date"],

                "Source": report["Source"],

                "Status": "VERIFIED",

                "Records": len(frame),

                "Error": ""

            }

        except Exception as error:

            return None, {

                "Date": report["Date"],

                "Source": report["Source"],

                "Status": "FAILED",

                "Records": None,

                "Error": str(error)

            }

    with ThreadPoolExecutor(
        max_workers=6
    ) as executor:

        for frame, diagnostic in executor.map(

            task,

            selected

        ):

            diagnostics.append(
                diagnostic
            )

            if frame is not None:

                frames.append(frame)

    if frames:

        all_data = pd.concat(

            frames,

            ignore_index=True

        )

        data = all_data[

            all_data["Symbol"].isin(
                universe
            )

        ].copy()

    else:

        all_data = pd.DataFrame()

        data = pd.DataFrame()

    latest_collection = {}

    try:

        latest_collection = read_json_file(

            sha,

            "historical_data/"
            "latest_collection.json"

        )

    except Exception as error:

        latest_collection = {

            "error": str(error)

        }

    return {

        "data": data,

        "all_data": all_data,

        "universe": universe,

        "universe_date": universe_date,

        "diagnostics": pd.DataFrame(
            diagnostics
        ),

        "start": start,

        "end": end,

        "commit": sha,

        "latest_collection":
            latest_collection

    }


# =====================================
# DATA COVERAGE
# =====================================

def coverage_table(history):

    diagnostics = history[
        "diagnostics"
    ]

    rows = []

    day = history["start"]

    while day <= history["end"]:

        row = {

            "Date": day,

            "Calendar": (

                "Weekend"

                if day.weekday() >= 5

                else "Weekday"

            )

        }

        for kind in SOURCES:

            status = "NOT STORED"

            if not diagnostics.empty:

                found = diagnostics[

                    (diagnostics["Date"] == day)

                    &

                    (diagnostics["Source"] == kind)

                ]

                if not found.empty:

                    status = found[
                        "Status"
                    ].iloc[0]

            row[
                SOURCE_NAMES[kind]
            ] = status

        rows.append(row)

        day += timedelta(days=1)

    return pd.DataFrame(rows)


# =====================================
# STOCK-WISE ACTIVITY SUMMARY
# =====================================

def stock_summary(data):

    if data.empty:

        return pd.DataFrame()

    rows = []

    for symbol, group in data.groupby(
        "Symbol"
    ):

        bulk = group[
            group.Source == "bulk"
        ]

        block = group[
            group.Source == "block"
        ]

        short = group[
            group.Source == "short"
        ]

        row = {

            "Symbol": symbol,

            "Disclosure Days":

                group[
                    "Report Date"
                ].nunique(),

            "Latest Disclosure":

                group[
                    "Report Date"
                ].max(),

            "Bulk Records": len(bulk),

            "Bulk Days":

                bulk[
                    "Report Date"
                ].nunique(),

            "Block Records": len(block),

            "Block Days":

                block[
                    "Report Date"
                ].nunique(),

            "Short Selling Days":

                short[
                    "Report Date"
                ].nunique(),

            "Short Quantity":

                short["Quantity"].sum()

                if not short.empty

                else None

        }

        for name, frame in [

            ("Bulk", bulk),

            ("Block", block)

        ]:

            for side in ["BUY", "SELL"]:

                subset = frame[

                    frame["Side"] == side

                ]

                row[
                    f"{name} {side} Cr"
                ] = round(

                    subset[
                        "Deal Value"
                    ].sum()

                    / 10_000_000,

                    3

                )

        rows.append(row)

    result = pd.DataFrame(rows)

    return result.sort_values(

        [

            "Disclosure Days",

            "Latest Disclosure",

            "Bulk Records"

        ],

        ascending=False

    ).reset_index(drop=True)


# =====================================
# CLIENT-WISE ACTIVITY
# =====================================

def client_summary(data):

    if data.empty:

        return pd.DataFrame()

    deals = data[

        data.Source.isin(
            ["bulk", "block"]
        )

        &

        data.Client.ne("")

    ].copy()

    if deals.empty:

        return pd.DataFrame()

    result = deals.groupby(

        [

            "Client",

            "Symbol",

            "Source",

            "Side"

        ],

        as_index=False

    ).agg(

        Records=("Quantity", "size"),

        Quantity=("Quantity", "sum"),

        Value=("Deal Value", "sum"),

        Days=("Report Date", "nunique")

    )

    result["Value Cr"] = (

        result["Value"]

        / 10_000_000

    ).round(3)

    return result.drop(

        columns=["Value"]

    ).sort_values(

        "Value Cr",

        ascending=False

    )


# =====================================
# DAILY SHORT SELLING TREND
# =====================================

def short_trend(data, symbol):

    if data.empty:

        return pd.DataFrame()

    short = data[

        (data.Source == "short")

        &

        (data.Symbol == symbol)

    ]

    if short.empty:

        return pd.DataFrame()

    return short.groupby(

        "Report Date",

        as_index=False

    ).agg(

        Quantity=("Quantity", "sum")

    ).sort_values("Report Date")


# =====================================
# CACHE REFRESH
# =====================================

def refresh_history():

    repository_index.clear()

    load_history.clear()
