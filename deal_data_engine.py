
import io
from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd
import requests

BASE = "https://nsearchives.nseindia.com"


def url_for(kind, day):

    if kind in ("bulk", "block"):
        return f"{BASE}/content/equities/{kind}.csv"

    if kind == "short":
        return (
            f"{BASE}/archives/equities/shortSelling/"
            f"shortselling_{day:%d%m%Y}.csv"
        )

    raise ValueError("Unknown report")


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

    if not set(required) <= set(d.columns) or d.empty:
        raise ValueError(
            "Empty or unexpected CSV schema"
        )

    d = d[required].copy()

    for c in required:
        d[c] = d[c].str.strip()

    d["Report Date"] = pd.to_datetime(
        d["Report Date"],
        format="%d-%b-%y",
        errors="raise"
    ).dt.date

    d["Symbol"] = d["Symbol"].str.upper()

    today = datetime.now(
        ZoneInfo("Asia/Kolkata")
    ).date()

    if (
        d["Report Date"].nunique() != 1
        or (d["Report Date"] > today).any()
    ):
        raise ValueError(
            "Mixed or future report date"
        )

    if (d["Symbol"] == "").any():
        raise ValueError("Missing symbol")

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
            & (d[c] < float("inf"))
        ).all():
            raise ValueError(
                "Invalid numeric value"
            )

    if (d.Quantity % 1 != 0).any():
        raise ValueError(
            "Fractional quantity"
        )

    if kind != "short":

        d["Side"] = d.Side.str.upper()

        d["Client"] = (
            d.Client.str.upper()
            .str.replace(
                r"\s+",
                " ",
                regex=True
            )
        )

        if (
            not d.Side.isin(
                ["BUY", "SELL"]
            ).all()
            or (d.Client == "").any()
        ):
            raise ValueError(
                "Invalid side/client"
            )

        d["Value INR"] = (
            d.Quantity * d.Price
        )

    d = d.drop_duplicates().reset_index(
        drop=True
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

    return parse_report(
        bytes(raw),
        kind
    )


def match_reports(frames, universe):

    d = pd.concat(
        frames,
        ignore_index=True
    )

    d = d[
        d.Symbol.isin(set(universe))
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

    return d
