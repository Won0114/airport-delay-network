from pathlib import Path
import zipfile

import pandas as pd


BASE_DIR = Path(__file__).resolve().parent
RAW_DIR = BASE_DIR / "raw"
OUTPUT_DIR = BASE_DIR / "processed" / "flights"


def clean_month(year, month):
    path = RAW_DIR / f"bts_{year}_{month:02d}.zip"

    with zipfile.ZipFile(path) as archive:
        csv_name = next(
            name for name in archive.namelist()
            if name.lower().endswith(".csv")
        )

        with archive.open(csv_name) as source:
            df = pd.read_csv(
                source,
                encoding="latin-1",
                low_memory=False,
            )

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

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    output = OUTPUT_DIR / f"flights_{year}_{month:02d}.parquet"
    df.to_parquet(output, index=False)

    print(f"Input rows: {original_rows:,}")
    print(f"Output rows: {len(df):,}")
    print(f"Removed columns: {empty_columns}")
    print("\nQuality flags:")
    flags = [column for column in df.columns if column.startswith("flag_")]
    print(df[flags].sum().to_string())
    print(f"\nSaved to: {output}")


if __name__ == "__main__":
    import subprocess
    import sys

    from add_utc import add_utc

    for year in range(2023, 2026):
        for month in range(1, 13):
            print(f"\nProcessing {year}-{month:02d}...", flush=True)
            clean_month(year, month)
            add_utc(year, month)

    subprocess.run(
        [sys.executable, str(BASE_DIR / "save_utc_exceptions.py")],
        check=True,
    )

    print("\nCleaning and UTC conversion completed.", flush=True)