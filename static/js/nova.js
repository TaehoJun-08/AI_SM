// Nova's dedicated chat tab: a streamed fetch/response loop (no sockets -- it's a one-to-one conversation
// with the model, not a multi-user room). Responses arrive as newline-delimited JSON events so they can be
// rendered token by token, and a Stop button can abort the request mid-flight.
(() => {
  const form = document.getElementById("nova-form");
  if (!form) return;
  const input = document.getElementById("nova-input");
  const log = document.getElementById("nova-log");
  const actionsBox = document.getElementById("nova-actions");
  const sendBtn = document.getElementById("nova-send-btn");
  const stopBtn = document.getElementById("nova-stop-btn");
  const fileInput = document.getElementById("nova-file");
  const preview = document.getElementById("nova-preview");
  const previewImg = document.getElementById("nova-preview-img");
  const previewName = document.getElementById("nova-preview-name");
  const previewClear = document.getElementById("nova-preview-clear");
  const sendUrl = form.dataset.send;

  let controller = null;
  let pendingFile = null;

  function resizeInput() {
    input.style.height = "auto";
    input.style.height = Math.min(input.scrollHeight, 160) + "px";
  }
  input.addEventListener("input", resizeInput);
  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      form.requestSubmit();
    }
  });

  function clearEmpty() { document.querySelector("#nova-log .chat-empty")?.remove(); }

  function userBubble(text, fileForPreview) {
    clearEmpty();
    const row = document.createElement("div");
    row.className = "msg mine";
    row.dataset.id = "pending";
    row.dataset.role = "user";
    const main = document.createElement("div");
    main.className = "msg-main";
    if (fileForPreview) {
      const att = document.createElement("div");
      att.className = "msg-att";
      att.innerHTML = `<img class="att-img" alt="">`;
      att.querySelector("img").src = URL.createObjectURL(fileForPreview);
      main.appendChild(att);
    }
    if (text) {
      const body = document.createElement("div");
      body.className = "msg-body";
      body.textContent = text;
      main.appendChild(body);
    }
    const editBtn = document.createElement("button");
    editBtn.type = "button";
    editBtn.className = "nova-edit-btn";
    editBtn.title = "Edit this message";
    editBtn.textContent = "✎";
    main.appendChild(editBtn);
    row.appendChild(main);
    log.appendChild(row);
    log.scrollTop = log.scrollHeight;
    return row;
  }

  function modelBubble() {
    clearEmpty();
    const row = document.createElement("div");
    row.dataset.role = "model";
    row.className = "msg";
    row.innerHTML = '<div class="msg-main"><div class="msg-body nova-thinking">…</div></div>';
    log.appendChild(row);
    log.scrollTop = log.scrollHeight;
    return row;
  }

  function renderActions(list) {
    actionsBox.innerHTML = "";
    for (const a of list) {
      const card = document.createElement("div");
      card.className = "nova-action";
      card.dataset.action = a.id;
      let head = "", body = "";
      if (a.kind === "send_dm") { head = `✉️ Send to <b>@${a.payload.username}</b>`; body = a.payload.message; }
      else if (a.kind === "post_update") { head = `📣 Post update on <b>${a.payload.title}</b>`; body = a.payload.body; }
      card.innerHTML = `<div class="nova-action-head">${head}</div><div class="nova-action-body"></div>
        <div class="nova-action-buttons">
          <button type="button" class="btn btn-sm btn-primary rounded-pill" data-decide="approve">Approve</button>
          <button type="button" class="btn btn-sm btn-outline-secondary rounded-pill" data-decide="reject">Discard</button>
        </div>`;
      card.querySelector(".nova-action-body").textContent = body;
      actionsBox.appendChild(card);
    }
  }

  function setBusy(busy) {
    input.disabled = busy;
    sendBtn.hidden = busy;
    stopBtn.hidden = !busy;
  }

  async function runStream(url, formData, userRow, modelRow) {
    controller = new AbortController();
    setBusy(true);
    let buffer = "", full = "";
    const body = modelRow.querySelector(".msg-body");
    try {
      const res = await fetch(url, { method: "POST", body: formData, signal: controller.signal });
      if (!res.ok || !res.body) {
        const info = await res.json().catch(() => ({}));
        body.textContent = info.error || "Something went wrong.";
        body.classList.remove("nova-thinking");
        return;
      }
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
          if (event.type === "start") {
            if (userRow && event.message_id) userRow.dataset.id = event.message_id;
          } else if (event.type === "token") {
            body.classList.remove("nova-thinking");
            full += event.text;
            body.textContent = full;
            log.scrollTop = log.scrollHeight;
          } else if (event.type === "tool") {
            if (!full) body.textContent = "Checking…";
          } else if (event.type === "done") {
            renderActions(event.actions || []);
          }
        }
      }
    } catch (e) {
      if (e.name === "AbortError") {
        body.textContent = full ? full + "\n\n_(stopped)_" : "(stopped)";
      } else {
        body.textContent = full || "Couldn't reach Nova. Try again.";
      }
    } finally {
      body.classList.remove("nova-thinking");
      setBusy(false);
      controller = null;
    }
  }

  function clearPreview() {
    pendingFile = null;
    fileInput.value = "";
    preview.hidden = true;
    previewImg.src = "";
  }

  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const text = input.value.trim();
    const file = pendingFile;
    if (!text && !file) return;
    input.value = "";
    resizeInput();
    clearPreview();
    const userRow = userBubble(text, file);
    const modelRow = modelBubble();
    const data = new FormData();
    data.append("text", text);
    if (file) data.append("file", file);
    await runStream(sendUrl, data, userRow, modelRow);
    input.focus();
  });

  stopBtn.addEventListener("click", () => controller?.abort());

  fileInput.addEventListener("change", () => {
    const file = fileInput.files[0];
    if (!file) return;
    pendingFile = file;
    previewImg.src = URL.createObjectURL(file);
    previewName.textContent = file.name;
    preview.hidden = false;
  });
  previewClear.addEventListener("click", clearPreview);

  actionsBox.addEventListener("click", async (e) => {
    const btn = e.target.closest("[data-decide]");
    if (!btn) return;
    const card = btn.closest(".nova-action");
    const id = card.dataset.action;
    btn.closest(".nova-action-buttons").querySelectorAll("button").forEach((b) => (b.disabled = true));
    const res = await fetch(`/nova/actions/${id}/${btn.dataset.decide}`, { method: "POST" });
    const info = await res.json().catch(() => ({}));
    card.querySelector(".nova-action-body").textContent = info.note || (btn.dataset.decide === "approve" ? "Done." : "Discarded.");
    card.querySelector(".nova-action-buttons").remove();
  });

  log.addEventListener("click", (e) => {
    const btn = e.target.closest(".nova-edit-btn");
    if (!btn) return;
    const row = btn.closest(".msg");
    const id = row.dataset.id;
    if (!id || id === "pending") return;
    const bodyEl = row.querySelector(".msg-body");
    const current = bodyEl ? bodyEl.textContent : "";
    const main = row.querySelector(".msg-main");
    const editor = document.createElement("div");
    editor.className = "nova-editor";
    const textarea = document.createElement("textarea");
    textarea.className = "form-control form-control-sm";
    textarea.value = current;
    editor.appendChild(textarea);
    const buttons = document.createElement("div");
    buttons.className = "nova-editor-buttons";
    buttons.innerHTML = `<button type="button" class="btn btn-sm btn-primary rounded-pill" data-save>Save &amp; resend</button>
      <button type="button" class="btn btn-sm btn-outline-secondary rounded-pill" data-cancel>Cancel</button>`;
    editor.appendChild(buttons);
    main.appendChild(editor);
    btn.hidden = true;
    textarea.focus();
    buttons.querySelector("[data-cancel]").addEventListener("click", () => { editor.remove(); btn.hidden = false; });
    buttons.querySelector("[data-save]").addEventListener("click", async () => {
      const text = textarea.value.trim();
      if (!text) return;
      let sibling = row.nextElementSibling;
      while (sibling) { const next = sibling.nextElementSibling; sibling.remove(); sibling = next; }
      editor.remove();
      if (bodyEl) bodyEl.textContent = text;
      else { const b = document.createElement("div"); b.className = "msg-body"; b.textContent = text; main.insertBefore(b, btn); }
      btn.hidden = false;
      const data = new FormData();
      data.append("text", text);
      const modelRow = modelBubble();
      await runStream(`/nova/messages/${id}/edit`, data, row, modelRow);
      input.focus();
    });
  });

  document.querySelectorAll(".nova-forget").forEach((btn) => {
    btn.addEventListener("click", async () => {
      await fetch(`/nova/memory/${btn.dataset.forget}/forget`, { method: "POST" });
      btn.closest(".nova-fact").remove();
    });
  });

  // Remember where you were in the conversation, and come back to that spot next time.
  // First visit (nothing saved yet) starts at the newest message instead of the top.
  const SCROLL_KEY = "nova-scroll";
  function restoreScroll() {
    let saved = null;
    try { saved = localStorage.getItem(SCROLL_KEY); } catch (e) {}
    log.scrollTop = saved === null ? log.scrollHeight : Number(saved);
  }
  restoreScroll();
  window.addEventListener("load", restoreScroll);
  log.addEventListener("scroll", () => {
    try { localStorage.setItem(SCROLL_KEY, log.scrollTop); } catch (e) {}
  });
})();
