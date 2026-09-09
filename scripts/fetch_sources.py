#!/usr/bin/env python3
"""Download every University of Nebraska salary source document.

Two publications cover the same payroll, and this project uses each to check the
other:

  * Personnel Roster (PDF)     -- one row per person, with position number, job
                                  class, term, FTE and the cost object that pays
                                  them. Published for 2010-11 onward.
  * Budgeted Employees (XLSX)  -- one row per *appointment*, with the split
                                  between state-aided and other funds. Published
                                  for 2014-15 onward.

Neither carries an employee ID, and the roster's "position" number belongs to
the seat rather than the person (1,002 of them changed occupant between 2024-25
and 2025-26), so names are the only identity anchor this project has. The
spreadsheet is the clean name authority: PDF text extraction bleeds job titles
into the name column and truncates long names, and reconcile.py repairs those
against the spreadsheet before anything is joined across years.

Run this before parse_roster_pdf.py or parse_xlsx.py:

    python3 scripts/fetch_sources.py            # fetch everything missing
    python3 scripts/fetch_sources.py --year 2025-2026
    python3 scripts/fetch_sources.py --verify   # re-hash what is on disk

Downloads land in data/raw/<year>/ (gitignored) and every file is recorded in
data/source_manifest.json with its URL, size and SHA-256, so a later re-fetch
can be proven byte-identical to the one that produced the committed CSVs.
"""

import argparse
import collections
import hashlib
import json
import re
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
DATA = HERE.parent / "data"
RAW = DATA / "raw"
MANIFEST = DATA / "source_manifest.json"

# Descriptive agent so the university can see who is pulling these and why.
UA = (
    "diepjustin-github-io-salary-search/1.0 "
    "(personal, non-commercial student-journalism project; "
    "contact: sdiepxj367@gmail.com)"
)
REQUEST_DELAY = 1.0

# The three pages that link the documents. The current year lives on the first
# two; everything older is on the archive.
INDEX_PAGES = [
    "https://nebraska.edu/offices/business-finance/budget-and-planning/",
    "https://nebraska.edu/offices/business-finance/budget-and-planning/budgeted-employees-as-of-july-1",
    "https://nebraska.edu/offices/business-finance/budget-and-planning/archive",
]

# 2010-11 is the oldest roster the university keeps online. Discovery is checked
# against this so a silent site reorganization shows up as a failure rather than
# as a quietly shorter dataset.
FIRST_YEAR = "2010-2011"
MIN_EXPECTED_YEARS = 17


def get(url, timeout=120):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def school_year(url):
    """Pull the school year out of a document URL.

    Filenames are wildly inconsistent across 17 years -- "2025-2026",
    "2021-22", "personnel-roster-2014-15.pdf", "2016NUSalariesJuly1FY201516",
    "Budgeted Employees as of July 1 2026" -- so try each shape in turn. The
    July-1 form names the *start* year of the fiscal year, i.e. the file dated
    July 1 2026 is the 2026-2027 book.
    """
    text = urllib.parse.unquote(url)

    m = re.search(r"(20\d\d)\s*[-_]\s*(20\d\d)", text)
    if m:
        return f"{m.group(1)}-{m.group(2)}"

    m = re.search(r"(20\d\d)\s*[-_]\s*(\d\d)(?!\d)", text)
    if m:
        return f"{m.group(1)}-20{m.group(2)}"

    m = re.search(r"July\s*1,?\s*(20\d\d)", text)
    if m:
        return f"{m.group(1)}-{int(m.group(1)) + 1}"

    # "2016NUSalariesJuly1FY201516" -- the FY suffix is the *end* of the year.
    m = re.search(r"FY(20\d\d)(\d\d)", text)
    if m:
        return f"{m.group(1)}-20{m.group(2)}"

    return None


def classify(url):
    """roster | xlsx | None -- what kind of document a URL points at.

    Deliberately narrow. These pages also link General Operating Budgets and
    per-department "Budget by Department" volumes, which are budget documents
    with no personnel rows in them; both must be left alone.
    """
    text = urllib.parse.unquote(url)
    if re.search(r"personnel.{0,3}roster", text, re.I):
        return "roster"
    if text.lower().endswith(".xlsx") and re.search(
        r"salar|budgeted\s+employees", text, re.I
    ):
        return "xlsx"
    return None


def discover():
    """Crawl the three index pages and group document URLs by school year."""
    found = set()
    for page in INDEX_PAGES:
        print(f"  reading {page}")
        html = get(page).decode("utf-8", "replace")
        for m in re.finditer(r'href=["\']([^"\']+\.(?:pdf|xlsx))["\']', html, re.I):
            href = m.group(1).replace("&amp;", "&")
            found.add(urllib.parse.urljoin(page, href))
        time.sleep(REQUEST_DELAY)

    years = collections.defaultdict(lambda: {"rosters": [], "xlsx": []})
    unclaimed = []
    for url in sorted(found):
        kind = classify(url)
        if not kind:
            continue
        year = school_year(url)
        if not year:
            unclaimed.append(url)
            continue
        years[year]["rosters" if kind == "roster" else "xlsx"].append(url)

    if unclaimed:
        # A salary document whose year cannot be read is a silent data gap, so
        # say so rather than dropping it.
        print(f"  WARNING: {len(unclaimed)} salary document(s) with no readable year:")
        for url in unclaimed:
            print(f"    {url}")

    return dict(sorted(years.items())), unclaimed


def local_name(url):
    return urllib.parse.unquote(url).rsplit("/", 1)[-1].replace(" ", "_")


def fetch_year(year, docs, entries, force=False):
    out = RAW / year
    out.mkdir(parents=True, exist_ok=True)

    for kind in ("rosters", "xlsx"):
        for url in docs[kind]:
            dest = out / local_name(url)
            if dest.exists() and not force:
                print(f"    have {dest.name} ({dest.stat().st_size:,} B)")
            else:
                print(f"    GET  {dest.name}", end="", flush=True)
                try:
                    blob = get(url)
                except Exception as exc:  # noqa: BLE001 - report and keep going
                    print(f"  FAILED: {exc}")
                    entries.append(
                        {"year": year, "kind": kind, "url": url, "error": str(exc)}
                    )
                    continue
                dest.write_bytes(blob)
                print(f"  {len(blob):,} B")
                time.sleep(REQUEST_DELAY)

            entries.append(
                {
                    "year": year,
                    "kind": "roster" if kind == "rosters" else "xlsx",
                    "url": url,
                    "file": str(dest.relative_to(DATA)),
                    "bytes": dest.stat().st_size,
                    "sha256": sha256(dest),
                }
            )


def verify(manifest):
    """Re-hash everything on disk against the manifest."""
    ok = missing = changed = 0
    for entry in manifest.get("files", []):
        if "sha256" not in entry:
            continue
        path = DATA / entry["file"]
        if not path.exists():
            print(f"  MISSING  {entry['file']}")
            missing += 1
        elif sha256(path) != entry["sha256"]:
            print(f"  CHANGED  {entry['file']}")
            changed += 1
        else:
            ok += 1
    print(f"\n  {ok} verified, {missing} missing, {changed} changed")
    return missing == 0 and changed == 0


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--year", help="fetch a single school year, e.g. 2025-2026")
    ap.add_argument("--force", action="store_true", help="re-download existing files")
    ap.add_argument(
        "--verify", action="store_true", help="re-hash local files against the manifest"
    )
    args = ap.parse_args()

    if args.verify:
        if not MANIFEST.exists():
            sys.exit("No manifest yet -- run without --verify first.")
        sys.exit(0 if verify(json.loads(MANIFEST.read_text())) else 1)

    print("Discovering source documents...")
    years, unclaimed = discover()

    print(f"\nFound {len(years)} school years:")
    for year, docs in years.items():
        print(f"  {year}  rosters={len(docs['rosters'])}  xlsx={len(docs['xlsx'])}")

    if FIRST_YEAR not in years or len(years) < MIN_EXPECTED_YEARS:
        sys.exit(
            f"\nExpected at least {MIN_EXPECTED_YEARS} years starting {FIRST_YEAR}, "
            f"got {len(years)}. The university may have reorganized these pages; "
            f"check INDEX_PAGES before trusting a shorter run."
        )

    if args.year and args.year not in years:
        sys.exit(f"No documents found for {args.year}.")
    targets = {args.year: years[args.year]} if args.year else years

    entries = []
    print()
    for year, docs in targets.items():
        print(f"  {year}")
        fetch_year(year, docs, entries, force=args.force)

    MANIFEST.write_text(
        json.dumps(
            {
                "generated": time.strftime("%Y-%m-%d"),
                "source_pages": INDEX_PAGES,
                "unclaimed": unclaimed,
                "files": entries,
            },
            indent=1,
        )
        + "\n"
    )

    good = [e for e in entries if "sha256" in e]
    failed = [e for e in entries if "error" in e]
    total = sum(e["bytes"] for e in good)
    print(f"\n{len(good)} files, {total / 1e6:.0f} MB -> {RAW}")
    print(f"manifest -> {MANIFEST.relative_to(DATA.parent)}")
    if failed:
        print(f"\n{len(failed)} download(s) FAILED:")
        for e in failed:
            print(f"  {e['year']} {e['url']}\n    {e['error']}")
        sys.exit(1)


if __name__ == "__main__":
    main()
