// Behaviour shared by the feed, post pages and search: likes, comments, sponsor dialog,
// video autoplay, and infinite scrolling. Everything uses event delegation so posts
// added later (when you scroll) work too.

const heartSvg = (filled) =>
  '<svg class="ico" width="26" height="26" viewBox="0 0 24 24" fill="' + (filled ? "currentColor" : "none") +
  '" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">' +
  '<path d="M20.84 4.61a5.5 5.5 0 0 0-7.78 0L12 5.67l-1.06-1.06a5.5 5.5 0 0 0-7.78 7.78l1.06 1.06L12 21.23l7.78-7.78 1.06-1.06a5.5 5.5 0 0 0 0-7.78z"/></svg>';

async function toggleLike(post, onlyLike) {
  const btn = post.querySelector("[data-like]");
  if (onlyLike && btn.classList.contains("liked")) return;
  const res = await fetch(`/p/${post.dataset.id}/like`, { method: "POST" });
  if (!res.ok) return;
  const data = await res.json();
  btn.classList.toggle("liked", data.liked);
  btn.innerHTML = heartSvg(data.liked);
  const label = post.querySelector(".like-count");
  if (label) label.textContent = data.count + (data.count === 1 ? " like" : " likes");
}

function burst(media) {
  const heart = media.querySelector(".heart-burst");
  if (!heart) return;
  heart.classList.remove("go");
  void heart.offsetWidth;
  heart.classList.add("go");
}

document.addEventListener("click", (event) => {
  const post = event.target.closest(".post");

  if (event.target.closest("[data-like]") && post) {
    toggleLike(post, false);
    return;
  }
  const sponsor = event.target.closest("[data-sponsor], [data-edit-offer]");
  if (sponsor) {
    const dialog = document.getElementById("sponsor-dialog");
    const editing = sponsor.dataset.editOffer !== undefined;
    dialog.querySelector("form").action = editing
      ? `/interests/${sponsor.dataset.editOffer}/edit` : `/interests/post/${sponsor.dataset.sponsor}`;
    document.getElementById("sponsor-heading").textContent = editing ? "Change your offer" : "Sponsor this project";
    document.getElementById("sponsor-submit").textContent = editing ? "Save changes" : "Send offer";
    const withdraw = document.getElementById("sponsor-withdraw");
    withdraw.hidden = !editing;
    if (editing) withdraw.setAttribute("formaction", `/interests/${sponsor.dataset.editOffer}/withdraw`);
    document.getElementById("sponsor-title").textContent = sponsor.dataset.title;
    document.getElementById("sponsor-amount").value = editing ? sponsor.dataset.amount : "";
    document.getElementById("sponsor-message").value = editing ? sponsor.dataset.message : "";
    dialog.showModal();
    return;
  }
  if (event.target.closest("[data-close-dialog]")) {
    event.target.closest("dialog").close();
    return;
  }
  if (event.target.matches("dialog")) {
    event.target.close();
    return;
  }
  const sound = event.target.closest("[data-sound]");
  if (sound) {
    const video = sound.parentElement.querySelector("video");
    video.muted = !video.muted;
    sound.style.opacity = video.muted ? 0.7 : 1;
    return;
  }
  const slideBtn = event.target.closest("[data-slide]");
  if (slideBtn) {
    const slides = slideBtn.closest(".post-media").querySelector("[data-slides]");
    slides.scrollBy({ left: Number(slideBtn.dataset.slide) * slides.clientWidth, behavior: "smooth" });
    return;
  }
  const video = event.target.closest(".slide video");
  if (video && !video.controls) video.paused ? video.play() : video.pause();
});

// Keep the dots and the "2/5" counter in step with the slide being shown.
document.addEventListener("scroll", (event) => {
  const slides = event.target;
  if (!slides.matches || !slides.matches("[data-slides]")) return;
  const index = Math.round(slides.scrollLeft / slides.clientWidth);
  const box = slides.closest(".post-media");
  box.querySelectorAll(".dots i").forEach((dot, i) => dot.classList.toggle("on", i === index));
  const count = box.querySelector("[data-count]");
  if (count) count.textContent = (index + 1) + "/" + slides.children.length;
}, true);

// Double-click a picture to like it, like Instagram.
document.addEventListener("dblclick", (event) => {
  const media = event.target.closest("[data-like-target]");
  const post = event.target.closest(".post");
  if (media && post) {
    burst(media);
    toggleLike(post, true);
  }
});

// Posting a comment.
document.addEventListener("submit", async (event) => {
  const form = event.target.closest("[data-comment]");
  if (!form) return;
  event.preventDefault();
  const post = form.closest("[data-id]");
  const input = form.querySelector("input");
  const body = input.value.trim();
  if (!body) return;
  const res = await fetch(`/p/${post.dataset.id}/comment`, {
    method: "POST",
    body: new URLSearchParams({ body }),
  });
  if (!res.ok) return;
  const data = await res.json();
  input.value = "";
  const line = document.createElement("p");
  line.className = "caption";
  line.innerHTML = "<b></b> <span></span>";
  line.querySelector("b").textContent = data.username;
  line.querySelector("span").textContent = data.body;
  const list = post.querySelector(".latest-comments") || post.querySelector(".comment-list");
  if (list) list.appendChild(line);
});

// Videos play only while they are on screen.
const watcher = new IntersectionObserver((entries) => {
  entries.forEach((e) => {
    if (e.isIntersecting) e.target.play().catch(() => {});
    else e.target.pause();
  });
}, { threshold: 0.6 });

function watchVideos(root) {
  if (document.documentElement.dataset.autoplay === "0") return;  // chosen in Settings
  root.querySelectorAll("video[data-autoplay]").forEach((v) => watcher.observe(v));
}
watchVideos(document);

// Infinite scroll: when the bottom marker comes into view, fetch the next posts.
const feed = document.getElementById("feed");
const end = document.getElementById("feed-end");
if (feed && end) {
  let loading = false;
  let done = !feed.querySelector(".post");
  const loader = new IntersectionObserver(async (entries) => {
    if (!entries[0].isIntersecting || loading || done) return;
    loading = true;
    const last = feed.querySelector(".post:last-of-type");
    const res = await fetch(`/feed/more?before=${last.dataset.id}`);
    const html = (await res.text()).trim();
    if (!html) {
      done = true;
      end.innerHTML = '<span class="muted">You\'re all caught up ✨</span>';
    } else {
      feed.insertAdjacentHTML("beforeend", html);
      watchVideos(feed);
    }
    loading = false;
    loader.unobserve(end);
    loader.observe(end);  // check again in case the marker is still visible
  }, { rootMargin: "600px" });
  loader.observe(end);
}
