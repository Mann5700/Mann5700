#!/usr/bin/env python3
"""Fetch NASA's Astronomy Picture of the Day and refresh the APOD panel in README.md.

Runs daily from a GitHub Action. Uses only the Python standard library so no
dependencies need installing. Fails soft: if the API is unreachable or returns
something unexpected, the existing README is left untouched.
"""
import base64
import json
import os
import re
import sys
import urllib.request

API = "https://api.nasa.gov/planetary/apod"
KEY = os.environ.get("NASA_API_KEY") or "DEMO_KEY"  # DEMO_KEY is fine for 1 call/day
README = "README.md"
START, END = "<!-- APOD:START -->", "<!-- APOD:END -->"
FALLBACK = "https://apod.nasa.gov/apod/astropix.html"

# Self-hosted composite thumbnail (video days): the poster frame embedded as a
# data URI with a play-button overlay, committed so GitHub serves it directly.
THUMB_DIR = os.path.join("assets", "apod")
THUMB_PATH = os.path.join(THUMB_DIR, "thumb.svg")
THUMB_REF = "./assets/apod/thumb.svg"


def fetch():
    url = f"{API}?api_key={KEY}&thumbs=true"
    req = urllib.request.Request(url, headers={"User-Agent": "Mann5700-APOD-bot"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.load(resp)


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
        req = urllib.request.Request(thumb_url, headers={"User-Agent": "Mann5700-APOD-bot"})
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read()
            ctype = resp.headers.get("Content-Type", "image/jpeg").split(";")[0].strip()
    except Exception as exc:  # noqa: BLE001 - fall back to the plain thumbnail URL
        print(f"::warning::video thumbnail download failed: {exc}")
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
        img = d.get("hdurl") or d.get("url", "")
        link = d.get("hdurl") or d.get("url", FALLBACK)
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
        data = fetch()
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
