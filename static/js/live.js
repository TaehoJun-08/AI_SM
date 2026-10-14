// One socket for every logged-in page. The server pushes small events (see live.py) and this file
// updates whatever part of the current page they touch, so nothing needs a refresh.
const liveSocket = io();
window.liveSocket = liveSocket;

// ---------- helpers ----------
async function fetchDoc(url) {
  const res = await fetch(url, { credentials: "same-origin" });
  if (!res.ok) return { status: res.status };
  return { status: 200, doc: new DOMParser().parseFromString(await res.text(), "text/html") };
}

// Replace each matching piece of `from` with the fresh copy found in `to`.
function swapParts(from, to, selectors) {
  selectors.forEach((sel) => {
    const old = from.querySelector(sel);
    const fresh = to.querySelector(sel);
    if (old && fresh) old.replaceWith(document.importNode(fresh, true));
  });
}

// Many events can arrive at once (ten likes, a burst of messages). Refresh once.
const timers = {};
function soon(key, fn, wait = 150) {
  clearTimeout(timers[key]);
  timers[key] = setTimeout(fn, wait);
}

const postsOnPage = (id) => document.querySelectorAll(`.post[data-id="${id}"]`);

// ---------- messages: the chat list on the left ----------
liveSocket.on("inbox", (data) => {
  const open = location.pathname.match(/^\/messages\/(\d+)/);
  if (data.reload && open && Number(open[1]) === data.thread_id) {
    location.reload();   // a request was accepted: the composer has to appear
    return;
  }
  const list = document.querySelector(".dm-scroll");
  if (!list || new URLSearchParams(location.search).get("q")) return;   // don't disturb a search
  soon("inbox", async () => {
    const res = await fetch("/messages/list?active=" + (open ? open[1] : ""));
    if (res.ok) list.innerHTML = await res.text();
  });
});

// ---------- the feed: likes, comments, sponsorship, new and removed posts ----------
liveSocket.on("post_likes", (data) => {
  postsOnPage(data.id).forEach((post) => {
    const label = post.querySelector(".like-count");
    if (label) label.textContent = data.count + (data.count === 1 ? " like" : " likes");
  });
});

function addComment(post, data) {
  const list = post.querySelector(".comment-list");
  if (list && !list.querySelector(`[data-cid="${data.cid}"]`)) {
    list.querySelector(".no-comments")?.remove();
    const line = document.createElement("p");
    line.className = "caption";
    line.dataset.cid = data.cid;
    line.innerHTML = '<a class="post-user"></a> <span></span>';
    const who = line.querySelector("a");
    who.dataset.uid = data.uid;
    who.href = "/u/" + data.username;
    who.textContent = data.username;
    line.querySelector("span").textContent = data.body;
    const box = list.closest(".detail-scroll");
    const atEnd = !box || box.scrollHeight - box.scrollTop - box.clientHeight < 80;
    list.appendChild(line);
    if (box && atEnd) box.scrollTop = box.scrollHeight;
  }
}
window.liveAddComment = addComment;
liveSocket.on("post_comment", (data) => postsOnPage(data.id).forEach((post) => addComment(post, data)));

// Funding bar, Sponsor button, caption, author, pictures: fetch the fresh version and swap just those parts.
function refreshPost(id) {
  postsOnPage(id).forEach((post) => {
    soon("post" + id, async () => {
      const detail = post.classList.contains("post-detail");
      const got = await fetchDoc(detail ? `/p/${id}` : `/p/${id}/card`);
      if (!got.doc) return;
      const fresh = got.doc.querySelector(`.post[data-id="${id}"]`);
      if (!fresh) return;
      swapParts(post, fresh, [".post-head", ".post-actions", ".fund", ".caption", ".like-count", ".detail-info"]);
      const oldMedia = post.querySelector(".post-media");
      const newMedia = fresh.querySelector(".post-media");
      if (oldMedia && newMedia && oldMedia.dataset.sig !== newMedia.dataset.sig) {   // photos were added or removed
        oldMedia.replaceWith(document.importNode(newMedia, true));
        if (typeof watchVideos === "function") watchVideos(post);
      }
    });
  });
}
liveSocket.on("post_changed", (data) => refreshPost(data.id));

// Your own heart, when you like something in another tab.
liveSocket.on("post_liked", (data) => {
  postsOnPage(data.id).forEach((post) => {
    const btn = post.querySelector("[data-like]");
    if (!btn || typeof heartSvg !== "function") return;
    btn.classList.toggle("liked", data.liked);
    btn.innerHTML = heartSvg(data.liked);
  });
});

liveSocket.on("post_deleted", (data) => {
  postsOnPage(data.id).forEach((post) => {
    if (post.classList.contains("post-detail")) location.href = "/";
    else post.remove();
  });
});

// A new post: slide it in at the top, or offer a button if you've scrolled away.
let newPill = null;
async function pullNewPosts() {
  const feed = document.getElementById("feed");
  const first = feed.querySelector(".post");
  const after = first ? first.dataset.id : 0;
  const res = await fetch(`/feed/new?after=${after}`);
  if (!res.ok) return;
  const html = (await res.text()).trim();
  if (!html) return;
  feed.insertAdjacentHTML("afterbegin", html);
  document.querySelector("#feed-end .empty-state")?.remove();
  if (typeof watchVideos === "function") watchVideos(feed);
}
liveSocket.on("post_new", () => {
  const feed = document.getElementById("feed");
  if (!feed) return;
  if (window.scrollY < 150) { soon("newpost", pullNewPosts, 200); return; }
  if (newPill) return;
  newPill = document.createElement("button");
  newPill.className = "new-posts-pill";
  newPill.textContent = "✨ New posts";
  newPill.addEventListener("click", async () => {
    newPill.remove();
    newPill = null;
    window.scrollTo({ top: 0, behavior: "smooth" });
    await pullNewPosts();
  });
  document.body.appendChild(newPill);
});

// ---------- parts of a page that re-fetch themselves ----------
// A template marks a part with data-region="name" data-live="topic topic". When the server "touches"
// a topic, the page fetches itself again and swaps just those parts (unless you are typing in one).
const touched = new Set();
const busy = (el) => el.contains(document.activeElement) && /^(INPUT|TEXTAREA|SELECT)$/.test(document.activeElement.tagName);
const listensTo = (el) => (el.dataset.live || "").split(" ").some((t) => touched.has(t));

liveSocket.on("touch", (data) => {
  data.topics.forEach((t) => touched.add(t));
  if (![...document.querySelectorAll("[data-region]")].some(listensTo)) { touched.clear(); return; }
  soon("touch", async () => {
    const got = await fetchDoc(location.pathname + location.search);
    const topics = new Set(touched);
    touched.clear();
    if (!got.doc) return;
    document.querySelectorAll("[data-region]").forEach((el) => {
      if (!(el.dataset.live || "").split(" ").some((t) => topics.has(t)) || busy(el)) return;
      const fresh = got.doc.querySelector(`[data-region="${el.dataset.region}"]`);
      if (!fresh) return;
      const copy = document.importNode(fresh, true);
      el.replaceWith(copy);
      if (typeof watchVideos === "function") watchVideos(copy);
    });
  }, 250);
});

// Somebody changed their name, @username or photo: update every place that shows them.
liveSocket.on("user_changed", (u) => {
  document.querySelectorAll(`[data-uid="${u.id}"]`).forEach((el) => {
    el.textContent = u.username;
    if (el.tagName === "A") el.href = "/u/" + u.username;
  });
  const log = document.getElementById("chat-log");
  if (log) {
    const name = log.dataset.key === "thread_id" ? u.full_name : u.username;   // DMs show real names
    log.querySelectorAll(`.msg[data-sender="${u.id}"]`).forEach((row) => {
      row.dataset.author = name;
      const strong = row.querySelector("strong");
      if (strong) strong.textContent = name;
      const avatar = row.querySelector(".avatar-sm");
      if (!avatar) return;
      avatar.textContent = "";
      if (u.avatar) {
        const img = document.createElement("img");
        img.src = "/static/" + u.avatar;
        avatar.appendChild(img);
      } else {
        avatar.textContent = name[0].toUpperCase();
      }
    });
  }
  document.querySelectorAll(`.post[data-author="${u.id}"]`).forEach((post) => refreshPost(post.dataset.id));
});

// Theme, accent, calm mode, autoplay: changed in another tab or device.
liveSocket.on("prefs", (p) => {
  const root = document.documentElement;
  root.dataset.pref = p.theme;
  root.dataset.accent = p.accent;
  root.dataset.calm = p.calm;
  root.dataset.autoplay = p.autoplay;
  let theme = p.theme;
  if (theme !== "light" && theme !== "dark") theme = matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
  root.dataset.theme = theme;
  root.dataset.bsTheme = theme;
});

// ---------- activity page ----------
liveSocket.on("activity", () => {
  if (location.pathname !== "/activity") return;
  soon("activity", async () => {
    const got = await fetchDoc("/activity");
    const now = document.querySelector(".narrow");
    const fresh = got.doc && got.doc.querySelector(".narrow");
    if (now && fresh) now.innerHTML = fresh.innerHTML;
  });
});

// ---------- servers: channels, roles and members ----------
liveSocket.on("server_changed", (data) => {
  const here = location.pathname.match(/^\/servers\/(\d+)/);
  if (!here || Number(here[1]) !== data.server_id) return;
  const settingsPage = document.querySelector(".srv-settings");
  if (!document.querySelector(".srv-channels") && !settingsPage) return;
  soon("server", async () => {
    const got = await fetchDoc(location.pathname + location.search);
    if (!got.doc) {
      // channel gone or no longer allowed: fall back to the server, or to the list if we were removed
      const server = await fetchDoc(`/servers/${data.server_id}`);
      location.href = server.doc ? `/servers/${data.server_id}` : "/servers/";
      return;
    }
    if (settingsPage) {                                        // roles, channels and members for admins
      const main = document.querySelector(".set-main");
      if (!main || !busy(main)) swapParts(document, got.doc, [".set-nav-inner", ".set-main"]);
      return;
    }
    if (!!got.doc.querySelector("#chat-form") !== !!document.querySelector("#chat-form")) {
      location.reload();                                       // permission to write changed
      return;
    }
    swapParts(document, got.doc, [".srv-rail", ".srv-name span", ".srv-scroll", ".srv-members", ".srv-chat-head", ".srv-me"]);
  });
});

// ---------- notifications: red badges, tab title, background-tab alerts ----------
const baseTitle = document.title.replace(/^\(\d+\) /, "");

function setBadge(el, n) {
  el.textContent = n > 99 ? "99+" : n;
  el.hidden = !n;
}

function updateTitle() {
  let total = 0;
  document.querySelectorAll("[data-badge]").forEach((el) => { if (!el.hidden) total += parseInt(el.textContent, 10) || 0; });
  document.title = (total ? `(${total > 99 ? "99+" : total}) ` : "") + baseTitle;
}

async function refreshBadges() {
  try {
    const res = await fetch("/live/counts");
    if (!res.ok) return;
    const c = await res.json();
    const top = { dms: c.dms, activity: c.activity, servers: c.server_total };
    document.querySelectorAll("[data-badge]").forEach((el) => setBadge(el, top[el.dataset.badge] || 0));
    document.querySelectorAll("[data-badge-server]").forEach((el) => setBadge(el, c.servers[el.dataset.badgeServer] || 0));
    document.querySelectorAll("[data-badge-channel]").forEach((el) => setBadge(el, c.channels[el.dataset.badgeChannel] || 0));
    updateTitle();
  } catch (e) { /* offline: the next event will try again */ }
}
document.addEventListener("DOMContentLoaded", updateTitle);
liveSocket.on("badges", () => soon("badges", refreshBadges, 100));

function desktopAlert(n) {
  if (!("Notification" in window) || Notification.permission !== "granted" || !document.hidden) return;
  const alert = new Notification(n.title, { body: n.body, tag: n.key || n.kind });
  alert.onclick = () => { window.focus(); location.href = n.url; alert.close(); };
}

liveSocket.on("notify", (n) => {
  soon("badges", refreshBadges, 100);
  desktopAlert(n);
});

// Ask once (on the first click, because browsers need a gesture) to show alerts when the tab is in the background.
document.addEventListener("click", () => {
  try {
    if ("Notification" in window && Notification.permission === "default" && !localStorage.getItem("asked-alerts")) {
      localStorage.setItem("asked-alerts", "1");
      Notification.requestPermission();
    }
  } catch (e) { /* private window */ }
}, { once: true });

// ---------- who is online ----------
const STATUS_LABELS = { online: "Online", idle: "Idle", dnd: "Do not disturb", call: "In a call", offline: "Offline" };

liveSocket.on("presence", (data) => {
  document.querySelectorAll(`[data-presence="${data.id}"]`).forEach((dot) => {
    dot.className = "pdot s-" + data.status;
    dot.title = STATUS_LABELS[data.status] || "";
  });
});

// Five quiet minutes with no mouse, keys or touch: show as idle until they come back.
let away = false;
let awayTimer;
function setAway(value) {
  if (away === value) return;
  away = value;
  liveSocket.emit("presence_away", { away: value });
}
function stillHere() {
  setAway(false);
  clearTimeout(awayTimer);
  awayTimer = setTimeout(() => setAway(true), 5 * 60 * 1000);
}
["mousemove", "keydown", "click", "scroll", "touchstart"].forEach((name) =>
  addEventListener(name, stillHere, { passive: true }));
stillHere();

// The status picker in the side menu.
document.addEventListener("click", async (event) => {
  const choice = event.target.closest("[data-status]");
  if (!choice) return;
  const res = await fetch("/live/status", { method: "POST", body: new URLSearchParams({ status: choice.dataset.status }) });
  if (!res.ok) return;
  const { status } = await res.json();
  const names = { online: "Online", idle: "Idle", dnd: "Do not disturb", invisible: "Invisible" };
  document.querySelectorAll("[data-my-status]").forEach((dot) => { dot.className = "pdot solo s-" + status; });
  document.querySelectorAll("[data-my-status-label]").forEach((label) => { label.textContent = names[status]; });
});

// ---------- muting a chat, channel or server ----------
document.addEventListener("click", async (event) => {
  const button = event.target.closest("[data-mute]");
  if (!button) return;
  const { mute: kind, id } = button.dataset;
  const res = await fetch("/live/mute", { method: "POST", body: new URLSearchParams({ kind, id }) });
  if (!res.ok) return;
  const { muted } = await res.json();
  document.querySelectorAll(`[data-mute="${kind}"][data-id="${id}"]`).forEach((el) => {
    el.dataset.on = muted ? "1" : "0";
    if (el.dataset.onText) el.textContent = muted ? el.dataset.onText : el.dataset.offText;
  });
  refreshBadges();
});

// ---------- voice and video calls ----------
// The call itself lives in its own window (/call/<key>) so it keeps going while you browse.
function openCall(key, video, ring) {
  const url = `/call/${key}?video=${video ? 1 : 0}${ring ? "&ring=1" : ""}`;
  const win = window.open(url, "call-" + key, "width=1000,height=680");
  if (!win) location.href = url;                       // pop-ups blocked: use this tab
}
document.addEventListener("click", (event) => {
  const button = event.target.closest("[data-call]");
  if (button) openCall(button.dataset.call, button.dataset.video === "1", button.dataset.ring === "1");
});

// A soft repeating ring, made with the browser's own audio (no file needed).
function startRingtone() {
  try {
    const ctx = new (window.AudioContext || window.webkitAudioContext)();
    const beep = () => {
      const osc = ctx.createOscillator();
      const gain = ctx.createGain();
      osc.frequency.value = 880;
      gain.gain.value = 0.06;
      osc.connect(gain).connect(ctx.destination);
      osc.start();
      setTimeout(() => osc.stop(), 320);
    };
    beep();
    const id = setInterval(() => { beep(); setTimeout(beep, 450); }, 2200);
    return () => { clearInterval(id); ctx.close(); };
  } catch (e) { return () => {}; }
}

let ringScreen = null;
function hideRing(key) {
  if (!ringScreen || (key && ringScreen.dataset.key !== key)) return;
  ringScreen.stop();
  clearTimeout(ringScreen.timer);
  ringScreen.remove();
  ringScreen = null;
}

liveSocket.on("call_ring", (call) => {
  if (document.getElementById("call-room")) return;    // already in a call window
  hideRing();
  const screen = document.createElement("div");
  screen.className = "ring-screen";
  screen.dataset.key = call.key;
  screen.innerHTML = '<div class="ring-card"><div class="ring-avatar"></div><b class="ring-name"></b>'
    + '<small class="ring-kind"></small><div class="ring-actions">'
    + '<button type="button" class="ring-btn decline" title="Decline">📵</button>'
    + '<button type="button" class="ring-btn accept" title="Accept">📞</button></div></div>';
  const avatar = screen.querySelector(".ring-avatar");
  if (call.from.avatar) {
    const img = document.createElement("img");
    img.src = "/static/" + call.from.avatar;
    avatar.appendChild(img);
  } else {
    avatar.textContent = call.from.name[0].toUpperCase();
  }
  screen.querySelector(".ring-name").textContent = "@" + call.from.name;
  screen.querySelector(".ring-kind").textContent = call.video ? "is video calling you…" : "is calling you…";
  screen.querySelector(".accept").addEventListener("click", () => { openCall(call.key, call.video, false); hideRing(call.key); });
  screen.querySelector(".decline").addEventListener("click", () => {
    liveSocket.emit("call_decline", { key: call.key });
    hideRing(call.key);
  });
  document.body.appendChild(screen);
  screen.stop = startRingtone();
  screen.timer = setTimeout(() => hideRing(call.key), 50000);
  ringScreen = screen;
  desktopAlert({ title: "@" + call.from.name + (call.video ? " is video calling" : " is calling"), body: "Tap to answer", url: "/messages/" + call.key.slice(3), key: call.key, kind: "dm" });
});
liveSocket.on("call_cancel", (data) => hideRing(data.key));

// "3 in a call" bar on a server channel
liveSocket.on("call_roster", (data) => {
  document.querySelectorAll(`[data-call-banner="${data.key}"]`).forEach((bar) => {
    bar.hidden = !data.count;
    bar.querySelector("[data-call-count]").textContent = data.count;
  });
});
