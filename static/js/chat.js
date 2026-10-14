// Real-time chat client shared by server channels and direct messages.
// The page says which room to use through data-* attributes on #chat-log.
const log = document.getElementById("chat-log");
const form = document.getElementById("chat-form");
const input = document.getElementById("chat-input");
const bar = document.getElementById("compose-bar");
const myId = Number(log.dataset.me);
const canManage = log.dataset.manage === "1";
const room = { [log.dataset.key]: Number(log.dataset.room) };
const socket = window.liveSocket;   // the page-wide socket from live.js

const srcOf = (path) => (/^https?:/.test(path) ? path : "/static/" + path);
const stickToBottom = () => { log.scrollTop = log.scrollHeight; };

// ---------- drawing messages ----------
function attachmentNode(m) {
  const src = srcOf(m.attachment);
  let node;
  if (m.attach_type === "image") {
    node = document.createElement("a");
    node.href = src;
    node.target = "_blank";
    node.rel = "noopener";
    const img = document.createElement("img");
    img.className = "att-img";
    img.src = src;
    img.alt = "";
    img.referrerPolicy = "no-referrer";
    img.addEventListener("load", stickToBottom);
    node.appendChild(img);
  } else if (m.attach_type === "video" || m.attach_type === "audio") {
    node = document.createElement(m.attach_type);
    node.className = m.attach_type === "video" ? "att-video" : "att-audio";
    node.src = src;
    node.controls = true;
    node.preload = "metadata";
  } else {
    node = document.createElement("a");
    node.className = "att-file";
    node.href = src;
    node.download = m.attach_name || "file";
    node.textContent = "📎 " + (m.attach_name || "file");
  }
  const holder = document.createElement("div");
  holder.className = "msg-att";
  holder.appendChild(node);
  return holder;
}

function addMessage(m) {
  document.getElementById("chat-empty")?.remove();
  const name = m.name || m.full_name;
  const last = log.lastElementChild;
  const grouped = last && last.dataset.sender === String(m.sender_id);
  const row = document.createElement("div");
  row.className = "msg" + (m.sender_id === myId ? " mine" : "") + (grouped ? " grouped" : "");
  row.dataset.sender = m.sender_id;
  row.dataset.id = m.id;
  row.dataset.author = name;
  row.dataset.canDelete = (m.sender_id === myId || canManage) ? "1" : "0";
  row.dataset.canEdit = (m.sender_id === myId && !m.attachment) ? "1" : "0";
  row.innerHTML = '<span class="avatar-sm"></span><div class="msg-main"><div class="msg-meta">'
    + '<strong></strong> <small></small></div><div class="msg-body"></div></div>';
  const avatar = row.querySelector(".avatar-sm");
  avatar.textContent = name[0].toUpperCase();
  const strong = row.querySelector("strong");
  strong.textContent = name;
  if (m.avatar) {
    avatar.textContent = "";
    const img = document.createElement("img");
    img.src = "/static/" + m.avatar;
    avatar.appendChild(img);
  } else if (m.color) {
    avatar.style.background = m.color;
  }
  if (m.color) strong.style.color = m.color;
  row.querySelector("small").textContent = m.created_at.slice(11, 16);
  if (m.reply_name) {
    const quote = document.createElement("div");
    quote.className = "msg-reply";
    quote.dataset.reply = m.reply_to;
    quote.innerHTML = "<b></b><span></span>";
    quote.querySelector("b").textContent = m.reply_name;
    quote.querySelector("span").textContent = m.reply_text;
    row.querySelector(".msg-meta").after(quote);
  }
  const body = row.querySelector(".msg-body");
  if (m.body) body.textContent = m.body; else body.remove();
  if (m.attachment) row.querySelector(".msg-main").appendChild(attachmentNode(m));
  log.appendChild(row);
  stickToBottom();
}

const joinRoom = () => socket.emit(log.dataset.join, room);
socket.on("connect", joinRoom);      // also after a dropped connection comes back
if (socket.connected) joinRoom();
// A message that arrives while this chat is open on screen counts as read.
const seenEvent = log.dataset.key === "thread_id" ? "dm_seen" : "srv_seen";
const markSeen = () => { if (document.visibilityState === "visible") socket.emit(seenEvent, room); };
socket.on("message", (m) => {
  addMessage(m);
  stopTyping(m.sender_id);
  if (m.sender_id !== myId) markSeen();
});
document.addEventListener("visibilitychange", markSeen);

// ---------- "X is typing…" and "Seen" ----------
const kind = log.dataset.key === "thread_id" ? "dm" : "srv";
const typers = new Map();                     // user id -> {name, timer}
const typingNote = document.getElementById("typing");

function showTyping() {
  if (!typingNote) return;
  const names = [...typers.values()].map((t) => t.name);
  typingNote.hidden = names.length === 0;
  typingNote.textContent = names.length === 1 ? `${names[0]} is typing…`
    : names.length === 2 ? `${names[0]} and ${names[1]} are typing…` : names.length ? "Several people are typing…" : "";
}
function stopTyping(userId) {
  const t = typers.get(userId);
  if (t) { clearTimeout(t.timer); typers.delete(userId); showTyping(); }
}
socket.on("typing", (d) => {
  if (d.kind !== kind || d.id !== room[log.dataset.key]) return;
  stopTyping(d.user_id);
  typers.set(d.user_id, { name: d.name, timer: setTimeout(() => stopTyping(d.user_id), 3500) });
  showTyping();
});
let lastTyped = 0;
input?.addEventListener("input", () => {
  if (Date.now() - lastTyped < 2000 || !input.value) return;
  lastTyped = Date.now();
  socket.emit("typing", { kind: kind, id: room[log.dataset.key] });
});

// DMs: a "Seen" mark under your last message once the other person has read it.
let seenUpto = Number(log.dataset.seenUpto) || 0;
function renderSeen() {
  log.querySelectorAll(".seen-mark").forEach((el) => el.remove());
  const last = log.lastElementChild;
  if (kind !== "dm" || !last || !last.classList.contains("mine") || Number(last.dataset.id) > seenUpto) return;
  const mark = document.createElement("div");
  mark.className = "seen-mark";
  mark.textContent = "Seen";
  last.querySelector(".msg-main").appendChild(mark);
}
socket.on("seen", (d) => {
  if (d.user_id === myId) return;
  seenUpto = Math.max(seenUpto, d.upto);
  renderSeen();
});
socket.on("message", () => { renderSeen(); });
renderSeen();
socket.on("deleted", (data) => log.querySelector(`.msg[data-id="${data.id}"]`)?.remove());
socket.on("edited", (data) => {
  const row = log.querySelector(`.msg[data-id="${data.id}"]`);
  if (!row) return;
  const body = row.querySelector(".msg-body");
  if (body) body.textContent = data.body;
  if (!row.querySelector(".edited")) {
    const tag = document.createElement("small");
    tag.className = "edited";
    tag.textContent = "(edited)";
    row.querySelector(".msg-meta").append(" ", tag);
  }
  // quotes of this message elsewhere in the chat
  log.querySelectorAll(`.msg-reply[data-reply="${data.id}"] span`).forEach((s) => { s.textContent = data.body; });
});

// Clicking a quote jumps to the original message.
log.addEventListener("click", (event) => {
  const quote = event.target.closest(".msg-reply");
  if (!quote) return;
  const target = log.querySelector(`.msg[data-id="${quote.dataset.reply}"]`);
  if (!target) return;
  target.scrollIntoView({ behavior: "smooth", block: "center" });
  target.classList.add("flash");
  setTimeout(() => target.classList.remove("flash"), 1400);
});

// ---------- the composer: reply / edit state ----------
let replyTo = null;   // {id, name, text}
let editing = null;   // {id}

function showBar() {
  if (!bar) return;
  bar.hidden = !(replyTo || editing);
  if (editing) bar.querySelector(".cb-text").innerHTML = "✏️ <b>Editing message</b>";
  else if (replyTo) {
    bar.querySelector(".cb-text").innerHTML = "↩️ Replying to <b></b>: <span></span>";
    bar.querySelector("b").textContent = replyTo.name;
    bar.querySelector("span").textContent = replyTo.text;
  }
}

function clearComposer(keepText) {
  replyTo = editing = null;
  if (!keepText && input) input.value = "";
  showBar();
}

bar?.querySelector("#cb-close").addEventListener("click", () => clearComposer(editing === null));

if (form) {
  form.addEventListener("submit", (event) => {
    event.preventDefault();
    const body = input.value.trim();
    if (!body) return;
    if (editing) {
      socket.emit(log.dataset.edit, { message_id: editing.id, body: body });
    } else {
      socket.emit(log.dataset.send, { ...room, body: body, reply_to: replyTo ? replyTo.id : null });
    }
    clearComposer(false);
  });
  input.addEventListener("keydown", (event) => { if (event.key === "Escape") clearComposer(editing === null); });
}

// ---------- right-click (or long-press) menu on a message ----------
const menu = document.createElement("div");
menu.className = "msg-menu";
menu.hidden = true;
menu.innerHTML = '<button data-act="reply">↩️ Reply</button><button data-act="copy">📋 Copy text</button>'
  + '<button data-act="edit">✏️ Edit</button><button data-act="delete" class="danger">🗑 Delete</button>';
document.body.appendChild(menu);
let menuRow = null;

function openMenu(row, x, y) {
  menuRow = row;
  const hasText = !!row.querySelector(".msg-body");
  menu.querySelector('[data-act="reply"]').hidden = !form;
  menu.querySelector('[data-act="copy"]').hidden = !hasText;
  menu.querySelector('[data-act="edit"]').hidden = !(row.dataset.canEdit === "1" && log.dataset.edit);
  menu.querySelector('[data-act="delete"]').hidden = !(row.dataset.canDelete === "1" && log.dataset.delete);
  menu.hidden = false;
  const w = menu.offsetWidth, h = menu.offsetHeight;
  menu.style.left = Math.max(8, Math.min(x, window.innerWidth - w - 8)) + "px";
  menu.style.top = Math.max(8, Math.min(y, window.innerHeight - h - 8)) + "px";
  row.classList.add("selected");
}
function closeMenu() {
  menu.hidden = true;
  menuRow?.classList.remove("selected");
  menuRow = null;
}

log.addEventListener("contextmenu", (event) => {
  const row = event.target.closest(".msg");
  if (!row) return;
  event.preventDefault();
  closeMenu();
  openMenu(row, event.clientX, event.clientY);
});
// phones: press and hold
let holdTimer;
log.addEventListener("touchstart", (event) => {
  const row = event.target.closest(".msg");
  if (!row) return;
  const touch = event.touches[0];
  holdTimer = setTimeout(() => openMenu(row, touch.clientX, touch.clientY), 500);
}, { passive: true });
["touchend", "touchmove", "touchcancel"].forEach((n) => log.addEventListener(n, () => clearTimeout(holdTimer)));

document.addEventListener("click", (event) => { if (!event.target.closest(".msg-menu")) closeMenu(); });
document.addEventListener("keydown", (event) => { if (event.key === "Escape") closeMenu(); });
log.addEventListener("scroll", closeMenu);

menu.addEventListener("click", (event) => {
  const act = event.target.closest("button")?.dataset.act;
  const row = menuRow;
  if (!act || !row) return;
  closeMenu();
  const id = Number(row.dataset.id);
  if (act === "reply") {
    const text = row.querySelector(".msg-body")?.textContent
      || (row.querySelector(".att-img") ? "📷 Photo" : row.querySelector(".att-video") ? "🎥 Video"
        : row.querySelector(".att-audio") ? "🎵 Audio" : "📎 File");
    editing = null;
    replyTo = { id: id, name: row.dataset.author, text: text.slice(0, 80) };
    showBar();
    input.focus();
  } else if (act === "copy") {
    navigator.clipboard?.writeText(row.querySelector(".msg-body").textContent);
  } else if (act === "edit") {
    replyTo = null;
    editing = { id: id };
    input.value = row.querySelector(".msg-body").textContent;
    showBar();
    input.focus();
  } else if (act === "delete") {
    if (confirm("Delete this message for everyone?")) socket.emit(log.dataset.delete, { message_id: id });
  }
});

// ---------- attachments, emoji and GIFs ----------
async function upload(files, gifUrl) {
  if (!log.dataset.attach) return;
  const data = new FormData();
  Array.from(files || []).forEach((file) => data.append("files", file));
  if (gifUrl) data.append("gif", gifUrl);
  if (replyTo) data.append("reply_to", replyTo.id);
  form.classList.add("uploading");
  try {
    const res = await fetch(log.dataset.attach, { method: "POST", body: data });
    if (!res.ok) {
      const info = await res.json().catch(() => ({}));
      alert(info.error || "Couldn't send that. Try again.");
    } else {
      clearComposer(true);
    }
  } catch (e) {
    alert("Couldn't send that. Check your connection and try again.");
  }
  form.classList.remove("uploading");
}

const fileInput = document.getElementById("chat-file");
const panel = document.getElementById("picker-panel");

if (form && fileInput && panel) {
  document.getElementById("btn-attach").addEventListener("click", () => fileInput.click());
  fileInput.addEventListener("change", () => { upload(fileInput.files); fileInput.value = ""; });

  // One panel, two tabs (like WhatsApp): all emoji, and GIFs.
  const picker = panel.querySelector("emoji-picker");
  const tabs = panel.querySelectorAll("[data-tab]");
  const panes = panel.querySelectorAll("[data-pane]");
  function showTab(name) {
    tabs.forEach((t) => t.classList.toggle("on", t.dataset.tab === name));
    panes.forEach((p) => { p.hidden = p.dataset.pane !== name; });
    if (name === "gif") loadGifs("");
  }
  tabs.forEach((t) => t.addEventListener("click", () => showTab(t.dataset.tab)));

  document.getElementById("btn-emoji").addEventListener("click", () => {
    panel.hidden = !panel.hidden;
    if (!panel.hidden) {
      picker.className = document.documentElement.dataset.theme === "dark" ? "dark" : "light";
      showTab("emoji");
    }
  });
  picker.addEventListener("emoji-click", (event) => {
    const emoji = event.detail.unicode;
    const start = input.selectionStart ?? input.value.length;
    const end = input.selectionEnd ?? input.value.length;
    input.value = input.value.slice(0, start) + emoji + input.value.slice(end);
    input.focus();
    input.setSelectionRange(start + emoji.length, start + emoji.length);
  });

  const gifPane = panel.querySelector(".gif-pane");
  const gifQuery = document.getElementById("gif-q");
  const gifResults = document.getElementById("gif-results");
  let gifTimer, gifLast = null;
  async function loadGifs(query) {
    if (!gifResults || gifLast === query) return;
    gifLast = query;
    const key = gifPane.dataset.key;
    const url = query
      ? `https://api.giphy.com/v1/gifs/search?api_key=${key}&q=${encodeURIComponent(query)}&limit=24`
      : `https://api.giphy.com/v1/gifs/trending?api_key=${key}&limit=24`;
    try {
      const data = await (await fetch(url)).json();
      gifResults.innerHTML = "";
      if (!data.data || !data.data.length) {
        gifResults.textContent = data.meta && data.meta.status === 403 ? "That GIPHY key isn't valid." : "No GIFs found.";
        return;
      }
      data.data.forEach((g) => {
        const img = document.createElement("img");
        img.src = g.images.fixed_height_small.url;
        img.alt = g.title || "gif";
        img.addEventListener("click", () => { panel.hidden = true; upload([], g.images.fixed_height.url); });
        gifResults.appendChild(img);
      });
    } catch (e) {
      gifLast = null;
      gifResults.textContent = "Couldn't load GIFs right now.";
    }
  }
  gifQuery?.addEventListener("input", () => {
    clearTimeout(gifTimer);
    gifTimer = setTimeout(() => loadGifs(gifQuery.value.trim()), 350);
  });

  // Paste a picture, or drop files anywhere on the chat.
  input.addEventListener("paste", (event) => {
    const files = Array.from(event.clipboardData?.files || []);
    if (files.length) { event.preventDefault(); upload(files); }
  });
  const area = log.parentElement;
  area.addEventListener("dragover", (event) => event.preventDefault());
  area.addEventListener("drop", (event) => {
    event.preventDefault();
    if (event.dataTransfer.files.length) upload(event.dataTransfer.files);
  });

  document.addEventListener("click", (event) => {
    if (!event.target.closest(".chat-tools")) panel.hidden = true;
  });
  document.addEventListener("keydown", (event) => { if (event.key === "Escape") panel.hidden = true; });
}

stickToBottom();
