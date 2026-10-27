"""Generate static/img/hero-network.svg - the landing-page "knowledge graph" artwork.

Run from the project folder:  python tools/make_hero_svg.py
Deterministic (fixed seed), self-contained SVG: no raster images, no text, no external references.
"""
import math
import random
from pathlib import Path

random.seed(11)
W, H = 1600, 640
CYAN, SKY, ICE, AMBER = "#38BDF8", "#7DD3FC", "#E0F2FE", "#F5A623"


def scatter(n, min_gap, xr=(20, W - 20), yr=(20, H - 20), bias=None):
    pts = []
    tries = 0
    while len(pts) < n and tries < 20000:
        tries += 1
        x, y = random.uniform(*xr), random.uniform(*yr)
        if bias and random.random() > bias(x, y):
            continue
        if all((x - a) ** 2 + (y - b) ** 2 > min_gap ** 2 for a, b in pts):
            pts.append((round(x), round(y)))
    return pts


def edges(pts, k, max_len):
    out = set()
    for i, (x, y) in enumerate(pts):
        near = sorted(range(len(pts)), key=lambda j: (pts[j][0] - x) ** 2 + (pts[j][1] - y) ** 2)[1:k + 1]
        for j in near:
            if math.dist(pts[i], pts[j]) <= max_len:
                out.add(tuple(sorted((i, j))))
    return out


# denser toward the left/right edges and the bottom so the centre (headline) stays calm
edge_bias = lambda x, y: 0.35 + 0.65 * abs(x - W / 2) / (W / 2)
far = scatter(70, 48, bias=lambda x, y: 0.8)
near = scatter(58, 92, bias=edge_bias)
hubs = sorted(range(len(near)), key=lambda i: -abs(near[i][0] - W / 2))[:11]
amber_nodes = set(random.sample([i for i in hubs], 3))
near_edges = edges(near, 3, 230)
far_edges = edges(far, 2, 140)

o = []
a = o.append
a(f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" preserveAspectRatio="xMidYMid slice" role="presentation" aria-hidden="true">')
a("<defs>"
  '<radialGradient id="bg" cx=".72" cy=".25" r="1.05"><stop offset="0" stop-color="#22324A"/><stop offset=".5" stop-color="#16213A"/><stop offset="1" stop-color="#0B1220"/></radialGradient>'
  f'<radialGradient id="gC" cx=".5" cy=".5" r=".5"><stop offset="0" stop-color="{CYAN}" stop-opacity=".75"/><stop offset=".45" stop-color="{CYAN}" stop-opacity=".22"/><stop offset="1" stop-color="{CYAN}" stop-opacity="0"/></radialGradient>'
  f'<radialGradient id="gA" cx=".5" cy=".5" r=".5"><stop offset="0" stop-color="{AMBER}" stop-opacity=".8"/><stop offset=".5" stop-color="{AMBER}" stop-opacity=".2"/><stop offset="1" stop-color="{AMBER}" stop-opacity="0"/></radialGradient>'
  f'<linearGradient id="flow" x1="0" y1="0" x2="1" y2="0"><stop offset="0" stop-color="{CYAN}" stop-opacity="0"/><stop offset=".5" stop-color="{SKY}" stop-opacity=".9"/><stop offset="1" stop-color="{CYAN}" stop-opacity="0"/></linearGradient>'
  '<linearGradient id="sheen" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#fff" stop-opacity=".13"/><stop offset=".45" stop-color="#fff" stop-opacity=".03"/><stop offset="1" stop-color="#fff" stop-opacity="0"/></linearGradient>'
  '<linearGradient id="rim" x1="0" y1="0" x2="1" y2="0"><stop offset="0" stop-color="#fff" stop-opacity="0"/><stop offset=".5" stop-color="#BAE6FD" stop-opacity=".55"/><stop offset="1" stop-color="#fff" stop-opacity="0"/></linearGradient>'
  f'<pattern id="hex" width="56" height="97" patternUnits="userSpaceOnUse" patternTransform="scale(.9)"><path d="M28 0l28 16v32L28 64 0 48V16zM28 64v33M0 48L-28 64M56 48l28 16" fill="none" stroke="{SKY}" stroke-opacity=".07"/></pattern>'
  '<filter id="soft" x="-5%" y="-5%" width="110%" height="110%"><feGaussianBlur stdDeviation="1.4"/></filter>'
  "</defs>")
a(f'<rect width="{W}" height="{H}" fill="url(#bg)"/><rect width="{W}" height="{H}" fill="url(#hex)"/>')
a(f'<circle cx="1280" cy="170" r="380" fill="url(#gC)" opacity=".5"/><circle cx="230" cy="470" r="330" fill="url(#gC)" opacity=".38"/>')

# depth: far layer, dimmer and slightly blurred
a(f'<g filter="url(#soft)" stroke="{SKY}" stroke-opacity=".22" stroke-width="1">')
for i, j in sorted(far_edges):
    a(f'<line x1="{far[i][0]}" y1="{far[i][1]}" x2="{far[j][0]}" y2="{far[j][1]}"/>')
a("</g>")
a(f'<g fill="{SKY}" fill-opacity=".45" filter="url(#soft)">' + "".join(f'<circle cx="{x}" cy="{y}" r="2"/>' for x, y in far) + "</g>")

# luminous flow curves
for k, (y0, y1) in enumerate([(430, 120), (150, 470), (560, 330)]):
    d = f"M-40 {y0} C 420 {y0 - 220}, 1000 {y1 + 240}, 1640 {y1}"
    a(f'<path d="{d}" fill="none" stroke="url(#flow)" stroke-width="10" stroke-opacity=".12"/><path d="{d}" fill="none" stroke="url(#flow)" stroke-width="{1.6 + .3 * k:.1f}"/>')

# near layer edges: brighter when touching a hub
hub_set = set(hubs)
a('<g stroke-linecap="round">')
for i, j in sorted(near_edges):
    strong = i in hub_set or j in hub_set
    a(f'<line x1="{near[i][0]}" y1="{near[i][1]}" x2="{near[j][0]}" y2="{near[j][1]}" stroke="{SKY if strong else CYAN}" stroke-opacity="{.8 if strong else .5}" stroke-width="{1.7 if strong else 1.2}"/>')
a("</g>")

# nodes with glow
for i, (x, y) in enumerate(near):
    if i in hub_set:
        amber = i in amber_nodes
        col, glow = (AMBER, "gA") if amber else (ICE, "gC")
        a(f'<circle cx="{x}" cy="{y}" r="38" fill="url(#{glow})"/><circle cx="{x}" cy="{y}" r="12" fill="none" stroke="{col}" stroke-opacity=".75" stroke-width="1.4"/><circle cx="{x}" cy="{y}" r="5.5" fill="{col}"/>')
    else:
        r = random.uniform(2.6, 4.2)
        a(f'<circle cx="{x}" cy="{y}" r="16" fill="url(#gC)" opacity=".55"/><circle cx="{x}" cy="{y}" r="{r:.1f}" fill="{ICE if r > 3.4 else SKY}"/>')

# glass sheen + rim light
a(f'<path d="M0 0H{W * .62:.0f}L{W * .30:.0f} {H}H0Z" fill="url(#sheen)"/><path d="M{W * .70:.0f} 0H{W * .80:.0f}L{W * .52:.0f} {H}H{W * .42:.0f}Z" fill="url(#sheen)" opacity=".55"/>')
a(f'<rect x="0" y="0" width="{W}" height="2" fill="url(#rim)"/>')
a("</svg>")
out = Path(__file__).resolve().parent.parent / "static" / "img" / "hero-network.svg"
out.write_text("".join(o), encoding="utf-8")
print(out, out.stat().st_size, "bytes;", len(near), "near nodes,", len(far), "far nodes")
