-- NEXUS AI database schema (SQLite)
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    name          TEXT NOT NULL DEFAULT 'Guest',
    email         TEXT UNIQUE,
    password_hash TEXT,
    is_admin      INTEGER NOT NULL DEFAULT 0,
    is_guest      INTEGER NOT NULL DEFAULT 1,
    is_synthetic  INTEGER NOT NULL DEFAULT 0,   -- generated training users
    created_at    TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS products (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    sku         TEXT UNIQUE NOT NULL,
    name        TEXT NOT NULL,
    category    TEXT NOT NULL,
    brand       TEXT NOT NULL,
    price       REAL NOT NULL,
    mrp         REAL NOT NULL,
    rating      REAL NOT NULL DEFAULT 0,
    reviews     INTEGER NOT NULL DEFAULT 0,
    emoji       TEXT,
    spec        TEXT,
    tags        TEXT,                -- space separated
    description TEXT,
    image_url   TEXT,
    stock       INTEGER NOT NULL DEFAULT 100
);

-- Every implicit-feedback signal: view, search, wish, cart, buy
CREATE TABLE IF NOT EXISTS interactions (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    product_id INTEGER REFERENCES products(id) ON DELETE CASCADE,
    event_type TEXT NOT NULL CHECK (event_type IN ('view','search','wish','cart','buy')),
    query      TEXT,
    source     TEXT,                 -- e.g. 'rec', 'search', 'assistant'
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS ix_inter_user ON interactions(user_id, created_at);

CREATE TABLE IF NOT EXISTS cart_items (
    user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    product_id INTEGER NOT NULL REFERENCES products(id) ON DELETE CASCADE,
    qty        INTEGER NOT NULL DEFAULT 1,
    PRIMARY KEY (user_id, product_id)
);

CREATE TABLE IF NOT EXISTS wishlist (
    user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    product_id INTEGER NOT NULL REFERENCES products(id) ON DELETE CASCADE,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (user_id, product_id)
);

CREATE TABLE IF NOT EXISTS orders (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    total      REAL NOT NULL,
    status     TEXT NOT NULL DEFAULT 'placed',
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS order_items (
    order_id   INTEGER NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
    product_id INTEGER NOT NULL REFERENCES products(id),
    qty        INTEGER NOT NULL,
    price      REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS price_history (
    product_id INTEGER NOT NULL REFERENCES products(id) ON DELETE CASCADE,
    day        TEXT NOT NULL,
    price      REAL NOT NULL,
    PRIMARY KEY (product_id, day)
);

-- Recommendations shown to users, used to measure click-through rate
CREATE TABLE IF NOT EXISTS rec_impressions (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    product_id INTEGER NOT NULL REFERENCES products(id) ON DELETE CASCADE,
    clicked    INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS ix_imp_user ON rec_impressions(user_id, product_id);
