"""The tools Nova can call. Each one is a small, scoped function -- Nova never runs raw SQL against your
data, it only ever gets back exactly what one of these decides to hand over. `draft_dm` and
`draft_project_update` never act immediately: they queue a row in ai_actions that the person must approve
(see ai/routes.py) before anything is actually sent or posted."""
import json

import deals
from ai import memory, sandbox


def _project_card(db, p):
    raised = db.execute(
        "SELECT COALESCE(SUM(amount), 0) FROM interests WHERE project_id = ? AND status = 'accepted'",
        (p["id"],)).fetchone()[0]
    return {"id": p["id"], "title": p["title"], "domain": p["domain"], "stage": p["stage"],
            "funding_needed": p["funding_needed"], "raised": raised,
            "accepts_money": bool(p["accepts_money"]), "accepts_goods": bool(p["accepts_goods"]),
            "accepts_pilot": bool(p["accepts_pilot"])}


def get_my_projects(ctx):
    db = ctx["db"]
    rows = db.execute("SELECT * FROM projects WHERE innovator_id = ? ORDER BY id DESC", (ctx["user_id"],)).fetchall()
    return {"projects": [_project_card(db, p) for p in rows]}


def get_project(ctx, project_id=None):
    db = ctx["db"]
    p = db.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone()
    if p is None:
        return {"error": "No project with that id."}
    card = _project_card(db, p)
    offers = db.execute(
        """SELECT i.id, i.status, u.username AS sponsor FROM interests i JOIN users u ON u.id = i.sponsor_id
           WHERE i.project_id = ? ORDER BY i.id DESC LIMIT 10""", (project_id,)).fetchall()
    card["open_offers"] = [{"interest_id": o["id"], "status": o["status"], "sponsor": o["sponsor"]} for o in offers]
    return card


def get_deal_room(ctx, interest_id=None):
    db = ctx["db"]
    offer = deals.load(db, interest_id)
    if offer is None or deals.role_of(offer, ctx["user_id"]) is None:
        return {"error": "That deal room isn't visible to you."}
    info = deals.room_context(db, offer, ctx["user_id"])
    return {"project": info["offer"]["title"], "domain": info["offer"]["domain"], "stage": info["offer"]["stage"],
            "your_role": info["role"], "your_turn": info["your_turn"],
            "status": info["offer"]["status"], "current_terms": deals.describe(info["offer"]),
            "history": [{"by": r["username"], "kind": r["kind"], "note": r["summary"] or r["note"]}
                        for r in info["rounds"][-10:]]}


def get_platform_trends(ctx, domain=None):
    return memory.trends(ctx["db"], domain or None)


def get_counter_benchmarks(ctx, domain=None, stage=None):
    return memory.counter_benchmarks(ctx["db"], domain or None, stage or None)


def remember(ctx, fact=None):
    return {"saved": memory.remember(ctx["db"], ctx["user_id"], fact or "")}


def run_python(ctx, code=None):
    output, ok = sandbox.run(code or "")
    return {"ok": ok, "output": output}


def draft_dm(ctx, username=None, message=None):
    db = ctx["db"]
    target = db.execute("SELECT id FROM users WHERE username = ? AND is_bot = 0",
                        ((username or "").lstrip("@"),)).fetchone()
    if target is None:
        return {"error": f"No user @{username}."}
    if target["id"] == ctx["user_id"]:
        return {"error": "That's the current user -- can't DM yourself."}
    action_id = db.execute(
        "INSERT INTO ai_actions (user_id, kind, payload) VALUES (?, 'send_dm', ?)",
        (ctx["user_id"], json.dumps({"to": target["id"], "username": username, "message": message}))).lastrowid
    db.commit()
    return {"drafted": True, "action_id": action_id, "note": "Waiting for the person to approve it."}


def draft_project_update(ctx, project_id=None, body=None):
    db = ctx["db"]
    p = db.execute("SELECT id, title FROM projects WHERE id = ? AND innovator_id = ?",
                   (project_id, ctx["user_id"])).fetchone()
    if p is None:
        return {"error": "The current user doesn't own a project with that id."}
    action_id = db.execute(
        "INSERT INTO ai_actions (user_id, kind, payload) VALUES (?, 'post_update', ?)",
        (ctx["user_id"], json.dumps({"project_id": project_id, "title": p["title"], "body": body}))).lastrowid
    db.commit()
    return {"drafted": True, "action_id": action_id, "note": "Waiting for the person to approve it."}


DISPATCH = {
    "get_my_projects": get_my_projects, "get_project": get_project, "get_deal_room": get_deal_room,
    "get_platform_trends": get_platform_trends, "get_counter_benchmarks": get_counter_benchmarks,
    "remember": remember, "run_python": run_python,
    "draft_dm": draft_dm, "draft_project_update": draft_project_update,
}

DECLARATIONS = [
    {"name": "get_my_projects",
     "description": "List the current user's own projects, with how much each has raised so far.",
     "parameters": {"type": "object", "properties": {}}},
    {"name": "get_project",
     "description": "Details of one project by id: domain, stage, funding raised, and its recent sponsorship offers.",
     "parameters": {"type": "object", "properties": {"project_id": {"type": "integer"}}, "required": ["project_id"]}},
    {"name": "get_deal_room",
     "description": "The negotiation history and current terms of one sponsorship deal, only if the current user is part of it.",
     "parameters": {"type": "object", "properties": {"interest_id": {"type": "integer"}}, "required": ["interest_id"]}},
    {"name": "get_platform_trends",
     "description": "Anonymised platform-wide patterns, grouped across many projects -- never one specific "
                     "person's data: typical funding ask by domain/stage, and the overall offer-acceptance rate.",
     "parameters": {"type": "object", "properties": {"domain": {"type": "string"}}}},
    {"name": "get_counter_benchmarks",
     "description": "How ACCEPTED deals' final terms landed compared to their opening offer, grouped by "
                     "domain/stage: the average fraction of the opening money ask that accepted deals settled "
                     "at, how many rounds deals like this usually take, and how often they took a counter at "
                     "all. Each group reports its own sample size -- treat groups with few deals as weak signal. "
                     "Use this (not get_platform_trends) when judging whether a specific counter-offer is fair.",
     "parameters": {"type": "object", "properties": {"domain": {"type": "string"}, "stage": {"type": "string"}}}},
    {"name": "remember",
     "description": "Save one short, durable fact about the current user for future conversations "
                     "(a goal, a preference, something recurring). Use sparingly, only for things worth keeping.",
     "parameters": {"type": "object", "properties": {"fact": {"type": "string"}}, "required": ["fact"]}},
    {"name": "run_python",
     "description": "Run a short Python snippet in an isolated sandbox (no imports, no files, no network) "
                     "and return its printed output. Good for a quick calculation or testing an idea.",
     "parameters": {"type": "object", "properties": {"code": {"type": "string"}}, "required": ["code"]}},
    {"name": "draft_dm",
     "description": "Prepare a direct message to another user (by username) for the current user to review "
                     "and approve. This never sends anything by itself.",
     "parameters": {"type": "object",
                     "properties": {"username": {"type": "string"}, "message": {"type": "string"}},
                     "required": ["username", "message"]}},
    {"name": "draft_project_update",
     "description": "Prepare an update post for one of the current user's own projects, for them to review "
                     "and approve. This never posts anything by itself.",
     "parameters": {"type": "object",
                     "properties": {"project_id": {"type": "integer"}, "body": {"type": "string"}},
                     "required": ["project_id", "body"]}},
]


def dispatch(name, args, ctx):
    fn = DISPATCH.get(name)
    if fn is None:
        return {"error": f"Unknown tool {name}."}
    try:
        return fn(ctx, **args)
    except Exception as e:
        return {"error": str(e)}
