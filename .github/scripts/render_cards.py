#!/usr/bin/env python3
"""Generate self-hosted, space-themed stat cards from the GitHub API.

Renders three animated SVGs into assets/cards/ that are committed to the repo,
so they are served by GitHub itself — no third-party service, no rate-limit 402s,
and nothing for a corporate proxy to block. Refreshed daily by a GitHub Action.

Cards:
  * stats.svg     — overview: stars, forks, repos, followers + top-language ring
  * languages.svg — most-used languages with a stacked bar
  * trophy.svg    — a row of milestone "achievement" tiles

Uses only the Python standard library. Fails soft: on any API error the existing
SVGs are left untouched so the profile never breaks.
"""
import datetime as dt
import json
import math
import os
import random
import sys
import urllib.request

USER = os.environ.get("GH_USER", "Mann5700")
TOKEN = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
OUT_DIR = os.path.join("assets", "cards")

# ---- theme --------------------------------------------------------------
BG_A, BG_B = "#0b1021", "#0d0524"
TITLE = "#a78bfa"
TEXT = "#c9d1d9"
MUTED = "#8b949e"
C_CYAN, C_VIOLET, C_PINK = "#22d3ee", "#a78bfa", "#f472b6"

LANG_COLORS = {
    "Python": "#3572A5", "Jupyter Notebook": "#DA5B0B", "Java": "#b07219",
    "C++": "#f34b7d", "C": "#555555", "C#": "#178600", "JavaScript": "#f1e05a",
    "TypeScript": "#3178c6", "HTML": "#e34c26", "CSS": "#563d7c", "SCSS": "#c6538c",
    "Shell": "#89e051", "Go": "#00ADD8", "Rust": "#dea584", "Ruby": "#701516",
    "PHP": "#4F5D95", "Kotlin": "#A97BFF", "Swift": "#F05138", "Dart": "#00B4AB",
    "Vue": "#41b883", "Dockerfile": "#384d54", "Makefile": "#427819",
}
FALLBACK_COLORS = [C_VIOLET, C_PINK, C_CYAN, "#7c3aed", "#e9d5ff", "#38bdf8"]


# ---- api ----------------------------------------------------------------
def api(path):
    url = path if path.startswith("http") else f"https://api.github.com{path}"
    headers = {"User-Agent": "Mann5700-cards", "Accept": "application/vnd.github+json"}
    if TOKEN:
        headers["Authorization"] = f"Bearer {TOKEN}"
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.load(resp)


def collect():
    user = api(f"/users/{USER}")
    repos, page = [], 1
    while True:
        batch = api(f"/users/{USER}/repos?per_page=100&type=owner&page={page}")
        repos.extend(batch)
        if len(batch) < 100:
            break
        page += 1
    owned = [r for r in repos if not r.get("fork")]

    stars = sum(r.get("stargazers_count", 0) for r in owned)
    forks = sum(r.get("forks_count", 0) for r in owned)

    lang_count = {}
    for r in owned:
        lang = r.get("language")
        if lang:
            lang_count[lang] = lang_count.get(lang, 0) + 1

    created = dt.datetime.strptime(user["created_at"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=dt.timezone.utc)
    years = (dt.datetime.now(dt.timezone.utc) - created).days / 365.25

    return {
        "name": user.get("name") or USER,
        "stars": stars,
        "forks": forks,
        "repos": len(owned),
        "followers": user.get("followers", 0),
        "following": user.get("following", 0),
        "years": years,
        "langs": sorted(lang_count.items(), key=lambda kv: kv[1], reverse=True),
    }


# ---- svg helpers --------------------------------------------------------
def esc(s):
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def commas(n):
    return f"{n:,}"


def star_path(cx, cy, r, inner=0.4):
    pts = []
    for i in range(10):
        ang = math.radians(-90 + i * 36)
        rad = r if i % 2 == 0 else r * inner
        pts.append(f"{cx + rad * math.cos(ang):.1f},{cy + rad * math.sin(ang):.1f}")
    return "M" + " L".join(pts) + " Z"


def twinkles(w, h, n, seed):
    rnd = random.Random(seed)
    out = []
    for _ in range(n):
        x = round(rnd.uniform(6, w - 6), 1)
        y = round(rnd.uniform(6, h - 6), 1)
        r = round(rnd.uniform(0.4, 1.2), 2)
        dur = round(rnd.uniform(2.2, 5.0), 2)
        beg = round(rnd.uniform(0, 4), 2)
        out.append(
            f'<circle cx="{x}" cy="{y}" r="{r}" fill="#ffffff">'
            f'<animate attributeName="opacity" values="0.08;0.9;0.08" '
            f'dur="{dur}s" begin="{beg}s" repeatCount="indefinite"/></circle>'
        )
    return "\n  ".join(out)


def defs(uid):
    return f'''<defs>
    <linearGradient id="bg{uid}" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0%" stop-color="{BG_A}"/><stop offset="100%" stop-color="{BG_B}"/>
    </linearGradient>
    <linearGradient id="stroke{uid}" x1="0" y1="0" x2="1" y2="0">
      <stop offset="0%" stop-color="{C_CYAN}"/><stop offset="50%" stop-color="{C_VIOLET}"/><stop offset="100%" stop-color="{C_PINK}"/>
    </linearGradient>
  </defs>'''


def card_open(w, h, uid):
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" '
        f'viewBox="0 0 {w} {h}" fill="none" role="img">\n'
        f'  {defs(uid)}\n'
        f'  <rect x="1" y="1" width="{w - 2}" height="{h - 2}" rx="14" '
        f'fill="url(#bg{uid})" stroke="url(#stroke{uid})" stroke-width="1.5"/>\n'
        f'  <g>{twinkles(w, h, max(10, w // 26), uid)}</g>'
    )


def title_block(x, y, text, sub):
    star = f'<path d="{star_path(x + 7, y - 5, 7)}" fill="{C_PINK}"><animate attributeName="opacity" values="0.5;1;0.5" dur="3s" repeatCount="indefinite"/></path>'
    t = f'<text x="{x + 22}" y="{y}" font-family="Segoe UI, Verdana, sans-serif" font-size="17" font-weight="700" fill="{TITLE}" letter-spacing="1.5">{esc(text)}</text>'
    s = f'<text x="{x + 22}" y="{y + 16}" font-family="Consolas, monospace" font-size="10.5" fill="{MUTED}">{esc(sub)}</text>'
    return star + t + s


# ---- cards --------------------------------------------------------------
def render_stats(d):
    w, h, uid = 480, 195, "s"
    parts = [card_open(w, h, uid), title_block(24, 40, "FLIGHT TELEMETRY", f"@{USER} \u00b7 commander's console")]
    parts.append(f'<line x1="24" y1="60" x2="{w-24}" y2="60" stroke="{MUTED}" stroke-opacity="0.2"/>')

    rows = [
        ("Total Stars", commas(d["stars"]), C_PINK),
        ("Total Forks", commas(d["forks"]), C_CYAN),
        ("Public Repos", commas(d["repos"]), C_VIOLET),
        ("Followers", commas(d["followers"]), C_PINK),
        ("Following", commas(d["following"]), C_CYAN),
    ]
    y = 86
    for label, value, dot in rows:
        parts.append(f'<circle cx="30" cy="{y-4}" r="3.2" fill="{dot}"/>')
        parts.append(f'<text x="44" y="{y}" font-family="Segoe UI, sans-serif" font-size="13" fill="{TEXT}">{esc(label)}</text>')
        parts.append(f'<text x="300" y="{y}" text-anchor="end" font-family="Consolas, monospace" font-size="13.5" font-weight="700" fill="{dot}">{esc(value)}</text>')
        y += 21

    # top-language ring
    cx, cy, r = 393, 116, 43
    if d["langs"]:
        total = sum(v for _, v in d["langs"])
        top_name, top_bytes = d["langs"][0]
        pct = (top_bytes / total * 100) if total else 0
    else:
        top_name, pct = "N/A", 0
    circ = 2 * math.pi * r
    frac = max(0.04, min(1.0, pct / 100))
    off = circ * (1 - frac)
    parts.append(f'<circle cx="{cx}" cy="{cy}" r="{r}" fill="none" stroke="{MUTED}" stroke-opacity="0.18" stroke-width="8"/>')
    parts.append(
        f'<circle cx="{cx}" cy="{cy}" r="{r}" fill="none" stroke="url(#stroke{uid})" stroke-width="8" '
        f'stroke-linecap="round" stroke-dasharray="{circ:.1f}" stroke-dashoffset="{circ:.1f}" '
        f'transform="rotate(-90 {cx} {cy})"><animate attributeName="stroke-dashoffset" from="{circ:.1f}" '
        f'to="{off:.1f}" dur="1.6s" begin="0.2s" fill="freeze" calcMode="spline" keySplines="0.4 0 0.2 1"/></circle>'
    )
    parts.append(f'<text x="{cx}" y="{cy+2}" text-anchor="middle" font-family="Segoe UI, sans-serif" font-size="24" font-weight="800" fill="{TITLE}">{pct:.0f}%</text>')
    parts.append(f'<text x="{cx}" y="{cy+20}" text-anchor="middle" font-family="Consolas, monospace" font-size="10" fill="{MUTED}">{esc(top_name)}</text>')
    parts.append(f'<text x="{cx}" y="{cy-24}" text-anchor="middle" font-family="Consolas, monospace" font-size="9" fill="{MUTED}" letter-spacing="1">TOP LANG</text>')

    parts.append("</svg>")
    return "\n  ".join(parts)


def render_languages(d):
    w, h, uid = 360, 232, "l"
    parts = [card_open(w, h, uid), title_block(24, 40, "MOST USED LANGUAGES", "primary language across public repos")]

    langs = d["langs"][:6]
    total = sum(v for _, v in langs) or 1
    colors = []
    for i, (name, _) in enumerate(langs):
        colors.append(LANG_COLORS.get(name, FALLBACK_COLORS[i % len(FALLBACK_COLORS)]))

    # stacked bar
    bx, by, bw, bh = 24, 64, w - 48, 13
    x = bx
    parts.append(f'<clipPath id="bar{uid}"><rect x="{bx}" y="{by}" width="{bw}" height="{bh}" rx="6.5"/></clipPath>')
    parts.append(f'<g clip-path="url(#bar{uid})">')
    parts.append(f'<rect x="{bx}" y="{by}" width="{bw}" height="{bh}" fill="#1b2340"/>')
    for (name, size), col in zip(langs, colors):
        seg = bw * size / total
        parts.append(f'<rect x="{x:.1f}" y="{by}" width="{seg:.1f}" height="{bh}" fill="{col}"/>')
        x += seg
    parts.append("</g>")

    # legend list
    y = 108
    for (name, size), col in zip(langs, colors):
        pct = size / total * 100
        parts.append(f'<circle cx="30" cy="{y-4}" r="4" fill="{col}"/>')
        parts.append(f'<text x="44" y="{y}" font-family="Segoe UI, sans-serif" font-size="12.5" fill="{TEXT}">{esc(name)}</text>')
        parts.append(f'<text x="{w-24}" y="{y}" text-anchor="end" font-family="Consolas, monospace" font-size="12" font-weight="700" fill="{col}">{pct:.1f}%</text>')
        y += 21

    if not langs:
        parts.append(f'<text x="{w/2}" y="130" text-anchor="middle" font-family="Segoe UI" font-size="13" fill="{MUTED}">No language data yet</text>')

    parts.append("</svg>")
    return "\n  ".join(parts)


def render_trophy(d):
    w, h, uid = 900, 132, "t"
    parts = [card_open(w, h, uid)]

    tiles = [
        ("PUBLIC REPOS", commas(d["repos"]), C_VIOLET),
        ("STARS EARNED", commas(d["stars"]), C_PINK),
        ("FORKS", commas(d["forks"]), C_CYAN),
        ("FOLLOWERS", commas(d["followers"]), C_PINK),
        ("LANGUAGES", commas(len(d["langs"])), C_VIOLET),
        ("LIGHT-YEARS", f'{d["years"]:.1f}', C_CYAN),
    ]

    pad, gap, n = 18, 12, len(tiles)
    tile_w = (w - 2 * pad - (n - 1) * gap) / n
    tile_h = 100
    ty = 16
    for i, (label, value, col) in enumerate(tiles):
        tx = pad + i * (tile_w + gap)
        cx = tx + tile_w / 2
        parts.append(f'<rect x="{tx:.1f}" y="{ty}" width="{tile_w:.1f}" height="{tile_h}" rx="10" fill="#121a35" stroke="{col}" stroke-opacity="0.35"/>')
        parts.append(f'<path d="{star_path(cx, ty + 24, 9)}" fill="{col}"><animate attributeName="opacity" values="0.55;1;0.55" dur="{3 + i*0.4}s" repeatCount="indefinite"/></path>')
        parts.append(f'<text x="{cx:.1f}" y="{ty+64}" text-anchor="middle" font-family="Segoe UI, sans-serif" font-size="23" font-weight="800" fill="{TEXT}">{esc(value)}</text>')
        parts.append(f'<text x="{cx:.1f}" y="{ty+84}" text-anchor="middle" font-family="Consolas, monospace" font-size="9.5" fill="{MUTED}" letter-spacing="0.5">{esc(label)}</text>')

    parts.append("</svg>")
    return "\n  ".join(parts)


def main():
    try:
        data = collect()
    except Exception as exc:  # noqa: BLE001 - keep last-good cards on failure
        print(f"::warning::card generation skipped (API error): {exc}")
        return 0

    os.makedirs(OUT_DIR, exist_ok=True)
    cards = {
        "stats.svg": render_stats(data),
        "languages.svg": render_languages(data),
        "trophy.svg": render_trophy(data),
    }
    for name, svg in cards.items():
        with open(os.path.join(OUT_DIR, name), "w", encoding="utf-8") as fh:
            fh.write(svg + "\n")
    print(f"Rendered {len(cards)} cards for @{USER}: "
          f"{data['stars']}\u2605 {data['repos']} repos {data['followers']} followers, "
          f"{len(data['langs'])} languages.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
