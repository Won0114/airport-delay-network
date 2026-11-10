"""
Data preparation pipeline: monthly BTS files -> analysis-ready tables.

Run once after download_bts.py (and verify_data.py):
    python prepare_data.py

Steps
  1. Pick airports: FAA 40 list + N_TOP busiest (config.py).
  2. For every month: clip extreme delays, convert scheduled departure hours to UTC,
     aggregate to airport-hour, and build aircraft-rotation links (same tail number).
  3. Build a complete hourly UTC grid per airport, keep operating hours only,
     require MIN_FLIGHTS per hour, fill short gaps, and compute missing shares.
  4. Remove the daily/weekly cycle: subtract each airport's mean for the same
     local weekday x hour  ->  *_resid columns (use these for Granger tests).
  5. Label each day: event / weather / normal.
  6. Save tables, a one-month sample, and a text report.

Outputs in data/processed/  (see README.md for column descriptions)
  airport_hourly.parquet   airports.csv   calendar.csv
  aircraft_links/ (one file per month)    sample/   prepare_report.txt
"""

import time

import numpy as np
import pandas as pd

import config as C

FLIGHT_COLS = [
    "FlightDate", "Reporting_Airline", "Tail_Number", "Origin", "Dest",
    "CRSDepTime", "CRSArrTime", "DepDelay", "DepDel15", "ArrDelay",
    "Cancelled", "Diverted",
    "CarrierDelay", "WeatherDelay", "NASDelay", "SecurityDelay", "LateAircraftDelay",
]
CAUSES = ["CarrierDelay", "WeatherDelay", "NASDelay", "SecurityDelay", "LateAircraftDelay"]


# --------------------------------------------------------------------------- I/O
def month_files():
    files = sorted(C.RAW_DIR.glob("ontime_*.parquet"))
    keep = []
    for f in files:
        y, m = f.stem.split("_")[1:3]
        if C.START <= f"{y}-{m}" <= C.END:
            keep.append(f)
    if not keep:
        raise SystemExit(f"No monthly files for {C.START}..{C.END} in {C.RAW_DIR}")
    return keep


def read_month(path, columns):
    return pd.read_parquet(path, columns=columns)


def save(df, path):
    df.to_parquet(path, index=False)


def load_timezones():
    try:
        import airportsdata
    except ImportError:
        raise SystemExit("Please install airportsdata first:  pip install airportsdata")
    tz = {k: v["tz"] for k, v in airportsdata.load("IATA").items() if v.get("tz")}
    tz.update(getattr(C, "TZ_OVERRIDES", {}))
    return tz


# ------------------------------------------------------------------ step 1: airports
def choose_airports(files, tz_map):
    counts = None
    for f in files:
        c = read_month(f, ["Origin"])["Origin"].value_counts()
        counts = c if counts is None else counts.add(c, fill_value=0)
    counts = counts.sort_values(ascending=False).astype(int)
    ap = pd.DataFrame({"airport": counts.index.astype(str), "departures": counts.values})
    ap["volume_rank"] = np.arange(1, len(ap) + 1)
    ap["faa40"] = ap["airport"].isin(C.FAA40)
    ap["top_n"] = ap["volume_rank"] <= C.N_TOP
    ap = ap[ap["faa40"] | ap["top_n"]].copy()
    ap["tz"] = ap["airport"].map(tz_map)
    absent = sorted(set(C.FAA40) - set(counts.index))
    return ap.reset_index(drop=True), absent


# ------------------------------------------------------------- step 2: per month
def hhmm_minutes(x):
    x = x.astype("float")
    return (x // 100 % 24) * 60 + x % 100


def localize_to_utc(ts_local, airports, tz_map):
    """Local naive timestamps -> naive UTC, per airport time zone."""
    out = pd.Series(pd.NaT, index=ts_local.index, dtype="datetime64[ns]")
    for ap, idx in airports.groupby(airports).groups.items():
        tz = tz_map.get(ap)
        if tz is None:
            continue
        loc = ts_local.loc[idx].dt.tz_localize(tz, ambiguous="NaT", nonexistent="shift_forward")
        out.loc[idx] = loc.dt.tz_convert("UTC").dt.tz_localize(None).astype("datetime64[ns]")
    return out


def prepare_flights(df, tz_map):
    df = df.copy()
    df["FlightDate"] = pd.to_datetime(df["FlightDate"])
    df["dep_min"] = hhmm_minutes(df["CRSDepTime"])
    df["arr_min"] = hhmm_minutes(df["CRSArrTime"])
    df["ts_local"] = df["FlightDate"] + pd.to_timedelta((df["dep_min"] // 60) * 60, unit="m")
    df["ts_utc"] = localize_to_utc(df["ts_local"], df["Origin"], tz_map)
    df["flown"] = (df["Cancelled"] == 0) & (df["Diverted"] == 0)
    df["dep_delay_clip"] = df["DepDelay"].clip(*C.DELAY_CLIP)
    return df


def hourly_counts(df, airports):
    d = df[df["Origin"].isin(airports) & df["ts_utc"].notna()]
    keys = ["Origin", "ts_utc"]
    g_all = d.groupby(keys)
    f = d[d["flown"]]
    g = f.groupby(keys)
    h = pd.DataFrame({"n_sched": g_all.size(), "n_cancelled": g_all["Cancelled"].sum()})
    h["n_flown"] = g.size()
    h["mean_dep_delay_raw"] = g["DepDelay"].mean()
    h["mean_dep_delay"] = g["dep_delay_clip"].mean()
    h["p_del15"] = g["DepDel15"].mean()
    for c in CAUSES:
        h["sum_" + c] = g[c].sum()
    return h.reset_index()


def aircraft_links(df):
    d = df[df["flown"] & df["Tail_Number"].notna()]
    d = d.sort_values(["Tail_Number", "FlightDate", "dep_min"])
    prev = d.groupby(["Tail_Number", "FlightDate"]).shift(1)
    ok = prev["Origin"].notna() & (prev["Dest"] == d["Origin"])
    cur, prv = d[ok], prev[ok]
    return pd.DataFrame({
        "FlightDate": cur["FlightDate"].values,
        "ts_utc": cur["ts_utc"].values,               # departure hour of the 2nd leg
        "Reporting_Airline": cur["Reporting_Airline"].values,
        "Tail_Number": cur["Tail_Number"].values,
        "prev_origin": prv["Origin"].values,          # inbound aircraft came from here
        "airport": cur["Origin"].values,              # turnaround airport
        "next_dest": cur["Dest"].values,
        "prev_arr_delay": prv["ArrDelay"].values,
        "dep_delay": cur["dep_delay_clip"].values,
        "late_aircraft_delay": cur["LateAircraftDelay"].fillna(0).values,
        "sched_turn_min": (cur["dep_min"] - prv["arr_min"]).values,
    })


def daily_stats(df):
    g = df.groupby("FlightDate")
    f = df[df["flown"]].groupby("FlightDate")
    return pd.DataFrame({
        "n_flights": g.size(),
        "cancel_rate": g["Cancelled"].mean(),
        "mean_dep_delay": f["dep_delay_clip"].mean(),
        "weather_min_per_flight": f["WeatherDelay"].sum() / f.size(),
        "nas_min_per_flight": f["NASDelay"].sum() / f.size(),
    })


# ------------------------------------------------- step 3+4: grid, clean, seasonal
def fill_short_gaps(df, value, group_cols, max_gap):
    """Linear interpolation of gaps of <= max_gap rows inside each group (vectorized)."""
    d = df
    pos = d.groupby(group_cols).cumcount()
    known = d[value].notna()
    kpos = pos.where(known)
    kval = d[value].where(known)
    grp = [d[c] for c in group_cols]
    prev_pos = kpos.groupby(grp).ffill()
    next_pos = kpos.groupby(grp).bfill()
    prev_val = kval.groupby(grp).ffill()
    next_val = kval.groupby(grp).bfill()
    gap = next_pos - prev_pos - 1
    can = (~known) & prev_pos.notna() & next_pos.notna() & (gap <= max_gap)
    interp = prev_val + (next_val - prev_val) * (pos - prev_pos) / (next_pos - prev_pos)
    filled = d[value].where(~can, interp)
    return filled, can


def build_grid(hourly, airports_df):
    start = pd.Timestamp(C.START + "-01")
    end = pd.Timestamp(C.END + "-01") + pd.offsets.MonthEnd(1) + pd.Timedelta(hours=23)
    # pad by a day on each side so local operating hours at the edges are complete
    idx = pd.date_range(start - pd.Timedelta(days=1), end + pd.Timedelta(days=1), freq="h")
    parts = []
    for _, row in airports_df.iterrows():
        g = pd.DataFrame({"Origin": row["airport"], "ts_utc": idx})
        loc = g["ts_utc"].dt.tz_localize("UTC").dt.tz_convert(row["tz"]).dt.tz_localize(None)
        g["ts_local"] = loc
        parts.append(g)
    grid = pd.concat(parts, ignore_index=True)
    grid = grid[(grid["ts_local"] >= start) & (grid["ts_local"] <= end)]
    grid = grid.merge(hourly, on=["Origin", "ts_utc"], how="left")
    for c in ["n_sched", "n_flown", "n_cancelled"]:
        grid[c] = grid[c].fillna(0).astype(int)
    grid["local_date"] = grid["ts_local"].dt.normalize()
    grid["local_hour"] = grid["ts_local"].dt.hour
    grid["weekday"] = grid["ts_local"].dt.dayofweek
    lo, hi = C.OP_HOURS_LOCAL
    grid["op_hour"] = grid["local_hour"].between(lo, hi - 1)
    return grid.sort_values(["Origin", "ts_utc"]).reset_index(drop=True)


def clean_series(grid):
    ok = grid["op_hour"] & (grid["n_flown"] >= C.MIN_FLIGHTS)
    grid["observed"] = ok
    for src, dst in [("mean_dep_delay", "delay"), ("p_del15", "p15")]:
        grid[dst] = grid[src].where(ok)
    op = grid["op_hour"]
    sub = grid.loc[op, ["Origin", "local_date", "delay", "p15"]]
    for col in ["delay", "p15"]:
        filled, was_filled = fill_short_gaps(sub, col, ["Origin", "local_date"], C.MAX_GAP_INTERP)
        grid.loc[op, col] = filled
        if col == "delay":
            grid["filled"] = False
            grid.loc[op, "filled"] = was_filled
    # seasonal adjustment: subtract airport x weekday x hour mean
    keys = ["Origin", "weekday", "local_hour"]
    for col in ["delay", "p15"]:
        mean = grid.groupby(keys)[col].transform("mean")
        grid[col + "_resid"] = grid[col] - mean
    return grid


def missing_shares(grid):
    op = grid[grid["op_hour"]]
    s = op.groupby("Origin").agg(
        op_hours=("delay", "size"),
        missing_share=("delay", lambda x: x.isna().mean()),
        filled_share=("filled", "mean"),
        mean_flights_per_op_hour=("n_flown", "mean"),
    )
    return s.reset_index().rename(columns={"Origin": "airport"})


# ----------------------------------------------------------------- step 5: calendar
def build_calendar(daily):
    cal = daily.sort_index().reset_index().rename(columns={"FlightDate": "date"})
    cal["event"] = ""
    for name, (a, b) in C.EVENTS.items():
        m = cal["date"].between(pd.Timestamp(a), pd.Timestamp(b)) & (cal["event"] == "")
        cal.loc[m, "event"] = name
    thr = cal["weather_min_per_flight"].quantile(C.WEATHER_QUANTILE)
    cal["weather_day"] = cal["weather_min_per_flight"] >= thr
    cal["label"] = np.where(cal["event"] != "", "event",
                            np.where(cal["weather_day"], "weather", "normal"))
    return cal, thr


# --------------------------------------------------------------------------- main
def main():
    t0 = time.time()
    C.PROC_DIR.mkdir(parents=True, exist_ok=True)
    C.SAMPLE_DIR.mkdir(parents=True, exist_ok=True)
    tz_map = load_timezones()
    files = month_files()
    print(f"{len(files)} monthly files, {C.START} to {C.END}")

    airports, absent = choose_airports(files, tz_map)
    no_tz = airports.loc[airports["tz"].isna(), "airport"].tolist()
    airports = airports[airports["tz"].notna()].reset_index(drop=True)
    ap_set = set(airports["airport"])
    print(f"Candidate airports: {len(airports)} (FAA list not in BTS data: {absent})")

    # aircraft links are large -> one file per month in a folder (read the folder later)
    link_dir = C.PROC_DIR / "aircraft_links"
    link_dir.mkdir(parents=True, exist_ok=True)
    hourly_parts, daily_parts = [], []
    n_links = n_links_late = 0
    for f in files:
        df = prepare_flights(read_month(f, FLIGHT_COLS), tz_map)
        hourly_parts.append(hourly_counts(df, ap_set))
        daily_parts.append(daily_stats(df))
        links = aircraft_links(df)
        save(links, link_dir / f.name.replace("ontime_", "links_"))
        n_links += len(links)
        n_links_late += int((links["late_aircraft_delay"] > 0).sum())
        if f.stem.endswith("2025_05"):
            save(links, C.SAMPLE_DIR / "aircraft_links_2025_05.parquet")
        print(f"  {f.name}: {len(df):,} flights, {len(links):,} aircraft links")

    hourly = pd.concat(hourly_parts, ignore_index=True)
    dup = hourly.duplicated(["Origin", "ts_utc"])
    if dup.any():  # should not happen: local hour -> UTC hour is one-to-one
        print(f"  warning: dropping {dup.sum()} duplicated airport-hours")
        hourly = hourly[~dup]
    daily = pd.concat(daily_parts)

    print("Building hourly grid and cleaning series...")
    grid = clean_series(build_grid(hourly, airports))

    miss = missing_shares(grid)
    airports = airports.merge(miss, on="airport", how="left")
    too_missing = airports["missing_share"] > C.MAX_MISSING_SHARE
    too_filled = airports["filled_share"] > C.MAX_FILLED_SHARE
    airports["included"] = ~too_missing & ~too_filled
    airports["exclude_reason"] = np.select(
        [too_missing & too_filled, too_missing, too_filled],
        [f"missing_share > {C.MAX_MISSING_SHARE} and filled_share > {C.MAX_FILLED_SHARE}",
         f"missing_share > {C.MAX_MISSING_SHARE}", f"filled_share > {C.MAX_FILLED_SHARE}"],
        default="")
    keep = set(airports.loc[airports["included"], "airport"])
    grid["included"] = grid["Origin"].isin(keep)

    cal, thr = build_calendar(daily)

    # ---- save
    save(grid, C.PROC_DIR / "airport_hourly.parquet")
    airports.to_csv(C.PROC_DIR / "airports.csv", index=False)
    cal.to_csv(C.PROC_DIR / "calendar.csv", index=False)

    s_mask = (grid["ts_local"] >= "2025-05-01") & (grid["ts_local"] < "2025-06-01")
    save(grid[s_mask], C.SAMPLE_DIR / "airport_hourly_2025_05.parquet")

    # ---- report
    lines = [
        f"Prepared {pd.Timestamp.now():%Y-%m-%d %H:%M} | period {C.START}..{C.END}",
        f"Rules: clip {C.DELAY_CLIP} min, operating hours {C.OP_HOURS_LOCAL} local, "
        f">= {C.MIN_FLIGHTS} flights/hour, fill gaps <= {C.MAX_GAP_INTERP} h, "
        f"drop airports with missing share > {C.MAX_MISSING_SHARE} or filled share > {C.MAX_FILLED_SHARE}",
        "",
        f"Airports: {len(airports)} candidates, {len(keep)} included",
        f"  FAA list airports not in BTS data: {absent}",
        f"  airports without time zone (dropped): {no_tz}",
        "  excluded:",
        airports.loc[~airports["included"], ["airport", "faa40", "exclude_reason"]].to_string(index=False),
        "",
        "Hourly table (operating hours of included airports):",
    ]
    op_inc = grid[grid["op_hour"] & grid["included"]]
    lines += [
        f"  rows: {len(op_inc):,}",
        f"  observed directly: {op_inc['observed'].mean():.1%}",
        f"  filled by interpolation: {op_inc['filled'].mean():.1%}",
        f"  still missing: {op_inc['delay'].isna().mean():.1%}",
        f"  hours where clipping changed the mean: "
        f"{((op_inc['mean_dep_delay_raw'] - op_inc['mean_dep_delay']).abs() > 1e-9).mean():.2%}",
        "",
        f"Aircraft links: {n_links:,}; with late-aircraft delay > 0: "
        f"{n_links_late / max(n_links, 1):.1%}",
        "",
        f"Calendar: weather-day threshold = {thr:.2f} weather-delay min per flight",
        cal["label"].value_counts().to_string(),
        "",
        "Event days:",
        cal[cal["event"] != ""].groupby("event")["date"].agg(["min", "max", "count"]).to_string(),
        "",
        "Lowest-coverage included airports:",
        airports[airports["included"]].sort_values("missing_share", ascending=False)
        .head(10)[["airport", "volume_rank", "faa40", "missing_share", "filled_share"]].to_string(index=False),
        "",
        "All candidate airports by missing share (use this to judge the thresholds):",
        airports.sort_values("missing_share")[
            ["airport", "volume_rank", "faa40", "mean_flights_per_op_hour", "missing_share",
             "filled_share", "included"]
        ].round(3).to_string(index=False),
    ]
    report = "\n".join(lines)
    (C.PROC_DIR / "prepare_report.txt").write_text(report, encoding="utf-8")
    print("\n" + report)
    print(f"\nDone in {time.time() - t0:.0f}s. Outputs in {C.PROC_DIR}")


if __name__ == "__main__":
    main()
