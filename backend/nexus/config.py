"""Application configuration."""
import os

PKG_DIR = os.path.dirname(os.path.abspath(__file__))      # backend/nexus
BACKEND_DIR = os.path.dirname(PKG_DIR)                    # backend


class Config:
    SECRET_KEY = os.environ.get("SECRET_KEY", "change-this-secret-key-in-production")
    DATABASE = os.environ.get("DATABASE", os.path.join(BACKEND_DIR, "instance", "nexus.db"))
    MODEL_DIR = os.path.join(BACKEND_DIR, "models")
    DATA_DIR = os.path.join(BACKEND_DIR, "data")
    IMAGE_DIR = os.path.join(BACKEND_DIR, "images")   # drop <sku>.jpg/.png/.webp here for real product photos
    # React dev server address(es) allowed to call the API (comma separated)
    FRONTEND_ORIGINS = os.environ.get("FRONTEND_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173").split(",")
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    TESTING = False


class TestConfig(Config):
    TESTING = True
    DATABASE = os.path.join(BACKEND_DIR, "instance", "test.db")


# How strongly each user action signals interest (implicit feedback weights)
EVENT_WEIGHTS = {"view": 1.0, "search": 1.2, "wish": 2.5, "cart": 3.5, "buy": 5.0}

# Hybrid model blend: content-based + collaborative filtering + popularity.
# Defaults below; `python -m nexus.train` tunes them on validation users and
# saves the best blend to models/hybrid_weights.json, which is loaded here.
HYBRID_WEIGHTS = {"content": 0.50, "collab": 0.35, "popularity": 0.15}
_tuned = os.path.join(BACKEND_DIR, "models", "hybrid_weights.json")
if os.path.exists(_tuned):
    import json as _json
    with open(_tuned, encoding="utf-8") as _f:
        HYBRID_WEIGHTS.update(_json.load(_f))

CATEGORIES = [
    {"key": "mobiles", "label": "Mobiles & Electronics", "short": "Mobiles", "icon": "📱", "color": "#e8f0ff"},
    {"key": "laptops", "label": "Laptops & Computers", "short": "Laptops", "icon": "💻", "color": "#e6edff"},
    {"key": "electronics", "label": "Audio & Wearables", "short": "Gadgets", "icon": "🎧", "color": "#eef0ff"},
    {"key": "fashion", "label": "Fashion & Clothing", "short": "Fashion", "icon": "👕", "color": "#ffe9ec"},
    {"key": "footwear", "label": "Footwear", "short": "Footwear", "icon": "👟", "color": "#fff0e6"},
    {"key": "beauty", "label": "Beauty & Personal Care", "short": "Beauty", "icon": "🌸", "color": "#fdeaf3"},
    {"key": "home", "label": "Home & Kitchen", "short": "Home", "icon": "🛋️", "color": "#e3f6ee"},
    {"key": "groceries", "label": "Groceries & Food", "short": "Groceries", "icon": "🛒", "color": "#fff4dc"},
    {"key": "books", "label": "Books & Education", "short": "Books", "icon": "📚", "color": "#e7efff"},
    {"key": "sports", "label": "Sports & Fitness", "short": "Sports", "icon": "🏋️", "color": "#f1ebff"},
    {"key": "bags", "label": "Bags & Travel", "short": "Bags", "icon": "🎒", "color": "#e6f4ff"},
    {"key": "toys", "label": "Toys & Baby Products", "short": "Toys", "icon": "🧸", "color": "#fff0e3"},
]
CATEGORY_KEYS = {c["key"] for c in CATEGORIES}
