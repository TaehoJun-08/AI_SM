import csv, json, os, re, time
from flask import Blueprint, abort, jsonify, render_template_string, request
from openai import OpenAI
from db import get_db
from helpers import current_user

match_bp = Blueprint("sponsor_match", __name__, url_prefix="/sponsor-match")

MODEL = os.getenv("AI_MODEL", "gemini-3.8-flash")
CSV_PATH = os.path.join(os.path.dirname(__file__), "sponsors.csv")
_client = None


def client():
    # created on first use, after app.py has loaded .env
    global _client
    if _client is None:
        _client = OpenAI(
            api_key=os.environ["GEMINI_KEY"],
            base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
        )
    return _client


def ask_json(prompt):
    for attempt in range(4):
        try:
            res = client().chat.completions.create(
                model=MODEL, messages=[{"role": "user", "content": prompt}])
            raw = res.choices[0].message.content
            raw = raw.replace("```json", "").replace("```", "").strip()
            return json.loads(raw)
        except Exception as e:
            code = getattr(e, "status_code", None)
            retry = code in (429, 500, 502, 503, 504) or isinstance(e, json.JSONDecodeError)
            if attempt == 3 or not retry:
                raise
            time.sleep(3 * (attempt + 1))


def my_project(pid):
    user = current_user()
    if not user:
        abort(401, "Please log in first.")
    p = get_db().execute("SELECT * FROM projects WHERE id = ?", (pid,)).fetchone()
    if p is None:
        abort(404, "No such project.")
    if p["innovator_id"] != user["id"]:
        abort(403, "Only the owner of this project can use AI matching.")
    return p


def words(text):
    return set(re.findall(r"[a-z]{3,}", (text or "").lower()))


def platform_candidates(owner_id):
    db = get_db()
    rows = db.execute(
        "SELECT sp.user_id, sp.organisation, sp.bio, sp.budget_min, sp.budget_max "
        "FROM sponsor_profiles sp JOIN users u ON u.id = sp.user_id "
        "WHERE u.accepts_offers = 1 AND u.is_bot = 0 AND u.id != ?", (owner_id,)).fetchall()
    out = []
    for r in rows:
        doms = [d["domain"] for d in db.execute(
            "SELECT domain FROM sponsor_domains WHERE user_id = ?", (r["user_id"],)).fetchall()]
        out.append({"id": "u%d" % r["user_id"], "name": r["organisation"], "about": r["bio"],
                    "domains": doms, "budget": "%d-%d" % (r["budget_min"], r["budget_max"]),
                    "source": "on_platform", "_min": r["budget_min"], "_max": r["budget_max"]})
    return out


def csv_candidates():
    if not os.path.exists(CSV_PATH):
        return []
    out = []
    with open(CSV_PATH, newline="", encoding="utf-8-sig") as f:
        for i, r in enumerate(csv.DictReader(f)):
            out.append({"id": "c%d" % i, "name": r["name"], "about": r["tags"],
                        "domains": [r["industry"]], "type": r["sponsor_type"],
                        "city": r["city"], "source": "external"})
    return out


def shortlist(p, owner_id, n=8):
    cands = platform_candidates(owner_id) + csv_candidates()
    pw = words(" ".join([p["title"], p["summary"], p["description"], p["domain"]]))
    funding = p["funding_needed"]

    def score(c):
        s = len(words(c["about"] + " " + " ".join(c["domains"])) & pw)
        if p["domain"].lower() in [d.lower() for d in c["domains"]]:
            s += 5
        if "_min" in c and c["_min"] <= funding <= c["_max"]:
            s += 3
        if c["source"] == "on_platform":
            s += 1  # real people on the site win ties
        return s

    cands.sort(key=score, reverse=True)
    return cands[:n]


@match_bp.post("/project/<int:pid>/questions")
def questions(pid):
    p = my_project(pid)
    data = ask_json(
        "A student is looking for sponsors for this project.\n"
        "Title: %s\nSummary: %s\nDescription: %s\nDomain: %s | Stage: %s | Funding needed: %s\n\n"
        "Ask exactly 3 short questions about things a sponsor would want to know that are "
        "NOT already stated (for example audience size, dates, location, what a sponsor "
        "could get in return). Return ONLY a JSON array of 3 strings."
        % (p["title"], p["summary"], p["description"][:3000], p["domain"],
           p["stage"], p["funding_needed"]))
    if isinstance(data, dict):
        data = next(iter(data.values()))
    return jsonify([str(q) for q in data][:3])


@match_bp.post("/project/<int:pid>/match")
def match(pid):
    p = my_project(pid)
    user = current_user()
    answers = str((request.get_json(silent=True) or {}).get("answers", ""))[:2000]
    cands = shortlist(p, user["id"])
    pool = [{k: v for k, v in c.items() if not k.startswith("_")} for c in cands]
    data = ask_json(
        "You help a student find sponsors for their project.\n\n"
        "PROJECT\nTitle: %s\nSummary: %s\nDescription: %s\nDomain: %s | Stage: %s | "
        "Funding needed: %s\nExtra answers from the organiser:\n%s\n\n"
        "CANDIDATE SPONSORS (pick ONLY from these, by id):\n%s\n\n"
        "Pick the 3 best fits (fewer if fewer are genuinely relevant). Rules: use only facts "
        "stated above. Never invent perks, numbers, dates or promises. Use normal spacing "
        "between words.\n"
        "Return ONLY a JSON array: "
        '[{"id": "...", "fit_score": 1-10, "reason": "one sentence", '
        '"email": {"subject": "...", "body": "4-6 sentence polite sponsorship request, '
        'signed [Your Name]"}}]'
        % (p["title"], p["summary"], p["description"][:3000], p["domain"], p["stage"],
           p["funding_needed"], answers or "none given", json.dumps(pool)))
    by_id = {c["id"]: c for c in cands}
    results = []
    for r in data:
        c = by_id.get(r.get("id"))
        if not c:
            continue  # the AI made up an id, drop it
        email = r.get("email") or {}
        results.append({
            "name": c["name"], "source": c["source"],
            "user_id": int(c["id"][1:]) if c["source"] == "on_platform" else None,
            "fit_score": r.get("fit_score"), "reason": r.get("reason", ""),
            "subject": email.get("subject", ""), "body": email.get("body", ""),
        })
    return jsonify(results)


PAGE = r"""<!doctype html>
<html><head><meta charset="utf-8"><title>AI sponsor match</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
 body{font-family:system-ui,Arial,sans-serif;max-width:720px;margin:32px auto;padding:0 16px;line-height:1.5}
 button{padding:8px 14px;font-size:15px;cursor:pointer;margin-top:8px}
 input{width:100%;padding:8px;font-size:15px;margin:4px 0 12px;box-sizing:border-box}
 .card{border:1px solid #8886;border-radius:8px;padding:12px;margin-top:14px}
 .badge{font-size:12px;padding:2px 8px;border-radius:10px;background:#8883}
 pre{white-space:pre-wrap;font-family:inherit;background:#8822;padding:8px;border-radius:6px}
</style></head><body>
<p><a href="/">&larr; Back</a></p>
<h1>AI sponsor match</h1>
<p>Project: <b>{{ title }}</b></p>
<p><small>The AI drafts the emails. Read them and fix anything wrong before you send.</small></p>
<button id="start">Start</button>
<div id="qs"></div>
<div id="out"></div>
<script>
var PID = {{ pid }};

function post(path, body) {
  return fetch("/sponsor-match/project/" + PID + path, {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify(body || {})
  }).then(function (r) {
    if (!r.ok) throw new Error("server said " + r.status);
    return r.json();
  });
}

document.getElementById("start").onclick = function () {
  var qs = document.getElementById("qs");
  qs.textContent = "Thinking up questions...";
  post("/questions").then(function (list) {
    qs.textContent = "";
    list.forEach(function (q) {
      var label = document.createElement("div");
      label.textContent = q;
      var input = document.createElement("input");
      input.className = "ans";
      input.dataset.q = q;
      qs.append(label, input);
    });
    var go = document.createElement("button");
    go.textContent = "Find sponsors";
    go.onclick = find;
    qs.appendChild(go);
  }).catch(function (e) { qs.textContent = "Could not get questions: " + e.message; });
};

function find() {
  var out = document.getElementById("out");
  out.textContent = "Matching sponsors (can take 10-20 seconds)...";
  var parts = [];
  document.querySelectorAll(".ans").forEach(function (i) {
    if (i.value.trim()) parts.push("Q: " + i.dataset.q + "\nA: " + i.value.trim());
  });
  post("/match", {answers: parts.join("\n")}).then(show).catch(function (e) {
    out.textContent = "Matching failed: " + e.message + ". Try again in a moment.";
  });
}

function show(list) {
  var out = document.getElementById("out");
  out.textContent = "";
  if (!list.length) { out.textContent = "No good matches found."; return; }
  list.forEach(function (s) {
    var card = document.createElement("div");
    card.className = "card";
    var h = document.createElement("h3");
    h.textContent = s.name + " (" + s.fit_score + "/10) ";
    var badge = document.createElement("span");
    badge.className = "badge";
    badge.textContent = s.source === "on_platform" ? "On the platform" : "Outside the platform";
    h.appendChild(badge);
    var why = document.createElement("p");
    why.textContent = s.reason;
    var subj = document.createElement("b");
    subj.textContent = "Subject: " + s.subject;
    var body = document.createElement("pre");
    body.textContent = s.body;
    var copy = document.createElement("button");
    copy.textContent = "Copy email";
    copy.onclick = function () {
      navigator.clipboard.writeText("Subject: " + s.subject + "\n\n" + s.body);
      copy.textContent = "Copied";
    };
    card.append(h, why, subj, body, copy);
    out.appendChild(card);
  });
}
</script>
</body></html>"""


@match_bp.get("/project/<int:pid>")
def page(pid):
    p = my_project(pid)
    return render_template_string(PAGE, pid=pid, title=p["title"])
