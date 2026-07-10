import os
from flask import Flask, render_template, request, redirect, url_for, session, flash
from werkzeug.security import generate_password_hash, check_password_hash
from dotenv import load_dotenv
import psycopg2

import db

load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY")
db.init_app(app)

@app.route("/reader")
def reader():
    return render_template("reader.html")

@app.route("/login", methods=['GET', 'POST'])
def login():
    if "user_id" in session:
        return redirect("/profile")
    
    if request.method == "GET":
        return render_template("login.html")
    
    email = request.form.get("email")
    password = request.form.get("password")

    conn = db.get_db()

    with conn.cursor() as cur:
        cur.execute("SELECT * FROM users WHERE email=%s", (email, ))
        user = cur.fetchone()

    if user is None or not check_password_hash(user["password_hash"], password):
        flash("Invalid email or password.")
        return redirect(url_for("login"))
    
    session["user_id"] = user["id"]
    session["full_name"] = user["full_name"]
    print(email, password)

    return redirect(url_for("index"))

@app.route("/signup", methods=["GET", "POST"])
def signup():
    if "user_id" in session:
        return redirect("/profile")
    
    if request.method == "GET":
        return render_template("signup.html")

    full_name = request.form.get("full_name")
    username = request.form.get("username")
    email = request.form.get("email")
    password = request.form.get("password")
    confirm_password = request.form.get("confirm_password")

    print(full_name, username, email, password, confirm_password)

    if not full_name or not username or not email or not password:
        flash("All fields are required.")
        return redirect(url_for("signup"))

    if password != confirm_password:
        flash("Passwords do not match.")
        return redirect(url_for("signup"))

    password_hash = generate_password_hash(password)

    conn = db.get_db()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO users (username, full_name, email, password_hash) VALUES (%s, %s, %s, %s)",
                (username, full_name, email, password_hash),
            )
        conn.commit()
    except psycopg2.errors.UniqueViolation:
        conn.rollback()
        flash("An account with that email already exists.")
        return redirect(url_for("signup"))

    flash("Account created. Please log in.")
    return redirect(url_for("login"))


@app.route("/")
def index():
    if "user_id" in session:
        return "Welcome back " + session['full_name']
    return "Hello from Flask!"

if __name__ == "__main__":
    app.run(debug=True)