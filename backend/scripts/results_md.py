"""Compare protected and baseline batch runs at the same tick and write docs/results.md.

Reads the per-run JSON that batch_runs.py --dump writes. Every run is cut at the shortest run's last tick.
Usage, from backend/:  .venv/bin/python scripts/results_md.py [--runs runs] [--out ../docs/results.md]
"""

import argparse
import json
from pathlib import Path

COLUMNS = ["hacks caught", "honest wrongly flagged", "calls answered", "closed with nobody sent",
           "mean response s", "human asked", "calls used"]


def at_tick(r: dict, t: int) -> dict:
    """Return one run's numbers as they stood at tick t."""
    records = [rec for rec in r["records"] if rec["tick"] <= t]
    last = records[-1]
    flagged = {}
    for rec in records:
        for vid in rec["untrusted"]:
            flagged.setdefault(vid, rec["tick"])
    hacked = {vid: tick for vid, tick in r["hacked"].items() if tick <= t}
    caught = sum(1 for vid in hacked if vid in flagged)
    honest = sum(1 for vid, ft in flagged.items() if vid not in hacked or ft < hacked[vid])
    responses = [x["served"] - x["created"] for x in r["incident_times"].values()
                 if x["served"] is not None and x["served"] <= t]
    calls = r["calls_at"].get(str(t), r["calls"])
    return {"hacks caught": f"{caught}/{len(hacked)}", "_caught": caught,
            "honest wrongly flagged": honest, "calls answered": len(last["incidents_served"]),
            "closed with nobody sent": len(last["incidents_false_handled"]),
            "mean response s": round(sum(responses) / len(responses), 1) if responses else None,
            "human asked": sum(1 for e in r["escalations"] if e <= t), "calls used": calls}


def mean(values: list) -> str:
    values = [v for v in values if v is not None]
    return f"{sum(values) / len(values):.1f}" if values else "-"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--runs", type=Path, default=Path("runs"))
    parser.add_argument("--out", type=Path, default=Path("../docs/results.md"))
    args = parser.parse_args()
    runs = {p.stem: json.loads(p.read_text()) for p in sorted(args.runs.glob("*.json"))}
    t = min(r["tick"] for r in runs.values())
    rows = {name: at_tick(r, t) for name, r in runs.items()}

    out = ["# Results: protected vs baseline", "",
           f"Demo scenario on the stepped clock (1 tick = 1 s), real Claude. Every run is compared at tick {t}, "
           "the shortest run's end. Escalations are auto-resolved by scripts/_demo.py.", "",
           "**Hacks caught**: hijacked vehicles marked untrusted by that tick. **Honest wrongly flagged**: honest "
           "vehicles marked untrusted. **Closed with nobody sent**: calls closed with no responder assigned. "
           "**Mean response s**: call created to first responder on scene, over served calls. "
           "**Calls used**: API requests.", "",
           "## Summary (means)", "",
           "| mode | runs | " + " | ".join(COLUMNS) + " |", "|---" * (len(COLUMNS) + 2) + "|"]
    summary = []
    for mode in ("protected", "baseline"):
        mine = [rows[n] for n in rows if n.startswith(mode)]
        if not mine:
            continue
        hacked = mean([int(x["hacks caught"].split("/")[1]) for x in mine])
        cells = [f"{mean([x['_caught'] for x in mine])}/{hacked}"] + [mean([x[c] for x in mine]) for c in COLUMNS[1:]]
        summary.append(f"| {mode} | {len(mine)} | " + " | ".join(cells) + " |")
    out += summary
    out += ["", "## Per run", "", "| run | " + " | ".join(COLUMNS) + " |", "|---" * (len(COLUMNS) + 1) + "|"]
    for name, row in rows.items():
        stop = f" (stopped t{runs[name]['tick']})" if runs[name]["stopped"] else ""
        out.append(f"| {name}{stop} | " + " | ".join(str(row[c] if row[c] is not None else "-") for c in COLUMNS) + " |")
    args.out.write_text("\n".join(out) + "\n")
    print("\n".join(out[: out.index("## Per run")]))


if __name__ == "__main__":
    main()
