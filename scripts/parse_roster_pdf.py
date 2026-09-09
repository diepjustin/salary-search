#!/usr/bin/env python3
"""Extract the Personnel Roster PDFs into one row per person per school year.

The roster is a fixed-column report, and the only reliable way to read it is by
where a word sits on the page, not by splitting text on whitespace. Titles wrap
mid-cell and can be printed hard against the next column ("Academic
Affairs05723"), so any whitespace-based split silently welds a job title onto a
name or a name onto a number. That is exactly the defect visible in existing
PDF-derived copies of this data: "Adkins, Dennis D Plumber/Pipefitter",
"Abdurakhmonov" with the first name lost, 49 rows with no comma at all.

Column anchors, stable from 2010-11 through 2026-27:

    cost element  x~56   name  x~101   title x~217   position x~362
    job class     x~411  term  x~450   FTE   x~478   salary   x~525 (right-aligned)

Line types, told apart by their left edge:

    x~53   cost object header  -- "Career Fair 23-0127-0001"
    x~56   a person or pool line, the only kind carrying a position number
    x~103  cross-reference     -- another cost object's share of the person above
    x~110  "TOTAL <name>"      -- that person's university-wide total
    x~137  "TOTAL WAGES AND SALARIES <cost object>"
    x~201  "SUBTOTAL <element>"
    x~217  a wrapped title continuing the line above

THE CRITICAL STRUCTURE: a person paid from more than one cost object is printed
once *under each* cost object, and their TOTAL line is reprinted at every one of
those places. Natalie Becerra appears on page 9 under Career Fair ($23,367) and
again on page 121 under the journalism college ($23,344), with "TOTAL Becerra,
Natalie E ... 46,711" at both. 3,918 of 12,740 people in 2025-26 are split this
way. So:

  * a person's salary is the SUM of their own x~56 lines,
  * cross-reference lines must never be summed -- they restate a share that is
    already counted at its own cost object,
  * and the printed TOTAL is an independent check on that sum, which this
    parser runs for every split person rather than trusting either number.

Reporting an unsummed single line as the person's pay is a real failure mode,
not a hypothetical: it is how a 1.000-FTE $50,508 employee gets published as a
0.500-FTE $25,254 one.

    python3 scripts/parse_roster_pdf.py
    python3 scripts/parse_roster_pdf.py --year 2025-2026
"""

import argparse
import csv
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

try:
    import pdfplumber
except ImportError:  # pragma: no cover
    sys.exit("pdfplumber is required:  pip3 install --user pdfplumber")

HERE = Path(__file__).resolve().parent
DATA = HERE.parent / "data"
RAW = DATA / "raw"
OUT = DATA / "by_year"

# Matt Waite's twelve columns first, in his order, so his per-year files and
# these are readable by the same code; everything this parser adds comes after.
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
    "Appointments",
    "All Units",
    "All Cost Centers",
    "Total Check",
    "Name Shared",
]

# Right edge of each column. A word belongs to the first bucket it fits under.
# The name/title edge sits at 213 rather than at the 200 where the name column
# nominally ends: titles always begin at x~216, so anything printed between the
# two is a name that ran long. Cutting at 200 truncates the middle initial off
# every long name -- "Aizenberg Ansari, Michele" for "...,  Michele R", 84 people
# in 2025-26 -- and a name that does not match the spreadsheet cannot be joined
# across years.
NAME_LIMIT = 213
BUCKETS = [
    (92, "element"),
    (NAME_LIMIT, "name"),
    (352, "title"),
    (402, "position"),
    (438, "job_class"),
    (468, "term"),
    (500, "fte"),
    (10_000, "salary"),
]

CAMPUS_HEADERS = [
    (r"INSTITUTE OF AG", "UNL-IANR"),
    (r"NEBRASKA COLLEGE OF TECHNICAL AGRICULTURE", "NCTA"),
    (r"UNIVERSITY OF NEBRASKA MEDICAL CENTER", "UNMC"),
    (r"UNIVERSITY OF NEBRASKA AT OMAHA", "UNO"),
    (r"UNIVERSITY OF NEBRASKA AT KEARNEY", "UNK"),
    (r"OFFICE OF THE PRESIDENT|CENTRAL ADMINISTRATION", "NU System"),
    (r"UNIVERSITY OF NEBRASKA\s*[-–]\s*LINCOLN", "UNL"),
]

COST_OBJECT_RE = re.compile(r"\b(\d\d-\d{4}-\d{4,}(?:-\d+)?)\b")
POSITION_RE = re.compile(r"^\d{4,6}$")
PAGE_FURNITURE_RE = re.compile(
    r"^(PERSONNEL ROSTER|Fiscal Period|UNIVERSITY OF|EXCLUDING|INSTITUTE OF"
    r"|NEBRASKA COLLEGE|COST$|ELEMENT|CLASS$|JOB$|TERM)",
    re.I,
)
# The staff and cost-object indexes at the back of every volume repeat thousands
# of names with page numbers. Useful for validation, but not payroll rows.
#
# Deliberately case-sensitive and anchored to a whole line: the table of
# contents on page 1 lists these same indexes in title case ("Alphabetical
# Index of Staff ......  1884"), and a case-insensitive match there ends the
# parse before it starts.
INDEX_START_RE = re.compile(
    r"^(ALPHABETICAL LISTING OF (COST OBJECTS|STAFF)|NUMERICAL INDEX OF COST OBJECTS)\s*$",
    re.M,
)


def group_lines(page, tol=3.0):
    """Cluster words into visual rows.

    FTE and salary sit a couple of points off the baseline of the rest of their
    row, so rows are clustered by proximity rather than by an exact `top`.
    """
    words = sorted(page.extract_words(), key=lambda w: (w["top"], w["x0"]))
    rows, cur = [], []
    for w in words:
        if cur and w["top"] - cur[0]["top"] > tol:
            rows.append(cur)
            cur = []
        cur.append(w)
    if cur:
        rows.append(cur)
    return rows


def weld_point(word):
    """Where a name was printed straight into the title, if it was.

    A name that exactly fills its cell gets no gap before the title, and the two
    arrive as one word: "SasitharanAssociate" (Sasitharan Balasubramaniam,
    Associate Professor), "LSenior" (a middle initial L welded to Senior).

    Rather than guess at capital letters -- which would cut McDonald, DeSilva
    and AnneMarie in half -- use the geometry. The word's own width says which
    character sits at the column boundary, so the cut is only made where the
    page says the columns divide, and only if a capital is right there. Returns
    the index to cut at, or None to leave the word alone.
    """
    x0, x1 = word["x0"], word.get("x1", word["x0"])
    text = word["text"]
    if not (x0 < NAME_LIMIT < x1) or len(text) < 3:
        return None

    boundary = (NAME_LIMIT - x0) / (x1 - x0) * len(text)
    candidates = [i for i, ch in enumerate(text) if i and ch.isupper()]
    if not candidates:
        return None

    cut = min(candidates, key=lambda i: abs(i - boundary))
    # Within about three characters of the boundary. Further away and this is a
    # capital that belongs to the name itself.
    return cut if abs(cut - boundary) <= 3 else None


def split_columns(words):
    cells = defaultdict(list)
    for w in sorted(words, key=lambda w: w["x0"]):
        cut = weld_point(w)
        if cut:
            cells["name"].append(w["text"][:cut])
            cells["title"].append(w["text"][cut:])
            continue
        for limit, key in BUCKETS:
            if w["x0"] < limit:
                cells[key].append(w["text"])
                break
    return {k: " ".join(v).strip() for k, v in cells.items()}


def deglue(cells):
    """Repair a title that has spilled into the position column.

    Long titles break the column grid two different ways, and both drop a real
    person's salary on the floor unless they are put back:

      * The title fills its cell exactly and is printed with no gap before the
        position number, so the two arrive as a single word -- "Exec. Vice
        Chancellor, Academic Affairs05723", "Asst Dir of Graduate & Prof
        Student Exp15052". 65 lines in 2025-26, one of them a $442,469
        executive vice chancellor.
      * The title merely runs long and its last word crosses the column
        boundary, so the position cell reads "II 72040" for "Advancd Human
        Simulation Specialist II".

    In both cases the position number is the trailing digits and everything
    before it is title. The per-cost-object totals are what caught these; the
    figures would otherwise just have been quietly missing.
    """
    position = cells.get("position", "").strip()

    if position and not POSITION_RE.fullmatch(position):
        parts = position.split()
        if len(parts) > 1 and POSITION_RE.fullmatch(parts[-1]):
            cells["title"] = f"{cells.get('title', '')} {' '.join(parts[:-1])}".strip()
            cells["position"] = parts[-1]
    elif not position and cells.get("title"):
        match = re.fullmatch(r"(.*?[A-Za-z].*?)(\d{4,6})", cells["title"])
        if match:
            cells["title"] = match.group(1).strip()
            cells["position"] = match.group(2)

    return cells


def number(text):
    text = (text or "").replace(",", "").strip()
    return float(text) if re.fullmatch(r"-?\d+(?:\.\d+)?", text) else None


def money(value):
    return f"{int(round(value)):,}" if value is not None else ""


class RosterParser:
    """Walks one volume, accumulating one record per person per cost object."""

    def __init__(self, year):
        self.year = year
        self.campus = ""
        self.unit = ""
        self.rows = []
        self.totals = defaultdict(list)  # name -> printed TOTAL salaries
        self.object_totals = []  # (cost center, printed, summed)
        self._section = []  # rows since the last cost object was closed
        self._pending = []  # rows not yet closed by a person TOTAL line

    def feed_page(self, page):
        for words in group_lines(page):
            left = min(w["x0"] for w in words)
            text = " ".join(w["text"] for w in words)

            for pattern, label in CAMPUS_HEADERS:
                if re.search(pattern, text):
                    self.campus = label
                    break

            if PAGE_FURNITURE_RE.match(text.strip()):
                continue

            cells = split_columns(words)
            # A payroll line always names the cost element it is paid from, as
            # a bare six-digit code (511000 tenured salaries, 513000
            # managerial/professional, and so on). A cost object header never
            # does. This is the discriminator between the two, and it has to be
            # decided before deglue() runs -- otherwise a header ending in its
            # own object number, "Instl Effectiveness & Analytics Tech Fee
            # 22-0130-0001", gets split into a title and a "0001" position and
            # is booked as a person.
            is_payroll = bool(re.fullmatch(r"\d{6}", cells.get("element", "")))

            if text.startswith("TOTAL WAGES AND SALARIES"):
                self._close_object(text)
            elif 106 <= left <= 113 and text.startswith("TOTAL "):
                # A TOTAL line has no title, position or job class, so the name
                # runs unobstructed all the way to the FTE column. Reading it
                # with the payroll-row column edges would clip the middle
                # initial off long names and stop them matching the lines they
                # are meant to close.
                name = re.sub(
                    r"^TOTAL\s+",
                    "",
                    " ".join(w["text"] for w in words if w["x0"] < 440),
                ).strip()
                salary = number(cells.get("salary"))
                if name and salary is not None:
                    self.totals[name].append(salary)
                    self._claim(name, salary)
            elif left <= 60 and is_payroll:
                cells = deglue(cells)
                if POSITION_RE.fullmatch(cells.get("position", "")):
                    self._person(cells)
            elif 216 <= left <= 230 and self.rows and not cells.get("position"):
                # A title too long for its cell, wrapped onto the next line.
                self.rows[-1]["Title"] = f"{self.rows[-1]['Title']} {text}".strip()
            elif (
                95 <= left <= 108
                and self.rows
                and not cells.get("salary")
                and not cells.get("position")
            ):
                # A name too long for its cell, wrapped onto the next line
                # ("GRADUATE RESEARCH" / "ASST"). Distinguished from a
                # cross-reference line, which sits at the same left edge but
                # always carries an FTE and a salary.
                self.rows[-1]["Name"] = f"{self.rows[-1]['Name']} {text}".strip()
            elif left <= 60 and not is_payroll and COST_OBJECT_RE.search(text):
                self._open_object(text)

    def _open_object(self, text):
        """Read the unit name from a section header.

        Only the name is taken from here. The header is an unreliable place to
        read the cost object NUMBER from, because a header can carry two of
        them and the eras disagree about the order: 2013-14 onward prints
        "COSTSHARING 36-0115-1005-011 31-0105-3016" with the section's own
        number last, while the 2010-11 volumes lead with it. Guessing either
        way files a whole department's people under the wrong cost centre. The
        number is taken from the TOTAL line that closes the section instead,
        which states it unambiguously.
        """
        name = COST_OBJECT_RE.sub("", text)
        # Continuation banners repeat the header at the top of each page.
        self.unit = re.sub(r"\(Continued\)", "", name).strip(" -–")

    def _close_object(self, text):
        """Stamp the closed section's cost object onto every line it contained."""
        found = COST_OBJECT_RE.findall(text)
        printed = number(text.split()[-1])
        if not found or printed is None:
            return
        key = found[-1]
        summed = sum(r["Salary"] for r in self._section)
        for row in self._section:
            row["Cost Center"] = key
        self.object_totals.append((key, printed, summed))
        self._section = []

    def _claim(self, name, salary):
        """Attach a printed TOTAL to the line it closes.

        The roster prints a person's lines contiguously and ends the block with
        their TOTAL, so the nearest preceding unclosed line bearing the same
        name is the one this total belongs to. Recording that link is what lets
        two people who share a name AND a campus be told apart later: at UNMC
        there are two Kelly A Johnsons and two Yan Zhangs, and only the block
        structure says which lines belong to which.
        """
        for row in reversed(self._pending):
            if row["Name"] == name:
                row["_total"] = salary
                self._pending.remove(row)
                return

    def _person(self, cells):
        name = cells.get("name", "").strip()
        salary = number(cells.get("salary"))
        if not name or salary is None:
            return
        row = {
            "Name": name,
            "Campus": self.campus,
            "Unit": self.unit,
            # Filled in by _close_object() from the section's own TOTAL line.
            "Cost Center": "",
            "Title": cells.get("title", "").strip(),
            "Position": cells.get("position", "").strip(),
            "Job Class": cells.get("job_class", "").strip(),
            "Term": cells.get("term", "").strip(),
            "FTE": number(cells.get("fte")) or 0.0,
            "Salary": salary,
            "Cost Elements": cells.get("element", "").strip(),
            "Year": self.year,
            # Filled in by _claim() when this line's block is closed by a
            # printed TOTAL; stays None for anyone paid from one cost object.
            "_total": None,
        }
        self.rows.append(row)
        self._pending.append(row)
        self._section.append(row)


def campus_group(campus):
    """Collapse the Lincoln payroll labels into one campus.

    UNL, IANR and NCTA are separate books in the roster but one employer: a
    person split between UNL and UNL-IANR is one person, not two. UNMC, UNO,
    UNK and the President's office stay distinct, which is what keeps the
    genuine namesakes apart.
    """
    return "UNL" if campus in ("UNL", "UNL-IANR", "NCTA") else campus


def is_placeholder(name):
    """Vacant lines and pooled dollars, which are not people.

    The roster fills unnamed money with TBA and with all-caps pool labels
    (GRADRSCH, STUDENT, ADM POOL). A real name is mixed case and has a comma.
    UNF lines are named endowment funds, not employees.
    """
    name = name.strip()
    if not name or name.upper().startswith(("TBA", "UNF")):
        return True
    return "," not in name or name == name.upper()


def aggregate(rows):
    """Collapse per-cost-object lines into one row per person.

    A person's pay is the sum of their own lines. Where the roster also printed
    a TOTAL for them, that printed figure is compared against the sum and the
    outcome recorded in Total Check, so a disagreement surfaces as data rather
    than being silently resolved in favour of one side.
    """
    # Two different people can share a name, and merging them invents a salary
    # that belongs to neither. "Johnson, Jennifer L" is an assistant director at
    # UNO ($69,319) AND an instructional technology specialist in the
    # President's office ($65,000); summing them yields a $134,319 employee who
    # does not exist. Campus alone does not separate them either -- UNMC has two
    # Kelly A Johnsons and two Yan Zhangs.
    #
    # So people are grouped by the TOTAL block their lines were printed under,
    # which is the roster's own statement of who is who.
    #
    # Lines the roster never closed with a TOTAL are grouped by campus instead.
    # These are separate appointments held by one person and printed apart --
    # Shannon Bartelt-Hunt is a chairperson ($130,233), a professor ($74,419)
    # and the holder of an endowed chair ($10,000), and her pay is the sum.
    # Lincoln's three payroll labels count as one campus here: a UNL/IANR split
    # is one employee (Thyagarajan Ammachathram is UNL for $84,631 and UNL-IANR
    # for $28,209), whereas the namesakes worth keeping apart -- Brian M Kelly,
    # Erin L Smith, Chi Zhang -- are each split across genuinely different
    # campuses. Two namesakes on the same campus would still merge here; the
    # spreadsheet cross-check in reconcile.py is what catches those.
    grouped = defaultdict(list)
    for row in rows:
        if row["_total"] is not None:
            key = (row["Name"], round(row["_total"], 2))
        else:
            key = (row["Name"], "solo", campus_group(row["Campus"]))
        grouped[key].append(row)

    # A name held by more than one person this year. Nothing downstream may
    # link such a name across years on the name alone -- that is precisely how
    # one person inherits another's salary history.
    shared = {n for n, c in Counter(k[0] for k in grouped).items() if c > 1}

    out = []
    for key, lines in sorted(grouped.items(), key=lambda kv: (kv[0][0], str(kv[0][1]))):
        name = key[0]
        # The person's main appointment is the one paying them the most; its
        # title, position and job class describe them best.
        main = max(lines, key=lambda r: r["Salary"])
        salary = sum(r["Salary"] for r in lines)
        fte = sum(r["FTE"] for r in lines)

        # Check this group's summed lines against the TOTAL the roster printed
        # for that same block. The same TOTAL is reprinted under every cost
        # object paying the person, so it is one value, not a sum.
        printed = lines[0]["_total"]
        if printed is None:
            # Anyone paid from a single cost object gets no printed TOTAL, so
            # there is nothing to check them against.
            check = "single" if len(lines) == 1 else "unchecked"
        else:
            check = "ok" if abs(printed - salary) < 1.0 else "MISMATCH"

        units = sorted({r["Unit"] for r in lines if r["Unit"]})
        centers = sorted({r["Cost Center"] for r in lines if r["Cost Center"]})
        elements = sorted({r["Cost Elements"] for r in lines if r["Cost Elements"]})

        out.append(
            {
                "Name": name,
                "Campus": main["Campus"],
                "Unit": main["Unit"],
                "Cost Center": main["Cost Center"],
                "Title": main["Title"],
                "Position": main["Position"],
                "Job Class": main["Job Class"],
                "Term": main["Term"],
                "FTE": f"{fte:.3f}",
                "Salary": money(salary),
                "Cost Elements": "; ".join(elements),
                "Year": main["Year"],
                "Appointments": len(lines),
                "All Units": "; ".join(units),
                "All Cost Centers": "; ".join(centers),
                "Total Check": check,
                "Name Shared": "yes" if name in shared else "",
            }
        )
    return out


def parse_volume(path, year):
    parser = RosterParser(year)
    with pdfplumber.open(path) as pdf:
        for page in pdf.pages:
            text = page.extract_text() or ""
            if INDEX_START_RE.search(text):
                # Everything past here is the back-of-book index.
                break
            parser.feed_page(page)
            page.flush_cache()
    return parser


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

    for year in years:
        pdfs = sorted(RAW.joinpath(year).glob("*.pdf"))
        if not pdfs:
            print(f"{year}  (no roster PDF)")
            continue

        print(f"{year}", flush=True)
        lines, object_totals = [], []
        for path in pdfs:
            parser = parse_volume(path, year)
            print(f"  {path.name}: {len(parser.rows):,} lines", flush=True)
            lines.extend(parser.rows)
            object_totals.extend(parser.object_totals)

        people = [r for r in aggregate(lines) if not is_placeholder(r["Name"])]

        # Two independent checks that the geometry held: every split person's
        # summed lines against their printed TOTAL, and every cost object's
        # summed lines against its printed TOTAL WAGES AND SALARIES.
        mismatch = [r for r in people if r["Total Check"] == "MISMATCH"]
        ambiguous = [r for r in people if r["Total Check"] == "AMBIGUOUS"]
        split = [r for r in people if r["Appointments"] > 1]
        bad_objects = [t for t in object_totals if abs(t[1] - t[2]) >= 1.0]

        print(
            f"  {len(people):,} people from {len(lines):,} lines "
            f"({len(split):,} split across cost objects)"
        )
        print(
            f"  person totals: {sum(1 for r in people if r['Total Check'] == 'ok'):,} ok"
            f"  {len(mismatch)} mismatch  {len(ambiguous)} ambiguous"
        )
        print(
            f"  cost objects : {len(object_totals) - len(bad_objects):,} ok  "
            f"{len(bad_objects)} mismatch"
        )
        for row in mismatch[:3]:
            print(f"    MISMATCH {row['Name']}: summed {row['Salary']}")
        for key, printed, summed in bad_objects[:3]:
            print(f"    OBJECT {key}: printed {printed:,.0f} vs summed {summed:,.0f}")

        dest = OUT / f"roster_{year}.csv"
        with open(dest, "w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=FIELDS)
            w.writeheader()
            w.writerows(people)


if __name__ == "__main__":
    main()
