from __future__ import annotations

from io import BytesIO
from math import floor
from typing import Any

import numpy as np
import pandas as pd
from google.cloud import storage

DATA_CONFIG = {
    "bucket_name": "clv_calculator_files",
    "blob_name": "Retention from AtScale.xlsx",
    "project": "gannett-datascience",
    "sheet_name": "Retention Drops",
}

MODEL_DEFAULTS: dict[str, int | float | None] = {
    "grace_days": 28,
    "pct_nonpay": 0.52,
    "blended_step_up": 24.0,
    "step_override": None,
    "cpa": None,
    "sales_lift": 0.0,
    "monthly_churn": 0.02,
}

LEGACY_OFFER_PRICES = {
    "Monthly Retention": 1.0,
    "Annual Retention": 8.25,
}
WEEKS_TO_MONTHS = 7 / (365.25 / 12)
FORECAST_WEEKS = 52 * 5
DROP_COLUMNS = ["Drop 1", "Drop 2", "Drop 3", "Drop 4"]
REQUIRED_COLUMNS = {"CM Site Name", "Offer", "Offer Length", *DROP_COLUMNS}


def load_gcs_bytes(bucket_name: str, blob_name: str, project: str) -> bytes:
    client = storage.Client(project=project)
    return client.bucket(bucket_name).blob(blob_name).download_as_bytes()


def read_source_bytes(data: bytes, blob_name: str, sheet_name: str) -> pd.DataFrame:
    stream = BytesIO(data)
    lower_name = blob_name.lower()
    if lower_name.endswith(".csv"):
        return pd.read_csv(stream)
    if lower_name.endswith(".xlsx"):
        return pd.read_excel(
            stream, engine="openpyxl", sheet_name=sheet_name, skiprows=1
        )
    if lower_name.endswith(".xlsb"):
        return pd.read_excel(stream, engine="pyxlsb", sheet_name=sheet_name, skiprows=1)
    if lower_name.endswith(".xls"):
        return pd.read_excel(stream, engine="xlrd", sheet_name=sheet_name, skiprows=1)
    raise ValueError(f"Unsupported source file type: {blob_name}")


def normalize_source_data(raw: pd.DataFrame) -> pd.DataFrame:
    """Normalize either the legacy retention worksheet or an already-tabular source."""
    if REQUIRED_COLUMNS.issubset(raw.columns):
        normalized = raw.copy()
        if "Intro Price" not in normalized.columns:
            normalized["Intro Price"] = normalized["Offer"].map(LEGACY_OFFER_PRICES)
        return _validate_source(normalized)

    if raw.shape[1] < 10:
        raise ValueError(
            "Retention Drops must contain the site, four monthly drops, and the annual drop block."
        )

    monthly = raw.iloc[:, :5].copy()
    monthly.columns = ["CM Site Name", *DROP_COLUMNS]
    monthly["Offer"] = "Monthly Retention"
    monthly["Offer Length"] = 4

    annual = raw.iloc[:, [0, 5, 6, 7, 8, 9]].copy()
    annual.columns = ["CM Site Name", "unused", *DROP_COLUMNS]
    annual = annual.drop(columns="unused")
    annual["Offer"] = "Annual Retention"
    annual["Offer Length"] = 52

    normalized = pd.concat([monthly, annual], ignore_index=True)
    normalized[DROP_COLUMNS] = -normalized[DROP_COLUMNS].apply(
        pd.to_numeric, errors="coerce"
    )
    normalized["Intro Price"] = normalized["Offer"].map(LEGACY_OFFER_PRICES)

    missing_sites = ["Packers News", "Hawk Central", "Tucson"]
    missing_retention_data = (
        pd.Series(missing_sites, name="CM Site Name")
        .to_frame()
        .merge(
            normalized[normalized["CM Site Name"] == "Total"].drop(
                "CM Site Name", axis=1
            ),
            how="cross",
        )
    )
    normalized = pd.concat([normalized, missing_retention_data], axis=0)

    return _validate_source(normalized)


def _validate_source(source: pd.DataFrame) -> pd.DataFrame:
    missing = REQUIRED_COLUMNS.difference(source.columns)
    if missing:
        raise ValueError(
            f"Source data is missing required columns: {', '.join(sorted(missing))}"
        )
    if "Intro Price" not in source.columns:
        raise ValueError(
            "Source data needs an Intro Price column or a supported legacy offer."
        )

    result = source.copy()
    result["CM Site Name"] = result["CM Site Name"].fillna("").astype(str).str.strip()
    result["Offer"] = result["Offer"].fillna("").astype(str).str.strip()
    result = result[(result["CM Site Name"] != "") & (result["Offer"] != "")].copy()
    if result.empty:
        raise ValueError("Source data contains no usable site and offer rows.")

    for column in [*DROP_COLUMNS, "Offer Length", "Intro Price"]:
        result[column] = pd.to_numeric(result[column], errors="coerce")
    numeric_columns = [*DROP_COLUMNS, "Offer Length", "Intro Price"]
    if result[numeric_columns].isna().any().any():
        raise ValueError(
            "Source data has missing or non-numeric prices, offer lengths, or drop values."
        )
    return result


def load_source(config: dict[str, str] | None = None) -> pd.DataFrame:
    settings = {**DATA_CONFIG, **(config or {})}
    data = load_gcs_bytes(
        settings["bucket_name"], settings["blob_name"], settings["project"]
    )
    raw = read_source_bytes(data, settings["blob_name"], settings["sheet_name"])
    return normalize_source_data(raw)


def available_options(source: pd.DataFrame) -> dict[str, list[str]]:
    missing = REQUIRED_COLUMNS.difference(source.columns)
    if missing:
        raise ValueError(
            f"Source data is missing required columns: {', '.join(sorted(missing))}"
        )
    return {
        "offers": sorted(source["Offer"].dropna().astype(str).unique()),
        "sites": ["Total"]
        + sorted(
            source["CM Site Name"][source["CM Site Name"] != "Total"]
            .dropna()
            .astype(str)
            .unique()
        ),
    }


def offer_price_map(source: pd.DataFrame) -> dict[str, dict[str, float]]:
    if "Intro Price" not in source.columns:
        return {}
    prices: dict[str, dict[str, float]] = {}
    for (site, offer), rows in source.groupby(["CM Site Name", "Offer"], sort=False):
        distinct = rows["Intro Price"].dropna().unique()
        if len(distinct) > 1:
            raise ValueError(f"Multiple intro prices found for {offer} at {site}.")
        if len(distinct) == 1:
            prices.setdefault(str(site), {})[str(offer)] = float(distinct[0])
    return prices


def get_offer_params(
    source: pd.DataFrame,
    site: str,
    offer: str,
    blended_step_up: float,
    intro_price: float | None = None,
) -> dict[str, Any]:
    matching = source[(source["CM Site Name"] == site) & (source["Offer"] == offer)]
    if matching.empty:
        raise ValueError(f"No source row found for offer '{offer}' at site '{site}'.")
    row = matching.iloc[0]
    adjustment = (24 - blended_step_up) * 0.25 / 100
    return {
        "offer_length": int(row["Offer Length"]),
        "intro_price": float(
            intro_price if intro_price is not None else row["Intro Price"]
        ),
        "loss_map": {
            "Intro": 0.0,
            "Step Up": float(row["Drop 1"]) - adjustment,
            "Grace1": float(row["Drop 2"]) - adjustment,
            "Second Payment": float(row["Drop 3"]),
            "Grace2": float(row["Drop 4"]) - 2 * adjustment,
            "Hold": 0.0,
        },
    }


def billing_flag(week: int, offer_length: int) -> str:
    interval = 52 if offer_length >= 52 else 4
    is_billing = week == offer_length or (
        week > offer_length and (week - offer_length) % interval == 0
    )
    return "Billing" if is_billing else "Non"


def timeline_state(schedule: list[str], offer_length: int, grace_weeks: int) -> str:
    billing_count = schedule.count("Billing")
    last = schedule[-1] if schedule else "Non"
    week = len(schedule)
    grace_weeks_at = {
        offer_length + grace_weeks,
        offer_length + grace_weeks + 1,
        2 * offer_length + grace_weeks,
        2 * offer_length + grace_weeks + 1,
    }

    if offer_length >= 52:
       
        if billing_count < 1:
            return "Intro"
        if billing_count == 1 and last == "Billing":
            return "Step Up"
        if billing_count in [1] and week in grace_weeks_at:
            return "Grace1"
        if billing_count in [2] and week in grace_weeks_at:
            return "Grace2"
        if billing_count == 2 and last == "Billing":
            return "Second Payment"
        if billing_count >= 2 and week > max(grace_weeks_at):
            return "Churn"
        return "Hold"

    if billing_count < 1:
        return "Intro"
    if billing_count == 1 and last == "Billing":
        return "Step Up"
    # if billing_count == 2 and last == "Billing":
    #     return "Grace"
    if billing_count == 2 and (last == "Billing" or schedule[-2] == "Billing"):
        return "Grace1"
    if billing_count == 3 and last == "Billing":
        return "Second Payment"
    if billing_count == 4 and (last == "Billing" or schedule[-2] == "Billing"):
        return "Grace2"
    if billing_count >= 4 and last in ["Non","Billing","Grace2"]:
        return "Churn"
    return "Hold"


def retention_delta(
    timeline: str,
    billing: str,
    week: int,
    offer_length: int,
    loss_map: dict[str, float],
) -> float:
    loss = loss_map[timeline]
    if timeline != "Churn":
        return loss
    if billing == "Non":
        return 0.0
    return loss / (floor((week - offer_length) / 52) + 1)


def compute_retention(previous: float | None, delta: float, timeline: str) -> float:
    if previous is None:
        return 1 - delta
    return max(previous - (delta / 2 if "Grace" in timeline else delta), 0)


def weekly_revenue_rate(
    timeline: str, intro_price: float, step_rate: float, override: float | None
) -> float:
    base = override if override is not None else step_rate
    return (intro_price if timeline == "Intro" else base) * WEEKS_TO_MONTHS


def non_pay_adjustment(
    week: int,
    offer_length: int,
    grace_days: int,
    revenue_history: list[float],
    delta: float,
    timeline: str,
    non_pay_share: float,
) -> float:
    if week < offer_length + grace_days / 7:
        return 0.0
    start = int((week - 1) - grace_days / 7)
    end = week - 1
    revenue_window = sum(revenue_history[start:end])
    factor = delta / 2 if "Grace" in timeline else delta
    return revenue_window * factor * non_pay_share


def run_model(config: dict[str, Any], source: pd.DataFrame) -> pd.DataFrame:
    values = {**MODEL_DEFAULTS, **config}

    options = available_options(source)
    if values.get("offer") not in options["offers"]:
        raise ValueError(f"Unknown offer: {values.get('offer')}")
    if values.get("site") not in options["sites"]:
        raise ValueError(f"Unknown site: {values.get('site')}")

    params = get_offer_params(
        source,
        values["site"],
        values["offer"],
        float(values["blended_step_up"]),
        values.get("intro_price"),
    )

    offer_length = params["offer_length"]
    intro_price = params["intro_price"]
    loss_map = {**params["loss_map"], "Churn": float(values["monthly_churn"])}
    schedule: list[str] = []
    retention_history: list[float] = []
    revenue_history: list[float] = []
    total_revenue_history: list[float] = []
    rows: list[dict[str, Any]] = []

    for week in range(1, FORECAST_WEEKS + 1):
        billing = billing_flag(week, offer_length)
        schedule.append(billing)
        timeline = timeline_state(
            schedule, offer_length, int(values["grace_days"]) // 7
        )
        delta = retention_delta(timeline, billing, week, offer_length, loss_map)
        previous = retention_history[-1] if retention_history else None
        retention = compute_retention(previous, delta, timeline)
        retention_history.append(retention)

        units = retention * (1 + float(values["sales_lift"]))
        rate = weekly_revenue_rate(
            timeline,
            intro_price,
            float(values["blended_step_up"]),
            values.get("step_override"),
        )
        gross = units * rate
        revenue_history.append(gross)
        contra = non_pay_adjustment(
            week,
            offer_length,
            int(values["grace_days"]),
            revenue_history,
            delta,
            timeline,
            float(values["pct_nonpay"]),
        )
        net = gross - contra
        cpa_adjustment = -float(values["cpa"]) if values.get("cpa") else 0.0
        total = (
            net + cpa_adjustment
            if week == 1
            else total_revenue_history[-1] + net + cpa_adjustment
        )
        total_revenue_history.append(total)

        rows.append(
            {
                "Site": values["site"],
                "Year": (week - 1) // 52 + 1,
                "Week": f"Week {(week - 1) % 52 + 1:02d}",
                "timeline": timeline,
                "Billing Flag": billing,
                "Retention": retention,
                "Units": units,
                "Weekly Rate": rate,
                "Gross Incremental Revenue": gross,
                "Non Pay Contra": contra,
                "Net Incremental Revenue": net,
                "CPA": values.get("cpa"),
                "Total Net Revenue": total,
            }
        )
    return pd.DataFrame(rows)
