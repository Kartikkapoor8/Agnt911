// The "called in" badge on a voice call's map pin. The map draws the pin from /state as usual; this only
// decorates it, so the map code needs no change. The pin may arrive a poll later, so keep looking briefly.

const LOOK_MS = 5000;

/** Put a "called in" badge on an incident's pin as soon as the pin exists. */
export function badgeIncident(incidentId: string): void {
  const started = Date.now();
  const attach = () => {
    const pin = document.querySelector<HTMLElement>(`.incident-pin[data-id="${CSS.escape(incidentId)}"]`);
    if (pin) {
      if (!pin.querySelector(".voice-badge")) {
        const badge = document.createElement("span");
        badge.className = "voice-badge";
        badge.textContent = "called in";
        pin.appendChild(badge);
      }
      return;
    }
    if (Date.now() - started < LOOK_MS) window.setTimeout(attach, 100);
  };
  attach();
}
