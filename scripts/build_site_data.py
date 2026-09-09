#!/usr/bin/env python3
"""Build the small files the web page actually loads.

The full dataset is 203,400 person-year records and will not fit down a phone
connection, so the page gets two things instead:

  * current.csv -- the newest year only, which is what the search table shows.
  * history/<letter>.json -- one shard per surname initial, fetched only when a
    reader expands somebody. Nobody should download 34,687 salary histories to
    look up one professor.

Salaries are written as plain integers and years as an index into years.json:
the same 17 year strings repeated 203,400 times would otherwise be most of the
payload.

    python3 scripts/build_site_data.py
"""

import argparse
import csv
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
DATA = HERE.parent / "data"
SITE = DATA / "site"

CURRENT_FIELDS = [
    "Person ID",
    "Name",
    "Campus",
    "Department",
    "Title",
    "FTE",
    "Salary",
    "Years",
    "Athletics",
]

# Athletics is the 77xx cost-object block on every campus -- UNL 23-7701, UNO
# 43-7701/7705/7710, UNK 53-7701/7702/7711/7712/7713. This is the only reliable
# test. Department name fails: only UNL calls it "Athletics", while UNO and UNK
# file staff under sport names (Football, Hockey, Volleyball, Training Room).
# Job title fails worse -- the university employs an Early Childhood Coach, an
# Academic Success Coach and a Trailblazer Program Job Coach, none of whom have
# anything to do with sport.
ATHLETICS_RE = re.compile(r"^\d\d-77\d\d")


def to_int(text):
    digits = re.sub(r"[^0-9-]", "", text or "")
    return int(digits) if digits else 0


def shard_key(name):
    """Surname initial, or '_' for anything that does not start with a letter."""
    return name[0].lower() if name[:1].isalpha() else "_"


def main():
    argparse.ArgumentParser(description=__doc__).parse_args()

    history_file = DATA / "salary_history.csv"
    if not history_file.exists():
        sys.exit("No data/salary_history.csv -- run scripts/build_people.py first.")

    with open(history_file, encoding="utf-8") as fh:
        history = list(csv.DictReader(fh))

    years = sorted({r["Year"] for r in history})
    index = {year: i for i, year in enumerate(years)}
    latest = years[-1]

    by_person = defaultdict(list)
    for row in history:
        by_person[row["Person ID"]].append(row)

    shards = defaultdict(dict)
    current = []

    for pid, rows in by_person.items():
        rows.sort(key=lambda r: r["Year"])

        # Most people keep the same title, campus, department and FTE for years
        # at a stretch, and repeating those for all 203,400 records is most of
        # the payload. Each entry is [year, salary], with the rest appended only
        # in the years they actually change; the page carries the last value
        # forward. That also means a change is visible as data, which is what
        # lets the page avoid presenting a jump from half-time to full-time as
        # though it were a raise.
        entries, previous = [], None
        for row in rows:
            fields = (
                row["Title"],
                row["Campus"],
                row["Department"] or row["Unit"],
                row["FTE"],
            )
            entry = [index[row["Year"]], to_int(row["Salary"])]
            if fields != previous:
                entry.extend(fields)
                previous = fields
            entries.append(entry)
        shards[shard_key(rows[-1]["Name"])][pid] = entries

        # The table lists people as they appear in the newest year. Someone who
        # has left still has a history in the shards, but they are not staff
        # now and listing them as current would be wrong.
        newest = rows[-1]
        if newest["Year"] == latest:
            current.append(
                {
                    "Person ID": pid,
                    "Name": newest["Name"],
                    "Campus": newest["Campus"],
                    "Department": newest["Department"] or newest["Unit"],
                    "Title": newest["Title"],
                    "FTE": newest["FTE"],
                    "Salary": newest["Salary"],
                    "Years": len(rows),
                    # Their main appointment, not any athletics money they
                    # happen to touch: the university's general counsel is paid
                    # partly from an athletics cost object, and is not an
                    # athletics employee.
                    "Athletics": (
                        "y"
                        if ATHLETICS_RE.match(newest.get("Cost Center") or "")
                        else ""
                    ),
                }
            )

    SITE.mkdir(parents=True, exist_ok=True)
    (SITE / "history").mkdir(exist_ok=True)

    (SITE / "years.json").write_text(json.dumps(years, separators=(",", ":")))
    for letter, people in shards.items():
        (SITE / "history" / f"{letter}.json").write_text(
            json.dumps(people, separators=(",", ":"))
        )

    current.sort(key=lambda r: r["Name"])
    with open(SITE / "current.csv", "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=CURRENT_FIELDS)
        writer.writeheader()
        writer.writerows(current)

    shard_files = list((SITE / "history").glob("*.json"))
    total = sum(p.stat().st_size for p in shard_files)
    biggest = max(shard_files, key=lambda p: p.stat().st_size)
    size = (SITE / "current.csv").stat().st_size / 1e6
    print(f"{len(years)} years, newest {latest}")
    print(f"current.csv  {len(current):,} people  {size:.1f} MB")
    print(f"history/     {len(shard_files)} shards  {total / 1e6:.1f} MB total")
    print(f"             largest {biggest.name} {biggest.stat().st_size / 1e3:.0f} KB")


if __name__ == "__main__":
    main()
