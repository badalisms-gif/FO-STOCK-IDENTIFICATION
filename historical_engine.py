import io
import json
import hashlib
import zipfile
import requests
import pandas as pd
import streamlit as st

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from pathlib import PurePosixPath


VERSION = "1.4.1"

IST = ZoneInfo("Asia/Kolkata")

REPO = "badalisms-gif/FO-STOCK-IDENTIFICATION"

ZIP_URL = (
    f"https://codeload.github.com/"
    f"{REPO}/zip/refs/heads/main"
)

MAX_ZIP_SIZE = 100 * 1024 * 1024
MAX_FILE_SIZE = 25 * 1024 * 1024
MAX_TOTAL_SIZE = 200 * 1024 * 1024

SOURCES = ["bulk", "block", "short"]

SOURCE_NAMES = {
    "bulk": "Bulk Deals",
    "block": "Block Deals",
    "short": "Short Selling"
}


def today_ist():

    return datetime.now(IST).date()


# =====================================
# GITHUB ZIP ARCHIVE
# NO GITHUB REST API
# =====================================

@st.cache_data(ttl=300, show_spinner=False)
def repository_archive():

    raw = bytearray()

    with requests.get(

        ZIP_URL,

        timeout=(15, 90),

        stream=True,

        headers={
            "User-Agent": "FO-Intelligence-V1.4.1"
        }

    ) as response:

        response.raise_for_status()

        for chunk in response.iter_content(
            chunk_size=65536
        ):

            raw.extend(chunk)

            if len(raw) > MAX_ZIP_SIZE:

                raise ValueError(
                    "Repository ZIP exceeds size limit"
                )

    files = {}

    total_size = 0

    with zipfile.ZipFile(
        io.BytesIO(raw)
    ) as archive:

        for item in archive.infolist():

            if item.is_dir():

                continue

            parts = PurePosixPath(
                item.filename
            ).parts

            if len(parts) < 3:

                continue

            if parts[1] != "historical_data":

                continue

            if ".." in parts:

                raise ValueError(
                    "Invalid archive path"
                )

            if item.file_size > MAX_FILE_SIZE:

                raise ValueError(
                    "Historical file exceeds size limit"
                )

            total_size += item.file_size

            if total_size > MAX_TOTAL_SIZE:

                raise ValueError(
                    "Historical archive exceeds memory limit"
                )

            path = "/".join(parts[1:])

            if path in files:

                raise ValueError(
                    "Duplicate archive path"
                )

            files[path] = archive.read(item)

    if not files:

        raise ValueError(
            "No historical data found in repository"
        )

    snapshot = hashlib.sha256()

    for path in sorted(files):

        snapshot.update(path.encode("utf-8"))
        snapshot.update(b"\x00")
        snapshot.update(
            hashlib.sha256(files[path]).digest()
        )

    archive_id = snapshot.hexdigest()

    return archive_id, files


# =====================================
# REPOSITORY INDEX
# =====================================

def repository_index():

    archive_id, files = repository_archive()

    return archive_id, set(files.keys())


def read_file(sha, path):

    archive_id, files = repository_archive()

    if archive_id != sha:

        raise ValueError(
            "Archive changed. Refresh historical data."
        )

    if path not in files:

        raise FileNotFoundError(path)

    return files[path]


def read_json_file(sha, path):

    return json.loads(
        read_file(sha, path).decode("utf-8-sig")
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
# VERIFY STORED SNAPSHOT
# =====================================

def verified_snapshot(sha, path):

    raw = read_file(sha, path)

    metadata_path = (
        path.rsplit("/", 1)[0]
        + "/metadata.json"
    )

    metadata = read_json_file(
        sha,
        metadata_path
    )

    digest = hashlib.sha256(raw).hexdigest()

    if digest != metadata.get("sha256"):

        raise ValueError(
            "SHA256 mismatch"
        )

    frame = pd.read_csv(
        io.BytesIO(raw)
    )

    if len(frame) != int(metadata["records"]):

        raise ValueError(
            "Record count mismatch"
        )

    return frame, metadata


# =====================================
# LOAD F&O UNIVERSE
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

    if str(
        metadata.get("snapshot_date")
    ) != str(day):

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

    if str(
        metadata.get("report_date")
    ) != str(day):

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

    missing = required - set(frame.columns)

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

        or frame["Quantity"].isin(
            [float("inf"), float("-inf")]
        ).any()

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

            or frame["Price"].isin(
                [float("inf"), float("-inf")]
            ).any()

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

    days = int(days)

    if not 1 <= days <= 366:

        raise ValueError(
            "Invalid historical analysis period"
        )

    sha, paths = repository_index()

    universe, universe_date = load_universe(
        sha,
        paths
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

    for report in selected:

        try:

            frame = load_report(
                sha,
                report
            )

            frames.append(frame)

            diagnostics.append({

                "Date": report["Date"],

                "Source": report["Source"],

                "Status": "VERIFIED",

                "Records": len(frame),

                "Error": ""

            })

        except Exception as error:

            diagnostics.append({

                "Date": report["Date"],

                "Source": report["Source"],

                "Status": "FAILED",

                "Records": None,

                "Error": str(error)

            })

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

            diagnostics,

            columns=[

                "Date",

                "Source",

                "Status",

                "Records",

                "Error"

            ]

        ),

        "start": start,

        "end": end,

        "commit": sha,

        "archive_id": sha,

        "archive_method": "GITHUB ZIP",

        "latest_collection":
            latest_collection

    }


# =====================================
# DATA COVERAGE
# =====================================

def coverage_table(history):

    diagnostics = history["diagnostics"]

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

            row[SOURCE_NAMES[kind]] = status

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

    load_history.clear()

    repository_archive.clear()
