// Small touches that make the app feel alive: confetti, count-up numbers, button ripples,
// and a skeleton shimmer for images that are still loading. All of it is skipped for people
// who prefer reduced motion or switched on Calm mode.

(function () {
  const calm = () =>
    matchMedia("(prefers-reduced-motion: reduce)").matches || document.documentElement.dataset.calm === "1";

  // ---------- Confetti: window.confetti(x, y) bursts from a point (default: top middle) ----------
  let canvas, ctx, bits = [], raf = 0;
  const COLORS = ["#8b5cf6", "#ec4899", "#fb923c", "#38bdf8", "#facc15", "#34d399"];

  function frame() {
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    bits = bits.filter((b) => b.life > 0 && b.y < canvas.height + 30);
    for (const b of bits) {
      b.vy += 0.16; b.vx *= 0.992; b.x += b.vx; b.y += b.vy; b.rot += b.spin; b.life -= 1;
      ctx.save();
      ctx.translate(b.x, b.y); ctx.rotate(b.rot);
      ctx.globalAlpha = Math.min(1, b.life / 40);
      ctx.fillStyle = b.color;
      if (b.round) { ctx.beginPath(); ctx.arc(0, 0, b.size / 2, 0, 6.3); ctx.fill(); }
      else ctx.fillRect(-b.size / 2, -b.size / 4, b.size, b.size / 2);
      ctx.restore();
    }
    raf = bits.length ? requestAnimationFrame(frame) : 0;
    if (!bits.length) canvas.remove(), canvas = null;
  }

  window.confetti = function (x, y) {
    if (calm()) return;
    if (!canvas) {
      canvas = document.createElement("canvas");
      canvas.className = "confetti";
      canvas.width = innerWidth; canvas.height = innerHeight;
      ctx = canvas.getContext("2d");
      document.body.append(canvas);
    }
    x = x === undefined ? innerWidth / 2 : x;
    y = y === undefined ? innerHeight * 0.3 : y;
    for (let i = 0; i < 90; i++) {
      const angle = Math.random() * Math.PI * 2, speed = 3 + Math.random() * 7;
      bits.push({
        x, y, vx: Math.cos(angle) * speed, vy: Math.sin(angle) * speed - 4,
        size: 6 + Math.random() * 7, rot: Math.random() * 6, spin: (Math.random() - 0.5) * 0.4,
        color: COLORS[(Math.random() * COLORS.length) | 0], round: Math.random() < 0.3, life: 90 + Math.random() * 60,
      });
    }
    if (!raf) raf = requestAnimationFrame(frame);
  };

  // A happy flash message ("Offer sent! 💌", "You're connected! 🎉", "Your project is live! ✨") earns confetti.
  document.querySelectorAll(".app-body .flash-stack .alert").forEach((a) => {
    if (/[🎉✨💌]/u.test(a.textContent)) setTimeout(() => window.confetti(), 300);
  });

  // ---------- Count-up: <b data-countup="1250"> glides from 0 to its number ----------
  function countUp(node) {
    const target = Number(node.dataset.countup);
    if (!Number.isFinite(target) || calm() || target < 2) return;
    const suffix = node.dataset.suffix || "";
    const start = performance.now(), dur = 900;
    (function tick(now) {
      const t = Math.min(1, (now - start) / dur), eased = 1 - Math.pow(1 - t, 3);
      node.textContent = Math.round(target * eased).toLocaleString() + suffix;
      if (t < 1) requestAnimationFrame(tick);
    })(start);
  }
  const counter = new IntersectionObserver((entries) => entries.forEach((e) => {
    if (e.isIntersecting) { countUp(e.target); counter.unobserve(e.target); }
  }), { threshold: 0.4 });
  document.querySelectorAll("[data-countup]").forEach((n) => counter.observe(n));

  // ---------- Ripple on gradient buttons ----------
  document.addEventListener("pointerdown", (e) => {
    const btn = e.target.closest(".btn-primary, .sn-create, .tour-next");
    if (!btn || calm()) return;
    const r = btn.getBoundingClientRect(), size = Math.max(r.width, r.height) * 1.6;
    const dot = document.createElement("span");
    dot.className = "ripple";
    dot.style.cssText = "width:" + size + "px;height:" + size + "px;left:" + (e.clientX - r.left - size / 2) +
      "px;top:" + (e.clientY - r.top - size / 2) + "px";
    btn.append(dot);
    setTimeout(() => dot.remove(), 650);
  });

  // ---------- Heart pops when a like is pressed ----------
  document.addEventListener("click", (e) => {
    const heart = e.target.closest("[data-like]");
    if (!heart || calm()) return;
    heart.classList.remove("pop"); void heart.offsetWidth; heart.classList.add("pop");
  });

  // ---------- Images shimmer until they have loaded ----------
  function watchImages(root) {
    root.querySelectorAll(".post-media img:not([data-seen])").forEach((img) => {
      img.dataset.seen = "1";
      if (img.complete) return;
      img.classList.add("img-loading");
      const done = () => img.classList.remove("img-loading");
      img.addEventListener("load", done, { once: true });
      img.addEventListener("error", done, { once: true });
    });
  }
  watchImages(document);
  const feed = document.getElementById("feed");
  if (feed) new MutationObserver(() => watchImages(feed)).observe(feed, { childList: true });
})();
