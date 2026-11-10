from pathlib import Path

import pandas as pd


DATA_DIR = Path(__file__).resolve().parent / "processed" / "flights_utc"

columns = [
    "FlightDate",
    "Origin",
    "Dest",
    "CRSDepTime",
    "DepTime",
    "DepDelay",
    "CRSArrTime",
    "CRSElapsedTime",
    "Cancelled",
    "Diverted",
]

for year, month in [(2024, 3), (2025, 7)]:
    path = DATA_DIR / f"flights_{year}_{month:02d}.parquet"
    df = pd.read_parquet(path, columns=columns)
    selected = df.loc[df["CRSDepTime"].eq(2400)]

    print(f"\n=== {year}-{month:02d}: Scheduled midnight departures ===")
    print(selected.to_string(index=False))