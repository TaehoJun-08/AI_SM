// First-run welcome tour: a spotlight moves over the side menu while a glass card explains each
// button. It shows once per person (remembered in this browser) and can be replayed from Settings.

(function () {
  const STEPS = [
    { title: "Welcome to Matchmaker ✨", text: "Creators share what they're building, sponsors back the ideas they love. Here's a 30-second look around." },
    { target: ".sn-create", title: "Share a project", text: "Post photos or videos of your idea, set milestones and let sponsors find you." },
    { target: ".sn-link[href='/']", title: "Your home feed", text: "Fresh projects from everyone. Double-tap a picture to cheer, or press Sponsor to make an offer." },
    { target: ".sn-link[href$='/search']", title: "Search", text: "Look up people, projects and topics in one place." },
    { target: ".sn-link[href*='/dms']", title: "Messages", text: "Chat privately with creators and sponsors, send pictures, even start a call." },
    { target: ".sn-link[href*='/activity']", title: "Activity", text: "Offers, likes and replies land here, so you never miss a sponsor." },
    { target: ".sn-link[href*='/servers']", title: "Servers", text: "Join or build a community space with channels, pitch boards, events and Companions." },
    { target: ".sn-bottom .sn-link", title: "Set your status", text: "Online, idle or do-not-disturb. Pick how you appear to others." },
    { target: ".sn-link[href*='/settings']", title: "Make it yours", text: "Switch light and dark mode, change colours and control your privacy." },
    { title: "You're all set 🎉", text: "Go share something, or find a project to back. You can replay this tour any time from Settings." },
  ];

  const key = () => "tour-done-" + (document.body.dataset.uid || "0");
  const seen = () => { try { return localStorage.getItem(key()) === "1"; } catch (e) { return true; } };
  const remember = () => { try { localStorage.setItem(key(), "1"); } catch (e) { /* private window */ } };

  let root, spot, card, index, steps, keyHandler;

  function el(tag, cls, html) {
    const node = document.createElement(tag);
    if (cls) node.className = cls;
    if (html !== undefined) node.innerHTML = html;
    return node;
  }

  function usable(step) {
    if (!step.target) return true;
    const node = document.querySelector(step.target);
    return !!(node && node.offsetParent !== null);
  }

  function place() {
    const step = steps[index];
    const node = step.target && document.querySelector(step.target);
    const pad = 8;
    if (!node) {
      spot.classList.add("none");
      card.style.left = "50%"; card.style.top = "50%";
      card.style.transform = "translate(-50%, -50%)";
      return;
    }
    const r = node.getBoundingClientRect();
    spot.classList.remove("none");
    spot.style.left = r.left - pad + "px"; spot.style.top = r.top - pad + "px";
    spot.style.width = r.width + pad * 2 + "px"; spot.style.height = r.height + pad * 2 + "px";
    spot.style.borderRadius = Math.min(r.height / 2 + pad, 28) + "px";

    const cw = card.offsetWidth, ch = card.offsetHeight, gap = 18;
    const wide = innerWidth > 800;
    let left, top;
    if (wide) {                                   // side menu is on the left: card goes to its right
      left = r.right + gap + pad;
      top = Math.min(Math.max(12, r.top + r.height / 2 - ch / 2), innerHeight - ch - 12);
    } else {                                      // menu is a bottom bar: card goes above it
      left = Math.min(Math.max(12, r.left + r.width / 2 - cw / 2), innerWidth - cw - 12);
      top = Math.max(12, r.top - ch - gap - pad);
    }
    card.style.left = left + "px"; card.style.top = top + "px"; card.style.transform = "none";
  }

  function show(i) {
    index = i;
    const step = steps[i];
    const last = i === steps.length - 1;
    card.classList.remove("in");
    void card.offsetWidth;
    card.innerHTML =
      '<div class="tour-dots">' + steps.map((_, n) => '<i class="' + (n === i ? "on" : n < i ? "past" : "") + '"></i>').join("") + "</div>" +
      "<h5>" + step.title + "</h5><p>" + step.text + "</p>" +
      '<div class="tour-actions">' +
      (last ? "" : '<button class="tour-skip" data-tour="skip">Skip</button>') +
      '<span class="tour-gap"></span>' +
      (i > 0 ? '<button class="tour-back" data-tour="back">Back</button>' : "") +
      '<button class="tour-next" data-tour="next">' + (last ? "Let's go" : i === 0 ? "Show me" : "Next") + "</button></div>";
    place();
    card.classList.add("in");
    if (last && window.confetti) setTimeout(() => window.confetti(), 250);
  }

  function finish() {
    remember();
    document.removeEventListener("keydown", keyHandler);
    removeEventListener("resize", place);
    root.classList.add("out");
    setTimeout(() => root.remove(), 350);
  }

  function start(force) {
    if (document.querySelector(".tour")) return;
    steps = STEPS.filter(usable);
    if (!force && seen()) return;
    root = el("div", "tour");
    spot = el("div", "tour-spot none");
    card = el("div", "tour-card");
    root.append(spot, card);
    root.addEventListener("click", (e) => {
      const act = e.target.closest("[data-tour]");
      if (!act) return;
      const a = act.dataset.tour;
      if (a === "skip") finish();
      else if (a === "back") show(index - 1);
      else if (index >= steps.length - 1) finish();
      else show(index + 1);
    });
    keyHandler = (e) => {
      if (e.key === "Escape") finish();
      else if (e.key === "ArrowRight" || e.key === "Enter") { index >= steps.length - 1 ? finish() : show(index + 1); }
      else if (e.key === "ArrowLeft" && index > 0) show(index - 1);
    };
    document.addEventListener("keydown", keyHandler);
    addEventListener("resize", place);
    document.body.append(root);
    requestAnimationFrame(() => root.classList.add("on"));
    show(0);
  }

  window.startTour = () => start(true);
  // Auto-start only on the home feed, a moment after the page settles.
  if (document.body.dataset.tour === "auto") setTimeout(() => start(false), 1100);
  document.addEventListener("click", (e) => { if (e.target.closest("[data-start-tour]")) start(true); });
})();
