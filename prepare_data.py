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


# This source is inserted into prepare_data.py; it is not a separate project script.
FAA40 = set("ANC BWI BOS CLT MDW ORD CVG DAL DFW DEN DTW FLL IAH LAS ATL HNL IND LAX SDF MEM MIA MSP JFK LGA EWR OAK ONT MCO PHL PHX PDX DCA SLC SAN SFO SEA TPA TEB IAD HOU".split())


def finalize(data_dir, start, end, output_dir=None):
    """Build a shared UTC grid from existing outputs without rerunning cleaning."""
    import pyarrow.parquet as pq

    period = months(start, end)
    baseline = months("2023-01", "2023-12")
    needed = sorted(set(period + baseline))
    for p in needed:
        path = data_dir / "processed" / "flights_utc" / f"flights_{p.year}_{p.month:02d}.parquet"
        if not path.is_file():
            raise FileNotFoundError(f"Required flight file missing: {path}")

    # Use a fixed, pre-event baseline, counting all scheduled departures.
    counts = pd.Series(dtype="int64")
    for p in baseline:
        path = data_dir / "processed" / "flights_utc" / f"flights_{p.year}_{p.month:02d}.parquet"
        origins = pd.read_parquet(path, columns=["Origin"])["Origin"]
        counts = counts.add(origins.value_counts(), fill_value=0)
    ranking = counts.astype("int64").rename_axis("airport").reset_index(name="scheduled_departures_2023")
    ranking = ranking.sort_values(["scheduled_departures_2023", "airport"], ascending=[False, True]).reset_index(drop=True)
    ranking["volume_rank_2023"] = ranking.index + 1
    ranking["top60"] = ranking["volume_rank_2023"].le(60)
    ranking["faa40"] = ranking["airport"].isin(FAA40)
    ranking["selected"] = ranking["top60"] | ranking["faa40"]
    selected = ranking.loc[ranking["selected"]].copy()
    airport_info = airportsdata.load("IATA")
    tz_map = {k: v.get("tz") for k, v in airport_info.items()}
    tz_map["PBI"] = "America/New_York"
    selected["timezone"] = selected["airport"].map(tz_map)
    if selected["timezone"].isna().any():
        raise ValueError("Selected airports lack timezone metadata.")

    parts, exception_parts, checks = [], [], []
    columns = ["FlightDate", "Origin", "scheduled_dep_utc", "CRSDepTime",
               "flag_dep_2400_pending", "flag_dep_dst_unresolved"]
    for p in period:
        stem = f"{p.year}_{p.month:02d}"
        flight_path = data_dir / "processed" / "flights_utc" / f"flights_{stem}.parquet"
        hour_path = data_dir / "processed" / "hourly" / f"airport_hourly_{stem}.parquet"
        flights = pd.read_parquet(flight_path, columns=columns)
        hourly = pd.read_parquet(hour_path)
        unknown = flights["scheduled_dep_utc"].isna()
        flags = flights["flag_dep_2400_pending"] | flights["flag_dep_dst_unresolved"]
        if not unknown.equals(flags):
            raise ValueError(f"{p}: unexplained missing UTC.")
        if len(flights) != pq.ParquetFile(flight_path).metadata.num_rows:
            raise ValueError(f"{p}: flight row mismatch.")
        source_zip = data_dir / "raw" / f"bts_{stem}.zip"
        # Count CSV records without loading all source columns again.
        with zipfile.ZipFile(source_zip) as archive:
            names = [n for n in archive.namelist() if n.lower().endswith(".csv")]
            if len(names) != 1:
                raise ValueError(f"{p}: expected exactly one source CSV.")
            with archive.open(names[0]) as source:
                source_rows = sum(len(chunk) for chunk in pd.read_csv(
                    source, usecols=["Origin"], chunksize=200000, encoding="latin-1"))
        if source_rows != len(flights):
            raise ValueError(f"{p}: source flight count does not match cleaned count.")
        if hourly.duplicated(["Origin", "hour_utc"]).any():
            raise ValueError(f"{p}: duplicate airport-hours.")
        if hourly["n_scheduled"].sum() != (~unknown).sum():
            raise ValueError(f"{p}: hourly totals do not match timestamped flights.")
        parts.append(hourly.loc[hourly["Origin"].isin(selected["airport"])])
        exception_parts.append(flights.loc[unknown])
        checks.append({"month": str(p), "source_rows": source_rows, "flight_rows": len(flights),
                       "utc_available": int((~unknown).sum()), "unresolved": int(unknown.sum()),
                       "hourly_scheduled": int(hourly["n_scheduled"].sum())})
        print(f"{p}: source and aggregate counts verified.", flush=True)

    observed = pd.concat(parts, ignore_index=True)
    if observed.duplicated(["Origin", "hour_utc"]).any():
        raise ValueError("Monthly files overlap on airport-hour keys.")
    exceptions = pd.concat(exception_parts, ignore_index=True)
    local_start = period[0].start_time
    local_end = (period[-1] + 1).start_time
    utc_hours = pd.date_range(local_start.tz_localize("UTC") - pd.Timedelta(days=1),
                              local_end.tz_localize("UTC") + pd.Timedelta(days=1),
                              freq="h", inclusive="left")
    count_cols = [c for c in observed.columns if c.startswith("n_")]
    panels, coverage = [], []

    for row in selected.itertuples(index=False):
        ap, timezone = row.airport, row.timezone
        local = utc_hours.tz_convert(timezone).tz_localize(None)
        covered = (local >= local_start) & (local < local_end)
        indexed = observed.loc[observed["Origin"].eq(ap)].set_index("hour_utc").drop(columns="Origin")
        grid = indexed.reindex(utc_hours)
        grid.index.name = "hour_utc"
        grid["Origin"] = ap
        grid["local_hour_start"] = local
        grid["source_covered"] = covered

        # Mark every plausible hour for unresolved records, rather than treating
        # an incomplete count as a known zero. No uncertain timestamp is imputed.
        possible = set()
        for ex in exceptions.loc[exceptions["Origin"].eq(ap)].itertuples(index=False):
            day = pd.Timestamp(ex.FlightDate).normalize()
            if ex.flag_dep_2400_pending:
                candidates = [day, day + pd.Timedelta(days=1)]
            else:
                hhmm = int(ex.CRSDepTime)
                candidates = [day + pd.Timedelta(minutes=(hhmm // 100) * 60 + hhmm % 100)]
            for candidate in candidates:
                for dst in (True, False):
                    stamp = candidate.tz_localize(timezone, ambiguous=dst, nonexistent="NaT")
                    if pd.notna(stamp):
                        possible.add(stamp.tz_convert("UTC").floor("h"))
            # A nonexistent local time has no candidate UTC instant; conservatively
            # flag the whole local day if localization yielded nothing.
            if not ex.flag_dep_2400_pending and not any(
                pd.notna(candidates[0].tz_localize(timezone, ambiguous=d, nonexistent="NaT"))
                for d in (True, False)
            ):
                possible.update(utc_hours[local.normalize() == day])

        grid["flag_unresolved_flights_possible"] = utc_hours.isin(possible)
        grid["counts_complete"] = grid["source_covered"] & ~grid["flag_unresolved_flights_possible"]
        for column in count_cols:
            grid[column] = grid[column].fillna(0).astype("Int64")
            grid.loc[~grid["source_covered"], column] = pd.NA
        grid["no_scheduled_flights"] = grid["counts_complete"] & grid["n_scheduled"].eq(0).fillna(False)
        grid["delay_available"] = grid["counts_complete"] & grid["mean_dep_delay"].notna()

        # Empty hours have undefined delay and cancellation rate, never zero delay.
        if grid.loc[grid["n_scheduled"].eq(0).fillna(False), "mean_dep_delay"].notna().any():
            raise ValueError(f"{ap}: empty-hour delays were filled.")
        if int(grid["n_scheduled"].sum()) != int(indexed["n_scheduled"].sum()):
            raise ValueError(f"{ap}: grid construction lost flights.")
        if not grid.index.to_series().diff().dropna().eq(pd.Timedelta(hours=1)).all():
            raise ValueError(f"{ap}: non-hourly grid.")
        coverage.append({
            "airport": ap, "covered_hours": int(grid["source_covered"].sum()),
            "known_scheduled_flights": int(grid["n_scheduled"].sum()),
            "no_scheduled_flight_hours": int(grid["no_scheduled_flights"].sum()),
            "uncertain_count_hours": int((grid["source_covered"] & ~grid["counts_complete"]).sum()),
            "delay_available_hours": int(grid["delay_available"].sum()),
            "delay_unavailable_share": float(1 - grid.loc[grid["source_covered"], "delay_available"].mean()),
        })
        panels.append(grid.reset_index())

    panel = pd.concat(panels, ignore_index=True)
    if len(panel) != len(selected) * len(utc_hours) or panel.duplicated(["Origin", "hour_utc"]).any():
        raise ValueError("Final grid keys or dimensions are invalid.")
    destination = data_dir if output_dir is None else output_dir
    output = destination / "processed" / "final"
    report_dir = destination / "reports" / "pipeline"
    output.mkdir(parents=True, exist_ok=True)
    report_dir.mkdir(parents=True, exist_ok=True)
    panel.to_parquet(output / "airport_hourly.parquet", index=False)
    selected.to_csv(output / "airports.csv", index=False)
    ranking.to_csv(report_dir / "airport_ranking_2023.csv", index=False)
    pd.DataFrame(coverage).to_csv(report_dir / "airport_coverage.csv", index=False)
    pd.DataFrame(checks).to_csv(report_dir / "final_checks.csv", index=False)
    exceptions.to_csv(report_dir / "final_utc_exceptions.csv", index=False)
    summary = {
        "start": start, "end": end, "airports": len(selected), "rows": len(panel),
        "source_flights": sum(c["source_rows"] for c in checks),
        "utc_available": sum(c["utc_available"] for c in checks),
        "unresolved": sum(c["unresolved"] for c in checks),
        "selected_known_flights": int(panel["n_scheduled"].sum()),
        "missing_faa_airports_in_2023": sorted(FAA40 - set(ranking["airport"])),
        "validation_passed": True,
    }
    import json
    (report_dir / "final_summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2), flush=True)
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", default="2023-01", help="First month, YYYY-MM")
    parser.add_argument("--end", default="2025-12", help="Last month, YYYY-MM")
    parser.add_argument("--data-dir", type=Path, default=BASE_DIR)
    parser.add_argument("--finalize-only", action="store_true",
                        help="Reuse cleaned flights and hourly outputs to build the final grid")
    args = parser.parse_args()
    data_dir = args.data_dir.resolve()
    if not args.finalize_only:
        run(data_dir, args.start, args.end)
    if args.finalize_only or (args.start == "2023-01" and args.end == "2025-12"):
        finalize(data_dir, args.start, args.end)
    else:
        print("Partial preparation complete. Finalization requires the 2023 baseline files.")


if __name__ == "__main__":
    main()
