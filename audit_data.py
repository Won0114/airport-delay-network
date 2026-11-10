from pathlib import Path
import zipfile

import pandas as pd


BASE_DIR = Path(__file__).resolve().parent
RAW_DIR = BASE_DIR / "raw"
REPORT_DIR = BASE_DIR / "reports"

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


def audit_month(path, year, month):
    with zipfile.ZipFile(path) as archive:
        csv_files = [
            name for name in archive.namelist()
            if name.lower().endswith(".csv")
        ]

        if len(csv_files) != 1:
            raise ValueError(
                f"Expected one CSV file, found {len(csv_files)}."
            )

        with archive.open(csv_files[0]) as source:
            df = pd.read_csv(
                source,
                encoding="latin-1",
                low_memory=False,
            )

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


def main():
    REPORT_DIR.mkdir(exist_ok=True)
    results = []

    for year in range(2023, 2026):
        for month in range(1, 13):
            label = f"{year}-{month:02d}"
            path = RAW_DIR / f"bts_{year}_{month:02d}.zip"

            print(f"{label}: Auditing...", flush=True)

            if not path.exists():
                result = {
                    "month": label,
                    "status": "missing_file",
                    "error": "ZIP file not found.",
                }
            else:
                try:
                    result = audit_month(path, year, month)
                except Exception as error:
                    result = {
                        "month": label,
                        "status": "error",
                        "error": str(error),
                    }

            results.append(result)
            print(f"{label}: {result['status']}", flush=True)

    report = pd.DataFrame(results)
    output = REPORT_DIR / "monthly_audit.csv"
    report.to_csv(output, index=False, encoding="utf-8-sig")

    print("\n=== Summary ===")
    print(report["status"].value_counts().to_string())
    print(f"\nReport saved to: {output}")

    flagged = report.loc[report["status"].ne("ok"), ["month", "status"]]
    if not flagged.empty:
        print("\n=== Months requiring review ===")
        print(flagged.to_string(index=False))


if __name__ == "__main__":
    main()