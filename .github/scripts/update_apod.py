#!/usr/bin/env python3
"""Fetch NASA's Astronomy Picture of the Day and refresh the APOD panel in README.md.

Runs daily from a GitHub Action. Standard library only, apart from an optional
Pillow import used to downscale the rare picture that is too large for GitHub's
image proxy. Sources are tried in order: the JSON API (with retries), then
apod.nasa.gov's own page. Fails soft: if both are unreachable or return
something unexpected, the existing README is left untouched.
"""
import base64
import io
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
from html import unescape

API = "https://api.nasa.gov/planetary/apod"
KEY = os.environ.get("NASA_API_KEY") or "DEMO_KEY"  # DEMO_KEY is fine for 1 call/day
README = "README.md"
START, END = "<!-- APOD:START -->", "<!-- APOD:END -->"
FALLBACK = "https://apod.nasa.gov/apod/astropix.html"
UA = "Mann5700-APOD-bot"

# GitHub proxies every README image through camo, which 404s on anything much
# bigger than ~5 MB. APOD's `hdurl` is regularly 20-30 MB, so an image that big
# renders as a broken image on the profile no matter how healthy the API is.
CAMO_MAX_BYTES = 4_500_000

# Self-hosted composite thumbnail (video days): the poster frame embedded as a
# data URI with a play-button overlay, committed so GitHub serves it directly.
THUMB_DIR = os.path.join("assets", "apod")
THUMB_PATH = os.path.join(THUMB_DIR, "thumb.svg")
THUMB_REF = "./assets/apod/thumb.svg"

# Last-resort self-hosted copy, downscaled until camo will serve it.
LOCAL_PATH = os.path.join(THUMB_DIR, "today.jpg")
LOCAL_REF = "./assets/apod/today.jpg"


def fetch():
    """APOD payload from the JSON API, retried a few times.

    api.nasa.gov reliably throws 500s for a stretch after each UTC midnight,
    before the new entry is published.
    """
    url = f"{API}?api_key={KEY}&thumbs=true"
    last = None
    for attempt in range(3):
        if attempt:
            time.sleep(5 * attempt)
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.load(resp)
        except Exception as exc:  # noqa: BLE001 - retried, then the page is scraped
            last = exc
            print(f"::warning::APOD API attempt {attempt + 1} failed: {exc}")
    raise last


def strip_tags(html):
    text = re.sub(r"(?s)<.*?>", " ", html)
    text = unescape(text)
    text = re.sub(r"\s+", " ", text).strip()
    # Removing inline links leaves stray gaps like "stars ." and "NASA 's".
    text = re.sub(r"\s+([,.;:!?%])", r"\1", text)
    text = re.sub(r"\s*/\s*", "/", text)
    text = re.sub(r"\(\s+", "(", text)
    text = re.sub(r"\s+\)", ")", text)
    return re.sub(r"\s+(['\u2019])", r"\1", text)


def scrape():
    """Rebuild the APOD payload from apod.nasa.gov's own page.

    The HTML page is the source of truth and stays up even when the JSON API
    is throwing 500s, so it keeps the panel current on API outage days.
    """
    req = urllib.request.Request(FALLBACK, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=30) as resp:
        html = resp.read().decode("utf-8", "replace")

    base = "https://apod.nasa.gov/apod/"
    data = {"media_type": "image"}

    m = re.search(r"(\d{4})\s+([A-Z][a-z]+)\s+(\d{1,2})\s*<br", html)
    if m:
        months = ["January", "February", "March", "April", "May", "June", "July",
                  "August", "September", "October", "November", "December"]
        try:
            data["date"] = f"{m.group(1)}-{months.index(m.group(2)) + 1:02d}-{int(m.group(3)):02d}"
        except ValueError:
            pass

    m = re.search(r"</center>\s*<center>\s*<b>\s*(.*?)\s*</b>", html, re.I | re.S)
    if m:
        data["title"] = strip_tags(m.group(1))

    m = re.search(r"<b>\s*Image Credit[^<]*</b>(.*?)<b>", html, re.I | re.S)
    if m:
        data["copyright"] = strip_tags(m.group(1))

    m = re.search(r"<b>\s*Explanation:\s*</b>(.*?)<p>\s*<center>", html, re.I | re.S)
    if m:
        data["explanation"] = strip_tags(m.group(1))

    m = re.search(r"<iframe[^>]+src=[\"']([^\"']+)[\"']", html, re.I)
    if m:
        data["media_type"] = "video"
        data["url"] = m.group(1)
        vid = re.search(r"(?:youtube\.com/embed/|youtu\.be/)([\w-]+)", m.group(1))
        if vid:
            data["thumbnail_url"] = f"https://img.youtube.com/vi/{vid.group(1)}/maxresdefault.jpg"
        return data

    m = re.search(r"<img\s+src=[\"']([^\"']+)[\"']", html, re.I)
    if m:
        data["url"] = urllib.parse.urljoin(base, m.group(1))
    m = re.search(r"<a\s+href=[\"'](image/[^\"']+)[\"']\s*>\s*<img", html, re.I)
    if m:
        data["hdurl"] = urllib.parse.urljoin(base, m.group(1))

    return data


def load_apod():
    try:
        return fetch()
    except Exception as exc:  # noqa: BLE001 - the HTML page is the backup source
        print(f"::warning::APOD API unavailable ({exc}), falling back to the APOD page")
        return scrape()


def probe(url):
    """Return (size_in_bytes, content_type) for url, or None if unreachable.

    size is 0 when the server does not advertise a Content-Length.
    """
    req = urllib.request.Request(url, method="HEAD", headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            size = int(resp.headers.get("Content-Length") or 0)
            ctype = (resp.headers.get("Content-Type") or "").split(";")[0].strip()
            return size, ctype
    except Exception as exc:  # noqa: BLE001 - treated as "cannot use this URL"
        print(f"::warning::HEAD {url} failed: {exc}")
        return None


def pick_image(candidates):
    """First candidate URL that GitHub's camo proxy will actually serve."""
    for url in candidates:
        info = probe(url)
        if info is None:
            continue
        size, ctype = info
        if ctype and not ctype.startswith("image/"):
            continue
        if 0 < size <= CAMO_MAX_BYTES:
            return url
        print(f"::notice::skipping {url} ({size or 'unknown'} bytes, camo limit {CAMO_MAX_BYTES})")
    return None


def self_host(candidates):
    """Downscale the first reachable candidate and commit it under assets/apod.

    Used only when every remote URL is too large for camo. Returns the README
    image reference, or None if the image could not be fetched or resized.
    """
    try:
        from PIL import Image  # noqa: PLC0415 - optional, only needed on oversized days
    except ImportError:
        print("::warning::Pillow unavailable, cannot self-host an oversized APOD")
        return None

    raw = None
    for url in candidates:
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=120) as resp:
                raw = resp.read()
            break
        except Exception as exc:  # noqa: BLE001 - try the next candidate
            print(f"::warning::download {url} failed: {exc}")

    if not raw:
        return None

    try:
        # NASA originals routinely exceed Pillow's 89 MP bomb guard; the source
        # is trusted, so raise the ceiling rather than disabling it.
        Image.MAX_IMAGE_PIXELS = 500_000_000
        img = Image.open(io.BytesIO(raw))
        img = img.convert("RGB")
        os.makedirs(THUMB_DIR, exist_ok=True)
        for width in (1600, 1200, 900, 700):
            if img.width > width:
                scaled = img.resize((width, round(img.height * width / img.width)), Image.LANCZOS)
            else:
                scaled = img
            scaled.save(LOCAL_PATH, "JPEG", quality=85, optimize=True, progressive=True)
            if os.path.getsize(LOCAL_PATH) <= CAMO_MAX_BYTES:
                print(f"::notice::self-hosted APOD at {width}px ({os.path.getsize(LOCAL_PATH)} bytes)")
                return LOCAL_REF
    except Exception as exc:  # noqa: BLE001 - fall back to the raw URL
        print(f"::warning::could not downscale APOD image: {exc}")
        return None

    return None


def esc(text):
    return (text or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def watch_url(url):
    """Turn an embeddable player URL into a normal 'watch' page URL so it opens
    as a real video page rather than a bare embed."""
    if not url:
        return FALLBACK
    m = re.search(r"(?:youtube\.com/embed/|youtu\.be/)([\w-]+)", url)
    if m:
        return f"https://www.youtube.com/watch?v={m.group(1)}"
    m = re.search(r"player\.vimeo\.com/video/(\d+)", url)
    if m:
        return f"https://vimeo.com/{m.group(1)}"
    return url


def build_video_thumb(thumb_url):
    """Download the video's poster frame, embed it in an SVG with a play-button
    overlay and write it to assets/apod/thumb.svg.

    Returns the README image reference on success, or None if the thumbnail
    could not be fetched (the caller then falls back to the plain URL).
    """
    if not thumb_url:
        return None
    try:
        req = urllib.request.Request(thumb_url, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read()
            ctype = resp.headers.get("Content-Type", "image/jpeg").split(";")[0].strip()
    except Exception as exc:  # noqa: BLE001 - fall back to the plain thumbnail URL
        print(f"::warning::video thumbnail download failed: {exc}")
        return None

    if len(raw) > CAMO_MAX_BYTES // 2:
        print(f"::warning::video poster too large to embed ({len(raw)} bytes)")
        return None

    if not ctype.startswith("image/"):
        ctype = "image/jpeg"
    data_uri = f"data:{ctype};base64," + base64.b64encode(raw).decode("ascii")

    w, h = 640, 360
    cx, cy = w // 2, h // 2
    svg = f'''<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" width="{w}" height="{h}" viewBox="0 0 {w} {h}" role="img" aria-label="Play video">
  <defs>
    <clipPath id="round"><rect width="{w}" height="{h}" rx="14"/></clipPath>
    <filter id="glow" x="-30%" y="-30%" width="160%" height="160%">
      <feDropShadow dx="0" dy="2" stdDeviation="7" flood-color="#000000" flood-opacity="0.55"/>
    </filter>
  </defs>
  <g clip-path="url(#round)">
    <image xlink:href="{data_uri}" href="{data_uri}" x="0" y="0" width="{w}" height="{h}" preserveAspectRatio="xMidYMid slice"/>
    <rect width="{w}" height="{h}" fill="#000000" opacity="0.18"/>
    <rect x="16" y="16" width="92" height="27" rx="13.5" fill="#000000" opacity="0.55"/>
    <circle cx="31" cy="29.5" r="5" fill="#f472b6"><animate attributeName="opacity" values="1;0.35;1" dur="1.6s" repeatCount="indefinite"/></circle>
    <text x="46" y="34" font-family="Segoe UI, Verdana, sans-serif" font-size="13" font-weight="700" fill="#ffffff" letter-spacing="1.5">VIDEO</text>
    <g filter="url(#glow)">
      <circle cx="{cx}" cy="{cy}" r="46" fill="#000000" opacity="0.55"/>
      <circle cx="{cx}" cy="{cy}" r="46" fill="none" stroke="#ffffff" stroke-opacity="0.92" stroke-width="3">
        <animate attributeName="r" values="46;52;46" dur="2.6s" repeatCount="indefinite"/>
        <animate attributeName="stroke-opacity" values="0.92;0.35;0.92" dur="2.6s" repeatCount="indefinite"/>
      </circle>
      <path d="M{cx-13},{cy-23} L{cx-13},{cy+23} L{cx+27},{cy} Z" fill="#ffffff"/>
    </g>
  </g>
</svg>
'''
    os.makedirs(THUMB_DIR, exist_ok=True)
    with open(THUMB_PATH, "w", encoding="utf-8") as fh:
        fh.write(svg)
    return THUMB_REF


def build_block(d):
    title = esc(d.get("title", "Astronomy Picture of the Day"))
    date = esc(d.get("date", ""))
    media = d.get("media_type", "image")

    explanation = (d.get("explanation") or "").strip()
    if len(explanation) > 300:
        explanation = explanation[:300].rsplit(" ", 1)[0] + "\u2026"
    explanation = esc(explanation)

    owner = esc((d.get("copyright") or "").strip().replace("\n", " "))

    if media == "video":
        thumb = d.get("thumbnail_url")
        # Prefer a self-hosted poster with a play-button overlay; fall back to the
        # raw thumbnail URL if it can't be downloaded.
        img = build_video_thumb(thumb) or thumb or "https://apod.nasa.gov/apod/image/apod.jpg"
        link = watch_url(d.get("url"))
        meta = f"\U0001F5D3\uFE0F {date} &nbsp;\u00B7&nbsp; \u25B6\uFE0F video of the day"
    else:
        # Prefer the web-resolution frame: `hdurl` is routinely 20-30 MB, which
        # camo refuses to proxy. The full-res original stays as the click target.
        candidates = list(dict.fromkeys(u for u in (d.get("url"), d.get("hdurl")) if u))
        img = pick_image(candidates) or self_host(candidates) or (candidates[0] if candidates else "")
        link = d.get("hdurl") or d.get("url") or FALLBACK
        meta = f"\U0001F5D3\uFE0F {date}"
        if owner:
            meta += f" &nbsp;\u00B7&nbsp; \U0001F4F7 {owner}"

    return "\n".join([
        START,
        f'<h3 align="center">{title}</h3>',
        f'<p align="center"><sub>{meta}</sub></p>',
        '<p align="center">',
        f'  <a href="{link}" target="_blank" rel="noopener noreferrer">',
        f'    <img src="{img}" width="62%" alt="{title}"/>',
        '  </a>',
        '</p>',
        f'<p align="center"><sub>{explanation}</sub></p>',
        f'<p align="center"><a href="{FALLBACK}">\U0001F517 View today\'s full transmission on NASA APOD \u2192</a></p>',
        END,
    ])


def main():
    try:
        data = load_apod()
    except Exception as exc:  # noqa: BLE001 - fail soft, never break the profile
        print(f"::warning::APOD fetch failed, keeping existing panel: {exc}")
        return 0

    if not isinstance(data, dict) or ("url" not in data and "thumbnail_url" not in data):
        print(f"::warning::Unexpected APOD payload, keeping existing panel: {data}")
        return 0

    block = build_block(data)
    with open(README, encoding="utf-8") as fh:
        readme = fh.read()

    if START not in readme or END not in readme:
        print("::error::APOD markers not found in README.md")
        return 1

    updated = re.sub(
        re.escape(START) + r".*?" + re.escape(END),
        lambda _m: block,
        readme,
        flags=re.DOTALL,
    )

    if updated != readme:
        with open(README, "w", encoding="utf-8") as fh:
            fh.write(updated)
        print(f"APOD updated -> {data.get('title')} ({data.get('date')})")
    else:
        print("APOD already up to date.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
