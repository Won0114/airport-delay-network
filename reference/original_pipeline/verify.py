"""
Verify the downloaded BTS monthly Parquet files.

Checks, month by month:
  1. Completeness  - every month in the range exists; every calendar day is present;
                     row count is in a plausible range.
  2. Schema        - key columns exist in every file.
  3. Duplicates    - the same flight (date, carrier, flight number, origin) appears once.
  4. Value ranges  - times, 0/1 flags, airport codes, delay values are sensible.
  5. Consistency   - BTS rule: for flights arriving 15+ min late, the five delay-cause
                     columns add up to the arrival delay. A good test that columns were
                     not shifted or mixed up while parsing.

It also writes  data/verify_monthly_summary.csv  with flights, on-time %, and
cancellation % per month. Compare a few months with the numbers BTS publishes in
its monthly "Air Travel Consumer Report" / on-time press releases (bts.gov newsroom).

Needs: pip install pandas pyarrow
"""

import calendar
from pathlib import Path

import pandas as pd

# ---- Settings -------------------------------------------------------------
PROJECT = Path(r"C:\Users\ASUS\Desktop\grad\1st_semester\elec573\project")
DATA = PROJECT / "data" / "parquet"
START, END = "2023-01", "2025-12"
# ----------------------------------------------------------------------------

KEY_COLS = ["FlightDate", "Reporting_Airline", "Flight_Number_Reporting_Airline",
            "Tail_Number", "Origin", "Dest", "CRSDepTime", "CRSArrTime",
            "DepDelay", "ArrDelay", "ArrDel15", "Cancelled", "Diverted",
            "CarrierDelay", "WeatherDelay", "NASDelay", "SecurityDelay", "LateAircraftDelay"]
CAUSES = ["CarrierDelay", "WeatherDelay", "NASDelay", "SecurityDelay", "LateAircraftDelay"]


def months(start, end):
    y, m = map(int, start.split("-"))
    ey, em = map(int, end.split("-"))
    while (y, m) <= (ey, em):
        yield y, m
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)


def check_month(path: Path, y: int, m: int):
    problems = []
    df = pd.read_parquet(path)
    missing = [c for c in KEY_COLS if c not in df.columns]
    if missing:
        return [f"missing columns {missing}"], None
    df = df[KEY_COLS]
    df["FlightDate"] = pd.to_datetime(df["FlightDate"])

    # 1. completeness
    n = len(df)
    if not 300_000 <= n <= 800_000:
        problems.append(f"unusual row count {n:,}")
    wrong_month = ((df["FlightDate"].dt.year != y) | (df["FlightDate"].dt.month != m)).sum()
    if wrong_month:
        problems.append(f"{wrong_month:,} rows dated outside {y}-{m:02d}")
    days = df["FlightDate"].dt.day.nunique()
    if days != calendar.monthrange(y, m)[1]:
        problems.append(f"only {days} distinct days")

    # 3. duplicates
    dup = df.duplicated(["FlightDate", "Reporting_Airline",
                         "Flight_Number_Reporting_Airline", "Origin", "CRSDepTime"]).sum()
    if dup:
        problems.append(f"{dup:,} duplicated flights")

    # 4. value ranges
    for c in ["CRSDepTime", "CRSArrTime"]:
        bad = (~df[c].between(1, 2400)).sum()
        if bad:
            problems.append(f"{bad:,} {c} values outside 0001-2400")
    for c in ["Cancelled", "Diverted"]:
        if not set(df[c].dropna().unique()) <= {0, 1}:
            problems.append(f"{c} has values other than 0/1")
    bad_codes = (~df["Origin"].astype(str).str.fullmatch(r"[A-Z0-9]{3}")).sum()
    if bad_codes:
        problems.append(f"{bad_codes:,} malformed Origin codes")
    flown = df[(df["Cancelled"] == 0) & (df["Diverted"] == 0)]
    if flown["DepDelay"].isna().mean() > 0.01:
        problems.append(f"{flown['DepDelay'].isna().mean():.1%} of flown flights lack DepDelay")
    if (flown["DepDelay"] < -120).any() or (flown["DepDelay"] > 3000).any():
        problems.append("DepDelay has extreme values (< -120 or > 3000 min)")

    # 5. consistency: causes sum to arrival delay for ArrDel15 flights
    late = flown[flown["ArrDel15"] == 1]
    diff = (late[CAUSES].sum(axis=1) - late["ArrDelay"]).abs()
    share_match = (diff <= 1).mean() if len(late) else float("nan")
    if share_match < 0.98:
        problems.append(f"delay causes match ArrDelay for only {share_match:.1%} of late flights")

    summary = {
        "month": f"{y}-{m:02d}",
        "flights": n,
        "cancelled_pct": round(100 * df["Cancelled"].mean(), 2),
        "diverted_pct": round(100 * df["Diverted"].mean(), 2),
        "on_time_arrival_pct": round(100 * (1 - flown["ArrDel15"].mean()), 2),
        "carriers": df["Reporting_Airline"].nunique(),
        "airports": df["Origin"].nunique(),
        "tail_missing_pct": round(100 * df["Tail_Number"].isna().mean(), 2),
        "causes_match_pct": round(100 * share_match, 1),
    }
    return problems, summary


def main():
    rows, all_ok = [], True
    for y, m in months(START, END):
        path = DATA / f"ontime_{y}_{m:02d}.parquet"
        if not path.exists():
            print(f"{y}-{m:02d}: MISSING FILE")
            all_ok = False
            continue
        problems, summary = check_month(path, y, m)
        if summary:
            rows.append(summary)
        if problems:
            all_ok = False
            print(f"{y}-{m:02d}: " + "; ".join(problems))
        else:
            print(f"{y}-{m:02d}: OK ({summary['flights']:,} flights)")

    table = pd.DataFrame(rows)
    out = PROJECT / "data" / "verify_monthly_summary.csv"
    table.to_csv(out, index=False)
    print("\n" + table.to_string(index=False))
    print(f"\nSummary saved to {out}")
    print("\nALL CHECKS PASSED" if all_ok else "\nSome checks need a look (see lines above)")


if __name__ == "__main__":
    main()