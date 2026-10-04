// Real-time chat client shared by project channels and direct messages.
// The page says which room to use through data-* attributes on #chat-log.
const log = document.getElementById("chat-log");
const form = document.getElementById("chat-form");
const input = document.getElementById("chat-input");
const myId = Number(log.dataset.me);
const room = { [log.dataset.key]: Number(log.dataset.room) };
const socket = io();

function addMessage(m) {
  document.getElementById("chat-empty")?.remove();
  const row = document.createElement("div");
  row.className = m.sender_id === myId ? "msg mine" : "msg";
  row.innerHTML = '<span class="avatar-sm"></span><div><div class="msg-meta">'
    + '<strong></strong> <small></small></div><div class="msg-body"></div></div>';
  row.querySelector(".avatar-sm").textContent = m.full_name[0].toUpperCase();
  row.querySelector("strong").textContent = m.full_name;
  row.querySelector("small").textContent = m.created_at.slice(11, 16);
  row.querySelector(".msg-body").textContent = m.body;
  log.appendChild(row);
  log.scrollTop = log.scrollHeight;
}

socket.on("connect", () => socket.emit(log.dataset.join, room));
socket.on("message", addMessage);

if (form) {
  form.addEventListener("submit", (event) => {
    event.preventDefault();
    const body = input.value.trim();
    if (!body) return;
    socket.emit(log.dataset.send, { ...room, body: body });
    input.value = "";
  });
}

log.scrollTop = log.scrollHeight;