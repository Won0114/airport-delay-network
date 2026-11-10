from pathlib import Path

import pandas as pd


BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "processed" / "flights_utc"
REPORT_DIR = BASE_DIR / "reports"

columns = [
    "FlightDate",
    "Reporting_Airline",
    "Flight_Number_Reporting_Airline",
    "Origin",
    "Dest",
    "CRSDepTime",
    "origin_timezone",
    "scheduled_dep_utc",
    "flag_dep_2400_pending",
    "flag_dep_dst_unresolved",
]

files = sorted(DATA_DIR.glob("flights_*.parquet"))
if len(files) != 36:
    raise ValueError(f"Expected 36 monthly files, found {len(files)}.")

exceptions = []
summary = []

for path in files:
    df = pd.read_parquet(path, columns=columns)
    missing_utc = df["scheduled_dep_utc"].isna()
    flagged = df["flag_dep_2400_pending"] | df["flag_dep_dst_unresolved"]

    if not missing_utc.equals(flagged):
        raise ValueError(f"Unexplained UTC status in {path.name}.")

    selected = df.loc[missing_utc].copy()
    selected["source_file"] = path.name
    exceptions.append(selected)

    summary.append({
        "source_file": path.name,
        "rows": len(df),
        "utc_available": int((~missing_utc).sum()),
        "pending_2400": int(df["flag_dep_2400_pending"].sum()),
        "unresolved_dst": int(df["flag_dep_dst_unresolved"].sum()),
    })

REPORT_DIR.mkdir(exist_ok=True)

pd.concat(exceptions, ignore_index=True).to_csv(
    REPORT_DIR / "utc_exceptions.csv",
    index=False,
    encoding="utf-8-sig",
)

report = pd.DataFrame(summary)
report.to_csv(
    REPORT_DIR / "utc_summary.csv",
    index=False,
    encoding="utf-8-sig",
)

print("=== UTC summary ===")
print(report.drop(columns="source_file").sum().to_string())
print("\nSaved utc_summary.csv and utc_exceptions.csv.")