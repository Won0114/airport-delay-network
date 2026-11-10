from pathlib import Path
import zipfile

import pandas as pd


BASE_DIR = Path(__file__).resolve().parent
RAW_DIR = BASE_DIR / "raw"
REPORT_DIR = BASE_DIR / "reports"

COLUMNS = [
    "FlightDate",
    "Reporting_Airline",
    "Flight_Number_Reporting_Airline",
    "Origin",
    "Dest",
    "CRSDepTime",
    "CRSArrTime",
    "CRSElapsedTime",
]


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


def main():
    REPORT_DIR.mkdir(exist_ok=True)
    results = []
    examples = []

    for year in range(2023, 2026):
        for month in range(1, 13):
            label = f"{year}-{month:02d}"
            path = RAW_DIR / f"bts_{year}_{month:02d}.zip"

            with zipfile.ZipFile(path) as archive:
                csv_name = next(
                    name for name in archive.namelist()
                    if name.lower().endswith(".csv")
                )
                with archive.open(csv_name) as source:
                    df = pd.read_csv(
                        source,
                        usecols=COLUMNS,
                        encoding="latin-1",
                        low_memory=False,
                    )

            result = {"month": label, "rows": len(df)}

            for column in ["CRSDepTime", "CRSArrTime"]:
                counts, invalid = check_time(df[column])

                for name, count in counts.items():
                    result[f"{column}_{name}"] = count

                sample = df.loc[invalid].head(5).copy()
                if not sample.empty:
                    sample["issue"] = f"Invalid {column}"
                    examples.append(sample)

            elapsed = pd.to_numeric(
                df["CRSElapsedTime"], errors="coerce"
            )
            invalid_elapsed = (
                df["CRSElapsedTime"].notna()
                & (elapsed.isna() | elapsed.le(0))
            )

            result["elapsed_missing"] = int(
                df["CRSElapsedTime"].isna().sum()
            )
            result["elapsed_invalid"] = int(invalid_elapsed.sum())

            sample = df.loc[invalid_elapsed].head(5).copy()
            if not sample.empty:
                sample["issue"] = "Invalid CRSElapsedTime"
                examples.append(sample)

            results.append(result)
            print(f"{label}: Checked.", flush=True)

    report = pd.DataFrame(results)
    report.to_csv(
        REPORT_DIR / "time_audit.csv",
        index=False,
        encoding="utf-8-sig",
    )

    example_columns = COLUMNS + ["issue"]
    example_table = (
        pd.concat(examples, ignore_index=True)
        if examples
        else pd.DataFrame(columns=example_columns)
    )
    example_table.to_csv(
        REPORT_DIR / "time_audit_examples.csv",
        index=False,
        encoding="utf-8-sig",
    )

    print("\n=== Totals across 36 months ===")
    print(report.drop(columns=["month", "rows"]).sum().to_string())
    print("\nReports saved in the reports folder.")


if __name__ == "__main__":
    main()