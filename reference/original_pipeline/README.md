# Who Spreads the Delay? — ELEC 573 project

Delay propagation among U.S. airports from BTS on-time data, 2023-01 to 2025-12.
Two kinds of networks are built from the same data and kept separate:

- **Predictive dependence** — Granger networks on hourly, seasonally adjusted delay series
  (`airport_hourly.parquet`, `*_resid` columns).
- **Physical propagation** — aircraft rotations: the same tail number flies A → B, then B → C
  on the same day (`aircraft_links/`).

## Done so far (data pipeline)

1. **Download**: BTS Reporting Carrier On-Time data, 2023-01 to 2025-12. 36 months,
   20.9M flights, all columns, stored as monthly Parquet files.
2. **Validation**: no missing months, no duplicate flights, consistent schema. The 5 delay
   causes sum to ArrDelay for 100% of flights delayed 15+ min. Extreme DepDelay values
   (> 3000 min) are real records; they are clipped in step 3.
3. **Hourly delay series** (for Granger):
   - airports: FAA 40 list + top 60 by departures = 62 candidates
   - delays clipped to [-60, 600] min
   - local times converted to UTC
   - one row per airport × hour; operating hours (6–23 local) only, ≥ 3 flights per hour
   - gaps of ≤ 2 h interpolated
   - quality filter keeps **38 airports**. 96.6% of their hours are observed, 0.6% filled,
     2.8% missing.
   - `delay_resid` = delay minus that airport's weekday × hour mean (daily/weekly cycle removed)
4. **Aircraft links** (for the physical network): 15.3M same-aircraft, same-day consecutive
   legs (A → B → C).
5. **Calendar**: each of 1,096 days labelled `event` (87), `weather` (103), or `normal` (906).

Findings so far:
- Late-arriving aircraft cause 39.9% of all delay minutes, the largest cause.
- Outbound delay rises almost one-to-one with inbound delay.
- 7 FAA-list airports fail the quality filter (IAD, IND, CVG, ONT, SDF, MEM, ANC). All but IAD
  are cargo hubs, and BTS has no cargo flights. They remain in the physical network.

Known pending change: `calendar.csv` may also label high-cancellation days as weather
(e.g. 2023-02-01, Texas ice storm). The hourly table and the links are final.

## Setup

```
python -m venv .venv
.venv\Scripts\activate            # Windows   (macOS/Linux: source .venv/bin/activate)
pip install -r requirements.txt
```

All paths come from `config.py` and are relative to the project folder, so the code runs
unchanged on every computer. Shared definitions (airport list, cleaning rules, event windows)
also live in `config.py`. **Change them only there, rerun `prepare_data.py`, and tell the team.**

## Run order

| Step | Script | What it does | Time |
|---|---|---|---|
| 1 | `download_bts.py` | Downloads the monthly BTS files into `data/parquet/` (skips months already there) | ~20–40 min |
| 2 | `verify_data.py` | Checks months, columns, duplicates, value ranges, delay causes; writes `data/verify_monthly_summary.csv` | ~1 min |
| 3 | `prepare_data.py` | Builds every analysis table in `data/processed/` and writes `prepare_report.txt` | a few min |
| – | `check_data.py` | Optional: summary and two figures in `figures/` | ~1 min |

Teammates who receive `data/processed/` from the shared drive can skip steps 1–3.

## Folder layout

```
project/
  config.py  download_bts.py  verify_data.py  prepare_data.py  check_data.py
  requirements.txt  README.md
  data/
    parquet/            ontime_YYYY_MM.parquet   raw monthly BTS files (all columns)
    processed/
      airport_hourly.parquet                     main table for Granger analysis
      aircraft_links/   links_YYYY_MM.parquet    one file per month
      airports.csv  calendar.csv  prepare_report.txt
      sample/           May 2025 only (hourly + links) for quick tests
  figures/
```

## Cleaning rules (from `config.py`)

- Departure delays are clipped to **[-60, 600] min**. The raw mean is kept as `mean_dep_delay_raw`.
- BTS times are local. Each flight's scheduled departure hour is converted to **UTC** with the
  origin airport's time zone, so hours line up across time zones. Ambiguous DST fall-back
  hours (1–2 am) are dropped.
- Airports: the FAA 40-airport list (Nov 2025 order; TEB is not in BTS) plus the 60 busiest.
- An airport-hour counts as **observed** if it falls in operating hours (6:00–22:59 local) and
  has **≥ 3** flown departures (not cancelled or diverted).
- Gaps of **≤ 2 hours** within the same local day are filled by linear interpolation
  (`filled = True`). Longer gaps stay missing.
- Airports with more than **10%** of their operating hours missing, or more than **10%**
  interpolated, are marked `included = False` (interpolation smooths the series and can
  create spurious Granger links). They are kept in the files so this rule can be changed later.
- Seasonal adjustment: `*_resid` = value − that airport's mean for the same local weekday × hour,
  over the whole period.
- `TZ_OVERRIDES` in `config.py` adds time zones the `airportsdata` package lacks (e.g. PBI).

## Data dictionary

### `airport_hourly.parquet` — one row per airport × UTC hour

| Column | Meaning |
|---|---|
| `Origin` | airport (IATA) |
| `ts_utc`, `ts_local` | hour start, in UTC and in local time |
| `local_date`, `local_hour`, `weekday` | local calendar fields (weekday 0 = Monday) |
| `op_hour` | inside operating hours (6–22 local) |
| `n_sched`, `n_cancelled`, `n_flown` | scheduled, cancelled, and flown departures in that hour |
| `mean_dep_delay_raw` | mean departure delay of flown flights, not clipped |
| `mean_dep_delay` | same, after clipping |
| `p_del15` | share of flown departures delayed ≥ 15 min |
| `sum_CarrierDelay` … `sum_LateAircraftDelay` | total cause minutes (BTS fills causes only for flights arriving ≥ 15 min late) |
| `observed` | op hour with ≥ 3 flown departures |
| `delay`, `p15` | **cleaned series**: `mean_dep_delay` / `p_del15` where observed, short gaps filled, else NaN |
| `filled` | value came from interpolation |
| `delay_resid`, `p15_resid` | **seasonally adjusted series, use these for Granger tests** |
| `included` | airport passes the missing-data rule |

Typical filter: `df[df.included & df.op_hour]`. For Granger tests, pivot to wide format
(`ts_utc` × `Origin`) and use only operating hours. Never interpolate across nights.

### `aircraft_links/links_YYYY_MM.parquet` — one row per aircraft turnaround

Pairs of consecutive same-day legs of one tail number where leg 1's destination equals
leg 2's origin.

| Column | Meaning |
|---|---|
| `FlightDate`, `ts_utc` | date, and UTC departure hour of the 2nd leg |
| `Reporting_Airline`, `Tail_Number` | carrier, aircraft |
| `prev_origin` → `airport` → `next_dest` | where the aircraft came from → turnaround airport → where it goes next |
| `prev_arr_delay` | arrival delay of the inbound leg |
| `dep_delay` | departure delay of the outbound leg (clipped) |
| `late_aircraft_delay` | BTS LateAircraftDelay of the outbound leg (0 if not reported) |
| `sched_turn_min` | scheduled turnaround time in minutes |

The physical network is the edge `prev_origin → airport`, weighted by
`late_aircraft_delay` (or the number of links with `late_aircraft_delay > 0`).

Read all months at once: `pd.read_parquet("data/processed/aircraft_links")`.

Before analysis, keep `10 <= sched_turn_min <= 360`. About 1% of links have impossible
negative turn times (tail-number errors), and about 2% sit on the ground for more than 6 h.

### `airports.csv`

`airport, departures, volume_rank, faa40, top_n, tz, op_hours, missing_share, filled_share,
mean_flights_per_op_hour, included, exclude_reason`

### `calendar.csv` — one row per date

`n_flights, cancel_rate, mean_dep_delay, weather_min_per_flight, nas_min_per_flight`, plus:

- `event` — name of the event window from `config.EVENTS`, or blank
- `weather_day` — national weather-delay minutes per flight in the top 10% of days
- `label` — `event` / `weather` / `normal` (event takes priority)

Use `label` to build period-specific networks (e.g. normal days vs. `shutdown_flight_cuts`).

## Sharing

- **Code** → GitHub. `data/` is in `.gitignore`, since GitHub rejects files over 100 MB.
- **Data** → upload `data/processed/` to the shared Rice Box or Google Drive folder. Put it in
  the same place in your own project folder (`project/data/processed/`).
- If something looks wrong, check `prepare_report.txt` first. It lists coverage per airport,
  how much was filled, and the event day ranges.
