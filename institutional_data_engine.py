
import io
import re
import json
import math
import requests
import pandas as pd

from datetime import datetime, date, timedelta
from concurrent.futures import ThreadPoolExecutor

from daily_collector import DATA, now, save_json

from futures_intelligence_engine import (
    download,
    normalize,
    collect_source
)

VERSION = "1.5.4"

ARCHIVE = "https://nsearchives.nseindia.com"

HOLIDAYS = {
    date(2026, 11, 24)
}

HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Accept": "*/*"
}

CATEGORIES = {
    "FII", "DII", "CLIENT", "PRO"
}

SUCCESS = {
    "SAVED", "UNCHANGED"
}

OI_TOLERANCE = 1

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

SEGMENTS = [
    "Index Futures",
    "Stock Futures",
    "Index Call",
    "Index Put",
    "Stock Call",
    "Stock Put"
]


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

        for token in re.findall(
            pattern, title
        ):

            try:

                detected.add(
                    parse_date(token)
                )

            except ValueError:

                pass

    if day not in detected:

        raise ValueError(
            f"Report date unmatched: "
            f"{day}; header={title[:300]}"
        )


# =====================================
# STRICT NUMERIC VALIDATION
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
# PARTICIPANT WISE OI
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

    if frame.columns.duplicated().any():

        raise ValueError(
            "Duplicate report columns"
        )

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

        frame["clienttype"].isin([

            "TOTAL",

            "TOTALS",

            "GRAND TOTAL"

        ])

    ].copy()

    if len(totals) != 1:

        raise ValueError(
            "Official TOTAL row missing or duplicated"
        )

    participants = frame[

        frame["clienttype"].isin(
            CATEGORIES
        )

    ].copy()

    if (

        len(participants) != 4

        or set(
            participants["clienttype"]
        ) != CATEGORIES

    ):

        raise ValueError(
            "Participant categories incomplete"
        )

    participants = participants.reset_index(
        drop=True
    )

    result = pd.DataFrame({

        "Report Date": str(day),

        "Participant":
            participants["clienttype"]

    })

    official_totals = {}

    calculated_totals = {}

    column_warnings = []

    market_warnings = []

    # =================================
    # COLUMN TOTAL RECONCILIATION
    # =================================

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

        difference = calculated - official

        if abs(difference) > OI_TOLERANCE:

            raise ValueError(

                f"Official column total mismatch: "

                f"{output}; "

                f"calculated={calculated}; "

                f"official={official}; "

                f"difference={difference}"

            )

        if difference != 0:

            column_warnings.append({

                "type": "COLUMN_TOTAL",

                "field": output,

                "calculated": calculated,

                "official": official,

                "difference": difference

            })

        result[output] = values.astype(
            "int64"
        )

        official_totals[output] = official

        calculated_totals[output] = calculated

        result[
            output + " Official Total"
        ] = official

        result[
            output + " Column Difference"
        ] = difference

    # =================================
    # MARKET LONG SHORT RECONCILIATION
    # =================================

    for segment in SEGMENTS:

        long_total = official_totals[
            segment + " Long"
        ]

        short_total = official_totals[
            segment + " Short"
        ]

        difference = long_total - short_total

        participant_difference = (

            calculated_totals[
                segment + " Long"
            ]

            - calculated_totals[
                segment + " Short"
            ]

        )

        result[
            segment + " Market Imbalance"
        ] = difference

        result[
            segment + " Participant Imbalance"
        ] = participant_difference

        if (

            difference != 0

            or participant_difference != 0

        ):

            market_warnings.append({

                "type": "MARKET_BALANCE",

                "segment": segment,

                "official_long": long_total,

                "official_short": short_total,

                "difference": difference,

                "participant_difference":
                    participant_difference

            })

        segment_eligible = (

            abs(difference) <= OI_TOLERANCE

            and

            abs(participant_difference)
            <= OI_TOLERANCE

        )

        result[
            segment + " Eligible"
        ] = segment_eligible

    # =================================
    # NET PARTICIPANT POSITIONS
    # =================================

    result["Net Index Futures"] = (

        result["Index Futures Long"]

        - result["Index Futures Short"]

    )

    result["Net Stock Futures"] = (

        result["Stock Futures Long"]

        - result["Stock Futures Short"]

    )

    result["Net Index Calls"] = (

        result["Index Call Long"]

        - result["Index Call Short"]

    )

    result["Net Index Puts"] = (

        result["Index Put Long"]

        - result["Index Put Short"]

    )

    result["Net Stock Calls"] = (

        result["Stock Call Long"]

        - result["Stock Call Short"]

    )

    result["Net Stock Puts"] = (

        result["Stock Put Long"]

        - result["Stock Put Short"]

    )

    warnings = (

        column_warnings

        + market_warnings

    )

    market_eligible = all(

        abs(item["difference"])
        <= OI_TOLERANCE

        and

        abs(item["participant_difference"])
        <= OI_TOLERANCE

        for item in market_warnings

    )

    result["Column Totals Verified"] = (
        len(column_warnings) == 0
    )

    result["Column Totals Within Tolerance"] = True

    result["Market Balance Verified"] = (
        len(market_warnings) == 0
    )

    result["Market Balance Within Tolerance"] = (
        market_eligible
    )

    result["OI Tolerance"] = OI_TOLERANCE

    if not market_eligible:

        validation_status = (
            "STORAGE_ONLY"
        )

    elif warnings:

        validation_status = (
            "VERIFIED_WITH_WARNING"
        )

    else:

        validation_status = "VERIFIED"

    result["Validation Status"] = (
        validation_status
    )

    result["Validation Warning"] = json.dumps(

        warnings,

        ensure_ascii=False

    )

    result["Eligible For Analysis"] = (
        market_eligible
    )

    result["Source URL"] = url

    print(

        json.dumps({

            "date": str(day),

            "source": "participant_oi",

            "validation_status":
                validation_status,

            "eligible_for_analysis":
                market_eligible,

            "warnings": warnings

        }),

        flush=True

    )

    return result, url


# =====================================
# FII DII CASH VALIDATION
# =====================================

def validate_cash(rows, day):

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

            f"{dates.tolist()}; "

            f"expected={day}"

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

        frame[
            ["Buy Cr", "Sell Cr"]
        ] < 0

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

    frame["Market Scope"] = (
        "UNVERIFIED_EXCHANGE_SCOPE"
    )

    frame["Numeric Validation"] = True

    frame["Date Validation"] = True

    frame["Scope Verified"] = False

    frame["Validation Status"] = (
        "SCOPE_UNVERIFIED"
    )

    frame["Eligible For Analysis"] = False

    return frame


# =====================================
# NSE CASH API
# OPTIONAL SUPPORTING DATA
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

                    "Report Date":
                        item["date"],

                    "Category":
                        item["category"],

                    "Buy Cr":
                        item["buyValue"],

                    "Sell Cr":
                        item["sellValue"],

                    "Net Cr":
                        item["netValue"]

                })

            frame = validate_cash(
                rows, day
            )

            frame["Source URL"] = url

            return frame, url, payload, ""

        except Exception as error:

            return (

                None,

                url,

                None,

                str(error)

            )

    with ThreadPoolExecutor(

        max_workers=2

    ) as executor:

        results = list(

            executor.map(
                attempt, hosts
            )

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

            "records":
                frame.to_dict("records")

        }

    )

    return frame, url


# =====================================
# SAFE COLLECTION WRAPPER
# =====================================

def safe_collect(source, day, fetcher):

    captured = {}

    def wrapped(requested):

        frame, url = fetcher(requested)

        captured["validation_status"] = (

            sorted(

                frame["Validation Status"]

                .astype(str)

                .unique()

                .tolist()

            )

        )

        captured["eligible_for_analysis"] = (

            bool(

                frame[
                    "Eligible For Analysis"
                ].all()

            )

        )

        if "Validation Warning" in frame.columns:

            captured["validation_warnings"] = (

                frame[
                    "Validation Warning"
                ].iloc[0]

            )

        return frame, url

    try:

        result = collect_source(

            source,

            day,

            wrapped

        )

        if result.get("status") in SUCCESS:

            result.update(captured)

        return result

    except Exception as error:

        return {

            "source": source,

            "status": "FAILED",

            "records": None,

            "requested_date": str(day),

            "actual_date": None,

            "eligible_for_analysis": False,

            "error": str(error)

        }


# =====================================
# DAILY COLLECTION
# =====================================

def main():

    today = now().date()

    results = []

    trading_days = []

    print(

        f"Institutional Engine V{VERSION}",

        flush=True

    )

    # Last seven calendar days.

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

    # FII DII is optional.
    # No BSE or MSEI dependency.

    if latest_day:

        candidate = safe_collect(

            "fii_dii_unverified",

            latest_day,

            fetch_cash_candidate

        )

        candidate["scope_verified"] = False

        candidate["eligible_for_analysis"] = False

        candidate["optional_source"] = True

        results.append(candidate)

        print(candidate, flush=True)

    successful = sorted({

        r["source"]

        for r in results

        if r["status"] in SUCCESS

    })

    latest_oi = next(

        (

            r for r in results

            if r["source"] == "participant_oi"

            and r["status"] in SUCCESS

            and str(
                r.get("actual_date")
            ) == str(latest_day)

        ),

        None

    )

    latest_oi_available = (
        latest_oi is not None
    )

    latest_oi_eligible = (

        latest_oi_available

        and latest_oi.get(
            "eligible_for_analysis",
            False
        )

    )

    candidate_available = any(

        r["source"] == "fii_dii_unverified"

        and r["status"] in SUCCESS

        and str(
            r.get("actual_date")
        ) == str(latest_day)

        for r in results

    )

    historical_failures = [

        r for r in results

        if r["source"] == "participant_oi"

        and r["status"] == "FAILED"

    ]

    if (

        latest_oi_eligible

        and not historical_failures

    ):

        overall = "COMPLETE"

    elif (

        latest_oi_available

        or "participant_oi" in successful

    ):

        overall = "PARTIAL"

    else:

        overall = "FAILED"

    missing = (

        []

        if "participant_oi" in successful

        else ["participant_oi"]

    )

    latest_missing = (

        []

        if latest_oi_available

        else ["participant_oi"]

    )

    summary = {

        "version": VERSION,

        "collection_time":
            now().isoformat(),

        "latest_expected_date":
            str(latest_day),

        "overall_status":
            overall,

        "required_sources": [

            "participant_oi"

        ],

        "optional_sources": [

            "fii_dii_unverified"

        ],

        "successful_sources":
            successful,

        "missing_sources":
            missing,

        "latest_missing_sources":
            latest_missing,

        "participant_oi_latest_available":
            latest_oi_available,

        "participant_oi_latest_eligible":
            bool(latest_oi_eligible),

        "combined_cash_required":
            False,

        "combined_cash_verified":
            False,

        "unverified_cash_available":
            candidate_available,

        "cash_eligible_for_analysis":
            False,

        "historical_oi_failed_attempts":
            len(historical_failures),

        "failed_attempts": sum(

            r["status"] == "FAILED"

            for r in results

        ),

        "results":
            results

    }

    save_json(

        DATA

        / "latest_institutional_collection.json",

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
