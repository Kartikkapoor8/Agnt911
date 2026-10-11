// What this page saw of each run: its tick records and when each vehicle was hacked. /state.comparison keeps only a
// finished run's final metrics, but the scoreboard compares both modes at the same tick, and "lies caught" needs
// to know which vehicles were hacked. Memory only: a page refresh resets the run anyway.

import type { TickRecord, WorldState } from "./types";

interface RunMemory {
  baseline: boolean;
  records: TickRecord[];
  hackedAt: Record<string, number>; // vehicle -> tick it was compromised
}

const runs = new Map<string, RunMemory>();

/** Record this poll's view of the run. */
export function rememberRun(state: WorldState): void {
  const mem = runs.get(state.run_id) ?? { baseline: state.mode === "baseline", records: [], hackedAt: {} };
  if (state.tick_records?.length) mem.records = state.tick_records;
  for (const e of state.events ?? []) {
    if (e.type === "vehicle_compromised" && e.vehicle && !(e.vehicle in mem.hackedAt)) mem.hackedAt[e.vehicle] = e.tick;
  }
  for (const id of state.compromised ?? []) if (!(id in mem.hackedAt)) mem.hackedAt[id] = state.tick;
  runs.set(state.run_id, mem);
}

/** The run's hacked vehicles, or null if this page never saw the run. */
export function compromisedIn(runId: string): string[] | null {
  const mem = runs.get(runId);
  return mem ? Object.keys(mem.hackedAt) : null;
}

/** The last tick this page saw for a run, or null. */
export function lastTick(runId: string): number | null {
  const recs = runs.get(runId)?.records;
  return recs?.length ? recs[recs.length - 1].tick : null;
}

export interface Score {
  tick: number;
  calls: number; // calls that existed by this tick
  served: number;
  hacked: number; // vehicles hacked by this tick
  caught: number; // hacked vehicles the fleet had stopped trusting by this tick
  wronglyFlagged: number; // honest vehicles it had stopped trusting
  avgResponseTicks: number | null;
  asked: number; // ticks where the run paused for a human
}

/** A run's scoreboard numbers as of tick n (from its tick records, the same inputs as trust.metrics). Null if unseen. */
export function scoreAt(runId: string, n: number): Score | null {
  const mem = runs.get(runId);
  if (!mem || mem.records.length === 0) return null;
  const upto = mem.records.filter((r) => r.tick <= n);
  if (upto.length === 0) return null;
  const last = upto[upto.length - 1];
  const created = new Map<string, number>();
  const served = new Map<string, number>();
  const flagged = new Set<string>();
  let asked = 0;
  for (const r of upto) {
    for (const id of [...r.incidents_open, ...r.incidents_served, ...r.incidents_false_handled]) if (!created.has(id)) created.set(id, r.tick);
    for (const id of r.incidents_served) if (!served.has(id)) served.set(id, r.tick);
    r.untrusted.forEach((v) => flagged.add(v));
    if (r.escalation) asked++;
  }
  const hacked = Object.entries(mem.hackedAt).filter(([, t]) => t <= n).map(([v]) => v);
  const responses = [...served].map(([id, t]) => t - (created.get(id) ?? t));
  return {
    tick: last.tick,
    calls: last.incidents_open.length + last.incidents_served.length + last.incidents_false_handled.length,
    served: last.incidents_served.length,
    hacked: hacked.length,
    caught: [...flagged].filter((v) => hacked.includes(v)).length,
    wronglyFlagged: [...flagged].filter((v) => !(v in mem.hackedAt)).length,
    avgResponseTicks: responses.length ? responses.reduce((a, b) => a + b, 0) / responses.length : null,
    asked,
  };
}
