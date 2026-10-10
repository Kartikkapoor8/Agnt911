// The three judge controls in backend/agnt911/engine/api.py. GET /state lives in useWorldState.

const BASE = "/api"; // Vite proxies /api to the backend

export type Mode = "protected" | "baseline";

/** Turn a FastAPI error body ({detail: string} or a 422 list) into one readable line. */
async function errorText(res: Response): Promise<string> {
  try {
    const body = await res.json();
    if (typeof body.detail === "string") return `${res.status}: ${body.detail}`;
    if (Array.isArray(body.detail)) return `${res.status}: ${body.detail.map((d: { msg?: string }) => d.msg).join("; ")}`;
  } catch {
    // fall through to the status line
  }
  return `${res.status} ${res.statusText}`;
}

async function post(path: string, body: unknown): Promise<unknown> {
  let res: Response;
  try {
    res = await fetch(BASE + path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  } catch {
    throw new Error("Backend not reachable");
  }
  if (!res.ok) throw new Error(await errorText(res));
  return res.json();
}

/** What the judge sends: exactly one of presetId or text. vehicleId may be null only for the fake_call preset. */
export type InjectRequest = { vehicleId: string | null } & ({ presetId: string } | { text: string });

/** Hack a vehicle's sensor feed with a scenario preset or a raw note, or place a fake call (POST /inject). */
export function postInject(req: InjectRequest): Promise<unknown> {
  const body: Record<string, string> = "presetId" in req ? { preset_id: req.presetId } : { text: req.text };
  if (req.vehicleId !== null) body.vehicle_id = req.vehicleId;
  return post("/inject", body);
}

/** Restart the scenario under fleet consensus or the single dispatcher (POST /mode). */
export function postMode(mode: Mode): Promise<unknown> {
  return post("/mode", { mode });
}

/** Send the human's choice of vehicles for the escalated calls (POST /resolve). */
export function postResolve(assignments: Record<string, string | null>): Promise<unknown> {
  return post("/resolve", { assignments });
}
