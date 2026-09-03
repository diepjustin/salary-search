#!/usr/bin/env python3
"""
Build a manifest of official headshot URLs for top administrators, athletic
directors, and head coaches in personnel_data.csv.

Scope (see conversation / README note): chancellors, vice chancellors,
president, CFO/VPs, deans/associate deans, athletic directors, and head
coaches only -- NOT the full roster and NOT department chairs (that's a
separate, larger follow-up).

This does a small, fixed number of GET requests (a couple dozen pages) against
each university's own public leadership/athletics pages -- not a bulk crawl.
It hotlinks to each university's own image URL rather than downloading and
re-hosting the photos, so this script writes only a JSON manifest of
{csv Position id -> photo_url, source_url}, never image bytes.

Usage:
    python3 scripts/build_leadership_photos.py
Writes:
    data/leadership_photos.json
"""
import csv
import json
import re
import time
import urllib.request
from html import unescape
from pathlib import Path
from urllib.parse import urljoin

try:
    from bs4 import BeautifulSoup
except ImportError:
    raise SystemExit("pip3 install --user bs4")

ROOT = Path(__file__).resolve().parent.parent
CSV_PATH = ROOT / "personnel_data.csv"
OUT_PATH = ROOT / "data" / "leadership_photos.json"

UA = (
    "diepjustin.github.io-salary-search/1.0 "
    "(personal, non-commercial student-journalism project; "
    "contact: sdiepxj367@gmail.com)"
)

REQUEST_DELAY = 1.0  # seconds between requests, be polite


def fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=15) as resp:
        html = resp.read().decode("utf-8", errors="replace")
    time.sleep(REQUEST_DELAY)
    return html


# ---------------------------------------------------------------------------
# Target list: filter personnel_data.csv down to the scoped roles
# ---------------------------------------------------------------------------

def norm_title(t):
    return re.sub(r"^[A-Z]\s+Chairperson$", "Chairperson", t.strip())


def is_head_coach(t):
    tl = t.lower()
    if "asst" in tl or "assistant" in tl or "associate" in tl:
        return False
    # require an explicit "head" qualifier -- a bare "Coach" title covers
    # non-athletic roles too (e.g. "Family-School Partnership Coach")
    return bool(re.search(r"\bhead\b.*\bcoach\b", tl))


GROUP_PATTERNS = {
    "chancellor": re.compile(
        r"^(Chancellor|Vice Chancellor.*|Assoc(iate)? Vice Chancellor.*|"
        r"Asst\.? Vice Chancellor.*|Assistant Vice Chancellor.*|Sr\.? Assoc VC.*)$",
        re.I,
    ),
    "president": re.compile(r"^President$|^Senior Vice President.*|^Vice President.*", re.I),
    "dean": re.compile(r"^(Dean|Associate Dean|Assoc Dean)$", re.I),
    "athletic_director": re.compile(r"athletic director", re.I),
}


def load_targets():
    rows = list(csv.DictReader(CSV_PATH.open(newline="", encoding="utf-8-sig")))
    targets = {}
    for r in rows:
        nt = norm_title(r["Title"])
        group = None
        for label, rx in GROUP_PATTERNS.items():
            if rx.match(nt):
                group = label
                break
        if group is None and is_head_coach(nt):
            group = "head_coach"
        if group:
            targets[r["Position"]] = {
                "name": r["Name"].strip(),
                "title": nt,
                "campus": r["Campus"],
                "salary": r["Salary"],
                "group": group,
            }
    return targets


SUFFIX_RE = re.compile(r"\b(jr|sr|ii|iii|iv)\b\.?", re.I)


def name_key(name):
    """Normalize 'Last, First Middle' -> (last, first) lowercase tokens.
    Generational suffixes (Jr/Sr/II/III/IV) are dropped from the last name --
    the CSV often includes them ("Dowell Jr, Adrian E") while a leadership
    page's plain caption usually doesn't ("Adrian Dowell")."""
    name = re.sub(r"\s+", " ", name).strip()
    if "," in name:
        last, rest = name.split(",", 1)
    else:
        parts = name.split()
        last, rest = parts[-1], " ".join(parts[:-1])
    last = SUFFIX_RE.sub("", last)
    last = re.sub(r"[^a-z]", "", last.lower())
    first_tokens = re.sub(r"[^a-z ]", "", rest.lower()).split()
    first = first_tokens[0] if first_tokens else ""
    return last, first


def match_candidate(candidate_name, targets_by_lastname):
    """Try to match a scraped 'First [Middle] Last' or 'Last, First' name
    against the CSV target list. Returns position id or None."""
    cname = unescape(candidate_name).strip()
    cname = re.sub(r"\s*,?\s*(Ph\.?D\.?|M\.?D\.?|M\.?B\.?A\.?|CFA|Ed\.?D\.?|J\.?D\.?).*$", "", cname, flags=re.I)
    parts = cname.replace(",", " ").split()
    if len(parts) < 2:
        return None
    if SUFFIX_RE.fullmatch(parts[-1]) and len(parts) > 2:
        parts = parts[:-1]
    last = re.sub(r"[^a-z]", "", parts[-1].lower())
    first = re.sub(r"[^a-z]", "", parts[0].lower())
    candidates = targets_by_lastname.get(last, [])
    for pos_id, first_tok in candidates:
        if first_tok == first or first_tok.startswith(first) or first.startswith(first_tok):
            return pos_id
    # fallback: last name unique match
    if len(candidates) == 1:
        return candidates[0][0]
    return None


# ---------------------------------------------------------------------------
# Source extractors -- each returns a list of (name, photo_url) tuples
# ---------------------------------------------------------------------------

def extract_unl_drupal_cards(html, base_url):
    soup = BeautifulSoup(html, "html.parser")
    out = []
    for img in soup.find_all("img", alt=re.compile(r"^Avatar for ")):
        name = img["alt"][len("Avatar for "):].strip()
        src = img.get("src")
        if not src:
            continue
        out.append((name, urljoin(base_url, src)))
    return out


def extract_unk_admin(html, base_url):
    soup = BeautifulSoup(html, "html.parser")
    out = []
    for h3 in soup.find_all("h3"):
        raw = h3.get_text(strip=True)
        # strip trailing credentials, e.g. "Neal Schnoor, Ph.D." -> "Neal Schnoor"
        name = raw.split(",")[0].strip()
        if not name or len(name.split()) > 4:
            continue
        # Scope strictly to this person's own card (div.bioLand) -- do NOT
        # expand the search beyond it, or a person with no headshot of their
        # own (e.g. only a candid/action photo) will silently pick up a
        # neighboring person's photo instead. No card, or no headshot inside
        # it, means no match for this person -- that's correct behavior.
        card = h3.find_parent("div", class_="bioLand")
        if card is None:
            continue
        img = card.find("img", src=re.compile(r"headshots/"))
        if img and img.get("src"):
            out.append((name, urljoin(base_url, img["src"])))
    return out


def extract_alt_name_cards(html, base_url, strip_suffixes=()):
    """Generic: img alt is (close to) the person's plain name.
    Used for UNO and NU System pages."""
    soup = BeautifulSoup(html, "html.parser")
    out = []
    for img in soup.find_all("img", alt=True):
        alt = img["alt"].strip()
        if not alt or len(alt) > 60:
            continue
        for suf in strip_suffixes:
            if alt.lower().endswith(suf):
                alt = alt[: -len(suf)].strip()
        # crude filter: looks like "First Last" or "First M Last"
        if not re.match(r"^[A-Z][a-zA-Z.\'-]+(\s+[A-Z][a-zA-Z.\'-]*){1,3}$", alt):
            continue
        src = img.get("src")
        if not src:
            continue
        out.append((alt, urljoin(base_url, src)))
    return out


def extract_unmc_bio_page(html, base_url):
    soup = BeautifulSoup(html, "html.parser")
    out = []
    best = None
    for img in soup.find_all("img", alt=True):
        alt = img["alt"].strip()
        if re.match(r"^[A-Z][a-zA-Z.\'-]+(\s+[A-Z][a-zA-Z.\'-]*){1,3}", alt):
            best = img
            break
    if best is None:
        return out
    alt = best["alt"].strip()
    name = alt.split(",")[0].strip()
    src = best.get("src")
    if src:
        out.append((name, urljoin(base_url, src)))
    return out


def extract_nuxt_payload_roster(html):
    """UNL/UNO/UNK athletics sites (huskers.com, omahamavs.com, lopers.com)
    all run the same Sidearm/Nuxt platform: the whole staff-directory roster
    is embedded as one large JSON payload array in the page."""
    arrs = [m.span() for m in re.finditer(r"\[(?:[^\[\]]|\[[^\[\]]*\])*\]", html)]
    if not arrs:
        return []
    arrs.sort(key=lambda s: s[1] - s[0], reverse=True)
    start, end = arrs[0]
    try:
        data = json.loads(html[start:end])
    except Exception:
        return []

    def resolve(v):
        return data[v] if isinstance(v, int) and 0 <= v < len(data) else v

    out = []
    for item in data:
        if isinstance(item, dict) and {"first_name", "last_name", "position"} <= item.keys():
            first = resolve(item["first_name"])
            last = resolve(item["last_name"])
            title = resolve(item.get("position"))
            if not isinstance(first, str) or not isinstance(last, str):
                continue
            photo_url = None
            mp = item.get("master_photo")
            mp = resolve(mp) if mp is not None else None
            if isinstance(mp, dict) and "url" in mp:
                photo_url = resolve(mp["url"])
            if isinstance(photo_url, str) and photo_url.startswith("http"):
                out.append((f"{first} {last}", photo_url, title if isinstance(title, str) else ""))
    return out


# ---------------------------------------------------------------------------
# Source list
# ---------------------------------------------------------------------------

DRUPAL_SOURCES = [
    "https://chancellor.unl.edu/university-leadership/executive-leadership-team/",
    "https://chancellor.unl.edu/university-leadership/college-deans/",
    "https://chancellor.unl.edu/university-leadership/division-and-unit-deans/",
]

UNK_SOURCE = "https://www.unk.edu/about/administration/index.php"

UNO_SOURCE = "https://www.unomaha.edu/office-of-the-chancellor/leadership/index.php"

NU_SOURCE = "https://nebraska.edu/meet-our-people/chancellors-and-vice-presidents"
NU_PRESIDENT_SOURCE = "https://nebraska.edu/president/"

UNMC_SOURCES = [
    "https://www.unmc.edu/aboutus/leadership-mission/chancellor.html",
    "https://www.unmc.edu/aboutus/leadership-mission/vc-academicaffairs.html",
    "https://www.unmc.edu/aboutus/leadership-mission/vc-busfin.html",
    "https://www.unmc.edu/aboutus/leadership-mission/vc-externalrelations.html",
    "https://www.unmc.edu/aboutus/leadership-mission/cahp-dean.html",
    "https://www.unmc.edu/aboutus/leadership-mission/cod-dean.html",
    "https://www.unmc.edu/aboutus/leadership-mission/com-dean.html",
    "https://www.unmc.edu/aboutus/leadership-mission/con-dean.html",
    "https://www.unmc.edu/aboutus/leadership-mission/cop-dean.html",
    "https://www.unmc.edu/aboutus/leadership-mission/coph-dean.html",
    "https://www.unmc.edu/aboutus/leadership-mission/gradstudies-dean.html",
    "https://www.unmc.edu/aboutus/leadership-mission/library-dean.html",
]

ATHLETICS_SOURCES = [
    "https://huskers.com/staff-directory",
    "https://omahamavs.com/staff-directory",
    "https://lopers.com/staff-directory",
]


def main():
    targets = load_targets()
    print(f"Loaded {len(targets)} target people from CSV")

    targets_by_lastname = {}
    for pos_id, t in targets.items():
        last, first = name_key(t["name"])
        targets_by_lastname.setdefault(last, []).append((pos_id, first))

    found = {}  # pos_id -> {photo_url, source_url, scraped_name}

    def record(pos_id, scraped_name, photo_url, source_url):
        if pos_id in found:
            return
        found[pos_id] = {
            "photo_url": photo_url,
            "source_url": source_url,
            "scraped_name": scraped_name,
        }

    # UNL leadership (Drupal cards)
    for url in DRUPAL_SOURCES:
        try:
            html = fetch(url)
        except Exception as e:
            print(f"  FAILED {url}: {e}")
            continue
        for name, photo_url in extract_unl_drupal_cards(html, url):
            pos_id = match_candidate(name, targets_by_lastname)
            if pos_id:
                record(pos_id, name, photo_url, url)
        print(f"  done {url}")

    # UNK admin cabinet
    try:
        html = fetch(UNK_SOURCE)
        for name, photo_url in extract_unk_admin(html, UNK_SOURCE):
            pos_id = match_candidate(name, targets_by_lastname)
            if pos_id:
                record(pos_id, name, photo_url, UNK_SOURCE)
        print(f"  done {UNK_SOURCE}")
    except Exception as e:
        print(f"  FAILED {UNK_SOURCE}: {e}")

    # UNO leadership
    try:
        html = fetch(UNO_SOURCE)
        for name, photo_url in extract_alt_name_cards(html, UNO_SOURCE):
            pos_id = match_candidate(name, targets_by_lastname)
            if pos_id:
                record(pos_id, name, photo_url, UNO_SOURCE)
        print(f"  done {UNO_SOURCE}")
    except Exception as e:
        print(f"  FAILED {UNO_SOURCE}: {e}")

    # NU System chancellors/VPs
    try:
        html = fetch(NU_SOURCE)
        for name, photo_url in extract_alt_name_cards(html, NU_SOURCE, strip_suffixes=(" headshot", " bio pic")):
            pos_id = match_candidate(name, targets_by_lastname)
            if pos_id:
                record(pos_id, name, photo_url, NU_SOURCE)
        print(f"  done {NU_SOURCE}")
    except Exception as e:
        print(f"  FAILED {NU_SOURCE}: {e}")

    # NU System president (not listed on the cabinet page above)
    try:
        html = fetch(NU_PRESIDENT_SOURCE)
        for name, photo_url in extract_alt_name_cards(html, NU_PRESIDENT_SOURCE, strip_suffixes=(" headshot", " bio pic")):
            pos_id = match_candidate(name, targets_by_lastname)
            if pos_id:
                record(pos_id, name, photo_url, NU_PRESIDENT_SOURCE)
        print(f"  done {NU_PRESIDENT_SOURCE}")
    except Exception as e:
        print(f"  FAILED {NU_PRESIDENT_SOURCE}: {e}")

    # UNMC per-person bio pages
    for url in UNMC_SOURCES:
        try:
            html = fetch(url)
        except Exception as e:
            print(f"  FAILED {url}: {e}")
            continue
        for name, photo_url in extract_unmc_bio_page(html, url):
            pos_id = match_candidate(name, targets_by_lastname)
            if pos_id:
                record(pos_id, name, photo_url, url)
        print(f"  done {url}")

    # Athletics rosters (one fetch each covers the whole staff list)
    for url in ATHLETICS_SOURCES:
        try:
            html = fetch(url)
        except Exception as e:
            print(f"  FAILED {url}: {e}")
            continue
        for name, photo_url, title in extract_nuxt_payload_roster(html):
            pos_id = match_candidate(name, targets_by_lastname)
            if pos_id and targets[pos_id]["group"] in ("head_coach", "athletic_director"):
                record(pos_id, name, photo_url, url)
        print(f"  done {url}")

    # ---- report ----
    matched = len(found)
    print(f"\nMatched {matched} / {len(targets)} target people")
    unmatched = [t for pid, t in targets.items() if pid not in found]
    print(f"\nUnmatched ({len(unmatched)}):")
    for t in sorted(unmatched, key=lambda t: (t["campus"], t["group"], t["name"])):
        print(f"  [{t['group']:18}] {t['campus']:9} {t['name']}")

    manifest = {}
    for pos_id, info in found.items():
        manifest[pos_id] = {
            **targets[pos_id],
            "photo_url": info["photo_url"],
            "source_url": info["source_url"],
        }

    OUT_PATH.parent.mkdir(exist_ok=True)
    OUT_PATH.write_text(json.dumps(manifest, indent=2, ensure_ascii=False))
    print(f"\nWrote {OUT_PATH} ({len(manifest)} entries)")


if __name__ == "__main__":
    main()
