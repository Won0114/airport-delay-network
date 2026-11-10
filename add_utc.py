from pathlib import Path

import pandas as pd


BASE_DIR = Path(__file__).resolve().parent
INPUT_DIR = BASE_DIR / "processed" / "flights"
OUTPUT_DIR = BASE_DIR / "processed" / "flights_utc"


def add_utc(year, month):
    path = INPUT_DIR / f"flights_{year}_{month:02d}.parquet"
    df = pd.read_parquet(path)

    timezone_table = pd.read_csv(
        BASE_DIR / "reports" / "airport_timezones.csv"
    )
    timezone_map = timezone_table.set_index("airport")["timezone"]

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

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    output = OUTPUT_DIR / path.name
    df.to_parquet(output, index=False)

    print(f"\n=== {year}-{month:02d} ===")
    print(f"Rows: {len(df):,}")
    print(f"UTC timestamps: {df['scheduled_dep_utc'].notna().sum():,}")
    print(f"Pending 2400 records: {df['flag_dep_2400_pending'].sum():,}")
    print(f"Unresolved DST records: {df['flag_dep_dst_unresolved'].sum():,}")

    print("\nExamples:")
    columns = [
        "Origin",
        "origin_timezone",
        "scheduled_dep_local",
        "scheduled_dep_utc",
    ]
    print(df[columns].head(5).to_string(index=False))

    unresolved = df.loc[df["flag_dep_dst_unresolved"]]

    if not unresolved.empty:
        print("\nUnresolved DST records:")
        print(
            unresolved[
                [
                    "FlightDate",
                    "Origin",
                    "Dest",
                    "CRSDepTime",
                    "origin_timezone",
                    "scheduled_dep_local",
                ]
            ].to_string(index=False)
        )

    print(f"\nSaved to: {output}")


if __name__ == "__main__":
    for year in range(2023, 2026):
        for month in range(1, 13):
            add_utc(year, month)

    print("\nAll 36 months processed successfully.", flush=True)