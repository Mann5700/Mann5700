#!/usr/bin/env python3
"""Fetch NASA's Astronomy Picture of the Day and refresh the APOD panel in README.md.

Runs daily from a GitHub Action. Uses only the Python standard library so no
dependencies need installing. Fails soft: if the API is unreachable or returns
something unexpected, the existing README is left untouched.
"""
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


def fetch():
    url = f"{API}?api_key={KEY}&thumbs=true"
    req = urllib.request.Request(url, headers={"User-Agent": "Mann5700-APOD-bot"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.load(resp)


def esc(text):
    return (text or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


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
        img = d.get("thumbnail_url") or "https://apod.nasa.gov/apod/image/apod.jpg"
        link = d.get("url", FALLBACK)
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
        f'  <a href="{link}" target="_blank" rel="noopener">',
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
