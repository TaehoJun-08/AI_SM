import re
from flask import (Blueprint, render_template, request,
                   redirect, url_for, session, flash)
from werkzeug.security import generate_password_hash, check_password_hash
from db import get_db

auth_bp = Blueprint("auth", __name__)

@auth_bp.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "GET":
        return render_template("auth/register.html", form={})

    full_name = request.form.get("full_name", "").strip()
    username = request.form.get("username", "").strip()
    email = request.form.get("email", "").strip().lower()
    password = request.form.get("password", "")
    role = request.form.get("role", "innovator")
    db = get_db()

    error = None
    if not full_name or not username or not email or not password:
        error = "All fields are required."
    elif role not in ("innovator", "sponsor"):
        error = "Please choose a valid role."
    elif not re.fullmatch(r"[A-Za-z0-9_.]{3,20}", username):
        error = "Usernames are 3-20 characters: letters, numbers, _ and . only."
    elif len(password) < 6:
        error = "Password must be at least 6 characters."
    elif db.execute("SELECT 1 FROM users WHERE username = ?", (username,)).fetchone():
        error = f"@{username} is already taken. Try another one."
    elif db.execute("SELECT 1 FROM users WHERE email = ?", (email,)).fetchone():
        error = "An account with this email already exists."
    if error:
        flash(error)
        return render_template("auth/register.html", form=request.form)

    db.execute(
        """INSERT INTO users (full_name, username, email, password_hash, role)
           VALUES (?, ?, ?, ?, ?)""",
        (full_name, username, email, generate_password_hash(password), role),
    )
    db.commit()
    flash("Account created. Please log in.")
    return redirect(url_for("auth.login"))


@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "GET":
        return render_template("auth/login.html")

    email = request.form["email"].strip().lower()
    password = request.form["password"]

    db = get_db()
    user = db.execute(
        "SELECT * FROM users WHERE email = ?", (email,)
    ).fetchone()

    if user is None or not check_password_hash(user["password_hash"], password):
        flash("Invalid email or password.")
        return render_template("auth/login.html")

    session.clear()
    session["user_id"] = user["id"]
    return redirect(url_for("dashboard.index"))

#logout

@auth_bp.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("auth.login"))    