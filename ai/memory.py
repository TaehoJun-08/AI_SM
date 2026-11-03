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
           GROUP BY domain, stage HAVING COUNT(*) >= 2 ORDER BY n DESC LIMIT 12""",
        {"domain": domain}).fetchall()
    offers = db.execute(
        "SELECT COUNT(*) AS n, SUM(CASE WHEN status = 'accepted' THEN 1 ELSE 0 END) AS accepted FROM interests").fetchone()
    return {
        "by_domain_and_stage": [
            {"domain": r["domain"], "stage": r["stage"], "projects": r["n"],
             "avg_funding_asked": round(r["avg_ask"]), "share_with_a_sponsor": round(r["sponsored_share"], 2)}
            for r in rows],
        "platform_offer_acceptance_rate": round((offers["accepted"] or 0) / offers["n"], 2) if offers["n"] else None,
    }


def counter_benchmarks(db, domain=None, stage=None):
    """How ACCEPTED deals' final terms compare to their opening offer, grouped by domain/stage -- the
    'is this counter in the right range' signal trends() doesn't give. Small demo dataset, so groups
    under 2 deals are dropped (same guard as trends()) and every row carries its own sample size so
    Nova can hedge on thin groups instead of sounding falsely confident."""
    rows = db.execute(
        """WITH opening AS (
             SELECT DISTINCT ON (interest_id) interest_id, amount AS open_amount
             FROM deal_rounds WHERE kind = 'offer' ORDER BY interest_id, id
           ), rounds_ct AS (
             SELECT interest_id, COUNT(*) AS n_rounds FROM deal_rounds GROUP BY interest_id
           )
           SELECT p.domain, p.stage, COUNT(*) AS n,
                  AVG(CASE WHEN o.open_amount > 0 AND i.amount IS NOT NULL
                           THEN (i.amount * 1.0) / o.open_amount END) AS avg_ratio,
                  AVG(rc.n_rounds) AS avg_rounds,
                  AVG(CASE WHEN rc.n_rounds > 1 THEN 1.0 ELSE 0.0 END) AS counter_share
           FROM interests i
           JOIN projects p ON p.id = i.project_id
           JOIN opening o ON o.interest_id = i.id
           LEFT JOIN rounds_ct rc ON rc.interest_id = i.id
           WHERE i.status = 'accepted'
             AND (:domain IS NULL OR p.domain = :domain) AND (:stage IS NULL OR p.stage = :stage)
           GROUP BY p.domain, p.stage HAVING COUNT(*) >= 2 ORDER BY n DESC LIMIT 12""",
        {"domain": domain, "stage": stage}).fetchall()
    return {"by_domain_and_stage": [
        {"domain": r["domain"], "stage": r["stage"], "deals": r["n"],
         "avg_money_landing_ratio": round(r["avg_ratio"], 2) if r["avg_ratio"] is not None else None,
         "avg_rounds_to_agree": round(r["avg_rounds"], 1), "share_that_took_a_counter": round(r["counter_share"], 2)}
        for r in rows]}
