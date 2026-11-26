
import io
import re
import json
import math
import requests
import pandas as pd

from datetime import datetime, date, timedelta
from lxml import html

from daily_collector import DATA, now, save_json

from futures_intelligence_engine import (
    download,
    normalize,
    collect_source
)

VERSION = "1.5.2"

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

    print("Participant title:", title)

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

    if warnings:

        print(
            "Official report imbalance:",
            json.dumps(warnings)
        )

    return result, url


# =====================================
# FII DII CASH VALIDATION
# =====================================

def validate_cash(rows, day, scope):

    frame = pd.DataFrame(rows)

    required = {
        "Report Date",
        "Category",
        "Buy Cr",
        "Sell Cr",
        "Net Cr"
    }

    if not required <= set(frame.columns):

        raise ValueError(
            "FII DII columns incomplete"
        )

    frame = frame[
        [
            "Report Date",
            "Category",
            "Buy Cr",
            "Sell Cr",
            "Net Cr"
        ]
    ].copy()

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

    frame["Market Scope"] = scope
    frame["Validation Status"] = "VERIFIED"

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

    table = table[
        table["Category"].isin([
            "FII",
            "FPI",
            "FII/FPI",
            "FII / FPI",
            "DII"
        ])
    ].copy()

    return table


# =====================================
# FIND COMBINED MARKET TABLES
# =====================================

def combined_cash_tables(content, day, mse=False):

    document = html.fromstring(content)

    all_frames = []

    for element in document.xpath("//table"):

        heading = element.xpath(
            "preceding::*["
            "self::h1 or self::h2 or "
            "self::h3 or self::h4 or "
            "self::h5 or self::h6"
            "][1]"
        )

        context = ""

        if heading:

            context = heading[0].text_content()

        context += " " + " ".join(
            element.xpath(".//caption//text()")
        )

        context = context.upper()

        combined = all(
            token in context
            for token in ["NSE", "BSE", "MSE"]
        )

        if not combined and not mse:

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

            if normalized is not None:

                if not normalized.empty:

                    all_frames.append(normalized)

    if not all_frames:

        raise ValueError(
            "Combined cash tables not found"
        )

    merged = pd.concat(
        all_frames,
        ignore_index=True
    ).drop_duplicates()

    required = [
        "Report Date",
        "Category",
        "Buy Cr",
        "Sell Cr",
        "Net Cr"
    ]

    merged = merged[
        required
    ].drop_duplicates()

    return validate_cash(
        merged.to_dict("records"),
        day,
        "NSE+BSE+MSEI"
    )


# =====================================
# OFFICIAL CASH DATA SOURCES
# =====================================

def fetch_fii_dii(day):

    errors = []

    sources = [
        (
            "https://www.nseindia.in/reports/fii-dii",
            False
        ),
        (
            "https://nseindia.in/reports/fii-dii",
            False
        ),
        (
            "https://www.nseindia.com/reports/fii-dii",
            False
        ),
        (
            "https://beta.msei.in/downloads/"
            "equity-reports/fii-dii-activities",
            True
        ),
        (
            "https://beta.mseindia.com/markets/"
            "markets-information/"
            "fii-dii-trading-activities",
            True
        )
    ]

    for url, mse in sources:

        try:

            response = requests.get(
                url,
                headers=HEADERS,
                timeout=(10, 25)
            )

            response.raise_for_status()

            frame = combined_cash_tables(
                response.content,
                day,
                mse=mse
            )

            frame["Source URL"] = url

            return frame, url

        except Exception as error:

            errors.append(
                f"{url}: {error}"
            )

    # Alternative NSE JSON endpoint.
    # Scope must be verified independently
    # before treating this as combined activity.

    for host in [
        "https://www.nseindia.com",
        "https://www.nseindia.in"
    ]:

        url = host + "/api/fiidiiTradeReact"

        try:

            response = requests.get(
                url,
                headers={
                    **HEADERS,
                    "Accept": "application/json",
                    "Referer": host + "/"
                },
                timeout=(10, 20)
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

            # Save separately for diagnostics.
            # Do not mix into combined cash history.

            save_json(
                DATA
                / "logs"
                / str(day)
                / "cash_json_candidate.json",
                {
                    "source": url,
                    "date": str(day),
                    "status": "SCOPE_UNVERIFIED",
                    "records": frame.to_dict(
                        "records"
                    )
                }
            )

            errors.append(
                f"{url}: valid data but "
                "exchange scope unverified"
            )

        except Exception as error:

            errors.append(
                f"{url}: {error}"
            )

    raise ValueError(
        "Official combined cash report unavailable: "
        + " | ".join(errors)
    )


# =====================================
# COLLECTION
# =====================================

def main():

    today = now().date()

    results = []

    print(
        f"Institutional Engine V{VERSION}"
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

            print(day, "MARKET CLOSED")
            continue

        trading_days.append(day)

        result = collect_source(
            "participant_oi",
            day,
            fetch_participant_oi
        )

        results.append(result)
        print(result)

    if trading_days:

        cash_day = trading_days[0]

        result = collect_source(
            "fii_dii",
            cash_day,
            fetch_fii_dii
        )

        results.append(result)
        print(result)

    successful = sorted({

        r["source"]

        for r in results

        if r["status"] in [
            "SAVED",
            "UNCHANGED"
        ]

    })

    missing = sorted({
        "participant_oi",
        "fii_dii"
    } - set(successful))

    failed_attempts = [
        r for r in results
        if r["status"] == "FAILED"
    ]

    latest_day = (
        trading_days[0]
        if trading_days else None
    )

    latest_missing = []

    for source in [
        "participant_oi",
        "fii_dii"
    ]:

        found = any(

            r["source"] == source

            and r["status"] in [
                "SAVED",
                "UNCHANGED"
            ]

            and str(
                r.get("actual_date")
            ) == str(latest_day)

            for r in results

        )

        if not found:

            latest_missing.append(source)

    summary = {
        "version": VERSION,
        "collection_time": now().isoformat(),
        "latest_expected_date": str(latest_day),
        "successful_sources": successful,
        "missing_sources": missing,
        "latest_missing_sources": latest_missing,
        "failed_attempts": len(failed_attempts),
        "results": results
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
        )
    )

    if missing or latest_missing:

        raise SystemExit(1)


if __name__ == "__main__":

    main()
