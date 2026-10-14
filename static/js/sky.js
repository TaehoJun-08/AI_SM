// Servers home: one cinematic orbital shot (NASA Earth at night, sunrise on the limb, a backlit station)
// plus the Discord-style "add a server" / "explore" dialogs.
(function () {
  "use strict";

  // =====================================================================
  //  Dialogs (Discord style: "Create a server" -> Create my own / Join, plus Explore)
  // =====================================================================
  const dlgAdd = document.getElementById("dlg-add"), dlgExp = document.getElementById("dlg-explore");
  function showStep(name) {
    dlgAdd.querySelectorAll("[data-step]").forEach(s => s.classList.toggle("on", s.dataset.step === name));
    const f = dlgAdd.querySelector(`[data-step="${name}"] input[name=name], [data-step="${name}"] input[name=code]`);
    if (f) setTimeout(() => f.focus(), 40);
  }
  function openFromHash() {
    const h = location.hash.slice(1);
    if (h === "explore") { if (!dlgExp.open) dlgExp.showModal(); }
    else if (h === "create" || h === "join") { showStep(h === "join" ? "join" : "choose"); if (!dlgAdd.open) dlgAdd.showModal(); }
  }
  const inside = (e, d) => { const r = d.getBoundingClientRect(); return e.clientX >= r.left && e.clientX <= r.right && e.clientY >= r.top && e.clientY <= r.bottom; };
  [dlgAdd, dlgExp].forEach(d => {
    d.addEventListener("click", e => {
      if (e.target.closest("[data-close]") || (e.target === d && !inside(e, d))) d.close();
      const go = e.target.closest("[data-go]"); if (go) showStep(go.dataset.go);
    });
    d.addEventListener("close", () => { history.replaceState(null, "", location.pathname + location.search); showStep("choose"); });
  });
  window.addEventListener("hashchange", openFromHash);
  if (location.hash) openFromHash();
  else if (dlgAdd.hasAttribute("data-auto")) { showStep("choose"); dlgAdd.showModal(); }

  // =====================================================================
  //  The scene
  // =====================================================================
  const cv = document.getElementById("scene");
  if (!cv) return;
  const ctx = cv.getContext("2d");
  const TAU = Math.PI * 2, PI = Math.PI;
  const clamp = (v, a, b) => v < a ? a : v > b ? b : v;
  const smooth = (a, b, x) => { const t = clamp((x - a) / (b - a), 0, 1); return t * t * (3 - 2 * t); };
  let seed = 20261015;                                    // fixed seed: the same shot on every visit
  const R = () => { seed |= 0; seed = seed + 0x6D2B79F5 | 0; let t = Math.imul(seed ^ seed >>> 15, 1 | seed); t = t + Math.imul(t ^ t >>> 7, 61 | t) ^ t; return ((t ^ t >>> 14) >>> 0) / 4294967296; };
  const rnd = (a, b) => a + R() * (b - a);

  const IMG = document.documentElement.dataset.spaceImg || "/static/img/space/";
  let W = 0, H = 0, dpr = 1, geo = null;
  let bgStars = null, earthBuf = null, stationBuf = null, grain = null, tEarth = -1, ready = false;
  let dust = [], ships = [], nextShip = 0, raf = 0, last = 0, T = 0, mx = 0, my = 0, cmx = 0, cmy = 0;
  const MARGIN = 44;

  function mk(w, h, scale) {
    const c = document.createElement("canvas"); c.width = Math.ceil(w * scale); c.height = Math.ceil(h * scale);
    const g = c.getContext("2d"); g.scale(scale, scale); return [c, g];
  }

  // ---------- layout: everything is placed relative to the canvas ----------
  function layout() {
    const Rp = W * 1.05, Cx = W * .5, Cy = H * .68 + Rp;
    const limbY = x => Cy - Math.sqrt(Rp * Rp - (x - Cx) * (x - Cx));
    const sunX = W * .8, sunY = limbY(sunX) - H * .006;
    const dx = sunX - Cx, dy = -(sunY - Cy), dl = Math.hypot(dx, dy), beta = 1.2;
    geo = { Rp, Cx, Cy, sunX, sunY, S: [dx / dl * Math.cos(beta), dy / dl * Math.cos(beta), -Math.sin(beta)] };
  }

  // ---------- stars ----------
  function paintStars() {
    const w = W + MARGIN * 2, h = H + MARGIN * 2, [c, g] = mk(w, h, dpr);
    g.fillStyle = "#010208"; g.fillRect(0, 0, w, h);
    const gr = g.createRadialGradient(w * .78, h * .6, 0, w * .78, h * .6, w * .7);   // faint warm haze toward the sunrise
    gr.addColorStop(0, "rgba(60,36,40,.28)"); gr.addColorStop(1, "rgba(0,0,0,0)");
    g.fillStyle = gr; g.fillRect(0, 0, w, h);
    const ang = -.42, cx = w * .4, cy = h * .3;                 // a faint milky-way band
    for (let i = 0; i < w * h / 380; i++) {
      const t = (R() * 2 - 1) * w, off = (R() + R() + R() - 1.5) * h * .2;
      g.fillStyle = `rgba(200,210,235,${.03 + R() * .2})`;
      g.fillRect(cx + Math.cos(ang) * t - Math.sin(ang) * off, cy + Math.sin(ang) * t + Math.cos(ang) * off, 1, 1);
    }
    for (let i = 0; i < w * h / 1500; i++) {
      const m = R() < .04, s = m ? 1.1 + R() * .9 : .5 + R() * .6, tint = R();
      g.fillStyle = tint < .25 ? "#cfe0ff" : tint < .4 ? "#ffe9cf" : "#ffffff"; g.globalAlpha = (m ? .8 : .25) + R() * .6;
      g.fillRect(R() * w, R() * h, s, s);
    }
    g.globalAlpha = 1;
    bgStars = c;
  }

  // =====================================================================
  //  Earth: real NASA imagery wrapped on a sphere, lit by a sun that is just behind the limb
  // =====================================================================
  const LIN = new Float32Array(256); for (let i = 0; i < 256; i++) LIN[i] = Math.pow(i / 255, 2.2);
  const TM = new Uint8ClampedArray(1100);                    // filmic tone-map + gamma lookup (x = i / 128)
  for (let i = 0; i < 1100; i++) TM[i] = Math.pow(1 - Math.exp(-i / 128 * 1.15), 1 / 2.2) * 255;
  const tm = x => TM[x <= 0 ? 0 : x >= 8.5 ? 1099 : (x * 128) | 0];
  let sr = 0, sg = 0, sb = 0;
  function sampler(img, w, h) {
    const c = document.createElement("canvas"); c.width = w; c.height = h;
    const g = c.getContext("2d", { willReadFrequently: true }); g.drawImage(img, 0, 0, w, h);
    const d = g.getImageData(0, 0, w, h).data;
    return (u, v) => {
      const fx = u * w - .5, fy = v * h - .5, ix = Math.floor(fx), iy = Math.floor(fy), tx = fx - ix, ty = fy - iy;
      const x0 = ((ix % w) + w) % w, x1 = (((ix + 1) % w) + w) % w, y0 = clamp(iy, 0, h - 1), y1 = clamp(iy + 1, 0, h - 1);
      const i00 = (y0 * w + x0) * 4, i10 = (y0 * w + x1) * 4, i01 = (y1 * w + x0) * 4, i11 = (y1 * w + x1) * 4;
      const a = (1 - tx) * (1 - ty), b = tx * (1 - ty), c2 = (1 - tx) * ty, d2 = tx * ty;
      sr = d[i00] * a + d[i10] * b + d[i01] * c2 + d[i11] * d2;
      sg = d[i00 + 1] * a + d[i10 + 1] * b + d[i01 + 1] * c2 + d[i11 + 1] * d2;
      sb = d[i00 + 2] * a + d[i10 + 2] * b + d[i01 + 2] * c2 + d[i11 + 2] * d2;
    };
  }
  let tex = null;
  function loadTextures(done) {
    const names = ["day", "clouds", "night"], got = {};
    let n = 0;
    names.forEach(k => {
      const im = new Image();
      im.onload = () => { got[k] = im; if (++n === names.length) { tex = { day: sampler(got.day, 2048, 1024), cloud: sampler(got.clouds, 2048, 1024), night: sampler(got.night, 2048, 1024) }; done(); } };
      im.onerror = () => { if (++n === names.length && got.day) { done(); } };
      im.src = IMG + k + ".jpg";
    });
  }

  function buildEarth() {
    if (!tex) return;
    const sc = Math.min(.8, 1500 / (W + MARGIN * 2)), bw = Math.ceil((W + MARGIN * 2) * sc), bh = Math.ceil((H + MARGIN * 2) * sc);
    const [c, g] = mk(bw, bh, 1), img = g.createImageData(bw, bh), out = img.data;
    const { Rp, Cx, Cy, S } = geo, lat0 = -.55, lon0 = .3, cP = Math.cos(lat0), sP = Math.sin(lat0);
    const s2 = Math.hypot(S[0], S[1]);
    for (let j = 0; j < bh; j++) {
      const wy = j / sc - MARGIN, dy = (wy - Cy) / Rp;
      for (let i = 0; i < bw; i++) {
        const wx = i / sc - MARGIN, dx = (wx - Cx) / Rp, r2 = dx * dx + dy * dy, o = (j * bw + i) * 4;
        let cr, cg, cb, a = 255;
        if (r2 >= 1) {                                                   // atmosphere glow outside the disc
          const r = Math.sqrt(r2), h = (r - 1) / .04;
          if (h > 3.2) continue;
          const base = Math.exp(-h * 3.0), core = Math.exp(-h * 9);
          const dirx = dx / r, diry = -dy / r, sd = (dirx * S[0] + diry * S[1]) / s2;
          const sun = Math.pow(clamp(.5 + .5 * sd, 0, 1), 3) + .035, warm = Math.pow(clamp(sd, 0, 1), 24) * .9;
          const k = (base * .8 + core * .7) * sun * 2.1;
          const lr = k * (.16 * (1 - warm) + 1.0 * warm), lg = k * (.4 * (1 - warm) + .5 * warm), lb = k * (1.0 * (1 - warm) + .2 * warm);
          const tr = tm(lr), tg = tm(lg), tb = tm(lb), mxc = Math.max(tr, tg, tb);
          a = Math.min(255, mxc * 1.25); if (a < 2) continue;
          out[o] = tr; out[o + 1] = tg; out[o + 2] = tb; out[o + 3] = a;
          continue;
        }
        const nz = Math.sqrt(1 - r2), nx = dx, ny = -dy;
        const d = nx * S[0] + ny * S[1] + nz * S[2];
        const y2 = ny * cP + nz * sP, z2 = -ny * sP + nz * cP;
        const lat = Math.asin(clamp(y2, -1, 1)), lon = lon0 + Math.atan2(nx, z2);
        let u = lon / TAU + .5; u -= Math.floor(u); const v = .5 - lat / PI;
        tex.day(u, v);
        const r0 = sr, g0 = sg, b0 = sb, oc = clamp((b0 - Math.max(r0, g0) - 8) / 40, 0, 1), shallow = clamp((r0 + g0) / 130, 0, 1);
        let lr = LIN[r0 | 0] * 1.2, lg = LIN[g0 | 0] * 1.12, lb = LIN[b0 | 0];
        lr = lr * (1 - oc) + (.004 + .02 * shallow) * oc; lg = lg * (1 - oc) + (.02 + .08 * shallow) * oc; lb = lb * (1 - oc) + (.08 + .14 * shallow) * oc;
        tex.cloud(u, v);
        const cl = smooth(.22, .92, sr / 255) * .93, lit = d > 0 ? d : 0, I = 2.7;
        cr = (lr * (1 - cl) + cl * .95) * lit * I; cg = (lg * (1 - cl) + cl * .95) * lit * I; cb = (lb * (1 - cl) + cl * .95) * lit * I;
        if (oc > 0 && d > 0) { const rv = 2 * d * nz - S[2], sp = Math.pow(clamp(rv, 0, 1), 70) * oc * 1.6 * (1 - cl); cr += sp; cg += sp * .95; cb += sp * .85; }
        const tw = Math.exp(-((d + .02) / .1) * ((d + .02) / .1)) * (.1 + cl * .25);   // orange sunset band on the terminator
        cr += tw * 1.0; cg += tw * .42; cb += tw * .16;
        cr += .012 * lr + .004; cg += .014 * lg + .006; cb += .024 * lb + .012;                           // faint earthshine / starlight
        tex.night(u, v);
        const L = Math.pow(Math.max(0, sr - 40) / 215, 1.5), vis = (1 - smooth(-.03, .1, d)) * (1 - cl * .7), kL = L * vis * 2.1;
        cr += kL; cg += kL * .62; cb += kL * .28;
        const rim = Math.pow(1 - nz, 3.2), ss = clamp(d * 1.2 + .25, 0, 1), at = rim * (.1 + 1.9 * ss), air = Math.pow(1 - nz, 6) * .1;
        cr += at * .15 + air * .2; cg += at * .4 + air * .5; cb += at * 1.0 + air;
        out[o] = tm(cr); out[o + 1] = tm(cg); out[o + 2] = tm(cb); out[o + 3] = a;
      }
    }
    g.putImageData(img, 0, 0);
    earthBuf = { c, sc };
  }

  // =====================================================================
  //  The station: lit only from behind, so it is mostly silhouette with hot rim light
  // =====================================================================
  const RIM = "rgba(255,232,198,.72)";
  function rim(g, x0, y0, x1, y1, w) {
    g.save(); g.shadowColor = "rgba(255,196,130,.95)"; g.shadowBlur = 6; g.strokeStyle = RIM; g.lineWidth = w || 1.3;
    g.beginPath(); g.moveTo(x0, y0); g.lineTo(x1, y1); g.stroke(); g.restore();
  }
  function cylH(g, x0, x1, cy, r, o) {
    o = o || {};
    const gr = g.createLinearGradient(0, cy - r, 0, cy + r);
    gr.addColorStop(0, "#2b3a5a"); gr.addColorStop(.06, "#171d2c"); gr.addColorStop(.3, "#0d111b"); gr.addColorStop(.7, "#090c14");
    gr.addColorStop(.9, "#241f22"); gr.addColorStop(.965, "#7a6650"); gr.addColorStop(1, "rgba(255,232,196,.95)");
    g.fillStyle = gr; g.fillRect(x0, cy - r, x1 - x0, 2 * r);
    if (o.gold) {                                                  // gold thermal blanket
      const [a, b] = o.gold, gg = g.createLinearGradient(0, cy - r, 0, cy + r);
      gg.addColorStop(0, "rgba(90,70,40,.6)"); gg.addColorStop(.3, "rgba(40,28,14,.9)"); gg.addColorStop(.8, "rgba(110,76,32,.85)"); gg.addColorStop(1, "rgba(255,214,140,.95)");
      g.fillStyle = gg; g.fillRect(a, cy - r, b - a, 2 * r);
      g.fillStyle = "rgba(0,0,0,.25)"; for (let x = a + 7; x < b; x += 11) g.fillRect(x, cy - r, 1, 2 * r);
    }
    const seg = o.seg || 56;
    for (let x = x0 + seg; x < x1 - 4; x += seg) {
      g.fillStyle = "rgba(0,0,0,.6)"; g.fillRect(x - 1.2, cy - r, 2.4, 2 * r);
      g.fillStyle = "rgba(255,225,185,.45)"; g.fillRect(x + 1.2, cy + r * .78, 1.2, r * .22);
    }
    const cap = g.createLinearGradient(x1 - r * .32, 0, x1 + r * .32, 0);
    cap.addColorStop(0, "#10141f"); cap.addColorStop(.55, "#4a4036"); cap.addColorStop(1, "rgba(255,226,186,.95)");
    g.fillStyle = cap; g.beginPath(); g.ellipse(x1, cy, r * .32, r, 0, -PI / 2, PI / 2); g.fill();
    g.fillStyle = "#0a0d15"; g.beginPath(); g.ellipse(x0, cy, r * .32, r, 0, PI / 2, PI * 1.5); g.fill();
    rim(g, x0, cy + r - .6, x1, cy + r - .6, 1.4);
    if (o.windows) for (let x = x0 + 14; x < x1 - 10; x += o.windows) {
      g.fillStyle = `rgba(255,214,150,${.5 + R() * .45})`; g.fillRect(x, cy - r * .1, 3.2, 2.2);
      g.fillStyle = "rgba(255,200,130,.12)"; g.fillRect(x - 2, cy - r * .1 - 2, 7.2, 6.2);
    }
  }
  function sphere(g, x, y, r) {
    const gr = g.createRadialGradient(x + r * .5, y + r * .62, r * .05, x - r * .1, y - r * .1, r * 1.15);
    gr.addColorStop(0, "rgba(255,232,196,.95)"); gr.addColorStop(.12, "#58493c"); gr.addColorStop(.35, "#141926"); gr.addColorStop(.8, "#080b12"); gr.addColorStop(1, "#1e2c47");
    g.fillStyle = gr; g.beginPath(); g.arc(x, y, r, 0, TAU); g.fill();
    g.strokeStyle = "rgba(0,0,0,.5)"; g.lineWidth = 1; g.beginPath(); g.ellipse(x, y, r, r * .22, -.3, 0, TAU); g.stroke();
  }
  function panel(g, x, y, len, wid, ang, skew) {
    g.save(); g.translate(x, y); g.rotate(ang); g.transform(1, 0, skew, 1, 0, 0);
    const base = g.createLinearGradient(0, 0, len, 0);
    base.addColorStop(0, "#0b1226"); base.addColorStop(.5, "#101a36"); base.addColorStop(1, "#0a1020");
    g.fillStyle = base; g.fillRect(0, -wid / 2, len, wid);
    g.strokeStyle = "rgba(120,150,220,.2)"; g.lineWidth = .6; g.beginPath();
    for (let i = 1; i < 14; i++) { const p = i * len / 14; g.moveTo(p, -wid / 2); g.lineTo(p, wid / 2); }
    for (let i = 1; i < 6; i++) { const q = -wid / 2 + i * wid / 6; g.moveTo(0, q); g.lineTo(len, q); }
    g.stroke();
    const sh = g.createLinearGradient(len * .15, -wid / 2, len * .75, wid / 2);   // specular sheen
    sh.addColorStop(0, "rgba(255,220,170,0)"); sh.addColorStop(.45, "rgba(255,225,185,.1)"); sh.addColorStop(.6, "rgba(160,190,255,.07)"); sh.addColorStop(1, "rgba(255,220,170,0)");
    g.fillStyle = sh; g.fillRect(0, -wid / 2, len, wid);
    g.strokeStyle = "rgba(90,74,56,.7)"; g.lineWidth = 1.2; g.strokeRect(0, -wid / 2, len, wid);
    rim(g, 0, wid / 2, len, wid / 2, 1.4);
    g.restore();
  }
  function dish(g, x, y, r, ang) {
    g.save(); g.translate(x, y); g.rotate(ang);
    g.fillStyle = "#1b2030"; g.beginPath(); g.ellipse(0, 0, r * .3, r, 0, 0, TAU); g.fill();
    g.strokeStyle = RIM; g.lineWidth = 1; g.shadowColor = "rgba(255,196,130,.8)"; g.shadowBlur = 5; g.beginPath(); g.ellipse(0, 0, r * .3, r, 0, -PI / 2, 0); g.stroke(); g.shadowBlur = 0;
    g.strokeStyle = "#2c3346"; g.lineWidth = 1.4; g.beginPath(); g.moveTo(0, 0); g.lineTo(r * 1.1, 0); g.stroke();
    g.fillStyle = "#cfd8ee"; g.fillRect(r * 1.1 - 1, -1.5, 3, 3);
    g.restore();
  }
  function truss(g, x0, x1, y, h) {
    g.fillStyle = "#07090f"; g.fillRect(x0, y - h, x1 - x0, 2 * h);
    g.strokeStyle = "#1c2233"; g.lineWidth = 1.5; g.beginPath();
    for (let x = x0; x < x1; x += 30) { g.moveTo(x, y - h); g.lineTo(x + 30, y + h); g.moveTo(x, y + h); g.lineTo(x + 30, y - h); g.moveTo(x, y - h); g.lineTo(x, y + h); }
    g.stroke();
    g.fillStyle = "rgba(0,0,0,.9)"; g.fillRect(x0, y - h * .35, x1 - x0, h * .7);
    rim(g, x0, y + h, x1, y + h, 1.6); g.strokeStyle = "rgba(70,90,140,.5)"; g.lineWidth = 1; g.beginPath(); g.moveTo(x0, y - h); g.lineTo(x1, y - h); g.stroke();
    for (let x = x0 + 80; x < x1; x += 160) { g.fillStyle = "#121722"; g.fillRect(x - 7, y - h - 4, 14, 2 * h + 8); rim(g, x - 7, y + h + 4, x + 7, y + h + 4, 1.2); }
  }
  function capsule(g, x, y, s) {
    g.save(); g.translate(x, y); g.scale(s, s);
    const gr = g.createLinearGradient(-14, 0, 14, 0); gr.addColorStop(0, "#0c0f18"); gr.addColorStop(.7, "#2a2e3c"); gr.addColorStop(1, "rgba(255,226,186,.95)");
    g.fillStyle = gr; g.beginPath(); g.moveTo(-9, 0); g.lineTo(9, 0); g.lineTo(15, 26); g.lineTo(-15, 26); g.closePath(); g.fill();
    g.fillStyle = "#0a0d15"; g.fillRect(-15, 26, 30, 6); g.fillStyle = "rgba(255,210,150,.8)"; g.fillRect(-3, 10, 6, 3);
    g.restore();
  }

  function paintStation() {
    const sw = Math.round(W * .62), sh = Math.round(H * .7), [c, g] = mk(sw, sh, dpr), u = sw / 1080;
    g.translate(sw / 2, sh / 2); g.scale(u, u);
    seed = 4242;
    panel(g, -470, -70, 270, 96, -1.58, .12);                         // far wing, left (behind everything)
    truss(g, -500, 520, 0, 15);
    // left wing: two panels reaching up, right wing: two reaching down
    panel(g, -430, -14, 250, 100, -1.57, .1); panel(g, -430, -272, 250, 100, -1.57, .1);
    panel(g, 330, 14, 230, 96, 1.57, -.1); panel(g, 330, 252, 230, 96, 1.57, -.1);
    // pressurised modules
    cylH(g, -250, 110, 62, 48, { gold: [-250, -130], windows: 22, seg: 60 });
    cylH(g, 185, 390, 62, 33, { seg: 51 });
    sphere(g, 148, 62, 54);
    g.fillStyle = "#0a0d15"; g.fillRect(100, 60, 94, 5);
    // vertical docking spurs
    g.save(); g.translate(-70, -24); g.rotate(-PI / 2); cylH(g, 0, 128, 0, 21, { seg: 42 }); g.restore();
    g.save(); g.translate(-170, 112); g.rotate(PI / 2); cylH(g, 0, 90, 0, 22, { seg: 45 }); g.restore();
    capsule(g, -170, 206, 1.1);
    // radiators
    panel(g, 120, 20, 150, 46, -.9, .0); panel(g, 176, 20, 150, 46, -1.1, .0);
    // dishes and antenna
    dish(g, 500, 0, 30, -.3); dish(g, -200, -15, 20, -1.1);
    g.strokeStyle = "#2a3144"; g.lineWidth = 1.5; g.beginPath(); g.moveTo(-70, -152); g.lineTo(-70, -210); g.moveTo(-84, -190); g.lineTo(-56, -190); g.stroke();
    rim(g, -70, -152, -70, -210, 1.2);
    // earthshine on the underside of the truss and modules
    const es = g.createLinearGradient(0, 40, 0, 130); es.addColorStop(0, "rgba(60,50,40,0)"); es.addColorStop(1, "rgba(120,90,60,.12)");
    g.fillStyle = es; g.fillRect(-260, 40, 520, 90);
    // navigation lights
    [[-500, 0, "#ff4040"], [520, 0, "#40ff80"], [-70, -212, "#ffffff"], [390, 62, "#ffffff"]].forEach(([x, y, col]) => { g.fillStyle = col; g.shadowColor = col; g.shadowBlur = 9; g.beginPath(); g.arc(x, y, 2.2, 0, TAU); g.fill(); g.shadowBlur = 0; });
    stationBuf = { c, w: sw, h: sh };
  }

  // =====================================================================
  //  Distant ships: tiny, dim, slow
  // =====================================================================
  function spawnShip(initial) {
    const dir = ships.length % 2 ? -1 : 1, big = !(ships.length % 3 === 2);
    const len = big ? 74 : 40, lane = big ? .17 + R() * .1 : .64 + R() * .06;
    ships.push({ x: initial ? W * .22 : (dir > 0 ? -len : W + len), y: H * lane, v: dir * (big ? 11 : 17), len, t: R() * 9, dir });
  }
  function drawShip(g, s) {
    g.save(); g.translate(s.x, s.y); g.scale(s.dir * s.len / 100, s.len / 100);
    const gr = g.createLinearGradient(0, -9, 0, 9);
    gr.addColorStop(0, "rgba(255,226,186,.9)"); gr.addColorStop(.12, "#2a2a30"); gr.addColorStop(.55, "#090b12"); gr.addColorStop(1, "#1f3a60");
    g.fillStyle = gr; g.beginPath(); g.moveTo(50, 0); g.lineTo(30, -6); g.lineTo(-34, -9); g.lineTo(-50, -6); g.lineTo(-50, 6); g.lineTo(-34, 9); g.lineTo(30, 6); g.closePath(); g.fill();
    g.fillStyle = "#0b0d14"; g.fillRect(-14, -14, 20, 6); g.fillRect(-14, 8, 20, 6);
    g.fillStyle = "rgba(255,214,150,.7)"; for (let i = 0; i < 9; i++) g.fillRect(-30 + i * 7, -2, 2, 1.4);
    g.globalCompositeOperation = "lighter";
    const eg = g.createRadialGradient(-52, 0, 0, -52, 0, 20); eg.addColorStop(0, "rgba(190,220,255,.95)"); eg.addColorStop(.25, "rgba(110,160,255,.4)"); eg.addColorStop(1, "rgba(60,90,255,0)");
    g.fillStyle = eg; g.fillRect(-72, -20, 40, 40);
    const tr = g.createLinearGradient(-52, 0, -130, 0); tr.addColorStop(0, "rgba(130,170,255,.28)"); tr.addColorStop(1, "rgba(130,170,255,0)");
    g.fillStyle = tr; g.fillRect(-130, -2.2, 78, 4.4);
    if (Math.floor(s.t * .9) % 2) { g.fillStyle = "#ff4a4a"; g.beginPath(); g.arc(-48, -7, 1.8, 0, TAU); g.fill(); }
    g.restore();
  }

  // =====================================================================
  //  Dust, grain and the per-frame composite
  // =====================================================================
  function makeDust() {
    seed = 99;
    dust = Array.from({ length: 46 }, () => {
      const near = R() < .28;
      return { x: R() * W, y: R() * H, r: near ? 5 + R() * 11 : .6 + R() * 1.1, d: near ? 2.4 + R() * 1.4 : 1.2 + R() * 1.2, a: near ? .035 + R() * .07 : .25 + R() * .4, vx: 2 + R() * 4, vy: -1 - R() * 2, near };
    });
  }
  function makeGrain() {
    const [c, g] = mk(256, 256, 1), im = g.createImageData(256, 256);
    for (let i = 0; i < im.data.length; i += 4) { const v = 96 + Math.random() * 64; im.data[i] = im.data[i + 1] = im.data[i + 2] = v; im.data[i + 3] = 255; }
    g.putImageData(im, 0, 0); grain = ctx.createPattern(c, "repeat");
  }

  function draw(dt) {
    T += dt; cmx += (mx - cmx) * Math.min(1, dt * 2.2); cmy += (my - cmy) * Math.min(1, dt * 2.2);
    const g = ctx, cx = Math.sin(T * .06) * 9 + cmx * 26, cy = Math.cos(T * .045) * 5 + cmy * 14;
    const zoom = 1 + .014 * (1 - Math.cos(T * .03)) / 2;
    g.setTransform(dpr, 0, 0, dpr, 0, 0); g.globalAlpha = 1; g.globalCompositeOperation = "source-over";
    g.fillStyle = "#000"; g.fillRect(0, 0, W, H);
    g.save(); g.translate(W / 2, H / 2); g.scale(zoom, zoom); g.translate(-W / 2, -H / 2);

    g.drawImage(bgStars, -MARGIN - cx * .1, -MARGIN - cy * .1, W + MARGIN * 2, H + MARGIN * 2);
    const sx = geo.sunX - cx * .25, sy = geo.sunY - cy * .25;
    // the sun itself, half hidden behind the planet's limb
    g.globalCompositeOperation = "lighter";
    const core = g.createRadialGradient(sx, sy, 0, sx, sy, 26);
    core.addColorStop(0, "rgba(255,255,250,1)"); core.addColorStop(.25, "rgba(255,246,220,.95)"); core.addColorStop(1, "rgba(255,220,160,0)");
    g.fillStyle = core; g.fillRect(sx - 30, sy - 30, 60, 60);
    g.globalCompositeOperation = "source-over";
    // the Earth (fades in once the NASA textures are processed)
    if (earthBuf) {
      g.globalAlpha = clamp((T - tEarth) / 1.8, 0, 1);
      g.drawImage(earthBuf.c, -MARGIN - cx * .35, -MARGIN - cy * .35, W + MARGIN * 2, H + MARGIN * 2);
      g.globalAlpha = 1;
    }
    // distant ships (silhouettes against the glow / sky)
    for (const s of ships) { g.save(); g.translate(-cx * .6, -cy * .6); drawShip(g, s); g.restore(); }
    // the station
    if (stationBuf) {
      const px = W * .38 - stationBuf.w / 2 - cx, py = H * .43 - stationBuf.h / 2 - cy;
      g.save(); g.translate(px + stationBuf.w / 2, py + stationBuf.h / 2); g.rotate(-.09 + Math.sin(T * .04) * .004); g.translate(-stationBuf.w / 2, -stationBuf.h / 2);
      g.drawImage(stationBuf.c, 0, 0, stationBuf.w, stationBuf.h); g.restore();
    }
    // sun bloom and anamorphic streak over everything
    g.globalCompositeOperation = "lighter";
    const bl = g.createRadialGradient(sx, sy, 0, sx, sy, Math.max(W, H) * .5);
    bl.addColorStop(0, "rgba(255,246,225,.7)"); bl.addColorStop(.04, "rgba(255,214,160,.38)"); bl.addColorStop(.14, "rgba(255,160,100,.14)"); bl.addColorStop(.4, "rgba(220,110,70,.04)"); bl.addColorStop(1, "rgba(0,0,0,0)");
    g.fillStyle = bl; g.fillRect(0, 0, W, H);
    const st = g.createLinearGradient(sx - W * .9, 0, sx + W * .9, 0);
    st.addColorStop(0, "rgba(120,170,255,0)"); st.addColorStop(.5, "rgba(170,205,255,.3)"); st.addColorStop(1, "rgba(120,170,255,0)");
    g.fillStyle = st; g.fillRect(sx - W * .9, sy - 1, W * 1.8, 2);
    const st2 = g.createLinearGradient(sx - W * .6, 0, sx + W * .6, 0);
    st2.addColorStop(0, "rgba(255,190,130,0)"); st2.addColorStop(.5, "rgba(255,200,150,.12)"); st2.addColorStop(1, "rgba(255,190,130,0)");
    g.fillStyle = st2; g.fillRect(sx - W * .6, sy - 9, W * 1.2, 18);
    for (const k of [-.9]) {                                   // faint lens ghosts across the frame
      const gx = sx + (W / 2 - sx) * (1 - k), gy = sy + (H / 2 - sy) * (1 - k), gr = 26 + Math.abs(k) * 22;
      const gg = g.createRadialGradient(gx, gy, gr * .2, gx, gy, gr); gg.addColorStop(0, "rgba(120,170,255,0)"); gg.addColorStop(.8, "rgba(120,170,255,.03)"); gg.addColorStop(1, "rgba(255,180,120,0)");
      g.fillStyle = gg; g.beginPath(); g.arc(gx, gy, gr, 0, TAU); g.fill();
    }
    // floating particles: sharp motes far, soft bokeh close (strong parallax, drifting)
    for (const p of dust) {
      p.x += p.vx * dt * p.d * .5; p.y += p.vy * dt * p.d * .5;
      if (p.x > W + 20) p.x = -20; if (p.y < -20) p.y = H + 20;
      const x = p.x - cx * p.d, y = p.y - cy * p.d, warm = clamp(x / W, 0, 1);
      if (p.near) {
        const gg = g.createRadialGradient(x, y, 0, x, y, p.r);
        gg.addColorStop(0, `rgba(${180 + warm * 75},${195 + warm * 20},${255 - warm * 70},${p.a})`); gg.addColorStop(.8, `rgba(${180 + warm * 75},${195 + warm * 20},${255 - warm * 70},${p.a * .8})`); gg.addColorStop(1, "rgba(0,0,0,0)");
        g.fillStyle = gg; g.beginPath(); g.arc(x, y, p.r, 0, TAU); g.fill();
      } else { g.fillStyle = `rgba(255,${230 - (1 - warm) * 20},${200 + (1 - warm) * 55},${p.a})`; g.fillRect(x, y, p.r, p.r); }
    }
    g.globalCompositeOperation = "source-over";
    g.restore();

    // film look: vignette, grain, soft fade-in
    const vg = g.createRadialGradient(W / 2, H / 2, Math.min(W, H) * .42, W / 2, H / 2, Math.max(W, H) * .78);
    vg.addColorStop(0, "rgba(0,0,0,0)"); vg.addColorStop(1, "rgba(0,0,4,.62)");
    g.fillStyle = vg; g.fillRect(0, 0, W, H);
    if (grain) {
      g.save(); g.globalCompositeOperation = "overlay"; g.globalAlpha = .1; g.translate(Math.random() * 256 | 0, Math.random() * 256 | 0);
      g.fillStyle = grain; g.fillRect(-256, -256, W + 512, H + 512); g.restore();
    }
    const fade = 1 - clamp(T / 1.6, 0, 1);
    if (fade > 0) { g.fillStyle = `rgba(0,0,0,${fade})`; g.fillRect(0, 0, W, H); }

    // traffic
    for (const s of ships) { s.x += s.v * dt; s.t += dt; }
    ships = ships.filter(s => s.x > -140 && s.x < W + 140);
    nextShip -= dt; if (nextShip <= 0 && ships.length < 2) { spawnShip(false); nextShip = 28 + Math.random() * 25; }
  }

  function frame(now) {
    const dt = Math.min(.05, (now - last) / 1000 || 0); last = now;
    draw(dt); raf = requestAnimationFrame(frame);
  }

  // ---------- boot ----------
  function fit() {
    dpr = Math.min(2, window.devicePixelRatio || 1);
    const r = cv.getBoundingClientRect();
    W = Math.max(480, r.width); H = Math.max(360, r.height);
    cv.width = Math.round(W * dpr); cv.height = Math.round(H * dpr);
    cancelAnimationFrame(raf);
    layout(); seed = 20261015; paintStars(); paintStation(); makeDust(); makeGrain();
    ships = []; spawnShip(true); nextShip = 20;
    const title = document.getElementById("sky-title"), sub = document.getElementById("sky-sub");
    if (title) title.textContent = "Orbital Station Helios";
    if (sub) sub.textContent = "Low Earth orbit · pick a server on the left, or press + to start your own";
    T = 0; last = performance.now();
    if (earthBuf) buildEarth();
    const motion = !window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    if (motion) raf = requestAnimationFrame(frame); else draw(2);
    if (!tex) loadTextures(() => { setTimeout(() => { buildEarth(); tEarth = T; if (!motion) draw(4); }, 30); });
    else if (!earthBuf) { buildEarth(); tEarth = T; }
  }
  window.addEventListener("mousemove", e => { mx = e.clientX / window.innerWidth - .5; my = e.clientY / window.innerHeight - .5; });
  let rt; window.addEventListener("resize", () => { clearTimeout(rt); rt = setTimeout(() => { fit(); }, 250); });
  document.addEventListener("visibilitychange", () => { cancelAnimationFrame(raf); if (!document.hidden) { last = performance.now(); raf = requestAnimationFrame(frame); } });
  fit();
})();
