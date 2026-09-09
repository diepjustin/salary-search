#!/usr/bin/env python3
"""Join the per-year files into one salary history per person.

This is the step where a mistake is least visible and most damaging. A wrong
photograph is obvious to anyone who knows the person; a wrong salary history is
a plausible-looking number in a table no reader can check without opening a
1,957-page PDF. It also compounds -- one bad link invents a whole career, and
with it a raise, a demotion or a start date that never happened.

There is no employee ID in either source, so the only identity anchor is the
name, and the name is not always enough. Two rules follow, and both cost
coverage on purpose:

  * A name held by more than one person in any year is never joined across
    years. In 2025-26 "Johnson, Jennifer L" is an assistant director at UNO AND
    an instructional technology specialist in the President's office; "Kelly,
    Brian M" is a UNL professor AND a UNMC systems analyst. Linking on a shared
    name hands one person the other's career. Those records are kept, each
    standing on its own year, with no history attached.
  * A changed surname is never joined either. The roster and the spreadsheet
    disagree about Riley Habrock/Kimbrough within a single year, which is what a
    name change looks like in this data, and no field confirms it.

The second rule has a cost worth stating plainly, because it does not fall on
everyone equally: people who change their surname -- most often women, at
marriage -- appear as two shorter careers rather than one long one. That is a
real limitation of this dataset, not a neutral default. Fixing it properly needs
a reviewed list of confirmed name changes, which is a human judgement and
belongs in a file someone signs their name to, not in a heuristic here.

What IS joined: an exact name, unique in every year it appears. A campus change
is allowed -- people do transfer between UNL, UNO, UNK and UNMC -- but is
recorded, because a campus change is also what an undetected namesake looks
like.

    python3 scripts/build_people.py
"""

import argparse
import csv
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
DATA = HERE.parent / "data"
BY_YEAR = DATA / "by_year"

HISTORY_FIELDS = [
    "Person ID",
    "Name",
    "Year",
    "Campus",
    "Department",
    "Unit",
    "Title",
    "Salary",
    "FTE",
    "Term",
    "Position",
    "Link",
]

PEOPLE_FIELDS = [
    "Person ID",
    "Name",
    "Campus",
    "Department",
    "Title",
    "Years",
    "First Year",
    "Last Year",
    "First Salary",
    "Last Salary",
    "Change",
    "Link",
]


def to_int(text):
    digits = re.sub(r"[^0-9-]", "", text or "")
    return int(digits) if digits else 0


def campus_group(campus):
    """Lincoln's three payroll books are one employer; see parse_roster_pdf."""
    return "UNL" if campus in ("UNL", "UNL-IANR", "NCTA") else campus


def load_years():
    years = {}
    for path in sorted(BY_YEAR.glob("salaries_*.csv")):
        with open(path, encoding="utf-8") as fh:
            years[path.stem.replace("salaries_", "")] = list(csv.DictReader(fh))
    return years


def record(pid, name, year, row, link):
    return {
        "Person ID": pid,
        "Name": name,
        "Year": year,
        "Campus": row["Campus"],
        "Department": row.get("Department", ""),
        "Unit": row.get("Unit", ""),
        "Title": row.get("Title", ""),
        "Salary": row.get("Salary", ""),
        "FTE": row.get("FTE", ""),
        "Term": row.get("Term", ""),
        "Position": row.get("Position", ""),
        "Link": link,
    }


def summary(pid, name, entries, link):
    first_year, first_row = entries[0]
    last_year, last_row = entries[-1]
    first = to_int(first_row.get("Salary"))
    last = to_int(last_row.get("Salary"))
    return {
        "Person ID": pid,
        "Name": name,
        "Campus": last_row["Campus"],
        "Department": last_row.get("Department", ""),
        "Title": last_row.get("Title", ""),
        "Years": len(entries),
        "First Year": first_year,
        "Last Year": last_year,
        "First Salary": first_row.get("Salary", ""),
        "Last Salary": last_row.get("Salary", ""),
        # Left blank for a single year: there is nothing to compare against, and
        # a 0 would read as "no raise" rather than "not applicable".
        "Change": f"{last - first:,}" if len(entries) > 1 else "",
        "Link": link,
    }


def write(path, fields, rows):
    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.parse_args()

    years = load_years()
    if not years:
        sys.exit("No data/by_year/salaries_*.csv -- run scripts/reconcile.py first.")

    # A name is unusable as an identity if it belonged to more than one person
    # in ANY single year. Ambiguity in one year poisons the name for all of
    # them: there is no way to tell which of the two people the other years'
    # records belong to.
    ambiguous = set()
    for rows in years.values():
        counts = Counter(r["Name"].strip() for r in rows)
        ambiguous.update(name for name, n in counts.items() if n > 1)

    by_name = defaultdict(list)
    for year, rows in years.items():
        for row in rows:
            by_name[row["Name"].strip()].append((year, row))

    history, people = [], []
    linked = solo = refused = 0

    for name, entries in sorted(by_name.items()):
        entries.sort(key=lambda e: e[0])

        if name in ambiguous:
            # Shared name: every record stands alone, with no history.
            for year, row in entries:
                pid = "x{:09d}".format(
                    abs(hash((name, year, row["Campus"], row["Position"]))) % 10**9
                )
                history.append(record(pid, name, year, row, "not linked"))
                people.append(summary(pid, name, [(year, row)], "not linked"))
                refused += 1
            continue

        campuses = {campus_group(row["Campus"]) for _, row in entries}
        link = "same campus" if len(campuses) == 1 else "campus changed"
        pid = "p{:09d}".format(abs(hash(name)) % 10**9)

        for year, row in entries:
            history.append(record(pid, name, year, row, link))
        people.append(summary(pid, name, entries, link))
        if len(entries) > 1:
            linked += 1
        else:
            solo += 1

    history.sort(key=lambda r: (r["Name"], r["Year"]))
    people.sort(key=lambda r: r["Name"])

    write(DATA / "salary_history.csv", HISTORY_FIELDS, history)
    write(DATA / "people.csv", PEOPLE_FIELDS, people)

    spans = Counter(int(p["Years"]) for p in people)
    tallest = max(spans.values())
    print(f"{len(history):,} person-year records across {len(years)} years")
    print(f"{len(people):,} people")
    print(f"  {linked:,} with a multi-year history")
    print(f"  {solo:,} appearing in one year only")
    print(f"  {refused:,} records left unlinked because the name is shared")
    print(
        f"  {sum(1 for p in people if p['Link'] == 'campus changed'):,} changed campus"
    )
    print("\nyears of history per person:")
    for span in sorted(spans):
        print(f"  {span:>2}: {spans[span]:>6,} {'#' * (spans[span] * 56 // tallest)}")


if __name__ == "__main__":
    main()
