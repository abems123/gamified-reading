from datetime import date, datetime
import os
from flask import Flask, render_template, request, redirect, url_for, session, flash, send_from_directory, jsonify
from models.user import User
from werkzeug.security import generate_password_hash, check_password_hash
from dotenv import load_dotenv
import psycopg2
from werkzeug.utils import secure_filename
from pypdf import PdfReader
from functools import wraps
import db


load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY")
db.init_app(app)

def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if "user_id" not in session:
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return decorated_function

UPLOAD_FOLDER = os.path.join(os.path.dirname(__file__), "uploads")
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

MEMBERSHIP_LIMITS = {"free": 1, "plus": 5, "pro": 10}

@app.route("/reader/<int:book_id>/track", methods=["POST"])
@login_required
def track_reading(book_id):
    user_id = session["user_id"]
    data = request.get_json(silent=True) or {}
    seconds = data.get("seconds", 0)

    if not isinstance(seconds, (int, float)) or seconds <= 0:
        return jsonify({"error": "invalid seconds"}), 400

    conn = db.get_db()
    with conn.cursor() as cur:
        cur.execute(
            "SELECT last_read_date, seconds_today, seconds_spent FROM books WHERE id = %s AND user_id = %s",
            (book_id, user_id),
        )
        book = cur.fetchone()

    if book is None:
        return jsonify({"error": "not found"}), 404

    today = date.today()
    seconds_today = book["seconds_today"] if book["last_read_date"] == today else 0
    seconds_today += int(seconds)
    seconds_spent = book["seconds_spent"] + int(seconds)

    with conn.cursor() as cur:
        cur.execute(
            "UPDATE books SET seconds_today = %s, seconds_spent = %s, last_read_date = %s WHERE id = %s",
            (seconds_today, seconds_spent, today, book_id),
        )
    conn.commit()

    # response uses "minutes_today"/"minutes_spent" keys to match the JS's
    # existing reconciliation code, but the values are minutes (float) —
    # the JS converts back to seconds for its internal counters.
    return jsonify({
        "minutes_today": seconds_today / 60,
        "minutes_spent": seconds_spent / 60,
    })


@app.route("/reader/<int:book_id>/page", methods=["POST"])
@login_required
def save_reading_page(book_id):
    user_id = session["user_id"]
    data = request.get_json(silent=True) or {}
    page = data.get("page")

    if not isinstance(page, int) or page < 1:
        return jsonify({"error": "invalid page"}), 400

    conn = db.get_db()
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE books SET read_pages = %s WHERE id = %s AND user_id = %s",
            (page, book_id, user_id),
        )
    conn.commit()

    return jsonify({"read_pages": page})

@app.route("/upload", methods=["GET", "POST"])
@login_required
def upload():
    user_id = session["user_id"]
    conn = db.get_db()

    with conn.cursor() as cur:
        cur.execute("SELECT membership, uploaded_books FROM users WHERE id = %s", (user_id,))
        user = cur.fetchone()

    limit = MEMBERSHIP_LIMITS.get(user["membership"] or "free", 1)

    if request.method == "GET":
        return render_template(
            "upload.html",         
            user=user,
            upload_limit=limit,
            can_upload=user["uploaded_books"] < limit,
        )

    # --- Enforce the limit server-side, not just in the UI ---
    if user["uploaded_books"] >= limit:
        flash("You've reached your upload limit for your membership tier.")
        return redirect(url_for("upload"))

    file = request.files.get("book")
    if not file or file.filename == "":
        flash("Please choose a PDF to upload.")
        return redirect(url_for("upload"))

    if not file.filename.lower().endswith(".pdf"):
        flash("Only PDF files are supported right now.")
        return redirect(url_for("upload"))

    # --- Save the file locally with a safe, unique-ish name ---
    filename = secure_filename(file.filename)
    user_folder = os.path.join(UPLOAD_FOLDER, str(user_id))
    os.makedirs(user_folder, exist_ok=True)
    filepath = os.path.join(user_folder, filename)
    file.save(filepath)

    # --- Read page count directly from the PDF ---
    reader = PdfReader(filepath)
    page_count = len(reader.pages)

    title = request.form.get("title") or filename.rsplit(".", 1)[0]

    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO books (user_id, title, filename, pages)
            VALUES (%s, %s, %s, %s)
            """,
            (user_id, title, filename, page_count),
        )
        cur.execute(
            "UPDATE users SET uploaded_books = uploaded_books + 1 WHERE id = %s",
            (user_id,),
        )
    conn.commit()

    flash("Book uploaded.")
    return redirect(url_for("dashboard"))

@app.route("/reader/<int:book_id>")
@login_required
def reader(book_id):
    user_id = session["user_id"]
    conn = db.get_db()

    with conn.cursor() as cur:
        cur.execute(
            "SELECT * FROM books WHERE id = %s AND user_id = %s",
            (book_id, user_id),
        )
        book = cur.fetchone()

    if book is None:
        flash("That book doesn't exist or isn't yours.")
        return redirect(url_for("dashboard"))

    today = date.today()
    now = datetime.now()

    # Reset today's seconds if this is a new day since last read.
    if book["last_read_date"] != today:
        book["seconds_today"] = 0

    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE books
            SET seconds_today = %s, last_read_date = %s, last_opened_at = %s
            WHERE id = %s
            """,
            (book["seconds_today"], today, now, book_id),
        )
    conn.commit()

    pct = 0
    if book["pages"]:
        pct = round((book["read_pages"] / book["pages"]) * 100)

    return render_template("reader.html", book=book, pct=pct)

@app.route("/books/<int:book_id>/file")
@login_required
def book_file(book_id):
    user_id = session["user_id"]
    conn = db.get_db()

    with conn.cursor() as cur:
        cur.execute(
            "SELECT filename FROM books WHERE id = %s AND user_id = %s",
            (book_id, user_id),
        )
        book = cur.fetchone()

    if book is None:
        return "Not found", 404

    user_folder = os.path.join(UPLOAD_FOLDER, str(user_id))
    return send_from_directory(user_folder, book["filename"])


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
                """
                INSERT INTO users (username, full_name, email, password_hash) 
                VALUES (%s, %s, %s, %s) 
                RETURNING id
                """,
                (username, full_name, email, password_hash),
            )

            id = cur.fetchone()['id']
            cur.execute("INSERT INTO streaks (id, days, streak_freeze_available) VALUES (%s, %s, %s)", (id, 0, False))
        conn.commit()
    except psycopg2.errors.UniqueViolation:
        conn.rollback()
        flash("An account with that email already exists.")
        return redirect(url_for("signup"))

    flash("Account created. Please log in.")
    return redirect(url_for("login"))

LEVEL_XP_STEP = 500  # xp required per level — change this curve however you like

MEMBERSHIP_LIMITS = {
    "free": 1,
    "plus": 5,
    "pro": 10,
}

@app.route("/dashboard")
@login_required
def dashboard():
    user_id = session["user_id"]
    conn = db.get_db()

    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT u.*, s.days AS streak_days, s.streak_freeze_available
            FROM users u
            LEFT JOIN streaks s ON s.id = u.id
            WHERE u.id = %s
            """,
            (user_id,),
        )
        user = cur.fetchone()

    if user is None:
        flash("User doesn't exist.")
        session.clear()
        return redirect(url_for("login"))

    # --- XP -> level progress ---
    xp_into_level = user["total_xp"] % LEVEL_XP_STEP
    xp_needed_for_level = LEVEL_XP_STEP
    xp_percent = round((xp_into_level / xp_needed_for_level) * 100)

    # --- Membership / upload limits ---
    membership = user["membership"] or "free"
    upload_limit = MEMBERSHIP_LIMITS.get(membership, 1)
    can_upload = user["uploaded_books"] < upload_limit

    # --- Books: current + rest of library ---
    with conn.cursor() as cur:
        cur.execute(
          "SELECT * FROM books WHERE user_id = %s ORDER BY last_opened_at DESC NULLS LAST, id DESC",
            (user_id,),
        )
        all_books = cur.fetchall()

    current_book = all_books[0] if all_books else None
    books = all_books[1:] if all_books else []

    book_pct = 0
    if current_book and current_book["pages"]:
        book_pct = round((current_book["read_pages"] / current_book["pages"]) * 100)

    # --- Quests ---
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT description, progress, goal_amount, xp_reward, coin_reward
            FROM quests
            WHERE user_id = %s AND completed = FALSE
            LIMIT 3
            """,
            (user_id,),
        )
        quests = cur.fetchall()

    # --- Leaderboard (top 5) ---
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT u.username AS name, l.score,
                   RANK() OVER (ORDER BY l.score DESC) AS rank
            FROM leaderboard l
            JOIN users u ON u.id = l.user_id
            ORDER BY l.score DESC
            LIMIT 5
            """
        )
        leaderboard = cur.fetchall()

    for row in leaderboard:
        row["is_me"] = row["name"] == user["username"]

    return render_template(
        "dashboard.html",
        user=user,
        streak_days=user["streak_days"] or 0,
        streak_freeze_available=user["streak_freeze_available"] or False,
        xp_into_level=xp_into_level,
        xp_needed_for_level=xp_needed_for_level,
        xp_percent=xp_percent,
        current_book=current_book,
        book_pct=book_pct,
        books=books,
        can_upload=can_upload,
        upload_limit=upload_limit,
        quests=quests,
        leaderboard=leaderboard,
    )

@app.route("/reader/<int:book_id>/reset", methods=["POST"])
@login_required
def reset_book_progress(book_id):
    user_id = session["user_id"]
    conn = db.get_db()

    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE books
            SET read_pages = 0, seconds_today = 0, seconds_spent = 0, last_read_date = NULL
            WHERE id = %s AND user_id = %s
            """,
            (book_id, user_id),
        )
    conn.commit()

    return jsonify({"success": True})

@app.route("/")
def index():
    if "user_id" in session:
        return "Welcome back " + session['full_name']
    return "Hello from Flask!"

if __name__ == "__main__":
    app.run(debug=True)