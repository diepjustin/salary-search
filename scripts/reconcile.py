#!/usr/bin/env python3
"""Check the roster PDF against the spreadsheet and merge them into one file.

The university publishes the same payroll twice, in formats that do not overlap:

    roster PDF    position number, job class, term, FTE, cost objects
    spreadsheet   department, and the split between state-aided and other funds

Only the person's name and salary appear in both, which is exactly what makes
them worth comparing: two independently typeset documents agreeing on a figure
is real corroboration. For 2025-26 they agree on the pay of all 12,771 people
who could be matched one-to-one, with no disagreements at all.

The comparison is not decoration. It is what makes the year-to-year join
possible, for two reasons:

  * The spreadsheet is the authority on how a name is spelled, and a name that
    does not match cannot be joined. Where the roster's columns overflow -- a
    very long name crowding a title word into the name cell -- the spelling is
    repaired from the spreadsheet, but only when the salary independently
    confirms it is the same person.
  * Where the two sources disagree, the person is marked rather than published
    as fact.

What this deliberately does NOT do is reconcile a changed surname. In 2025-26
the roster prints "Kimbrough, Riley A" where the spreadsheet prints "Habrock,
Riley A" -- same job, same salary, same department. It is almost certainly one
person who changed their name between the two snapshots, and "almost certainly"
is still a judgement call, so it is recorded as a discrepancy for a human to
decide. Guessing at names is how a salary gets attached to the wrong person.

2010-11 through 2013-14 have no spreadsheet -- the series does not begin until
2014-15 -- so those four years are single-sourced and are marked as such rather
than being presented at the same confidence as the rest.

    python3 scripts/reconcile.py
    python3 scripts/reconcile.py --year 2025-2026
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
RECON = DATA / "reconciliation"

# Matt Waite's twelve columns, in his order, so his per-year files and these
# stay interoperable; this project's additions all come after them.
FIELDS = [
    "Name",
    "Campus",
    "Unit",
    "Cost Center",
    "Title",
    "Position",
    "Job Class",
    "Term",
    "FTE",
    "Salary",
    "Cost Elements",
    "Year",
    "Department",
    "State Funds",
    "Other Funds",
    "Appointments",
    "All Units",
    "All Cost Centers",
    "Name Shared",
    "Name Source",
    "Recon",
]

RECON_FIELDS = [
    "Roster Name",
    "Spreadsheet Name",
    "Campus",
    "Roster Salary",
    "Spreadsheet Salary",
    "Delta",
    "Status",
]


def to_int(text):
    digits = re.sub(r"[^0-9-]", "", text or "")
    return int(digits) if digits else 0


def money(value):
    return f"{value:,}" if value else ""


def load(path):
    if not path.exists():
        return []
    with open(path, encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def fold_appointments(rows):
    """Collapse the spreadsheet's appointment rows into one record per person.

    A person can hold several appointments -- a chair, a professorship and an
    endowed chair are three rows -- and their pay is the sum. Departments and
    titles are kept largest-appointment-first, so the primary one leads.
    """
    by_name = defaultdict(list)
    for row in rows:
        by_name[row["Name"].strip()].append(row)

    people = {}
    for name, group in by_name.items():
        group.sort(key=lambda r: -to_int(r["Salary"]))
        people[name] = {
            "campus": group[0]["Campus"].strip(),
            "departments": list(
                dict.fromkeys(r["Department"] for r in group if r["Department"])
            ),
            "salary": sum(to_int(r["Salary"]) for r in group),
            "state": sum(to_int(r["State Funds"]) for r in group),
            "other": sum(to_int(r["Other Funds"]) for r in group),
        }
    return people


def repair_name(roster_name, salary, unmatched, by_salary):
    """Find the spreadsheet spelling of a name the roster printed imperfectly.

    A very long name fills the whole name column and pushes a word of the job
    title into it: "Medici-Thiemann, Catherine M Resources", whose real title
    begins "Resources...". The spreadsheet spelling is a whole-word prefix of
    what the roster printed.

    Two independent things must line up before a repair is accepted: the prefix,
    AND the salary matching to the dollar. A prefix alone could rename someone
    into a different person with a similar name; the salary is the corroboration.
    """
    for candidate in by_salary.get(salary, ()):
        if candidate not in unmatched:
            continue
        tail = roster_name[len(candidate) : len(candidate) + 1]
        if roster_name.startswith(candidate) and tail == " ":
            return candidate
    return None


def reconcile_year(year):
    roster = load(BY_YEAR / f"roster_{year}.csv")
    appointments = load(BY_YEAR / f"appointments_{year}.csv")
    if not roster:
        print(f"{year}  (no roster CSV -- run parse_roster_pdf.py)")
        return None

    people = fold_appointments(appointments)
    by_salary = defaultdict(list)
    for name, person in people.items():
        by_salary[person["salary"]].append(name)

    # A name held by more than one person cannot be matched on the name alone.
    roster_counts = Counter(r["Name"].strip() for r in roster)
    unmatched = set(people)
    merged, audit = [], []
    stats = Counter()

    for row in roster:
        roster_name = row["Name"].strip()
        salary = to_int(row["Salary"])
        source, status, match = "roster", "single-source", None

        if people:
            if roster_name in people and roster_counts[roster_name] == 1:
                match = roster_name
            elif roster_counts[roster_name] == 1:
                match = repair_name(roster_name, salary, unmatched, by_salary)

            if match:
                person = people[match]
                unmatched.discard(match)
                source = "spreadsheet"
                if person["salary"] == salary:
                    status = "match" if match == roster_name else "name repaired"
                else:
                    status = "SALARY MISMATCH"
            else:
                status = (
                    "shared name" if roster_counts[roster_name] > 1 else "roster only"
                )

            audit.append(
                {
                    "Roster Name": roster_name,
                    "Spreadsheet Name": match or "",
                    "Campus": row["Campus"],
                    "Roster Salary": salary,
                    "Spreadsheet Salary": people[match]["salary"] if match else "",
                    "Delta": people[match]["salary"] - salary if match else "",
                    "Status": status,
                }
            )

        stats[status] += 1
        person = people.get(match) if match else None
        merged.append(
            {
                **{k: row.get(k, "") for k in FIELDS if k in row},
                "Name": match or roster_name,
                "Department": "; ".join(person["departments"]) if person else "",
                "State Funds": money(person["state"]) if person else "",
                "Other Funds": money(person["other"]) if person else "",
                "Name Source": source,
                "Recon": status,
            }
        )

    for name in sorted(unmatched):
        stats["spreadsheet only"] += 1
        audit.append(
            {
                "Roster Name": "",
                "Spreadsheet Name": name,
                "Campus": people[name]["campus"],
                "Roster Salary": "",
                "Spreadsheet Salary": people[name]["salary"],
                "Delta": "",
                "Status": "spreadsheet only",
            }
        )

    RECON.mkdir(parents=True, exist_ok=True)
    with open(BY_YEAR / f"salaries_{year}.csv", "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=FIELDS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(merged)
    with open(RECON / f"recon_{year}.csv", "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=RECON_FIELDS)
        writer.writeheader()
        writer.writerows(audit)

    label = f"{len(people):,} in spreadsheet" if people else "PDF only"
    print(f"{year}  {len(roster):,} in roster, {label}")
    for key in (
        "match",
        "name repaired",
        "SALARY MISMATCH",
        "shared name",
        "roster only",
        "spreadsheet only",
        "single-source",
    ):
        if stats[key]:
            print(f"    {stats[key]:>6,}  {key}")
    return stats


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--year", help="reconcile a single school year")
    args = ap.parse_args()

    if not BY_YEAR.exists():
        sys.exit("No data/by_year -- run the two parsers first.")

    years = sorted(p.stem.replace("roster_", "") for p in BY_YEAR.glob("roster_*.csv"))
    if args.year:
        if args.year not in years:
            sys.exit(f"No roster CSV for {args.year}.")
        years = [args.year]

    total = Counter()
    per_year = {}
    for year in years:
        stats = reconcile_year(year)
        if stats:
            total.update(stats)
            per_year[year] = stats

    # The per-year recon files are 10 MB of mostly "these agreed". These two
    # small files carry what a reader would actually want to audit -- the tally,
    # and every row that was not a clean match -- and are the ones committed.
    with open(DATA / "reconciliation_summary.csv", "w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(["Year", "Status", "Count"])
        for year, stats in per_year.items():
            for status, count in sorted(stats.items()):
                writer.writerow([year, status, count])

    with open(DATA / "reconciliation_exceptions.csv", "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=["Year"] + RECON_FIELDS)
        writer.writeheader()
        for year in years:
            for row in load(RECON / f"recon_{year}.csv"):
                if row["Status"] not in ("match", ""):
                    writer.writerow({"Year": year, **row})

    print(f"\nacross {len(years)} year(s):")
    for key, count in total.most_common():
        print(f"  {count:>7,}  {key}")
    if total["SALARY MISMATCH"]:
        print(
            f"\n{total['SALARY MISMATCH']:,} person-year(s) where the two sources "
            f"disagree on pay -- see data/reconciliation/."
        )


if __name__ == "__main__":
    main()
