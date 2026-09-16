#!/usr/bin/env python3
"""Generate self-hosted, space-themed profile art from the GitHub API.

Everything is committed to the repo as SVG, so GitHub serves it directly — no
third-party service, no rate-limit 402s, and nothing for a corporate proxy to
block. Refreshed on a schedule by .github/workflows/cards.yml.

Written to assets/cards/:
  * stats.svg     — stars, forks, repos, followers + top-language orbit gauge
  * languages.svg — language spectrum, top 5 + Other, always totalling 100%
  * streak.svg    — lifetime contributions, current streak, longest streak
  * trophy.svg    — milestone badge tiles
  * activity.svg  — last 30 days as a flight path, flown by a probe
  * typing.svg    — animated tagline
  * footer.svg    — closing wave
Plus assets/header.svg, the hero banner.

Output is deterministic for a given data set, so an unchanged profile produces
an unchanged commit. Uses only the Python standard library, and fails soft: on
any API error the existing SVGs are left untouched so the profile never breaks.

Animations are authored so that every piece of *content* is fully drawn at t=0.
Some renderers (notably SVGs embedded via <img>) freeze SMIL on the first frame,
which would permanently hide anything that fades or draws itself in.
"""
import datetime as dt
import json
import math
import os
import random
import re
import sys
import time
import urllib.request

USER = os.environ.get("GH_USER", "Mann5700")
TOKEN = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
OUT_DIR = os.path.join("assets", "cards")

# ---- theme --------------------------------------------------------------
BG_A, BG_B = "#0b1021", "#0d0524"
DEEP = "#070a18"
TITLE = "#e6e9ff"
TEXT = "#c9d1d9"
MUTED = "#8b949e"
C_CYAN, C_VIOLET, C_PINK = "#22d3ee", "#a78bfa", "#f472b6"
C_GOLD = "#fbbf24"
FONT = "'Segoe UI', Verdana, system-ui, sans-serif"
MONO = "'Fira Code', ui-monospace, SFMono-Regular, Consolas, monospace"

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
def _get(url, data=None, headers=None, attempts=3):
    """HTTP with retry/backoff so a transient 5xx doesn't cost a whole refresh."""
    last = None
    for i in range(attempts):
        try:
            req = urllib.request.Request(url, data=data, headers=headers or {})
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.load(resp)
        except Exception as exc:  # noqa: BLE001 - retried below, re-raised if final
            last = exc
            if i < attempts - 1:
                time.sleep(2 ** i)
    raise last


def api(path):
    url = path if path.startswith("http") else f"https://api.github.com{path}"
    headers = {"User-Agent": "Mann5700-cards", "Accept": "application/vnd.github+json"}
    if TOKEN:
        headers["Authorization"] = f"Bearer {TOKEN}"
    return _get(url, headers=headers)


GRAPHQL_URL = "https://api.github.com/graphql"

CONTRIB_QUERY = """
query($login: String!, $from: DateTime!, $to: DateTime!) {
  user(login: $login) {
    contributionsCollection(from: $from, to: $to) {
      contributionCalendar {
        weeks { contributionDays { date contributionCount } }
      }
    }
  }
}
"""


def graphql(query, variables):
    payload = json.dumps({"query": query, "variables": variables}).encode("utf-8")
    headers = {
        "User-Agent": "Mann5700-cards",
        "Content-Type": "application/json",
        "Authorization": f"Bearer {TOKEN}",
    }
    body = _get(GRAPHQL_URL, data=payload, headers=headers)
    if body.get("errors"):
        raise RuntimeError(body["errors"][0].get("message", "GraphQL error"))
    return body["data"]


def fetch_contributions(created_at):
    """Return sorted [(date_str, count)] across every year since account creation.

    GraphQL is authenticated-only, so this needs a token (the Action provides one).
    Returns [] when unavailable, letting the caller fall back to a graceful
    "syncing" state instead of breaking.
    """
    if not TOKEN:
        return []
    start_year = dt.datetime.strptime(created_at, "%Y-%m-%dT%H:%M:%SZ").year
    now = dt.datetime.now(dt.timezone.utc)
    days = {}
    for year in range(start_year, now.year + 1):
        frm = dt.datetime(year, 1, 1, tzinfo=dt.timezone.utc)
        to = dt.datetime(year, 12, 31, 23, 59, 59, tzinfo=dt.timezone.utc)
        if to > now:
            to = now
        data = graphql(CONTRIB_QUERY, {"login": USER, "from": frm.isoformat(), "to": to.isoformat()})
        weeks = data["user"]["contributionsCollection"]["contributionCalendar"]["weeks"]
        for wk in weeks:
            for day in wk["contributionDays"]:
                days[day["date"]] = day["contributionCount"]
    return sorted(days.items())


def compute_streaks(days):
    """From [(date_str, count)] compute totals, current streak and longest streak."""
    parsed = sorted((dt.date.fromisoformat(d), c) for d, c in days)
    total = sum(c for _, c in parsed)
    active = [d for d, c in parsed if c > 0]

    longest = run = 0
    run_start = longest_start = longest_end = None
    prev = None
    for d in active:
        if prev is not None and (d - prev).days == 1:
            run += 1
        else:
            run = 1
            run_start = d
        if run > longest:
            longest, longest_start, longest_end = run, run_start, d
        prev = d

    active_set = set(active)
    today = dt.datetime.now(dt.timezone.utc).date()
    cursor = today if today in active_set else today - dt.timedelta(days=1)
    current = 0
    while cursor in active_set:
        current += 1
        cursor -= dt.timedelta(days=1)
    current_start = (active[-1] - dt.timedelta(days=current - 1)) if current else None

    return {
        "total": total,
        "current": current,
        "longest": longest,
        "first": active[0] if active else None,
        "last": active[-1] if active else None,
        "current_start": current_start,
        "current_end": active[-1] if current else None,
        "longest_start": longest_start,
        "longest_end": longest_end,
    }


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

    # Language mix is averaged per repo: every repo contributes 1.0, split by its
    # own byte breakdown. Summing raw bytes instead would be ~98% Jupyter Notebook,
    # because notebooks embed their rendered output in the source file.
    lang_share = {}
    repo_langs = {}
    for r in owned:
        try:
            breakdown = api(r["languages_url"])
        except Exception as exc:  # noqa: BLE001 - one bad repo must not sink the card
            print(f"::warning::languages skipped for {r.get('name')}: {exc}")
            continue
        repo_total = sum(breakdown.values())
        if not repo_total:
            continue
        ordered = sorted(breakdown.items(), key=lambda kv: kv[1], reverse=True)
        repo_langs[r["name"]] = [k for k, _ in ordered[:3]]
        for lang, size in breakdown.items():
            lang_share[lang] = lang_share.get(lang, 0.0) + size / repo_total

    created = dt.datetime.strptime(user["created_at"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=dt.timezone.utc)
    years = (dt.datetime.now(dt.timezone.utc) - created).days / 365.25

    # A transient GraphQL failure must NOT be swallowed: rendering with an empty
    # calendar would overwrite good streak/activity cards with "syncing" placeholders.
    # Letting it raise keeps the last-good SVGs committed in the repo.
    contributions = fetch_contributions(user["created_at"])

    # Featured = repos that describe themselves. A repo with no description would
    # only render as filler, so it is skipped until one is set.
    missions = sorted(
        (r for r in owned
         if r["name"].lower() != USER.lower()
         and (r.get("description") or "").strip()
         and repo_langs.get(r["name"])),
        key=lambda r: (r.get("stargazers_count", 0), r.get("pushed_at") or ""),
        reverse=True,
    )[:6]

    return {
        "name": user.get("name") or USER,
        "stars": stars,
        "forks": forks,
        "repos": len(owned),
        "followers": user.get("followers", 0),
        "following": user.get("following", 0),
        "years": years,
        "langs": sorted(lang_share.items(), key=lambda kv: kv[1], reverse=True),
        "contributions": contributions,
        "streaks": compute_streaks(contributions),
        "missions": [{
            "name": r["name"],
            "url": r["html_url"],
            "desc": (r.get("description") or "").strip(),
            "langs": repo_langs.get(r["name"], []),
            "stars": r.get("stargazers_count", 0),
        } for r in missions],
    }


# ---- svg helpers --------------------------------------------------------
def esc(s):
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def commas(n):
    return f"{n:,}"


def pct_split(values, places=1):
    """Percentages that always add up to exactly 100 (largest-remainder method)."""
    total = sum(values)
    if total <= 0:
        return [0.0] * len(values)
    scale = 10 ** places
    exact = [v / total * 100 * scale for v in values]
    out = [math.floor(x) for x in exact]
    short = round(100 * scale - sum(out))
    for i in sorted(range(len(exact)), key=lambda i: exact[i] - out[i], reverse=True)[:short]:
        out[i] += 1
    return [v / scale for v in out]


def star_path(cx, cy, r, inner=0.4):
    pts = []
    for i in range(10):
        ang = math.radians(-90 + i * 36)
        rad = r if i % 2 == 0 else r * inner
        pts.append(f"{cx + rad * math.cos(ang):.1f},{cy + rad * math.sin(ang):.1f}")
    return "M" + " L".join(pts) + " Z"


def sparkle_path(cx, cy, r, waist=0.16):
    """Four-point 'lens flare' star."""
    k = r * waist
    return (f"M{cx},{cy - r} Q{cx + k},{cy - k} {cx + r},{cy} Q{cx + k},{cy + k} {cx},{cy + r} "
            f"Q{cx - k},{cy + k} {cx - r},{cy} Q{cx - k},{cy - k} {cx},{cy - r} Z")


def starfield(w, h, n, seed, pad=5):
    """Layered starfield. Every star is fully drawn at t=0, so it still reads in
    renderers that freeze SMIL on the first frame; the animation only dims them."""
    rnd = random.Random(seed)
    out = []
    for _ in range(n):
        x = round(rnd.uniform(pad, w - pad), 1)
        y = round(rnd.uniform(pad, h - pad), 1)
        r = round(rnd.uniform(0.35, 1.3), 2)
        o = round(rnd.uniform(0.35, 0.95), 2)
        dur = round(rnd.uniform(2.4, 6.5), 2)
        beg = round(rnd.uniform(0, 6), 2)
        out.append(
            f'<circle cx="{x}" cy="{y}" r="{r}" fill="#ffffff" opacity="{o}">'
            f'<animate attributeName="opacity" values="{o};0.1;{o}" dur="{dur}s" '
            f'begin="{beg}s" repeatCount="indefinite"/></circle>'
        )
    for i in range(max(2, n // 18)):                       # a few bright flares
        x = round(rnd.uniform(pad + 8, w - pad - 8), 1)
        y = round(rnd.uniform(pad + 8, h - pad - 8), 1)
        r = round(rnd.uniform(3.0, 5.4), 1)
        col = (C_CYAN, C_VIOLET, C_PINK, "#ffffff")[i % 4]
        dur = round(rnd.uniform(5.0, 9.0), 1)
        out.append(
            f'<path d="{sparkle_path(x, y, r)}" fill="{col}" opacity="0.75">'
            f'<animateTransform attributeName="transform" type="rotate" values="0 {x} {y};360 {x} {y}" '
            f'dur="{dur}s" repeatCount="indefinite"/>'
            f'<animate attributeName="opacity" values="0.75;0.25;0.75" dur="{dur / 2:.1f}s" repeatCount="indefinite"/></path>'
        )
    return "".join(out)


def comets(w, h, uid, n=2, seed=7):
    """Shooting stars that streak across on a long loop. Hidden at rest by design."""
    rnd = random.Random(seed)
    out = []
    for i in range(n):
        period = round(rnd.uniform(9, 15), 1)
        flight = round(rnd.uniform(1.1, 1.8), 2)
        begin = round(rnd.uniform(0, 8), 1)
        y0 = round(rnd.uniform(0.08, 0.55) * h, 1)
        x0 = round(rnd.uniform(-0.15, 0.25) * w, 1)
        dx = round(rnd.uniform(0.55, 0.95) * w, 1)
        dy = round(rnd.uniform(0.2, 0.45) * h, 1)
        f1, f2 = flight * 0.72 / period, flight / period
        out.append(
            f'<linearGradient id="cm{uid}{i}" x1="0" y1="0" x2="1" y2="0">'
            f'<stop offset="0%" stop-color="{C_CYAN}" stop-opacity="0"/>'
            f'<stop offset="100%" stop-color="#ffffff" stop-opacity="0.95"/></linearGradient>'
            f'<g opacity="0">'
            f'<path d="M-44,0 L0,0" stroke="url(#cm{uid}{i})" stroke-width="1.8" stroke-linecap="round"/>'
            f'<circle cx="0" cy="0" r="1.9" fill="#ffffff"/>'
            f'<animate attributeName="opacity" values="0;0.95;0.95;0;0" '
            f'keyTimes="0;0.02;{f1:.3f};{f2:.3f};1" dur="{period}s" begin="{begin}s" repeatCount="indefinite"/>'
            f'<animateMotion path="M{x0},{y0} l{dx},{dy}" keyPoints="0;1;1" keyTimes="0;{f2:.3f};1" '
            f'calcMode="linear" rotate="auto" dur="{period}s" begin="{begin}s" repeatCount="indefinite"/></g>'
        )
    return "".join(out)


def defs(uid):
    return f'''<defs>
    <linearGradient id="bg{uid}" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0%" stop-color="{DEEP}"/><stop offset="45%" stop-color="{BG_A}"/><stop offset="100%" stop-color="{BG_B}"/>
    </linearGradient>
    <radialGradient id="nebA{uid}" cx="16%" cy="20%" r="65%">
      <stop offset="0%" stop-color="#7c3aed" stop-opacity="0.42"/>
      <stop offset="55%" stop-color="#4c1d95" stop-opacity="0.13"/>
      <stop offset="100%" stop-color="{BG_A}" stop-opacity="0"/>
    </radialGradient>
    <radialGradient id="nebB{uid}" cx="88%" cy="82%" r="58%">
      <stop offset="0%" stop-color="#db2777" stop-opacity="0.30"/>
      <stop offset="100%" stop-color="{BG_A}" stop-opacity="0"/>
    </radialGradient>
    <radialGradient id="nebC{uid}" cx="72%" cy="8%" r="46%">
      <stop offset="0%" stop-color="#0891b2" stop-opacity="0.26"/>
      <stop offset="100%" stop-color="{BG_A}" stop-opacity="0"/>
    </radialGradient>
    <linearGradient id="stroke{uid}" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0%" stop-color="{C_CYAN}"/><stop offset="50%" stop-color="{C_VIOLET}"/><stop offset="100%" stop-color="{C_PINK}"/>
      <animateTransform attributeName="gradientTransform" type="rotate" values="0 .5 .5;360 .5 .5" dur="18s" repeatCount="indefinite"/>
    </linearGradient>
    <linearGradient id="sweep{uid}" x1="0" y1="0" x2="1" y2="0">
      <stop offset="0%" stop-color="{C_CYAN}" stop-opacity="0"/>
      <stop offset="50%" stop-color="{C_CYAN}" stop-opacity="0.10"/>
      <stop offset="100%" stop-color="{C_CYAN}" stop-opacity="0"/>
    </linearGradient>
    <filter id="glow{uid}" x="-70%" y="-70%" width="240%" height="240%">
      <feGaussianBlur stdDeviation="2.6" result="b"/>
      <feMerge><feMergeNode in="b"/><feMergeNode in="SourceGraphic"/></feMerge>
    </filter>
    <clipPath id="clip{uid}"><rect x="1.5" y="1.5" width="{{w}}" height="{{h}}" rx="16"/></clipPath>
  </defs>'''


def card_open(w, h, uid, stars=None):
    """Card chrome: nebula backdrop, starfield, comets, HUD brackets, glowing rim."""
    n = stars if stars is not None else max(26, int(w * h / 1700))
    d = defs(uid).replace("{w}", f"{w - 3}").replace("{h}", f"{h - 3}")
    hud = []
    arm = 15
    for cx, cy, sx, sy in ((16, 16, 1, 1), (w - 16, 16, -1, 1), (16, h - 16, 1, -1), (w - 16, h - 16, -1, -1)):
        hud.append(f'<path d="M{cx},{cy + sy * arm} L{cx},{cy} L{cx + sx * arm},{cy}" fill="none" '
                   f'stroke="{C_CYAN}" stroke-opacity="0.38" stroke-width="1.3" stroke-linecap="round"/>')
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" '
        f'viewBox="0 0 {w} {h}" fill="none" role="img">\n  {d}\n'
        f'  <g clip-path="url(#clip{uid})">'
        f'<rect x="1.5" y="1.5" width="{w - 3}" height="{h - 3}" rx="16" fill="url(#bg{uid})"/>'
        f'<rect x="1.5" y="1.5" width="{w - 3}" height="{h - 3}" fill="url(#nebA{uid})">'
        f'<animate attributeName="opacity" values="1;0.6;1" dur="11s" repeatCount="indefinite"/></rect>'
        f'<rect x="1.5" y="1.5" width="{w - 3}" height="{h - 3}" fill="url(#nebB{uid})">'
        f'<animate attributeName="opacity" values="0.8;1;0.8" dur="14s" repeatCount="indefinite"/></rect>'
        f'<rect x="1.5" y="1.5" width="{w - 3}" height="{h - 3}" fill="url(#nebC{uid})"/>'
        f'{starfield(w, h, n, uid)}{comets(w, h, uid, seed=sum(ord(c) for c in uid) * 37)}'
        f'<rect x="-{w * 0.4:.0f}" y="1.5" width="{w * 0.4:.0f}" height="{h - 3}" fill="url(#sweep{uid})">'
        f'<animate attributeName="x" values="-{w * 0.4:.0f};{w}" dur="9s" repeatCount="indefinite"/></rect>'
        f'{"".join(hud)}</g>\n'
        f'  <rect x="1.5" y="1.5" width="{w - 3}" height="{h - 3}" rx="16" fill="none" '
        f'stroke="url(#stroke{uid})" stroke-width="1.6"/>'
    )


def title_block(x, y, text, sub, uid=""):
    """Section heading with a slowly orbiting marker dot."""
    r = 9
    orbit = (
        f'<g><circle cx="{x + 8}" cy="{y - 6}" r="{r}" fill="none" stroke="{C_VIOLET}" '
        f'stroke-opacity="0.45" stroke-width="1"/>'
        f'<circle cx="{x + 8}" cy="{y - 6}" r="3.1" fill="{C_PINK}">'
        f'<animate attributeName="opacity" values="1;0.45;1" dur="2.6s" repeatCount="indefinite"/></circle>'
        f'<circle cx="{x + 8 + r}" cy="{y - 6}" r="1.7" fill="{C_CYAN}">'
        f'<animateTransform attributeName="transform" type="rotate" values="0 {x + 8} {y - 6};360 {x + 8} {y - 6}" '
        f'dur="7s" repeatCount="indefinite"/></circle></g>'
    )
    t = (f'<text x="{x + 26}" y="{y}" font-family="{FONT}" font-size="16.5" font-weight="700" '
         f'fill="{TITLE}" letter-spacing="1.6">{esc(text)}</text>')
    s = (f'<text x="{x + 26}" y="{y + 16}" font-family="{MONO}" font-size="10" '
         f'fill="{MUTED}">{esc(sub)}</text>')
    return orbit + t + s



# ---- cards --------------------------------------------------------------
def render_stats(d):
    w, h, uid = 480, 260, "s"
    parts = [card_open(w, h, uid),
             title_block(24, 44, "FLIGHT TELEMETRY", f"@{USER} \u00b7 commander's console")]
    parts.append(f'<line x1="24" y1="74" x2="{w-24}" y2="74" stroke="{C_VIOLET}" stroke-opacity="0.22"/>')

    rows = [
        ("Total Stars", commas(d["stars"]), C_PINK),
        ("Total Forks", commas(d["forks"]), C_CYAN),
        ("Public Repos", commas(d["repos"]), C_VIOLET),
        ("Followers", commas(d["followers"]), C_PINK),
        ("Following", commas(d["following"]), C_CYAN),
    ]
    y = 108
    for i, (label, value, dot) in enumerate(rows):
        parts.append(
            f'<circle cx="32" cy="{y-4}" r="3.4" fill="{dot}">'
            f'<animate attributeName="opacity" values="1;0.4;1" dur="{2.8 + i*0.45:.2f}s" repeatCount="indefinite"/></circle>'
            f'<circle cx="32" cy="{y-4}" r="7" fill="none" stroke="{dot}" stroke-opacity="0.22" stroke-width="1"/>'
        )
        parts.append(f'<text x="48" y="{y}" font-family="{FONT}" font-size="13" fill="{TEXT}">{esc(label)}</text>')
        parts.append(f'<text x="272" y="{y}" text-anchor="end" font-family="{MONO}" font-size="13.5" '
                     f'font-weight="700" fill="{dot}">{esc(value)}</text>')
        y += 27

    # --- top-language gauge, dressed as a planet in orbit ---
    cx, cy, r = 372, 162, 45
    if d["langs"]:
        top_name, top_share = d["langs"][0]
        pct = top_share / (sum(v for _, v in d["langs"]) or 1) * 100
    else:
        top_name, pct = "N/A", 0.0
    frac = max(0.02, min(1.0, pct / 100))
    circ = 2 * math.pi * r
    arc = circ * frac
    rx, ry = 66, 23
    orbit = f"M{cx-rx},{cy} a{rx},{ry} 0 1,0 {2*rx},0 a{rx},{ry} 0 1,0 {-2*rx},0"

    parts.append(f'<g transform="rotate(-18 {cx} {cy})">'
                 f'<path d="{orbit}" fill="none" stroke="{C_CYAN}" stroke-opacity="0.30" '
                 f'stroke-width="1" stroke-dasharray="3 5"/>'
                 f'<circle r="3.6" fill="{C_CYAN}">'
                 f'<animateMotion path="{orbit}" dur="9s" repeatCount="indefinite"/></circle></g>')
    parts.append(f'<circle cx="{cx}" cy="{cy}" r="{r}" fill="none" stroke="{MUTED}" '
                 f'stroke-opacity="0.16" stroke-width="9"/>')
    parts.append(f'<circle cx="{cx}" cy="{cy}" r="{r}" fill="none" stroke="url(#stroke{uid})" stroke-width="9" '
                 f'stroke-linecap="round" stroke-dasharray="{arc:.1f} {circ - arc:.1f}" '
                 f'transform="rotate(-90 {cx} {cy})" filter="url(#glow{uid})"/>')
    end_a = math.radians(-90 + 360 * frac)
    parts.append(f'<circle cx="{cx + r*math.cos(end_a):.1f}" cy="{cy + r*math.sin(end_a):.1f}" r="3.4" fill="#ffffff">'
                 f'<animate attributeName="opacity" values="1;0.35;1" dur="2.2s" repeatCount="indefinite"/></circle>')
    parts.append(f'<text x="{cx}" y="{cy-20}" text-anchor="middle" font-family="{MONO}" font-size="8.5" '
                 f'fill="{MUTED}" letter-spacing="1.2">TOP LANG</text>')
    parts.append(f'<text x="{cx}" y="{cy+8}" text-anchor="middle" font-family="{FONT}" font-size="27" '
                 f'font-weight="800" fill="{TITLE}">{pct:.0f}%</text>')
    parts.append(f'<text x="{cx}" y="{cy+26}" text-anchor="middle" font-family="{MONO}" font-size="9.5" '
                 f'fill="{C_CYAN}">{esc(top_name)}</text>')

    parts.append(f'<text x="40" y="{h-22}" font-family="{MONO}" font-size="9" fill="{MUTED}">'
                 f'\u25b8 {d["years"]:.1f} light-years in orbit</text>')

    parts.append("</svg>")
    return "\n  ".join(parts)


def render_languages(d):
    w, h, uid = 480, 260, "l"
    repos = d["repos"]
    parts = [card_open(w, h, uid),
             title_block(24, 44, "LANGUAGE SPECTRUM", f"code share across {repos} public repos")]

    ranked = d["langs"]
    top = ranked[:5]
    rest = sum(v for _, v in ranked[5:])
    rows = [(name, val, LANG_COLORS.get(name, FALLBACK_COLORS[i % len(FALLBACK_COLORS)]))
            for i, (name, val) in enumerate(top)]
    if rest > 0:
        rows.append((f"Other ({len(ranked) - 5})", rest, "#64748b"))

    if not rows:
        parts.append(f'<text x="{w/2}" y="{h/2}" text-anchor="middle" font-family="{FONT}" '
                     f'font-size="13" fill="{MUTED}">No language data yet</text>')
        parts.append("</svg>")
        return "\n  ".join(parts)

    pcts = pct_split([v for _, v, _ in rows])          # always totals exactly 100.0

    # stacked spectrum bar
    bx, by, bw, bh = 24, 84, w - 48, 15
    parts.append(f'<clipPath id="bar{uid}"><rect x="{bx}" y="{by}" width="{bw}" height="{bh}" rx="7.5"/></clipPath>')
    parts.append(f'<g clip-path="url(#bar{uid})">')
    parts.append(f'<rect x="{bx}" y="{by}" width="{bw}" height="{bh}" fill="#151b33"/>')
    x = float(bx)
    for (name, _, col), pct in zip(rows, pcts):
        seg = bw * pct / 100
        parts.append(f'<rect x="{x:.2f}" y="{by}" width="{seg:.2f}" height="{bh}" fill="{col}"/>')
        x += seg
    # light sweeping over the spectrum
    parts.append(f'<rect x="-70" y="{by}" width="70" height="{bh}" fill="#ffffff" opacity="0.16">'
                 f'<animate attributeName="x" values="-70;{w}" dur="5.5s" repeatCount="indefinite"/></rect>')
    parts.append("</g>")
    parts.append(f'<rect x="{bx}" y="{by}" width="{bw}" height="{bh}" rx="7.5" fill="none" '
                 f'stroke="{C_VIOLET}" stroke-opacity="0.35"/>')

    # one language per line: planet dot, name, orbit track, percentage
    tx0, tw = 214, 168
    y = 124
    for i, ((name, _, col), pct) in enumerate(zip(rows, pcts)):
        parts.append(f'<circle cx="34" cy="{y-4}" r="4.6" fill="{col}"/>'
                     f'<circle cx="34" cy="{y-4}" r="8" fill="none" stroke="{col}" stroke-opacity="0.28" '
                     f'stroke-width="1"><animate attributeName="r" values="8;9.6;8" dur="{3.4 + i*0.35:.2f}s" '
                     f'repeatCount="indefinite"/></circle>')
        parts.append(f'<text x="50" y="{y}" font-family="{FONT}" font-size="12.5" fill="{TEXT}">{esc(name)}</text>')
        parts.append(f'<rect x="{tx0}" y="{y-9}" width="{tw}" height="6" rx="3" fill="#1b2340"/>')
        parts.append(f'<rect x="{tx0}" y="{y-9}" width="{max(3.0, tw * pct / 100):.1f}" height="6" rx="3" fill="{col}"/>')
        parts.append(f'<text x="{w-24}" y="{y}" text-anchor="end" font-family="{MONO}" font-size="12" '
                     f'font-weight="700" fill="{col}">{pct:.1f}%</text>')
        y += 22

    parts.append(f'<text x="40" y="{h-15}" font-family="{MONO}" font-size="8" fill="{MUTED}">'
                 f'\u25b8 averaged per repository \u00b7 {len(ranked)} languages detected \u00b7 totals 100%</text>')

    parts.append("</svg>")
    return "\n  ".join(parts)


def render_trophy(d):
    w, h, uid = 480, 260, "t"
    parts = [card_open(w, h, uid),
             title_block(24, 44, "MISSION BADGES", "milestones logged en route")]

    tiles = [
        ("PUBLIC REPOS", commas(d["repos"]), C_VIOLET),
        ("STARS EARNED", commas(d["stars"]), C_GOLD),
        ("FORKS", commas(d["forks"]), C_CYAN),
        ("FOLLOWERS", commas(d["followers"]), C_PINK),
        ("LANGUAGES", commas(len(d["langs"])), C_VIOLET),
        ("LIGHT-YEARS", f'{d["years"]:.1f}', C_CYAN),
    ]

    pad, gap, cols = 20, 12, 3
    tile_w = (w - 2 * pad - (cols - 1) * gap) / cols
    tile_h, gap_y, ty0 = 80, 12, 82
    for i, (label, value, col) in enumerate(tiles):
        tx = pad + (i % cols) * (tile_w + gap)
        ty = ty0 + (i // cols) * (tile_h + gap_y)
        cx = tx + tile_w / 2
        pulse = 3.2 + i * 0.45
        parts.append(f'<rect x="{tx:.1f}" y="{ty}" width="{tile_w:.1f}" height="{tile_h}" rx="12" '
                     f'fill="#111935" fill-opacity="0.82" stroke="{col}" stroke-opacity="0.38">'
                     f'<animate attributeName="stroke-opacity" values="0.38;0.8;0.38" dur="{pulse:.2f}s" '
                     f'repeatCount="indefinite"/></rect>')
        parts.append(f'<path d="{star_path(cx, ty + 20, 8.5)}" fill="{col}" filter="url(#glow{uid})">'
                     f'<animateTransform attributeName="transform" type="rotate" '
                     f'values="0 {cx:.1f} {ty + 20};360 {cx:.1f} {ty + 20}" dur="{14 + i * 2}s" '
                     f'repeatCount="indefinite"/></path>')
        parts.append(f'<text x="{cx:.1f}" y="{ty+52}" text-anchor="middle" font-family="{FONT}" '
                     f'font-size="21" font-weight="800" fill="{TITLE}">{esc(value)}</text>')
        parts.append(f'<text x="{cx:.1f}" y="{ty+69}" text-anchor="middle" font-family="{MONO}" '
                     f'font-size="8" fill="{MUTED}" letter-spacing="0.5">{esc(label)}</text>')

    parts.append("</svg>")
    return "\n  ".join(parts)


def _kf(times, values, t, v):
    """Append a keyframe, replacing the last one if it lands on the same time."""
    t = max(0.0, min(1.0, t))
    if times and abs(t - times[-1]) < 1e-6:
        times[-1], values[-1] = t, v
    else:
        times.append(t)
        values.append(v)


def _fmt_date(d):
    return d.strftime("%b %d, %Y") if d else "—"


def render_streak(d):
    w, h, uid = 480, 260, "k"
    s = d.get("streaks") or {}
    syncing = not d.get("contributions")
    parts = [card_open(w, h, uid),
             title_block(24, 44, "ORBITAL CADENCE", "contribution continuity")]

    if syncing:
        parts.append(f'<text x="{w/2:.0f}" y="{h/2:.0f}" text-anchor="middle" font-family="{FONT}" '
                     f'font-size="14" fill="{MUTED}">Syncing streaks \u2014 awaiting first Action run\u2026</text>')
        parts.append("</svg>")
        return "\n  ".join(parts)

    for xd in (w / 3, 2 * w / 3):
        parts.append(f'<line x1="{xd:.0f}" y1="86" x2="{xd:.0f}" y2="{h-30}" stroke="{C_VIOLET}" stroke-opacity="0.16"/>')

    cols = [w * 0.17, w * 0.5, w * 0.83]
    current, longest, total = s.get("current", 0), s.get("longest", 0), s.get("total", 0)

    def pillar(cx, value, label, col, sub):
        out = [f'<text x="{cx:.0f}" y="156" text-anchor="middle" font-family="{FONT}" font-size="31" '
               f'font-weight="800" fill="{TITLE}">{esc(commas(value))}</text>',
               f'<text x="{cx:.0f}" y="202" text-anchor="middle" font-family="{MONO}" font-size="9" '
               f'fill="{col}" letter-spacing="0.9">{esc(label)}</text>',
               f'<text x="{cx:.0f}" y="219" text-anchor="middle" font-family="{MONO}" font-size="7.5" '
               f'fill="{MUTED}">{esc(sub)}</text>']
        return out

    # --- left: lifetime contributions, with a little satellite ---
    cx = cols[0]
    parts.append(f'<g transform="translate({cx:.0f} 116)">'
                 f'<ellipse rx="30" ry="11" fill="none" stroke="{C_PINK}" stroke-opacity="0.35" '
                 f'stroke-width="1" stroke-dasharray="3 4" transform="rotate(-16)"/>'
                 f'<circle r="2.8" fill="{C_PINK}"><animateMotion dur="8s" repeatCount="indefinite" '
                 f'path="M-30,0 a30,11 0 1,0 60,0 a30,11 0 1,0 -60,0"/></circle>'
                 f'<circle r="9" fill="{C_PINK}" fill-opacity="0.22"/>'
                 f'<circle r="5" fill="{C_PINK}"/></g>')
    parts.extend(pillar(cx, total, "CONTRIBUTIONS", C_PINK, f'since {_fmt_date(s.get("first"))}'))

    # --- centre: current streak in a progress ring ---
    cx, cy, r = cols[1], 116, 40
    frac = max(0.03, min(1.0, current / longest)) if longest else 0.03
    circ = 2 * math.pi * r
    arc = circ * frac
    parts.append(f'<circle cx="{cx:.0f}" cy="{cy}" r="{r}" fill="none" stroke="{MUTED}" stroke-opacity="0.16" stroke-width="7"/>')
    parts.append(f'<circle cx="{cx:.0f}" cy="{cy}" r="{r}" fill="none" stroke="url(#stroke{uid})" stroke-width="7" '
                 f'stroke-linecap="round" stroke-dasharray="{arc:.1f} {circ - arc:.1f}" '
                 f'transform="rotate(-90 {cx:.0f} {cy})" filter="url(#glow{uid})"/>')
    parts.append(f'<circle cx="{cx:.0f}" cy="{cy - r}" r="3" fill="#ffffff" opacity="0.9">'
                 f'<animateTransform attributeName="transform" type="rotate" '
                 f'values="0 {cx:.0f} {cy};360 {cx:.0f} {cy}" dur="6s" repeatCount="indefinite"/></circle>')
    parts.append(f'<text x="{cx:.0f}" y="{cy+11}" text-anchor="middle" font-family="{FONT}" font-size="32" '
                 f'font-weight="800" fill="{TITLE}">{esc(commas(current))}</text>')
    cur_range = f'{_fmt_date(s.get("current_start"))} \u2192 now' if current else "\u2014"
    parts.append(f'<text x="{cx:.0f}" y="202" text-anchor="middle" font-family="{MONO}" font-size="9" '
                 f'fill="{C_VIOLET}" letter-spacing="0.9">CURRENT STREAK</text>')
    parts.append(f'<text x="{cx:.0f}" y="219" text-anchor="middle" font-family="{MONO}" font-size="7.5" '
                 f'fill="{MUTED}">{esc(cur_range)}</text>')
    parts.append(f'<text x="{cx:.0f}" y="168" text-anchor="middle" font-family="{MONO}" font-size="8" '
                 f'fill="{MUTED}">days in a row</text>')

    # --- right: longest streak ---
    cx = cols[2]
    parts.append(f'<g transform="translate({cx:.0f} 116)">'
                 f'<ellipse rx="30" ry="11" fill="none" stroke="{C_CYAN}" stroke-opacity="0.35" '
                 f'stroke-width="1" stroke-dasharray="3 4" transform="rotate(16)"/>'
                 f'<circle r="2.8" fill="{C_CYAN}"><animateMotion dur="10s" repeatCount="indefinite" '
                 f'path="M-30,0 a30,11 0 1,1 60,0 a30,11 0 1,1 -60,0"/></circle>'
                 f'<circle r="9" fill="{C_CYAN}" fill-opacity="0.22"/>'
                 f'<circle r="5" fill="{C_CYAN}"/></g>')
    long_range = (f'{_fmt_date(s.get("longest_start"))} \u2192 {_fmt_date(s.get("longest_end"))}'
                  if longest else "\u2014")
    parts.extend(pillar(cx, longest, "LONGEST STREAK", C_CYAN, long_range))

    parts.append("</svg>")
    return "\n  ".join(parts)


def render_activity(d):
    w, h, uid = 980, 250, "a"
    days = (d.get("contributions") or [])[-30:]
    parts = [card_open(w, h, uid),
             title_block(26, 46, "ORBITAL TRAJECTORY", "contribution flight path \u00b7 last 30 days")]

    if not days:
        parts.append(f'<text x="{w/2:.0f}" y="{h/2:.0f}" text-anchor="middle" font-family="{FONT}" '
                     f'font-size="14" fill="{MUTED}">Syncing telemetry \u2014 awaiting first Action run\u2026</text>')
        parts.append("</svg>")
        return "\n  ".join(parts)

    left, right, top, bottom = 52, w - 40, 92, h - 46
    counts = [c for _, c in days]
    peak = max(counts)
    mx = peak or 1          # avoid divide-by-zero when the window has no contributions
    n = len(days)

    def px(i):
        return left + (right - left) * (i / (n - 1) if n > 1 else 0.5)

    def py(v):
        return bottom - (bottom - top) * (v / mx)

    pts = [(px(i), py(c)) for i, c in enumerate(counts)]
    line = " ".join(f"{x:.1f},{y:.1f}" for x, y in pts)
    motion = "M" + " L".join(f"{x:.1f},{y:.1f}" for x, y in pts)
    area = (
        f"M{pts[0][0]:.1f},{bottom:.1f} L"
        + " L".join(f"{x:.1f},{y:.1f}" for x, y in pts)
        + f" L{pts[-1][0]:.1f},{bottom:.1f} Z"
    )

    for gl in range(1, 4):                                  # faint altitude gridlines
        gy = bottom - (bottom - top) * gl / 4
        parts.append(f'<line x1="{left}" y1="{gy:.1f}" x2="{right}" y2="{gy:.1f}" stroke="{MUTED}" '
                     f'stroke-opacity="0.10" stroke-dasharray="2 6"/>')
    parts.append(f'<line x1="{left}" y1="{bottom}" x2="{right}" y2="{bottom}" stroke="{C_VIOLET}" stroke-opacity="0.35"/>')
    parts.append(f'<linearGradient id="area{uid}" x1="0" y1="0" x2="0" y2="1">'
                 f'<stop offset="0%" stop-color="{C_VIOLET}" stop-opacity="0.55"/>'
                 f'<stop offset="100%" stop-color="{C_VIOLET}" stop-opacity="0.02"/></linearGradient>')
    parts.append(f'<path d="{area}" fill="url(#area{uid})"/>')
    parts.append(f'<polyline points="{line}" fill="none" stroke="url(#stroke{uid})" stroke-width="2.6" '
                 f'stroke-linecap="round" stroke-linejoin="round" filter="url(#glow{uid})"/>')
    for i, (x, y) in enumerate(pts):
        parts.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="2.6" fill="{C_CYAN}">'
                     f'<animate attributeName="opacity" values="1;0.45;1" dur="{3 + (i % 5) * 0.4:.1f}s" '
                     f'repeatCount="indefinite"/></circle>')

    # a probe flying the trajectory, nose pointed along the path
    parts.append(f'<g opacity="0.95"><path d="M-6,-3.4 L7,0 L-6,3.4 L-3.2,0 Z" fill="#ffffff"/>'
                 f'<circle cx="-7.5" cy="0" r="2.6" fill="{C_PINK}" fill-opacity="0.55">'
                 f'<animate attributeName="r" values="2.6;4.4;2.6" dur="1.1s" repeatCount="indefinite"/></circle>'
                 f'<animateMotion path="{motion}" dur="14s" rotate="auto" repeatCount="indefinite"/></g>')

    peak_i = counts.index(peak)
    peak_x, peak_y = px(peak_i), py(peak)
    if peak_x > right - 90:          # drop below the point so it clears the Σ summary
        lx, ly, anchor = peak_x - 9, peak_y + 16, "end"
    else:
        lx, ly, anchor = peak_x, peak_y - 10, "middle"
    parts.append(f'<text x="{lx:.1f}" y="{ly:.1f}" text-anchor="{anchor}" font-family="{MONO}" '
                 f'font-size="10" font-weight="700" fill="{C_CYAN}">{peak}</text>')
    for i, anchor in ((0, "start"), (n // 2, "middle"), (n - 1, "end")):
        parts.append(f'<text x="{px(i):.1f}" y="{h-22}" text-anchor="{anchor}" font-family="{MONO}" '
                     f'font-size="9" fill="{MUTED}">{esc(days[i][0][5:])}</text>')
    total = sum(counts)
    parts.append(f'<text x="{right}" y="46" text-anchor="end" font-family="{MONO}" font-size="11" '
                 f'fill="{C_CYAN}">\u03a3 {total} contributions / 30d</text>')
    parts.append(f'<text x="{right}" y="62" text-anchor="end" font-family="{MONO}" font-size="9" '
                 f'fill="{MUTED}">avg {total / n:.1f} per day \u00b7 peak {peak}</text>')

    parts.append("</svg>")
    return "\n  ".join(parts)


def render_header(d):
    """Wide hero banner: nameplate, ringed planet, launching probe, planet limb."""
    w, h, uid = 1000, 330, "h"
    name = (d.get("name") or USER).upper()
    rnd = random.Random("header-constellation")

    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" viewBox="0 0 {w} {h}" '
        f'fill="none" role="img" aria-label="{esc(d.get("name") or USER)} - space banner">',
        f'''<defs>
    <linearGradient id="sky{uid}" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0%" stop-color="#03000d"/><stop offset="50%" stop-color="#0b0224"/><stop offset="100%" stop-color="#05010f"/>
    </linearGradient>
    <radialGradient id="n1{uid}" cx="28%" cy="30%" r="58%">
      <stop offset="0%" stop-color="#7c3aed" stop-opacity="0.50"/>
      <stop offset="55%" stop-color="#4338ca" stop-opacity="0.16"/>
      <stop offset="100%" stop-color="#05010f" stop-opacity="0"/>
    </radialGradient>
    <radialGradient id="n2{uid}" cx="78%" cy="70%" r="52%">
      <stop offset="0%" stop-color="#db2777" stop-opacity="0.34"/>
      <stop offset="100%" stop-color="#05010f" stop-opacity="0"/>
    </radialGradient>
    <radialGradient id="n3{uid}" cx="60%" cy="6%" r="40%">
      <stop offset="0%" stop-color="#0891b2" stop-opacity="0.28"/>
      <stop offset="100%" stop-color="#05010f" stop-opacity="0"/>
    </radialGradient>
    <linearGradient id="ttl{uid}" x1="0" y1="0" x2="1" y2="0">
      <stop offset="0%" stop-color="{C_CYAN}"/><stop offset="45%" stop-color="{C_VIOLET}"/><stop offset="100%" stop-color="{C_PINK}"/>
      <animate attributeName="x1" values="0;-1;0" dur="9s" repeatCount="indefinite"/>
      <animate attributeName="x2" values="1;2;1" dur="9s" repeatCount="indefinite"/>
    </linearGradient>
    <radialGradient id="plt{uid}" cx="34%" cy="28%" r="78%">
      <stop offset="0%" stop-color="#d8b4fe"/><stop offset="48%" stop-color="#7c3aed"/><stop offset="100%" stop-color="#2e1065"/>
    </radialGradient>
    <linearGradient id="limb{uid}" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0%" stop-color="#1e1b4b" stop-opacity="0.95"/><stop offset="100%" stop-color="#05010f"/>
    </linearGradient>
    <linearGradient id="rim{uid}" x1="0" y1="0" x2="1" y2="0">
      <stop offset="0%" stop-color="{C_CYAN}" stop-opacity="0"/><stop offset="50%" stop-color="{C_CYAN}" stop-opacity="0.9"/>
      <stop offset="100%" stop-color="{C_PINK}" stop-opacity="0"/>
    </linearGradient>
    <filter id="soft{uid}" x="-60%" y="-60%" width="220%" height="220%">
      <feGaussianBlur stdDeviation="9" result="b"/><feMerge><feMergeNode in="b"/><feMergeNode in="SourceGraphic"/></feMerge>
    </filter>
    <filter id="tglow{uid}" x="-30%" y="-60%" width="160%" height="240%">
      <feGaussianBlur stdDeviation="5" result="b"/><feMerge><feMergeNode in="b"/><feMergeNode in="SourceGraphic"/></feMerge>
    </filter>
  </defs>''',
        f'<rect width="{w}" height="{h}" fill="url(#sky{uid})"/>',
        f'<rect width="{w}" height="{h}" fill="url(#n1{uid})">'
        f'<animate attributeName="opacity" values="1;0.65;1" dur="13s" repeatCount="indefinite"/></rect>',
        f'<rect width="{w}" height="{h}" fill="url(#n2{uid})">'
        f'<animate attributeName="opacity" values="0.7;1;0.7" dur="17s" repeatCount="indefinite"/></rect>',
        f'<rect width="{w}" height="{h}" fill="url(#n3{uid})"/>',
        starfield(w, h, 150, "header"),
        comets(w, h, uid, n=3, seed=99),
    ]

    # constellation: joined stars in the upper left quadrant
    nodes = [(round(rnd.uniform(40, 320), 1), round(rnd.uniform(36, 150), 1)) for _ in range(6)]
    seg = " ".join(f"{x},{y}" for x, y in nodes)
    parts.append(f'<polyline points="{seg}" fill="none" stroke="{C_CYAN}" stroke-opacity="0.28" stroke-width="1"/>')
    for i, (x, y) in enumerate(nodes):
        parts.append(f'<circle cx="{x}" cy="{y}" r="2.2" fill="{C_CYAN}" opacity="0.85">'
                     f'<animate attributeName="opacity" values="0.85;0.3;0.85" dur="{3 + i * 0.6}s" '
                     f'repeatCount="indefinite"/></circle>')

    # ringed planet with an orbiting moon
    pcx, pcy, pr = 826, 100, 48
    orb = "M-88,0 a88,25 0 1,0 176,0 a88,25 0 1,0 -176,0"
    parts.append(
        f'<g transform="translate({pcx} {pcy})">'
        f'<circle r="{pr + 26}" fill="{C_VIOLET}" fill-opacity="0.12" filter="url(#soft{uid})"/>'
        f'<g transform="rotate(-20)">'
        f'<path d="{orb}" fill="none" stroke="{C_PINK}" stroke-opacity="0.55" stroke-width="3"/>'
        f'<circle r="{pr}" fill="url(#plt{uid})"/>'
        f'<path d="M-88,0 a88,25 0 0,0 176,0" fill="none" stroke="{C_PINK}" stroke-opacity="0.85" stroke-width="3"/>'
        f'<circle r="5" fill="#e0f2fe"><animateMotion path="{orb}" dur="12s" repeatCount="indefinite"/></circle>'
        f'</g>'
        f'<circle cx="-16" cy="-14" r="8" fill="#ffffff" fill-opacity="0.13"/>'
        f'<circle cx="14" cy="16" r="12" fill="#1e1b4b" fill-opacity="0.35"/>'
        f'<animateTransform attributeName="transform" type="translate" '
        f'values="{pcx} {pcy};{pcx} {pcy - 7};{pcx} {pcy}" dur="8s" repeatCount="indefinite"/></g>'
    )

    # probe climbing out of the limb on the left
    parts.append(
        f'<g transform="translate(112 214) rotate(-32)">'
        f'<path d="M0,-18 C7,-8 8,6 0,17 C-8,6 -7,-8 0,-18 Z" fill="#e6e9ff"/>'
        f'<path d="M0,-18 C5,-10 6,-2 5,4 L-5,4 C-6,-2 -5,-10 0,-18 Z" fill="{C_CYAN}" fill-opacity="0.55"/>'
        f'<path d="M-5,6 L-13,20 L-4,15 Z" fill="{C_PINK}"/><path d="M5,6 L13,20 L4,15 Z" fill="{C_PINK}"/>'
        f'<circle cy="-6" r="3.4" fill="{C_VIOLET}"/>'
        f'<path d="M0,30 C-5,22 -3,20 0,17 C3,20 5,22 0,30 Z" fill="{C_GOLD}">'
        f'<animate attributeName="opacity" values="1;0.45;1" dur="0.45s" repeatCount="indefinite"/>'
        f'<animateTransform attributeName="transform" type="scale" values="1 1;1 1.5;1 1" '
        f'dur="0.45s" repeatCount="indefinite"/></path>'
        f'<animateTransform attributeName="transform" type="translate" values="0 0;0 -9;0 0" '
        f'dur="5s" repeatCount="indefinite" additive="sum"/></g>'
    )

    # planet limb across the bottom
    parts.append(f'<path d="M-80,{h + 10} Q{w / 2},252 {w + 80},{h + 10} Z" fill="url(#limb{uid})"/>')
    parts.append(f'<path d="M-80,{h + 10} Q{w / 2},252 {w + 80},{h + 10}" fill="none" stroke="url(#rim{uid})" '
                 f'stroke-width="2" filter="url(#soft{uid})"/>')

    # nameplate
    cx = w / 2
    parts.append(f'<text x="{cx}" y="172" text-anchor="middle" font-family="{FONT}" font-size="66" '
                 f'font-weight="900" letter-spacing="9" fill="url(#ttl{uid})" filter="url(#tglow{uid})">{esc(name)}</text>')
    parts.append(f'<text x="{cx}" y="206" text-anchor="middle" font-family="{MONO}" font-size="15.5" '
                 f'fill="{TEXT}" letter-spacing="1.4">&lt;/&gt; building clean software across the digital galaxy</text>')

    chips = ["NEW YORK METRO SECTOR", "OPEN TO COLLABORATE", "EST. EARTH"]
    cw = 176
    total = len(chips) * cw + (len(chips) - 1) * 14
    x0 = cx - total / 2
    for i, chip in enumerate(chips):
        x = x0 + i * (cw + 14)
        col = (C_CYAN, C_VIOLET, C_PINK)[i]
        parts.append(f'<rect x="{x:.1f}" y="228" width="{cw}" height="27" rx="13.5" fill="#0b1021" '
                     f'fill-opacity="0.72" stroke="{col}" stroke-opacity="0.5"/>')
        parts.append(f'<circle cx="{x + 16:.1f}" cy="241.5" r="3" fill="{col}">'
                     f'<animate attributeName="opacity" values="1;0.35;1" dur="{2.4 + i * 0.5}s" '
                     f'repeatCount="indefinite"/></circle>')
        parts.append(f'<text x="{x + cw / 2 + 8:.1f}" y="246" text-anchor="middle" font-family="{MONO}" '
                     f'font-size="9.5" fill="{TEXT}" letter-spacing="0.8">{esc(chip)}</text>')

    parts.append("</svg>")
    return "\n".join(parts)


GITHUB_MARK = (
    "M12 .297c-6.63 0-12 5.373-12 12 0 5.303 3.438 9.8 8.205 11.385.6.113.82-.258.82-.577 "
    "0-.285-.01-1.04-.015-2.04-3.338.724-4.042-1.61-4.042-1.61C4.422 18.07 3.633 17.7 3.633 "
    "17.7c-1.087-.744.084-.729.084-.729 1.205.084 1.838 1.236 1.838 1.236 1.07 1.835 2.809 "
    "1.305 3.495.998.108-.776.417-1.305.76-1.605-2.665-.3-5.466-1.332-5.466-5.93 0-1.31.465-"
    "2.38 1.235-3.22-.135-.303-.54-1.523.105-3.176 0 0 1.005-.322 3.3 1.23.96-.267 1.98-.399 "
    "3-.405 1.02.006 2.04.138 3 .405 2.28-1.552 3.285-1.23 3.285-1.23.645 1.653.24 2.873.12 "
    "3.176.765.84 1.23 1.91 1.23 3.22 0 4.61-2.805 5.625-5.475 5.92.42.36.81 1.096.81 2.22 0 "
    "1.606-.015 2.896-.015 3.286 0 .315.21.69.825.57C20.565 22.092 24 17.592 24 12.297c0-6.627-"
    "5.373-12-12-12"
)


def render_followers(d):
    """Self-hosted 'for-the-badge' follower count, so the number comes from the
    same API call as the cards instead of a third-party service."""
    label, value = "CREW MEMBERS", commas(d["followers"])
    h, uid = 28, "b"
    text_x = 30                                # left edge of the label, clear of the logo
    lw = text_x + len(label) * 8.3 + 12        # uppercase advance + right padding
    rw = 24 + len(value) * 9.0
    w = lw + rw
    return "\n".join([
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{w:.0f}" height="{h}" '
        f'viewBox="0 0 {w:.0f} {h}" role="img" aria-label="{label}: {value}">',
        f'<clipPath id="r{uid}"><rect width="{w:.0f}" height="{h}" rx="4"/></clipPath>',
        f'<g clip-path="url(#r{uid})">',
        f'<rect width="{lw:.0f}" height="{h}" fill="{BG_A}"/>',
        f'<rect x="{lw:.0f}" width="{rw:.0f}" height="{h}" fill="{C_PINK}"/>',
        f'<g transform="translate(9 7) scale(0.583)"><path d="{GITHUB_MARK}" fill="#ffffff"/></g>',
        f'<text x="{(text_x + lw - 12) / 2:.0f}" y="18.5" text-anchor="middle" '
        f'font-family="Verdana, DejaVu Sans, sans-serif" font-size="10" font-weight="bold" '
        f'letter-spacing="1.1" fill="#ffffff">{esc(label)}</text>',
        f'<text x="{lw + rw / 2:.0f}" y="18.5" text-anchor="middle" '
        f'font-family="Verdana, DejaVu Sans, sans-serif" font-size="10" font-weight="bold" '
        f'letter-spacing="1.1" fill="{BG_A}">{esc(value)}</text>',
        "</g></svg>",
    ])


def render_typing():
    lines = [
        "Full-Stack Developer + UI/UX Designer",
        "Machine Learning \u2022 GANs \u2022 Distributed Systems",
        "Exploring the universe, one commit at a time",
        "Space enthusiast who codes among the stars",
    ]
    w, h, uid = 720, 54, "y"
    fs, cw = 21, 12.7          # font size + approx monospace advance width
    slot, type_t, erase_t = 3.6, 1.3, 0.5
    n = len(lines)
    cycle = n * slot
    baseline = 34

    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" viewBox="0 0 {w} {h}" fill="none" role="img">',
        f'<defs><linearGradient id="tg{uid}" x1="0" y1="0" x2="1" y2="0">'
        f'<stop offset="0%" stop-color="{C_CYAN}"/><stop offset="50%" stop-color="{C_VIOLET}"/>'
        f'<stop offset="100%" stop-color="{C_PINK}"/></linearGradient></defs>',
    ]

    # Reveal is done with nested <svg> viewports rather than an animated <clipPath>:
    # WebKit never animates elements inside a <clipPath> (they are not rendered).
    font = "'Fira Code', ui-monospace, SFMono-Regular, Menlo, Consolas, monospace"
    cur_t, cur_x = [], []
    for i, text in enumerate(lines):
        chars = len(text)
        pw = chars * cw
        x0 = (w - pw) / 2
        st, sv = [], []
        for j in range(chars + 1):                      # type in, one glyph per step
            _kf(st, sv, (i * slot + j / chars * type_t) / cycle, j * cw)
        for j in range(chars, -1, -1):                  # erase, one glyph per step
            _kf(st, sv, ((i + 1) * slot - erase_t + (chars - j) / chars * erase_t) / cycle, j * cw)
        kt, vals = [0.0], [0.0]
        for t, v in zip(st, sv):
            _kf(kt, vals, t, v)
        _kf(kt, vals, 1, 0)
        kts = ";".join(f"{v:.4f}" for v in kt)
        # slack on the fully-typed frame: the last glyph's ink can overhang its advance width
        wvals = ";".join(f"{(v + cw if v >= pw - 1e-6 else v):.1f}" for v in vals)
        parts.append(
            f'<svg x="{x0:.1f}" y="0" width="0" height="{h}" overflow="hidden">'
            f'<text x="0" y="{baseline}" font-family="{font}" font-size="{fs}" font-weight="600" '
            f'textLength="{pw:.1f}" lengthAdjust="spacing" '
            f'fill="url(#tg{uid})">{esc(text)}</text>'
            f'<animate attributeName="width" values="{wvals}" keyTimes="{kts}" '
            f'calcMode="discrete" dur="{cycle}s" repeatCount="indefinite"/></svg>'
        )
        for t, v in zip(st, sv):
            _kf(cur_t, cur_x, t, x0 + v)

    _kf(cur_t, cur_x, 1, cur_x[0])
    ckt = ";".join(f"{v:.4f}" for v in cur_t)
    cxs = ";".join(f"{v:.1f}" for v in cur_x)
    parts.append(
        f'<rect x="{cur_x[0]:.1f}" y="{baseline-16}" width="2.5" height="20" fill="{C_PINK}">'
        f'<animate attributeName="x" values="{cxs}" keyTimes="{ckt}" calcMode="discrete" dur="{cycle}s" repeatCount="indefinite"/>'
        f'<animate attributeName="opacity" values="1;1;0;1" keyTimes="0;0.5;0.75;1" dur="0.9s" repeatCount="indefinite"/></rect>'
    )
    parts.append("</svg>")
    return "\n".join(parts)


def render_footer():
    w, h, uid = 1200, 150, "f"
    wave1 = "M0,52 C200,22 400,82 600,52 C800,22 1000,82 1200,52 L1200,150 L0,150 Z"
    wave2 = "M0,52 C200,82 400,22 600,52 C800,82 1000,22 1200,52 L1200,150 L0,150 Z"
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" viewBox="0 0 {w} {h}" fill="none" role="img">',
        f'<defs><linearGradient id="fg{uid}" x1="0" y1="0" x2="1" y2="0">'
        f'<stop offset="0%" stop-color="{C_CYAN}"/><stop offset="50%" stop-color="{C_VIOLET}"/>'
        f'<stop offset="100%" stop-color="{C_PINK}"/></linearGradient>'
        f'<radialGradient id="fn{uid}" cx="50%" cy="10%" r="70%">'
        f'<stop offset="0%" stop-color="#6d28d9" stop-opacity="0.35"/>'
        f'<stop offset="100%" stop-color="{DEEP}" stop-opacity="0"/></radialGradient></defs>',
        f'<rect width="{w}" height="{h}" fill="{DEEP}"/>',
        f'<rect width="{w}" height="52" fill="url(#fn{uid})"/>',
        starfield(w, 52, 64, "footer", pad=3),
        comets(w, 52, uid, n=2, seed=21),
        f'<path fill="url(#fg{uid})" fill-opacity="0.92" d="{wave1}">'
        f'<animate attributeName="d" values="{wave1};{wave2};{wave1}" dur="6s" repeatCount="indefinite"/></path>',
        f'<text x="{w/2:.0f}" y="108" text-anchor="middle" font-family="{FONT}" font-size="26" '
        f'font-weight="800" fill="#ffffff">Ad Astra \u2014 to the stars</text>',
        f'<text x="{w/2:.0f}" y="132" text-anchor="middle" font-family="{MONO}" font-size="13" '
        f'fill="#eef1ff" fill-opacity="0.85">Thanks for drifting by the flight deck</text>',
        "</svg>",
    ]
    return "\n".join(parts)


MISSION_START, MISSION_END = "<!-- MISSIONS:START -->", "<!-- MISSIONS:END -->"


def update_missions(d, path="README.md"):
    """Rewrite the Featured Missions table from live repo data, so new repos
    show up on their own. No-op if the markers are absent."""
    missions = d.get("missions") or []
    if not missions or not os.path.isfile(path):
        return
    with open(path, encoding="utf-8") as fh:
        readme = fh.read()
    if MISSION_START not in readme or MISSION_END not in readme:
        print("::warning::mission markers not found in README.md")
        return

    rows = []
    for m in missions:
        desc = esc(m["desc"])
        if len(desc) > 190:
            desc = desc[:190].rsplit(" ", 1)[0] + "\u2026"
        payload = "&nbsp;\u00b7&nbsp;".join(esc(l) for l in m["langs"]) or "\u2014"
        star = f' <sub>\u2605{m["stars"]}</sub>' if m["stars"] else ""
        rows.append(
            "    <tr>\n"
            f'      <td><a href="{esc(m["url"])}"><b>{esc(m["name"])}</b></a>{star}</td>\n'
            f"      <td>{desc}</td>\n"
            f"      <td><sub><code>{payload}</code></sub></td>\n"
            "    </tr>"
        )

    block = "\n".join([
        MISSION_START,
        '<table width="100%">',
        "  <thead>",
        "    <tr>",
        '      <th align="left" width="23%">\U0001F6F0\uFE0F System</th>',
        '      <th align="left" width="52%">Mission Briefing</th>',
        '      <th align="left" width="25%">Payload</th>',
        "    </tr>",
        "  </thead>",
        "  <tbody>",
        *rows,
        "  </tbody>",
        "</table>",
        MISSION_END,
    ])

    updated = re.sub(re.escape(MISSION_START) + r".*?" + re.escape(MISSION_END),
                     lambda _m: block, readme, flags=re.DOTALL)
    if updated != readme:
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(updated)
        print(f"Featured Missions refreshed ({len(missions)} repos).")


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
        "streak.svg": render_streak(data),
        "activity.svg": render_activity(data),
        "followers.svg": render_followers(data),
        "typing.svg": render_typing(),
        "footer.svg": render_footer(),
    }
    no_calendar = not data.get("contributions")
    for name, svg in cards.items():
        path = os.path.join(OUT_DIR, name)
        # Never downgrade a good card to a "syncing" placeholder.
        if no_calendar and name in ("streak.svg", "activity.svg") and os.path.exists(path):
            print(f"::warning::no contribution data this run, keeping last-good {name}")
            continue
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(svg + "\n")
    with open(os.path.join("assets", "header.svg"), "w", encoding="utf-8") as fh:
        fh.write(render_header(data) + "\n")
    update_missions(data)
    streak = data.get("streaks", {})
    print(f"Rendered {len(cards)} cards for @{USER}: "
          f"{data['stars']}\u2605 {data['repos']} repos {data['followers']} followers, "
          f"{len(data['langs'])} languages, "
          f"{streak.get('total', 0)} contributions "
          f"(current streak {streak.get('current', 0)}, longest {streak.get('longest', 0)}).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
