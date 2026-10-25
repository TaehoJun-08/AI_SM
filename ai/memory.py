"""Nova's memory: durable facts about one person, and anonymised trends across the whole platform.
Trends only ever return counts and averages grouped across several projects/offers -- never a specific
person's own numbers -- so this is safe to use when answering anyone, not just the person it's about."""


def facts(db, user_id):
    return [r["fact"] for r in db.execute(
        "SELECT fact FROM ai_memory WHERE user_id = ? ORDER BY id", (user_id,))]


def remember(db, user_id, fact):
    fact = " ".join((fact or "").split())[:300]
    if not fact:
        return False
    db.execute("INSERT INTO ai_memory (user_id, fact) VALUES (?, ?)", (user_id, fact))
    db.commit()
    return True


def forget(db, user_id, fact_id):
    db.execute("DELETE FROM ai_memory WHERE id = ? AND user_id = ?", (fact_id, user_id))
    db.commit()


def trends(db, domain=None):
    rows = db.execute(
        """SELECT domain, stage, COUNT(*) AS n, AVG(funding_needed) AS avg_ask,
                  AVG(CASE WHEN EXISTS (SELECT 1 FROM interests i WHERE i.project_id = p.id AND i.status = 'accepted')
                      THEN 1.0 ELSE 0.0 END) AS sponsored_share
           FROM projects p
           WHERE (:domain IS NULL OR domain = :domain)
           GROUP BY domain, stage HAVING n >= 2 ORDER BY n DESC LIMIT 12""",
        {"domain": domain}).fetchall()
    offers = db.execute(
        "SELECT COUNT(*) AS n, SUM(status = 'accepted') AS accepted FROM interests").fetchone()
    return {
        "by_domain_and_stage": [
            {"domain": r["domain"], "stage": r["stage"], "projects": r["n"],
             "avg_funding_asked": round(r["avg_ask"]), "share_with_a_sponsor": round(r["sponsored_share"], 2)}
            for r in rows],
        "platform_offer_acceptance_rate": round((offers["accepted"] or 0) / offers["n"], 2) if offers["n"] else None,
    }
