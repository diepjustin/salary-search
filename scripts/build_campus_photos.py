#!/usr/bin/env python3
"""Add UNL and UNO faculty headshots to the photo manifest.

Run after build_leadership_photos.py; this adds to that manifest and never
overwrites an entry it already made. UNMC is handled separately by
build_unmc_photos.py, which reads a different page structure.

    python3 scripts/build_campus_photos.py

WHERE THE DIRECTORY LISTS COME FROM

Neither site publishes a sitemap or a central index of departments, so the URL
lists below were found by crawling and are pinned here rather than rediscovered
on every run:

  UNL  departments live on their own subdomains (math.unl.edu, physics.unl.edu)
       with no shared path convention -- /directory/, /people/, /faculty/ and
       /about/directory/ are all in use. Candidate subdomains were generated
       from the department names in the salary data itself.
  UNO  is one host whose paths vary just as much (/about-us/directory/,
       /about-us/faculty-and-staff.php, /about/faculty-staff/), so its
       directories were found by following links whose text mentions faculty or
       staff, rather than by guessing paths.

Re-run that search when a campus reorganizes its sites, and update the lists.

UNO's robots.txt disallows /search/, which is where its employee directory
lives, so that page is never fetched. Department pages sit outside /search/.

WHAT HAS TO AGREE BEFORE A PHOTO IS ATTACHED

Both sites publish a display name ("Arthur C. Allen"), not the separate name
fields UNMC gives, so matching is deliberately narrower here:

  1. Surname and first name both match one -- and only one -- payroll person.
     A multi-word surname therefore fails: payroll's "Zuniga Ulloa, Jorge M"
     will not match a page saying "Jorge Zuniga". That is a real coverage cost,
     and it is the right side to err on.
  2. The job title or unit printed beside the person corroborates their payroll
     title or department. Both sites publish these -- UNL as schema.org
     microdata (jobTitle, organization-unit), UNO as text in the card.
  3. The image filename does not name somebody else.

And no photo at all when two employees share a name, checked across every
campus rather than within one. "Gumenyuk, Valentina" is a UNMC assistant
professor on $144,418 and a UNL dining services team leader on $49,870; a
single-campus check let the professor's face onto the dining worker's row.

A SIGNAL THAT WAS TESTED AND REJECTED

Matching a directory's own subdomain against the person's payroll department
sounds like free corroboration and is not: it agreed for only 413 of 716 UNL
matches, because college sites host many departments (business.unl.edu lists
Management, Finance and Marketing). Using it as a gate would have discarded
correct matches while proving nothing.
"""

import argparse
import csv
import difflib
import json
import re
import sys
import time
import os
import unicodedata
import urllib.parse
import urllib.request
from collections import Counter, defaultdict
from pathlib import Path
from urllib.parse import urljoin

try:
    from bs4 import BeautifulSoup
except ImportError:  # pragma: no cover
    sys.exit("pip3 install --user bs4")

ROOT = Path(__file__).resolve().parent.parent
MANIFEST = ROOT / "data" / "leadership_photos.json"

UA = (
    "diepjustin.github.io-salary-search/1.0 "
    "(personal, non-commercial student-journalism project; "
    "contact: sdiepxj367@gmail.com)"
)
REQUEST_DELAY = 1.0

UNL_DIRECTORIES = [
    "https://agronomy.unl.edu/people/",
    "https://business.unl.edu/directory/",
    "https://cas.unl.edu/directory/",
    "https://civil.unl.edu/faculty-staff/",
    "https://classics.unl.edu/directory/",
    "https://comm.unl.edu/directory/",
    "https://computing.unl.edu/directory/",
    "https://const.unl.edu/about/directory/",
    "https://eas.unl.edu/directory/",
    "https://engineering.unl.edu/about/directory/",
    "https://engl.unl.edu/faculty/",
    "https://entomology.unl.edu/people/",
    "https://film.unl.edu/faculty-staff/",
    "https://history.unl.edu/directory/",
    "https://hort.unl.edu/directory/",
    "https://horticulture.unl.edu/people/",
    "https://libraries.unl.edu/directory/",
    "https://math.unl.edu/about/directory/",
    "https://mme.unl.edu/directory/",
    "https://modlang.unl.edu/directory/",
    "https://philosophy.unl.edu/directory/",
    "https://physics.unl.edu/directory/",
    "https://psychology.unl.edu/directory/",
    "https://raikes.unl.edu/faculty/",
    "https://statistics.unl.edu/faculty/",
    "https://wgs.unl.edu/directory/",
]

UNO = "https://www.unomaha.edu/"
UNO_DIRECTORIES = [
    UNO + "college-of-arts-and-sciences/biology/about-us/directory/index.php",
    UNO + "college-of-arts-and-sciences/geography-geology/about-us/directory/index.php",
    UNO + "college-of-arts-and-sciences/history/about-us/directory/index.php",
    UNO + "college-of-arts-and-sciences/master-of-arts-in-critical-and-creative-thinking"
        "/about-us/faculty-directory/index.php",
    UNO + "college-of-arts-and-sciences/mathematics/about-us/directory/index.php",
    UNO + "college-of-arts-and-sciences/neuroscience/about-us/directory/index.php",
    UNO + "college-of-arts-and-sciences/ollas/about-us/directory/index.php",
    UNO + "college-of-arts-and-sciences/philosophy/about-us/directory/index.php",
    UNO + "college-of-arts-and-sciences/physics/about-us/directory/index.php",
    UNO + "college-of-arts-and-sciences/political-science/about-us/directory/index.php",
    UNO + "college-of-arts-and-sciences/religion/about-us/directory/index.php",
    UNO + "college-of-arts-and-sciences/sociology-and-anthropology/about-us/directory/index.php",
    UNO + "college-of-arts-and-sciences/world-languages-and-literature/about-us/directory/index.php",
    UNO + "college-of-education-health-and-human-sciences/teacher-education/about-us/directory/index.php",
    UNO + "college-of-public-affairs-and-community-service/criminology-and-criminal-justice"
        "/about-us/faculty-and-staff.php",
    UNO + "college-of-public-affairs-and-community-service/gerontology/about-us/faculty-and-staff.php",
    UNO + "college-of-public-affairs-and-community-service/social-work"
        "/about-us/faculty-staff-adjunct-directory.php",
    UNO + "criss-library/about-us/staff-directory/index.php",
    UNO + "general-education/directory/index.php",
    UNO + "office-of-graduate-studies/staff-directory/index.php",
]

UNK = "https://www.unk.edu/academics/"
UNK_DIRECTORIES = [
    UNK + "accounting-finance/faculty-staff/index.php",
    UNK + "art/faculty/index.php",
    UNK + "biology/faculty/index.php",
    UNK + "communications/faculty/index.php",
    UNK + "math/faculty-staff/index.php",
    UNK + "physics/faculty-staff/index.php",
    UNK + "social-work/faculty_staff/index.php",
    UNK + "sociology/faculty-staff/index.php",
    UNK + "theatre/faculty-staff/index.php",
]

# IANR's departments are UNL subdomains running the same Drupal theme, so they
# are read with scrape_unl. The exact paths matter: agronomy.unl.edu/people/
# lists 5 people and agronomy.unl.edu/faculty/ lists 67, so accepting the first
# URL that answered would have quietly captured a fourteenth of the department.
IANR_DIRECTORIES = [
    "https://agecon.unl.edu/faculty/",
    "https://agronomy.unl.edu/faculty/",
    "https://agronomy.unl.edu/our-people/",
    "https://animalscience.unl.edu/faculty/",
    "https://biochem.unl.edu/faculty/",
    "https://bse.unl.edu/about/faculty-staff/",
    "https://entomology.unl.edu/people/",
    "https://foodscience.unl.edu/people/faculty/",
    "https://ncta.unl.edu/directory/",
    "https://plantpathology.unl.edu/faculty/",
    "https://scal.unl.edu/staff/",
    "https://vbms.unl.edu/faculty/",
]

# A generic silhouette stands in for anyone without a portrait. Serving one as
# a headshot is the failure that directory.unl.edu invites -- every employee
# there has an imageURL and every one redirects to default-avatar-100.jpeg -- so
# these are rejected by name rather than left to the filename heuristic below,
# which only caught them by accident.
PLACEHOLDER_RE = re.compile(
    r"default[-_]?avatar|placeholder|no[-_]?photo|silhouette|pending[-_]?person", re.I
)

# UNK renders its placeholder card without substituting the name, so the alt
# text arrives as the literal template "${display}". Anything still carrying
# template syntax is not a person.
TEMPLATE_ALT_RE = re.compile(r"\$\{|\{\{")

# Words that appear in these filenames but name nobody.
FILENAME_NOISE = set(
    """faculty portrait photo image images staff headshot headshots profile default
    placeholder picture screenshot screen shot web site small large medium thumb crop
    cropped edit edited square resize resized final copy horiz vert directory person
    people node styles public files sites unomaha unledu university nebraska college
    department dept school center itok jpeg png webp avatar business engineering
    libraries agronomy entomology horticulture""".split()
)

CREDENTIALS = re.compile(
    r",?\s*\b(Ph\.?D\.?|M\.?D\.?|J\.?D\.?|Ed\.?D\.?|D\.?N\.?P\.?|M\.?A\.?|M\.?S\.?|"
    r"M\.?B\.?A\.?|MFA|MPH|RN|APRN|PE|CPA|LCSW|PsyD|DVM)\b\.?",
    re.I,
)


def get(url, timeout=30):
    request = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read().decode("utf-8", "replace")


def newest_salary_csv():
    files = sorted((ROOT / "data" / "by_year").glob("salaries_*.csv"))
    if not files:
        sys.exit("No data/by_year/salaries_*.csv -- run reconcile.py first.")
    return files[-1]


def norm(text):
    """Lowercase ASCII letters only, so 'Aguero' and 'Agüero' compare equal."""
    text = unicodedata.normalize("NFKD", text or "")
    return re.sub(r"[^a-z]", "", text.encode("ascii", "ignore").decode().lower())


def split_display_name(display):
    """'Arthur C. Allen' -> ('arthur', 'allen'). None when unusable."""
    cleaned = CREDENTIALS.sub("", display).strip(" ,")
    # UNK prints "Dr. Jacob Cooper"; the honorific is not part of the name.
    cleaned = re.sub(r"^(Dr|Prof|Professor|Mr|Ms|Mrs|Mx)\.?\s+", "", cleaned, flags=re.I)
    parts = [p for p in cleaned.split() if p.strip(".")]
    if len(parts) < 2:
        return None, None
    return norm(parts[0]), norm(parts[-1])


def photo_src(img, page):
    src = img.get("src")
    if not src:
        srcset = img.get("srcset") or ""
        src = srcset.split(",")[0].strip().split(" ")[0] if srcset else None
    if not src:
        return None
    url = urljoin(page, src)
    # Some of these paths contain literal spaces -- UNO files history portraits
    # under "headshots/faculty headshots/". A browser encodes that silently;
    # urllib raises InvalidURL and the photo is lost for no good reason.
    parts = urllib.parse.urlsplit(url)
    return urllib.parse.urlunsplit(
        parts._replace(path=urllib.parse.quote(parts.path, safe="/%~"))
    )


def card_text(img, levels, minimum):
    """Text of the smallest ancestor that carries the person's details."""
    node = img
    for _ in range(levels):
        node = node.parent
        if node is None:
            return ""
        text = node.get_text(" ", strip=True)
        if len(text) >= minimum:
            return text
    return ""


def scrape_unl(html, page):
    """UNL's Drupal cards: alt='Avatar for <name>' plus schema.org microdata."""
    soup = BeautifulSoup(html, "html.parser")
    out = []
    for img in soup.find_all("img", alt=re.compile(r"^Avatar for ")):
        url = photo_src(img, page)
        if not url:
            continue
        out.append(
            {
                "display": img["alt"][len("Avatar for ") :].strip(),
                "photo": url,
                "context": card_text(img, 6, 40),
                "page": page,
            }
        )
    return out


def scrape_uno(html, page):
    """UNO's cards: a headshot image whose alt is the person's name."""
    soup = BeautifulSoup(html, "html.parser")
    out = []
    for img in soup.find_all("img", src=re.compile(r"headshots/", re.I)):
        alt = (img.get("alt") or "").strip()
        url = photo_src(img, page)
        if alt and url:
            out.append(
                {
                    "display": alt,
                    "photo": url,
                    "context": card_text(img, 5, 40),
                    "page": page,
                }
            )
    return out


def scrape_unk(html, page):
    """UNK's cards: <img alt="Dr. Jane Doe" class="bioImg" src=".../headshots/doeja.jpg">."""
    soup = BeautifulSoup(html, "html.parser")
    out = []
    for img in soup.find_all("img", class_="bioImg"):
        alt = (img.get("alt") or "").strip()
        url = photo_src(img, page)
        if not alt or not url or TEMPLATE_ALT_RE.search(alt):
            continue
        out.append(
            {
                "display": alt,
                "photo": url,
                "context": card_text(img, 5, 40),
                "page": page,
            }
        )
    return out


def names_someone_else(payroll_name, image_url, display_name=""):
    """True when the image filename names a person who is not this one.

    The display name is taken into account as well as the payroll name, because
    these pages print nicknames the payroll does not hold -- "Jia (Joya) Yu",
    "Yunxia (Peter) Zhu" -- and their photos are filed under the nickname. That
    is not somebody else, it is the same person under the name they use.
    """
    stem = urllib.parse.unquote(
        image_url.rsplit("/", 1)[-1].split("?")[0].rsplit(".", 1)[0]
    ).lower()
    surname, _, rest = payroll_name.partition(",")
    mine = [norm(surname)] + [norm(x) for x in rest.split() if len(x) > 1]
    mine += [norm(x) for x in re.findall(r"[A-Za-z]{2,}", display_name)]
    mine = [p for p in mine if p]

    informative = [t for t in re.findall(r"[a-z]{4,}", stem) if t not in FILENAME_NOISE]
    if not informative:
        return False
    for token in informative:
        for part in mine:
            if token in part or part in token:
                return False
            # A shared opening -- "mirzoa" for Mirzokhidjon -- is how these
            # files are abbreviated, so compare prefixes, not whole strings.
            common = len(os.path.commonprefix([token, part]))
            if common >= 4:
                return False
            if difflib.SequenceMatcher(None, token, part).ratio() >= 0.78:
                return False
    return True


def build_index(rows):
    index = defaultdict(list)
    for row in rows:
        surname, _, rest = row["Name"].strip().partition(",")
        tokens = rest.split()
        blob = f"{row['Title']} {row.get('Department', '')} {row.get('Unit', '')}".lower()
        index[norm(surname)].append(
            {
                "row": row,
                "first": norm(tokens[0]) if tokens else "",
                "haystack": set(re.findall(r"[a-z]{4,}", blob)),
            }
        )
    return index


def collect(directories, scraper, label):
    people = []
    for url in directories:
        try:
            people.extend(scraper(get(url), url))
        except Exception as exc:  # noqa: BLE001 - one bad page is not fatal
            print(f"    skip {url}: {exc}")
        time.sleep(REQUEST_DELAY)
    print(f"  {label}: {len(people):,} person records from {len(directories)} pages")
    return people


def match(people, index, shared, label, group):
    matched, taken, stats = {}, set(), Counter()
    for person in people:
        first, last = split_display_name(person["display"])
        if not first or not last:
            stats["name could not be parsed"] += 1
            continue
        candidates = [
            c
            for c in index.get(last, [])
            if c["first"] == first
            or c["first"].startswith(first)
            or first.startswith(c["first"])
        ]
        if len(candidates) != 1:
            stats["no match, or more than one payroll person fits"] += 1
            continue

        candidate = candidates[0]
        name = candidate["row"]["Name"].strip()
        if name in shared:
            stats["name shared by two employees"] += 1
            continue
        if name in taken:
            stats["already matched from another page"] += 1
            continue
        context = set(re.findall(r"[a-z]{4,}", person["context"].lower()))
        if not (context & candidate["haystack"]):
            stats["job title does not corroborate"] += 1
            continue
        if PLACEHOLDER_RE.search(person["photo"]):
            stats["generic placeholder image, not a photo of anyone"] += 1
            continue
        if names_someone_else(name, person["photo"], person["display"]):
            stats["image filename names someone else"] += 1
            continue

        taken.add(name)
        matched[name] = {
            "name": name,
            "title": candidate["row"]["Title"],
            "campus": candidate["row"]["Campus"],
            "salary": candidate["row"]["Salary"],
            "group": group,
            "photo_url": person["photo"],
            "source_url": person["page"],
        }

    print(f"\n{label}: matched {len(matched):,}")
    for reason, count in stats.most_common():
        print(f"  {count:>5} rejected: {reason}")
    return matched


def main():
    argparse.ArgumentParser(description=__doc__).parse_args()

    everyone = list(
        csv.DictReader(newest_salary_csv().open(newline="", encoding="utf-8-sig"))
    )
    shared = {n for n, c in Counter(r["Name"].strip() for r in everyone).items() if c > 1}
    print(f"payroll rows: {len(everyone):,} ({len(shared)} names shared system-wide)")

    unl_rows = [r for r in everyone if r["Campus"] in ("UNL", "UNL-IANR", "NCTA")]
    uno_rows = [r for r in everyone if r["Campus"] == "UNO"]
    unk_rows = [r for r in everyone if r["Campus"] == "UNK"]

    print("\nFetching directories...")
    unl_people = collect(UNL_DIRECTORIES, scrape_unl, "UNL")
    uno_people = collect(UNO_DIRECTORIES, scrape_uno, "UNO")
    unk_people = collect(UNK_DIRECTORIES, scrape_unk, "UNK")
    ianr_people = collect(IANR_DIRECTORIES, scrape_unl, "IANR")

    matched = {}
    matched.update(match(unl_people, build_index(unl_rows), shared, "UNL", "unl_faculty"))
    matched.update(match(uno_people, build_index(uno_rows), shared, "UNO", "uno_faculty"))
    matched.update(match(unk_people, build_index(unk_rows), shared, "UNK", "unk_faculty"))
    # IANR people are indexed against the whole Lincoln payroll: the campus
    # label in the data splits UNL and UNL-IANR, but a department site does not.
    matched.update(match(ianr_people, build_index(unl_rows), shared, "IANR", "ianr_faculty"))

    manifest = json.loads(MANIFEST.read_text()) if MANIFEST.exists() else {}
    before = len(manifest)
    for name, entry in matched.items():
        manifest.setdefault(name, entry)
    MANIFEST.write_text(json.dumps(manifest, indent=2, ensure_ascii=False))

    urls = Counter(e["photo_url"] for e in manifest.values())
    print(f"\nManifest {before} -> {len(manifest)} entries (+{len(manifest) - before})")
    print(f"photos reused across different people: {sum(1 for v in urls.values() if v > 1)}")


if __name__ == "__main__":
    main()
