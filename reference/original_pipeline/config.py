"""
Shared settings for the ELEC 573 delay-propagation project.

Every script imports this file, so all team members use the same definitions.
If you want to change a definition, change it HERE, rerun prepare_data.py,
and tell the team.
"""

from pathlib import Path

# ---- Paths (relative to this file, so it works on every teammate's computer) ----
PROJECT = Path(__file__).resolve().parent
RAW_DIR = PROJECT / "data" / "parquet"        # monthly files from download_bts.py
PROC_DIR = PROJECT / "data" / "processed"     # tables made by prepare_data.py
SAMPLE_DIR = PROC_DIR / "sample"              # one-month subset for quick testing
FIG_DIR = PROJECT / "figures"

# ---- Study period -----------------------------------------------------------------
START = "2023-01"
END = "2025-12"

# ---- Airports ---------------------------------------------------------------------
# The 40 airports in the FAA Emergency Order of Nov 6, 2025 (list as published by AAAE).
# TEB (Teterboro) is mainly private aviation and is not in the BTS airline data.
FAA40 = [
    "ANC", "BWI", "BOS", "CLT", "MDW", "ORD", "CVG", "DAL", "DFW", "DEN",
    "DTW", "FLL", "IAH", "LAS", "ATL", "HNL", "IND", "LAX", "SDF", "MEM",
    "MIA", "MSP", "JFK", "LGA", "EWR", "OAK", "ONT", "MCO", "PHL", "PHX",
    "PDX", "DCA", "SLC", "SAN", "SFO", "SEA", "TPA", "TEB", "IAD", "HOU",
]
N_TOP = 60                    # also include the N busiest airports by departures

# ---- Cleaning rules ---------------------------------------------------------------
DELAY_CLIP = (-60, 600)       # minutes; departure delays outside are capped
OP_HOURS_LOCAL = (6, 23)      # operating hours, local time: 6:00 <= hour < 23:00
MIN_FLIGHTS = 3               # an airport-hour needs >= this many flown departures
MAX_GAP_INTERP = 2            # fill gaps of up to this many hours (same local day)
MAX_MISSING_SHARE = 0.10      # drop an airport if >10% of its operating hours are missing
MAX_FILLED_SHARE = 0.10       # ...or if >10% are interpolated (a smoothed series biases Granger tests)

# Time zones missing from the airportsdata package (IATA -> IANA time zone)
TZ_OVERRIDES = {"PBI": "America/New_York"}

# ---- Period labels ----------------------------------------------------------------
# Event windows (inclusive). Adjust after checking news sources; keep them in one place.
EVENTS = {
    "crowdstrike_it_outage": ("2024-07-19", "2024-07-23"),
    "newark_atc_outage":     ("2025-04-28", "2025-05-31"),
    "shutdown_flight_cuts":  ("2025-11-07", "2025-11-17"),
    "shutdown_other_days":   ("2025-10-01", "2025-11-06"),
}
# A day is "weather" if national weather-delay minutes per flight are in the
# top WEATHER_QUANTILE of all days (and it is not already an event day).
WEATHER_QUANTILE = 0.90
