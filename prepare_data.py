"""Validate, clean, convert to UTC, and aggregate BTS monthly data.

Run without arguments for January 2023 through December 2025.
Use --start and --end for a smaller, inclusive month range.
"""

from pathlib import Path
import argparse
import zipfile

import airportsdata
import pandas as pd

BASE_DIR = Path(__file__).resolve().parent

FLIGHT_KEY = [
    "FlightDate",
    "Reporting_Airline",
    "Flight_Number_Reporting_Airline",
    "Origin",
    "CRSDepTime",
]

REQUIRED_COLUMNS = [
    *FLIGHT_KEY,
    "Dest",
    "Tail_Number",
    "CRSArrTime",
    "CRSElapsedTime",
    "DepDelay",
    "ArrDelay",
    "DepDel15",
    "Cancelled",
    "Diverted",
]


def audit_month(df, year, month):
    missing_columns = sorted(set(REQUIRED_COLUMNS) - set(df.columns))
    if missing_columns:
        raise ValueError(f"Missing columns: {missing_columns}")

    dates = pd.to_datetime(df["FlightDate"], errors="coerce")
    expected_dates = pd.date_range(
        f"{year}-{month:02d}-01",
        periods=pd.Period(f"{year}-{month:02d}").days_in_month,
        freq="D",
    )
    actual_dates = pd.DatetimeIndex(dates.dropna().dt.normalize().unique())

    completed = df.loc[
        df["Cancelled"].eq(0) & df["Diverted"].eq(0)
    ]

    unnamed = [
        column for column in df.columns
        if column.startswith("Unnamed:")
    ]

    result = {
        "month": f"{year}-{month:02d}",
        "status": "ok",
        "rows": len(df),
        "columns": len(df.columns),
        "missing_calendar_days": len(expected_dates.difference(actual_dates)),
        "invalid_or_missing_dates": int(dates.isna().sum()),
        "rows_outside_month": int(
            (
                dates.notna()
                & (
                    dates.dt.year.ne(year)
                    | dates.dt.month.ne(month)
                )
            ).sum()
        ),
        "exact_duplicate_rows": int(df.duplicated().sum()),
        "repeated_flight_keys": int(
            df.duplicated(subset=FLIGHT_KEY).sum()
        ),
        "invalid_cancelled_flags": int(
            (~df["Cancelled"].isin([0, 1])).sum()
        ),
        "invalid_diverted_flags": int(
            (~df["Diverted"].isin([0, 1])).sum()
        ),
        "cancelled_flights": int(df["Cancelled"].eq(1).sum()),
        "diverted_flights": int(df["Diverted"].eq(1).sum()),
        "completed_nondiverted_flights": len(completed),
        "missing_tail_number": int(df["Tail_Number"].isna().sum()),
        "missing_origin": int(df["Origin"].isna().sum()),
        "missing_dest": int(df["Dest"].isna().sum()),
        "missing_scheduled_departure": int(df["CRSDepTime"].isna().sum()),
        "missing_scheduled_arrival": int(df["CRSArrTime"].isna().sum()),
        "missing_scheduled_elapsed": int(df["CRSElapsedTime"].isna().sum()),
        "missing_departure_delay": int(df["DepDelay"].isna().sum()),
        "missing_arrival_delay": int(df["ArrDelay"].isna().sum()),
        "completed_missing_departure_delay": int(
            completed["DepDelay"].isna().sum()
        ),
        "completed_missing_arrival_delay": int(
            completed["ArrDelay"].isna().sum()
        ),
        "unnamed_columns": len(unnamed),
        "unnamed_nonmissing_cells": int(
            df[unnamed].notna().sum().sum()
        ),
    }

    # These checks identify records that need review.
    # Expected missing values, such as missing aircraft IDs, remain descriptive.
    review_checks = [
        "missing_calendar_days",
        "invalid_or_missing_dates",
        "rows_outside_month",
        "exact_duplicate_rows",
        "repeated_flight_keys",
        "invalid_cancelled_flags",
        "invalid_diverted_flags",
        "missing_origin",
        "missing_dest",
        "missing_scheduled_departure",
        "missing_scheduled_arrival",
        "missing_scheduled_elapsed",
        "completed_missing_departure_delay",
        "completed_missing_arrival_delay",
        "unnamed_nonmissing_cells",
    ]

    if len(df) == 0 or any(result[key] > 0 for key in review_checks):
        result["status"] = "review"

    return result

def check_time(series):
    numeric = pd.to_numeric(series, errors="coerce")

    # Accept whole-number HHMM values and the special midnight value 2400.
    ordinary = (
        numeric.ge(0)
        & numeric.lt(2400)
        & numeric.mod(1).eq(0)
        & numeric.mod(100).lt(60)
    )
    valid = ordinary | numeric.eq(2400)
    invalid = series.notna() & ~valid

    return {
        "missing": int(series.isna().sum()),
        "invalid": int(invalid.sum()),
        "midnight_0000": int(numeric.eq(0).sum()),
        "midnight_2400": int(numeric.eq(2400).sum()),
    }, invalid

def clean_flights(df):
    df = df.copy()
    original_rows = len(df)

    # Remove only unnamed columns that are entirely empty.
    empty_columns = [
        column for column in df.columns
        if column.startswith("Unnamed:")
        and df[column].isna().all()
    ]
    df = df.drop(columns=empty_columns)

    # Preserve original values and add quality flags.
    elapsed = pd.to_numeric(df["CRSElapsedTime"], errors="coerce")

    df["flag_elapsed_missing"] = df["CRSElapsedTime"].isna()
    df["flag_elapsed_invalid"] = (
        df["CRSElapsedTime"].notna()
        & (elapsed.isna() | elapsed.le(0))
    )
    df["flag_tail_missing"] = df["Tail_Number"].isna()

    completed = df["Cancelled"].eq(0) & df["Diverted"].eq(0)

    df["flag_completed_dep_delay_missing"] = (
        completed & df["DepDelay"].isna()
    )
    df["flag_completed_arr_delay_missing"] = (
        completed & df["ArrDelay"].isna()
    )

    return df


def add_utc(df, timezone_map):
    df["origin_timezone"] = df["Origin"].map(timezone_map)

    if df["origin_timezone"].isna().any():
        raise ValueError("Some origin airports have no timezone.")

    dates = pd.to_datetime(df["FlightDate"], errors="raise")
    hhmm = pd.to_numeric(df["CRSDepTime"], errors="raise")

    # Preserve 2400 records pending confirmation of the date convention.
    df["flag_dep_2400_pending"] = hhmm.eq(2400)

    minutes = (hhmm // 100) * 60 + hhmm % 100
    local = dates + pd.to_timedelta(
        minutes.where(~df["flag_dep_2400_pending"]), unit="min"
    )

    df["scheduled_dep_local"] = local
    df["scheduled_dep_utc"] = pd.Series(
        pd.NaT, index=df.index, dtype="datetime64[ns, UTC]"
    )

    # Leave ambiguous or nonexistent daylight-saving times unresolved.
    for timezone, indices in df.groupby("origin_timezone").groups.items():
        localized = local.loc[indices].dt.tz_localize(
            timezone,
            ambiguous="NaT",
            nonexistent="NaT",
        )
        df.loc[indices, "scheduled_dep_utc"] = (
            localized.dt.tz_convert("UTC")
        )

    df["flag_dep_dst_unresolved"] = (
        local.notna() & df["scheduled_dep_utc"].isna()
    )

    return df


def build_hourly(df):
    unresolved_count = int(df["scheduled_dep_utc"].isna().sum())

    # Keep unresolved records in the flight-level data.
    valid = df.loc[df["scheduled_dep_utc"].notna()].copy()
    valid["hour_utc"] = valid["scheduled_dep_utc"].dt.floor("h")

    completed = valid["Cancelled"].eq(0) & valid["Diverted"].eq(0)
    valid["completed"] = completed.astype(int)

    # Use completed, non-diverted flights for delay statistics.
    valid["dep_delay_value"] = valid["DepDelay"].where(completed)
    valid["arr_delay_value"] = valid["ArrDelay"].where(completed)
    valid["dep_del15_value"] = valid["DepDel15"].where(completed)

    hourly = (
        valid.groupby(["Origin", "hour_utc"], as_index=False)
        .agg(
            n_scheduled=("Cancelled", "size"),
            n_cancelled=("Cancelled", "sum"),
            n_diverted=("Diverted", "sum"),
            n_completed=("completed", "sum"),
            n_dep_delay_valid=("dep_delay_value", "count"),
            n_arr_delay_valid=("arr_delay_value", "count"),
            n_dep_del15_valid=("dep_del15_value", "count"),
            mean_dep_delay=("dep_delay_value", "mean"),
            median_dep_delay=("dep_delay_value", "median"),
            mean_arr_delay=("arr_delay_value", "mean"),
            share_dep_del15=("dep_del15_value", "mean"),
        )
        .sort_values(["Origin", "hour_utc"])
        .reset_index(drop=True)
    )

    hourly["cancel_rate"] = (
        hourly["n_cancelled"] / hourly["n_scheduled"]
    )

    # Verify that every timestamped flight was counted once.
    if hourly["n_scheduled"].sum() != len(valid):
        raise ValueError("Scheduled flight counts do not match.")

    if hourly.duplicated(["Origin", "hour_utc"]).any():
        raise ValueError("Duplicate airport-hour rows found.")

    return hourly


def months(start, end):
    first = pd.Period(start, freq="M")
    last = pd.Period(end, freq="M")
    if first > last:
        raise ValueError("Start month must not follow end month.")
    return list(pd.period_range(first, last, freq="M"))


def read_month(path):
    with zipfile.ZipFile(path) as archive:
        names = [n for n in archive.namelist() if n.lower().endswith(".csv")]
        if len(names) != 1:
            raise ValueError(f"Expected one CSV in {path.name}, found {len(names)}.")
        with archive.open(names[0]) as source:
            return pd.read_csv(source, encoding="latin-1", low_memory=False)


def run(data_dir, start, end):
    period = months(start, end)
    paths = [data_dir / "raw" / f"bts_{p.year}_{p.month:02d}.zip" for p in period]
    missing = [p.name for p in paths if not p.is_file()]
    if missing:
        raise FileNotFoundError(f"Missing monthly files: {missing}")

    airport_info = airportsdata.load("IATA")
    timezone_map = {code: info.get("tz") for code, info in airport_info.items()}
    timezone_map["PBI"] = "America/New_York"

    flights_dir = data_dir / "processed" / "flights_utc"
    hourly_dir = data_dir / "processed" / "hourly"
    report_dir = data_dir / "reports" / "pipeline"
    for directory in (flights_dir, hourly_dir, report_dir):
        directory.mkdir(parents=True, exist_ok=True)

    audits, utc_summary, exceptions = [], [], []
    for p, path in zip(period, paths):
        raw = read_month(path)
        audit = audit_month(raw, p.year, p.month)
        time_invalid = 0
        for column in ("CRSDepTime", "CRSArrTime"):
            counts, _ = check_time(raw[column])
            for key, value in counts.items():
                audit[f"{column}_{key}"] = value
            time_invalid += counts["invalid"]
        audits.append(audit)
        pd.DataFrame(audits).to_csv(report_dir / "monthly_audit.csv", index=False)

        # Stop rather than silently count ambiguous duplicates or corrupt records.
        blocking = [
            "missing_calendar_days", "invalid_or_missing_dates", "rows_outside_month",
            "repeated_flight_keys", "invalid_cancelled_flags", "invalid_diverted_flags",
            "missing_origin", "missing_dest", "missing_scheduled_departure",
            "missing_scheduled_arrival",
        ]
        if not len(raw) or time_invalid or any(audit[k] for k in blocking):
            raise ValueError(f"{p}: validation failed. See {report_dir / 'monthly_audit.csv'}")

        flights = add_utc(clean_flights(raw), timezone_map)
        hourly = build_hourly(flights)
        if len(flights) != len(raw):
            raise ValueError(f"{p}: flight rows were lost.")
        unknown = flights["scheduled_dep_utc"].isna()
        flagged = flights["flag_dep_2400_pending"] | flights["flag_dep_dst_unresolved"]
        if not unknown.equals(flagged):
            raise ValueError(f"{p}: unexplained UTC status.")

        flights.to_parquet(flights_dir / f"flights_{p.year}_{p.month:02d}.parquet", index=False)
        hourly.to_parquet(hourly_dir / f"airport_hourly_{p.year}_{p.month:02d}.parquet", index=False)
        columns = [
            "FlightDate", "Reporting_Airline", "Flight_Number_Reporting_Airline",
            "Origin", "Dest", "CRSDepTime", "origin_timezone", "scheduled_dep_utc",
            "flag_dep_2400_pending", "flag_dep_dst_unresolved",
        ]
        exceptional = flights.loc[unknown, columns].copy()
        exceptional["source_file"] = f"flights_{p.year}_{p.month:02d}.parquet"
        exceptions.append(exceptional)
        utc_summary.append({
            "month": str(p), "rows": len(flights), "utc_available": int((~unknown).sum()),
            "pending_2400": int(flights["flag_dep_2400_pending"].sum()),
            "unresolved_dst": int(flights["flag_dep_dst_unresolved"].sum()),
            "airport_hours": len(hourly),
        })
        print(f"{p}: {len(flights):,} flights, {len(hourly):,} airport-hours, "
              f"{unknown.sum():,} unresolved timestamps", flush=True)

    # Reports describe this invocation's requested range, not older output files.
    pd.DataFrame(utc_summary).to_csv(report_dir / "utc_summary.csv", index=False)
    pd.concat(exceptions, ignore_index=True).to_csv(report_dir / "utc_exceptions.csv", index=False)
    print(f"Completed {len(period)} months. Reports: {report_dir}", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", default="2023-01", help="First month, YYYY-MM")
    parser.add_argument("--end", default="2025-12", help="Last month, YYYY-MM")
    parser.add_argument("--data-dir", type=Path, default=BASE_DIR)
    args = parser.parse_args()
    run(args.data_dir.resolve(), args.start, args.end)


if __name__ == "__main__":
    main()
