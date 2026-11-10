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
- Hourly tables currently contain hours with known scheduled flights only. A complete hourly
  grid, airport selection, minimum-flight rules, and analysis windows are not implemented yet.
- Monthly filenames refer to original BTS flight-date months; some UTC timestamps cross month boundaries.

The earlier run preserved 20,928,579 flights. Of those, 20,928,559 had UTC departure
timestamps; 18 had unresolved daylight-saving times and 2 had pending `2400` values.
The consolidated version retains these rules. Earlier team code is preserved in
`reference/original_pipeline/` for comparison and is not used by the new pipeline.

Only source code and documentation belong in GitHub. Data, reports, environments,
and local backups are ignored. This pipeline does not perform network analysis.
