import json
import re
from flask import (Blueprint, render_template, request, redirect, url_for,
                   flash, session, jsonify, Response)
from werkzeug.security import generate_password_hash, check_password_hash
from db import get_db
from helpers import login_required, current_user
from media import save_avatar, delete_media

settings_bp = Blueprint("settings", __name__, url_prefix="/settings")

TABS = {"profile": "Profile", "appearance": "Appearance", "privacy": "Privacy",
        "account": "Account"}
THEMES = ("system", "light", "dark")
ACCENTS = {"aurora": "Aurora", "ocean": "Ocean", "sunset": "Sunset", "forest": "Forest"}
USERNAME_PATTERN = r"[A-Za-z0-9_.]{3,20}"
BIO_LIMIT = 300


def back(tab):
    return redirect(url_for("settings.index", tab=tab))


@settings_bp.route("/")
@login_required
def index():
    tab = request.args.get("tab", "profile")
    if tab not in TABS:
        tab = "profile"
    return render_template("settings/index.html", tab=tab, tabs=TABS, accents=ACCENTS)


# ---------- Profile ----------

@settings_bp.route("/profile", methods=["POST"])
@login_required
def save_profile():
    me = current_user()
    full_name = request.form.get("full_name", "").strip()
    username = request.form.get("username", "").strip()
    bio = request.form.get("bio", "").strip()
    db = get_db()
    if not full_name or not username:
        flash("Name and username can't be empty.")
    elif not re.fullmatch(USERNAME_PATTERN, username):
        flash("Usernames are 3-20 characters: letters, numbers, _ and . only.")
    elif len(bio) > BIO_LIMIT:
        flash(f"Keep your bio to {BIO_LIMIT} characters or less.")
    elif db.execute("SELECT 1 FROM users WHERE username = ? AND id != ?",
                    (username, me["id"])).fetchone():
        flash(f"@{username} is already taken. Try another one.")
    else:
        db.execute("UPDATE users SET full_name = ?, username = ?, bio = ? WHERE id = ?",
                   (full_name, username, bio, me["id"]))
        db.commit()
        flash("Profile updated. ✨")
    return back("profile")


@settings_bp.route("/photo", methods=["POST"])
@login_required
def save_photo():
    me = current_user()
    db = get_db()
    if request.form.get("remove"):
        db.execute("UPDATE users SET avatar = NULL WHERE id = ?", (me["id"],))
        db.commit()
        delete_media(me["avatar"])
        flash("Profile photo removed.")
        return back("profile")
    file = request.files.get("photo")
    if file is None or not file.filename:
        flash("Choose a picture first.")
        return back("profile")
    try:
        path = save_avatar(file)
    except ValueError as e:
        flash(str(e))
        return back("profile")
    db.execute("UPDATE users SET avatar = ? WHERE id = ?", (path, me["id"]))
    db.commit()
    delete_media(me["avatar"])
    flash("Looking good! Profile photo updated. 📸")
    return back("profile")


# ---------- Appearance ----------

@settings_bp.route("/appearance", methods=["POST"])
@login_required
def save_appearance():
    """Saves theme / accent / calm / autoplay. Accepts a form or a fetch() call."""
    me = current_user()
    theme = request.form.get("theme", me["theme"])
    accent = request.form.get("accent", me["accent"])
    if theme not in THEMES or accent not in ACCENTS:
        return jsonify(error="Unknown choice."), 400
    # Checkboxes are only sent when ticked, so the full form always carries `form=1`.
    if request.form.get("form"):
        calm = 1 if request.form.get("calm") else 0
        autoplay = 1 if request.form.get("autoplay") else 0
    else:
        calm, autoplay = me["calm"], me["autoplay"]
    db = get_db()
    db.execute("UPDATE users SET theme = ?, accent = ?, calm = ?, autoplay = ? WHERE id = ?",
               (theme, accent, calm, autoplay, me["id"]))
    db.commit()
    if request.headers.get("X-Requested-With") == "fetch":
        return jsonify(ok=True)
    flash("Appearance saved. ✨")
    return back("appearance")


# ---------- Privacy ----------

@settings_bp.route("/privacy", methods=["POST"])
@login_required
def save_privacy():
    policy = request.form.get("dm_policy", "everyone")
    if policy not in ("everyone", "none"):
        policy = "everyone"
    db = get_db()
    db.execute("UPDATE users SET dm_policy = ?, accepts_offers = ? WHERE id = ?",
               (policy, 1 if request.form.get("accepts_offers") else 0, session["user_id"]))
    db.commit()
    flash("Privacy settings saved. 🔒")
    return back("privacy")


# ---------- Account ----------

@settings_bp.route("/password", methods=["POST"])
@login_required
def save_password():
    me = current_user()
    new = request.form.get("new_password", "")
    if not check_password_hash(me["password_hash"], request.form.get("current_password", "")):
        flash("Your current password is wrong, so the password wasn't changed.")
    elif len(new) < 6:
        flash("The new password must be at least 6 characters.")
    elif new != request.form.get("confirm_password", ""):
        flash("The two new passwords don't match.")
    else:
        db = get_db()
        db.execute("UPDATE users SET password_hash = ? WHERE id = ?",
                   (generate_password_hash(new), me["id"]))
        db.commit()
        flash("Password changed. 🔒")
    return back("account")


@settings_bp.route("/export")
@login_required
def export():
    """Everything the person has put on the platform, as a JSON download."""
    me = current_user()
    db = get_db()
    rows = lambda sql: [dict(r) for r in db.execute(sql, (me["id"],))]
    data = {
        "profile": {k: me[k] for k in ("full_name", "username", "email", "bio", "created_at")},
        "posts": rows("SELECT id, title, summary, description, domain, stage, funding_needed, "
                      "created_at FROM projects WHERE innovator_id = ? ORDER BY id"),
        "comments": rows("SELECT post_id, body, created_at FROM comments WHERE user_id = ? ORDER BY id"),
        "offers_sent": rows("SELECT project_id, amount, message, status, created_at "
                            "FROM interests WHERE sponsor_id = ? ORDER BY id"),
    }
    return Response(json.dumps(data, indent=2, ensure_ascii=False), mimetype="application/json",
                    headers={"Content-Disposition": "attachment; filename=my-data.json"})


@settings_bp.route("/delete", methods=["POST"])
@login_required
def delete_account():
    me = current_user()
    if not check_password_hash(me["password_hash"], request.form.get("password", "")):
        flash("That password isn't right, so your account was kept.")
        return back("account")
    db = get_db()
    paths = [r["path"] for r in db.execute(
        """SELECT m.path FROM post_media m JOIN projects p ON p.id = m.post_id
           WHERE p.innovator_id = ?""", (me["id"],))]
    paths.append(me["avatar"])
    db.execute("DELETE FROM users WHERE id = ?", (me["id"],))
    db.commit()
    for path in paths:
        delete_media(path)
    session.clear()
    flash("Your account was deleted. We're sorry to see you go.")
    return redirect(url_for("auth.login"))
