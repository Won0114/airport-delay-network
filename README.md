# ELEC 573: BTS data pipeline

Study period: January 2023 through December 2025.

## Run

From this folder in PowerShell, after creating `.venv`:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe download_bts.py
.\.venv\Scripts\python.exe prepare_data.py
```

The downloader skips existing valid ZIPs. Preparation reads one month at a time,
validates it, cleans it, converts scheduled departures to UTC, and aggregates it.
No separate inspection scripts or pre-generated timezone file are required.
For a smaller run, add `--start 2023-01 --end 2023-01` to `prepare_data.py`.
Existing output files for the selected months are replaced on rerun.

## Outputs (local only)

- `raw/bts_YYYY_MM.zip`: unchanged original BTS downloads.
- `processed/flights_utc/flights_YYYY_MM.parquet`: flight records with quality flags and UTC times.
- `processed/hourly/airport_hourly_YYYY_MM.parquet`: scheduled-departure airport-hour aggregates.
- `reports/pipeline/`: monthly validation, UTC summary, and unresolved records for the requested range.

## Processing rules

- Preserve flight rows and original values. Remove only entirely empty unnamed columns.
- Report missing dates, unexpected months, duplicate flight keys, missing airports,
  invalid HHMM times, and invalid cancellation/diversion flags; stop on structural errors.
- Mark missing/nonpositive scheduled elapsed times, missing tail numbers, and missing
  delay values among completed non-diverted flights. Do not replace missing values with zero.
- Use IANA airport timezones, with an explicit `PBI: America/New_York` override.
- Preserve scheduled departures coded `2400` as unresolved pending date-convention review.
- Leave ambiguous/nonexistent daylight-saving times unresolved, with flags and an exception report.
- Group by origin and UTC scheduled-departure hour. Cancellation rates use all timestamped
  scheduled flights; delay statistics use completed, non-diverted flights with valid values.
- Preserve negative and extreme delays. Do not interpolate, clip, or remove seasonal patterns.
- Arrival delay is a statistic of outbound flight cohorts, not a destination-arrival-hour series.
- Monthly hourly tables retain known scheduled hours. The final table uses a complete,
  shared UTC grid for 2023 baseline top-60 airports plus FAA-listed airports present
  in that baseline (61 candidates). This is candidate selection, not proof that
  each airport has sufficient observations for Granger analysis.
- No airport is removed based on delay outcomes. No minimum-flight cutoff is imposed;
  use the valid-flight counts when setting analysis-specific thresholds.
- Monthly filenames refer to original BTS flight-date months; some UTC timestamps cross month boundaries.

The earlier run preserved 20,928,579 flights. Of those, 20,928,559 had UTC departure
timestamps; 18 had unresolved daylight-saving times and 2 had pending `2400` values.
The consolidated version retains these rules. Earlier team code is preserved in
`reference/original_pipeline/` for comparison and is not used by the new pipeline.

Only source code and documentation belong in GitHub. Data, reports, environments,
and local backups are ignored. This pipeline does not perform network analysis.

## Final dataset

For existing cleaned monthly files, finish without redownloading or recleaning:

```powershell
.\.venv\Scripts\python.exe prepare_data.py --finalize-only
```

A normal full-period `prepare_data.py` run now also performs this finalization.
Finalization requires all 12 baseline flight files for 2023, the requested months'
cleaned and hourly files, and their original ZIPs for row-count verification.

- `processed/final/airport_hourly.parquet`: one row per candidate airport and UTC hour.
- `processed/final/airports.csv`: selected airports, baseline ranks, FAA flags, timezones.
- `reports/pipeline/airport_coverage.csv`: per-airport counts and missing-delay coverage.
- `reports/pipeline/final_checks.csv`: source, flight, and hourly count reconciliation per month.
- `reports/pipeline/final_summary.json`: final dimensions and verification result.
- `reports/pipeline/final_utc_exceptions.csv`: all unresolved records in the requested range.

The UTC grid is padded by one day on each side of the local study period to preserve
all local-date flights. `source_covered` marks hours whose airport-local dates lie
inside the requested study period. Outside coverage, counts and statistics remain
missing; do not interpret those hours as zero operations.

Within coverage, counts represent records with known UTC departure times. Empty
hours receive zero counts, but delay statistics and cancellation rates remain
missing. Hours that may contain an unresolved flight are marked
`flag_unresolved_flights_possible`; their counts are lower bounds.
`counts_complete` distinguishes fully assigned counts. For DST ambiguity both
candidate UTC hours are marked; pending `2400` records mark both possible midnight
dates. No timestamp is guessed. `no_scheduled_flights` is true only when counts are
complete and zero. `delay_available` requires complete counts and an observed mean.

The final table retains every hour; do not drop gaps and concatenate observations
before creating time lags. `mean_arr_delay` still describes outbound flight cohorts.
No network, Granger, forecasting, interpolation, or seasonal adjustment is performed.
