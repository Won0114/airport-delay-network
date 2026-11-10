from pathlib import Path
import time
import zipfile

import requests


# Store original ZIP files beside this script.
SAVE_DIR = Path(__file__).resolve().parent / "raw"
SAVE_DIR.mkdir(exist_ok=True)

BASE_URL = "https://transtats.bts.gov/PREZIP/"


def valid_zip(path):
    if not path.exists() or not zipfile.is_zipfile(path):
        return False

    try:
        with zipfile.ZipFile(path) as archive:
            return archive.testzip() is None
    except (OSError, zipfile.BadZipFile):
        return False


def download_month(session, year, month):
    target = SAVE_DIR / f"bts_{year}_{month:02d}.zip"

    if valid_zip(target):
        print(f"{year}-{month:02d}: Already downloaded.", flush=True)
        return

    temporary = target.with_suffix(".part")

    # BTS uses two filename formats across the study period.
    names = [
        f"On_Time_Reporting_Carrier_On_Time_Performance_1987_present_{year}_{month}.zip",
        f"On_Time_Reporting_Carrier_On_Time_Performance_(1987_present)_{year}_{month}.zip",
    ]

    for attempt in range(3):
        try:
            for name in names:
                with session.get(
                    BASE_URL + name,
                    stream=True,
                    timeout=(30, 120),
                ) as response:
                    if response.status_code == 404:
                        continue

                    response.raise_for_status()

                    with temporary.open("wb") as output:
                        for chunk in response.iter_content(1024 * 1024):
                            if chunk:
                                output.write(chunk)

                if valid_zip(temporary):
                    temporary.replace(target)
                    print(
                        f"{year}-{month:02d}: Download complete "
                        f"({target.stat().st_size / 1_000_000:.1f} MB).",
                        flush=True,
                    )
                    return

            raise RuntimeError("No valid ZIP file was received.")

        except (requests.RequestException, OSError, RuntimeError) as error:
            print(
                f"{year}-{month:02d}: Attempt {attempt + 1}/3 failed: {error}",
                flush=True,
            )
            if attempt < 2:
                time.sleep(5)

    raise RuntimeError(
        f"Download failed for {year}-{month:02d}. Run the script again."
    )


def main():
    with requests.Session() as session:
        session.headers["User-Agent"] = "ELEC573-course-project"

        for year in range(2023, 2026):
            for month in range(1, 13):
                print(
                    f"{year}-{month:02d}: Checking and downloading...",
                    flush=True,
                )
                download_month(session, year, month)

    print("All 36 monthly files downloaded successfully.", flush=True)


if __name__ == "__main__":
    main()