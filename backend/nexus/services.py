"""Shared helpers used by the route modules."""
import json
import os
from functools import wraps

import pandas as pd
from flask import current_app, jsonify, session

from .db import connect, execute, get_db, query
from .recommender import HybridRecommender


# ---------------------------------------------------------------- model
def load_frames(conn):
    products = pd.read_sql_query("SELECT * FROM products", conn)
    interactions = pd.read_sql_query(
        "SELECT user_id, product_id, event_type, created_at FROM interactions WHERE product_id IS NOT NULL", conn)
    order_items = pd.read_sql_query(
        "SELECT oi.order_id, oi.product_id, o.user_id FROM order_items oi JOIN orders o ON o.id = oi.order_id", conn)
    return products, interactions, order_items


def build_engine(app):
    conn = connect(app.config["DATABASE"])
    products, interactions, order_items = load_frames(conn)
    conn.close()
    engine = HybridRecommender().fit(products, interactions, order_items)
    app.extensions["recommender"] = engine
    return engine


def engine():
    return current_app.extensions["recommender"]


def refresh_collaborative():
    """Online learning: re-fit the behaviour-based parts with the newest data."""
    products, interactions, order_items = load_frames(get_db())
    engine().fit_collaborative(interactions, order_items)


def load_metrics():
    path = os.path.join(current_app.config["MODEL_DIR"], "metrics.json")
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    return None


# ---------------------------------------------------------------- users
def current_user_id():
    """Every visitor gets a user row. Guests are upgraded when they register."""
    uid = session.get("user_id")
    if uid and query("SELECT 1 FROM users WHERE id=?", (uid,), one=True):
        return uid
    uid = execute("INSERT INTO users (name, is_guest) VALUES ('Guest', 1)")
    session["user_id"] = uid
    session.permanent = True
    return uid


def current_user():
    return query("SELECT id, name, email, is_admin, is_guest FROM users WHERE id=?", (current_user_id(),), one=True)


def user_json(row):
    return {"id": row["id"], "name": row["name"], "email": row["email"],
            "is_admin": bool(row["is_admin"]), "is_guest": bool(row["is_guest"])}


def admin_required(fn):
    @wraps(fn)
    def wrapper(*a, **kw):
        u = current_user()
        if not u["is_admin"]:
            return jsonify(error="Admin access required"), 403
        return fn(*a, **kw)
    return wrapper


def user_events(uid, limit=60):
    rows = query("""SELECT product_id, event_type, query, created_at FROM interactions
                    WHERE user_id=? ORDER BY id DESC LIMIT ?""", (uid, limit))
    return [dict(r) for r in rows]


def user_vector(uid):
    eng = engine()
    events = user_events(uid)
    w, strongest = eng.user_vector([e for e in events if e["product_id"]])
    # searches without a clicked product still shape the profile softly
    from .recommender import parse_query
    for rank, e in enumerate([e for e in events if e["event_type"] == "search" and e["query"]][:5]):
        p = parse_query(e["query"])
        w = w + eng.tag_vector(p["tags"], p["category"]) * (eng.decay ** rank)
    return w, strongest


def log_event(uid, event_type, product_id=None, q=None, source=None):
    execute("INSERT INTO interactions (user_id, product_id, event_type, query, source) VALUES (?,?,?,?,?)",
            (uid, product_id, event_type, q, source))
    if event_type == "view" and source == "rec" and product_id:
        execute("""UPDATE rec_impressions SET clicked=1 WHERE id = (
                     SELECT id FROM rec_impressions WHERE user_id=? AND product_id=? ORDER BY id DESC LIMIT 1)""",
                (uid, product_id))


# ---------------------------------------------------------------- products
IMAGE_EXTS = (".jpg", ".jpeg", ".png", ".webp")
_image_cache = {"mtime": None, "files": {}, "credits": {}}


def product_images():
    """Map sku -> filename for photos dropped into backend/images (rescanned when the folder changes)."""
    folder = current_app.config["IMAGE_DIR"]
    if not os.path.isdir(folder):
        return {}
    cpath = os.path.join(folder, "credits.json")
    mtime = (os.path.getmtime(folder), os.path.getmtime(cpath) if os.path.exists(cpath) else 0)
    if mtime != _image_cache["mtime"]:
        files = {}
        for f in os.listdir(folder):
            stem, ext = os.path.splitext(f)
            if ext.lower() in IMAGE_EXTS:
                files[stem.lower()] = f
        credits = {}
        if os.path.exists(cpath):
            try:
                with open(cpath, encoding="utf-8") as fh:
                    credits = json.load(fh)
            except ValueError:
                credits = {}
        _image_cache.update(mtime=mtime, files=files, credits=credits)
    return _image_cache["files"]


def product_json(row, uid=None, cart=None, wish=None):
    d = dict(row)
    if not d.get("image_url") and "sku" in d:
        f = product_images().get(str(d["sku"]).lower())
        if f:
            d["image_url"] = f"/api/images/{f}"
            credit = _image_cache["credits"].get(str(d["sku"]))
            if credit:
                d["image_credit"] = credit
    d["tags"] = (d.get("tags") or "").split()
    d["discount"] = round(100 * (1 - d["price"] / d["mrp"])) if d["mrp"] else 0
    if cart is not None:
        d["in_cart"] = d["id"] in cart
    if wish is not None:
        d["in_wishlist"] = d["id"] in wish
    return d


def user_sets(uid):
    cart = {r["product_id"] for r in query("SELECT product_id FROM cart_items WHERE user_id=?", (uid,))}
    wish = {r["product_id"] for r in query("SELECT product_id FROM wishlist WHERE user_id=?", (uid,))}
    return cart, wish


def products_by_ids(ids, uid=None):
    if not ids:
        return []
    cart, wish = user_sets(uid) if uid else (None, None)
    marks = ",".join("?" * len(ids))
    rows = {r["id"]: r for r in query(f"SELECT * FROM products WHERE id IN ({marks})", tuple(ids))}
    return [product_json(rows[i], uid, cart, wish) for i in ids if i in rows]
