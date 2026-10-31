(function () {
  var body = document.getElementById("match-body");
  if (!body) return;

  var pid = body.dataset.pid;
  var startScreen = document.getElementById("match-start");
  var qsBox = document.getElementById("match-qs");
  var results = document.getElementById("match-results");

  function post(path, data) {
    return fetch("/sponsor-match/project/" + pid + path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(data || {}),
    }).then(function (r) {
      if (!r.ok) throw new Error("server said " + r.status);
      return r.json();
    });
  }

  document.getElementById("match-start-btn").addEventListener("click", function () {
    startScreen.hidden = true;
    qsBox.hidden = false;
    qsBox.innerHTML = '<p class="muted">Thinking up questions…</p>';
    post("/questions")
      .then(function (list) {
        qsBox.innerHTML = "";
        list.forEach(function (q) {
          var field = document.createElement("div");
          field.className = "match-field";
          var label = document.createElement("label");
          label.textContent = q;
          var input = document.createElement("input");
          input.className = "match-input";
          input.dataset.q = q;
          field.append(label, input);
          qsBox.appendChild(field);
        });
        var go = document.createElement("button");
        go.type = "button";
        go.className = "btn btn-primary rounded-pill px-4 mt-1";
        go.textContent = "Find sponsors";
        go.onclick = findSponsors;
        qsBox.appendChild(go);
      })
      .catch(function (e) {
        qsBox.innerHTML = '<p class="muted">Could not get questions: ' + e.message + "</p>";
      });
  });

  function findSponsors() {
    qsBox.hidden = true;
    results.innerHTML = '<p class="muted">Matching sponsors (can take 10-20 seconds)…</p>';
    var parts = [];
    document.querySelectorAll(".match-input").forEach(function (i) {
      if (i.value.trim()) parts.push("Q: " + i.dataset.q + "\nA: " + i.value.trim());
    });
    post("/match", { answers: parts.join("\n") })
      .then(renderResults)
      .catch(function (e) {
        results.innerHTML = '<p class="muted">Matching failed: ' + e.message + ". Try again in a moment.</p>";
      });
  }

  function renderResults(list) {
    results.innerHTML = "";
    if (!list.length) {
      results.innerHTML = '<p class="muted">No good matches found.</p>';
      return;
    }
    list.forEach(function (s) {
      var card = document.createElement("div");
      card.className = "match-card";

      var head = document.createElement("div");
      head.className = "match-card-head";
      var name = document.createElement("h6");
      name.textContent = s.name;
      var score = document.createElement("span");
      score.className = "tag";
      score.textContent = s.fit_score + "/10 fit";
      var source = document.createElement("span");
      source.className = "tag";
      source.textContent = s.source === "on_platform" ? "On the platform" : "External";
      head.append(name, score, source);

      var why = document.createElement("p");
      why.className = "muted mb-2";
      why.textContent = s.reason;

      var email = document.createElement("div");
      email.className = "match-email";
      var subj = document.createElement("div");
      subj.className = "match-email-subject";
      subj.textContent = "Subject: " + s.subject;
      var bodyEl = document.createElement("pre");
      bodyEl.className = "match-email-body";
      bodyEl.textContent = s.body;
      email.append(subj, bodyEl);

      var copy = document.createElement("button");
      copy.type = "button";
      copy.className = "btn btn-outline-secondary btn-sm rounded-pill mt-2";
      copy.textContent = "Copy email";
      copy.onclick = function () {
        navigator.clipboard.writeText("Subject: " + s.subject + "\n\n" + s.body);
        copy.textContent = "Copied!";
        setTimeout(function () {
          copy.textContent = "Copy email";
        }, 1500);
      };

      card.append(head, why, email, copy);
      results.appendChild(card);
    });
  }
})();
