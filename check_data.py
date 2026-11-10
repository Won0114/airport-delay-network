"""
Quick sanity check + first look at the BTS Parquet files.

Run it after download_bts.py. It prints a data summary and saves two figures
into  <project>/figures/  that can go straight into the progress report:
  - ewr_daily_delay.png   : daily mean departure delay at EWR vs. other hubs
  - top_airports.png      : the 20 busiest airports by departures

Needs: pip install pandas pyarrow matplotlib
"""

from pathlib import Path

import matplotlib.pyplot as plt
import pandas as pd

# ---- Settings ----------------------------------------------------------
import config as C

DATA = C.RAW_DIR
FIGS = C.FIG_DIR
COMPARE = ["EWR", "JFK", "LGA", "PHL", "ORD", "ATL"]  # airports to plot
# -------------------------------------------------------------------------


def load(data_dir: Path) -> pd.DataFrame:
    files = sorted(data_dir.glob("ontime_*.parquet"))
    if not files:
        raise SystemExit(f"No Parquet files found in {data_dir}")
    print(f"Reading {len(files)} file(s): {[f.name for f in files]}")
    # read only the columns used here (all ~110 columns for 3 years would not fit in memory)
    cols = ["FlightDate", "Reporting_Airline", "Tail_Number", "Origin", "Dest", "Cancelled",
            "Diverted", "DepDelay", "DepDel15", "CarrierDelay", "WeatherDelay", "NASDelay",
            "SecurityDelay", "LateAircraftDelay"]
    df = pd.concat((pd.read_parquet(f, columns=cols) for f in files), ignore_index=True)
    df["FlightDate"] = pd.to_datetime(df["FlightDate"])
    return df


def summary(df: pd.DataFrame) -> None:
    print("\n=== Basic counts ===")
    print(f"Flights:            {len(df):,}")
    print(f"Date range:         {df['FlightDate'].min().date()} to {df['FlightDate'].max().date()}")
    print(f"Carriers:           {df['Reporting_Airline'].nunique()}")
    print(f"Origin airports:    {df['Origin'].nunique()}")
    print(f"Directed routes:    {df.groupby(['Origin', 'Dest']).ngroups:,}")
    print(f"Unique tail numbers:{df['Tail_Number'].nunique():,}")

    print("\n=== Data quality ===")
    print(f"Cancelled:          {df['Cancelled'].mean():.2%}")
    print(f"Diverted:           {df['Diverted'].mean():.2%}")
    print(f"Missing Tail_Number:{df['Tail_Number'].isna().mean():.2%}")
    flown = df[(df['Cancelled'] == 0) & (df['Diverted'] == 0)]
    print(f"Missing DepDelay (flown flights): {flown['DepDelay'].isna().mean():.2%}")

    print("\n=== Delays (flown flights) ===")
    print(f"Mean departure delay:   {flown['DepDelay'].mean():.1f} min")
    print(f"Share delayed 15+ min:  {flown['DepDel15'].mean():.1%}")
    causes = ["CarrierDelay", "WeatherDelay", "NASDelay", "SecurityDelay", "LateAircraftDelay"]
    total = flown[causes].sum()
    print("Share of delay minutes by cause:")
    print((total / total.sum()).map("{:.1%}".format).to_string())


def plot_top_airports(df: pd.DataFrame) -> None:
    top = df['Origin'].value_counts().head(20)
    print("\n=== 20 busiest airports (departures) ===")
    print(top.to_string())
    ax = top[::-1].plot.barh(figsize=(6, 6), color="#4a7ab5")
    ax.set_xlabel("Departures")
    ax.set_title("20 busiest airports")
    plt.tight_layout()
    plt.savefig(FIGS / "top_airports.png", dpi=200)
    plt.close()


def plot_daily_delay(df: pd.DataFrame) -> None:
    flown = df[(df['Cancelled'] == 0) & (df['Diverted'] == 0)]
    sub = flown[flown['Origin'].isin(COMPARE)]
    daily = sub.groupby(['FlightDate', 'Origin'])['DepDelay'].mean().unstack()
    fig, ax = plt.subplots(figsize=(9, 4))
    for ap in COMPARE:
        if ap in daily:
            lw, alpha = (2.2, 1.0) if ap == "EWR" else (1.0, 0.6)
            ax.plot(daily.index, daily[ap], label=ap, linewidth=lw, alpha=alpha)
    outage = pd.Timestamp("2025-04-28")
    if daily.index.min() <= outage <= daily.index.max():
        ax.axvline(outage, color="red", linestyle="--", linewidth=1)
        ax.text(outage, ax.get_ylim()[1] * 0.95, " Newark outage (Apr 28)", color="red", fontsize=8)
    ax.set_ylabel("Mean departure delay (min)")
    ax.set_title("Daily mean departure delay")
    ax.legend(ncol=3, fontsize=8)
    plt.tight_layout()
    plt.savefig(FIGS / "ewr_daily_delay.png", dpi=200)
    plt.close()

    # cancellations are often the clearest signal of an ATC disruption
    canc = df[df['Origin'].isin(COMPARE)].groupby(['FlightDate', 'Origin'])['Cancelled'].mean().unstack()
    print("\n=== Days with the highest EWR cancellation rate ===")
    if "EWR" in canc:
        print(canc["EWR"].sort_values(ascending=False).head(10).map("{:.1%}".format).to_string())


def main():
    FIGS.mkdir(parents=True, exist_ok=True)
    df = load(DATA)
    summary(df)
    plot_top_airports(df)
    plot_daily_delay(df)
    print(f"\nFigures saved to {FIGS}")


if __name__ == "__main__":
    main()
