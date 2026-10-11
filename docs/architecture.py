"""Draws docs/architecture.png (1600x900, the app's dark palette). Needs matplotlib: python docs/architecture.py"""
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import font_manager
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch

# Colours from frontend/src/tokens.css
BG, PANEL, LINE = "#07090f", "#0d1018", "#2a2e38"
TEXT, TEXT2, TEXT3 = "#e8ebf2", "#9aa1b2", "#5f6677"
ACCENT, AMBER, GREEN = "#3b82ff", "#ffb020", "#2fd480"


def font(*names: str) -> list[str]:
    """First installed font family from names, with DejaVu as the fallback for glyphs it lacks (→)."""
    have = {f.name for f in font_manager.fontManager.ttflist}
    return [next((n for n in names if n in have), "DejaVu Sans"), "DejaVu Sans"]


UI = font("Inter", "Arial", "DejaVu Sans")  # Helvetica Neue ships without a bold matplotlib can load
MONO = font("JetBrains Mono", "Menlo", "DejaVu Sans Mono")

fig = plt.figure(figsize=(16, 9), dpi=100, facecolor=BG)
ax = fig.add_axes([0, 0, 1, 1])
ax.set_xlim(0, 1600)
ax.set_ylim(900, 0)  # y grows downward, like the screen
ax.axis("off")


def box(x, y, w, h, title, color, lines=(), tag=None, mono=()):
    """A rounded panel: coloured dot and title, body lines, optional mono lines and a tag pill at the bottom."""
    ax.add_patch(FancyBboxPatch((x, y), w, h, boxstyle="round,pad=0,rounding_size=14",
                                fc=PANEL, ec=LINE, lw=1.2))
    ax.add_patch(plt.Circle((x + 24, y + 30), 5, color=color))
    ax.text(x + 38, y + 30, title, color=TEXT, fontsize=17, fontweight="bold", family=UI, va="center")
    ty = y + 68
    for line in lines:
        ax.text(x + 22, ty, line, color=TEXT2, fontsize=12.5, family=UI, va="center")
        ty += 26
    for line in mono:
        ax.text(x + 22, ty, line, color=TEXT3, fontsize=11, family=MONO, va="center")
        ty += 22
    if tag:
        ax.text(x + 22, y + h - 24, tag, color=color, fontsize=11, family=MONO, va="center",
                bbox=dict(boxstyle="round,pad=0.45,rounding_size=0.6", fc=BG, ec=color, lw=0.9))


def arrow(a, b, label=None, at=None, both=False, color=TEXT2, ha="center"):
    """An arrow from a to b, with an optional label at `at` (defaults to the midpoint)."""
    ax.add_patch(FancyArrowPatch(a, b, arrowstyle="<|-|>" if both else "-|>", mutation_scale=16,
                                 color=color, lw=1.6, shrinkA=0, shrinkB=0))
    if label:
        lx, ly = at or ((a[0] + b[0]) / 2, (a[1] + b[1]) / 2)
        ax.text(lx, ly, label, color=TEXT2, fontsize=11, family=MONO, ha=ha, va="center", linespacing=1.5)


# Title
def after(t, gap=0.0):
    """The x (data units) just past text t's right edge, plus gap."""
    return ax.transData.inverted().transform((t.get_window_extent(fig.canvas.get_renderer()).x1, 0))[0] + gap


t = ax.text(50, 62, "Agnt", color=TEXT, fontsize=28, fontweight="bold", family=UI, va="center")
t = ax.text(after(t), 62, "911", color=ACCENT, fontsize=28, fontweight="bold", family=UI, va="center")
ax.text(after(t, 14), 63, "architecture", color=TEXT2, fontsize=22, family=UI, va="center")
ax.text(50, 104, "Emergency vehicles that dispatch themselves: seven agents negotiate every 911 call.",
        color=TEXT3, fontsize=13, family=UI, va="center")

# Top row: Judge -> Engine <-> Protocol <-> Trust
TOP, H = 170, 300
box(50, TOP, 250, H, "Judge", AMBER,
    ["Hijack a vehicle", "Drop a call on the map", "Voice call (ElevenLabs)"],
    tag="the human in the loop")
box(440, TOP, 340, H, "FastAPI Engine", TEXT2,
    ["World state + tick loop", "Message bus, HMAC-signed", "JSONL log per run"],
    mono=["POST /inject  /incident", "POST /resolve  /mode", "GET  /state"],
    tag="one process, in memory")
box(900, TOP, 300, H, "Protocol", ACCENT,
    ["propose → counter →", "vote → resolve", "Sonnet round 1,", "Haiku after that"],
    tag="up to 4 rounds per call")
box(1320, TOP, 230, H, "Trust layer", GREEN,
    ["Check claims", "Trust scores", "Vote weights", "Escalation"],
    tag="pure Python · no LLM")

# Bottom row: callout, Frontend, agents
BOT, BH = 600, 220
ax.add_patch(FancyBboxPatch((50, BOT), 250, BH, boxstyle="round,pad=0,rounding_size=14",
                            fc=(59 / 255, 130 / 255, 1, 0.08), ec=ACCENT, lw=1.4, ls=(0, (5, 4))))
ax.text(175, BOT + 88, "No orchestrator.", color=TEXT, fontsize=19, fontweight="bold", family=UI,
        ha="center", va="center")
ax.text(175, BOT + 134, "No node decides\nfor the others.", color=TEXT2, fontsize=15, family=UI,
        ha="center", va="center", linespacing=1.5)

box(440, BOT, 340, BH, "Frontend", TEXT2,
    ["React + Mapbox GL", "Polls /state every 500 ms", "Animates the typed events"],
    tag="what the judge watches")

box(900, BOT, 650, BH, "Seven vehicle agents", ACCENT, ["Each sees 500 m and the shared log, bids, claims and votes."])
for i, (vid, kind) in enumerate([("A1", "ambulance"), ("A2", "ambulance"), ("A3", "ambulance"),
                                 ("F1", "fire"), ("F2", "fire"), ("P1", "police"), ("P2", "police")]):
    cx = 922 + i * 89
    ax.add_patch(FancyBboxPatch((cx, BOT + 110), 76, 70, boxstyle="round,pad=0,rounding_size=10",
                                fc=BG, ec=ACCENT, lw=1.2))
    ax.text(cx + 38, BOT + 136, vid, color=TEXT, fontsize=15, fontweight="bold", family=MONO, ha="center", va="center")
    ax.text(cx + 38, BOT + 160, kind, color=TEXT3, fontsize=9.5, family=UI, ha="center", va="center")

# Arrows
mid = TOP + H / 2
arrow((300, mid), (440, mid), "endpoints", at=(370, mid - 18), color=AMBER)
arrow((780, mid - 50), (900, mid - 50), "world in", at=(840, mid - 68))
arrow((900, mid + 50), (780, mid + 50), "plans out", at=(840, mid + 32))
arrow((1200, mid - 50), (1320, mid - 50), "claims", at=(1260, mid - 68))
arrow((1320, mid + 50), (1200, mid + 50), "verdicts,\nweights", at=(1260, mid + 82))
arrow((610, TOP + H), (610, BOT), "/state +\ntyped events", at=(626, (TOP + H + BOT) / 2), ha="left")
arrow((1050, BOT), (1050, TOP + H), "signed messages on the bus (HMAC)", at=(1068, (TOP + H + BOT) / 2),
      both=True, color=ACCENT, ha="left")

out = Path(__file__).with_name("architecture.png")
fig.savefig(out, dpi=100, facecolor=BG)
print(out)
