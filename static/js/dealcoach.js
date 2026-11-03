// The counter-offer coach: opens automatically the first time either party flips a part of the deal
// form to "counter", shows this deal's history plus Nova's take, and lets them chat before they go
// back and type their own counter into the deal form themselves. Modeled on server.js's showPanel()
// and nova.js's runStream()/modelBubble()/userBubble().
(() => {
  const script = document.currentScript;
  const interestId = script.dataset.interest;
  const panel = document.getElementById("coach-panel");
  if (!panel || !interestId) return;

  // .app-main has a one-time transform-based entrance animation, and per spec that keeps it acting
  // as a containing block for position:fixed descendants even after the animation finishes -- so a
  // fixed panel left inside it quietly becomes position:absolute-relative-to-.app-main and scrolls
  // with the page instead of staying pinned. Move it out to a direct child of <body> to dodge that.
  document.body.appendChild(panel);

  let shown = false, userClosed = false, lastForm = "missing";

  function clearEmpty() { panel.querySelector("#coach-log > p")?.remove(); }

  function userBubble(text) {
    clearEmpty();
    const log = panel.querySelector("#coach-log");
    const row = document.createElement("div");
    row.className = "msg mine";
    row.innerHTML = '<div class="msg-main"><div class="msg-body"></div></div>';
    row.querySelector(".msg-body").textContent = text;
    log.appendChild(row);
    log.scrollTop = log.scrollHeight;
  }

  function modelBubble() {
    clearEmpty();
    const log = panel.querySelector("#coach-log");
    const row = document.createElement("div");
    row.className = "msg";
    row.innerHTML = '<div class="msg-main"><div class="msg-body nova-thinking">…</div></div>';
    log.appendChild(row);
    log.scrollTop = log.scrollHeight;
    return row;
  }

  function closedBanner() {
    const log = panel.querySelector("#coach-log");
    if (log.querySelector(".coach-closed-banner")) return;
    const note = document.createElement("p");
    note.className = "coach-closed-banner muted small";
    note.textContent = "This deal just closed. Open it again from the top to see the final outcome.";
    log.appendChild(note);
    log.scrollTop = log.scrollHeight;
    const form = panel.querySelector("#coach-form");
    if (form) form.querySelector("textarea, button").disabled = true;
  }

  async function send(text, kickoff) {
    const log = panel.querySelector("#coach-log");
    if (!kickoff) {
      userBubble(text);
      panel.querySelector("#coach-input").value = "";
    }
    const modelRow = modelBubble();
    const body = modelRow.querySelector(".msg-body");
    const params = new URLSearchParams(kickoff ? { kickoff: "1" } : { text });
    let buffer = "", full = "";
    try {
      const res = await fetch(`/deals/${interestId}/coach/send`, { method: "POST", body: params });
      if (!res.ok || !res.body) { body.textContent = "Something went wrong."; body.classList.remove("nova-thinking"); return; }
      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      while (true) {
        const { value, done } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        let idx;
        while ((idx = buffer.indexOf("\n")) >= 0) {
          const line = buffer.slice(0, idx);
          buffer = buffer.slice(idx + 1);
          if (!line.trim()) continue;
          const event = JSON.parse(line);
          if (event.type === "token") {
            body.classList.remove("nova-thinking");
            full += event.text;
            body.textContent = full;
            log.scrollTop = log.scrollHeight;
          } else if (event.type === "tool" && !full) {
            body.textContent = "Checking…";
          }
        }
      }
    } catch {
      body.textContent = full || "Couldn't reach Nova. Try again.";
    } finally {
      body.classList.remove("nova-thinking");
    }
  }

  function bindForm() {
    const form = panel.querySelector("#coach-form");
    form?.addEventListener("submit", (e) => {
      e.preventDefault();
      const input = panel.querySelector("#coach-input");
      const text = input.value.trim();
      if (text) send(text, false);
    });
  }

  async function openPanel() {
    const res = await fetch(`/deals/${interestId}/coach`);
    if (!res.ok) return;
    panel.innerHTML = await res.text();
    panel.hidden = false;
    bindForm();
    const log = panel.querySelector("#coach-log");
    if (log && log.dataset.kickoff === "1") send(null, true);
  }

  function closePanel() { panel.hidden = true; userClosed = true; }

  document.addEventListener("change", (e) => {
    if (!e.target.closest("#deal-form")) return;
    const flippedToCounter = [...document.querySelectorAll("#deal-form .deal-part input[type=radio]:checked")]
      .some((r) => r.value === "counter");
    if (flippedToCounter && !shown && !userClosed) { shown = true; openPanel(); }
  });

  document.addEventListener("click", (e) => { if (e.target.closest("#coach-panel [data-close-panel]")) closePanel(); });
  document.addEventListener("keydown", (e) => { if (e.key === "Escape" && !panel.hidden) closePanel(); });

  // The deal form is swapped live (see room.html's own observer) whenever either side acts. A new
  // form element means a fresh round: let the panel auto-open again. No form at all means the deal
  // just closed -- if the panel is open, say so instead of going stale.
  new MutationObserver(() => {
    const form = document.getElementById("deal-form");
    if (form === lastForm) return;
    lastForm = form;
    if (form) { shown = false; userClosed = false; }
    else if (!panel.hidden) { closedBanner(); }
  }).observe(document.querySelector(".deal-room"), { childList: true, subtree: true });
})();
