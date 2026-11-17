"""Admin analytics dashboard and model retraining."""
from flask import Blueprint, current_app, jsonify

from ..db import query
from ..services import admin_required, build_engine, load_metrics

bp = Blueprint("admin", __name__, url_prefix="/api/admin")


@bp.get("/dashboard")
@admin_required
def dashboard():
    one = lambda sql, p=(): query(sql, p, one=True)[0]  # noqa: E731
    users = one("SELECT COUNT(*) FROM users WHERE is_guest=0 AND is_admin=0")
    active = one("SELECT COUNT(DISTINCT user_id) FROM interactions")
    buyers = one("SELECT COUNT(DISTINCT user_id) FROM orders")
    orders = one("SELECT COUNT(*) FROM orders")
    revenue = one("SELECT COALESCE(SUM(total),0) FROM orders")
    imps = one("SELECT COUNT(*) FROM rec_impressions")
    clicks = one("SELECT COUNT(*) FROM rec_impressions WHERE clicked=1")
    kpis = {
        "users": users,
        "products": one("SELECT COUNT(*) FROM products"),
        "orders": orders,
        "revenue": revenue,
        "avg_order_value": round(revenue / orders) if orders else 0,
        "ctr": round(100 * clicks / imps, 2) if imps else 0,
        # view -> purchase conversion: share of product views that end in a purchase
        "conversion_rate": round(100 * one("SELECT COUNT(*) FROM interactions WHERE event_type='buy'")
                                 / max(1, one("SELECT COUNT(*) FROM interactions WHERE event_type='view'")), 2),
        "buyers": buyers,
        "active_users": active,
        "interactions": one("SELECT COUNT(*) FROM interactions"),
    }
    top_products = [dict(r) for r in query("""
        SELECT p.id, p.name, p.emoji, SUM(oi.qty) units, SUM(oi.qty*oi.price) revenue
        FROM order_items oi JOIN products p ON p.id=oi.product_id
        GROUP BY p.id ORDER BY units DESC LIMIT 8""")]
    by_category = [dict(r) for r in query("""
        SELECT p.category, SUM(oi.qty*oi.price) revenue, SUM(oi.qty) units
        FROM order_items oi JOIN products p ON p.id=oi.product_id
        GROUP BY p.category ORDER BY revenue DESC""")]
    events = [dict(r) for r in query("SELECT event_type, COUNT(*) n FROM interactions GROUP BY event_type ORDER BY n DESC")]
    daily = [dict(r) for r in query("""
        SELECT date(created_at) day, COUNT(*) orders, SUM(total) revenue FROM orders
        WHERE created_at >= date('now','-30 day') GROUP BY day ORDER BY day""")]
    return jsonify(kpis=kpis, top_products=top_products, by_category=by_category, events=events,
                   daily=daily, metrics=load_metrics())


@bp.post("/retrain")
@admin_required
def retrain():
    from ..train import train_and_evaluate
    metrics = train_and_evaluate(current_app.config["DATABASE"], current_app.config["MODEL_DIR"], verbose=False)
    build_engine(current_app)
    return jsonify(ok=True, metrics=metrics)
