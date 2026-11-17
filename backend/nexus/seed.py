"""
Create the database and fill it with:
  * the product catalog (data/products.csv)
  * an admin account and a demo account
  * synthetic shoppers with realistic browsing / cart / purchase behaviour
    (so collaborative filtering has data to learn from on day one)
  * 60-day price history for the price-intelligence chart

Run:  python -m nexus.seed   (or it runs automatically from run.py)
"""
import csv
import os
import random
from datetime import datetime, timedelta, timezone

from werkzeug.security import generate_password_hash

from .config import Config
from .db import connect, init_schema

# Each persona likes certain tags. Users get one or two personas.
PERSONAS = {
    "gamer":        {"gaming": 3, "gpu": 2, "audio": 1, "fast-charging": 1, "earbuds": 1},
    "ml_student":   {"ai-ml": 3, "coding": 2, "student": 2, "laptop-accessory": 1, "book": 1},
    "fitness":      {"fitness": 3, "running": 2, "gym": 2, "healthy": 1, "smartwatch": 2, "yoga": 1},
    "oily_skin":    {"oily-skin": 3, "acne": 2, "skincare": 1, "sunscreen": 1},
    "dry_skin":     {"dry-skin": 3, "skincare": 1, "sensitive": 2, "serum": 1},
    "traveler":     {"travel": 3, "noise-cancelling": 2, "bag": 2, "luggage": 2, "phone-accessory": 1},
    "home_cook":    {"kitchen": 3, "cooking": 3, "healthy": 1, "appliance": 1},
    "apple_fan":    {"ios": 3, "premium": 2, "case": 1, "audio": 1},
    "android_user": {"android": 3, "phone": 2, "budget": 1, "fast-charging": 1, "earbuds": 1},
    "fashion_men":  {"men": 3, "casual": 2, "denim": 2, "shoes": 1, "winter": 1},
    "fashion_women": {"women": 3, "ethnic": 3, "festive": 2, "skincare": 1},
    "reader":       {"book": 3, "self-help": 2, "finance": 1, "study": 1},
    "parent":       {"kids": 3, "toys": 2, "creative": 1},
    "cricket_fan":  {"cricket": 3, "sports": 2, "football": 1},
    "budget_buyer": {"budget": 3, "earbuds": 1, "phone": 1},
}


def _ts(days_ago, rnd):
    t = datetime.now(timezone.utc) - timedelta(days=days_ago, seconds=rnd.randint(0, 86000))
    return t.strftime("%Y-%m-%d %H:%M:%S")


def load_products(conn, csv_path):
    with open(csv_path, encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    for r in rows:
        conn.execute(
            """INSERT INTO products (sku,name,category,brand,price,mrp,rating,reviews,emoji,spec,tags,description,image_url)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (r["sku"], r["name"], r["category"], r["brand"], float(r["price"]), float(r["mrp"]),
             float(r["rating"]), int(r["reviews"]), r["emoji"], r["spec"], r["tags"],
             r["description"], r.get("image_url") or None),
        )
    conn.commit()


def _affinity(product, persona_list):
    tags = set(product["tags"].split())
    score = 0.0
    for p in persona_list:
        score += sum(w for t, w in PERSONAS[p].items() if t in tags)
    return score


def generate_synthetic(conn, n_users=300, seed=42):
    rnd = random.Random(seed)
    products = [dict(r) for r in conn.execute("SELECT * FROM products")]
    names = list(PERSONAS)
    for u in range(n_users):
        personas = rnd.sample(names, rnd.choice([1, 1, 2]))
        uid = conn.execute(
            "INSERT INTO users (name, email, is_guest, is_synthetic, created_at) VALUES (?,?,0,1,?)",
            (f"Shopper {u + 1}", f"shopper{u + 1}@synthetic.local", _ts(90, rnd)),
        ).lastrowid
        aff = [(_affinity(p, personas) ** 2 + 0.05, p) for p in products]
        liked = [p for a, p in aff if a > 1]
        weights = [a for a, _ in aff]

        def pick():
            return rnd.choices(products, weights=weights, k=1)[0]

        for _session in range(rnd.randint(3, 9)):
            day = rnd.uniform(0, 60)
            anchor = pick()
            conn.execute("INSERT INTO interactions (user_id,product_id,event_type,created_at) VALUES (?,?,?,?)",
                         (uid, anchor["id"], "view", _ts(day, rnd)))
            for _ in range(rnd.randint(0, 3)):
                other = pick()
                conn.execute("INSERT INTO interactions (user_id,product_id,event_type,created_at) VALUES (?,?,?,?)",
                             (uid, other["id"], "view", _ts(day, rnd)))
            if rnd.random() < 0.15:
                conn.execute("INSERT INTO interactions (user_id,product_id,event_type,created_at) VALUES (?,?,?,?)",
                             (uid, anchor["id"], "wish", _ts(day, rnd)))
                conn.execute("INSERT OR IGNORE INTO wishlist (user_id,product_id) VALUES (?,?)", (uid, anchor["id"]))
            if rnd.random() < 0.45:
                basket = [anchor]
                # complementary item from a different category the persona also likes
                comp = [p for p in liked if p["category"] != anchor["category"] or "accessory" in p["tags"]]
                if comp and rnd.random() < 0.6:
                    basket.append(rnd.choice(comp))
                if rnd.random() < 0.6:
                    ts = _ts(day, rnd)
                    total = sum(p["price"] for p in basket)
                    oid = conn.execute("INSERT INTO orders (user_id,total,created_at) VALUES (?,?,?)",
                                       (uid, total, ts)).lastrowid
                    for p in basket:
                        conn.execute("INSERT INTO interactions (user_id,product_id,event_type,created_at) VALUES (?,?,?,?)",
                                     (uid, p["id"], "cart", ts))
                        conn.execute("INSERT INTO interactions (user_id,product_id,event_type,created_at) VALUES (?,?,?,?)",
                                     (uid, p["id"], "buy", ts))
                        conn.execute("INSERT INTO order_items (order_id,product_id,qty,price) VALUES (?,?,?,?)",
                                     (oid, p["id"], 1, p["price"]))
                else:
                    conn.execute("INSERT INTO interactions (user_id,product_id,event_type,created_at) VALUES (?,?,?,?)",
                                 (uid, anchor["id"], "cart", _ts(day, rnd)))
        # historical recommendation impressions -> clicks (for CTR on the admin dashboard)
        for _ in range(12):
            p = pick() if rnd.random() < 0.6 else rnd.choice(products)
            clicked = 1 if rnd.random() < min(0.6, 0.08 + 0.04 * _affinity(p, personas)) else 0
            conn.execute("INSERT INTO rec_impressions (user_id,product_id,clicked,created_at) VALUES (?,?,?,?)",
                         (uid, p["id"], clicked, _ts(rnd.uniform(0, 60), rnd)))
    conn.commit()


def generate_price_history(conn, days=60, seed=7):
    rnd = random.Random(seed)
    today = datetime.now(timezone.utc).date()
    for p in conn.execute("SELECT id, price, mrp FROM products").fetchall():
        price, mrp = p["price"], p["mrp"]
        v = price * rnd.uniform(1.0, 1.15)
        series = []
        for _ in range(days):
            v += (rnd.random() - 0.52) * price * 0.04
            v = min(mrp, max(price * 0.92, v))
            series.append(round(v))
        series[-1] = price
        for i, val in enumerate(series):
            day = today - timedelta(days=days - 1 - i)
            conn.execute("INSERT INTO price_history (product_id, day, price) VALUES (?,?,?)",
                         (p["id"], day.isoformat(), val))
    conn.commit()


def create_accounts(conn):
    conn.execute("INSERT INTO users (name,email,password_hash,is_admin,is_guest) VALUES (?,?,?,1,0)",
                 ("Admin", "admin@nexus.ai", generate_password_hash("admin123")))
    conn.execute("INSERT INTO users (name,email,password_hash,is_admin,is_guest) VALUES (?,?,?,0,0)",
                 ("Rajeshwari", "demo@nexus.ai", generate_password_hash("demo123")))
    conn.commit()


def seed_database(db_path=None, n_users=300, reset=False):
    db_path = db_path or Config.DATABASE
    if reset and os.path.exists(db_path):
        os.remove(db_path)
    conn = connect(db_path)
    init_schema(conn)
    if conn.execute("SELECT COUNT(*) FROM products").fetchone()[0] == 0:
        load_products(conn, os.path.join(Config.DATA_DIR, "products.csv"))
        create_accounts(conn)
        generate_synthetic(conn, n_users=n_users)
        generate_price_history(conn)
        print(f"Seeded database at {db_path}")
    conn.close()
    return db_path


if __name__ == "__main__":
    seed_database(reset=True)
