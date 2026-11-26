
import io
import re
import requests
import pandas as pd

from datetime import datetime, date, timedelta

from daily_collector import DATA, now, save_json

from futures_intelligence_engine import (
    download,
    numeric,
    normalize,
    collect_source
)

VERSION = "1.5.1"

ARCHIVE = "https://nsearchives.nseindia.com"

HOLIDAYS = {
    date(2026, 11, 24)
}

HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Accept": "*/*"
}


# =====================================
# PARTICIPANT REPORT DATE VALIDATION
# =====================================

def verify_date(title, day):

    patterns = [
        (
            r"\b\d{1,2}[-/]\d{1,2}[-/]\d{4}\b",
            ["%d-%m-%Y", "%d/%m/%Y"]
        ),
        (
            r"\b\d{4}-\d{2}-\d{2}\b",
            ["%Y-%m-%d"]
        ),
        (
            r"\b\d{8}\b",
            ["%d%m%Y", "%Y%m%d"]
        ),
        (
            r"\b\d{1,2}[- /][A-Za-z]{3,9}[- /]\d{2,4}\b",
            [
                "%d-%b-%Y",
                "%d-%B-%Y",
                "%d %b %Y",
                "%d %B %Y",
                "%d/%b/%Y",
                "%d-%b-%y",
                "%d %b %y"
            ]
        ),
        (
            r"\b[A-Za-z]{3,9}\s+\d{1,2},?\s+\d{4}\b",
            [
                "%b %d, %Y",
                "%B %d, %Y",
                "%b %d %Y",
                "%B %d %Y"
            ]
        )
    ]

    detected = set()

    for pattern, formats in patterns:

        for token in re.findall(pattern, title):

            for fmt in formats:

                try:

                    detected.add(
                        datetime.strptime(
                            token, fmt
                        ).date()
                    )

                except ValueError:
                    pass

    if day not in detected:

        raise ValueError(
            f"Report date unmatched. "
            f"Expected={day}; "
            f"Header={title[:300]}"
        )


# =====================================
# PARTICIPANT WISE OI
# =====================================

def fetch_participant_oi(day):

    code = day.strftime("%d%m%Y")

    url = (
        f"{ARCHIVE}/content/nsccl/"
        f"fao_participant_oi_{code}.csv"
    )

    raw = download(url)

    text = raw.decode("utf-8-sig")

    lines = text.splitlines()

    header = None

    for index, line in enumerate(lines):

        if "clienttype" in normalize(line):

            header = index
            break

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

        index_col=False

    )

    frame.columns = [
        normalize(c)
        for c in frame.columns
    ]

    required = {

        "clienttype",
        "futureindexlong",
        "futureindexshort",
        "futurestocklong",
        "futurestockshort"

    }

    if not required <= set(frame.columns):

        raise ValueError(
            f"Missing columns: "
            f"{required - set(frame.columns)}"
        )

    frame["clienttype"] = (

        frame["clienttype"]
        .astype(str)
        .str.strip()
        .str.upper()

    )

    categories = {
        "FII", "DII", "CLIENT", "PRO"
    }

    frame = frame[
        frame["clienttype"].isin(categories)
    ].copy()

    if (
        len(frame) != 4
        or set(frame["clienttype"]) != categories
    ):

        raise ValueError(
            "Participant categories incomplete"
        )

    result = pd.DataFrame({

        "Report Date": str(day),

        "Participant": frame["clienttype"]

    })

    mapping = {

        "futureindexlong":
            "Index Futures Long",

        "futureindexshort":
            "Index Futures Short",

        "futurestocklong":
            "Stock Futures Long",

        "futurestockshort":
            "Stock Futures Short",

        "optionindexcalllong":
            "Index Call Long",

        "optionindexcallshort":
            "Index Call Short",

        "optionindexputlong":
            "Index Put Long",

        "optionindexputshort":
            "Index Put Short",

        "optionstockcalllong":
            "Stock Call Long",

        "optionstockcallshort":
            "Stock Call Short",

        "optionstockputlong":
            "Stock Put Long",

        "optionstockputshort":
            "Stock Put Short"

    }

    for original, output in mapping.items():

        if original not in frame.columns:

            raise ValueError(
                f"Missing field: {original}"
            )

        result[output] = numeric(
            frame[original]
        )

        if (result[output] < 0).any():

            raise ValueError(
                f"Negative OI: {output}"
            )

    for segment in [
        "Index Futures",
        "Stock Futures",
        "Index Call",
        "Index Put",
        "Stock Call",
        "Stock Put"
    ]:

        long_total = result[
            segment + " Long"
        ].sum()

        short_total = result[
            segment + " Short"
        ].sum()

        if long_total != short_total:

            raise ValueError(
                f"OI totals mismatch: {segment}"
            )

    result["Net Index Futures"] = (

        result["Index Futures Long"]

        - result["Index Futures Short"]

    )

    result["Net Stock Futures"] = (

        result["Stock Futures Long"]

        - result["Stock Futures Short"]

    )

    return result, url


# =====================================
# FII DII VALIDATION
# =====================================

def validate_cash(rows, day):

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
            "FPI": "FII/FPI"

        })

    )

    if set(frame["Category"]) != {

        "FII/FPI", "DII"

    }:

        raise ValueError(
            "Unexpected category"
        )

    dates = pd.to_datetime(

        frame["Report Date"],

        dayfirst=True,

        errors="raise"

    )

    if not dates.dt.date.eq(day).all():

        raise ValueError(
            f"Stale report: {dates.tolist()}"
        )

    frame["Report Date"] = str(day)

    for column in [
        "Buy Cr",
        "Sell Cr",
        "Net Cr"
    ]:

        frame[column] = numeric(
            frame[column]
        )

    if (
        frame[["Buy Cr", "Sell Cr"]] < 0
    ).any().any():

        raise ValueError(
            "Negative trade values"
        )

    difference = (

        frame["Buy Cr"]

        - frame["Sell Cr"]

        - frame["Net Cr"]

    ).abs()

    if (difference > 0.05).any():

        raise ValueError(
            "Net cash value mismatch"
        )

    frame["Market Scope"] = (
        "NSE+BSE+MSEI"
    )

    return frame


# =====================================
# OFFICIAL NSE CASH REPORT
# =====================================

def fetch_fii_dii(day):

    errors = []

    for host in [

        "https://www.nseindia.in",

        "https://nseindia.in",

        "https://www.nse.in"

    ]:

        url = (
            host + "/reports/fii-dii"
        )

        try:

            response = requests.get(

                url,

                headers=HEADERS,

                timeout=(10, 30)

            )

            response.raise_for_status()

            tables = pd.read_html(

                io.StringIO(response.text)

            )

            combined = []

            for table in tables:

                if isinstance(
                    table.columns,
                    pd.MultiIndex
                ):

                    table.columns = [
                        " ".join(map(str, col))
                        for col in table.columns
                    ]

                names = {}

                for col in table.columns:

                    key = normalize(col)

                    if "category" in key:

                        names[col] = "Category"

                    elif "date" in key:

                        names[col] = "Report Date"

                    elif "buyvalue" in key:

                        names[col] = "Buy Cr"

                    elif "sellvalue" in key:

                        names[col] = "Sell Cr"

                    elif "netvalue" in key:

                        names[col] = "Net Cr"

                table = table.rename(
                    columns=names
                )

                required = {

                    "Category",
                    "Report Date",
                    "Buy Cr",
                    "Sell Cr",
                    "Net Cr"

                }

                if required <= set(
                    table.columns
                ):

                    combined.append(table)

            if len(combined) != 2:

                raise ValueError(
                    "Expected NSE exclusive and "
                    "combined market tables"
                )

            # Official page lists NSE-only
            # first and combined exchanges second.

            frame = validate_cash(

                combined[1].to_dict("records"),

                day

            )

            return frame, url

        except Exception as error:

            errors.append(
                f"{host}: {error}"
            )

    raise ValueError(

        "Official cash report unavailable: "

        + " | ".join(errors)

    )


# =====================================
# COLLECTION
# =====================================

def main():

    today = now().date()

    results = []

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
                "MARKET CLOSED"
            )

            continue

        result = collect_source(

            "participant_oi",

            day,

            fetch_participant_oi

        )

        results.append(result)

        print(result)

    result = collect_source(

        "fii_dii",

        today,

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

    summary = {

        "version": VERSION,

        "collection_time":

            now().isoformat(),

        "successful_sources":

            successful,

        "missing_sources":

            missing,

        "results":

            results

    }

    save_json(

        DATA /
        "latest_institutional_collection.json",

        summary

    )

    print(summary)

    if missing:

        raise SystemExit(1)


if __name__ == "__main__":

    main()
