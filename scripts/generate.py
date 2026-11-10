#!/usr/bin/env python3
"""Builds assets/terminal.svg and assets/activity.svg from live GitHub data.

Pure standard library. Runs daily in .github/workflows/update-profile.yml.
  GH_TOKEN   token used for the GraphQL API (PAT with read:user + repo for private data)
  PREVIEW=1  offline run with fake data and no animation, writes to ./preview/
"""
import json, os, sys, datetime as dt, urllib.request, random
from pathlib import Path
from xml.sax.saxutils import escape

LOGIN = "ovitrinker"
ROOT = Path(__file__).resolve().parent.parent
PREVIEW = os.environ.get("PREVIEW") == "1"
OUT = ROOT / ("preview" if PREVIEW else "assets")

# ---- palette: flat, GitHub-dark, one accent -------------------------------
BG, BAR, LINE = "#0d1117", "#161b22", "#30363d"
FG, DIM, MUTE = "#e6edf3", "#8b949e", "#6e7681"
ACC = "#58A6FF"
TIER = ["#3d444d", "#6e7681", "#8b949e", "#b1bac4", "#e6edf3"]
HEAT = ["#161b22", "#17324f", "#1f5a99", "#3b86d6", ACC]
FONT = "ui-monospace,SFMono-Regular,'SF Mono',Menlo,Consolas,'DejaVu Sans Mono','Liberation Mono',monospace"

# ---- data ------------------------------------------------------------------
def gql(query, variables):
    req = urllib.request.Request(
        "https://api.github.com/graphql",
        data=json.dumps({"query": query, "variables": variables}).encode(),
        headers={"Authorization": f"bearer {os.environ['GH_TOKEN']}", "User-Agent": "profile-card"},
    )
    with urllib.request.urlopen(req, timeout=60) as r:
        data = json.load(r)
    if "errors" in data:
        raise RuntimeError(data["errors"])
    return data["data"]

Q_REPOS = """
query($login:String!,$after:String){ user(login:$login){
  followers{totalCount}
  contributionsCollection{contributionYears}
  repositories(first:100, after:$after, ownerAffiliations:OWNER, orderBy:{field:PUSHED_AT,direction:DESC}){
    totalCount pageInfo{hasNextPage endCursor}
    nodes{ name isPrivate isFork stargazerCount
      defaultBranchRef{target{... on Commit{abbreviatedOid messageHeadline committedDate}}}
      languages(first:8, orderBy:{field:SIZE,direction:DESC}){edges{size node{name}}} } } } }"""

def q_year(y, to):
    return (f'y{y}: contributionsCollection(from:"{y}-01-01T00:00:00Z", to:"{to}"){{'
            "totalCommitContributions restrictedContributionsCount "
            "contributionCalendar{weeks{contributionDays{date contributionCount}}}}")

def fetch():
    if PREVIEW:
        random.seed(3)
        today = dt.datetime.now(dt.timezone.utc).date()
        days = {str(today - dt.timedelta(d)): random.choice([0, 0, 0, 1, 2, 3, 5, 9]) for d in range(400)}
        return dict(followers=0, repos=4, stars=1, commits=120, days=days,
                    langs=[("Python", 41.2), ("TypeScript", 22.5), ("Rust", 14.8), ("C++", 9.9), ("Shell", 6.1)],
                    recent=[("a1b2c3d", "2026-10-18", "portfolio", "feat: add contact section", False),
                            ("9f8e7d6", "2026-10-16", "nmap-notes", "docs: scan cheat sheet", False),
                            ("c0ffee1", "2026-10-12", "private", "", True)])
    repos, after, user = [], None, None
    while True:
        user = gql(Q_REPOS, {"login": LOGIN, "after": after})["user"]
        page = user["repositories"]
        repos += page["nodes"]
        if not page["pageInfo"]["hasNextPage"]:
            break
        after = page["pageInfo"]["endCursor"]
    now = dt.datetime.now(dt.timezone.utc)
    years = user["contributionsCollection"]["contributionYears"]
    parts = [q_year(y, now.strftime("%Y-%m-%dT%H:%M:%SZ") if y == now.year else f"{y}-12-31T23:59:59Z") for y in years]
    contrib = gql("query($login:String!){user(login:$login){" + " ".join(parts) + "}}", {"login": LOGIN})["user"]
    commits, days = 0, {}
    for c in contrib.values():
        commits += c["totalCommitContributions"]
        for w in c["contributionCalendar"]["weeks"]:
            for d in w["contributionDays"]:
                days[d["date"]] = d["contributionCount"]
    size = {}
    for r in repos:
        if r["isFork"]:
            continue
        for e in r["languages"]["edges"]:
            size[e["node"]["name"]] = size.get(e["node"]["name"], 0) + e["size"]
    tot = sum(size.values()) or 1
    langs = [(n, 100 * s / tot) for n, s in sorted(size.items(), key=lambda kv: -kv[1])[:5]]
    candidates = []
    for r in repos:
        if r["isFork"] or r["name"].lower() == LOGIN or not r["defaultBranchRef"]:
            continue                     # skip forks and this profile repo (the bot commits here daily)
        t = r["defaultBranchRef"]["target"]
        candidates.append((t["committedDate"], t["abbreviatedOid"], r["name"], t["messageHeadline"], r["isPrivate"]))
    candidates.sort(key=lambda item: item[0], reverse=True)
    recent = [(oid, committed[:10], name, msg, private)
              for committed, oid, name, msg, private in candidates[:5]]
    return dict(followers=user["followers"]["totalCount"], repos=user["repositories"]["totalCount"],
                stars=sum(r["stargazerCount"] for r in repos), commits=commits, days=days,
                langs=langs, recent=recent)

def streak(days):
    d = dt.datetime.now(dt.timezone.utc).date()
    if not days.get(str(d)):
        d -= dt.timedelta(1)             # today may still be empty
    n = 0
    while days.get(str(d)):
        n += 1; d -= dt.timedelta(1)
    return n

# ---- svg helpers -----------------------------------------------------------
class Svg:
    def __init__(self, w, h):
        self.w, self.h, self.defs, self.body, self.n = w, h, [], [], 0
    def anim(self, begin, dur="0.25s"):
        return "" if PREVIEW else f'<animate attributeName="opacity" from="0" to="1" dur="{dur}" begin="{begin:.2f}s" fill="freeze"/>'
    def fade(self, inner, begin, dur="0.25s"):
        attr = "" if PREVIEW else ' opacity="0"'
        return f'<g{attr}>{self.anim(begin, dur)}{inner}</g>'
    def typed(self, x, y, h, width, inner, begin, dur=0.6):
        """left-to-right reveal of a text line (typewriter feel)"""
        if PREVIEW:
            return inner
        self.n += 1
        self.defs.append(f'<clipPath id="t{self.n}"><rect x="{x}" y="{y-h+4}" width="0" height="{h}">'
                         f'<animate attributeName="width" from="0" to="{width}" dur="{dur}s" begin="{begin:.2f}s" fill="freeze"/></rect></clipPath>')
        return f'<g clip-path="url(#t{self.n})">{inner}</g>'
    def cursor(self, x, y, begin):
        blink = '' if PREVIEW else '<animate attributeName="opacity" values="1;0;1" keyTimes="0;0.5;1" calcMode="discrete" dur="1.1s" repeatCount="indefinite"/>'
        return f'<rect x="{x}" y="{y-12}" width="8" height="15" fill="{ACC}">{blink}</rect>'
    def frame(self, title, right):
        self.body.append(
            f'<rect x="0.5" y="0.5" width="{self.w-1}" height="{self.h-1}" rx="10" fill="{BG}" stroke="{LINE}"/>'
            f'<path d="M0.5 36V10.5a10 10 0 0 1 10-10H{self.w-10.5}a10 10 0 0 1 10 10V36z" fill="{BAR}"/>'
            f'<line x1="0" y1="36.5" x2="{self.w}" y2="36.5" stroke="{LINE}"/>'
            f'<text x="20" y="23" font-size="12.5" fill="{DIM}">{escape(title)}</text>'
            f'<text x="{self.w-20}" y="23" font-size="12.5" fill="{MUTE}" text-anchor="end">{escape(right)}</text>')
    def render(self, desc):
        return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{self.w}" height="{self.h}" viewBox="0 0 {self.w} {self.h}" '
                f'font-family="{FONT}" role="img" aria-label="{escape(desc)}"><title>{escape(desc)}</title>'
                f'<defs>{"".join(self.defs)}</defs>{"".join(self.body)}</svg>')

def prompt(x, y, cmd, size=13):
    return (f'<text x="{x}" y="{y}" font-size="{size}" fill="{ACC}">$ <tspan fill="{FG}">{escape(cmd)}</tspan></text>')

def cut(s, n):
    return s if len(s) <= n else s[: n - 1] + "…"

# ---- terminal.svg ------------------------------------------------------------
FAVICON = '<rect x="0" y="0" width="3" height="1" fill="#77563e"/><rect x="3" y="0" width="1" height="1" fill="#654a35"/><rect x="4" y="0" width="3" height="1" fill="#77563e"/><rect x="7" y="0" width="1" height="1" fill="#654a35"/><rect x="0" y="1" width="1" height="1" fill="#ff5014"/><rect x="1" y="1" width="2" height="1" fill="#ff4c0f"/><rect x="3" y="1" width="1" height="1" fill="#ff581f"/><rect x="4" y="1" width="1" height="1" fill="#ff541a"/><rect x="5" y="1" width="1" height="1" fill="#ff4505"/><rect x="6" y="1" width="1" height="1" fill="#ff541a"/><rect x="7" y="1" width="1" height="1" fill="#ff5c24"/><rect x="0" y="2" width="1" height="1" fill="#ff6733"/><rect x="1" y="2" width="1" height="1" fill="#ff5f29"/><rect x="2" y="2" width="1" height="1" fill="#ff5c24"/><rect x="3" y="2" width="1" height="1" fill="#ff4c0f"/><rect x="4" y="2" width="1" height="1" fill="#ff6733"/><rect x="5" y="2" width="1" height="1" fill="#ff5f29"/><rect x="6" y="2" width="1" height="1" fill="#ff6733"/><rect x="7" y="2" width="1" height="1" fill="#ff5c24"/><rect x="0" y="3" width="1" height="1" fill="#654a35"/><rect x="1" y="3" width="3" height="1" fill="#543e2c"/><rect x="4" y="3" width="1" height="1" fill="#654a35"/><rect x="5" y="3" width="3" height="1" fill="#543e2c"/><rect x="0" y="4" width="1" height="1" fill="#77563e"/><rect x="1" y="4" width="2" height="1" fill="#ffffff"/><rect x="3" y="4" width="1" height="1" fill="#654a35"/><rect x="4" y="4" width="1" height="1" fill="#77563e"/><rect x="5" y="4" width="2" height="1" fill="#ffffff"/><rect x="7" y="4" width="1" height="1" fill="#654a35"/><rect x="0" y="5" width="1" height="1" fill="#77563e"/><rect x="1" y="5" width="1" height="1" fill="#ffffff"/><rect x="2" y="5" width="1" height="1" fill="#000000"/><rect x="3" y="5" width="1" height="1" fill="#543e2c"/><rect x="4" y="5" width="1" height="1" fill="#77563e"/><rect x="5" y="5" width="1" height="1" fill="#000000"/><rect x="6" y="5" width="1" height="1" fill="#ffffff"/><rect x="7" y="5" width="1" height="1" fill="#543e2c"/><rect x="0" y="6" width="1" height="1" fill="#77563e"/><rect x="1" y="6" width="2" height="1" fill="#654a35"/><rect x="3" y="6" width="2" height="1" fill="#000000"/><rect x="5" y="6" width="2" height="1" fill="#654a35"/><rect x="7" y="6" width="1" height="1" fill="#543e2c"/><rect x="0" y="7" width="1" height="1" fill="#654a35"/><rect x="1" y="7" width="2" height="1" fill="#543e2c"/><rect x="3" y="7" width="1" height="1" fill="#000000"/><rect x="4" y="7" width="1" height="1" fill="#fb2600"/><rect x="5" y="7" width="3" height="1" fill="#543e2c"/>'
LOGO = [
 " ██████  ██    ██ ██ ",
 "██    ██ ██    ██ ██ ",
 "██    ██ ██    ██ ██ ",
 "██    ██  ██  ██  ██ ",
 "██    ██   ████   ██ ",
 " ██████     ██    ██ ",
]
CUBE = [
 "      ┌──────────┐",
 "     ╱          ╱│",
 "    ┌──────────┐ │",
 "    │  ▓▓  ▓▓  │ │",
 "    │    ▓▓    │ │",
 "    │  ▓▓▓▓▓▓  │╱ ",
 "    └──────────┘  ",
]

def build_terminal(d):
    W = 940
    H = 470
    s = Svg(W, H)
    s.frame("ovitrinker@mainframe: ~", "bash")
    s.body.append(s.typed(20, 66, 18, 300, prompt(20, 66, "fastfetch"), 0.1, 0.7))
    t0 = 0.9
    s.body.append(s.fade(f'<g transform="translate(70 130) scale(20)" shape-rendering="crispEdges">{FAVICON}</g>', t0, "0.6s"))
    s.body.append(s.fade(f'<text x="150" y="320" font-size="13" fill="{MUTE}" text-anchor="middle">Build things. Ship them.</text>', t0 + 0.6))
    IX, FSZ, y, LH2 = 520, 13, 112, 21
    streak_n = streak(d["days"])
    now = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines = [("title", f"{LOGIN}@mainframe"), ("rule", "─" * 36),
             ("kv", "Name", "Andrin"),
             ("kv", "Role", "Konstrukteur EFZ apprentice"),
             ("kv", "Location", "Zürich, Switzerland"),
             ("kv", "Builds", "Minecraft mods & plugins"),
             ("kv", "Also", "Websites, Discord bots"),
             ("kv", "Business", "3DVisionStore (3D printing)"),
             ("kv", "Web", "ovitrinker.ch"), ("gap",),
             ("rule", "─ github · live " + "─" * 20),
             ("kv", "Repos", str(d["repos"])), ("kv", "Commits", f"{d['commits']:,}"), ("kv", "Stars", str(d["stars"])),
             ("kv", "Followers", str(d["followers"])), ("kv", "Streak", f"{streak_n} day{'s' if streak_n != 1 else ''}"),
             ("gap",), ("kv", "Updated", now, True)]
    t = 0.6
    for ln in lines:
        if ln[0] == "gap":
            y += 8; continue
        if ln[0] == "title":
            inner = f'<text x="{IX}" y="{y}" font-size="14.5" font-weight="700" fill="{FG}">{escape(ln[1])}</text>'
        elif ln[0] == "rule":
            inner = f'<text x="{IX}" y="{y}" font-size="{FSZ}" fill="{MUTE}">{escape(ln[1])}</text>'
        else:
            dim = len(ln) > 3
            inner = (f'<text x="{IX}" y="{y}" font-size="{FSZ}" fill="{ACC}">{escape(ln[1])}</text>'
                     f'<text x="{IX+112}" y="{y}" font-size="{FSZ}" fill="{MUTE if dim else FG}">{escape(ln[2])}</text>')
        s.body.append(s.typed(IX, y, 18, W - IX - 20, inner, t, 0.55))
        y += LH2; t += 0.32
    s.body.append(prompt(20, H - 16, ""))
    s.body.append(s.cursor(36, H - 16, 0))
    return s.render(f"{LOGIN}@mainframe fastfetch: {d['repos']} repos, {d['commits']} commits, {d['stars']} stars, "
                    f"{d['followers']} followers, {streak_n} day streak")

# ---- activity.svg ------------------------------------------------------------
def build_activity(d):
    W, H = 940, 560
    s = Svg(W, H)
    s.frame("ovitrinker@mainframe: ~/activity", "live · updated daily")
    t = 0.2

    y = 66
    s.body.append(s.typed(20, y, 18, 320, prompt(20, y, "git log --oneline -5"), t, 0.6)); t += 0.7
    for oid, date, repo, msg, private in d["recent"]:
        y += 22
        oid_t = "private" if private else oid
        repo_t, msg_t = ("private repo", "") if private else (cut(repo, 26), cut(msg, 58))
        inner = (f'<text x="20" y="{y}" font-size="12.5" fill="{ACC}">{escape(oid_t)}</text>'
                 f'<text x="92" y="{y}" font-size="12.5" fill="{MUTE}">{date}</text>'
                 f'<text x="182" y="{y}" font-size="12.5" fill="{FG}">{escape(repo_t)}</text>'
                 f'<text x="400" y="{y}" font-size="12.5" fill="{DIM}">{escape(msg_t)}</text>')
        s.body.append(s.typed(20, y, 17, W - 40, inner, t, 0.4)); t += 0.25

    y = 218
    s.body.append(s.typed(20, y, 18, 320, prompt(20, y, "lang --top 5"), t, 0.5)); t += 0.6
    BX, BW = 150, 330
    for name, pct in d["langs"]:
        y += 24
        w = max(2, BW * pct / 100)
        inner = (f'<text x="20" y="{y}" font-size="12.5" fill="{FG}">{escape(cut(name, 12))}</text>'
                 f'<rect x="{BX}" y="{y-10}" width="{BW}" height="10" fill="{BAR}"/>'
                 f'<rect x="{BX}" y="{y-10}" width="{w:.1f}" height="10" fill="{ACC}"/>'
                 f'<text x="{BX+BW+14}" y="{y}" font-size="12.5" fill="{DIM}">{pct:5.1f}%</text>')
        s.body.append(s.fade(inner, t, "0.4s")); t += 0.15

    y = 380
    s.body.append(s.typed(20, y, 18, 380, prompt(20, y, "contrib --last-year"), t, 0.6)); t += 0.7
    today = dt.datetime.now(dt.timezone.utc).date()
    start = today - dt.timedelta(days=(today.weekday() + 1) % 7) - dt.timedelta(weeks=52)   # Sunday-based weeks
    nz = sorted(v for v in d["days"].values() if v > 0)
    cuts = [nz[int(len(nz) * q)] for q in (0.25, 0.5, 0.75)] if nz else [1, 2, 3]
    lvl = lambda c: 0 if c == 0 else 1 + sum(c > k for k in cuts)
    GX, GY, C, G = 20, y + 26, 12, 3
    total, last_month = 0, None
    for w in range(53):
        cells = []
        for r in range(7):
            day = start + dt.timedelta(weeks=w, days=r)
            if day > today:
                continue
            c = d["days"].get(str(day), 0); total += c
            cells.append(f'<rect x="{GX + w*(C+G)}" y="{GY + r*(C+G)}" width="{C}" height="{C}" fill="{HEAT[lvl(c)]}"/>')
            if r == 0 and day.month != last_month and day.day <= 7:
                cells.append(f'<text x="{GX + w*(C+G)}" y="{GY-8}" font-size="10.5" fill="{MUTE}">{day.strftime("%b")}</text>')
                last_month = day.month
        s.body.append(s.fade("".join(cells), t + w * 0.025, "0.3s"))
    ly = GY + 7 * (C + G) + 18
    legend = f'<text x="20" y="{ly}" font-size="11.5" fill="{MUTE}">{total:,} contributions in the last year</text>'
    lx = W - 20 - (5 * (C + 4)) - 60
    legend += f'<text x="{lx}" y="{ly}" font-size="11.5" fill="{MUTE}">less</text>'
    legend += "".join(f'<rect x="{lx+34+i*(C+4)}" y="{ly-10}" width="{C}" height="{C}" fill="{HEAT[i]}"/>' for i in range(5))
    legend += f'<text x="{lx+34+5*(C+4)+4}" y="{ly}" font-size="11.5" fill="{MUTE}">more</text>'
    s.body.append(s.fade(legend, t + 1.4))
    return s.render(f"GitHub activity: latest commits, top languages and {total} contributions in the last year")

if __name__ == "__main__":
    try:
        data = fetch()
    except Exception as e:                  # keep yesterday's files if the API fails
        print("fetch failed, keeping existing assets:", e, file=sys.stderr); sys.exit(1)
    OUT.mkdir(exist_ok=True)
    (OUT / "terminal.svg").write_text(build_terminal(data), encoding="utf-8")
    (OUT / "activity.svg").write_text(build_activity(data), encoding="utf-8")
    print("ok", {k: v for k, v in data.items() if k not in ("days", "recent")})
