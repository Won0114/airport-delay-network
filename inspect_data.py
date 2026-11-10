from pathlib import Path
import zipfile

import pandas as pd


path = Path(__file__).resolve().parent / "raw" / "bts_2023_01.zip"

with zipfile.ZipFile(path) as archive:
    csv_name = next(
        name for name in archive.namelist()
        if name.lower().endswith(".csv")
    )

    with archive.open(csv_name) as source:
        df = pd.read_csv(source, encoding="latin-1", low_memory=False)

print("=== Size ===")
print(f"Rows: {len(df):,}")
print(f"Columns: {len(df.columns)}")

print("\n=== Dates ===")
dates = pd.to_datetime(df["FlightDate"], errors="coerce")
print(f"First date: {dates.min()}")
print(f"Last date: {dates.max()}")
print(f"Unique dates: {dates.nunique()}")
print(f"Missing or invalid dates: {dates.isna().sum():,}")

print("\n=== Duplicates ===")
print(f"Exact duplicate rows: {df.duplicated().sum():,}")

flight_key = [
    "FlightDate",
    "Reporting_Airline",
    "Flight_Number_Reporting_Airline",
    "Origin",
    "CRSDepTime",
]
print(
    "Repeated flight keys:",
    f"{df.duplicated(subset=flight_key).sum():,}",
)

print("\n=== Status values ===")
for column in ["Cancelled", "Diverted"]:
    print(f"\n{column}:")
    print(df[column].value_counts(dropna=False).to_string())

print("\n=== Missing values ===")
columns = [
    "FlightDate", "Origin", "Dest", "Tail_Number",
    "CRSDepTime", "CRSArrTime", "CRSElapsedTime",
    "DepDelay", "ArrDelay", "DepDel15",
]
missing = pd.DataFrame({
    "missing_count": df[columns].isna().sum(),
    "missing_percent": df[columns].isna().mean().mul(100).round(2),
})
print(missing.to_string())

print("\n=== Missing delays among completed, non-diverted flights ===")
completed = df.loc[
    df["Cancelled"].eq(0) & df["Diverted"].eq(0)
]
print(f"Flights: {len(completed):,}")
print(completed[["DepDelay", "ArrDelay"]].isna().sum().to_string())

print("\n=== Unnamed columns ===")
for column in df.columns:
    if column.startswith("Unnamed:"):
        print(f"{column}: {df[column].notna().sum():,} non-missing values")