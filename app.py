import os
from dotenv import load_dotenv
from flask import Flask, flash, redirect, request, url_for
from db import close_db, get_db, init_db
from helpers import current_user
from auth.routes import auth_bp
from feed.routes import feed_bp
from projects.routes import projects_bp
from interests.routes import interests_bp
from servers.routes import servers_bp
from dms.routes import dms_bp
from reports.routes import reports_bp
from profiles.routes import profiles_bp
from settings.routes import settings_bp
from extensions import socketio

load_dotenv()

app = Flask(__name__)
# A fresh clone has no .env yet, so fall back to a development key (set SECRET_KEY in .env for real use).
app.config["SECRET_KEY"] = os.getenv("SECRET_KEY") or "dev-only-secret-key-change-me"
app.config["MAX_CONTENT_LENGTH"] = 500 * 1024 * 1024  # all files of one post together
app.teardown_appcontext(close_db)
app.register_blueprint(auth_bp)
app.register_blueprint(feed_bp)
app.register_blueprint(projects_bp)
app.register_blueprint(interests_bp)
app.register_blueprint(servers_bp)
app.register_blueprint(dms_bp)
app.register_blueprint(reports_bp)
app.register_blueprint(profiles_bp)
app.register_blueprint(settings_bp)
socketio.init_app(app)

import servers.chat  # registers the server chat events
import dms.chat  # registers the DM events

@app.context_processor
def inject_user():
    user = current_user()
    pending = 0
    if user:
        pending = get_db().execute(
            """SELECT COUNT(*) FROM interests i JOIN projects p ON p.id = i.project_id
               WHERE p.innovator_id = ? AND i.status = 'pending'""", (user["id"],)).fetchone()[0]
    return {"user": user, "pending_offers": pending, "giphy_key": os.getenv("GIPHY_API_KEY", "")}

@app.errorhandler(413)
def too_large(error):
    flash("Those files are too big. Keep a post under 500 MB in total (100 MB per video).")
    return redirect(request.referrer or url_for("feed.index"))


if __name__ == "__main__":
    init_db()  # creates the database on a fresh clone (safe to run every time)
    socketio.run(app, host=os.getenv("HOST", "127.0.0.1"), port=int(os.getenv("PORT", "5000")), debug=True)
