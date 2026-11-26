
import gzip
import json
import hashlib
import requests
import pandas as pd

from pathlib import Path
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from deal_data_engine import fetch_report


VERSION = "1.3"

IST = ZoneInfo("Asia/Kolkata")

ROOT = Path(__file__).resolve().parent

DATA = ROOT / "historical_data"

MASTER_URL = (
    "https://assets.upstox.com/"
    "market-quote/instruments/"
    "exchange/NSE.json.gz"
)

SOURCES = ["bulk", "block", "short"]


def now():

    return datetime.now(IST)


def timestamp():

    return now().isoformat()


# =====================================
# SAFE FILE STORAGE
# =====================================

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


def save_json(path, data):

    content = json.dumps(
        data,
        indent=2,
        ensure_ascii=False,
        default=str
    ).encode("utf-8")

    atomic_write(path, content)


def sha256(content):

    return hashlib.sha256(
        content
    ).hexdigest()


def save_snapshot(
    folder,
    frame,
    metadata
):

    folder.mkdir(
        parents=True,
        exist_ok=True
    )

    frame = frame.copy()

    frame = frame.sort_values(
        list(frame.columns),
        na_position="last"
    ).reset_index(drop=True)

    content = frame.to_csv(
        index=False,
        lineterminator="\n"
    ).encode("utf-8")

    digest = sha256(content)

    revision = (
        folder
        / "revisions"
        / f"{digest}.csv"
    )

    latest = folder / "latest.csv"

    manifest = folder / "metadata.json"

    if latest.exists():

        previous = sha256(
            latest.read_bytes()
        )

        if previous == digest:

            return "UNCHANGED", digest

    if not revision.exists():

        atomic_write(
            revision,
            content
        )

    atomic_write(
        latest,
        content
    )

    metadata.update({

        "version": VERSION,

        "sha256": digest,

        "records": len(frame),

        "saved_at": timestamp()

    })

    save_json(
        manifest,
        metadata
    )

    return "SAVED", digest


# =====================================
# AUTOMATIC F&O UNIVERSE
# =====================================

def get_fo_universe():

    response = requests.get(
        MASTER_URL,
        timeout=(10, 45)
    )

    response.raise_for_status()

    raw = response.content

    if raw[:2] == b"\x1f\x8b":

        raw = gzip.decompress(raw)

    instruments = json.loads(raw)

    if not isinstance(
        instruments, list
    ):

        raise ValueError(
            "Invalid instrument master"
        )

    today = now().date()

    equity = {}

    for item in instruments:

        if (

            item.get("segment")
            == "NSE_EQ"

            and item.get(
                "instrument_key"
            )

            and item.get(
                "trading_symbol"
            )

        ):

            equity[
                item["instrument_key"]
            ] = str(
                item["trading_symbol"]
            ).strip().upper()

    option_keys = {}

    for item in instruments:

        if item.get(
            "segment"
        ) != "NSE_FO":

            continue

        side = item.get(
            "instrument_type"
        )

        if side not in (
            "CE", "PE"
        ):

            continue

        key = item.get(
            "underlying_key"
        )

        if key not in equity:

            continue

        try:

            expiry = float(
                item.get("expiry")
            )

            if expiry > 100000000000:

                expiry /= 1000

            expiry_date = (
                datetime.fromtimestamp(
                    expiry,
                    IST
                ).date()
            )

            if expiry_date < today:

                continue

        except (
            ValueError,
            TypeError,
            OverflowError,
            OSError
        ):

            continue

        option_keys.setdefault(
            key,
            set()
        ).add(side)

    rows = []

    for key, sides in option_keys.items():

        if not {
            "CE", "PE"
        } <= sides:

            continue

        rows.append({

            "Symbol": equity[key],

            "Instrument Key": key

        })

    if not rows:

        raise ValueError(
            "Empty F&O universe"
        )

    universe = pd.DataFrame(rows)

    universe = (

        universe
        .drop_duplicates("Symbol")
        .sort_values("Symbol")
        .reset_index(drop=True)

    )

    if not 100 <= len(universe) <= 500:

        raise ValueError(
            f"Unexpected universe size: "
            f"{len(universe)}"
        )

    return universe


# =====================================
# STORE DAILY F&O UNIVERSE
# =====================================

def collect_universe():

    universe = get_fo_universe()

    day = now().date()

    folder = (

        DATA

        / "universe"

        / str(day)

    )

    status, digest = save_snapshot(

        folder,

        universe,

        {

            "source": "Upstox NSE Master",

            "snapshot_date": str(day),

            "master_url": MASTER_URL

        }

    )

    return universe, {

        "source": "universe",

        "status": status,

        "records": len(universe),

        "actual_date": str(day),

        "sha256": digest,

        "error": ""

    }


# =====================================
# STORE NSE DISCLOSURE REPORTS
# =====================================

def collect_report(
    kind,
    requested,
    universe=None
):

    frame = fetch_report(
        kind,
        requested
    )

    if frame.empty:

        raise ValueError(
            "Empty report"
        )

    actual = frame[
        "Report Date"
    ].iloc[0]

    folder = (

        DATA

        / "nse"

        / kind

        / str(actual)

    )

    status, digest = save_snapshot(

        folder,

        frame,

        {

            "source": kind,

            "report_date": str(actual),

            "requested_date": str(requested),

            "origin": "NSE automatic"

        }

    )

    matched_count = None

    if universe is not None:

        symbols = set(
            universe.Symbol
        )

        matched_count = int(

            frame.Symbol.isin(
                symbols
            ).sum()

        )

    return {

        "source": kind,

        "status": status,

        "records": len(frame),

        "fo_matched_current_universe":
            matched_count,

        "requested_date": str(requested),

        "actual_date": str(actual),

        "date_match": actual == requested,

        "sha256": digest,

        "error": ""

    }


# =====================================
# ERROR RECORD
# =====================================

def failure(source, error, day=None):

    return {

        "source": source,

        "status": "FAILED",

        "records": None,

        "requested_date": (
            str(day)
            if day is not None
            else None
        ),

        "actual_date": None,

        "error": str(error)

    }


# =====================================
# MAIN DAILY COLLECTION
# =====================================

def main():

    started = timestamp()

    today = now().date()

    results = []

    universe = None

    print(
        f"Collector V{VERSION}"
    )

    print(
        f"Started: {started}"
    )

    try:

        universe, result = (
            collect_universe()
        )

        results.append(result)

        print(result)

    except Exception as e:

        result = failure(
            "universe",
            e
        )

        results.append(result)

        print(result)

    # Bulk and Block:
    # Latest available NSE reports.

    for kind in ["bulk", "block"]:

        try:

            result = collect_report(

                kind,

                today,

                universe

            )

        except Exception as e:

            result = failure(

                kind,

                e,

                today

            )

        results.append(result)

        print(result)

    # Short selling:
    # Check recent dated URLs.
    # Store by actual report date.

    for offset in range(4):

        requested = (

            today

            - timedelta(days=offset)

        )

        if requested.weekday() >= 5:

            continue

        try:

            result = collect_report(

                "short",

                requested,

                universe

            )

        except Exception as e:

            result = failure(

                "short",

                e,

                requested

            )

        results.append(result)

        print(result)

    saved = sum(

        r["status"] == "SAVED"

        for r in results

    )

    unchanged = sum(

        r["status"] == "UNCHANGED"

        for r in results

    )

    failed = sum(

        r["status"] == "FAILED"

        for r in results

    )

    successful_sources = {

        r["source"]

        for r in results

        if r["status"] in (

            "SAVED",

            "UNCHANGED"

        )

    }

    expected = {

        "universe",

        "bulk",

        "block",

        "short"

    }

    missing_sources = sorted(

        expected

        - successful_sources

    )

    summary = {

        "version": VERSION,

        "started": started,

        "finished": timestamp(),

        "collection_date": str(today),

        "saved": saved,

        "unchanged": unchanged,

        "failed_attempts": failed,

        "successful_sources": sorted(
            successful_sources
        ),

        "missing_sources": missing_sources,

        "results": results

    }

    log_name = now().strftime(

        "%Y%m%d_%H%M%S_%f"

    )

    save_json(

        DATA

        / "logs"

        / str(today)

        / f"{log_name}.json",

        summary

    )

    save_json(

        DATA / "latest_collection.json",

        summary

    )

    print(

        json.dumps(

            summary,

            indent=2,

            default=str

        )

    )

    if missing_sources:

        raise SystemExit(1)


if __name__ == "__main__":

    main()
