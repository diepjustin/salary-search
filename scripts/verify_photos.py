#!/usr/bin/env python3
"""Check every photo in the manifest resolves, and drop the ones that do not.

Run last, after the three builders:

    python3 scripts/build_leadership_photos.py
    python3 scripts/build_unmc_photos.py
    python3 scripts/build_campus_photos.py
    python3 scripts/verify_photos.py

The page hotlinks these images from the universities' own servers, so a URL can
be wrong the moment it is written and can rot later. Both happen here:

  * UNL's Drupal sites serve each other's files. film.unl.edu prints image paths
    under /sites/unl.edu.hixson-lied/ and mme.unl.edu prints them under
    /sites/unl.edu.engineering/. Resolved against the page's own host those
    404; against the site that owns the file they are fine. This retries on the
    owning host before giving up.
  * Anything still failing is removed. A dead URL is not a headshot, and
    counting one toward coverage overstates what the page can show.

    python3 scripts/verify_photos.py --dry-run   # report without changing
"""

import argparse
import json
import re
import sys
import time
import urllib.error
import urllib.request
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MANIFEST = ROOT / "data" / "leadership_photos.json"

UA = (
    "diepjustin.github.io-salary-search/1.0 "
    "(personal, non-commercial student-journalism project; "
    "contact: sdiepxj367@gmail.com)"
)
REQUEST_DELAY = 0.2

# The Drupal site that owns a file is named in its path, but that slug is not
# always the host that serves it -- the Hixson-Lied college's files sit under
# unl.edu.hixson-lied and are served from arts.unl.edu.
SITE_SLUG_RE = re.compile(r"/sites/unl\.edu\.([a-z0-9-]+)/")
SLUG_HOSTS = {"hixson-lied": "arts"}


def alternates(url):
    """Other hosts that might serve this file."""
    match = SITE_SLUG_RE.search(url)
    if not match:
        return []
    slug = match.group(1)
    hosts = [SLUG_HOSTS.get(slug, slug), slug.split("-")[0]]
    out = []
    for host in dict.fromkeys(hosts):
        candidate = re.sub(r"^https://[a-z0-9.-]+", f"https://{host}.unl.edu", url)
        if candidate != url:
            out.append(candidate)
    return out


# The first bytes of the formats these sites serve. Content-type headers cannot
# be relied on here, so the file itself is asked what it is.
MAGIC = (b"\xff\xd8\xff", b"\x89PNG", b"GIF8", b"RIFF", b"<svg", b"\x00\x00\x01\x00")


def looks_like_image(head_bytes, content_type):
    if content_type.startswith("image/"):
        return True
    return head_bytes.startswith(MAGIC) or head_bytes[8:12] == b"WEBP"


def check(url, timeout=20):
    """(ok, note).

    A HEAD is cheapest and is tried first, but it cannot be trusted on its own:
    huskers.com answers HEAD with 200 and no image content-type, and
    images.sidearmdev.com refuses HEAD altogether with 405. Treating either as a
    failure deletes photos that work perfectly well in a browser -- it discarded
    211 athletics headshots before this fallback existed. So anything short of a
    clear yes is settled by fetching the first few bytes and looking at them.
    """
    try:
        request = urllib.request.Request(url, headers={"User-Agent": UA}, method="HEAD")
        with urllib.request.urlopen(request, timeout=timeout) as response:
            if response.headers.get("content-type", "").startswith("image/"):
                return True, "image"
    except Exception:  # noqa: BLE001 - fall through to the ranged GET
        pass

    # A network error here is usually the server briefly refusing a rapid
    # sequence of requests, not a missing file: of 112 connection failures in
    # one pass, retrying showed the images were mostly fine. Dropping an entry
    # removes a real photo from the site, so a blip must not cause one. An HTTP
    # status is a real answer from the server and is never retried.
    note = "URLError"
    for attempt in range(3):
        try:
            request = urllib.request.Request(
                url, headers={"User-Agent": UA, "Range": "bytes=0-1023"}
            )
            with urllib.request.urlopen(request, timeout=timeout) as response:
                blob = response.read(1024)
                kind = response.headers.get("content-type", "")
                if looks_like_image(blob, kind):
                    return True, "image"
                return False, kind or f"HTTP {response.status}"
        except urllib.error.HTTPError as exc:
            return False, f"HTTP {exc.code}"
        except Exception as exc:  # noqa: BLE001 - retried, then reported
            note = type(exc).__name__
            if attempt < 2:
                time.sleep(1.5 * (attempt + 1))
    return False, note


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true", help="report without changing")
    args = ap.parse_args()

    if not MANIFEST.exists():
        sys.exit("No data/leadership_photos.json -- run the photo builders first.")
    manifest = json.loads(MANIFEST.read_text())
    print(f"checking {len(manifest):,} photos")

    keep, dropped, repaired = {}, [], 0
    stats = Counter()
    for i, (name, entry) in enumerate(sorted(manifest.items()), 1):
        url = entry["photo_url"]
        ok, note = check(url)
        if not ok:
            for candidate in alternates(url):
                time.sleep(REQUEST_DELAY)
                ok, note = check(candidate)
                if ok:
                    entry["photo_url"] = candidate
                    repaired += 1
                    break
        if ok:
            keep[name] = entry
        else:
            dropped.append((name, entry.get("group", ""), note, url))
            stats[note] += 1
        time.sleep(REQUEST_DELAY)
        if i % 250 == 0:
            print(f"  {i:,}/{len(manifest):,}  kept {len(keep):,}  dropped {len(dropped)}")

    print(f"\n{len(keep):,} verified, {repaired} repaired, {len(dropped)} dropped")
    if dropped:
        print("\nreasons:")
        for note, count in stats.most_common():
            print(f"  {count:>5}  {note}")
        print("\nby source:")
        for group, count in Counter(g for _, g, _, _ in dropped).most_common():
            print(f"  {count:>5}  {group}")
        print("\nexamples:")
        for name, _group, note, url in dropped[:8]:
            print(f"  {name[:28]:28} {note:10} {url[:70]}")

    if args.dry_run:
        print("\n--dry-run: manifest not changed")
        return
    MANIFEST.write_text(json.dumps(keep, indent=2, ensure_ascii=False))
    print(f"\nwrote {MANIFEST.relative_to(ROOT)} ({len(keep):,} entries)")


if __name__ == "__main__":
    main()
