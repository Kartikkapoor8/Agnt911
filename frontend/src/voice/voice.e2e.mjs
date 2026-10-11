// Playwright check for the voice dispatcher, with a mock conversation (no mic, no ElevenLabs).
//
// Needs the backend (AGNT911_LLM=fake) behind a Vite dev server, and playwright-core installed somewhere:
//   PLAYWRIGHT_DIR=<dir with node_modules/playwright-core> CHROMIUM=<chromium binary> \
//   APP_URL=http://127.0.0.1:5173 node src/voice/voice.e2e.mjs
// Exits non-zero on the first failed check.

import { createRequire } from "node:module";

const require = createRequire((process.env.PLAYWRIGHT_DIR ?? process.cwd()) + "/");
const { chromium } = require("playwright-core");
const APP = process.env.APP_URL ?? "http://127.0.0.1:5173";
const DEFAULT = { lat: 37.334306, lng: -121.889232 };
const results = [];

function check(name, ok, detail = "") {
  results.push({ name, ok: Boolean(ok), detail });
  console.log(`${ok ? "PASS" : "FAIL"} ${name}${detail ? `: ${detail}` : ""}`);
  if (!ok) throw new Error(`failed: ${name}`);
}

const browser = await chromium.launch({ executablePath: process.env.CHROMIUM, args: ["--use-gl=angle", "--use-angle=swiftshader"] });
const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
await page.addInitScript(() => { window.__agnt911VoiceMock = true; });
const state = () => page.evaluate(() => fetch("/api/state").then((r) => r.json()));
const tool = (name, args) => page.evaluate(([n, a]) => window.__agnt911Voice.tool(n, a), [name, args]);
const say = (role, text) => page.evaluate(([r, t]) => window.__agnt911Voice.say(r, t), [role, text]);
const status = () => page.locator(".voice-status").textContent();

async function startCall() {
  await page.getByRole("button", { name: "Start Call" }).click();
  await page.waitForFunction(() => Boolean(window.__agnt911Voice));
  await page.locator(".voice-status.is-live").waitFor();
}

try {
  await page.goto(APP);
  // A refresh no longer resets: a run past t0 opens on the live map, a run at t0 on the landing.
  await page.waitForSelector(".start-demo, .status-strip");
  if (await page.locator(".start-demo").count()) await page.locator(".start-demo").click();
  await page.getByRole("tab", { name: "Fake call" }).click();
  check("notice before Start", await page.getByText("Demo only. Does not contact emergency services.").isVisible());

  // Call 1: the three tools in order.
  await startCall();
  check("status live", (await status()) === "live");
  await say("agent", "911 dispatcher, what's your emergency?");
  await say("user", "A man collapsed at 2nd and Santa Clara, he's not breathing.");
  const lines = await page.locator(".voice-line").allTextContents();
  check("transcript shows both sides", lines.length === 2 && lines[0].startsWith("Dispatcher") && lines[1].startsWith("Caller"), lines.join(" | "));

  const before = Object.keys((await state()).incidents);
  const first = await tool("update_incident", { type: "medical", location_text: "S 2nd St and E Santa Clara St", caller_name: "Dana", details: "man collapsed, not breathing" });
  let s = await state();
  const added = Object.keys(s.incidents).filter((id) => !before.includes(id));
  check("first update_incident creates one incident", added.length === 1 && added[0] === first.incident_id, JSON.stringify(first));
  const id = first.incident_id;
  check("created as a caller call with the label", s.incidents[id].source === "caller" && s.labels[id] === "Dana: man collapsed, not breathing", `${s.incidents[id].source} / ${s.labels[id]}`);
  const metres = Math.hypot((first.location.lat - 37.33792) * 111000, (first.location.lng + 121.8869) * 88300);
  check("geocoded to the corner", !first.location.fallback && metres < 200, `${Math.round(metres)} m off; ${JSON.stringify(first.location)}`);
  await page.locator(`.incident-pin[data-id="${id}"] .voice-badge`).waitFor({ timeout: 5000 });
  check("pin has the called-in badge", true, id);

  const second = await tool("update_incident", { type: "medical", severity: 5, details: "still not breathing, CPR started" });
  s = await state();
  check("second update_incident edits the same call", second.incident_id === id && Object.keys(s.incidents).length === before.length + 1, JSON.stringify(second));
  check("severity and label updated", s.incidents[id].severity === 5 && s.labels[id] === "Dana: still not breathing, CPR started", `${s.incidents[id].severity} / ${s.labels[id]}`);

  const d1 = await tool("request_demo_dispatch", {});
  const d2 = await tool("request_demo_dispatch", {});
  check("request_demo_dispatch is idempotent", d1.status === "negotiating" && d1.incident_id === id && d2.incident_id === id, `${JSON.stringify(d1)} then ${JSON.stringify(d2)}`);

  let report = await tool("get_dispatch_status", {});
  const firstReport = report;
  const until = Date.now() + 15000;
  while (!report.assigned.length && Date.now() < until) {
    await page.waitForTimeout(300);
    report = await tool("get_dispatch_status", {});
  }
  const shapeOk = report.assigned.every((a) => typeof a.id === "string" && typeof a.type === "string" && Number.isFinite(a.eta_seconds));
  check("get_dispatch_status lists units after a round", report.assigned.length > 0 && shapeOk && typeof report.consensus === "boolean" && ["dispatched", "on_scene"].includes(report.status), `first ${JSON.stringify(firstReport)}; then ${JSON.stringify(report)}`);
  const plan = (await state()).plans[id];
  check("units match /state", JSON.stringify(report.assigned.map((a) => a.id).sort()) === JSON.stringify([...(await state()).incidents[id].assigned].sort()), `plan ${JSON.stringify(plan?.assigned)}`);

  await page.evaluate(() => window.__agnt911Voice.hangUp());
  await page.getByRole("button", { name: "Start Call" }).waitFor();
  check("agent hang-up ends the call", (await status()) === "ended" && (await page.locator(".voice-result").textContent()).includes(id));

  // Call 2: a new session makes a new call; a place off the map falls back; an error resets the button.
  await startCall();
  const far = await tool("update_incident", { type: "fire", location_text: "Times Square, New York", caller_name: "Lee" });
  check("new session creates a new call", far.incident_id !== id, far.incident_id);
  check("off-map place uses DEFAULT_CALL_LOCATION", far.location.fallback && far.location.lat === DEFAULT.lat && far.location.lng === DEFAULT.lng, JSON.stringify(far.location));
  await page.evaluate(() => window.__agnt911Voice.fail("network lost"));
  await page.getByRole("button", { name: "Start Call" }).waitFor();
  const toastText = await page.locator(".toast", { hasText: "Voice call failed" }).first().textContent().catch(() => "");
  check("error shows a toast and resets the button", (await status()) === "error" && toastText.includes("network lost"), toastText);

  // Call 3: nobody hangs up; the client stops it at 25 s.
  const t0 = Date.now();
  await startCall();
  await page.waitForTimeout(2000);
  const shown = await page.locator(".voice-countdown").textContent();
  check("countdown runs", /^0:1[78]$/.test(shown), shown);
  await page.getByRole("button", { name: "Start Call" }).waitFor({ timeout: 30000 });
  const took = (Date.now() - t0) / 1000;
  check("hard stop at 25 s", took >= 24.5 && took < 27 && (await status()) === "ended" && !(await page.evaluate(() => Boolean(window.__agnt911Voice))), `${took.toFixed(1)} s`);

  await page.getByRole("tab", { name: "Hijack" }).click();
  check("rest of the page still works", await page.locator(".judge-panel").isVisible());
} catch (err) {
  console.error(String(err));
  await page.screenshot({ path: process.env.SCREENSHOT ?? "voice-e2e-failure.png" }).catch(() => {});
  await browser.close();
  process.exit(1);
}
await page.screenshot({ path: process.env.SCREENSHOT ?? "voice-e2e.png" }).catch(() => {});
await browser.close();
console.log(`${results.length} checks passed`);
