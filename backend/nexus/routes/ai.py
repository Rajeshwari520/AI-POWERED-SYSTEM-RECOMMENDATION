"""Recommendations, AI search, shopping assistant and the user's taste profile."""
from flask import Blueprint, jsonify, request

from ..config import CATEGORIES, HYBRID_WEIGHTS
from ..db import execute, get_db, query
from ..recommender import describe, parse_query
from ..recommender.engine import nice
from ..services import (current_user_id, engine, load_metrics, log_event, products_by_ids, user_events,
                        user_vector)

bp = Blueprint("ai", __name__, url_prefix="/api")
CAT_LABELS = {c["key"]: c["short"] for c in CATEGORIES}

# Demo shoppers for presentations: each is a short list of (event, sku)
PERSONAS = {
    "gamer": [("view", "tufF15"), ("view", "victus"), ("wish", "op13r"), ("cart", "tufF15"), ("view", "xm5")],
    "fitness": [("view", "rev7"), ("wish", "softride"), ("view", "noisepro"), ("cart", "yogamat"), ("view", "whey")],
    "skincare": [("view", "niacin"), ("view", "nfw"), ("wish", "reequil"), ("cart", "niacin")],
    "ml_student": [("view", "ideap5"), ("view", "homl"), ("wish", "aspire7"), ("cart", "dlpy"), ("view", "mx3s")],
}


@bp.get("/recommendations")
def recommendations():
    uid = current_user_id()
    n = min(int(request.args.get("n", 12)), 40)
    w, strongest = user_vector(uid)
    exclude = [r["product_id"] for r in query("SELECT product_id FROM cart_items WHERE user_id=?", (uid,))]
    recs = engine().recommend(w, strongest, n=n, exclude_ids=exclude, category=request.args.get("category"))
    items = products_by_ids([r["product_id"] for r in recs], uid)
    meta = {r["product_id"]: r for r in recs}
    for it in items:
        it["reason"] = meta[it["id"]]["reason"]
        it["score"] = meta[it["id"]]["score"]
        it["components"] = meta[it["id"]]["components"]
    db = get_db()
    db.executemany("INSERT INTO rec_impressions (user_id, product_id) VALUES (?,?)", [(uid, it["id"]) for it in items])
    db.commit()
    return jsonify(products=items, cold_start=bool(w.sum() <= 0), weights=HYBRID_WEIGHTS)


@bp.post("/events")
def track_event():
    uid = current_user_id()
    data = request.get_json(silent=True) or {}
    etype = data.get("type")
    if etype not in ("view", "search", "wish", "cart", "buy"):
        return jsonify(error="Unknown event type"), 400
    log_event(uid, etype, data.get("product_id"), data.get("query"), data.get("source"))
    return jsonify(ok=True), 201


def _run_search(q):
    parsed = parse_query(q)
    uid = current_user_id()
    w, _ = user_vector(uid)
    results = engine().search(q, parsed, user_w=w)
    log_event(uid, "search", q=q)
    items = products_by_ids([r["product_id"] for r in results], uid)
    matched = {r["product_id"]: r["matched"] for r in results}
    for it in items:
        it["matched"] = [nice(t) for t in matched[it["id"]]]
    return parsed, items


@bp.post("/search")
def search():
    q = ((request.get_json(silent=True) or {}).get("q") or "").strip()
    if not q:
        return jsonify(error="Type something to search"), 400
    parsed, items = _run_search(q)
    understood = []
    if parsed["category"]:
        understood.append("category: " + CAT_LABELS[parsed["category"]])
    if parsed["tags"]:
        understood.append("needs: " + ", ".join(nice(t) for t in parsed["tags"]))
    if parsed["budget"]:
        understood.append(f"budget ≤ ₹{int(parsed['budget']):,}")
    return jsonify(query=q, parsed=parsed, understood=" · ".join(understood) or "keyword search", products=items)


@bp.post("/assistant")
def assistant():
    msg = ((request.get_json(silent=True) or {}).get("message") or "").strip()
    if not msg:
        return jsonify(error="Empty message"), 400
    parsed = parse_query(msg)
    if parsed["intent"] == "greeting":
        return jsonify(reply="Hi! Tell me what you're shopping for — a budget, a use case, a skin type — and I'll find the best matches.",
                       products=[], total=0)
    parsed, items = _run_search(msg)
    if not items:
        reply = (f"I couldn't find anything for {describe(parsed, CAT_LABELS)}. "
                 "Try raising the budget or asking for another category, e.g. \"budget earbuds\".")
        return jsonify(reply=reply, products=[], total=0, parsed=parsed)
    reply = (f"Here are my top picks for {describe(parsed, CAT_LABELS)}, ranked by how well they match, "
             "their ratings and your shopping history:")
    return jsonify(reply=reply, products=items[:3], total=len(items), parsed=parsed)


@bp.get("/profile")
def profile():
    uid = current_user_id()
    w, _ = user_vector(uid)
    events = user_events(uid, 15)
    names = {p["id"]: p["name"] for p in products_by_ids([e["product_id"] for e in events if e["product_id"]])}
    activity = [{"type": e["event_type"], "label": names.get(e["product_id"]) or e["query"], "at": e["created_at"]}
                for e in events]
    stats = query("""SELECT COUNT(*) total, SUM(event_type='view') views, SUM(event_type='cart') carts,
                     SUM(event_type='buy') buys FROM interactions WHERE user_id=?""", (uid,), one=True)
    return jsonify(taste=engine().taste_profile(w), activity=activity, stats=dict(stats), weights=HYBRID_WEIGHTS)


@bp.post("/profile/reset")
def reset_profile():
    uid = current_user_id()
    execute("DELETE FROM interactions WHERE user_id=?", (uid,))
    return jsonify(ok=True)


@bp.post("/profile/demo")
def demo_profile():
    uid = current_user_id()
    persona = (request.get_json(silent=True) or {}).get("persona")
    if persona not in PERSONAS:
        return jsonify(error="Unknown demo persona"), 400
    execute("DELETE FROM interactions WHERE user_id=?", (uid,))
    for etype, sku in PERSONAS[persona]:
        row = query("SELECT id FROM products WHERE sku=?", (sku,), one=True)
        if row:
            log_event(uid, etype, row["id"], source="demo")
    return jsonify(ok=True)


@bp.get("/model")
def model_info():
    eng = engine()
    return jsonify(
        weights=HYBRID_WEIGHTS,
        products=eng.n,
        vocabulary=len(eng.tfidf.vocabulary_),
        orders_learned=eng.n_orders,
        metrics=load_metrics(),
    )
