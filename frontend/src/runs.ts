// Which vehicles were hacked in each run this browser watched. /state.comparison keeps a finished run's metrics but
// not its hacked list, and "lies caught" vs "honest units wrongly flagged" needs it. Kept for the browser session.

const KEY = "agnt911.runCompromised";
const runs = new Map<string, string[]>(load());

function load(): [string, string[]][] {
  try {
    return Object.entries(JSON.parse(sessionStorage.getItem(KEY) ?? "{}") as Record<string, string[]>);
  } catch {
    return [];
  }
}

/** Record the run's hacked vehicles as of this poll. */
export function rememberRun(runId: string, compromised: string[]): void {
  const prev = runs.get(runId);
  if (prev && prev.length === compromised.length && prev.every((v, i) => v === compromised[i])) return;
  runs.set(runId, [...compromised]);
  try {
    sessionStorage.setItem(KEY, JSON.stringify(Object.fromEntries(runs)));
  } catch {
    // storage blocked: memory only
  }
}

/** The run's hacked vehicles, or null if this browser never saw the run. */
export function compromisedIn(runId: string): string[] | null {
  return runs.get(runId) ?? null;
}
