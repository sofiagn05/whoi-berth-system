"""One-time export: pull each year tab out of the source .ods workbook and
write it as a plain CSV grid, in the same raw layout app.import_schedule
already parses. Kept as a script (not folded into import_schedule.py)
because it needs pandas + odfpy, which the running app itself does not --
those are a one-off tooling dependency, not a runtime one.

Usage:
    pip install pandas odfpy
    python scripts/export_ods_years.py data/dock_schedule_workbook.ods data/years
"""

import csv
import sys
from pathlib import Path

import pandas as pd

YEAR_RANGE = range(1997, 2020)  # the year tabs in the source workbook


def cell_str(value) -> str:
    if pd.isna(value):
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))  # avoid "1.0" for what should be day number "1"
    return str(value).strip()


def export(workbook_path: Path, out_dir: Path):
    out_dir.mkdir(parents=True, exist_ok=True)
    for year in YEAR_RANGE:
        sheet = str(year)
        df = pd.read_excel(workbook_path, sheet_name=sheet, engine="odf", header=None)
        out_path = out_dir / f"dock_schedule_{year}.csv"
        with open(out_path, "w", newline="") as f:
            writer = csv.writer(f)
            for _, row in df.iterrows():
                writer.writerow([cell_str(v) for v in row])
        print(f"{sheet}: {df.shape} -> {out_path}")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print(f"Usage: python {sys.argv[0]} <workbook.ods> <output_dir>", file=sys.stderr)
        sys.exit(1)
    export(Path(sys.argv[1]), Path(sys.argv[2]))
