"""Fill a fresh database with demo accounts and content, so the app can be shown straight away.

    python seed_demo.py

Safe to run again: it does nothing if the demo data is already there.
All demo accounts use the password below.
"""
import os
import sqlite3

from PIL import Image, ImageDraw, ImageFont
from werkzeug.security import generate_password_hash

import db as dbmod
import media

PASSWORD = "demo1234"

USERS = [
    ("maya_k", "Maya Kulkarni", "Building tech that helps small farmers. Coffee first, code second."),
    ("dev_patel", "Dev Patel", "Health-tech nerd. I back ideas that save people time."),
    ("sushil_r", "Sushil Rao", "Teaching with technology. Always looking for good teams."),
    ("surbhi_m", "Surbhi Mehta", "Angel investor and chai enthusiast."),
]

# (author, title, pitch, description, domain, stage, funding, [image colours])
BLUE, VIOLET, GREEN, GOLD, PINK, ORANGE, SKY = (14, 165, 233), (124, 58, 237), (16, 185, 129), (250, 204, 21), (236, 72, 153), (251, 146, 60), (56, 189, 248)
POSTS = [
    ("maya_k", "AgriSense", "Low-cost soil sensors that tell farmers when to water",
     "Small farms lose up to 30% of water to guesswork. AgriSense is a Rs.800 sensor with an SMS alert, "
     "tested on three farms near Pune. We need funds to build 500 units and run a full-season pilot.",
     "AgriTech", "mvp", 500000, [(BLUE, VIOLET, "Soil sensor"), (GREEN, GOLD, "Pilot farms"), (PINK, ORANGE, "SMS alerts")]),
    ("dev_patel", "MedBot", "AI triage assistant for rural clinics",
     "A chat assistant that helps clinic staff decide which patients need a doctor first. "
     "Works offline, in Hindi and Marathi. Prototype tested with two clinics.",
     "HealthTech", "prototype", 1200000, [(SKY, VIOLET, "MedBot")]),
    ("maya_k", "SolarShare", "Peer-to-peer rooftop solar for housing societies",
     "Neighbours with solar panels sell spare power to neighbours without. Still at the idea stage: "
     "we are designing the billing and want feedback.",
     "CleanTech", "idea", 300000, []),
    ("sushil_r", "LearnLoop", "Bite-sized lessons that adapt to each student",
     "Five-minute lessons with instant feedback. The engine adapts the next question to what the "
     "student just got wrong. 400 students are on the waiting list.",
     "EdTech", "mvp", 800000, [(VIOLET, PINK, "LearnLoop"), (ORANGE, GOLD, "400 on the waitlist")]),
]


def make_image(name, c1, c2, label, size=900):
    """A soft diagonal gradient with a caption, saved into the uploads folder."""
    image = Image.new("RGB", (size, size))
    pixels = image.load()
    for y in range(size):
        for x in range(size):
            t = (x + y) / (2 * size)
            pixels[x, y] = tuple(int(c1[i] + (c2[i] - c1[i]) * t) for i in range(3))
    draw = ImageDraw.Draw(image)
    try:
        font = ImageFont.load_default(size=64)
    except TypeError:                      # very old Pillow: tiny default font
        font = ImageFont.load_default()
    left, top, right, bottom = draw.textbbox((0, 0), label, font=font)
    draw.text(((size - (right - left)) / 2, (size - (bottom - top)) / 2), label, fill="white", font=font)
    os.makedirs(media.UPLOAD_DIR, exist_ok=True)
    image.save(os.path.join(media.UPLOAD_DIR, name), "PNG")
    return f"uploads/{name}"


def main():
    dbmod.init_db()
    conn = sqlite3.connect(dbmod.DB_PATH)
    conn.execute("PRAGMA foreign_keys = ON")
    if conn.execute("SELECT 1 FROM users WHERE username = 'maya_k'").fetchone():
        print("Demo data is already there. Nothing to do.")
        return

    pw = generate_password_hash(PASSWORD)
    ids = {}
    for username, full_name, bio in USERS:
        cur = conn.execute("INSERT INTO users (full_name, username, email, password_hash, bio) VALUES (?, ?, ?, ?, ?)",
                           (full_name, username, f"{username}@example.com", pw, bio))
        ids[username] = cur.lastrowid

    post_ids = {}
    for author, title, pitch, text, domain, stage, funding, images in POSTS:
        cur = conn.execute(
            """INSERT INTO projects (innovator_id, title, summary, description, domain, stage, funding_needed)
               VALUES (?, ?, ?, ?, ?, ?, ?)""", (ids[author], title, pitch, text, domain, stage, funding))
        post_ids[title] = cur.lastrowid
        for position, (c1, c2, label) in enumerate(images):
            path = make_image(f"demo-{cur.lastrowid}-{position}.png", c1, c2, label)
            conn.execute("INSERT INTO post_media (post_id, path, type, position) VALUES (?, ?, 'image', ?)",
                         (cur.lastrowid, path, position))

    # likes and comments
    for title, fans in {"AgriSense": ["dev_patel", "sushil_r", "surbhi_m"], "MedBot": ["maya_k", "surbhi_m"],
                        "LearnLoop": ["maya_k", "dev_patel"], "SolarShare": ["sushil_r"]}.items():
        for fan in fans:
            conn.execute("INSERT INTO likes (post_id, user_id) VALUES (?, ?)", (post_ids[title], ids[fan]))
    for title, who, text in [("AgriSense", "dev_patel", "This is brilliant. How long does the battery last?"),
                             ("AgriSense", "surbhi_m", "Where can I buy one?"),
                             ("MedBot", "sushil_r", "Love that it works in Marathi."),
                             ("LearnLoop", "surbhi_m", "Waiting list of 400 is a great sign.")]:
        conn.execute("INSERT INTO comments (post_id, user_id, body) VALUES (?, ?, ?)", (post_ids[title], ids[who], text))

    # sponsorship offers: one accepted (with a chat), two still waiting for an answer
    for sponsor, title, amount, message, status in [
            ("dev_patel", "AgriSense", 150000, "Love the pilot plan. Count me in.", "accepted"),
            ("surbhi_m", "AgriSense", 50000, "Happy to back this.", "pending"),
            ("surbhi_m", "LearnLoop", 200000, "Your waitlist convinced me.", "pending")]:
        conn.execute("INSERT INTO interests (sponsor_id, project_id, amount, message, status) VALUES (?, ?, ?, ?, ?)",
                     (ids[sponsor], post_ids[title], amount, message, status))
    a, b = sorted((ids["maya_k"], ids["dev_patel"]))
    thread = conn.execute("INSERT INTO dm_threads (user_a, user_b, requested_by, status) VALUES (?, ?, ?, 'accepted')",
                          (a, b, ids["dev_patel"])).lastrowid
    for sender, body in [("dev_patel", "Sponsorship accepted for AgriSense: Rs.1,50,000. Love the pilot plan."),
                         ("maya_k", "Thank you so much! Can I send you the pilot report?"),
                         ("dev_patel", "Please do. Excited to see the results.")]:
        conn.execute("INSERT INTO dm_messages (thread_id, sender_id, body) VALUES (?, ?, ?)", (thread, ids[sender], body))
    conn.commit()
    conn.close()

    # a server with a few channels and messages (reuses the app's own server set-up code)
    from app import app
    from servers.routes import create_server
    with app.app_context():
        server = create_server(ids["maya_k"], "Green Farm Labs", "🌱", "#10b981", "Farm tech crew, welcome!", 1)
        conn = dbmod.get_db()
        for user in ("dev_patel", "sushil_r"):
            conn.execute("INSERT INTO server_members (server_id, user_id) VALUES (?, ?)", (server, ids[user]))
        role = conn.execute("INSERT INTO server_roles (server_id, name, color, is_admin, position) VALUES (?, 'Founder', '#f97316', 1, 1)",
                            (server,)).lastrowid
        conn.execute("INSERT INTO member_roles (server_id, user_id, role_id) VALUES (?, ?, ?)", (server, ids["maya_k"], role))
        general = conn.execute("SELECT id FROM server_channels WHERE server_id = ? AND name = 'general'", (server,)).fetchone()[0]
        for sender, body in [("maya_k", "Welcome to the farm crew! 🌱"), ("dev_patel", "Excited to be here"),
                             ("sushil_r", "Sensors arrive on Monday, who is helping with the install?")]:
            conn.execute("INSERT INTO server_messages (channel_id, sender_id, body) VALUES (?, ?, ?)", (general, ids[sender], body))
        conn.commit()

    print("Demo data created. Log in with any of these (password: %s):" % PASSWORD)
    for username, full_name, _ in USERS:
        print(f"  {username}@example.com   ({full_name})")


if __name__ == "__main__":
    main()
