# salary-search

Searchable University of Nebraska salaries, published at
<https://diepjustin.github.io/salary-search/>, covering **17 school years from
2010-11 through 2026-27** — 203,400 person-year records for 34,687 people.

This file is the single source of truth for the project. Read it before
changing anything here.

## Where the data comes from

The university publishes the same payroll twice a year, in two formats that
barely overlap. Both are pulled, and each is used to check the other.

| | Personnel Roster (PDF) | Budgeted Employees (spreadsheet) |
| --- | --- | --- |
| years | 2010-11 → 2026-27 | 2014-15 → 2026-27 |
| a row is | one person, per cost object | one **appointment** |
| only here | position number, job class, term, FTE, cost objects | department, state-aided vs other funds |

Both are July 1 snapshots of the same fiscal year, linked from
[Budget & Planning](https://nebraska.edu/offices/business-finance/budget-and-planning/)
and its [archive](https://nebraska.edu/offices/business-finance/budget-and-planning/archive).

**These are budgeted salaries, not money paid.** They are what the university
planned to pay on 1 July. Mid-year hires, departures and raises are not in
them, and neither is outside income — a coach's media and apparel money is not
here. Hourly, temporary and student payroll appear as unnamed pool lines.

## Running it

Six scripts, run in order, each standalone. Needs `pdfplumber` and `openpyxl`.

```bash
python3 scripts/fetch_sources.py       # 36 documents, 265 MB, ~5 min
python3 scripts/parse_xlsx.py          # 13 spreadsheets -> 174,078 appointments
python3 scripts/parse_roster_pdf.py    # 23 PDFs, ~25,000 pages, ~30 min
python3 scripts/reconcile.py           # cross-check the two, merge
python3 scripts/build_people.py        # join across years
python3 scripts/build_site_data.py     # the files the page loads
```

Headshots are a separate chain, run in this order because the second adds to the
first one's file and never overwrites it:

```bash
python3 scripts/build_leadership_photos.py   # leadership + all athletics staff
python3 scripts/build_unmc_photos.py         # UNMC faculty, ~10 min
python3 scripts/build_campus_photos.py       # UNL, UNO, UNK and IANR faculty
python3 scripts/verify_photos.py             # drop anything that does not resolve
```

`verify_photos.py` is not optional. The page hotlinks these images, so a wrong
or rotted URL shows a broken avatar and still counts toward coverage unless
something checks. It also repairs a real UNL quirk: their Drupal sites print
each other's file paths, so an image linked on `film.unl.edu` may only be
served by `arts.unl.edu`.

`fetch_sources.py --verify` re-hashes local files against
`data/source_manifest.json`, so a later re-fetch can be proven identical to the
one that produced the committed CSVs.

## What is committed, and what is not

Committed: `data/by_year/salaries_<year>.csv` (17 files, the reconciled record
for each school year), `data/people.csv`, `data/site/`, the source manifest,
and the reconciliation summary and exceptions.

Gitignored, all regenerable: `data/raw/` (265 MB of source documents),
`data/by_year/roster_*` and `appointments_*` (each source read alone, before
reconciliation), `data/reconciliation/recon_*.csv` (158,280 comparison rows,
almost all recording agreement), and `data/salary_history.csv` (the per-year
files stacked, which the site shards already carry).

## How the PDF is read, and why it is read that way

The roster is a fixed-column report. Columns are sliced by **x-position**, not
by splitting text on whitespace, because titles wrap mid-cell and are printed
hard against the next column — `Academic Affairs05723` is a job title welded to
a position number. Whitespace splitting produces exactly the damage visible in
other PDF-derived copies of this data: `Adkins, Dennis D Plumber/Pipefitter`,
`Abdurakhmonov` with the first name gone, rows with no comma at all.

Anchors, stable across all 17 years: cost element x≈56, name x≈101, title
x≈217, position x≈362, job class x≈411, term x≈450, FTE x≈478, salary x≈525.

**A person paid from several cost objects is printed once under each**, with
their `TOTAL` reprinted at every one. Natalie Becerra appears on page 9 under
Career Fair ($23,367) and page 121 under the journalism college ($23,344), with
`TOTAL … 46,711` at both; 3,930 of 12,795 people in 2025-26 are split this way.
So a person's pay is the **sum** of their own lines — reporting one line as the
whole is how a 1.000-FTE $50,508 employee gets published as a 0.500-FTE
$25,254 one.

### The parser checks itself

Two things in the document are summed independently and compared, every year:

* every split person's lines against their printed `TOTAL`
* every cost object's lines against its printed `TOTAL WAGES AND SALARIES`

**124,706 such checks across 17 years, 43 failures (0.03%).** Every bug found
during development was found this way, not by eye — a dropped $442,469
executive vice chancellor, titles running one word long and pushing the
position number out of its column, cost-sharing headers naming two departments,
and the 2010-11 volumes printing the object number before the name instead of
after.

## The fact-check

`reconcile.py` matches roster people to spreadsheet people and compares pay.

**158,280 person-years cross-checked between the two documents. 10 disagree
(0.006%).** Eight of the ten are one person whose name the roster prints
inconsistently (`Fernandes Filho, Jose Americo M` / `Fernandes Jr, Joseph
Americo M`). All ten are marked `SALARY MISMATCH` in the output and listed in
`data/reconciliation_exceptions.csv`; none is presented as verified.

The spreadsheet is the authority on **name spelling**. Where a very long name
crowds a title word into the roster's name column, the spelling is repaired
from the spreadsheet — but only when the salary independently confirms it is
the same person. 26 names repaired across 17 years.

2010-11 through 2013-14 have no spreadsheet and are marked `single-source`.

## Identity: the part most likely to publish something false

**There is no employee ID in either source.** The only anchor is the name.

**A position number is a seat, not a person.** 1,002 position IDs changed
occupant between 2024-25 and 2025-26 — position 00014 went from Kelly Dick to
Lori Robison. Joining a salary history on position would hand about a thousand
people someone else's career. Never join on it. (The page's photo lookup was
keyed by position and is now keyed by name for this reason.)

**Names are not unique.** In 2025-26 `Johnson, Jennifer L` is an assistant
director at UNO *and* an instructional technology specialist in the President's
office; `Kelly, Brian M` is a UNL professor *and* a UNMC systems analyst. UNMC
has two Kelly A Johnsons and two Yan Zhangs — same name, same campus. The
parser separates these using the roster's own `TOTAL` blocks, which state who
is who; other published versions of this dataset collapse them into one row,
losing one of the two people entirely.

So `build_people.py` links across years only on an **exact name that is unique
in every year it appears**. A campus change is allowed and recorded (Jeffrey
Gold really did move from UNMC chancellor to system president in 2024-25). Two
things are refused:

* a name shared by more than one person in any year — 654 records left unlinked
* a **changed surname**

### The known bias, stated plainly

Refusing to link changed surnames is not a neutral default. People who change
their surname — most often women, at marriage — appear as two shorter careers
rather than one long one. That is a real limitation of this dataset.

It is refused anyway because the alternative is worse: a wrong link fabricates
a numeric narrative ("her pay fell from $199k to $33k"), is invisible without
opening a 1,957-page PDF, and propagates to every year. An unlinked person is
*visibly* missing history; a mislinked one looks perfect.

The honest fix is a reviewed list of confirmed name changes that a person signs
off on — a human judgement, not a heuristic. **This has not been built.**

## Comparing salaries across years

Things that make a year-over-year comparison wrong, and what the page does:

* **FTE changes.** Half-time to full-time is not a 100% raise. The history
  table shows FTE and prints `FTE changed` instead of a figure for that step.
* **Term.** Codes 12 and 02 dominate; 02 is believed to be a nine-month
  academic-year appointment, but **no legend for these codes was found**, so
  the page does not interpret them.
* **First year is not a hire date.** It is the first year the name was matched.
  The page says so under every history.
* **Nothing is inflation-adjusted.** All figures are nominal.
* **Reorganizations.** Campus labels change (UNCA → UNOP), departments get
  renamed (`Pathology/Microbiology` → `Pathology, Microbiology & Immunolog`).
  Neither is a move. UNL, IANR and NCTA are treated as one campus, because a
  UNL/IANR split is one employee.

## Filtering athletics out

The page has an Athletics control: include (default), exclude, or only. With
athletics excluded the top of the list stops being a $6.4M football coach and
becomes the university president at $1.06M.

Athletics is decided by **cost object** — the `77xx` block, on every campus
(UNL `23-7701`, UNO `43-7701/7705/7710`, UNK `53-7701/7702/7711/7712/7713`).
That is 427 people in 2026-27.

The two obvious tests are both wrong:

* **Job title.** The university employs an Early Childhood Coach, an Academic
  Success Coach and a Trailblazer Program Job Coach. 37 people have "coach" in
  their title and nothing to do with sport.
* **Department name.** Only UNL files them under a department called
  "Athletics". UNO and UNK use sport names — Football, Hockey, Volleyball,
  Training Room — which no keyword list catches reliably.

A person's *primary* cost object decides it, not any athletics money they
touch: the general counsel is paid partly from an athletics cost object and is
not an athletics employee.

## Headshots

`data/leadership_photos.json` maps a name to a photo hosted by the university,
hotlinked rather than copied. 2,670 of 12,588 people (21%) have one — NCTA 78%,
UNMC 25%, UNL 23%, UNL-IANR 20%, UNO 14%, UNK 13%. Every URL in it has been
fetched and confirmed to return an actual image.

**Keyed by name, never by position number.** A position is a seat: 1,002
changed occupant in a single year, so a position-keyed manifest starts serving
the previous occupant's face the moment the data rolls over.

Sources: each university's own leadership pages, the three athletics staff
directories (huskers.com, lopers.com, omahamavs.com), UNMC's department faculty
listings (which embed a person record carrying first name, middle initial,
surname and image URL as separate fields), and the department directories of
UNL, UNO, UNK and IANR.

None of those four publishes a usable sitemap or a central list of
departments, so the directory URLs were found by crawling and are pinned in
`build_campus_photos.py`. UNL and IANR departments live on their own subdomains
with no shared path convention; UNO is one host whose paths vary just as much,
and its employee directory sits under `/search/`, which its robots.txt
disallows, so that page is never fetched. UNK's `sitemap.xml` is a CMS test
page, and its A-to-Z department index is rendered by JavaScript, so its
departments were found through its four college pages instead.

Which path is chosen matters more than it looks. `agronomy.unl.edu/people/`
lists 5 people and `agronomy.unl.edu/faculty/` lists 67, for a department of
116 — taking the first URL that answered would have captured a fourteenth of
it and reported nothing wrong.

Both publish a display name rather than separate name fields, so matching there
is narrower on purpose: a multi-word surname cannot be matched at all, because
payroll's "Zuniga Ulloa, Jorge M" and a page saying "Jorge Zuniga" are not
safely the same person.

### What has to agree before a photo is attached

Three independent things, because a name alone is not enough:

1. Surname exact, first name matching or a prefix ("J Bruce" for "Joseph
   Bavitz"), and exactly one payroll person fitting.
2. A word from the web page's job title also appearing in that person's payroll
   title, department or unit. 1,149 of 1,157 UNMC matches cleared this.
3. The image filename not naming somebody else. UNMC names these files after
   their subject, so an unexplained surname is evidence — this is what caught
   `krause1.jpg` being served on Monica Johnson's entry.

And a photo is refused outright when **two employees share a name**, because
nothing in the name says which of them is in the picture. That check has to run
across all campuses, not one: `Gumenyuk, Valentina` is a UNMC assistant
professor on $144,418 *and* a UNL dining services team leader on $49,870, and a
UNMC-only check let the professor's face through onto the dining worker's row
before this was caught.

A shortened or changed surname is never bridged either. UNMC lists Michele
Aizenberg; payroll says `Aizenberg Ansari, Michele R`. Probably the same
person; "probably" gets initials.

### Sources that look usable and are not

UNL's directory at `directory.unl.edu` has a clean JSON API
(`?q=<name>&format=json`) with an `imageURL` for every employee. **Every one of
those URLs redirects to `default-avatar-100.jpeg`.** Taking the field at face
value would put the same silhouette on 4,000 people and call them headshots.
UNL photos here come from department and leadership pages instead — and those
pages serve the same silhouette for staff without a portrait, so
`build_campus_photos.py` rejects placeholder filenames by rule rather than
trusting a heuristic to notice.

Matching a directory's own subdomain against a person's payroll department is
the other one. It sounds like free corroboration and is not: it agreed for only
413 of 716 UNL matches, because college sites host many departments
(`business.unl.edu` lists Management, Finance and Marketing). As a gate it
would have discarded correct matches while proving nothing.

## Relationship to Matt Waite's version

Matt Waite publishes 2024-25 and 2025-26 at
<https://github.com/mattwaite/mattwaite.github.io/tree/master/salary-search>.
`salaries_<year>.csv` uses his twelve columns, in his order, as a prefix, so
the two are interoperable; this project's additions come after them.

It diverges in taking names from the spreadsheet rather than the PDF, in
keeping same-name people apart rather than collapsing them, and in summing a
person's cost-object lines rather than reporting one of them.

## Still open

* Salary histories are not linked across surname changes (above).
* 10 person-years where the two sources disagree on pay are flagged, not
  resolved.
* 43 of 124,706 internal checks fail and have not been individually traced.
* A few names per year are long enough to crowd a title word into the name
  column, in years with no spreadsheet to repair them from.
* `personnel_data.csv` in this folder is the old single-year file the page used
  before this. It is superseded by `data/by_year/salaries_2025-2026.csv` and is
  no longer read by anything.
* Headshot coverage is 21% and uneven, and the ceiling is far below 100%: a
  large share of the headcount is in units that publish no portraits at all —
  199 in Custodial Services, 110 in Building Systems Maintenance, 54 in
  University Police, and 95 at USMARC, which is a federal USDA facility rather
  than a university site.
* The directory lists in `build_campus_photos.py` were found by crawling and
  are certainly incomplete; no campus publishes an index to check them against.
  A department whose site uses a path not in that list is simply missed.
* IANR's School of Natural Resources (78 people) runs a different CMS on
  `.aspx` pages with no photo markup this can read, so it is not scraped rather
  than badly scraped.
* The UNMC pass reads only listing pages whose URL ends in `/faculty/`,
  `/staff/` and similar. A trial that also walked nested listings found ~200
  more people; the tighter rule was kept because it is easier to reason about.
