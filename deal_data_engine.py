
import io
from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd
import requests


VERSION = "1.1"

BASE = "https://nsearchives.nseindia.com"

IST = ZoneInfo("Asia/Kolkata")


def url_for(kind, day):

    if kind in ("bulk", "block"):

        return (
            f"{BASE}/content/equities/"
            f"{kind}.csv"
        )

    if kind == "short":

        return (
            f"{BASE}/archives/equities/"
            f"shortSelling/"
            f"shortselling_{day:%d%m%Y}.csv"
        )

    raise ValueError("Unknown report")


def parse_date(value):

    value = str(value).strip()

    formats = [
        "%d-%b-%Y",
        "%d-%b-%y",
        "%d-%m-%Y",
        "%d-%m-%y",
        "%d/%m/%Y",
        "%d/%m/%y",
        "%Y-%m-%d",
        "%d %b %Y",
        "%d-%B-%Y"
    ]

    for fmt in formats:

        try:

            return datetime.strptime(
                value,
                fmt
            ).date()

        except ValueError:

            continue

    raise ValueError(
        f"Invalid report date: {value}"
    )


def parse_report(raw, kind):

    d = pd.read_csv(
        io.BytesIO(raw),
        encoding="utf-8-sig",
        dtype=str,
        keep_default_na=False
    )

    d.columns = d.columns.str.strip()

    d = d.rename(columns={

        "Date": "Report Date",

        "Trade Date": "Report Date",

        "Symbol Name": "Symbol",

        "Quantity Traded": "Quantity",

        "Trade Price / Wght. Avg. Price": "Price",

        "Buy/Sell": "Side",

        "Client Name": "Client"

    })

    required = [
        "Report Date",
        "Symbol",
        "Quantity"
    ]

    if kind != "short":

        required += [
            "Price",
            "Side",
            "Client"
        ]

    missing = set(required) - set(d.columns)

    if missing:

        raise ValueError(
            f"Missing CSV columns: {missing}"
        )

    if d.empty:

        raise ValueError(
            "Empty CSV report"
        )

    d = d[required].copy()

    for c in required:

        d[c] = d[c].str.strip()

    d["Report Date"] = (
        d["Report Date"]
        .apply(parse_date)
    )

    d["Symbol"] = (
        d["Symbol"]
        .str.strip()
        .str.upper()
    )

    today = datetime.now(IST).date()

    if d["Report Date"].nunique() != 1:

        raise ValueError(
            "Mixed report dates"
        )

    if (d["Report Date"] > today).any():

        raise ValueError(
            "Future report date"
        )

    if (d["Symbol"] == "").any():

        raise ValueError(
            "Missing stock symbol"
        )

    numeric_columns = ["Quantity"]

    if kind != "short":

        numeric_columns.append("Price")

    for c in numeric_columns:

        d[c] = pd.to_numeric(

            d[c].str.replace(
                ",",
                "",
                regex=False
            ),

            errors="raise"

        )

        if not (

            (d[c] > 0)

            &

            (d[c] < float("inf"))

        ).all():

            raise ValueError(
                f"Invalid numeric value: {c}"
            )

    if (d.Quantity % 1 != 0).any():

        raise ValueError(
            "Fractional share quantity"
        )

    d["Quantity"] = (
        d["Quantity"].astype("int64")
    )

    if kind != "short":

        d["Side"] = (
            d.Side.str.strip().str.upper()
        )

        d["Client"] = (

            d.Client.str.upper()

            .str.replace(
                r"\s+",
                " ",
                regex=True
            )

        )

        if not d.Side.isin(
            ["BUY", "SELL"]
        ).all():

            raise ValueError(
                "Invalid deal side"
            )

        if (d.Client == "").any():

            raise ValueError(
                "Missing client name"
            )

        d["Value INR"] = (
            d.Quantity * d.Price
        )

        d["Value Crore"] = (
            d["Value INR"] / 10000000
        ).round(4)

    d = (
        d.drop_duplicates()
        .reset_index(drop=True)
    )

    d["Source"] = kind

    return d


def fetch_report(kind, day):

    url = url_for(kind, day)

    headers = {

        "User-Agent": "Mozilla/5.0",

        "Accept": "text/csv,*/*"

    }

    with requests.get(

        url,

        headers=headers,

        timeout=(8, 25),

        allow_redirects=False,

        stream=True

    ) as r:

        if r.status_code != 200:

            raise ValueError(
                f"HTTP {r.status_code}"
            )

        raw = bytearray()

        for chunk in r.iter_content(65536):

            raw.extend(chunk)

            if len(raw) > 20_000_000:

                raise ValueError(
                    "Report too large"
                )

    if not raw:

        raise ValueError(
            "Empty server response"
        )

    beginning = bytes(raw[:500]).lower()

    if (

        b"<html" in beginning

        or

        b"<!doctype html" in beginning

    ):

        raise ValueError(
            "Server returned HTML instead of CSV"
        )

    return parse_report(
        bytes(raw),
        kind
    )


def match_reports(frames, universe):

    if not frames:

        return pd.DataFrame()

    d = pd.concat(
        frames,
        ignore_index=True
    )

    symbols = {

        str(s).strip().upper()

        for s in universe

    }

    d = d[
        d.Symbol.isin(symbols)
    ].copy()

    d["Cross Report Match"] = False

    keys = [

        "Report Date",

        "Symbol",

        "Client",

        "Side",

        "Quantity",

        "Price"

    ]

    if {"bulk", "block"} <= set(d.Source):

        m = d.Source.isin(
            ["bulk", "block"]
        )

        d.loc[
            m,
            "Cross Report Match"
        ] = (

            d.loc[m]

            .groupby(
                keys,
                dropna=False
            )["Source"]

            .transform("nunique")

            .gt(1)

        )

    return d.reset_index(drop=True)
