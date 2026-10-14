// The call window: voice and video between browsers (WebRTC). Socket.IO only carries the
// "hello, here is how to reach me" messages; the sound and pictures go straight between people.
(() => {
  const root = document.getElementById("call-room");
  const key = root.dataset.key;
  const back = root.dataset.back;
  const isDm = root.dataset.kind === "dm";
  const wantVideo = root.dataset.video === "1";
  const shouldRing = root.dataset.ring === "1";
  const socket = window.liveSocket;
  const grid = document.getElementById("call-grid");
  const statusEl = document.getElementById("call-status");
  const timerEl = document.getElementById("call-timer");
  const errorEl = document.getElementById("call-error");
  const ICE = { iceServers: [{ urls: "stun:stun.l.google.com:19302" }, { urls: "stun:stun1.l.google.com:19302" }] };

  const local = new MediaStream();       // my microphone (and camera, when it is on)
  const peers = new Map();               // socket id -> connection + tile of another person
  const known = new Map();               // socket id -> {name, avatar, ...} as the server told us
  let micOn = true;
  let camTrack = null;
  let screenTrack = null;
  let myTile = null;
  let connectedAt = null;
  let timerHandle = null;
  let left = false;

  const setStatus = (text) => { statusEl.textContent = text; };
  const showError = (text) => { errorEl.textContent = text; errorEl.hidden = !text; };
  const send = (to, data) => socket.emit("call_signal", { key, to, data });
  const sendState = () => socket.emit("call_state", {
    key, muted: !micOn, video: !!camTrack, sharing: !!screenTrack,
  });

  // ---------- tiles ----------
  function makeTile(sid, info, mine) {
    const tile = document.createElement("div");
    tile.className = "tile no-video" + (mine ? " me" : "");
    tile.dataset.sid = sid;
    tile.innerHTML = '<video autoplay playsinline></video><div class="tile-avatar"></div>'
      + '<span class="tile-name"></span><span class="tile-mic" hidden>🔇</span>';
    const name = mine ? "You" : (info.name || "Guest");
    tile.querySelector(".tile-name").textContent = name;
    const avatar = tile.querySelector(".tile-avatar");
    if (info.avatar) {
      const img = document.createElement("img");
      img.src = "/static/" + info.avatar;
      avatar.appendChild(img);
    } else {
      avatar.textContent = name[0].toUpperCase();
    }
    if (mine) tile.querySelector("video").muted = true;      // never hear yourself
    grid.appendChild(tile);
    grid.dataset.count = grid.children.length;
    return tile;
  }

  function applyState(tile, s) {
    tile.querySelector(".tile-mic").hidden = !s.muted;
    tile.classList.toggle("no-video", !(s.video || s.sharing));
    tile.classList.toggle("sharing", !!s.sharing);
  }

  function updatePreview() {
    const video = myTile.querySelector("video");
    const track = screenTrack || camTrack;
    video.srcObject = track ? new MediaStream([track]) : null;
    myTile.classList.toggle("no-video", !track);
    myTile.classList.toggle("sharing", !!screenTrack);
    myTile.querySelector(".tile-mic").hidden = micOn;
  }

  // who is talking: a glowing ring around their tile
  function watchSpeaking(stream, tile) {
    try {
      if (!stream.getAudioTracks().length) return;
      const ctx = new (window.AudioContext || window.webkitAudioContext)();
      const analyser = ctx.createAnalyser();
      analyser.fftSize = 512;
      ctx.createMediaStreamSource(stream).connect(analyser);
      const data = new Uint8Array(analyser.frequencyBinCount);
      const tick = () => {
        if (left || !tile.isConnected) { ctx.close(); return; }
        analyser.getByteFrequencyData(data);
        const level = data.reduce((a, b) => a + b, 0) / data.length;
        tile.classList.toggle("speaking", level > 18);
        requestAnimationFrame(tick);
      };
      tick();
    } catch (e) { /* no audio analysis in this browser: the call still works */ }
  }

  // ---------- media ----------
  async function getMedia() {
    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
      showError("Calls need a secure address (https or localhost) to use the microphone.");
      return;
    }
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true, video: wantVideo });
      stream.getAudioTracks().forEach((t) => local.addTrack(t));
      camTrack = stream.getVideoTracks()[0] || null;
      if (camTrack) local.addTrack(camTrack);
    } catch (e) {
      if (wantVideo) {                                  // maybe only the camera is the problem
        try {
          const audio = await navigator.mediaDevices.getUserMedia({ audio: true });
          audio.getAudioTracks().forEach((t) => local.addTrack(t));
          showError("Couldn't start your camera, so you joined with voice only.");
          return;
        } catch (e2) { /* fall through */ }
      }
      showError("Couldn't use your microphone. Allow it in the browser's address bar to be heard.");
    }
  }

  const replaceVideoEverywhere = (track) => peers.forEach((p) => p.videoSender.replaceTrack(track).catch(() => {}));

  // ---------- one connection per other person ----------
  function createPeer(sid) {
    if (peers.has(sid)) return peers.get(sid);
    const info = known.get(sid) || { name: "Guest" };
    const pc = new RTCPeerConnection(ICE);
    const peer = {
      pc, sid, info, makingOffer: false, ignoreOffer: false,
      polite: socket.id > sid,                          // one of the two steps back if both call at once
      tile: makeTile(sid, info, false),
    };
    peers.set(sid, peer);

    if (local.getAudioTracks().length) local.getAudioTracks().forEach((t) => pc.addTrack(t, local));
    else pc.addTransceiver("audio", { direction: "sendrecv" });
    const video = pc.addTransceiver("video", { direction: "sendrecv", streams: [local] });
    peer.videoSender = video.sender;                    // the camera or screen can be swapped in later
    const current = screenTrack || camTrack;
    if (current) video.sender.replaceTrack(current).catch(() => {});

    pc.ontrack = (event) => {
      const stream = event.streams[0];
      const el = peer.tile.querySelector("video");
      if (stream && el.srcObject !== stream) {
        el.srcObject = stream;
        watchSpeaking(stream, peer.tile);
      }
    };
    pc.onicecandidate = (event) => { if (event.candidate) send(sid, { candidate: event.candidate }); };
    pc.onnegotiationneeded = async () => {
      try {
        peer.makingOffer = true;
        await pc.setLocalDescription();
        send(sid, { description: pc.localDescription });
      } catch (e) { console.error(e); } finally { peer.makingOffer = false; }
    };
    pc.onconnectionstatechange = () => {
      if (pc.connectionState === "connected") markConnected();
      if (pc.connectionState === "failed") pc.restartIce();
    };
    applyState(peer.tile, info);
    return peer;
  }

  socket.on("call_signal", async ({ from, data }) => {
    const peer = createPeer(from);
    const { description, candidate } = data;
    try {
      if (description) {
        const collision = description.type === "offer" && (peer.makingOffer || peer.pc.signalingState !== "stable");
        peer.ignoreOffer = !peer.polite && collision;
        if (peer.ignoreOffer) return;
        await peer.pc.setRemoteDescription(description);
        if (description.type === "offer") {
          await peer.pc.setLocalDescription();
          send(from, { description: peer.pc.localDescription });
        }
      } else if (candidate) {
        try { await peer.pc.addIceCandidate(candidate); } catch (e) { if (!peer.ignoreOffer) throw e; }
      }
    } catch (e) { console.error(e); }
  });

  // ---------- who is here ----------
  socket.on("call_peers", (data) => {
    data.peers.forEach((p) => { known.set(p.sid, p); createPeer(p.sid); });
    if (data.peers.length) setStatus("Connecting…");
    else if (isDm && shouldRing) {
      socket.emit("call_invite", { key, video: wantVideo });
      setStatus("Calling…");
    } else setStatus(isDm ? "Waiting for them to join…" : "You're the first one here");
  });
  socket.on("call_peer_joined", ({ peer }) => { known.set(peer.sid, peer); createPeer(peer.sid); });
  socket.on("call_peer_state", (s) => {
    const peer = peers.get(s.sid);
    if (peer) applyState(peer.tile, s);
  });
  socket.on("call_peer_left", ({ sid }) => {
    const peer = peers.get(sid);
    if (!peer) return;
    peer.pc.close();
    peer.tile.remove();
    peers.delete(sid);
    grid.dataset.count = grid.children.length;
    if (isDm) { setStatus("They left the call"); setTimeout(leave, 1200); }
  });
  socket.on("call_ringing", () => setStatus("Ringing…"));
  socket.on("call_failed", ({ reason }) => { setStatus(reason); setTimeout(leave, 2200); });
  socket.on("call_replaced", () => {
    left = true;
    cleanup();
    setStatus("You joined this call from another window.");
  });

  function markConnected() {
    if (connectedAt) return;
    connectedAt = Date.now();
    setStatus(isDm ? "Connected" : "In the call");
    timerEl.hidden = false;
    timerHandle = setInterval(() => {
      const s = Math.floor((Date.now() - connectedAt) / 1000);
      timerEl.textContent = String(Math.floor(s / 60)).padStart(2, "0") + ":" + String(s % 60).padStart(2, "0");
    }, 1000);
  }

  // ---------- buttons ----------
  const micBtn = document.getElementById("ctl-mic");
  const camBtn = document.getElementById("ctl-cam");
  const shareBtn = document.getElementById("ctl-share");

  function paintButtons() {
    micBtn.textContent = micOn ? "🎤" : "🔇";
    micBtn.classList.toggle("off", !micOn);
    camBtn.textContent = camTrack ? "📷" : "🚫";
    camBtn.classList.toggle("off", !camTrack);
    shareBtn.classList.toggle("on", !!screenTrack);
  }

  micBtn.addEventListener("click", () => {
    micOn = !micOn;
    local.getAudioTracks().forEach((t) => { t.enabled = micOn; });
    paintButtons(); updatePreview(); sendState();
  });

  camBtn.addEventListener("click", async () => {
    if (camTrack) {
      camTrack.stop();
      local.removeTrack(camTrack);
      camTrack = null;
      replaceVideoEverywhere(screenTrack);
    } else {
      try {
        const stream = await navigator.mediaDevices.getUserMedia({ video: true });
        camTrack = stream.getVideoTracks()[0];
        local.addTrack(camTrack);
        if (!screenTrack) replaceVideoEverywhere(camTrack);
      } catch (e) { showError("Couldn't start your camera."); return; }
    }
    paintButtons(); updatePreview(); sendState();
  });

  async function stopSharing() {
    if (!screenTrack) return;
    screenTrack.stop();
    screenTrack = null;
    replaceVideoEverywhere(camTrack);
    paintButtons(); updatePreview(); sendState();
  }

  shareBtn.addEventListener("click", async () => {
    if (screenTrack) { stopSharing(); return; }
    if (!navigator.mediaDevices || !navigator.mediaDevices.getDisplayMedia) { showError("Screen sharing isn't available in this browser."); return; }
    try {
      const stream = await navigator.mediaDevices.getDisplayMedia({ video: true });
      screenTrack = stream.getVideoTracks()[0];
      screenTrack.addEventListener("ended", stopSharing);
      replaceVideoEverywhere(screenTrack);
      paintButtons(); updatePreview(); sendState();
    } catch (e) { /* they cancelled the picker */ }
  });

  document.getElementById("ctl-hang").addEventListener("click", leave);

  function cleanup() {
    peers.forEach((p) => p.pc.close());
    peers.clear();
    local.getTracks().forEach((t) => t.stop());
    if (screenTrack) screenTrack.stop();
    clearInterval(timerHandle);
  }

  function leave() {
    if (left) return;
    left = true;
    socket.emit("call_leave", { key });
    cleanup();
    setStatus("Call ended");
    setTimeout(() => {
      window.close();                                   // works for the window we opened
      setTimeout(() => { location.href = back; }, 300); // otherwise go back to the chat
    }, 400);
  }
  window.addEventListener("beforeunload", () => { if (!left) socket.emit("call_leave", { key }); });

  // ---------- go ----------
  (async function start() {
    const me = { avatar: null };
    myTile = makeTile("me", me, true);
    await getMedia();
    paintButtons();
    updatePreview();
    watchSpeaking(local, myTile);
    const join = () => socket.emit("call_join", { key, video: !!camTrack });
    if (socket.connected) join(); else socket.once("connect", join);
  })();
})();
