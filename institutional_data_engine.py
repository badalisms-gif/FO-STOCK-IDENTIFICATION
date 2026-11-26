
import io
import re
import json
import math
import requests
import pandas as pd

from datetime import datetime, date, timedelta
from lxml import html
from concurrent.futures import ThreadPoolExecutor

from daily_collector import DATA, now, save_json

from futures_intelligence_engine import (
    download,
    normalize,
    collect_source
)

VERSION = "1.5.3"

ARCHIVE = "https://nsearchives.nseindia.com"

HOLIDAYS = {
    date(2026, 11, 24)
}

HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Accept": "*/*"
}

CATEGORIES = {"FII", "DII", "CLIENT", "PRO"}

MAPPING = {
    "futureindexlong": "Index Futures Long",
    "futureindexshort": "Index Futures Short",
    "futurestocklong": "Stock Futures Long",
    "futurestockshort": "Stock Futures Short",
    "optionindexcalllong": "Index Call Long",
    "optionindexcallshort": "Index Call Short",
    "optionindexputlong": "Index Put Long",
    "optionindexputshort": "Index Put Short",
    "optionstockcalllong": "Stock Call Long",
    "optionstockcallshort": "Stock Call Short",
    "optionstockputlong": "Stock Put Long",
    "optionstockputshort": "Stock Put Short"
}

SUCCESS = {"SAVED", "UNCHANGED"}


# =====================================
# DATE VALIDATION
# =====================================

def parse_date(value):

    value = str(value).strip()
    value = value.replace("Sept", "Sep")

    formats = [
        "%Y-%m-%d",
        "%d-%m-%Y",
        "%d/%m/%Y",
        "%d%m%Y",
        "%Y%m%d",
        "%d-%b-%Y",
        "%d-%B-%Y",
        "%d-%b-%y",
        "%d %b %Y",
        "%d %B %Y",
        "%d %b %y",
        "%d/%b/%Y",
        "%b %d, %Y",
        "%B %d, %Y",
        "%b %d %Y",
        "%B %d %Y"
    ]

    for fmt in formats:
        try:
            return datetime.strptime(
                value, fmt
            ).date()
        except ValueError:
            pass

    raise ValueError(
        f"Invalid date: {value}"
    )


def verify_date(title, day):

    patterns = [
        r"\b\d{4}-\d{2}-\d{2}\b",
        r"\b\d{1,2}[-/]\d{1,2}[-/]\d{4}\b",
        r"\b\d{8}\b",
        r"\b\d{1,2}[- /][A-Za-z]{3,9}[- /]\d{2,4}\b",
        r"\b[A-Za-z]{3,9}\s+\d{1,2},?\s+\d{4}\b"
    ]

    detected = set()

    for pattern in patterns:

        for token in re.findall(pattern, title):

            try:
                detected.add(parse_date(token))
            except ValueError:
                pass

    if day not in detected:

        raise ValueError(
            f"Report date unmatched: "
            f"{day}; header={title[:300]}"
        )


# =====================================
# NUMERIC VALIDATION
# =====================================

def strict_numeric(series):

    values = pd.to_numeric(
        series.astype(str)
        .str.replace(",", "", regex=False)
        .str.strip(),
        errors="coerce"
    )

    if values.isna().any():

        raise ValueError(
            "Missing or invalid numeric value"
        )

    if not values.map(math.isfinite).all():

        raise ValueError(
            "Non-finite numeric value"
        )

    return values


# =====================================
# PARTICIPANT OI
# =====================================

def fetch_participant_oi(day):

    url = (
        f"{ARCHIVE}/content/nsccl/"
        f"fao_participant_oi_"
        f"{day.strftime('%d%m%Y')}.csv"
    )

    raw = download(url)

    text = raw.decode("utf-8-sig")

    lines = text.splitlines()

    header = next(
        (
            i for i, line in enumerate(lines)
            if "clienttype" in normalize(line)
        ),
        None
    )

    if header is None:

        raise ValueError(
            "Participant header missing"
        )

    title = " ".join(lines[:header])

    verify_date(title, day)

    frame = pd.read_csv(
        io.StringIO(text),
        skiprows=header,
        index_col=False,
        dtype=str
    )

    frame.columns = [
        normalize(c)
        for c in frame.columns
    ]

    required = {
        "clienttype",
        *MAPPING.keys()
    }

    if not required <= set(frame.columns):

        raise ValueError(
            f"Missing fields: "
            f"{required - set(frame.columns)}"
        )

    frame["clienttype"] = (
        frame["clienttype"]
        .fillna("")
        .str.strip()
        .str.upper()
    )

    totals = frame[
        frame["clienttype"].isin(
            ["TOTAL", "TOTALS", "GRAND TOTAL"]
        )
    ].copy()

    if len(totals) != 1:

        raise ValueError(
            "Official TOTAL row missing or duplicated"
        )

    participants = frame[
        frame["clienttype"].isin(CATEGORIES)
    ].copy()

    if (
        len(participants) != 4
        or set(participants["clienttype"]) != CATEGORIES
    ):

        raise ValueError(
            "Participant categories incomplete"
        )

    participants = participants.reset_index(
        drop=True
    )

    result = pd.DataFrame({
        "Report Date": str(day),
        "Participant": participants["clienttype"]
    })

    official_totals = {}

    for original, output in MAPPING.items():

        values = strict_numeric(
            participants[original]
        )

        official = strict_numeric(
            totals[original]
        ).iloc[0]

        if (
            (values < 0).any()
            or official < 0
        ):

            raise ValueError(
                f"Negative OI: {output}"
            )

        if (
            (values % 1 != 0).any()
            or official % 1 != 0
        ):

            raise ValueError(
                f"Non-integer OI: {output}"
            )

        calculated = int(values.sum())
        official = int(official)

        if calculated != official:

            raise ValueError(
                f"Official column total mismatch: "
                f"{output}; calculated={calculated}; "
                f"official={official}"
            )

        result[output] = values.astype("int64")

        official_totals[output] = official

    segments = [
        "Index Futures",
        "Stock Futures",
        "Index Call",
        "Index Put",
        "Stock Call",
        "Stock Put"
    ]

    warnings = []

    for segment in segments:

        long_total = official_totals[
            segment + " Long"
        ]

        short_total = official_totals[
            segment + " Short"
        ]

        difference = long_total - short_total

        result[
            segment + " Market Imbalance"
        ] = difference

        if difference != 0:

            warnings.append({
                "segment": segment,
                "official_long": long_total,
                "official_short": short_total,
                "difference": difference
            })

    result["Net Index Futures"] = (
        result["Index Futures Long"]
        - result["Index Futures Short"]
    )

    result["Net Stock Futures"] = (
        result["Stock Futures Long"]
        - result["Stock Futures Short"]
    )

    result["Column Totals Verified"] = True

    result["Market Balance Verified"] = (
        len(warnings) == 0
    )

    result["Validation Status"] = (
        "VERIFIED_WITH_WARNING"
        if warnings
        else "VERIFIED"
    )

    result["Validation Warning"] = json.dumps(
        warnings,
        ensure_ascii=False
    )

    result["Eligible For Analysis"] = (
        len(warnings) == 0
    )

    if warnings:

        print(
            "Official report imbalance:",
            json.dumps(warnings),
            flush=True
        )

    return result, url


# =====================================
# FII DII CASH VALIDATION
# =====================================

def validate_cash(rows, day, scope):

    frame = pd.DataFrame(rows)

    required = [
        "Report Date",
        "Category",
        "Buy Cr",
        "Sell Cr",
        "Net Cr"
    ]

    if not set(required) <= set(frame.columns):

        raise ValueError(
            "FII DII columns incomplete"
        )

    frame = frame[required].copy()

    if len(frame) != 2:

        raise ValueError(
            "Expected two participant rows"
        )

    frame["Category"] = (
        frame["Category"]
        .astype(str)
        .str.strip()
        .str.upper()
        .replace({
            "FII": "FII/FPI",
            "FPI": "FII/FPI",
            "FII / FPI": "FII/FPI"
        })
    )

    if set(frame["Category"]) != {
        "FII/FPI", "DII"
    }:

        raise ValueError(
            "Unexpected participant category"
        )

    dates = frame[
        "Report Date"
    ].map(parse_date)

    if not dates.eq(day).all():

        raise ValueError(
            f"Stale cash report: "
            f"{dates.tolist()}; expected={day}"
        )

    frame["Report Date"] = str(day)

    for column in [
        "Buy Cr",
        "Sell Cr",
        "Net Cr"
    ]:

        frame[column] = strict_numeric(
            frame[column]
        )

    if (
        frame[["Buy Cr", "Sell Cr"]] < 0
    ).any().any():

        raise ValueError(
            "Negative cash trade value"
        )

    difference = (
        frame["Buy Cr"]
        - frame["Sell Cr"]
        - frame["Net Cr"]
    ).abs()

    if (difference > 0.05).any():

        raise ValueError(
            "Cash net value mismatch"
        )

    verified = scope == "NSE+BSE+MSEI"

    frame["Market Scope"] = scope

    frame["Numeric Validation"] = True

    frame["Date Validation"] = True

    frame["Scope Verified"] = verified

    frame["Validation Status"] = (
        "VERIFIED"
        if verified
        else "SCOPE_UNVERIFIED"
    )

    frame["Eligible For Analysis"] = verified

    return frame


# =====================================
# CASH TABLE NORMALIZATION
# =====================================

def normalize_cash_table(table):

    table = table.copy()

    if isinstance(
        table.columns,
        pd.MultiIndex
    ):

        table.columns = [
            " ".join(map(str, col))
            for col in table.columns
        ]

    mapping = {}

    for col in table.columns:

        key = normalize(col)

        if "category" in key:

            mapping[col] = "Category"

        elif "date" in key:

            mapping[col] = "Report Date"

        elif "buyvalue" in key:

            mapping[col] = "Buy Cr"

        elif "sellvalue" in key:

            mapping[col] = "Sell Cr"

        elif "netvalue" in key:

            mapping[col] = "Net Cr"

    table = table.rename(
        columns=mapping
    )

    required = {
        "Category",
        "Report Date",
        "Buy Cr",
        "Sell Cr",
        "Net Cr"
    }

    if not required <= set(table.columns):

        return None

    table["Category"] = (
        table["Category"]
        .astype(str)
        .str.strip()
        .str.upper()
    )

    return table[
        table["Category"].isin([
            "FII",
            "FPI",
            "FII/FPI",
            "FII / FPI",
            "DII"
        ])
    ].copy()


# =====================================
# VERIFIED COMBINED MARKET TABLE
# =====================================

def combined_cash_tables(content, day):

    document = html.fromstring(content)

    valid_frames = []

    for element in document.xpath("//table"):

        headings = element.xpath(
            "preceding::*["
            "self::h1 or self::h2 or "
            "self::h3 or self::h4 or "
            "self::h5 or self::h6"
            "][1]"
        )

        context = ""

        if headings:

            context = headings[0].text_content()

        context += " " + " ".join(
            element.xpath(".//caption//text()")
        )

        context = context.upper()

        combined = all(
            re.search(
                rf"\b{token}\b",
                context
            )
            for token in ["NSE", "BSE"]
        ) and bool(
            re.search(r"\bMSEI?\b", context)
        )

        if not combined:

            continue

        if "EXCLUSIVE" in context:

            continue

        tables = pd.read_html(
            io.StringIO(
                html.tostring(
                    element,
                    encoding="unicode"
                )
            )
        )

        for table in tables:

            normalized = normalize_cash_table(
                table
            )

            if normalized is None:

                continue

            try:

                frame = validate_cash(
                    normalized.to_dict("records"),
                    day,
                    "NSE+BSE+MSEI"
                )

                valid_frames.append(frame)

            except ValueError:

                continue

    if len(valid_frames) != 1:

        raise ValueError(
            "Unambiguous combined cash table "
            "not found"
        )

    return valid_frames[0]


# =====================================
# OFFICIAL COMBINED CASH SOURCE
# =====================================

def fetch_fii_dii(day):

    sources = [
        "https://www.nseindia.in/reports/fii-dii",
        "https://www.nseindia.com/reports/fii-dii"
    ]

    def attempt(url):

        try:

            response = requests.get(
                url,
                headers=HEADERS,
                timeout=(5, 10)
            )

            response.raise_for_status()

            frame = combined_cash_tables(
                response.content,
                day
            )

            frame["Source URL"] = url

            return frame, url, ""

        except Exception as error:

            return None, url, str(error)

    errors = []

    with ThreadPoolExecutor(
        max_workers=2
    ) as executor:

        results = list(
            executor.map(attempt, sources)
        )

    for frame, url, error in results:

        if frame is not None:

            return frame, url

        errors.append(
            f"{url}: {error}"
        )

    raise ValueError(
        "Combined cash scope not verified: "
        + " | ".join(errors)
    )


# =====================================
# NSE JSON CASH DATA
# SEPARATE UNVERIFIED STORAGE
# =====================================

def fetch_cash_candidate(day):

    hosts = [
        "https://www.nseindia.com",
        "https://www.nseindia.in"
    ]

    def attempt(host):

        url = host + "/api/fiidiiTradeReact"

        try:

            response = requests.get(
                url,
                headers={
                    **HEADERS,
                    "Accept": "application/json",
                    "Referer": host + "/"
                },
                timeout=(5, 12)
            )

            response.raise_for_status()

            payload = response.json()

            if not isinstance(payload, list):

                raise ValueError(
                    "Unexpected JSON response"
                )

            rows = []

            for item in payload:

                rows.append({
                    "Report Date": item["date"],
                    "Category": item["category"],
                    "Buy Cr": item["buyValue"],
                    "Sell Cr": item["sellValue"],
                    "Net Cr": item["netValue"]
                })

            frame = validate_cash(
                rows,
                day,
                "UNVERIFIED_EXCHANGE_SCOPE"
            )

            frame["Source URL"] = url

            return frame, url, payload, ""

        except Exception as error:

            return None, url, None, str(error)

    with ThreadPoolExecutor(
        max_workers=2
    ) as executor:

        results = list(
            executor.map(attempt, hosts)
        )

    valid = [
        item for item in results
        if item[0] is not None
    ]

    if not valid:

        raise ValueError(
            "NSE cash API unavailable: "
            + " | ".join(
                f"{item[1]}: {item[3]}"
                for item in results
            )
        )

    columns = [
        "Category",
        "Buy Cr",
        "Sell Cr",
        "Net Cr"
    ]

    reference = (
        valid[0][0][columns]
        .sort_values("Category")
        .reset_index(drop=True)
    )

    for item in valid[1:]:

        other = (
            item[0][columns]
            .sort_values("Category")
            .reset_index(drop=True)
        )

        if not reference.equals(other):

            raise ValueError(
                "NSE cash API sources disagree"
            )

    frame, url, payload, _ = valid[0]

    frame["Matching API Hosts"] = len(valid)

    save_json(
        DATA
        / "logs"
        / str(day)
        / "cash_json_candidate.json",
        {
            "version": VERSION,
            "source": url,
            "date": str(day),
            "status": "SCOPE_UNVERIFIED",
            "eligible_for_analysis": False,
            "matching_hosts": len(valid),
            "raw_payload": payload,
            "records": frame.to_dict("records")
        }
    )

    print(
        "NSE cash data received; "
        "date and numeric validation passed; "
        "exchange scope remains unverified.",
        flush=True
    )

    return frame, url


# =====================================
# SAFE COLLECTION WRAPPER
# =====================================

def safe_collect(source, day, fetcher):

    try:

        result = collect_source(
            source,
            day,
            fetcher
        )

        return result

    except Exception as error:

        return {
            "source": source,
            "status": "FAILED",
            "records": None,
            "requested_date": str(day),
            "actual_date": None,
            "error": str(error)
        }


# =====================================
# DAILY COLLECTION
# =====================================

def main():

    today = now().date()

    results = []

    print(
        f"Institutional Engine V{VERSION}",
        flush=True
    )

    trading_days = []

    for offset in range(7):

        day = today - timedelta(
            days=offset
        )

        if (
            day.weekday() >= 5
            or day in HOLIDAYS
        ):

            print(
                day,
                "MARKET CLOSED",
                flush=True
            )

            continue

        trading_days.append(day)

        result = safe_collect(
            "participant_oi",
            day,
            fetch_participant_oi
        )

        results.append(result)

        print(result, flush=True)

    latest_day = (
        trading_days[0]
        if trading_days
        else None
    )

    if latest_day:

        result = safe_collect(
            "fii_dii",
            latest_day,
            fetch_fii_dii
        )

        results.append(result)

        print(result, flush=True)

        if result["status"] not in SUCCESS:

            candidate = safe_collect(
                "fii_dii_unverified",
                latest_day,
                fetch_cash_candidate
            )

            candidate["scope_verified"] = False

            candidate["eligible_for_analysis"] = False

            results.append(candidate)

            print(candidate, flush=True)

    successful = sorted({

        r["source"]

        for r in results

        if r["status"] in SUCCESS

    })

    expected = {
        "participant_oi",
        "fii_dii"
    }

    missing = sorted(
        expected - set(successful)
    )

    latest_missing = []

    for source in expected:

        found = any(

            r["source"] == source

            and r["status"] in SUCCESS

            and str(
                r.get("actual_date")
            ) == str(latest_day)

            for r in results

        )

        if not found:

            latest_missing.append(source)

    candidate_available = (
        "fii_dii_unverified" in successful
    )

    latest_oi_available = (
        "participant_oi" not in latest_missing
    )

    combined_available = (
        "fii_dii" not in latest_missing
    )

    if latest_oi_available and combined_available:

        overall = "COMPLETE"

    elif latest_oi_available or candidate_available:

        overall = "PARTIAL"

    else:

        overall = "FAILED"

    summary = {
        "version": VERSION,

        "collection_time":
            now().isoformat(),

        "latest_expected_date":
            str(latest_day),

        "overall_status":
            overall,

        "successful_sources":
            successful,

        "missing_sources":
            missing,

        "latest_missing_sources":
            sorted(latest_missing),

        "participant_oi_latest_available":
            latest_oi_available,

        "combined_cash_verified":
            combined_available,

        "unverified_cash_available":
            candidate_available,

        "failed_attempts": sum(
            r["status"] == "FAILED"
            for r in results
        ),

        "results":
            results
    }

    save_json(
        DATA /
        "latest_institutional_collection.json",
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
