
"""
F&O STOCK INTELLIGENCE ENGINE
Version 1.6.0

NSE + Upstox
Full F&O Universe
Permanent Historical Storage
Research Only
"""

import os
import io
import re
import gzip
import json
import math
import hashlib
import zipfile
import requests
import pandas as pd

from datetime import datetime, date, timedelta
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlparse, urljoin

from daily_collector import DATA, now, save_json


VERSION = "1.6.0"

NSE = "https://www.nseindia.in"

ARC = "https://nsearchives.nseindia.com"

UP = "https://api.upstox.com"

MASTER = (
    "https://assets.upstox.com/"
    "market-quote/instruments/exchange/NSE.json.gz"
)

NSE_HOSTS = {
    "www.nseindia.com",
    "nseindia.com",
    "www.nseindia.in",
    "nseindia.in",
    "nsearchives.nseindia.com"
}

UP_HOSTS = {
    "api.upstox.com"
}

MASTER_HOSTS = {
    "assets.upstox.com"
}

HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Accept": "application/json, text/plain, */*"
}

MAX_SIZE = 100_000_000

NEWS_LOOKBACK = 7

HISTORY_DAYS = 35

HOLIDAYS = {
    date(2026, 11, 24)
}

ROOT = DATA / "stock_intelligence"


# =====================================
# BASIC UTILITIES
# =====================================

def normalize(value):

    return re.sub(
        r"[^a-z0-9]",
        "",
        str(value).lower()
    )


def symbol(value):

    return str(value).strip().upper()


def clean_error(error, token=""):

    message = str(error)

    if token:

        message = message.replace(
            token,
            "[REDACTED]"
        )

    return message[:1500]


def parse_timestamp(value):

    if value is None:

        return None

    try:

        if isinstance(value, (int, float)):

            number = float(value)

            if abs(number) >= 1e12:

                number /= 1000

            stamp = pd.to_datetime(
                number,
                unit="s",
                utc=True
            )

        else:

            stamp = pd.to_datetime(
                value,
                dayfirst=True,
                errors="coerce"
            )

        if pd.isna(stamp):

            return None

        if stamp.tzinfo is None:

            stamp = stamp.tz_localize(
                "Asia/Kolkata"
            )

        return stamp.tz_convert(
            "Asia/Kolkata"
        )

    except Exception:

        return None


def numeric(series):

    return pd.to_numeric(

        series.astype(str)
        .str.replace(",", "", regex=False)
        .str.strip(),

        errors="coerce"

    )


def require_columns(frame, columns):

    missing = set(columns) - set(
        frame.columns
    )

    if missing:

        raise ValueError(
            f"Missing columns: {sorted(missing)}"
        )


def trading_days(count=35):

    today = now().date()

    days = []

    for offset in range(count):

        day = today - timedelta(
            days=offset
        )

        if day.weekday() >= 5:

            continue

        if day in HOLIDAYS:

            continue

        if (
            day == today
            and now().hour < 18
        ):

            continue

        days.append(day)

    return days


# =====================================
# SECURE HTTP
# =====================================

def secure_get(
    url,
    params=None,
    token=""
):

    host = urlparse(url).hostname

    if host in NSE_HOSTS:

        allowed = NSE_HOSTS

    elif host in UP_HOSTS:

        allowed = UP_HOSTS

    elif host in MASTER_HOSTS:

        allowed = MASTER_HOSTS

    else:

        raise ValueError(
            "Unknown source host"
        )

    if token and allowed != UP_HOSTS:

        raise ValueError(
            "Token restricted to Upstox"
        )

    headers = dict(HEADERS)

    if token:

        headers["Authorization"] = (
            "Bearer " + token
        )

    current = url

    with requests.Session() as session:

        for _ in range(6):

            parsed = urlparse(current)

            if (
                parsed.scheme != "https"
                or parsed.hostname not in allowed
                or parsed.username
                or parsed.password
                or parsed.port not in (None, 443)
            ):

                raise ValueError(
                    "Unsafe source URL"
                )

            with session.get(

                current,

                params=params,

                headers=headers,

                timeout=(8, 30),

                allow_redirects=False,

                stream=True

            ) as response:

                if response.status_code in {

                    301, 302, 303, 307, 308

                }:

                    location = response.headers.get(
                        "Location"
                    )

                    if not location:

                        raise ValueError(
                            "Missing redirect location"
                        )

                    current = urljoin(
                        response.url,
                        location
                    )

                    params = None

                    continue

                response.raise_for_status()

                if response.status_code != 200:

                    raise ValueError(
                        f"HTTP {response.status_code}"
                    )

                content = bytearray()

                for chunk in response.iter_content(
                    chunk_size=65536
                ):

                    content.extend(chunk)

                    if len(content) > MAX_SIZE:

                        raise ValueError(
                            "Response exceeds size limit"
                        )

                return bytes(content)

    raise ValueError(
        "Maximum redirects exceeded"
    )


def get_json(
    url,
    params=None,
    token=""
):

    content = secure_get(
        url,
        params,
        token
    )

    try:

        return json.loads(content)

    except Exception:

        raise ValueError(
            "Invalid JSON response"
        )


def extract_rows(payload):

    if isinstance(payload, list):

        rows = payload

    elif isinstance(payload, dict):

        rows = payload.get("data")

    else:

        rows = None

    if not isinstance(rows, list):

        raise ValueError(
            "Unexpected response schema"
        )

    if not all(
        isinstance(row, dict)
        for row in rows
    ):

        raise ValueError(
            "Invalid record schema"
        )

    return rows


# =====================================
# FULL F&O UNIVERSE
# =====================================

def get_universe():

    content = secure_get(MASTER)

    if content[:2] == b"\x1f\x8b":

        content = gzip.decompress(content)

    if len(content) > MAX_SIZE:

        raise ValueError(
            "Instrument master too large"
        )

    instruments = json.loads(content)

    if not isinstance(instruments, list):

        raise ValueError(
            "Invalid instrument master"
        )

    equity = {}

    options = {}

    today = now().date()

    for item in instruments:

        if item.get("segment") == "NSE_EQ":

            key = item.get("instrument_key")

            name = item.get("trading_symbol")

            if key and name:

                equity[key] = symbol(name)

    for item in instruments:

        if item.get("segment") != "NSE_FO":

            continue

        side = item.get("instrument_type")

        if side not in {"CE", "PE"}:

            continue

        key = item.get("underlying_key")

        if key not in equity:

            continue

        expiry = parse_timestamp(
            item.get("expiry")
        )

        if expiry is None:

            continue

        if expiry.date() < today:

            continue

        options.setdefault(
            key, set()
        ).add(side)

    universe = {

        equity[key]: key

        for key, sides in options.items()

        if sides == {"CE", "PE"}

    }

    if not 100 <= len(universe) <= 500:

        raise ValueError(
            f"Unexpected F&O universe: "
            f"{len(universe)}"
        )

    return dict(sorted(
        universe.items()
    ))


# =====================================
# PERMANENT STORAGE
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


def save_snapshot(
    source,
    day,
    frame,
    metadata=None
):

    folder = ROOT / source / str(day)

    frame = frame.copy()

    if not frame.empty:

        frame = frame.drop_duplicates()

        frame = frame.sort_values(

            list(frame.columns),

            na_position="last"

        )

    content = frame.to_csv(

        index=False,

        lineterminator="\n"

    ).encode("utf-8")

    digest = hashlib.sha256(
        content
    ).hexdigest()

    revision = (

        folder

        / "revisions"

        / f"{digest}.csv"

    )

    latest = folder / "latest.csv"

    unchanged = (

        latest.exists()

        and hashlib.sha256(
            latest.read_bytes()
        ).hexdigest() == digest

    )

    if not revision.exists():

        atomic_write(
            revision,
            content
        )

    if not unchanged:

        atomic_write(
            latest,
            content
        )

    details = {

        "version": VERSION,

        "source": source,

        "report_date": str(day),

        "records": len(frame),

        "sha256": digest,

        "checked_at": now().isoformat(),

        **(metadata or {})

    }

    save_json(

        folder / "metadata.json",

        details

    )

    return {

        **details,

        "status": (

            "UNCHANGED"

            if unchanged

            else "SAVED"

        )

    }


# =====================================
# NSE HISTORICAL REPORTS
# =====================================

def download_report(url):

    content = secure_get(url)

    if content[:2] == b"PK":

        with zipfile.ZipFile(
            io.BytesIO(content)
        ) as archive:

            files = [

                item

                for item in archive.infolist()

                if item.filename.lower()
                .endswith(".csv")

            ]

            if len(files) != 1:

                raise ValueError(
                    "Unexpected ZIP contents"
                )

            if files[0].file_size > MAX_SIZE:

                raise ValueError(
                    "CSV exceeds size limit"
                )

            with archive.open(files[0]) as file:

                frame = pd.read_csv(

                    file,

                    low_memory=False

                )

    else:

        frame = pd.read_csv(

            io.BytesIO(content),

            low_memory=False

        )

    frame.columns = [

        str(column).strip().upper()

        for column in frame.columns

    ]

    if frame.columns.duplicated().any():

        raise ValueError(
            "Duplicate report columns"
        )

    return frame


# =====================================
# CASH MARKET BHAVCOPY
# =====================================

def fetch_cash_bhavcopy(day, universe):

    url = (

        ARC

        + "/content/cm/"

        + "BhavCopy_NSE_CM_0_0_0_"

        + day.strftime("%Y%m%d")

        + "_F_0000.csv.zip"

    )

    frame = download_report(url)

    required = {

        "TCKRSYMB",

        "TRADDT",

        "SCTYSRS",

        "OPNPRIC",

        "HGHPRIC",

        "LWPRIC",

        "CLSPRIC",

        "PRVSCLSGPRIC",

        "TTLTRADGVOL",

        "TTLTRFVAL"

    }

    require_columns(
        frame,
        required
    )

    dates = pd.to_datetime(

        frame["TRADDT"],

        errors="coerce"

    ).dt.date

    if (
        frame.empty
        or not dates.eq(day).all()
    ):

        raise ValueError(
            "Cash bhavcopy date mismatch"
        )

    frame["Symbol"] = (

        frame["TCKRSYMB"]

        .map(symbol)

    )

    frame = frame[

        frame["Symbol"].isin(universe)

        &

        frame["SCTYSRS"]
        .astype(str)
        .str.strip()
        .eq("EQ")

    ].copy()

    if frame.empty:

        raise ValueError(
            "No F&O equity records"
        )

    if frame["Symbol"].duplicated().any():

        raise ValueError(
            "Duplicate equity symbols"
        )

    mapping = {

        "OPNPRIC": "Open",

        "HGHPRIC": "High",

        "LWPRIC": "Low",

        "CLSPRIC": "Close",

        "PRVSCLSGPRIC": "Previous Close",

        "TTLTRADGVOL": "Volume",

        "TTLTRFVAL": "Turnover"

    }

    result = pd.DataFrame({

        "Report Date": str(day),

        "Symbol": frame["Symbol"]

    })

    for original, output in mapping.items():

        result[output] = numeric(
            frame[original]
        )

    values = list(mapping.values())

    valid = (

        result[values]
        .notna().all(axis=1)

        &

        result[values]
        .apply(
            lambda s: s.map(math.isfinite)
        ).all(axis=1)

        &

        (result["Open"] > 0)

        &

        (result["Low"] > 0)

        &

        (result["Previous Close"] > 0)

        &

        (result["High"] >= result["Low"])

        &

        result["Close"].between(
            result["Low"],
            result["High"]
        )

        &

        result["Open"].between(
            result["Low"],
            result["High"]
        )

        &

        (result["Volume"] >= 0)

        &

        (result["Turnover"] >= 0)

    )

    result["Eligible For Analysis"] = valid

    result["Change %"] = (

        (

            result["Close"]

            / result["Previous Close"]

            - 1

        ) * 100

    ).where(valid)

    result["Source URL"] = url

    return result.reset_index(
        drop=True
    )


# =====================================
# DELIVERY INTELLIGENCE
# =====================================

def fetch_delivery(day, universe):

    url = (

        ARC

        + "/products/content/"

        + "sec_bhavdata_full_"

        + day.strftime("%d%m%Y")

        + ".csv"

    )

    frame = download_report(url)

    required = {

        "SYMBOL",

        "SERIES",

        "DATE1",

        "TTL_TRD_QNTY",

        "DELIV_QTY",

        "DELIV_PER"

    }

    require_columns(
        frame,
        required
    )

    dates = pd.to_datetime(

        frame["DATE1"],

        dayfirst=True,

        errors="coerce"

    ).dt.date

    if (
        frame.empty
        or not dates.eq(day).all()
    ):

        raise ValueError(
            "Delivery report date mismatch"
        )

    frame["Symbol"] = (

        frame["SYMBOL"].map(symbol)

    )

    frame = frame[

        frame["Symbol"].isin(universe)

        &

        frame["SERIES"]
        .astype(str)
        .str.strip()
        .eq("EQ")

    ].copy()

    if frame.empty:

        raise ValueError(
            "No F&O delivery records"
        )

    if frame["Symbol"].duplicated().any():

        raise ValueError(
            "Duplicate delivery symbols"
        )

    result = pd.DataFrame({

        "Report Date": str(day),

        "Symbol": frame["Symbol"],

        "Traded Quantity": numeric(
            frame["TTL_TRD_QNTY"]
        ),

        "Delivery Quantity": numeric(
            frame["DELIV_QTY"]
        ),

        "Delivery %": numeric(
            frame["DELIV_PER"]
        )

    })

    quantity = result["Traded Quantity"]

    delivered = result["Delivery Quantity"]

    percentage = result["Delivery %"]

    calculated = (

        delivered

        / quantity.where(quantity > 0)

        * 100

    )

    valid = (

        (quantity > 0)

        &

        (delivered >= 0)

        &

        (delivered <= quantity)

        &

        percentage.between(0, 100)

        &

        ((calculated - percentage).abs()
         <= 0.05)

    )

    result["Eligible For Analysis"] = valid

    result["Source URL"] = url

    return result.reset_index(
        drop=True
    )


# =====================================
# CORPORATE ANNOUNCEMENTS
# =====================================

def fetch_announcements(universe):

    url = NSE + "/api/corporate-announcements"

    end = now().date()

    start = end - timedelta(
        days=NEWS_LOOKBACK
    )

    payload = get_json(

        url,

        {

            "index": "equities",

            "from_date": start.strftime(
                "%d-%m-%Y"
            ),

            "to_date": end.strftime(
                "%d-%m-%Y"
            )

        }

    )

    rows = extract_rows(payload)

    records = []

    rejected = 0

    for item in rows:

        name = symbol(
            item.get("symbol", "")
        )

        if name not in universe:

            continue

        published = parse_timestamp(

            item.get("an_dt")

            or item.get("sort_date")

        )

        if (
            published is None
            or published.date() < start
            or published > now()
        ):

            rejected += 1

            continue

        heading = str(

            item.get("desc")

            or ""

        ).strip()

        details = str(

            item.get("attchmntText")

            or ""

        ).strip()

        if not heading and not details:

            rejected += 1

            continue

        text = (

            heading + " " + details

        ).lower()

        event_type = "OTHER"

        patterns = {

            "RESULTS": (

                "financial result",

                "quarterly result",

                "audited result"

            ),

            "BOARD MEETING": (

                "board meeting",

                "board of directors"

            ),

            "ORDER OR CONTRACT": (

                "order received",

                "award of order",

                "letter of award",

                "contract"

            ),

            "ACQUISITION": (

                "acquisition",

                "merger",

                "amalgamation"

            ),

            "REGULATORY": (

                "regulatory",

                "penalty",

                "litigation"

            ),

            "FUND RAISING": (

                "fund raising",

                "preferential issue",

                "qualified institutional"

            ),

            "CORPORATE ACTION": (

                "dividend",

                "bonus",

                "buyback",

                "stock split"

            )

        }

        for category, words in patterns.items():

            if any(
                word in text
                for word in words
            ):

                event_type = category

                break

        records.append({

            "Report Date":
                str(published.date()),

            "Symbol": name,

            "Published":
                published.isoformat(),

            "Heading": heading,

            "Details": details,

            "Event Type": event_type,

            "Attachment":
                str(item.get("attchmntFile") or ""),

            "Impact": "REVIEW REQUIRED",

            "Eligible For Analysis": True,

            "Source URL": url

        })

    columns = [

        "Report Date",

        "Symbol",

        "Published",

        "Heading",

        "Details",

        "Event Type",

        "Attachment",

        "Impact",

        "Eligible For Analysis",

        "Source URL"

    ]

    frame = pd.DataFrame(
        records,
        columns=columns
    )

    frame = frame.drop_duplicates(

        subset=[

            "Symbol",

            "Published",

            "Heading",

            "Attachment"

        ]

    )

    return frame, {

        "raw_records": len(rows),

        "rejected_records": rejected,

        "coverage_verified": False,

        "lookback_start": str(start),

        "lookback_end": str(end)

    }


# =====================================
# CORPORATE EVENTS / BOARD MEETINGS
# =====================================

def fetch_events(universe):

    url = NSE + "/api/corporate-board-meetings"

    today = now().date()

    end = today + timedelta(days=30)

    payload = get_json(

        url,

        {

            "index": "equities",

            "from_date": today.strftime(
                "%d-%m-%Y"
            ),

            "to_date": end.strftime(
                "%d-%m-%Y"
            )

        }

    )

    rows = extract_rows(payload)

    records = []

    rejected = 0

    for item in rows:

        name = symbol(
            item.get("symbol", "")
        )

        if name not in universe:

            continue

        event_date = parse_timestamp(

            item.get("bm_date")

            or item.get("meetingDate")

        )

        if (
            event_date is None
            or not today <= event_date.date() <= end
        ):

            rejected += 1

            continue

        records.append({

            "Symbol": name,

            "Event Date":
                str(event_date.date()),

            "Purpose": str(

                item.get("purpose")

                or item.get("bm_purpose")

                or ""

            ),

            "Details":
                str(item.get("bm_desc") or ""),

            "Event Type":
                "SCHEDULED BOARD MEETING",

            "Impact":
                "REVIEW REQUIRED",

            "Eligible For Analysis": True,

            "Source URL": url

        })

    frame = pd.DataFrame(

        records,

        columns=[

            "Symbol",

            "Event Date",

            "Purpose",

            "Details",

            "Event Type",

            "Impact",

            "Eligible For Analysis",

            "Source URL"

        ]

    ).drop_duplicates()

    return frame, {

        "raw_records": len(rows),

        "rejected_records": rejected,

        "coverage_verified": False

    }


# =====================================
# NSE PRE-MARKET INTELLIGENCE
# =====================================

def fetch_pre_market(universe):

    url = NSE + "/api/market-data-pre-open"

    payload = get_json(

        url,

        {"key": "FO"}

    )

    rows = extract_rows(payload)

    records = []

    rejected = 0

    for item in rows:

        metadata = item.get(
            "metadata", {}
        )

        detail = item.get(
            "detail", {}
        )

        if not isinstance(metadata, dict):

            rejected += 1

            continue

        if not isinstance(detail, dict):

            rejected += 1

            continue

        preopen = detail.get(
            "preOpenMarket", {}
        )

        if not isinstance(preopen, dict):

            rejected += 1

            continue

        name = symbol(
            metadata.get("symbol", "")
        )

        if name not in universe:

            continue

        timestamp = parse_timestamp(

            preopen.get("lastUpdateTime")

            or metadata.get("lastUpdateTime")

        )

        if timestamp is None:

            rejected += 1

            continue

        if (
            timestamp.date() != now().date()
            or timestamp > now()
        ):

            rejected += 1

            continue

        previous = pd.to_numeric(

            metadata.get("previousClose"),

            errors="coerce"

        )

        price = pd.to_numeric(

            preopen.get(

                "finalPrice",

                metadata.get("iep")

            ),

            errors="coerce"

        )

        valid = (

            pd.notna(previous)

            and pd.notna(price)

            and math.isfinite(previous)

            and math.isfinite(price)

            and previous > 0

            and price > 0

        )

        records.append({

            "Report Date":
                str(timestamp.date()),

            "Symbol": name,

            "Timestamp":
                timestamp.isoformat(),

            "Previous Close": previous,

            "Pre Open Price": price,

            "Gap %": (

                (price / previous - 1) * 100

                if valid

                else None

            ),

            "Buy Quantity":
                preopen.get("totalBuyQuantity"),

            "Sell Quantity":
                preopen.get("totalSellQuantity"),

            "Matched Quantity":
                preopen.get("totalTradedVolume"),

            "Eligible For Analysis": valid,

            "Source URL": url

        })

    if not records:

        raise ValueError(
            "No current dated pre-market records"
        )

    frame = pd.DataFrame(records)

    if frame["Symbol"].duplicated().any():

        raise ValueError(
            "Duplicate pre-market symbols"
        )

    return frame, {

        "raw_records": len(rows),

        "rejected_records": rejected,

        "coverage_verified": False

    }


# =====================================
# UPSTOX NEWS - FULL UNIVERSE
# =====================================

def fetch_company_news(
    universe,
    token
):

    if not token:

        raise ValueError(
            "UPSTOX_ACCESS_TOKEN not configured"
        )

    reverse = {

        key: name

        for name, key in universe.items()

    }

    keys = list(reverse)

    records = []

    errors = []

    complete_batches = 0

    attempted_batches = 0

    returned_keys = set()

    rejected = 0

    start = now() - timedelta(
        days=NEWS_LOOKBACK
    )

    for offset in range(
        0,
        len(keys),
        30
    ):

        batch = keys[
            offset:offset + 30
        ]

        attempted_batches += 1

        complete = False

        try:

            for page in range(1, 21):

                payload = get_json(

                    UP + "/v2/news",

                    {

                        "category":
                            "instrument_keys",

                        "instrument_keys":
                            ",".join(batch),

                        "page_number": page,

                        "page_size": 100

                    },

                    token

                )

                mapping = payload.get(
                    "data", {}
                )

                if (

                    payload.get("status")
                    != "success"

                    or not isinstance(
                        mapping, dict
                    )

                ):

                    raise ValueError(
                        "Invalid news response"
                    )

                for key, articles in mapping.items():

                    if key not in batch:

                        continue

                    if not isinstance(
                        articles, list
                    ):

                        rejected += 1

                        continue

                    returned_keys.add(key)

                    for article in articles:

                        if not isinstance(
                            article, dict
                        ):

                            rejected += 1

                            continue

                        published = parse_timestamp(

                            article.get(
                                "published_time"
                            )

                        )

                        if (

                            published is None

                            or published < start

                            or published > now()

                        ):

                            rejected += 1

                            continue

                        heading = str(

                            article.get("heading")

                            or ""

                        ).strip()

                        if not heading:

                            rejected += 1

                            continue

                        records.append({

                            "Report Date":
                                str(published.date()),

                            "Symbol": reverse[key],

                            "Instrument": key,

                            "Published":
                                published.isoformat(),

                            "Heading": heading,

                            "Summary": str(

                                article.get("summary")

                                or ""

                            ),

                            "URL": str(

                                article.get("article_link")

                                or ""

                            ),

                            "Impact":
                                "REVIEW REQUIRED",

                            "Eligible For Analysis":
                                True

                        })

                pages = payload.get(

                    "metadata", {}

                ).get(

                    "page", {}

                ).get(

                    "total_pages"

                )

                if pages is None:

                    break

                if page >= int(pages):

                    complete = True

                    break

            if complete:

                complete_batches += 1

            else:

                errors.append(

                    f"Batch {attempted_batches}: "

                    "Pagination unverified"

                )

        except Exception as error:

            errors.append(

                f"Batch {attempted_batches}: "

                + clean_error(error, token)

            )

    frame = pd.DataFrame(

        records,

        columns=[

            "Report Date",

            "Symbol",

            "Instrument",

            "Published",

            "Heading",

            "Summary",

            "URL",

            "Impact",

            "Eligible For Analysis"

        ]

    )

    frame = frame.drop_duplicates(

        subset=[

            "Symbol",

            "Heading",

            "Published"

        ]

    )

    coverage = (

        complete_batches == attempted_batches

        and len(returned_keys) == len(keys)

        and rejected == 0

    )

    return frame, {

        "requested_stocks": len(keys),

        "returned_instrument_keys":
            len(returned_keys),

        "attempted_batches":
            attempted_batches,

        "complete_batches":
            complete_batches,

        "rejected_records": rejected,

        "coverage_verified": coverage,

        "errors": errors

    }


# =====================================
# HISTORICAL COLLECTION
# =====================================

def collect_historical(
    source,
    day,
    fetcher,
    universe
):

    try:

        frame = fetcher(
            day,
            universe
        )

        eligible = frame[

            frame["Eligible For Analysis"]
            .eq(True)

        ]

        return save_snapshot(

            source,

            day,

            frame,

            {

                "universe_size":
                    len(universe),

                "covered_stocks":
                    frame["Symbol"].nunique(),

                "eligible_stocks":
                    eligible["Symbol"].nunique(),

                "coverage_verified": (

                    eligible["Symbol"].nunique()

                    == len(universe)

                )

            }

        )

    except Exception as error:

        return {

            "source": source,

            "report_date": str(day),

            "status": "FAILED",

            "records": None,

            "error": clean_error(error)

        }


# =====================================
# EVENT COLLECTION
# =====================================

def collect_current(
    source,
    fetcher,
    token=""
):

    try:

        frame, metadata = fetcher()

        if (

            source == "company_news"

            and frame.empty

            and metadata.get("errors")

        ):

            raise ValueError(

                " | ".join(
                    metadata["errors"]
                )

            )

        return save_snapshot(

            source,

            now().date(),

            frame,

            metadata

        )

    except Exception as error:

        return {

            "source": source,

            "status": "FAILED",

            "records": None,

            "error": clean_error(
                error,
                token
            )

        }


# =====================================
# DAILY MAIN ENGINE
# =====================================

def main():

    print(

        f"Stock Intelligence V{VERSION}",

        flush=True

    )

    token = os.environ.get(

        "UPSTOX_ACCESS_TOKEN",

        ""

    ).strip()

    results = []

    universe = get_universe()

    save_json(

        ROOT / "latest_universe.json",

        {

            "version": VERSION,

            "checked_at":
                now().isoformat(),

            "count": len(universe),

            "stocks": universe

        }

    )

    print(

        f"F&O Universe: {len(universe)}",

        flush=True

    )

    days = trading_days(
        HISTORY_DAYS
    )

    tasks = []

    for day in days:

        tasks.append((

            "cash_bhavcopy",

            day,

            fetch_cash_bhavcopy

        ))

        tasks.append((

            "delivery",

            day,

            fetch_delivery

        ))

    def run_task(task):

        source, day, fetcher = task

        output = collect_historical(

            source,

            day,

            fetcher,

            universe

        )

        print(
            output,
            flush=True
        )

        return output

    with ThreadPoolExecutor(

        max_workers=3

    ) as executor:

        results.extend(

            executor.map(
                run_task,
                tasks
            )

        )

    current_tasks = [

        (

            "announcements",

            lambda: fetch_announcements(
                universe
            )

        ),

        (

            "corporate_events",

            lambda: fetch_events(
                universe
            )

        ),

        (

            "pre_market",

            lambda: fetch_pre_market(
                universe
            )

        ),

        (

            "company_news",

            lambda: fetch_company_news(
                universe,
                token
            )

        )

    ]

    for source, fetcher in current_tasks:

        output = collect_current(

            source,

            fetcher,

            token

        )

        results.append(output)

        print(
            output,
            flush=True
        )

    latest_day = (

        str(days[0])

        if days

        else None

    )

    successful = {

        item["source"]

        for item in results

        if item["status"] in {

            "SAVED",

            "UNCHANGED"

        }

    }

    required = {

        "cash_bhavcopy",

        "delivery"

    }

    latest_ready = {

        item["source"]

        for item in results

        if (

            item["source"] in required

            and item["status"] in {

                "SAVED",

                "UNCHANGED"

            }

            and item.get("report_date")
            == latest_day

            and item.get("coverage_verified")
            is True

        )

    }

    supporting = {

        "announcements",

        "corporate_events",

        "pre_market",

        "company_news"

    }

    supporting_received = (

        supporting & successful

    )

    supporting_verified = {

        item["source"]

        for item in results

        if (

            item["source"] in supporting

            and item["status"] in {

                "SAVED",

                "UNCHANGED"

            }

            and item.get("coverage_verified")
            is True

        )

    }

    failed = [

        item

        for item in results

        if item["status"] == "FAILED"

    ]

    if (

        latest_ready == required

        and supporting_verified == supporting

        and not failed

    ):

        overall = "COMPLETE"

    elif latest_ready or successful:

        overall = "PARTIAL"

    else:

        overall = "FAILED"

    summary = {

        "version": VERSION,

        "collection_time":
            now().isoformat(),

        "overall_status": overall,

        "universe_size": len(universe),

        "latest_expected_report_date":
            latest_day,

        "historical_dates_requested":
            len(days),

        "required_sources":
            sorted(required),

        "latest_required_ready":
            sorted(latest_ready),

        "latest_required_missing":
            sorted(required - latest_ready),

        "supporting_sources_received":
            sorted(supporting_received),

        "supporting_sources_verified":
            sorted(supporting_verified),

        "supporting_sources_unverified":
            sorted(supporting - supporting_verified),

        "successful_sources":
            sorted(successful),

        "failed_attempts": len(failed),

        "results": results

    }

    save_json(

        DATA / "latest_stock_intelligence.json",

        summary

    )

    print(

        json.dumps(

            summary,

            indent=2,

            default=str

        ),

        flush=True

    )

    if overall == "FAILED":

        raise SystemExit(1)


if __name__ == "__main__":

    main()
