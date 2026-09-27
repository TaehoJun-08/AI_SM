from flask import (Blueprint, render_template, request,
                   redirect, url_for, session, flash)
from werkzeug.security import generate_password_hash, check_password_hash
from db import get_db

auth_bp = Blueprint("auth", __name__)

@auth_bp.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "GET":
        return render_template("auth/register.html")

    full_name = request.form["full_name"].strip()
    email = request.form["email"].strip().lower()
    password = request.form["password"]

    if not full_name or not email or not password:
        flash("All fields are required.")
        return render_template("auth/register.html")

    if len(password) < 6:
        flash("Password must be at least 6 characters.")
        return render_template("auth/register.html")

    db = get_db()
    existing = db.execute(
        "SELECT id FROM users WHERE email = ?", (email,)
    ).fetchone()
    if existing:
        flash("An account with this email already exists.")
        return render_template("auth/register.html")

    db.execute(
        "INSERT INTO users (full_name, email, password_hash) VALUES (?, ?, ?)",
        (full_name, email, generate_password_hash(password)),
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
    return redirect(url_for("home"))

#logout

@auth_bp.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("auth.login"))    