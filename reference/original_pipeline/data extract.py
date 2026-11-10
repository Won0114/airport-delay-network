"""
Batch-download BTS "Reporting Carrier On-Time Performance" monthly files
and convert them to Parquet with only the columns we need.

Usage:
    1. pip install requests pandas pyarrow   (once, in a terminal)
    2. Edit START / END / OUT in main() below, then run this file
       (e.g. the Run button in VS Code). Command-line flags such as
       --start 2024-01 still work and override those settings.

Output:
    data/raw/       monthly .zip files (deleted after conversion unless --keep-zip)
    data/parquet/   one Parquet file per month, e.g. ontime_2025_05.parquet

Query all months at once afterwards, e.g. with DuckDB:
    import duckdb
    duckdb.sql("SELECT Origin, COUNT(*) AS n FROM 'data/parquet/*.parquet' "
               "GROUP BY Origin ORDER BY n DESC LIMIT 20").show()
"""

import argparse
import io
import sys
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import pandas as pd
import requests

URL = ("https://transtats.bts.gov/PREZIP/"
       "On_Time_Reporting_Carrier_On_Time_Performance_1987_present_{y}_{m}.zip")

# Columns we keep (the raw files have ~110 columns).
COLUMNS = [
    "Year", "Month", "DayofMonth", "DayOfWeek", "FlightDate",
    "Reporting_Airline", "Tail_Number", "Flight_Number_Reporting_Airline",
    "Origin", "OriginState", "Dest", "DestState",
    "CRSDepTime", "DepTime", "DepDelay", "DepDelayMinutes", "DepDel15",
    "CRSArrTime", "ArrTime", "ArrDelay", "ArrDelayMinutes", "ArrDel15",
    "Cancelled", "CancellationCode", "Diverted",
    "CRSElapsedTime", "ActualElapsedTime", "Distance",
    "CarrierDelay", "WeatherDelay", "NASDelay", "SecurityDelay", "LateAircraftDelay",
]

DTYPES = {
    "Reporting_Airline": "string", "Tail_Number": "string",
    "Origin": "string", "Dest": "string", "OriginState": "string",
    "DestState": "string", "CancellationCode": "string",
    "CRSDepTime": "Int16", "DepTime": "Int16", "CRSArrTime": "Int16", "ArrTime": "Int16",
}

HEADERS = {"User-Agent": "Mozilla/5.0 (course project data download)"}


def months_between(start: str, end: str):
    y, m = map(int, start.split("-"))
    ey, em = map(int, end.split("-"))
    while (y, m) <= (ey, em):
        yield y, m
        m += 1
        if m == 13:
            y, m = y + 1, 1


class NotPublished(Exception):
    """BTS has not released this month yet."""


def download(y: int, m: int, raw_dir: Path, retries: int = 4) -> Path:
    path = raw_dir / f"ontime_{y}_{m:02d}.zip"
    if path.exists() and zipfile.is_zipfile(path):
        return path  # already downloaded
    url = URL.format(y=y, m=m)
    for attempt in range(1, retries + 1):
        try:
            with requests.get(url, headers=HEADERS, stream=True, timeout=120) as r:
                if r.status_code == 404:
                    raise NotPublished()
                r.raise_for_status()
                tmp = path.with_suffix(".part")
                with open(tmp, "wb") as f:
                    for chunk in r.iter_content(chunk_size=1 << 20):
                        f.write(chunk)
            if not zipfile.is_zipfile(tmp):
                tmp.unlink(missing_ok=True)
                raise NotPublished()
            tmp.replace(path)
            return path
        except NotPublished:
            raise
        except requests.exceptions.SSLError as e:
            raise RuntimeError(
                "SSL certificate error. Try: pip install truststore, then rerun "
                "with --system-certs") from e
        except Exception as e:  # network hiccup: wait and retry
            if attempt == retries:
                raise
            wait = 5 * attempt
            print(f"  {y}-{m:02d}: {e} -> retry in {wait}s")
            time.sleep(wait)


def to_parquet(zip_path: Path, out_dir: Path, all_columns: bool) -> tuple[Path, int]:
    with zipfile.ZipFile(zip_path) as z:
        csv_name = next(n for n in z.namelist() if n.lower().endswith(".csv"))
        with z.open(csv_name) as f:
            df = pd.read_csv(
                io.TextIOWrapper(f, encoding="latin-1"),
                usecols=None if all_columns else (lambda c: c in COLUMNS),
                dtype={k: v for k, v in DTYPES.items()},
                low_memory=False,
            )
    # raw files end each line with a comma -> an empty "Unnamed: N" column
    df = df.loc[:, ~df.columns.str.startswith("Unnamed")]
    missing = [c for c in COLUMNS if c not in df.columns]
    if missing:
        print(f"  warning: {zip_path.name} is missing columns {missing}")
    # text columns as proper strings so Parquet stores them cleanly
    for c in df.columns[df.dtypes == object]:
        df[c] = df[c].astype("string")
    df["FlightDate"] = pd.to_datetime(df["FlightDate"])
    out = out_dir / (zip_path.stem + ".parquet")
    df.to_parquet(out, index=False)
    return out, len(df)


def process(y, m, raw_dir, out_dir, keep_zip, all_columns):
    out = out_dir / f"ontime_{y}_{m:02d}.parquet"
    if out.exists():
        return f"{y}-{m:02d}: already done, skipped"
    zp = download(y, m, raw_dir)
    out, n = to_parquet(zp, out_dir, all_columns)
    if not keep_zip:
        zp.unlink()
    return f"{y}-{m:02d}: {n:,} flights -> {out.name}"


def main():
    p = argparse.ArgumentParser()
    # ---- Settings: edit these three lines, then just run the script ----
    START = "2023-01"   # first month (YYYY-MM)
    END = "2025-12"     # last month (YYYY-MM); months not yet released are skipped
    OUT = r"C:\Users\ASUS\Desktop\grad\1st_semester\elec573\project\data"
    ALL_COLUMNS = True  # True: keep all ~110 columns; False: only the COLUMNS list above
    # -------------------------------------------------------------------
    p.add_argument("--start", default=START, help="first month, YYYY-MM")
    p.add_argument("--end", default=END, help="last month, YYYY-MM")
    p.add_argument("--out", default=OUT, help="output folder")
    p.add_argument("--workers", type=int, default=3,
                   help="parallel downloads (keep small to be polite to the server)")
    p.add_argument("--keep-zip", action="store_true", help="keep raw zip files")
    p.add_argument("--system-certs", action="store_true",
                   help="use the operating system's certificate store (needs truststore)")
    a = p.parse_args()

    if a.system_certs:
        import truststore
        truststore.inject_into_ssl()

    raw_dir, out_dir = Path(a.out) / "raw", Path(a.out) / "parquet"
    raw_dir.mkdir(parents=True, exist_ok=True)
    out_dir.mkdir(parents=True, exist_ok=True)

    months = list(months_between(a.start, a.end))
    print(f"Processing {len(months)} months with {a.workers} workers...")
    failed, unpublished = [], []
    with ThreadPoolExecutor(max_workers=a.workers) as ex:
        futs = {ex.submit(process, y, m, raw_dir, out_dir, a.keep_zip, ALL_COLUMNS): (y, m)
                for y, m in months}
        for fut in as_completed(futs):
            y, m = futs[fut]
            try:
                print(fut.result())
            except NotPublished:
                unpublished.append(f"{y}-{m:02d}")
            except Exception as e:
                failed.append(f"{y}-{m:02d}")
                print(f"{y}-{m:02d}: FAILED ({e})")

    if unpublished:
        print("\nNot released by BTS yet (skipped):", ", ".join(sorted(unpublished)))
    if failed:
        print("\nFailed months (rerun the same command to retry):", ", ".join(failed))
        sys.exit(1)
    print("\nAll done. Parquet files are in", out_dir.resolve())


if __name__ == "__main__":
    main()