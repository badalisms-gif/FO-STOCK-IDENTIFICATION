
"""
PERMANENT LIVE MARKET COLLECTOR
Version 2.6.0

Independent background collection.
Full F&O universe.
Historical baseline integration.
Compressed permanent archives.
No automatic orders.
"""

import os
import json
import gzip
import time
import hashlib
import subprocess
import math

from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

from historical_live_bridge import scan_market


VERSION = "2.6.0"

IST = ZoneInfo("Asia/Kolkata")

ROOT = Path(__file__).resolve().parent

DATA = ROOT / "historical_data" / "live_market"

INTERVAL = 30

SAVE_INTERVAL = 300

TOKEN = os.getenv(
    "UPSTOX_ACCESS_TOKEN", ""
).strip()

WINDOW = os.getenv(
    "COLLECTION_WINDOW", "AM"
).upper()


def now():

    return datetime.now(IST)


def safe_error(error):

    message = str(error)

    if TOKEN:

        message = message.replace(
            TOKEN, "[REDACTED]"
        )

    return message[:500]


def minute_of_day():

    current = now()

    return (
        current.hour * 60
        + current.minute
    )


def session_limits():

    if WINDOW == "AM":

        return 555, 750

    if WINDOW == "PM":

        return 750, 930

    raise ValueError(
        "Invalid collection window"
    )


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


def run_git(*args):

    result = subprocess.run(

        ["git", *args],

        cwd=ROOT,

        capture_output=True,

        text=True,

        timeout=120

    )

    if result.returncode != 0:

        raise RuntimeError(
            safe_error(result.stderr)
        )

    return result.stdout


def publish_archive():

    run_git(

        "config",

        "user.name",

        "github-actions[bot]"

    )

    run_git(

        "config",

        "user.email",

        "41898282+github-actions[bot]"
        "@users.noreply.github.com"

    )

    run_git(

        "add",

        "-f",

        "historical_data/live_market"

    )

    changes = run_git(

        "diff",

        "--cached",

        "--name-only"

    )

    if changes.strip():

        run_git(

            "commit",

            "-m",

            "Archive live market "
            + now().isoformat()

        )

    for attempt in range(3):

        try:

            run_git(
                "pull",
                "--rebase",
                "origin",
                "main"
            )

            run_git(
                "push",
                "origin",
                "HEAD:main"
            )

            print(
                "Permanent archive published",
                flush=True
            )

            return True

        except Exception as error:

            print(

                "Archive publish retry:",

                safe_error(error),

                flush=True

            )

            try:

                run_git(
                    "rebase", "--abort"
                )

            except Exception:

                pass

            time.sleep(
                5 * (attempt + 1)
            )

    return False


def save_batch(records):

    if not records:

        return None

    current = now()

    day = current.date().isoformat()

    folder = DATA / day / WINDOW

    identifier = current.strftime(
        "%H%M%S_%f"
    )

    run_id = os.getenv(
        "GITHUB_RUN_ID", "local"
    )

    run_attempt = os.getenv(
        "GITHUB_RUN_ATTEMPT", "1"
    )

    raw = json.dumps(

        records,

        ensure_ascii=False,

        default=str,

        allow_nan=False,

        separators=(",", ":")

    ).encode("utf-8")

    compressed = gzip.compress(raw)

    filename = (

        f"{run_id}_{run_attempt}_"

        f"{identifier}.json.gz"

    )

    path = folder / filename

    atomic_write(
        path,
        compressed
    )

    metadata = {

        "Version": VERSION,

        "Session Date": day,

        "Window": WINDOW,

        "Scans": len(records),

        "SHA256": hashlib.sha256(
            compressed
        ).hexdigest(),

        "Archive": filename,

        "Saved At": current.isoformat(),

        "Independent NSE Cross Check":
            False

    }

    atomic_write(

        folder / (
            filename + ".metadata.json"
        ),

        json.dumps(

            metadata,

            indent=2

        ).encode("utf-8")

    )

    print(

        "Saved batch:",

        filename,

        "Scans:",

        len(records),

        flush=True

    )

    return path


def clean_value(value):

    if isinstance(value, dict):

        return {

            str(key): clean_value(item)

            for key, item in value.items()

        }

    if isinstance(value, (list, tuple)):

        return [
            clean_value(item)
            for item in value
        ]

    if isinstance(value, float):

        if not math.isfinite(value):

            return None

    return value


def collect_snapshot():

    started = now()

    frame, summary, sectors = (
        scan_market(TOKEN)
    )

    completed = now()

    status = str(
        summary.get("status", "")
    ).upper()

    if isinstance(frame, pd.DataFrame):

        stocks = json.loads(

            frame.to_json(
                orient="records",
                date_format="iso"
            )

        )

    else:

        stocks = []

    if isinstance(sectors, pd.DataFrame):

        sector_records = json.loads(

            sectors.to_json(
                orient="records"
            )

        )

    else:

        sector_records = []

    return clean_value({

        "Version": VERSION,

        "Session Date":
            started.date().isoformat(),

        "Started At":
            started.isoformat(),

        "Completed At":
            completed.isoformat(),

        "Status": status,

        "Summary": summary,

        "Stocks": stocks,

        "Sectors": sector_records,

        "Trade Signal": "NOT GENERATED"

    })


def main():

    if not TOKEN:

        raise RuntimeError(
            "UPSTOX_ACCESS_TOKEN missing"
        )

    start, end = session_limits()

    if now().weekday() >= 5:

        print("Weekend - no collection")

        return

    if minute_of_day() >= end:

        print("Collection window expired")

        return

    print(

        "PERMANENT LIVE COLLECTOR",

        VERSION,

        "Window:",

        WINDOW,

        flush=True

    )

    while minute_of_day() < start:

        time.sleep(10)

    records = []

    successful_scans = 0

    failed_scans = 0

    total_rows = 0

    last_save = time.monotonic()

    started = now()

    try:

        while minute_of_day() < end:

            cycle_start = time.monotonic()

            try:

                snapshot = collect_snapshot()

                if (

                    snapshot["Status"] == "LIVE"

                    and snapshot["Stocks"]

                ):

                    records.append(snapshot)

                    successful_scans += 1

                    total_rows += len(
                        snapshot["Stocks"]
                    )

                    print(

                        now().isoformat(),

                        "Stocks:",

                        len(snapshot["Stocks"]),

                        "Fresh:",

                        snapshot["Summary"].get(
                            "fresh_stocks", 0
                        ),

                        flush=True

                    )

                else:

                    records.append(snapshot)

                    print(

                        "Market status:",

                        snapshot["Status"],

                        flush=True

                    )

            except Exception as error:

                failed_scans += 1

                records.append({

                    "Version": VERSION,

                    "Completed At":
                        now().isoformat(),

                    "Status": "SCAN FAILED",

                    "Error":
                        safe_error(error),

                    "Stocks": []

                })

                print(

                    "Scan failed:",

                    safe_error(error),

                    flush=True

                )

            if (

                time.monotonic() - last_save

                >= SAVE_INTERVAL

            ):

                save_batch(records)

                records.clear()

                publish_archive()

                last_save = time.monotonic()

            elapsed = (

                time.monotonic()

                - cycle_start

            )

            time.sleep(

                max(1, INTERVAL - elapsed)

            )

    finally:

        save_batch(records)

        report = {

            "Version": VERSION,

            "Window": WINDOW,

            "Started At":
                started.isoformat(),

            "Completed At":
                now().isoformat(),

            "Successful Live Scans":
                successful_scans,

            "Failed Scans":
                failed_scans,

            "Total Stock Rows":
                total_rows,

            "Status": (

                "COLLECTION COMPLETED"

                if successful_scans > 0

                else "NO LIVE DATA"

            ),

            "Prediction Accuracy Verified":
                False

        }

        run_id = os.getenv(
            "GITHUB_RUN_ID", "local"
        )

        run_attempt = os.getenv(
            "GITHUB_RUN_ATTEMPT", "1"
        )

        path = (

            DATA

            / now().date().isoformat()

            / WINDOW

            / (
                f"summary_{run_id}_"
                f"{run_attempt}.json"
            )

        )

        atomic_write(

            path,

            json.dumps(

                report,

                indent=2

            ).encode("utf-8")

        )

        published = publish_archive()

        print(

            json.dumps(report, indent=2),

            flush=True

        )

        if not published:

            raise RuntimeError(

                "Permanent archive publish failed"

            )


if __name__ == "__main__":

    main()
