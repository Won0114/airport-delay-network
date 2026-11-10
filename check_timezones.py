from pathlib import Path

import airportsdata
import pandas as pd


BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "processed" / "flights"
REPORT_DIR = BASE_DIR / "reports"

airports = airportsdata.load("IATA")
# Supply missing metadata for PBI during the study period.
airports["PBI"] = {
    "name": "Palm Beach International Airport",
    "tz": "America/New_York",
}
codes = set()

for path in sorted(DATA_DIR.glob("flights_*.parquet")):
    df = pd.read_parquet(path, columns=["Origin", "Dest"])
    codes.update(df["Origin"].dropna().unique())
    codes.update(df["Dest"].dropna().unique())

rows = []

for code in sorted(codes):
    info = airports.get(code, {})
    rows.append({
        "airport": code,
        "name": info.get("name", ""),
        "timezone": info.get("tz", ""),
    })

report = pd.DataFrame(rows)
missing = report.loc[
    report["timezone"].isna() | report["timezone"].eq("")
]

REPORT_DIR.mkdir(exist_ok=True)
report.to_csv(
    REPORT_DIR / "airport_timezones.csv",
    index=False,
    encoding="utf-8-sig",
)

print(f"Total airports: {len(report)}")
print(f"Airports without a timezone: {len(missing)}")

if not missing.empty:
    print(missing.to_string(index=False))

print("\nExamples:")
print(
    report.loc[
        report["airport"].isin(["JFK", "LAX", "ORD", "EWR", "HNL"])
    ].to_string(index=False)
)