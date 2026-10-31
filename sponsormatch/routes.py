import csv, json, os, re, time
from flask import Blueprint, abort, jsonify, render_template, request
from openai import OpenAI
from db import get_db
from helpers import current_user

match_bp = Blueprint("sponsor_match", __name__, url_prefix="/sponsor-match")

MODEL = os.getenv("AI_MODEL", "gemini-flash-latest")
CSV_PATH = os.path.join(os.path.dirname(__file__), "sponsors.csv")
_client = None


def available():
    return bool(os.getenv("GEMINI_API_KEY"))


def client():
    # created on first use, after app.py has loaded .env
    global _client
    if _client is None:
        if not available():
            raise RuntimeError("AI sponsor matching needs a free GEMINI_API_KEY (from Google AI Studio) "
                                "in the .env file.")
        _client = OpenAI(
            api_key=os.environ["GEMINI_API_KEY"],
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
    if not available():
        return jsonify(error="AI sponsor matching needs a free GEMINI_API_KEY (from Google AI Studio) "
                              "in the .env file."), 503
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
    if not available():
        return jsonify(error="AI sponsor matching needs a free GEMINI_API_KEY (from Google AI Studio) "
                              "in the .env file."), 503
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


@match_bp.get("/project/<int:pid>")
def page(pid):
    p = my_project(pid)
    return render_template("sponsormatch/page.html", pid=pid, title=p["title"], connected=available())
