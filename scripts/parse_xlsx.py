#!/usr/bin/env python3
"""Turn the "Budgeted Employees as of July 1" spreadsheets into per-year CSVs.

One spreadsheet row is one *appointment*, not one person. In 2025-26, 12,785
people held 14,154 appointments: Aviva Abosch appears three times (Chairperson
$100,000 + Professor $40,400 + an endowed chair $40,000). Anything that treats a
row as a person will both overcount heads and understate what individuals make,
so this parser keeps every row and lets the consumer aggregate.

The spreadsheet is also this project's authority on how a name is spelled.
Roster PDF extraction mangles names -- it bleeds job titles into them
("Adkins, Dennis D Plumber/Pipefitter"), truncates them ("Abdurakhmonov"), and
flattens case ("Abdalla, Osama Ib" for "Abdalla, Osama IB"). About 2,100 of the
12,760 names in a PDF-derived file for 2025-26 carry a defect of that kind.
reconcile.py repairs those against this output.

Two column names are traps:

  * The spreadsheet's "Position" column holds a job TITLE. The roster PDF's
    "Position" column holds a numeric seat ID. They are unrelated, and the seat
    ID is not a person -- 1,002 of them changed occupant between 2024-25 and
    2025-26. This parser emits the spreadsheet's as "Title".
  * "Campus" is UNCA for Central Administration in older years and UNOP for the
    renamed Office of the President later. Both are kept as published;
    build_people.py is what treats them as continuous.

    python3 scripts/parse_xlsx.py
    python3 scripts/parse_xlsx.py --year 2025-2026
"""

import argparse
import csv
import re
import sys
from pathlib import Path

try:
    import openpyxl
except ImportError:  # pragma: no cover
    sys.exit("openpyxl is required:  pip3 install --user openpyxl")

HERE = Path(__file__).resolve().parent
DATA = HERE.parent / "data"
RAW = DATA / "raw"
OUT = DATA / "by_year"

FIELDS = [
    "Name",
    "Campus",
    "Department",
    "Title",
    "Salary",
    "State Funds",
    "Other Funds",
    "Year",
]

# Header text drifts across the 13 years -- "Salary Paid from State or Tuition
# Funds" (2014-15) became "Salary from State Aided Funds" (recent) -- and the
# header cells carry embedded newlines from the published layout. Match on a
# normalized substring rather than on any exact string. "annual salary" (rather
# than the fuller "budgeted annual salary") is what 2015-16 reduces to once its
# split header is rejoined.
HEADER_PATTERNS = [
    ("Name", r"^employee$"),
    ("Title", r"^position$"),
    ("Campus", r"^campus$"),
    ("Department", r"^department$"),
    ("Salary", r"annual\s*salary"),
    ("State Funds", r"state"),
    ("Other Funds", r"other"),
]


def norm_header(cell):
    return re.sub(r"\s+", " ", str(cell or "")).strip().lower()


def find_header(rows):
    """Locate the header row and map our field names onto column indexes.

    The banner above the table is 3 rows in recent files and 4 in 2014-15, so
    the row is found by content rather than at a fixed offset.

    2015-16 splits its money headers over two rows -- the row above holds
    "Annual" / "State or Tuition" / "Other" and the "Employee" row holds only
    "Salary" / "Funds" / "Funds", which is ambiguous on its own. So each column
    is matched against its own cell OR that cell prefixed by the one above it.
    Both forms are needed: the money columns only resolve when joined, while in
    2016-17 the cell above "Employee" is the title "FY 2016-17", which would
    break the joined form.
    """
    for i, row in enumerate(rows[:40]):
        if norm_header(row[0]) != "employee":
            continue
        above = rows[i - 1] if i else [None] * len(row)
        headers = [
            (
                norm_header(c),
                norm_header(
                    f"{above[j] if j < len(above) and above[j] else ''} {c or ''}"
                ),
            )
            for j, c in enumerate(row)
        ]
        cols = {}
        for field, pattern in HEADER_PATTERNS:
            for j, forms in enumerate(headers):
                if j in cols.values():
                    continue
                if any(h and re.search(pattern, h) for h in forms):
                    cols[field] = j
                    break
        missing = [f for f, _ in HEADER_PATTERNS if f not in cols]
        if missing:
            raise ValueError(f"header row {i} is missing {missing}: {headers}")
        return i, cols
    raise ValueError("no row starting with 'Employee' in the first 40 rows")


def money(value):
    """Salaries are numeric cells; keep them as plain integers."""
    if value is None or value == "":
        return ""
    if isinstance(value, (int, float)):
        return str(int(round(value)))
    digits = re.sub(r"[^0-9.-]", "", str(value))
    return str(int(round(float(digits)))) if digits else ""


def parse_workbook(path, year):
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    try:
        ws = wb[wb.sheetnames[0]]
        rows = list(ws.iter_rows(values_only=True))
    finally:
        wb.close()

    start, cols = find_header(rows)
    out = []
    skipped = 0

    for row in rows[start + 1 :]:
        name = str(row[cols["Name"]] or "").strip()
        campus = str(row[cols["Campus"]] or "").strip()
        # Banner lines and trailing blanks have a value in column A but no
        # campus; a real appointment always names a campus.
        if not name or not campus:
            if name:
                skipped += 1
            continue

        out.append(
            {
                "Name": re.sub(r"\s+", " ", name),
                "Campus": campus,
                # Old files pad Department out to a fixed width with spaces.
                "Department": re.sub(
                    r"\s+", " ", str(row[cols["Department"]] or "")
                ).strip(),
                "Title": re.sub(r"\s+", " ", str(row[cols["Title"]] or "")).strip(),
                "Salary": money(row[cols["Salary"]]),
                "State Funds": money(row[cols["State Funds"]]),
                "Other Funds": money(row[cols["Other Funds"]]),
                "Year": year,
            }
        )

    return out, skipped


def check(rows):
    """Report the things that would quietly corrupt a downstream join."""
    people = {r["Name"] for r in rows}
    no_salary = sum(1 for r in rows if not r["Salary"])

    # The two fund columns should reconstruct the total. A row where they do not
    # is either a published inconsistency or a parsing error, and either way it
    # should not pass silently.
    mismatched = []
    for r in rows:
        if not r["Salary"]:
            continue
        parts = sum(int(r[k]) for k in ("State Funds", "Other Funds") if r[k])
        if parts and parts != int(r["Salary"]):
            mismatched.append((r["Name"], r["Salary"], parts))

    print(
        f"  {len(rows):>6,} appointments  {len(people):>6,} people  "
        f"(+{len(rows) - len(people):,} extra appointments)"
    )
    if no_salary:
        print(f"         {no_salary} row(s) with no salary")
    if mismatched:
        print(f"         {len(mismatched)} row(s) where state+other != total, e.g.:")
        for name, total, parts in mismatched[:3]:
            print(f"           {name}: total {total} vs parts {parts}")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--year", help="parse a single school year, e.g. 2025-2026")
    args = ap.parse_args()

    if not RAW.exists():
        sys.exit("No data/raw -- run scripts/fetch_sources.py first.")

    OUT.mkdir(parents=True, exist_ok=True)
    years = sorted(d.name for d in RAW.iterdir() if d.is_dir())
    if args.year:
        if args.year not in years:
            sys.exit(f"No downloaded sources for {args.year}.")
        years = [args.year]

    total = 0
    for year in years:
        books = sorted(RAW.joinpath(year).glob("*.xlsx"))
        if not books:
            # Expected for 2010-11 through 2013-14: the spreadsheet series does
            # not start until 2014-15, so those years are PDF-only.
            print(f"{year}  (no spreadsheet published)")
            continue

        rows = []
        for book in books:
            parsed, skipped = parse_workbook(book, year)
            rows.extend(parsed)
            if skipped:
                print(f"  {book.name}: skipped {skipped} banner/blank row(s)")

        print(f"{year}  {books[0].name}")
        check(rows)

        dest = OUT / f"appointments_{year}.csv"
        with open(dest, "w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=FIELDS)
            w.writeheader()
            w.writerows(rows)
        total += len(rows)

    print(f"\n{total:,} appointment rows -> {OUT}")


if __name__ == "__main__":
    main()
