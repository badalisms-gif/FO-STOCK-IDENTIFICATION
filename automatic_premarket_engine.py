
"""
AUTOMATIC PRE-MARKET INTELLIGENCE
Version 2.7.0

Reuses Pre-Market Quality Engine V2.3.1.
Permanent date-wise archives.
Historical integrity validation.
Automatic overnight and preopen collection.

Stock identification only.
No automatic orders.
No strike selection.
"""

import os
import io
import json
import gzip
import math
import hashlib
import time

from pathlib import Path
from datetime import datetime, date
from zoneinfo import ZoneInfo

import pandas as pd

import premarket_quality_engine as quality

from stock_intelligence_engine import ROOT


VERSION = "2.7.0"

IST = ZoneInfo("Asia/Kolkata")

DATA = ROOT / "automatic_premarket"

TOKEN = os.getenv(
    "UPSTOX_ACCESS_TOKEN", ""
).strip()

MODE = os.getenv(
    "PREMARKET_MODE", "OVERNIGHT"
).upper()

MIN_COVERAGE = 0.90


# =====================================
# UTILITIES
# =====================================

def now():
    return datetime.now(IST)


def safe_error(error):

    message = str(error)

    if TOKEN:

        message = message.replace(
            TOKEN, "[REDACTED]"
        )

    return message[:1000]


def atomic_write(path, content):

    path.parent.mkdir(
        parents=True,
        exist_ok=True
    )

    temporary = path.with_suffix(
        path.suffix + ".tmp"
    )

    temporary.write_bytes(content)

    temporary.replace(path)


def clean(value):

    if isinstance(value, dict):

        return {
            str(k): clean(v)
            for k, v in value.items()
        }

    if isinstance(value, (list, tuple)):

        return [
            clean(v)
            for v in value
        ]

    if isinstance(value, float):

        if not math.isfinite(value):
            return None

    if isinstance(value, str) and TOKEN:

        return value.replace(
            TOKEN, "[REDACTED]"
        )

    return value


def json_bytes(value):

    return json.dumps(
        clean(value),
        ensure_ascii=False,
        indent=2,
        default=str,
        allow_nan=False
    ).encode("utf-8")


def frame_records(frame):

    if not isinstance(frame, pd.DataFrame):

        raise ValueError(
            "Invalid source dataframe"
        )

    return json.loads(
        frame.to_json(
            orient="records",
            date_format="iso"
        )
    )


# =====================================
# HISTORICAL BASE VERIFICATION
# =====================================

def verify_source(source, session):

    folder = ROOT / source / session

    path = folder / "latest.csv"

    metadata_path = (
        folder / "metadata.json"
    )

    if (
        not path.exists()
        or not metadata_path.exists()
    ):

        raise ValueError(
            source + " archive missing"
        )

    raw = path.read_bytes()

    metadata = json.loads(
        metadata_path.read_text()
    )

    actual_hash = hashlib.sha256(
        raw
    ).hexdigest()

    if metadata.get("sha256") != actual_hash:

        raise ValueError(
            source + " checksum mismatch"
        )

    if str(
        metadata.get("report_date")
    ) != session:

        raise ValueError(
            source + " report date mismatch"
        )

    frame = pd.read_csv(
        io.BytesIO(raw)
    )

    required = {
        "Symbol",
        "Report Date",
        "Eligible For Analysis"
    }

    if not required.issubset(
        frame.columns
    ):

        raise ValueError(
            source + " required fields missing"
        )

    if len(frame) != metadata.get("records"):

        raise ValueError(
            source + " record count mismatch"
        )

    if not frame["Report Date"].astype(
        str
    ).eq(session).all():

        raise ValueError(
            source + " row date mismatch"
        )

    symbols = (
        frame["Symbol"]
        .fillna("")
        .astype(str)
        .str.strip()
        .str.upper()
    )

    if (
        symbols.eq("").any()
        or symbols.duplicated().any()
    ):

        raise ValueError(
            source + " invalid symbols"
        )

    eligible = frame[
        "Eligible For Analysis"
    ].map(quality.truth)

    return set(symbols[eligible])


def verify_historical_base():

    base, latest, diagnostics, status = (
        quality.old.load_base()
    )

    session = str(
        latest["cash_date"]
    )

    session_date = date.fromisoformat(
        session
    )

    if session_date >= now().date():

        raise ValueError(
            "Historical date is not previous"
        )

    universe = set(
        base["Symbol"]
        .fillna("")
        .astype(str)
        .str.strip()
        .str.upper()
    )

    universe.discard("")
    universe.discard("NO MATCHED RECORD")

    if not 100 <= len(universe) <= 500:

        raise ValueError(
            "Historical universe invalid"
        )

    cash = verify_source(
        "cash_bhavcopy",
        session
    )

    delivery = verify_source(
        "delivery",
        session
    )

    matched = universe & cash & delivery

    coverage = len(matched) / len(universe)

    return {

        "Historical Date": session,

        "Historical Universe": len(universe),

        "Verified Matched Stocks": len(matched),

        "Historical Coverage %": round(
            coverage * 100, 2
        ),

        "Archive Integrity Verified": True,

        "Historical Coverage Sufficient":
            coverage >= MIN_COVERAGE,

        "Latest NSE Session Independently Verified":
            False,

        "Previous Session Manually Verified":
            False,

        "History Status": status

    }


# =====================================
# COLLECTION WINDOW
# =====================================

def minute_of_day():

    current = now()

    return (
        current.hour * 60
        + current.minute
    )


def allowed_window():

    minute = minute_of_day()

    if now().weekday() >= 5:
        return False

    if MODE == "OVERNIGHT":

        return 480 <= minute < 540

    if MODE == "PREOPEN":

        return 540 <= minute < 555

    return False


# =====================================
# COLLECT EXISTING QUALITY ENGINE
# =====================================

def collect():

    started = now()

    history = {}

    try:

        history = verify_historical_base()

    except Exception as error:

        history = {

            "Archive Integrity Verified": False,

            "Error": safe_error(error)

        }

    # Never claim manual verification
    # in an unattended workflow.

    frame, news, announcements, report = (
        quality.scan_market(
            TOKEN,
            verified=False
        )
    )

    frame = frame.copy()

    required = {

        "Symbol",

        "Pre-Market Category",

        "Morning Direction"

    }

    if not required.issubset(
        frame.columns
    ):

        raise ValueError(
            "Watchlist fields missing"
        )

    frame["Symbol"] = (
        frame["Symbol"]
        .fillna("")
        .astype(str)
        .str.strip()
        .str.upper()
    )

    frame = frame[
        frame["Symbol"].ne("")
        & frame["Symbol"].ne(
            "NO MATCHED RECORD"
        )
    ].copy()

    if frame["Symbol"].duplicated().any():

        raise ValueError(
            "Duplicate watchlist symbols"
        )

    if frame.empty:

        raise ValueError(
            "Empty premarket watchlist"
        )

    historical_date = str(
        report["Cash Report Date"]
    )

    if date.fromisoformat(
        historical_date
    ) >= now().date():

        raise ValueError(
            "Invalid historical report date"
        )

    frame["Report Date"] = historical_date

    frame["Collection Date"] = (
        started.date().isoformat()
    )

    frame["Collection Mode"] = MODE

    frame["Priority Allowed"] = False

    priority = frame[
        "Pre-Market Category"
    ].isin([

        "PRIORITY BULLISH",

        "PRIORITY BEARISH"

    ])

    frame.loc[
        priority,
        "Pre-Market Category"
    ] = "PREOPEN DIRECTION WATCH"

    frame.loc[
        priority,
        "Morning Direction"
    ] = "UNCONFIRMED"

    frame["Trade Signal"] = (
        "NOT GENERATED"
    )

    completed = now()

    report.update({

        "Automatic Version": VERSION,

        "Collection Mode": MODE,

        "Collection Date":
            started.date().isoformat(),

        "Automatic Collection": True,

        "Historical Verification": history,

        "Previous Session Manually Verified":
            False,

        "Priority Bullish": 0,

        "Priority Bearish": 0,

        "Status":
            "AUTOMATIC PRELIMINARY RESEARCH",

        "Category Counts": frame[
            "Pre-Market Category"
        ].value_counts().to_dict(),

        "Automatic Completed At":
            completed.isoformat(),

        "Used As Live Confirmation": False,

        "Prediction Accuracy Verified": False,

        "Trade Signal": "NOT GENERATED"

    })

    payload = {

        "Version": VERSION,

        "Collection Date":
            started.date().isoformat(),

        "Collection Mode": MODE,

        "Historical Report Date":
            historical_date,

        "Generated At":
            completed.isoformat(),

        "Watchlist": frame_records(frame),

        "News": frame_records(news),

        "Announcements":
            frame_records(announcements),

        "Diagnostics": report

    }

    return payload


# =====================================
# PERMANENT ARCHIVE
# =====================================

def save_archive(payload):

    day = payload["Collection Date"]

    folder = DATA / day / MODE.lower()

    identifier = now().strftime(
        "%H%M%S_%f"
    )

    run_id = os.getenv(
        "GITHUB_RUN_ID", "local"
    )

    attempt = os.getenv(
        "GITHUB_RUN_ATTEMPT", "1"
    )

    filename = (

        f"{run_id}_{attempt}_"

        f"{identifier}.json.gz"

    )

    compressed = gzip.compress(
        json_bytes(payload)
    )

    path = folder / filename

    atomic_write(
        path,
        compressed
    )

    metadata = {

        "Version": VERSION,

        "Collection Date": day,

        "Collection Mode": MODE,

        "Historical Report Date":
            payload["Historical Report Date"],

        "Generated At":
            payload["Generated At"],

        "Records": len(
            payload["Watchlist"]
        ),

        "SHA256": hashlib.sha256(
            compressed
        ).hexdigest(),

        "Archive": filename,

        "Preopen Coverage %":
            payload["Diagnostics"].get(
                "Preopen Coverage %", 0
            ),

        "Historical Verification":
            payload["Diagnostics"].get(
                "Historical Verification", {}
            ),

        "Trade Signal": "NOT GENERATED"

    }

    atomic_write(

        folder / (
            filename + ".metadata.json"
        ),

        json_bytes(metadata)

    )

    print(
        json.dumps(
            metadata,
            indent=2,
            default=str
        ),
        flush=True
    )

    return path


# =====================================
# VERIFIED ARCHIVE READER
# =====================================

def load_latest(session_date=None):

    day = str(
        session_date or now().date()
    )

    date.fromisoformat(day)

    folder = DATA / day

    if not folder.exists():

        raise ValueError(
            "Today's automatic archive "
            "is not available"
        )

    candidates = []

    for metadata_path in folder.rglob(
        "*.json.gz.metadata.json"
    ):

        try:

            metadata = json.loads(
                metadata_path.read_text()
            )

            if metadata.get(
                "Collection Date"
            ) != day:

                continue

            generated = datetime.fromisoformat(
                metadata["Generated At"]
            )

            if generated.tzinfo is None:

                continue

            generated = generated.astimezone(
                IST
            )

            if (
                generated.date().isoformat() != day
                or generated > now()
            ):

                continue

            candidates.append((
                generated,
                metadata_path,
                metadata
            ))

        except Exception:

            continue

    candidates.sort(
        key=lambda item: item[0],
        reverse=True
    )

    for generated, path, metadata in candidates:

        try:

            archive_name = metadata["Archive"]

            if Path(archive_name).name != archive_name:

                continue

            archive = path.parent / archive_name

            compressed = archive.read_bytes()

            if hashlib.sha256(
                compressed
            ).hexdigest() != metadata["SHA256"]:

                continue

            payload = json.loads(
                gzip.decompress(compressed)
            )

            if payload.get("Version") != VERSION:

                continue

            if payload.get(
                "Collection Date"
            ) != day:

                continue

            if payload.get(
                "Generated At"
            ) != metadata["Generated At"]:

                continue

            if payload.get(
                "Historical Report Date"
            ) != metadata["Historical Report Date"]:

                continue

            frame = pd.DataFrame(
                payload["Watchlist"]
            )

            required = {

                "Symbol",

                "Report Date",

                "Collection Date",

                "Pre-Market Category",

                "Morning Direction"

            }

            if not required.issubset(
                frame.columns
            ):

                continue

            if (
                frame.empty
                or len(frame) != metadata["Records"]
                or frame["Symbol"].duplicated().any()
                or not frame["Collection Date"].eq(
                    day
                ).all()
                or not frame["Report Date"].eq(
                    metadata["Historical Report Date"]
                ).all()
            ):

                continue

            report = payload["Diagnostics"]

            report["Archive Hash Verified"] = True

            report["Archive Path"] = str(
                archive.relative_to(ROOT)
            )

            return (

                frame,

                pd.DataFrame(
                    payload["News"]
                ),

                pd.DataFrame(
                    payload["Announcements"]
                ),

                report

            )

        except Exception:

            continue

    raise ValueError(
        "No valid automatic premarket archive"
    )


# =====================================
# AUTOMATIC LIVE CONTEXT
# =====================================

def load_live_context(historical_date):

    frame, _, _, report = load_latest()

    verification = report.get(
        "Historical Verification", {}
    )

    if not verification.get(
        "Archive Integrity Verified"
    ):

        raise ValueError(
            "Historical source integrity "
            "not verified"
        )

    if not verification.get(
        "Historical Coverage Sufficient"
    ):

        raise ValueError(
            "Historical coverage insufficient"
        )

    if str(
        report.get("Cash Report Date")
    ) != str(historical_date):

        raise ValueError(
            "Historical context date mismatch"
        )

    # Archived morning evidence is context,
    # never a fresh live confirmation.

    frame = frame.copy()

    frame["Priority Allowed"] = False

    frame["Used As Live Confirmation"] = False

    frame["Morning Direction"] = "UNCONFIRMED"

    priority = frame[
        "Pre-Market Category"
    ].isin([

        "PRIORITY BULLISH",

        "PRIORITY BEARISH"

    ])

    frame.loc[
        priority,
        "Pre-Market Category"
    ] = "ARCHIVED DIRECTION WATCH"

    return frame.to_csv(
        index=False
    ).encode("utf-8")


# =====================================
# BACKGROUND EXECUTION
# =====================================

def main():

    if not TOKEN:

        raise RuntimeError(
            "UPSTOX_ACCESS_TOKEN missing"
        )

    if MODE not in (
        "OVERNIGHT", "PREOPEN"
    ):

        raise ValueError(
            "Invalid collection mode"
        )

    if now().weekday() >= 5:

        print(
            "Weekend - collection skipped"
        )

        return

    if MODE == "PREOPEN":

        # Allow workflow to start early.
        # Wait until the preopen window.

        while 535 <= minute_of_day() < 540:

            time.sleep(5)

    if not allowed_window():

        print(
            "Outside collection window",
            flush=True
        )

        return

    successful = 0

    errors = []

    max_attempts = (

        1 if MODE == "OVERNIGHT"

        else 3

    )

    for attempt in range(max_attempts):

        if not allowed_window():

            break

        try:

            payload = collect()

            save_archive(payload)

            successful += 1

        except Exception as error:

            errors.append(
                safe_error(error)
            )

            print(
                "Collection error:",
                safe_error(error),
                flush=True
            )

        if (
            MODE == "PREOPEN"
            and attempt < max_attempts - 1
            and minute_of_day() < 552
        ):

            time.sleep(90)

        else:

            break

    print(
        json.dumps({

            "Version": VERSION,

            "Mode": MODE,

            "Successful Archives": successful,

            "Errors": errors,

            "Status": (

                "ARCHIVES CREATED"

                if successful

                else "COLLECTION FAILED"

            )

        }, indent=2),
        flush=True
    )

    if not successful:

        raise RuntimeError(
            "No automatic archive created"
        )


if __name__ == "__main__":

    main()
