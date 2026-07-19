from datetime import date, datetime
import random
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

@app.context_processor
def inject_user():
    if "user_id" not in session:
        return {"user": None}

    conn = db.get_db()
    with conn.cursor() as cur:
        cur.execute("SELECT * FROM users WHERE id = %s", (session["user_id"],))
        user = cur.fetchone()

    return {"user": user}

def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if "user_id" not in session:
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return decorated_function

UPLOAD_FOLDER = os.path.join(os.path.dirname(__file__), "uploads")
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

MEMBERSHIP_LIMITS = {
    "free": 1,
    "plus": 5,
    "pro": 10,
    "admin": 999999
}

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

    update_quest_progress(conn, user_id, "seconds", int(seconds))

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

    # Need the previous read_pages to compute how many *new* pages were turned
    with conn.cursor() as cur:
        cur.execute(
            "SELECT read_pages FROM books WHERE id = %s AND user_id = %s",
            (book_id, user_id),
        )
        book = cur.fetchone()

    if book is None:
        return jsonify({"error": "not found"}), 404

    pages_advanced = page - book["read_pages"]

    with conn.cursor() as cur:
        cur.execute(
            "UPDATE books SET read_pages = %s WHERE id = %s AND user_id = %s",
            (page, book_id, user_id),
        )
    conn.commit()

    if pages_advanced > 0:
        update_quest_progress(conn, user_id, "pages", pages_advanced)

    return jsonify({"read_pages": page})

BASE_LEVEL_XP = 100 

def compute_level(total_xp):
    """Given all-time XP, return (level, xp_into_current_level, xp_needed_for_next)."""
    level = 0
    threshold = BASE_LEVEL_XP
    remaining = total_xp
    while remaining >= threshold:
        remaining -= threshold
        level += 1
        threshold *= 2
    return level, remaining, threshold


def award_xp_and_coins(conn, user_id, xp_reward, coin_reward):
    with conn.cursor() as cur:
        cur.execute("SELECT total_xp, todays_xp, coins FROM users WHERE id = %s", (user_id,))
        user = cur.fetchone()

    new_total_xp = user["total_xp"] + xp_reward
    new_todays_xp = user["todays_xp"] + xp_reward
    new_coins = user["coins"] + coin_reward
    new_level, _, _ = compute_level(new_total_xp)

    with conn.cursor() as cur:
        cur.execute(
            "UPDATE users SET total_xp = %s, todays_xp = %s, coins = %s, level = %s WHERE id = %s",
            (new_total_xp, new_todays_xp, new_coins, new_level, user_id),
        )
        cur.execute(
            """
            INSERT INTO leaderboard (user_id, score)
            VALUES (%s, %s)
            ON CONFLICT (user_id) DO UPDATE SET score = EXCLUDED.score
            """,
            (user_id, new_total_xp),
        )

QUEST_TEMPLATES = [
    {"description": "Read for 10 minutes",  "goal_type": "seconds", "goal_amount": 600,  "xp_reward": 40, "coin_reward": 10},
    {"description": "Read for 20 minutes",  "goal_type": "seconds", "goal_amount": 1200, "xp_reward": 80, "coin_reward": 20},
    {"description": "Read for 30 minutes",  "goal_type": "seconds", "goal_amount": 1800, "xp_reward": 120, "coin_reward": 30},
    {"description": "Read for 40 minutes",  "goal_type": "seconds", "goal_amount": 2400, "xp_reward": 160, "coin_reward": 40},
    {"description": "Read for 50 minutes",  "goal_type": "seconds", "goal_amount": 3000, "xp_reward": 200, "coin_reward": 50},
    {"description": "Read for one hour",  "goal_type": "seconds", "goal_amount": 3600, "xp_reward": 240, "coin_reward": 60},
    {"description": "Turn 15 pages",        "goal_type": "pages",   "goal_amount": 15,   "xp_reward": 30, "coin_reward": 15},
    {"description": "Turn 30 pages",        "goal_type": "pages",   "goal_amount": 30,   "xp_reward": 60, "coin_reward": 25},
]


def generate_quests_if_needed(conn, user_id):
    today = date.today()

    with conn.cursor() as cur:
        cur.execute(
            "SELECT COUNT(*) AS c FROM quests WHERE user_id = %s AND assigned_date = %s",
            (user_id, today),
        )
        existing_today = cur.fetchone()["c"]

    if existing_today > 0:
        return  # already have (or already finished) today's quests

    chosen = random.sample(QUEST_TEMPLATES, k=min(3, len(QUEST_TEMPLATES)))
    with conn.cursor() as cur:
        for q in chosen:
            cur.execute(
                """
                INSERT INTO quests (user_id, description, goal_type, goal_amount, xp_reward, coin_reward, assigned_date)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                """,
                (user_id, q["description"], q["goal_type"], q["goal_amount"], q["xp_reward"], q["coin_reward"], today),
            )
    conn.commit()


def update_quest_progress(conn, user_id, goal_type, amount):
    if amount <= 0:
        return

    with conn.cursor() as cur:
        cur.execute(
            "SELECT id, progress, goal_amount, xp_reward, coin_reward FROM quests WHERE user_id = %s AND goal_type = %s AND completed = FALSE",
            (user_id, goal_type),
        )
        quests = cur.fetchall()

    for q in quests:
        new_progress = min(q["progress"] + amount, q["goal_amount"])
        completed = new_progress >= q["goal_amount"]
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE quests SET progress = %s, completed = %s WHERE id = %s",
                (new_progress, completed, q["id"]),
            )
        if completed:
            award_xp_and_coins(conn, user_id, q["xp_reward"], q["coin_reward"])

    conn.commit()

@app.route("/leaderboard")
@login_required
def leaderboard_page():
    conn = db.get_db()
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT u.username AS name, l.score,
                    RANK() OVER (ORDER BY l.score DESC) AS rank
            FROM leaderboard l
            JOIN users u ON u.id = l.user_id
            ORDER BY l.score DESC
            """
        )
        leaderboard = cur.fetchall()

    user_id = session["user_id"]
    with conn.cursor() as cur:
        cur.execute("SELECT username FROM users WHERE id = %s", (user_id,))
        me = cur.fetchone()

    for row in leaderboard:
        row["is_me"] = row["name"] == me["username"]

    return render_template("leaderboard.html", leaderboard=leaderboard)

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

    # --- Enforce the limit ---
    if user["uploaded_books"] >= limit:
        flash("You've reached your upload limit for your membership tier.", "danger")
        return redirect(url_for("upload"))

    file = request.files.get("book")
    if not file or file.filename == "":
        flash("Please choose a PDF to upload.", "danger")
        return redirect(url_for("upload"))

    if not file.filename.lower().endswith(".pdf"):
        flash("Only PDF files are supported right now.", "danger")
        return redirect(url_for("upload"))

    # --- Save the file locally with a safe and unique name ---
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

    flash("Book uploaded.", "success")
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
        flash("That book doesn't exist or isn't yours.", "danger")
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
        flash("Invalid email or password.", "danger")
        return redirect(url_for("login"))
    
    session["user_id"] = user["id"]
    session["full_name"] = user["full_name"]

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

    if not full_name or not username or not email or not password:
        flash("All fields are required.", "danger")
        return redirect(url_for("signup"))

    if password != confirm_password:
        flash("Passwords do not match.", "danger")
        return redirect(url_for("signup"))

    if len(password) < 8:
        flash("Password must be at least 8 characters.", "danger")
        return redirect(url_for("signup"))
    
    password_hash = generate_password_hash(password)

    conn = db.get_db()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO users (username, full_name, email, password_hash, membership) 
                VALUES (%s, %s, %s, %s, %s) 
                RETURNING id
                """,
                (username, full_name, email, password_hash, "free"),
            )

            id = cur.fetchone()['id']
            cur.execute("INSERT INTO streaks (id, days, streak_freeze_available) VALUES (%s, %s, %s)", (id, 0, False))
            cur.execute("INSERT INTO leaderboard (user_id, score) VALUES (%s, %s)", (id, 0))

        conn.commit()
    except psycopg2.errors.UniqueViolation:
        conn.rollback()
        flash("An account with that email already exists.", "danger")
        return redirect(url_for("signup"))

    flash("Account created. Please log in.", "success")
    return redirect(url_for("login"))


@app.route("/logout", methods=["POST"])
def logout():
    session.clear()
    flash("You've been logged out.", "success")
    return redirect(url_for("login"))

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
        flash("User doesn't exist.", "danger")
        session.clear()
        return redirect(url_for("login"))

    level, xp_into_level, xp_needed_for_level = compute_level(user["total_xp"])
    xp_percent = round((xp_into_level / xp_needed_for_level) * 100)
    user["level"] = level 
    
    # --- Quests ---
    today = date.today()

    generate_quests_if_needed(conn, user_id)

    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT description, progress, goal_amount, goal_type, xp_reward, coin_reward, completed
            FROM quests
            WHERE user_id = %s AND assigned_date = %s
            ORDER BY id
            """,
            (user_id, today),
        )
        quests = cur.fetchall()

    quests_done_for_today = len(quests) > 0 and all(q["completed"] for q in quests)

    for q in quests:
        if q["goal_type"] == "seconds":
            q["sub"] = f"{q['progress'] // 60} / {q['goal_amount'] // 60} min"
        else:
            q["sub"] = f"{q['progress']} / {q['goal_amount']} pages"


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
        all_books_list = cur.fetchall()

    current_book = all_books_list[0] if all_books_list else None
    books = all_books_list[1:4] if all_books_list else []
    has_more_books = len(all_books_list) > 5                 

    book_pct = 0
    if current_book and current_book["pages"]:
        book_pct = round((current_book["read_pages"] / current_book["pages"]) * 100)

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
        has_more_books=has_more_books,
        xp_needed_for_level=xp_needed_for_level,
        quests_done_for_today=quests_done_for_today,
        xp_percent=xp_percent,
        current_book=current_book,
        book_pct=book_pct,
        books=books,
        can_upload=can_upload,
        upload_limit=upload_limit,
        quests=quests,
        leaderboard=leaderboard,
    )

@app.route("/quests/reset", methods=["POST"])
@login_required
def reset_quests():
    user_id = session["user_id"]
    conn = db.get_db()
    today = date.today()

    with conn.cursor() as cur:
        cur.execute(
            "SELECT id, description FROM quests WHERE user_id = %s AND assigned_date = %s AND completed = FALSE",
            (user_id, today),
        )
        active_quests = cur.fetchall()

    if not active_quests:
        return jsonify({"error": "No active quests to reset"}), 400
    

    with conn.cursor() as cur:
        cur.execute(
            "SELECT description FROM quests WHERE user_id = %s AND assigned_date = %s",
            (user_id, today),
        )
        today_descriptions = {row["description"] for row in cur.fetchall()}

    fresh_templates = [q for q in QUEST_TEMPLATES if q["description"] not in today_descriptions]

    needed = len(active_quests)
    pool = fresh_templates if len(fresh_templates) >= needed else QUEST_TEMPLATES
    chosen = random.sample(pool, k=min(needed, len(pool)))

    with conn.cursor() as cur:
        active_ids = [q["id"] for q in active_quests]
        cur.execute("DELETE FROM quests WHERE id = ANY(%s)", (active_ids,))
        for q in chosen:
            cur.execute(
                """
                INSERT INTO quests (user_id, description, goal_type, goal_amount, xp_reward, coin_reward, assigned_date)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                """,
                (user_id, q["description"], q["goal_type"], q["goal_amount"], q["xp_reward"], q["coin_reward"], today),
            )
    conn.commit()

    return jsonify({"success": True})

@app.route("/books")
@login_required
def all_books_page():
    user_id = session["user_id"]
    conn = db.get_db()

    with conn.cursor() as cur:
        cur.execute("SELECT membership, uploaded_books FROM users WHERE id = %s", (user_id,))
        user = cur.fetchone()

    limit = MEMBERSHIP_LIMITS.get(user["membership"] or "free", 1)
    can_upload = user["uploaded_books"] < limit

    with conn.cursor() as cur:
        cur.execute(
            "SELECT * FROM books WHERE user_id = %s ORDER BY last_opened_at DESC NULLS LAST, id DESC",
            (user_id,),
        )
        books = cur.fetchall()

    return render_template("books.html", books=books, can_upload=can_upload, upload_limit=limit)

@app.route("/profile", methods=["GET", "POST"])
@login_required
def profile():
    user_id = session["user_id"]
    conn = db.get_db()

    if request.method == "POST":
        action = request.form.get("action")

        if action == "update_info":
            full_name = request.form.get("full_name", "").strip()
            username = request.form.get("username", "").strip()
            email = request.form.get("email", "").strip()

            if not full_name or not username or not email:
                flash("All fields are required.", "danger")
                return redirect(url_for("profile"))

            try:
                with conn.cursor() as cur:
                    cur.execute(
                        "UPDATE users SET full_name = %s, username = %s, email = %s WHERE id = %s",
                        (full_name, username, email, user_id),
                    )
                conn.commit()
            except psycopg2.errors.UniqueViolation:
                conn.rollback()
                flash("That username or email is already taken.", "danger")
                return redirect(url_for("profile"))

            session["full_name"] = full_name
            flash("Profile updated.", "success")
            return redirect(url_for("profile"))

        elif action == "change_password":
            current_password = request.form.get("current_password", "")
            new_password = request.form.get("new_password", "")
            confirm_password = request.form.get("confirm_password", "")

            with conn.cursor() as cur:
                cur.execute("SELECT password_hash FROM users WHERE id = %s", (user_id,))
                row = cur.fetchone()

            if row is None or not check_password_hash(row["password_hash"], current_password):
                flash("Current password is incorrect.", "danger")
                return redirect(url_for("profile"))

            if not new_password or new_password != confirm_password:
                flash("New passwords do not match.", "danger")
                return redirect(url_for("profile"))

            if len(new_password) < 8:
                flash("New password must be at least 8 characters.", "danger")
                return redirect(url_for("profile"))

            new_hash = generate_password_hash(new_password)
            with conn.cursor() as cur:
                cur.execute("UPDATE users SET password_hash = %s WHERE id = %s", (new_hash, user_id))
            conn.commit()

            flash("Password changed.", "success")
            return redirect(url_for("profile"))

        flash("Unknown action.", "danger")
        return redirect(url_for("profile"))

    with conn.cursor() as cur:
        cur.execute("SELECT * FROM users WHERE id = %s", (user_id,))
        user = cur.fetchone()

    if user is None:
        flash("User doesn't exist.", "danger")
        session.clear()
        return redirect(url_for("login"))

    return render_template("profile.html", user=user)

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

@app.route("/books/<int:book_id>/delete", methods=["POST"])
@login_required
def delete_book(book_id):
    user_id = session["user_id"]
    conn = db.get_db()

    with conn.cursor() as cur:
        cur.execute(
            "SELECT filename FROM books WHERE id = %s AND user_id = %s",
            (book_id, user_id),
        )
        book = cur.fetchone()

    if book is None:
        flash("That book doesn't exist or isn't yours.", "danger")
        return redirect(url_for("dashboard"))

    with conn.cursor() as cur:
        cur.execute("DELETE FROM books WHERE id = %s AND user_id = %s", (book_id, user_id))
        cur.execute(
            "UPDATE users SET uploaded_books = GREATEST(uploaded_books - 1, 0) WHERE id = %s",
            (user_id,),
        )
    conn.commit()

    # Remove the file from disk only after the DB delete succeeds
    user_folder = os.path.join(UPLOAD_FOLDER, str(user_id))
    filepath = os.path.join(user_folder, book["filename"])
    if os.path.exists(filepath):
        os.remove(filepath)

    flash("Book removed.", "success")
    return redirect(url_for("dashboard"))

@app.context_processor
def inject_year():
    return {"current_year": datetime.now().year}

@app.route("/")
def index():
    if "user_id" in session:
        return redirect(url_for("dashboard"))
    return render_template("home.html")

if __name__ == "__main__":
    app.run(debug=True)