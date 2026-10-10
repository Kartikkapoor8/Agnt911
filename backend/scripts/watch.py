"""Terminal viewer: polls /state every 500 ms and redraws map, fleet and feed.

    python scripts/watch.py                       # http://127.0.0.1:8000
    python scripts/watch.py --url http://127.0.0.1:8000 --interval 0.5

Works against scripts/replay.py or the live server. Needs `rich` (script-only, not a backend dep).
"""

import argparse
import json
import time
import urllib.request

from rich.console import Group
from rich.layout import Layout
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

W, H = 40, 20
TYPE_COLOR = {"ambulance": "white", "fire": "bright_magenta", "police": "bright_blue"}
SEV_COLOR = {1: "green", 2: "green", 3: "yellow", 4: "dark_orange", 5: "bold red"}


def fetch(url: str) -> dict:
    """GET /state as a dict."""
    with urllib.request.urlopen(url + "/state", timeout=2) as resp:
        return json.load(resp)


class Bounds:
    """Lat/lng box that only grows, so the map doesn't jump between frames."""

    def __init__(self):
        self.lat0 = self.lng0 = float("inf")
        self.lat1 = self.lng1 = float("-inf")

    def add(self, loc):
        self.lat0, self.lat1 = min(self.lat0, loc["lat"]), max(self.lat1, loc["lat"])
        self.lng0, self.lng1 = min(self.lng0, loc["lng"]), max(self.lng1, loc["lng"])

    def cell(self, loc):
        dlat = (self.lat1 - self.lat0) or 1e-6
        dlng = (self.lng1 - self.lng0) or 1e-6
        x = round((loc["lng"] - self.lng0) / dlng * (W - 1))
        y = round((self.lat1 - loc["lat"]) / dlat * (H - 1))
        return min(max(x, 0), W - 1), min(max(y, 0), H - 1)


def line(a, b):
    """Grid cells between a and b (Bresenham), endpoints excluded."""
    (x0, y0), (x1, y1) = a, b
    dx, dy, sx, sy = abs(x1 - x0), -abs(y1 - y0), 1 if x0 < x1 else -1, 1 if y0 < y1 else -1
    err, cells = dx + dy, []
    while (x0, y0) != (x1, y1):
        e2 = 2 * err
        if e2 >= dy:
            err += dy
            x0 += sx
        if e2 <= dx:
            err += dx
            y0 += sy
        cells.append((x0, y0))
    return cells[:-1]


def render_map(state: dict, bounds: Bounds) -> Panel:
    """40x20 grid: incident pins, vehicle ids, dotted lines to assigned incidents."""
    vehicles, incidents = state["vehicles"], state["incidents"]
    for v in vehicles.values():
        bounds.add(v["location"])
    for i in incidents.values():
        bounds.add(i["location"])
    grid = [[(" ", "")] * W for _ in range(H)]
    for v in vehicles.values():
        inc = incidents.get(v.get("target_incident") or "")
        if inc:
            style = "red" if v["untrusted"] else "grey50"
            for x, y in line(bounds.cell(v["location"]), bounds.cell(inc["location"])):
                grid[y][x] = ("·", style)
    for i in incidents.values():
        x, y = bounds.cell(i["location"])
        if i["status"] == "resolved":
            grid[y][x] = ("✓", "green")
        else:
            grid[y][x] = ("!", SEV_COLOR.get(i["severity"], "white") + " reverse")
            if x + 1 < W:
                grid[y][x + 1] = (i["id"][-1], SEV_COLOR.get(i["severity"], "white"))
    for v in vehicles.values():
        x, y = bounds.cell(v["location"])
        x = min(x, W - 2)
        style = "bold white on red" if v["untrusted"] else "bold " + TYPE_COLOR.get(v["type"], "white")
        for k, ch in enumerate(v["id"][:2]):
            if x + k < W:
                grid[y][x + k] = (ch, style)
    text = Text()
    for row in grid:
        for ch, style in row:
            text.append(ch, style)
        text.append("\n")
    text.append("! call  ✓ resolved  · route  ", "dim")
    text.append("XX", "bold white on red")
    text.append(" untrusted", "dim")
    return Panel(text, title=f"map  tick {state.get('tick')}", width=W + 4)


def trust_bar(trust: float, width: int = 10) -> Text:
    """Green-to-red bar for a 0..1 trust value."""
    n = round(trust * width)
    color = "green" if trust >= 0.7 else "yellow" if trust >= 0.4 else "red"
    return Text("█" * n, color) + Text("░" * (width - n), "grey30") + Text(f" {trust:.1f}")


def render_fleet(state: dict) -> Panel:
    """id, type, status, trust bar, assignment; compromised tag for the judge."""
    t = Table(expand=True, box=None, pad_edge=False)
    for col in ("id", "type", "status", "trust", "assignment"):
        t.add_column(col)
    compromised = set(state.get("compromised", []))
    labels = state.get("labels", {})
    for vid, v in sorted(state["vehicles"].items()):
        tag = Text(vid, "bold white on red" if v["untrusted"] else "bold")
        if vid in compromised:
            tag.append(" ☠", "red")
        target = v.get("target_incident")
        assignment = f"{target} {labels.get(target, '')}"[:34] if target else "-"
        t.add_row(tag, v["type"], v["status"], trust_bar(v["trust"]), assignment)
    return Panel(t, title="fleet")


def describe(e: dict) -> Text:
    """One feed line for a typed event."""
    d, who = e.get("data", {}), e.get("vehicle") or ""
    body = " ".join(f"{k}={v}" for k, v in d.items() if k not in ("label",) and not isinstance(v, (dict, list)))
    if d.get("label"):
        body = f"{d['label']}  {body}"
    color = "red" if "compromised" in e["type"] or "trust" in e["type"] or "contradict" in body else \
        "yellow" if "escalat" in e["type"] or "human" in e["type"] else "cyan" if e["type"] == "call_received" else ""
    line_ = Text(f"t{e['tick']:>4} ", "dim") + Text(f"{e['type']:<20}", color or "bold")
    line_ += Text(f" {who:<3} {e.get('incident') or '':<3} ", "bold") + Text(body[:90])
    return line_


def render_feed(state: dict) -> Panel:
    """Last 12 typed events, newest on top."""
    events = state.get("events", [])[-12:][::-1]
    return Panel(Group(*[describe(e) for e in events]) if events else Text("no events yet", "dim"),
                 title="feed (newest first)")


def render(state: dict, bounds: Bounds, url: str) -> Layout:
    """Whole screen."""
    root = Layout()
    root.split_column(Layout(name="top", size=H + 3), Layout(name="feed"))
    root["top"].split_row(Layout(render_map(state, bounds), size=W + 4), Layout(name="right"))
    status = Text(f"{url}  run {state.get('run_id', '?')[:8]}  mode {state.get('mode')}  "
                  f"t={state.get('time_s', 0):.0f}s", "dim")
    if state.get("paused"):
        status.append("  ⏸ WAITING FOR HUMAN", "bold yellow")
    if state.get("replay"):
        status.append("  [replay]", "magenta")
    root["right"].update(Group(render_fleet(state), status))
    root["feed"].update(render_feed(state))
    return root


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--url", default="http://127.0.0.1:8000")
    ap.add_argument("--interval", type=float, default=0.5)
    args = ap.parse_args()
    bounds, run_id = Bounds(), None
    with Live(Text("connecting..."), screen=True, auto_refresh=False) as live:
        while True:
            try:
                state = fetch(args.url)
                if state.get("run_id") != run_id:
                    bounds, run_id = Bounds(), state.get("run_id")
                live.update(render(state, bounds, args.url), refresh=True)
            except Exception as exc:  # server not up yet / restarting
                live.update(Panel(Text(f"waiting for {args.url}/state: {exc}", "yellow")), refresh=True)
            time.sleep(args.interval)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
