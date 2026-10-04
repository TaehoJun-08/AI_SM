import re
from db import get_db

DOMAIN_POINTS = 50
BUDGET_POINTS = 30
TEXT_POINTS = 20
MIN_SCORE = 50

STOP_WORDS = {
    "and", "are", "for", "from", "has", "have", "its", "that", "the", "their",
    "this", "with", "our", "you", "your", "who", "what", "how", "can", "will",
    "they", "them", "into", "not", "all", "but", "also", "more", "about",
    "been", "which", "want", "like", "help", "support", "ideas", "idea",
}


def budget_score(funding, budget_min, budget_max):
    """Full points inside the sponsor's range, half points if within 25% of it."""
    if budget_min <= funding <= budget_max:
        return BUDGET_POINTS
    if budget_min * 0.75 <= funding <= budget_max * 1.25:
        return BUDGET_POINTS // 2
    return 0


def keywords(text):
    """Lower-case words of 3+ letters, minus common filler words."""
    return set(re.findall(r"[a-z]{3,}", text.lower())) - STOP_WORDS


def text_score(project_text, sponsor_text):
    """5 points per meaningful shared word, up to TEXT_POINTS."""
    shared = keywords(project_text) & keywords(sponsor_text)
    return min(len(shared) * 5, TEXT_POINTS), sorted(shared)


def match_score(project, profile, domains):
    """Score one project against one sponsor. Returns (score, reasons)."""
    score, reasons = 0, []
    if project["domain"] in domains:
        score += DOMAIN_POINTS
        reasons.append("✓ " + project["domain"])

    points = budget_score(project["funding_needed"],
                          profile["budget_min"], profile["budget_max"])
    score += points
    if points == BUDGET_POINTS:
        reasons.append("✓ within budget")
    elif points:
        reasons.append("≈ close to budget")

    project_text = " ".join([project["title"], project["summary"], project["description"]])
    points, shared = text_score(project_text, profile["bio"])
    score += points
    if shared:
        reasons.append("shared words: " + ", ".join(shared[:3]))
    return score, reasons


def matches_for_sponsor(user_id, limit=5):
    """Best projects for one sponsor, best first. Empty if no profile yet."""
    db = get_db()
    profile = db.execute(
        "SELECT * FROM sponsor_profiles WHERE user_id = ?", (user_id,)
    ).fetchone()
    if profile is None:
        return []
    domains = {r["domain"] for r in db.execute(
        "SELECT domain FROM sponsor_domains WHERE user_id = ?", (user_id,))}
    results = []
    for project in db.execute("SELECT * FROM projects"):
        score, reasons = match_score(project, profile, domains)
        if score >= MIN_SCORE:
            results.append({"project": project, "score": score, "reasons": reasons})
    results.sort(key=lambda m: m["score"], reverse=True)
    return results[:limit]


def load_sponsors():
    """Every sponsor profile (with the sponsor's name) and their set of domains."""
    db = get_db()
    sponsors = {}
    for row in db.execute(
        """SELECT p.*, u.full_name FROM sponsor_profiles p
           JOIN users u ON u.id = p.user_id"""
    ):
        sponsors[row["user_id"]] = {"profile": row, "domains": set()}
    for row in db.execute("SELECT user_id, domain FROM sponsor_domains"):
        if row["user_id"] in sponsors:
            sponsors[row["user_id"]]["domains"].add(row["domain"])
    return list(sponsors.values())


def matches_for_projects(projects, limit=3):
    """For each project, its best sponsors. Returns {project_id: [matches]}."""
    sponsors = load_sponsors()
    all_matches = {}
    for project in projects:
        results = []
        for s in sponsors:
            score, reasons = match_score(project, s["profile"], s["domains"])
            if score >= MIN_SCORE:
                results.append({"sponsor": s["profile"], "score": score, "reasons": reasons})
        results.sort(key=lambda m: m["score"], reverse=True)
        all_matches[project["id"]] = results[:limit]
    return all_matches    