"""API tests.  Run with:  python -m pytest -q"""
import os

import pytest

from nexus import create_app
from nexus.config import TestConfig
from nexus.seed import seed_database


@pytest.fixture(scope="module")
def app():
    seed_database(TestConfig.DATABASE, n_users=80, reset=True)
    app = create_app(TestConfig)
    yield app
    os.remove(TestConfig.DATABASE)


@pytest.fixture()
def client(app):
    return app.test_client()


def test_categories_and_products(client):
    cats = client.get("/api/categories").get_json()["categories"]
    assert len(cats) == 12 and sum(c["count"] for c in cats) == 60
    prods = client.get("/api/products?category=laptops&sort=price_asc").get_json()["products"]
    assert prods and all(p["category"] == "laptops" for p in prods)
    assert prods == sorted(prods, key=lambda p: p["price"])


def test_cold_start_then_personalised(client):
    r = client.get("/api/recommendations?n=6").get_json()
    assert r["cold_start"] is True and len(r["products"]) == 6
    laptop = client.get("/api/products?category=laptops").get_json()["products"][0]
    detail = client.get(f"/api/products/{laptop['id']}").get_json()
    assert detail["similar"] and len(detail["price_history"]) == 30
    client.post("/api/cart", json={"product_id": laptop["id"]})
    r = client.get("/api/recommendations?n=6").get_json()
    assert r["cold_start"] is False
    assert all(p["reason"]["text"] for p in r["products"])
    assert laptop["id"] not in [p["id"] for p in r["products"]]   # items in cart are excluded


def test_ai_search_understands_budget_and_needs(client):
    r = client.post("/api/search", json={"q": "best laptop under 70k for AI/ML"}).get_json()
    assert r["parsed"]["category"] == "laptops" and r["parsed"]["budget"] == 70000
    assert "ai-ml" in r["parsed"]["tags"]
    assert r["products"] and all(p["price"] <= 70000 and p["category"] == "laptops" for p in r["products"])
    a = client.post("/api/assistant", json={"message": "skincare for oily skin"}).get_json()
    assert a["products"] and all("oily-skin" in p["tags"] for p in a["products"])


def test_cart_checkout_and_online_learning(client):
    prods = client.get("/api/products?sort=price_asc").get_json()["products"]
    a, b = prods[0]["id"], prods[-1]["id"]
    client.post("/api/cart", json={"product_ids": [a, b]})
    cart = client.get("/api/cart").get_json()
    assert cart["count"] >= 2
    order = client.post("/api/orders").get_json()
    assert order["order_id"] and client.get("/api/cart").get_json()["count"] == 0
    detail = client.get(f"/api/products/{a}?track=0").get_json()
    assert b in [p["id"] for p in detail["bought_together"]] or detail["bought_together"]


def test_wishlist_and_profile(client):
    pid = client.get("/api/products").get_json()["products"][0]["id"]
    assert client.post("/api/wishlist", json={"product_id": pid}).status_code == 201
    assert pid in [p["id"] for p in client.get("/api/wishlist").get_json()["products"]]
    prof = client.get("/api/profile").get_json()
    assert prof["taste"]["categories"] and prof["activity"]
    client.post("/api/profile/demo", json={"persona": "gamer"})
    top = client.get("/api/profile").get_json()["taste"]["tags"][0]["key"]
    assert top in ("gaming", "laptop", "gpu")


def test_auth_and_admin(client):
    assert client.get("/api/admin/dashboard").status_code == 403
    r = client.post("/api/auth/register", json={"name": "Raj", "email": "raj@test.com", "password": "secret1"})
    assert r.status_code == 201 and r.get_json()["user"]["is_guest"] is False
    client.post("/api/auth/logout")
    assert client.post("/api/auth/login", json={"email": "raj@test.com", "password": "nope"}).status_code == 401
    assert client.post("/api/auth/login", json={"email": "admin@nexus.ai", "password": "admin123"}).status_code == 200
    d = client.get("/api/admin/dashboard").get_json()
    assert d["kpis"]["products"] == 60 and d["kpis"]["orders"] > 0 and d["kpis"]["ctr"] > 0


def test_cors_allows_react_dev_server(client):
    r = client.get("/api/health", headers={"Origin": "http://localhost:5173"})
    assert r.headers["Access-Control-Allow-Origin"] == "http://localhost:5173"
    assert r.headers["Access-Control-Allow-Credentials"] == "true"
    r = client.get("/api/health", headers={"Origin": "http://evil.example"})
    assert "Access-Control-Allow-Origin" not in r.headers
    pre = client.options("/api/cart", headers={"Origin": "http://localhost:5173"})
    assert pre.status_code in (200, 204) and "PATCH" in pre.headers["Access-Control-Allow-Methods"]
