"""Register / login / logout. Guests are upgraded in place so their history is kept."""
import re

from flask import Blueprint, jsonify, request, session
from werkzeug.security import check_password_hash, generate_password_hash

from ..db import execute, get_db, query
from ..services import current_user, current_user_id, user_json

bp = Blueprint("auth", __name__, url_prefix="/api/auth")
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


@bp.get("/me")
def me():
    return jsonify(user=user_json(current_user()))


@bp.post("/register")
def register():
    data = request.get_json(silent=True) or {}
    name = (data.get("name") or "").strip()
    email = (data.get("email") or "").strip().lower()
    password = data.get("password") or ""
    if not name or not EMAIL_RE.match(email):
        return jsonify(error="Please enter your name and a valid email."), 400
    if len(password) < 6:
        return jsonify(error="Password must be at least 6 characters."), 400
    if query("SELECT 1 FROM users WHERE email=?", (email,), one=True):
        return jsonify(error="An account with this email already exists."), 409
    uid = current_user_id()
    u = current_user()
    if u["is_guest"]:
        execute("UPDATE users SET name=?, email=?, password_hash=?, is_guest=0 WHERE id=?",
                (name, email, generate_password_hash(password), uid))
    else:
        uid = execute("INSERT INTO users (name,email,password_hash,is_guest) VALUES (?,?,?,0)",
                      (name, email, generate_password_hash(password)))
        session["user_id"] = uid
    return jsonify(user=user_json(current_user())), 201


@bp.post("/login")
def login():
    data = request.get_json(silent=True) or {}
    email = (data.get("email") or "").strip().lower()
    row = query("SELECT * FROM users WHERE email=?", (email,), one=True)
    if not row or not row["password_hash"] or not check_password_hash(row["password_hash"], data.get("password") or ""):
        return jsonify(error="Wrong email or password."), 401
    guest = current_user()
    if guest["is_guest"] and guest["id"] != row["id"]:
        _merge_guest(guest["id"], row["id"])
    session["user_id"] = row["id"]
    return jsonify(user=user_json(current_user()))


@bp.post("/logout")
def logout():
    session.clear()
    return jsonify(user=user_json(current_user()))


def _merge_guest(guest_id, account_id):
    """Carry the guest's browsing history, cart and wishlist into the account."""
    db = get_db()
    db.execute("UPDATE interactions SET user_id=? WHERE user_id=?", (account_id, guest_id))
    db.execute("UPDATE rec_impressions SET user_id=? WHERE user_id=?", (account_id, guest_id))
    for r in db.execute("SELECT product_id, qty FROM cart_items WHERE user_id=?", (guest_id,)).fetchall():
        db.execute("""INSERT INTO cart_items (user_id,product_id,qty) VALUES (?,?,?)
                      ON CONFLICT(user_id,product_id) DO UPDATE SET qty = qty + excluded.qty""",
                   (account_id, r["product_id"], r["qty"]))
    db.execute("INSERT OR IGNORE INTO wishlist (user_id,product_id) SELECT ?, product_id FROM wishlist WHERE user_id=?",
               (account_id, guest_id))
    db.execute("DELETE FROM users WHERE id=?", (guest_id,))
    db.commit()
