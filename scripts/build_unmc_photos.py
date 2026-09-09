#!/usr/bin/env python3
"""Add UNMC faculty and staff headshots to the photo manifest.

UNMC is the largest campus in the salary data -- 4,564 people -- and had three
photos. Its department sites render person listings from a JSON blob embedded
in the page (`allItemsList`), which gives first name, middle initial, last name
and an image URL as separate fields. Separate fields matter: matching on a
display string is how a photo ends up on the wrong salary.

Run after build_leadership_photos.py; this adds to that manifest and never
overwrites an entry it already made.

    python3 scripts/build_unmc_photos.py

WHAT HAS TO AGREE BEFORE A PHOTO IS ATTACHED

A name alone is not enough, so three independent things are required:

  1. Surname matches exactly and the first name matches or is a prefix
     ("J Bruce" for "Joseph Bavitz"), with exactly ONE payroll person fitting.
  2. A word from the web page's job title also appears in that person's payroll
     title, department or unit. 1,149 of 1,157 matches clear this.
  3. The image filename does not name somebody else. UNMC names these files
     after their subject -- aizenberg.jpg, garvin_labcoat.jpg -- so a filename
     carrying an unrelated surname is real evidence. This is what caught
     "krause1.jpg" being served on Monica Johnson's entry.

And two refusals, both costing coverage on purpose:

  * If two payroll people share a name, neither gets a photo. Nothing in the
    name says which of them is in the picture. That is the Hoiberg rule.
  * A shortened or changed surname is never bridged. UNMC lists Michele
    Aizenberg; payroll says "Aizenberg Ansari, Michele R". That is probably the
    same person, and "probably" is not good enough, so she gets initials.
"""

import argparse
import csv
import difflib
import json
import re
import sys
import time
import urllib.request
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MANIFEST = ROOT / "data" / "leadership_photos.json"

UA = (
    "diepjustin.github.io-salary-search/1.0 "
    "(personal, non-commercial student-journalism project; "
    "contact: sdiepxj367@gmail.com)"
)
REQUEST_DELAY = 1.0

# unmc.edu/robots.txt is "Disallow:" (nothing disallowed) and advertises this
# sitemap, so this walks the site the way the site asks to be walked.
SITEMAP_INDEX = "https://www.unmc.edu/xml-sitemap-index.xml"
LISTING_RE = re.compile(
    r"/(faculty|staff|people|our-team|our-faculty|faculty-staff|directory|team)"
    r"(/index\.html|/)?$",
    re.I,
)
ITEMS_RE = re.compile(r"allItemsList\s*=\s*(\[.*?\]);", re.S)

# Words that turn up in UNMC image filenames but name nobody: departments,
# credentials, and photo-production descriptors.
FILENAME_NOISE = set(
    """faculty portrait photo image staff headshot headshots profile default placeholder
    picture pictures web site small large medium thumb crop cropped edit edited edits
    update updated final copy horiz vert labcoat background whitecoat coat suit smiling
    square round color dermatology emergencymed emergencymedicine internalmed
    internalmedicine hematology hematologyoncology hemonc oncology rheumatology
    pulmonology pulmonary cardiology radiology psychiatry psychiatryx neurology
    neurosurgery pathology pediatrics surgery nursing pharmacy genetics anesthesia obgyn
    biochemistry physiology pharmacology eppley heartvascular infectiousdiseases
    transplant unmc college department dept division center centre clinic hospital
    medicine medical mbbs mbchb phd rnbsn dnp aprn""".split()
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
    return re.sub(r"[^a-z]", "", (text or "").lower())


def listing_urls():
    """Every UNMC page that looks like a person listing, from the sitemaps."""
    subs = re.findall(r"<loc>([^<]+)</loc>", get(SITEMAP_INDEX))
    print(f"  {len(subs)} sub-sitemaps")
    urls = set()
    for sub in subs:
        try:
            urls.update(re.findall(r"<loc>([^<]+)</loc>", get(sub)))
        except Exception as exc:  # noqa: BLE001 - a missing section is not fatal
            print(f"    skip {sub}: {exc}")
        time.sleep(0.5)
    listings = sorted(u for u in urls if LISTING_RE.search(u))
    print(f"  {len(urls):,} pages, {len(listings)} person listings")
    return listings


def people_on(html, page):
    """Pull the embedded person records out of one listing page."""
    out = []
    for match in ITEMS_RE.finditer(html):
        # The page emits trailing commas, which json.loads will not accept.
        blob = re.sub(r",(\s*[\]}])", r"\1", match.group(1))
        try:
            items = json.loads(blob)
        except ValueError:
            continue
        for item in items:
            image = (item.get("image") or "").strip()
            first = (item.get("firstName") or "").strip()
            last = (item.get("lastName") or "").strip()
            if not (image and first and last):
                continue
            titles = item.get("title")
            out.append(
                {
                    "first": first,
                    "last": last,
                    "middle": (item.get("middleInitial") or "").strip(),
                    "image": image,
                    "titles": (
                        " ".join(titles) if isinstance(titles, list) else str(titles or "")
                    ),
                    "page": page,
                }
            )
    return out


def names_someone_else(payroll_name, image_url):
    """True when the image filename names a person who is not this one.

    UNMC names these files after their subject, so an unexplained surname in the
    filename is evidence the wrong image is attached.
    """
    stem = image_url.rsplit("/", 1)[-1].rsplit(".", 1)[0].lower()
    surname, _, rest = payroll_name.partition(",")
    mine = [p for p in [norm(surname)] + [norm(x) for x in rest.split() if len(x) > 1] if p]
    informative = [t for t in re.findall(r"[a-z]{4,}", stem) if t not in FILENAME_NOISE]
    if not informative:
        return False
    for token in informative:
        for part in mine:
            # Tolerant of the misspellings these filenames are full of --
            # "harman" for Hartman, "thomesen" for Thomsen, "dreesen" for
            # Dreessen -- and of nicknames sitting beside a real name.
            if token in part or part in token:
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
                "middle": norm(tokens[1])[:1] if len(tokens) > 1 else "",
                "haystack": set(re.findall(r"[a-z]{4,}", blob)),
            }
        )
    return index


def main():
    argparse.ArgumentParser(description=__doc__).parse_args()

    everyone = list(
        csv.DictReader(newest_salary_csv().open(newline="", encoding="utf-8-sig"))
    )
    rows = [r for r in everyone if r["Campus"] == "UNMC"]

    # Shared names are counted across EVERY campus, not just UNMC. The page
    # looks photos up by name with no campus attached, so a name that is unique
    # within UNMC but shared with someone at UNL still resolves to two people.
    # "Gumenyuk, Valentina" is a UNMC assistant professor on $144,418 and a UNL
    # dining services team leader on $49,870; a UNMC-only check let the
    # professor's face through onto the dining worker's row.
    shared = {n for n, c in Counter(r["Name"].strip() for r in everyone).items() if c > 1}
    index = build_index(rows)
    print(f"UNMC payroll rows: {len(rows):,} ({len(shared)} names shared system-wide)")

    print("Reading UNMC sitemaps...")
    listings = listing_urls()

    print("Fetching listings...")
    scraped = []
    for url in listings:
        try:
            scraped.extend(people_on(get(url), url))
        except Exception as exc:  # noqa: BLE001 - one bad page is not fatal
            print(f"    skip {url}: {exc}")
        time.sleep(REQUEST_DELAY)
    print(f"  {len(scraped):,} person records on the web")

    matched, taken, stats = {}, set(), Counter()
    for person in scraped:
        last, first = norm(person["last"]), norm(person["first"])
        if not last or not first:
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
            stats["name shared by two people"] += 1
            continue
        if name in taken:
            stats["already matched from another page"] += 1
            continue

        middle = norm(person["middle"])[:1]
        if middle and candidate["middle"] and middle != candidate["middle"]:
            stats["middle initial conflicts"] += 1
            continue
        page_words = set(re.findall(r"[a-z]{4,}", person["titles"].lower()))
        if not (page_words & candidate["haystack"]):
            stats["job title does not corroborate"] += 1
            continue
        if names_someone_else(name, person["image"]):
            stats["image filename names someone else"] += 1
            continue

        taken.add(name)
        matched[name] = {
            "name": name,
            "title": candidate["row"]["Title"],
            "campus": "UNMC",
            "salary": candidate["row"]["Salary"],
            "group": "unmc_faculty",
            "photo_url": person["image"],
            "source_url": person["page"],
        }

    manifest = json.loads(MANIFEST.read_text()) if MANIFEST.exists() else {}
    before = len(manifest)
    added = 0
    for name, entry in matched.items():
        if name not in manifest:
            manifest[name] = entry
            added += 1
    MANIFEST.write_text(json.dumps(manifest, indent=2, ensure_ascii=False))

    print(f"\nMatched {len(matched):,} UNMC people")
    for reason, count in stats.most_common():
        print(f"  {count:>5} rejected: {reason}")
    urls = Counter(e["photo_url"] for e in manifest.values())
    print(f"\nManifest {before} -> {len(manifest)} entries (+{added})")
    print(f"photos reused across different people: {sum(1 for v in urls.values() if v > 1)}")


if __name__ == "__main__":
    main()
