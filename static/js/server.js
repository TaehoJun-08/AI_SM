// Everything extra inside a server: message formatting (bold, code, spoilers, @mentions, custom emoji),
// embeds and polls from companions, reactions, pins, threads, side panels, member cards,
// slash-command and @mention autocomplete. chat.js draws the basic chat; this file dresses it up.
(function () {
  const SRV = window.SRV;
  if (!SRV) return;
  const socket = window.liveSocket;
  const log = document.getElementById("chat-log");
  const input = document.getElementById("chat-input");
  const $ = (sel, root = document) => root.querySelector(sel);
  const members = new Map(SRV.members.map((m) => [m.id, m]));
  const byName = new Map(SRV.members.map((m) => [m.name, m]));
  const roleByName = new Map(SRV.roles.map((r) => [r.name.toLowerCase(), r]));
  const channelByName = new Map(SRV.channels.map((c) => [c.name, c]));
  const me = members.get(SRV.me);
  const can = new Set(SRV.can);
  const el = (tag, cls, text) => {
    const node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text !== undefined) node.textContent = text;
    return node;
  };
  const srcOf = (path) => (/^https?:/.test(path) ? path : "/static/" + path);

  // ---------- formatting ----------
  const INLINE = /(`[^`\n]+`)|(\|\|[\s\S]+?\|\|)|(\*\*[\s\S]+?\*\*)|(__[\s\S]+?__)|(~~[\s\S]+?~~)|(\*[^*\n]+\*)|(https?:\/\/[^\s<]+)|(@[A-Za-z0-9_.]+)|(#[a-z0-9_-]+)|(:[A-Za-z0-9_]{2,20}:)/g;

  function mentionNode(word) {
    const name = word.slice(1);
    if (name === "everyone" || name === "here") return el("span", "mention all", word);
    const member = byName.get(name);
    if (member) {
      const node = el("span", "mention" + (member.id === SRV.me ? " me" : ""), "@" + (member.nick || member.name));
      node.dataset.card = member.id;
      return node;
    }
    const role = roleByName.get(name.toLowerCase());
    if (role) {
      const node = el("span", "mention role", word);
      node.style.setProperty("--rc", role.color);
      return node;
    }
    return null;
  }

  function inline(text, parent) {
    let last = 0;
    text.replace(INLINE, (match, code, spoiler, bold, under, strike, italic, link, mention, chan, emoji, offset) => {
      if (offset > last) parent.append(text.slice(last, offset));
      last = offset + match.length;
      let node = null;
      if (code) node = el("code", "fmt-code", match.slice(1, -1));
      else if (spoiler) { node = el("span", "spoiler"); inline(match.slice(2, -2), node); node.addEventListener("click", () => node.classList.toggle("revealed")); }
      else if (bold) { node = el("strong"); inline(match.slice(2, -2), node); }
      else if (under) { node = el("u"); inline(match.slice(2, -2), node); }
      else if (strike) { node = el("s"); inline(match.slice(2, -2), node); }
      else if (italic) { node = el("em"); inline(match.slice(1, -1), node); }
      else if (link) {
        const clean = match.replace(/[.,;:!?)]+$/, "");
        node = el("a", "fmt-link", clean);
        node.href = clean; node.target = "_blank"; node.rel = "noopener noreferrer";
        last -= match.length - clean.length;
      } else if (mention) node = mentionNode(match);
      else if (chan) {
        const c = channelByName.get(match.slice(1));
        if (c) { node = el("a", "mention chan", match); node.href = `/servers/${SRV.server}/${c.id}`; }
      } else if (emoji) {
        const path = SRV.emojis[match.slice(1, -1)];
        if (path) { node = el("img", "cemoji"); node.src = srcOf(path); node.alt = match; node.title = match; }
      }
      parent.append(node || match);
      return match;
    });
    if (last < text.length) parent.append(text.slice(last));
  }

  function format(target, raw) {
    target.dataset.raw = raw;
    target.textContent = "";
    const parts = raw.split(/```(?:[a-z0-9]*\n)?([\s\S]*?)```/);
    parts.forEach((part, i) => {
      if (i % 2) { const pre = el("pre", "fmt-pre"); pre.append(el("code", "", part.replace(/\n$/, ""))); target.append(pre); return; }
      const lines = part.split("\n");
      let quote = null;
      lines.forEach((line, n) => {
        const isQuote = /^>\s/.test(line);
        const holder = isQuote ? (quote || (quote = el("blockquote", "fmt-quote"))) : target;
        if (!isQuote && quote) { target.append(quote); quote = null; }
        inline(isQuote ? line.slice(2) : line, holder);
        if (n < lines.length - 1 && !isQuote) holder.append("\n");
        else if (isQuote && n < lines.length - 1) holder.append("\n");
      });
      if (quote) target.append(quote);
    });
  }

  // ---------- embeds, polls and progress bars ----------
  function pollNode(embed, msg, row) {
    const box = el("div", "poll");
    box.dataset.msg = msg;
    embed.poll.forEach((label, index) => {
      const button = el("button", "poll-opt");
      button.type = "button";
      button.dataset.choice = index;
      button.append(el("i", "poll-fill"), el("span", "poll-label", label), el("b", "poll-num", "0"));
      button.addEventListener("click", () => socket.emit("srv_vote", { message_id: msg, choice: index }));
      box.append(button);
    });
    box.append(el("small", "poll-total"));
    return box;
  }

  function paintVotes(row, votes) {
    const box = $(".poll", row);
    if (!box) return;
    const ids = Object.keys(votes);
    const counts = {};
    ids.forEach((u) => { counts[votes[u]] = (counts[votes[u]] || 0) + 1; });
    box.querySelectorAll(".poll-opt").forEach((button) => {
      const n = counts[button.dataset.choice] || 0;
      button.querySelector(".poll-fill").style.width = (ids.length ? (n * 100) / ids.length : 0) + "%";
      button.querySelector(".poll-num").textContent = n;
      button.classList.toggle("mine", String(votes[SRV.me]) === button.dataset.choice);
    });
    box.querySelector(".poll-total").textContent = ids.length + (ids.length === 1 ? " vote" : " votes");
  }

  function embedNode(embed, msg, row) {
    const card = el("div", "embed" + (embed.kind ? " " + embed.kind : ""));
    if (embed.color) card.style.setProperty("--ec", embed.color);
    if (embed.title) {
      const title = embed.url ? el("a", "embed-title", embed.title) : el("div", "embed-title", embed.title);
      if (embed.url) title.href = embed.url;
      card.append(title);
    }
    if (embed.description) { const d = el("div", "embed-desc"); format(d, embed.description); card.append(d); }
    if (embed.fields) {
      const grid = el("div", "embed-fields");
      embed.fields.forEach((f) => { const cell = el("div", f.inline ? "inline" : ""); cell.append(el("b", "", f.name), el("span", "", f.value)); grid.append(cell); });
      card.append(grid);
    }
    if (embed.progress) {
      const pct = Math.max(0, Math.min(100, (embed.progress.value * 100) / embed.progress.goal));
      const bar = el("div", "embed-progress");
      bar.append(el("div", "goal-bar"), el("small", "", embed.progress.label || Math.round(pct) + "%"));
      bar.firstChild.append(el("i"));
      bar.firstChild.firstChild.style.width = pct + "%";
      card.append(bar);
    }
    if (embed.poll) card.append(pollNode(embed, msg, row));
    if (embed.image) { const img = el("img", "embed-img"); img.src = embed.image; img.referrerPolicy = "no-referrer"; img.alt = ""; card.append(img); }
    if (embed.footer) card.append(el("small", "embed-foot", embed.footer));
    return card;
  }

  // ---------- reactions ----------
  function emojiNode(emoji) {
    if (emoji.startsWith(":") && SRV.emojis[emoji.slice(1, -1)]) {
      const img = el("img", "cemoji"); img.src = srcOf(SRV.emojis[emoji.slice(1, -1)]); img.alt = emoji; return img;
    }
    return document.createTextNode(emoji);
  }

  function paintReactions(row, list) {
    let bar = $(".rx-bar", row);
    if (!list || !list.length) { bar?.remove(); return; }
    if (!bar) { bar = el("div", "rx-bar"); $(".msg-main", row).append(bar); }
    bar.textContent = "";
    list.forEach((r) => {
      const chip = el("button", "rx" + (r.u.includes(SRV.me) ? " mine" : ""));
      chip.type = "button";
      chip.append(emojiNode(r.e), el("span", "", String(r.u.length)));
      chip.title = r.u.map((id) => (members.get(id) || {}).name || "someone").join(", ");
      chip.addEventListener("click", () => react(row, r.e));
      bar.append(chip);
    });
  }

  function react(row, emoji) { if (SRV.can.includes("react")) socket.emit("srv_react", { message_id: Number(row.dataset.id), emoji: emoji }); }

  // ---------- dressing one message ----------
  function dress(row, x) {
    row.dataset.dressed = "1";
    const body = $(".msg-body", row);
    if (body) format(body, body.dataset.raw ?? body.textContent);
    const main = $(".msg-main", row);
    const meta = $(".msg-meta", row);
    if (x.bot && !$(".bot-tag", row)) { meta.append(" ", el("em", "bot-tag", "BOT")); row.classList.add("is-bot"); }
    const raw = body ? body.dataset.raw : "";
    if (raw && ((me && new RegExp("(^|\\W)@" + me.name.replace(/[.]/g, "\\.") + "(?![\\w.])").test(raw)) || /(^|\W)@(everyone|here)\b/.test(raw))
        && Number(row.dataset.sender) !== SRV.me) row.classList.add("mentioned");
    row.classList.toggle("pinned", !!x.pinned);
    if (x.embed && !$(".embed", row)) {
      const card = embedNode(x.embed, Number(row.dataset.id), row);
      const att = $(".msg-att", main);
      att ? main.insertBefore(card, att) : main.append(card);
      if (x.embed.poll) paintVotes(row, x.votes || {});
    }
    paintReactions(row, x.reactions);
    paintThread(row, x.thread_id, x.thread_count);
  }

  function paintThread(row, id, count) {
    let chip = $(".thread-chip", row);
    if (!id) { chip?.remove(); return; }
    if (!chip) { chip = el("a", "thread-chip"); $(".msg-main", row).append(chip); }
    chip.href = `/servers/${SRV.server}/${id}`;
    chip.textContent = count ? `🧵 ${count} ${count === 1 ? "reply" : "replies"}` : "🧵 Open thread";
  }

  function dressRow(row) {
    if (row.dataset.dressed || !row.classList.contains("msg")) return;
    let x = {};
    try { x = JSON.parse(row.dataset.x || "{}"); } catch (e) { /* plain row */ }
    dress(row, x);
  }

  if (log) {
    log.querySelectorAll(".msg").forEach(dressRow);
    window.addEventListener("msg:add", (event) => {
      const { row, m } = event.detail;
      dress(row, { reactions: m.reactions, votes: m.votes, embed: m.embed, pinned: m.pinned, thread_id: m.thread_id,
                   thread_count: m.thread_count, bot: m.is_bot });
    });
    window.addEventListener("msg:edit", (event) => {
      const body = $(".msg-body", event.detail.row);
      if (body) format(body, event.detail.body);
    });
    const rowOf = (id) => log.querySelector(`.msg[data-id="${id}"]`);
    socket.on("reactions", (d) => { const row = rowOf(d.id); if (row) paintReactions(row, d.reactions); });
    socket.on("pinned", (d) => { rowOf(d.id)?.classList.toggle("pinned", !!d.pinned); });
    socket.on("votes", (d) => { const row = rowOf(d.id); if (row) paintVotes(row, d.votes); });
    socket.on("thread", (d) => { const row = rowOf(d.id); if (row) paintThread(row, d.thread_id, d.count); });
    socket.on("ephemeral", (d) => {
      if (d.channel_id !== SRV.channel) return;
      const row = el("div", "msg ephemeral");
      row.innerHTML = '<span class="avatar-sm">👁</span><div class="msg-main"><div class="msg-meta"><strong>Only you can see this</strong> <button type="button" class="eph-x">dismiss</button></div></div>';
      const text = el("div", "msg-body"); format(text, d.text); $(".msg-main", row).append(text);
      if (d.embed) $(".msg-main", row).append(embedNode(d.embed, 0, row));
      $(".eph-x", row).addEventListener("click", () => row.remove());
      log.append(row);
      log.scrollTop = log.scrollHeight;
    });
    const target = new URLSearchParams(location.search).get("msg");
    if (target) {
      const row = rowOf(target);
      if (row) { row.scrollIntoView({ block: "center" }); row.classList.add("flash"); setTimeout(() => row.classList.remove("flash"), 2500); }
    }
  }

  // ---------- right-click extras ----------
  window.msgMenuExtras = [
    { act: "react", label: "😀 Add reaction", show: (row) => can.has("react") && !row.classList.contains("ephemeral"),
      run: (row) => pickEmoji(row) },
    { act: "pin", label: "📌 Pin / unpin", show: () => can.has("manage_messages"),
      run: (row, id) => socket.emit("srv_pin", { message_id: id }) },
    { act: "thread", label: "🧵 Thread", show: () => SRV.threadable !== false,
      run: (row, id) => socket.emit("srv_thread", { message_id: id }, (res) => { if (res && res.thread_id) location.href = `/servers/${SRV.server}/${res.thread_id}`; }) },
    { act: "link", label: "🔗 Copy link",
      run: (row, id) => navigator.clipboard?.writeText(`${location.origin}/servers/${SRV.server}/${SRV.channel}?msg=${id}`) },
  ];

  let pickerPop = null;
  function pickEmoji(row) {
    pickerPop?.remove();
    pickerPop = el("div", "pop react-pop");
    const quick = el("div", "quick-rx");
    ["👍", "❤️", "😂", "🎉", "🔥", "👀"].forEach((e) => {
      const b = el("button", "", e); b.type = "button";
      b.addEventListener("click", () => { react(row, e); pickerPop.remove(); });
      quick.append(b);
    });
    Object.keys(SRV.emojis).slice(0, 8).forEach((name) => {
      const b = el("button"); b.type = "button"; b.append(emojiNode(`:${name}:`));
      b.addEventListener("click", () => { react(row, `:${name}:`); pickerPop.remove(); });
      quick.append(b);
    });
    const more = el("button", "", "＋"); more.type = "button";
    pickerPop.append(quick);
    quick.append(more);
    const picker = document.createElement("emoji-picker");
    picker.dataset.source = "/static/vendor/emoji-picker/data.json";
    picker.className = document.documentElement.dataset.theme === "dark" ? "dark" : "light";
    picker.hidden = true;
    more.addEventListener("click", () => { picker.hidden = false; more.remove(); });
    picker.addEventListener("emoji-click", (e) => { react(row, e.detail.unicode || `:${e.detail.name}:`); pickerPop.remove(); });
    customElements.whenDefined("emoji-picker").then(() => { picker.customEmoji = customList(); });
    pickerPop.append(picker);
    const rect = row.getBoundingClientRect();
    pickerPop.style.top = Math.max(60, Math.min(rect.top, window.innerHeight - 440)) + "px";
    pickerPop.style.left = Math.max(80, rect.left + 120) + "px";
    document.body.append(pickerPop);
    setTimeout(() => document.addEventListener("click", function close(e) {
      if (!pickerPop || pickerPop.contains(e.target)) return;
      pickerPop.remove(); document.removeEventListener("click", close);
    }), 0);
  }
  const customList = () => Object.entries(SRV.emojis).map(([name, path]) => ({ name: name, shortcodes: [name], url: srcOf(path) }));
  const composerPicker = $("#picker-panel emoji-picker");
  if (composerPicker && Object.keys(SRV.emojis).length) customElements.whenDefined("emoji-picker").then(() => { composerPicker.customEmoji = customList(); });

  // ---------- side panels (pins, threads, search, events) ----------
  const panel = $("#srv-panel");
  let openKind = null, openChannel = null;

  async function showPanel(kind, channel, q) {
    if (!panel) return;
    if (!q && openKind === kind && !panel.hidden) { panel.hidden = true; openKind = null; return; }
    openKind = kind; openChannel = channel;
    const url = `/servers/${SRV.server}/panel/${kind}?` + new URLSearchParams({ ...(channel ? { channel } : {}), ...(q ? { q } : {}) });
    const res = await fetch(url);
    if (!res.ok) return;
    panel.innerHTML = await res.text();
    panel.hidden = false;
    panel.querySelectorAll(".fmt").forEach((n) => format(n, n.textContent));
  }

  document.addEventListener("click", (event) => {
    const btn = event.target.closest("[data-panel]");
    if (btn) { event.preventDefault(); showPanel(btn.dataset.panel, btn.dataset.channel); return; }
    if (event.target.closest("[data-close-panel]")) { panel.hidden = true; openKind = null; return; }
    const post = event.target.closest("[data-post]");
    if (post && panel.contains(post)) {
      fetch(post.dataset.post, { method: "POST" }).then(() => {
        const kind = openKind, channel = openChannel;
        panel.hidden = true; openKind = null; showPanel(kind, channel);
      });
    }
  });
  panel?.addEventListener("submit", async (event) => {
    const form = event.target;
    if (form.dataset.ajax === undefined) return;
    event.preventDefault();
    await fetch(form.action, { method: "POST", body: new FormData(form) });
    const kind = openKind; panel.hidden = true; openKind = null; showPanel(kind, openChannel);
  });
  $("#srv-search")?.addEventListener("submit", (event) => {
    event.preventDefault();
    const q = event.target.q.value.trim();
    if (q.length >= 2) { openKind = null; showPanel("search", null, q); }
  });
  document.addEventListener("keydown", (event) => { if (event.key === "Escape" && panel) { panel.hidden = true; openKind = null; } });

  // ---------- member cards ----------
  const card = $("#member-card");
  document.addEventListener("click", async (event) => {
    const who = event.target.closest("[data-card]");
    if (!who) { if (card && !event.target.closest("#member-card")) card.hidden = true; return; }
    event.preventDefault();
    const res = await fetch(`/servers/${SRV.server}/member/${who.dataset.card}/card`);
    if (!res.ok) return;
    card.innerHTML = await res.text();
    const rect = who.getBoundingClientRect();
    card.hidden = false;
    card.style.top = Math.max(60, Math.min(rect.top, window.innerHeight - card.offsetHeight - 20)) + "px";
    card.style.left = Math.max(80, Math.min(rect.left - card.offsetWidth - 10 < 80 ? rect.right + 10 : rect.left - card.offsetWidth - 10, window.innerWidth - card.offsetWidth - 10)) + "px";
  });
  log?.addEventListener("click", (event) => {
    const name = event.target.closest(".msg-meta strong, .msg > .avatar-sm");
    const row = name && name.closest(".msg");
    if (!row || row.classList.contains("ephemeral") || !members.has(Number(row.dataset.sender))) return;
    name.dataset.card = row.dataset.sender;
  }, true);

  $("#set-nick")?.addEventListener("click", async (event) => {
    const button = event.currentTarget;
    const nick = prompt("Your nickname in this server (empty to clear):", button.dataset.nick);
    if (nick === null) return;
    const data = new FormData(); data.append("nickname", nick);
    await fetch(button.dataset.url, { method: "POST", body: data });
    location.reload();
  });

  // ---------- autocomplete: /commands, @mentions, :custom emoji ----------
  const pop = $("#slash-pop");
  let items = [], active = 0, token = null;

  function suggestions() {
    const caret = input.selectionStart ?? input.value.length;
    const before = input.value.slice(0, caret);
    let m = before.match(/^\/([a-z0-9_-]*)$/i);
    if (m) {
      token = { start: 0, end: caret, prefix: "/" };
      return SRV.commands.filter((c) => c.name.startsWith(m[1].toLowerCase())).slice(0, 8)
        .map((c) => ({ text: `/${c.name} `, label: `/${c.name}`, hint: `${c.usage ? c.usage + " · " : ""}${c.description}`, tag: c.bot }));
    }
    m = before.match(/(?:^|\s)@([A-Za-z0-9_.]*)$/);
    if (m) {
      token = { start: caret - m[1].length - 1, end: caret };
      const q = m[1].toLowerCase();
      const list = [...SRV.members.map((p) => ({ text: `@${p.name} `, label: `@${p.name}`, hint: p.nick || (p.bot ? "companion" : ""), tag: p.bot ? "BOT" : "" })),
        ...SRV.roles.filter((r) => !/\s/.test(r.name)).map((r) => ({ text: `@${r.name} `, label: `@${r.name}`, hint: "role", tag: "" }))];
      if (can.has("mention_all")) list.unshift({ text: "@everyone ", label: "@everyone", hint: "notify all members", tag: "" }, { text: "@here ", label: "@here", hint: "notify all members", tag: "" });
      return list.filter((i) => i.label.toLowerCase().slice(1).startsWith(q)).slice(0, 8);
    }
    m = before.match(/(?:^|\s):([A-Za-z0-9_]{2,})$/);
    if (m && Object.keys(SRV.emojis).length) {
      token = { start: caret - m[1].length - 1, end: caret };
      return Object.keys(SRV.emojis).filter((n) => n.toLowerCase().startsWith(m[1].toLowerCase())).slice(0, 8)
        .map((n) => ({ text: `:${n}: `, label: `:${n}:`, hint: "custom emoji", tag: "", emoji: SRV.emojis[n] }));
    }
    return [];
  }

  function drawPop() {
    items = input ? suggestions() : [];
    active = 0;
    if (!pop) return;
    pop.hidden = items.length === 0;
    pop.textContent = "";
    items.forEach((item, i) => {
      const row = el("button", "slash-item" + (i === 0 ? " on" : ""));
      row.type = "button";
      if (item.emoji) { const img = el("img", "cemoji"); img.src = srcOf(item.emoji); row.append(img); }
      row.append(el("b", "", item.label), el("span", "", item.hint));
      if (item.tag) row.append(el("em", "bot-tag", item.tag));
      row.addEventListener("mousedown", (e) => { e.preventDefault(); choose(i); });
      pop.append(row);
    });
  }

  function choose(i) {
    const item = items[i];
    if (!item) return;
    input.value = input.value.slice(0, token.start) + item.text + input.value.slice(token.end);
    const pos = token.start + item.text.length;
    input.setSelectionRange(pos, pos);
    input.focus();
    pop.hidden = true;
    items = [];
  }

  if (input && pop) {
    input.addEventListener("input", drawPop);
    input.addEventListener("click", drawPop);
    input.addEventListener("blur", () => setTimeout(() => { pop.hidden = true; }, 120));
    input.addEventListener("keydown", (event) => {
      if (pop.hidden || !items.length) return;
      if (event.key === "ArrowDown" || event.key === "ArrowUp") {
        event.preventDefault();
        active = (active + (event.key === "ArrowDown" ? 1 : items.length - 1)) % items.length;
        pop.querySelectorAll(".slash-item").forEach((n, i) => n.classList.toggle("on", i === active));
      } else if (event.key === "Tab" || event.key === "Enter") {
        if (event.key === "Enter" && token.prefix === "/" && input.value.trim() === items[active].label) return;   // already typed: let it send
        event.preventDefault(); choose(active);
      } else if (event.key === "Escape") { pop.hidden = true; event.stopPropagation(); }
    }, true);
  }
})();
