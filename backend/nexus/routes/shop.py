"""Catalog, product details, cart, wishlist and orders."""
from flask import Blueprint, jsonify, request

from ..config import CATEGORIES, CATEGORY_KEYS
from ..db import execute, get_db, query
from ..services import (current_user_id, engine, log_event, product_json, products_by_ids,
                        refresh_collaborative, user_sets, user_vector)

bp = Blueprint("shop", __name__, url_prefix="/api")


@bp.get("/categories")
def categories():
    counts = {r["category"]: r["n"] for r in query("SELECT category, COUNT(*) n FROM products GROUP BY category")}
    return jsonify(categories=[dict(c, count=counts.get(c["key"], 0)) for c in CATEGORIES])


@bp.get("/brands")
def brands():
    rows = query("""SELECT brand, COUNT(*) n, MIN(price) min_price, MAX(1 - price*1.0/mrp) best_off,
                           (SELECT emoji FROM products p2 WHERE p2.brand = p.brand ORDER BY reviews DESC LIMIT 1) emoji
                    FROM products p GROUP BY brand ORDER BY SUM(reviews) DESC""")
    return jsonify(brands=[dict(r, best_off=round(100 * r["best_off"])) for r in rows])


@bp.get("/products")
def list_products():
    uid = current_user_id()
    category = request.args.get("category")
    sort = request.args.get("sort", "rec")
    if category and category not in CATEGORY_KEYS:
        return jsonify(error="Unknown category"), 404
    if sort == "rec":
        w, strongest = user_vector(uid)
        recs = engine().recommend(w, strongest, n=1000, category=category, diversify=False)
        items = products_by_ids([r["product_id"] for r in recs], uid)
        reasons = {r["product_id"]: r["reason"] for r in recs}
        for it in items:
            it["reason"] = reasons.get(it["id"]) if w.sum() > 0 else None
        return jsonify(products=items, sort=sort)
    order = {"price_asc": "price ASC", "price_desc": "price DESC", "rating": "rating DESC, reviews DESC",
             "discount": "(1 - price*1.0/mrp) DESC"}.get(sort, "id")
    sql = "SELECT * FROM products" + (" WHERE category=?" if category else "") + f" ORDER BY {order}"
    cart, wish = user_sets(uid)
    rows = query(sql, (category,) if category else ())
    return jsonify(products=[product_json(r, uid, cart, wish) for r in rows], sort=sort)


@bp.get("/products/<int:pid>")
def product_detail(pid):
    uid = current_user_id()
    row = query("SELECT * FROM products WHERE id=?", (pid,), one=True)
    if not row:
        return jsonify(error="Product not found"), 404
    if request.args.get("track", "1") == "1":
        log_event(uid, "view", pid, source=request.args.get("source"))
    eng = engine()
    cart, wish = user_sets(uid)
    history = [dict(r) for r in query("SELECT day, price FROM price_history WHERE product_id=? ORDER BY day", (pid,))]
    prices = [h["price"] for h in history[-30:]] or [row["price"]]
    avg, low = sum(prices) / len(prices), min(prices)
    if row["price"] <= low * 1.01:
        verdict = {"level": "good", "text": "Lowest price in 30 days — great time to buy"}
    elif row["price"] < avg:
        verdict = {"level": "ok", "text": f"{round(100 * (1 - row['price'] / avg))}% below the 30-day average — good deal"}
    else:
        verdict = {"level": "wait", "text": "Above the 30-day average — consider waiting for a drop"}
    w, strongest = user_vector(uid)
    reason = None
    if w.sum() > 0:
        recs = eng.recommend(w, strongest, n=1000, diversify=False)
        reason = next((r["reason"] for r in recs if r["product_id"] == pid), None)
    return jsonify(
        product=product_json(row, uid, cart, wish),
        price_history=history[-30:],
        price_verdict=verdict,
        reason=reason,
        similar=products_by_ids(eng.similar(pid, 6), uid),
        bought_together=products_by_ids(eng.frequently_bought_together(pid, 2), uid),
    )


@bp.get("/deals")
def deals():
    uid = current_user_id()
    cart, wish = user_sets(uid)
    rows = query("SELECT * FROM products ORDER BY (1 - price*1.0/mrp) DESC LIMIT 24")
    return jsonify(products=[product_json(r, uid, cart, wish) for r in rows])


@bp.get("/banners")
def banners():
    """Best current deal in each headline category, for the home-page offer slider."""
    uid = current_user_id()
    cats = request.args.get("categories", "mobiles,laptops,electronics,fashion").split(",")
    ids = []
    for c in cats:
        # headline products only (no cases/chargers), best mix of discount and rating
        row = query("""SELECT id FROM products WHERE category=? AND tags NOT LIKE '%accessory%'
                       ORDER BY (1 - price*1.0/mrp) * rating * (1 + reviews/20000.0) DESC LIMIT 1""", (c,), one=True)
        if row:
            ids.append(row["id"])
    return jsonify(products=products_by_ids(ids, uid))


@bp.get("/trending")
def trending():
    uid = current_user_id()
    n = min(int(request.args.get("n", 18)), 60)
    return jsonify(products=products_by_ids(engine().trending(n), uid))


@bp.get("/compare")
def compare():
    uid = current_user_id()
    a, b = request.args.get("a", type=int), request.args.get("b", type=int)
    items = products_by_ids([a, b], uid)
    if len(items) != 2:
        return jsonify(error="Pick two different products"), 400
    w, _ = user_vector(uid)
    eng = engine()
    fit = None
    if w.sum() > 0:
        s = eng.content_sim @ w
        m = s.max() or 1
        fit = {str(p["id"]): round(100 * s[eng.idx[p["id"]]] / m) for p in items}
    pa, pb = items
    checks = [("price", pa["price"] < pb["price"], pb["price"] < pa["price"]),
              ("rating", pa["rating"] > pb["rating"], pb["rating"] > pa["rating"]),
              ("discount", pa["discount"] > pb["discount"], pb["discount"] > pa["discount"]),
              ("reviews", pa["reviews"] > pb["reviews"], pb["reviews"] > pa["reviews"])]
    sa, sb = sum(c[1] for c in checks), sum(c[2] for c in checks)
    winner = pa if sa >= sb else pb
    return jsonify(products=items, fit=fit, wins={str(pa["id"]): sa, str(pb["id"]): sb},
                   verdict=f"{winner['name']} wins on {max(sa, sb)} of 4 measures (price, rating, discount, reviews).")


# ------------------------------------------------------------------ cart
def _cart_payload(uid):
    rows = query("""SELECT p.*, c.qty FROM cart_items c JOIN products p ON p.id=c.product_id
                    WHERE c.user_id=? ORDER BY p.name""", (uid,))
    items = [dict(product_json(r), qty=r["qty"]) for r in rows]
    total = sum(i["price"] * i["qty"] for i in items)
    mrp = sum(i["mrp"] * i["qty"] for i in items)
    suggestions = products_by_ids(engine().complete_the_order([i["id"] for i in items], 2), uid)
    return {"items": items, "count": sum(i["qty"] for i in items), "total": total, "mrp": mrp,
            "savings": mrp - total, "suggestions": suggestions}


@bp.get("/cart")
def get_cart():
    return jsonify(_cart_payload(current_user_id()))


@bp.post("/cart")
def add_to_cart():
    uid = current_user_id()
    data = request.get_json(silent=True) or {}
    ids = data.get("product_ids") or [data.get("product_id")]
    for pid in ids:
        if not pid or not query("SELECT 1 FROM products WHERE id=?", (pid,), one=True):
            return jsonify(error="Product not found"), 404
        execute("""INSERT INTO cart_items (user_id,product_id,qty) VALUES (?,?,?)
                   ON CONFLICT(user_id,product_id) DO UPDATE SET qty = qty + excluded.qty""",
                (uid, pid, int(data.get("qty", 1))))
        log_event(uid, "cart", pid, source=data.get("source"))
    return jsonify(_cart_payload(uid)), 201


@bp.patch("/cart/<int:pid>")
def update_cart(pid):
    uid = current_user_id()
    qty = int((request.get_json(silent=True) or {}).get("qty", 1))
    if qty <= 0:
        execute("DELETE FROM cart_items WHERE user_id=? AND product_id=?", (uid, pid))
    else:
        execute("UPDATE cart_items SET qty=? WHERE user_id=? AND product_id=?", (qty, uid, pid))
    return jsonify(_cart_payload(uid))


@bp.delete("/cart/<int:pid>")
def remove_from_cart(pid):
    uid = current_user_id()
    execute("DELETE FROM cart_items WHERE user_id=? AND product_id=?", (uid, pid))
    return jsonify(_cart_payload(uid))


# -------------------------------------------------------------- wishlist
@bp.get("/wishlist")
def get_wishlist():
    uid = current_user_id()
    ids = [r["product_id"] for r in query("SELECT product_id FROM wishlist WHERE user_id=? ORDER BY created_at DESC", (uid,))]
    return jsonify(products=products_by_ids(ids, uid))


@bp.post("/wishlist")
def add_wishlist():
    uid = current_user_id()
    pid = (request.get_json(silent=True) or {}).get("product_id")
    if not query("SELECT 1 FROM products WHERE id=?", (pid,), one=True):
        return jsonify(error="Product not found"), 404
    execute("INSERT OR IGNORE INTO wishlist (user_id,product_id) VALUES (?,?)", (uid, pid))
    log_event(uid, "wish", pid)
    return jsonify(ok=True, count=query("SELECT COUNT(*) n FROM wishlist WHERE user_id=?", (uid,), one=True)["n"]), 201


@bp.delete("/wishlist/<int:pid>")
def remove_wishlist(pid):
    uid = current_user_id()
    execute("DELETE FROM wishlist WHERE user_id=? AND product_id=?", (uid, pid))
    return jsonify(ok=True, count=query("SELECT COUNT(*) n FROM wishlist WHERE user_id=?", (uid,), one=True)["n"])


# ---------------------------------------------------------------- orders
@bp.post("/orders")
def place_order():
    uid = current_user_id()
    cart = _cart_payload(uid)
    if not cart["items"]:
        return jsonify(error="Your cart is empty"), 400
    db = get_db()
    oid = db.execute("INSERT INTO orders (user_id,total) VALUES (?,?)", (uid, cart["total"])).lastrowid
    for it in cart["items"]:
        db.execute("INSERT INTO order_items (order_id,product_id,qty,price) VALUES (?,?,?,?)",
                   (oid, it["id"], it["qty"], it["price"]))
        db.execute("INSERT INTO interactions (user_id,product_id,event_type) VALUES (?,?,'buy')", (uid, it["id"]))
    db.execute("DELETE FROM cart_items WHERE user_id=?", (uid,))
    db.commit()
    refresh_collaborative()   # the new order immediately improves "Frequently Bought Together"
    return jsonify(order_id=oid, total=cart["total"], items=len(cart["items"]),
                   message="Order placed. The model has learned from this purchase."), 201


@bp.get("/orders")
def list_orders():
    uid = current_user_id()
    out = []
    for o in query("SELECT * FROM orders WHERE user_id=? ORDER BY id DESC", (uid,)):
        items = query("""SELECT p.id, p.name, p.emoji, p.category, oi.qty, oi.price FROM order_items oi
                         JOIN products p ON p.id=oi.product_id WHERE oi.order_id=?""", (o["id"],))
        out.append(dict(o, items=[dict(i) for i in items]))
    return jsonify(orders=out)
