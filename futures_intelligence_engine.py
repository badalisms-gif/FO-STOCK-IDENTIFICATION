
import io
import re
import zipfile
import requests
import pandas as pd
import numpy as np

from datetime import datetime, timedelta
from daily_collector import (
    DATA,
    now,
    save_snapshot,
    save_json
)

VERSION = "1.5"

ARCHIVE = "https://nsearchives.nseindia.com"
NSE = "https://www.nseindia.com"

HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Accept": "*/*"
}


def download(url):

    r = requests.get(
        url,
        headers=HEADERS,
        timeout=(10, 40)
    )

    r.raise_for_status()

    if len(r.content) > 100_000_000:
        raise ValueError("File too large")

    return r.content


def numeric(series):

    result = pd.to_numeric(
        series.astype(str)
        .str.replace(",", "", regex=False)
        .str.strip(),
        errors="coerce"
    )

    if result.isna().any():
        raise ValueError("Invalid numeric data")

    if not np.isfinite(result).all():
        raise ValueError("Non-finite values")

    return result


# =====================================
# STOCK FUTURES BHAVCOPY
# =====================================

def fetch_futures(day):

    date_code = day.strftime("%Y%m%d")

    url = (
        f"{ARCHIVE}/content/fo/"
        f"BhavCopy_NSE_FO_0_0_0_"
        f"{date_code}_F_0000.csv.zip"
    )

    raw = download(url)

    with zipfile.ZipFile(
        io.BytesIO(raw)
    ) as archive:

        files = [
            f for f in archive.infolist()
            if f.filename.lower().endswith(".csv")
        ]

        if len(files) != 1:
            raise ValueError("Unexpected ZIP contents")

        if files[0].file_size > 100_000_000:
            raise ValueError("CSV too large")

        frame = pd.read_csv(
            archive.open(files[0])
        )

    frame.columns = (
        frame.columns.str.strip().str.upper()
    )

    required = {
        "TRADDT",
        "FININSTRMTP",
        "TCKRSYMB",
        "XPRYDT",
        "CLSPRIC",
        "OPNINTRST",
        "TTLTRADGVOL"
    }

    if not required <= set(frame.columns):
        raise ValueError(
            f"Missing columns: "
            f"{required - set(frame.columns)}"
        )

    dates = pd.to_datetime(
        frame["TRADDT"],
        errors="coerce"
    )

    if not dates.dt.date.eq(day).all():
        raise ValueError("Bhavcopy date mismatch")

    frame = frame[
        frame["FININSTRMTP"]
        .astype(str)
        .str.strip()
        .str.upper()
        .isin(["STF", "FUTSTK"])
    ].copy()

    if frame.empty:
        raise ValueError("No stock futures found")

    result = pd.DataFrame({

        "Report Date": str(day),

        "Symbol": (
            frame["TCKRSYMB"]
            .astype(str)
            .str.strip()
            .str.upper()
        ),

        "Expiry": pd.to_datetime(
            frame["XPRYDT"],
            errors="raise"
        ).dt.strftime("%Y-%m-%d"),

        "Close": numeric(frame["CLSPRIC"]),

        "OI": numeric(frame["OPNINTRST"]),

        "Volume": numeric(
            frame["TTLTRADGVOL"]
        )

    })

    if (
        (result["OI"] < 0).any()
        or (result["Volume"] < 0).any()
        or (result["Close"] < 0).any()
    ):
        raise ValueError("Invalid futures values")

    if result.duplicated(
        ["Symbol", "Expiry"]
    ).any():
        raise ValueError("Duplicate futures contracts")

    return result, url


# =====================================
# PARTICIPANT WISE OPEN INTEREST
# =====================================

def normalize(text):

    return re.sub(
        r"[^a-z0-9]",
        "",
        str(text).lower()
    )


def fetch_participant_oi(day):

    code = day.strftime("%d%m%Y")

    url = (
        f"{ARCHIVE}/content/nsccl/"
        f"fao_participant_oi_{code}.csv"
    )

    raw = download(url)

    text = raw.decode(
        "utf-8-sig",
        errors="strict"
    )

    lines = text.splitlines()

    header = None

    for index, line in enumerate(lines):

        if "clienttype" in normalize(line):
            header = index
            break

    if header is None:
        raise ValueError("Participant header missing")

    title = " ".join(lines[:header])

    date_tokens = re.findall(
        r"\d{2}[-/]\d{2}[-/]\d{4}"
        r"|\d{8}",
        title
    )

    confirmed = False

    for token in date_tokens:

        for fmt in (
            "%d-%m-%Y",
            "%d/%m/%Y",
            "%d%m%Y"
        ):

            try:
                parsed = datetime.strptime(
                    token, fmt
                ).date()

                if parsed == day:
                    confirmed = True

            except ValueError:
                pass

    if not confirmed:
        raise ValueError(
            "Participant report date unverified"
        )

    df = pd.read_csv(
        io.StringIO(text),
        skiprows=header
    )

    df.columns = [
        normalize(c) for c in df.columns
    ]

    columns = {
        "clienttype",
        "futureindexlong",
        "futureindexshort",
        "futurestocklong",
        "futurestockshort"
    }

    if not columns <= set(df.columns):
        raise ValueError(
            "Participant columns missing"
        )

    df["clienttype"] = (
        df["clienttype"]
        .astype(str)
        .str.strip()
        .str.upper()
    )

    df = df[
        df["clienttype"].isin(
            ["FII", "DII", "CLIENT", "PRO"]
        )
    ].copy()

    if (
        len(df) != 4
        or df["clienttype"].nunique() != 4
    ):
        raise ValueError(
            "Participant categories incomplete"
        )

    result = pd.DataFrame({

        "Report Date": str(day),

        "Participant": df["clienttype"],

        "Index Futures Long": numeric(
            df["futureindexlong"]
        ),

        "Index Futures Short": numeric(
            df["futureindexshort"]
        ),

        "Stock Futures Long": numeric(
            df["futurestocklong"]
        ),

        "Stock Futures Short": numeric(
            df["futurestockshort"]
        )
    })

    position_columns = [
        "Index Futures Long",
        "Index Futures Short",
        "Stock Futures Long",
        "Stock Futures Short"
    ]

    if (result[position_columns] < 0).any().any():
        raise ValueError("Negative position count")

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
# FII DII CASH MARKET
# =====================================

def fetch_fii_dii(day):

    url = f"{NSE}/api/fiidiiTradeReact"

    with requests.Session() as session:

        session.headers.update(HEADERS)

        session.get(
            NSE,
            timeout=(10, 20)
        ).raise_for_status()

        response = session.get(
            url,
            timeout=(10, 30)
        )

        response.raise_for_status()

        payload = response.json()

    if not isinstance(payload, list):
        raise ValueError("Invalid FII DII response")

    rows = []

    for item in payload:

        actual = pd.to_datetime(
            item["date"],
            dayfirst=True,
            errors="raise"
        ).date()

        if actual != day:
            raise ValueError(
                f"FII DII report date: {actual}"
            )

        rows.append({

            "Report Date": str(actual),

            "Category": str(
                item["category"]
            ).strip().upper(),

            "Buy Cr": item["buyValue"],

            "Sell Cr": item["sellValue"],

            "Net Cr": item["netValue"]

        })

    frame = pd.DataFrame(rows)

    if frame.empty:
        raise ValueError("Empty FII DII report")

    if (
        len(frame) != 2
        or "DII" not in set(frame["Category"])
        or not frame["Category"].str.contains(
            "FII|FPI", regex=True
        ).any()
    ):
        raise ValueError("Missing FII DII categories")

    for column in [
        "Buy Cr", "Sell Cr", "Net Cr"
    ]:
        frame[column] = numeric(
            frame[column]
        )

    if (
        frame[["Buy Cr", "Sell Cr"]] < 0
    ).any().any():
        raise ValueError("Invalid trade values")

    difference = (
        frame["Buy Cr"]
        - frame["Sell Cr"]
        - frame["Net Cr"]
    ).abs()

    if (difference > 0.05).any():
        raise ValueError("FII DII net value mismatch")

    return frame, url


# =====================================
# SAVE VERIFIED REPORTS
# =====================================

def collect_source(kind, day, fetcher):

    try:

        frame, url = fetcher(day)

        folder = (
            DATA / "nse" / kind / str(day)
        )

        status, digest = save_snapshot(

            folder,

            frame,

            {
                "source": kind,
                "report_date": str(day),
                "origin": url,
                "engine_version": VERSION
            }
        )

        return {
            "source": kind,
            "status": status,
            "records": len(frame),
            "actual_date": str(day),
            "sha256": digest,
            "error": ""
        }

    except Exception as error:

        return {
            "source": kind,
            "status": "FAILED",
            "records": None,
            "requested_date": str(day),
            "error": str(error)
        }


# =====================================
# FUTURES OI ANALYSIS
# =====================================

def analyse_futures(current, previous):

    current = current.copy()
    previous = previous.copy()

    current["Expiry"] = pd.to_datetime(
        current["Expiry"]
    )

    previous["Expiry"] = pd.to_datetime(
        previous["Expiry"]
    )

    rows = []

    symbols = (
        set(current["Symbol"])
        & set(previous["Symbol"])
    )

    for symbol in sorted(symbols):

        a = current[
            current["Symbol"] == symbol
        ]

        b = previous[
            previous["Symbol"] == symbol
        ]

        old_oi = b["OI"].sum()
        new_oi = a["OI"].sum()

        if old_oi <= 0:
            continue

        oi_change = (
            (new_oi - old_oi)
            / old_oi * 100
        )

        common = a.merge(
            b,
            on="Expiry",
            suffixes=("_new", "_old")
        ).sort_values("Expiry")

        common = common[
            (common["Close_new"] > 0)
            & (common["Close_old"] > 0)
            & (common["Volume_new"] > 0)
            & (common["Volume_old"] > 0)
        ]

        if common.empty:
            continue

        contract = common.iloc[0]

        price_change = (
            (
                contract["Close_new"]
                / contract["Close_old"]
            ) - 1
        ) * 100

        expiry_change = (
            set(a["Expiry"])
            != set(b["Expiry"])
        )

        if expiry_change:

            activity = "ROLLOVER REVIEW"

        elif price_change > 0 and oi_change > 0:

            activity = "LONG BUILDUP"

        elif price_change < 0 and oi_change > 0:

            activity = "SHORT BUILDUP"

        elif price_change > 0 and oi_change < 0:

            activity = "SHORT COVERING"

        elif price_change < 0 and oi_change < 0:

            activity = "LONG UNWINDING"

        else:

            activity = "NEUTRAL"

        rows.append({

            "Symbol": symbol,

            "Price Change %": round(
                price_change, 2
            ),

            "Previous Total OI": old_oi,

            "Current Total OI": new_oi,

            "OI Change %": round(
                oi_change, 2
            ),

            "Total Futures Volume": (
                a["Volume"].sum()
            ),

            "Compared Expiry": (
                str(contract["Expiry"].date())
            ),

            "Activity": activity

        })

    result = pd.DataFrame(rows)

    if not result.empty:

        result = result.sort_values(
            "OI Change %",
            key=lambda x: x.abs(),
            ascending=False
        ).reset_index(drop=True)

    return result


# =====================================
# DAILY COLLECTION
# =====================================

def main():

    today = now().date()

    results = []

    for offset in range(7):

        day = today - timedelta(days=offset)

        if day.weekday() >= 5:
            continue

        for kind, fetcher in [

            ("futures", fetch_futures),

            (
                "participant_oi",
                fetch_participant_oi
            )

        ]:

            result = collect_source(
                kind, day, fetcher
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

    available = sorted({

        r["source"]

        for r in results

        if r["status"] in [
            "SAVED", "UNCHANGED"
        ]

    })

    missing = sorted(

        {
            "futures",
            "participant_oi",
            "fii_dii"
        } - set(available)

    )

    summary = {

        "version": VERSION,

        "collection_time": now().isoformat(),

        "successful_sources": available,

        "missing_sources": missing,

        "results": results

    }

    save_json(

        DATA / "latest_futures_collection.json",

        summary

    )

    save_json(

        DATA / "logs" / str(today)
        / (
            "futures_"
            + now().strftime("%H%M%S_%f")
            + ".json"
        ),

        summary

    )

    print(summary)

    if missing:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
