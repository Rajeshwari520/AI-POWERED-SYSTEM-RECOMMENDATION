"""
Hybrid recommendation engine
============================

Three signals are blended into one score per product:

1. Content-based filtering (scikit-learn)
   Every product becomes a feature vector: TF-IDF over name/brand/tags/description
   plus one-hot category, brand and price band. Cosine similarity between these
   vectors tells us which products are alike.

2. Item-item collaborative filtering
   A user x item matrix of implicit feedback (view=1 ... buy=5) is built from the
   interactions table. Cosine similarity between item columns finds products that
   the same people engage with. Co-purchase counts from orders power
   "Frequently Bought Together".

3. Popularity
   rating x log(reviews) blended with recent interaction volume. Used alone for
   brand-new users (cold start).

final_score = 0.50 * content + 0.35 * collaborative + 0.15 * popularity
"""
from collections import defaultdict
import math
import threading

import numpy as np
import pandas as pd
from scipy import sparse
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity
from sklearn.preprocessing import OneHotEncoder, normalize

from ..config import CATEGORIES, EVENT_WEIGHTS, HYBRID_WEIGHTS

CATEGORY_LABELS = {c["key"]: c["short"] for c in CATEGORIES}
# tags too broad to be a convincing reason on their own
GENERIC_TAGS = {"accessory", "budget", "premium", "casual", "phone", "laptop", "book", "grocery"}

VERBS = {"view": "viewed", "search": "searched for", "wish": "wishlisted", "cart": "added to cart", "buy": "bought"}
NICE = {"ai-ml": "AI/ML", "oily-skin": "oily skin", "dry-skin": "dry skin", "noise-cancelling": "noise cancelling",
        "laptop-accessory": "laptop accessory", "phone-accessory": "phone accessory", "fast-charging": "fast charging"}


def nice(tag):
    return NICE.get(tag, tag)


def price_band(p):
    if p < 1000:
        return "under-1k"
    if p < 5000:
        return "1k-5k"
    if p < 20000:
        return "5k-20k"
    if p < 60000:
        return "20k-60k"
    return "60k-plus"


def _scale(v):
    m = v.max() if len(v) else 0
    return v / m if m > 0 else np.zeros_like(v)


class HybridRecommender:
    def __init__(self, weights=None, decay=0.97, max_per_category=3):
        self.weights = dict(weights or HYBRID_WEIGHTS)
        self.decay = decay
        self.max_per_category = max_per_category
        self.lock = threading.RLock()
        self.fitted = False

    # ------------------------------------------------------------------ fit
    def fit(self, products: pd.DataFrame, interactions: pd.DataFrame, order_items: pd.DataFrame):
        with self.lock:
            self.products = products.reset_index(drop=True).copy()
            self.products["tag_list"] = self.products["tags"].fillna("").str.split()
            self.ids = self.products["id"].to_numpy()
            self.idx = {int(pid): i for i, pid in enumerate(self.ids)}
            self.n = len(self.products)
            self._fit_content()
            self.fit_collaborative(interactions, order_items)
            self.fitted = True
        return self

    def _fit_content(self):
        p = self.products
        text = (p["name"] + " " + p["brand"] + " " + p["category"] + " "
                + (p["tags"].fillna("").str.replace("-", "_") + " ") * 2 + p["description"].fillna(""))
        self.tfidf = TfidfVectorizer(ngram_range=(1, 2), sublinear_tf=True, stop_words="english")
        self.X_text = normalize(self.tfidf.fit_transform(text))

        meta = pd.DataFrame({"category": p["category"], "brand": p["brand"], "band": p["price"].map(price_band)})
        self.encoder = OneHotEncoder(handle_unknown="ignore")
        X_meta = self.encoder.fit_transform(meta)
        block_w = np.concatenate([np.full(len(c), w) for c, w in zip(self.encoder.categories_, [1.0, 0.4, 0.4])])
        X_meta = X_meta @ sparse.diags(block_w)

        self.F = normalize(sparse.hstack([self.X_text, X_meta]).tocsr())
        self.content_sim = cosine_similarity(self.F)
        np.fill_diagonal(self.content_sim, 0.0)

    def fit_collaborative(self, interactions: pd.DataFrame, order_items: pd.DataFrame):
        """Rebuild only the behaviour-based parts. Cheap enough to run after every order."""
        with self.lock:
            df = interactions.dropna(subset=["product_id"]).copy()
            df = df[df["product_id"].astype(int).isin(self.idx)]
            if len(df):
                users = {u: i for i, u in enumerate(df["user_id"].unique())}
                rows = df["user_id"].map(users).to_numpy()
                cols = df["product_id"].astype(int).map(self.idx).to_numpy()
                vals = df["event_type"].map(EVENT_WEIGHTS).fillna(1.0).to_numpy()
                R = sparse.csr_matrix((vals, (rows, cols)), shape=(len(users), self.n))
                R.sum_duplicates()
                R.data = np.log1p(R.data)          # dampen heavy repeat viewers
                self.cf_sim = cosine_similarity(R.T)
                counts = np.asarray((R > 0).sum(axis=0)).ravel()
            else:
                self.cf_sim = np.zeros((self.n, self.n))
                counts = np.zeros(self.n)
            np.fill_diagonal(self.cf_sim, 0.0)

            C = np.zeros((self.n, self.n))
            if len(order_items):
                for _, grp in order_items.groupby("order_id"):
                    items = [self.idx[int(x)] for x in grp["product_id"] if int(x) in self.idx]
                    for a in items:
                        for b in items:
                            if a != b:
                                C[a, b] += 1
            self.copurchase = C

            p = self.products
            quality = _scale((p["rating"] * np.log10(p["reviews"] + 10)).to_numpy(dtype=float))
            self.popularity = 0.6 * quality + 0.4 * _scale(counts.astype(float))
            self.n_orders = int(order_items["order_id"].nunique()) if len(order_items) else 0

    # ------------------------------------------------------------ user side
    def user_vector(self, events):
        """events: list of dicts (product_id, event_type) newest first -> item-weight vector."""
        w = np.zeros(self.n)
        strongest = {}
        for rank, e in enumerate(events):
            pid = e.get("product_id")
            if pid is None or int(pid) not in self.idx:
                continue
            i = self.idx[int(pid)]
            ew = EVENT_WEIGHTS.get(e["event_type"], 1.0)
            w[i] += ew * (self.decay ** rank)            # recent actions count more
            if ew > EVENT_WEIGHTS.get(strongest.get(i, "view"), 0) or i not in strongest:
                strongest[i] = e["event_type"]
        return w, strongest

    def tag_vector(self, tags, category=None):
        """Turn search intent (tags/category) into a soft item-weight vector."""
        w = np.zeros(self.n)
        for i, row in self.products.iterrows():
            hits = len(set(tags) & set(row["tag_list"]))
            if hits or (category and row["category"] == category):
                w[i] = 0.3 * hits + (0.2 if category and row["category"] == category else 0)
        return w

    # ------------------------------------------------------------ scoring
    def score(self, w, weights=None):
        wts = weights or self.weights
        if w.sum() <= 0:
            zero = np.zeros(self.n)
            return self.popularity.copy(), {"content": zero, "collab": zero, "popularity": self.popularity}
        content = _scale(self.content_sim @ w)
        collab = _scale(0.7 * _scale(self.cf_sim @ w) + 0.3 * _scale(self.copurchase @ w))
        total = wts["content"] * content + wts["collab"] * collab + wts["popularity"] * self.popularity
        total = np.where(w > 0, total * 0.8, total)   # nudge toward discovery
        return total, {"content": content, "collab": collab, "popularity": self.popularity}

    def recommend(self, w, strongest=None, n=10, exclude_ids=(), category=None, diversify=True, weights=None):
        with self.lock:
            total, parts = self.score(w, weights)
            mask = np.ones(self.n, dtype=bool)
            for pid in exclude_ids:
                if int(pid) in self.idx:
                    mask[self.idx[int(pid)]] = False
            if category:
                mask &= (self.products["category"] == category).to_numpy()
            order = [i for i in np.argsort(-total) if mask[i]]
            out, per_cat = [], defaultdict(int)
            for i in order:
                cat = self.products.at[i, "category"]
                if diversify and not category and per_cat[cat] >= self.max_per_category:
                    continue
                per_cat[cat] += 1
                out.append({
                    "product_id": int(self.ids[i]),
                    "score": round(float(total[i]), 4),
                    "components": {k: round(float(v[i]), 3) for k, v in parts.items()},
                    "reason": self.explain(i, w, strongest or {}, parts),
                })
                if len(out) >= n:
                    break
            return out

    def explain(self, i, w, strongest, parts):
        """Turn the score into a sentence a shopper can trust.
        A reason is only shown when the link is genuinely strong; otherwise we fall back
        to a softer interest-based or popularity reason instead of a far-fetched one."""
        p = self.products
        if w.sum() <= 0:
            return {"type": "popular", "text": f"Top rated · {int(p.at[i, 'reviews']):,} reviews"}
        inter = np.array([j for j in np.flatnonzero(w) if j != i])
        if len(inter):
            csim = self.content_sim[i, inter]
            cop = self.copurchase[i, inter]
            cf = self.cf_sim[i, inter]

            def content_reason():
                j = inter[int(np.argmax(csim * w[inter]))]
                shared = [t for t in p.at[i, "tag_list"] if t in p.at[j, "tag_list"]][:2]
                verb = VERBS.get(strongest.get(j, "view"), "viewed")
                text = f"Because you {verb} {_short(p.at[j, 'name'])}"
                if shared:
                    text += " · " + ", ".join(nice(t) for t in shared)
                return {"type": "content", "based_on": int(self.ids[j]), "text": text}

            # 1. a clearly similar product is the most intuitive reason
            if csim.max() >= 0.25:
                return content_reason()
            # 2. strong behaviour links: bought together repeatedly, or a very similar audience
            if cop.max() >= 2:
                j = inter[int(np.argmax(cop * w[inter]))]
                return {"type": "collab", "based_on": int(self.ids[j]),
                        "text": f"Often bought together with {_short(p.at[j, 'name'])}"}
            if cf.max() >= 0.30:
                j = inter[int(np.argmax(cf * w[inter]))]
                return {"type": "collab", "based_on": int(self.ids[j]),
                        "text": f"Shoppers who liked {_short(p.at[j, 'name'])} also liked this"}
            # 3. moderately similar product
            if csim.max() >= 0.18:
                return content_reason()
        # 4. shares a tag the shopper keeps engaging with
        tag_w = defaultdict(float)
        for j in np.flatnonzero(w):
            for t in p.at[j, "tag_list"]:
                tag_w[t] += w[j]
        common = [t for t in p.at[i, "tag_list"] if t in tag_w and t not in GENERIC_TAGS]
        if common:
            best = max(common, key=lambda t: tag_w[t])
            return {"type": "interest", "text": f"Matches your interest in {nice(best)}"}
        return {"type": "popular", "text": f"Popular in {CATEGORY_LABELS.get(p.at[i, 'category'], p.at[i, 'category'].title())}"}

    # ------------------------------------------------------- item side
    def similar(self, product_id, n=6):
        i = self.idx[int(product_id)]
        s = self.content_sim[i] + 0.05 * self.popularity
        s[i] = -1
        return [int(self.ids[j]) for j in np.argsort(-s)[:n]]

    def frequently_bought_together(self, product_id, n=2):
        i = self.idx[int(product_id)]
        s = self.copurchase[i] + 0.3 * self.cf_sim[i]
        cat = self.products.at[i, "category"]
        ranked = [j for j in np.argsort(-s) if s[j] > 0 and j != i]
        cross = [j for j in ranked if self.products.at[j, "category"] != cat or "accessory" in self.products.at[j, "tag_list"]]
        picks = (cross + [j for j in ranked if j not in cross])[:n]
        return [int(self.ids[j]) for j in picks]

    def complete_the_order(self, cart_ids, n=2):
        idxs = [self.idx[int(x)] for x in cart_ids if int(x) in self.idx]
        if not idxs:
            return []
        s = self.copurchase[idxs].sum(axis=0) + 0.3 * self.cf_sim[idxs].sum(axis=0)
        s[idxs] = -1
        return [int(self.ids[j]) for j in np.argsort(-s)[:n] if s[j] > 0]

    def trending(self, n=12):
        p = self.products
        discount = 1 - p["price"] / p["mrp"]
        s = self.popularity * (1 + discount.to_numpy()) + 0.02 * self.copurchase.sum(axis=1)
        return [int(self.ids[j]) for j in np.argsort(-s)[:n]]

    def taste_profile(self, w):
        cats, tags = defaultdict(float), defaultdict(float)
        for i in np.flatnonzero(w):
            cats[self.products.at[i, "category"]] += w[i]
            for t in self.products.at[i, "tag_list"]:
                tags[t] += w[i]

        def top(d, k):
            if not d:
                return []
            m = max(d.values())
            return [{"key": key, "label": nice(key), "pct": round(100 * v / m)} for key, v in
                    sorted(d.items(), key=lambda x: -x[1])[:k]]
        return {"categories": top(cats, 5), "tags": top(tags, 6)}

    # ------------------------------------------------------- search
    def search(self, text, parsed, user_w=None, n=60):
        """Rank products for a natural-language query."""
        p = self.products
        q = self.tfidf.transform([text.replace("-", "_")])
        text_sim = (self.X_text @ normalize(q).T).toarray().ravel()
        tag_hits = np.array([len(set(parsed["tags"]) & set(t)) for t in p["tag_list"]], dtype=float)
        pers = _scale(self.content_sim @ user_w) if user_w is not None and user_w.sum() > 0 else np.zeros(self.n)
        trend_boost = (1 - p["price"] / p["mrp"]).to_numpy() if parsed.get("trending") else 0
        s = 2.5 * tag_hits + 3.0 * text_sim + 1.2 * self.popularity + 0.6 * pers + trend_boost
        mask = np.ones(self.n, dtype=bool)
        if parsed.get("category"):
            mask &= (p["category"] == parsed["category"]).to_numpy()
        if parsed.get("budget"):
            mask &= (p["price"] <= parsed["budget"]).to_numpy()
        if parsed.get("min_price"):
            mask &= (p["price"] >= parsed["min_price"]).to_numpy()
        if parsed["tags"] and not parsed.get("category"):
            mask &= tag_hits > 0
        if not parsed["tags"] and not parsed.get("category") and not parsed.get("budget"):
            mask &= text_sim > 0.02
        order = [j for j in np.argsort(-s) if mask[j]][:n]
        return [{"product_id": int(self.ids[j]), "score": round(float(s[j]), 3),
                 "matched": [t for t in p.at[j, "tag_list"] if t in parsed["tags"]]} for j in order]


def _short(name, k=26):
    return name if len(name) <= k else name[: k - 1] + "…"


# ---------------------------------------------------------------- evaluation
def evaluate(products, interactions, order_items, k=10, holdout=2, min_items=6, strategies=None, users=None):
    """
    Offline evaluation with a per-user hold-out split.
    For every synthetic user with enough history, the last `holdout` items they
    bought or carted are hidden. The model trains on everything else and must
    rank the hidden items in its top-k.
    """
    df = interactions.dropna(subset=["product_id"]).copy()
    df["product_id"] = df["product_id"].astype(int)
    df = df.sort_values("created_at")
    test = {}
    drop_idx = []
    for uid, g in df.groupby("user_id"):
        if users is not None and uid not in users:
            continue
        if g["product_id"].nunique() < min_items:
            continue
        pos = g[g["event_type"].isin(["buy", "cart"])]["product_id"].drop_duplicates(keep="last").tolist()
        if len(pos) < holdout:
            continue
        hidden = pos[-holdout:]
        test[uid] = set(hidden)
        drop_idx.extend(g[g["product_id"].isin(hidden)].index.tolist())
    train = df.drop(index=drop_idx)
    hidden_pairs = {(u, p) for u, ps in test.items() for p in ps}
    oi = order_items
    # remove hidden purchases from co-purchase data too (no peeking at the answers)
    if "user_id" in oi.columns:
        oi = oi[[(u, p) not in hidden_pairs for u, p in zip(oi["user_id"], oi["product_id"])]]

    model = HybridRecommender(weights=strategies.get("Hybrid (ours)")).fit(products, train, oi)
    strategies = strategies or {
        "Popularity": {"content": 0, "collab": 0, "popularity": 1},
        "Content-based": {"content": 1, "collab": 0, "popularity": 0},
        "Collaborative": {"content": 0, "collab": 1, "popularity": 0},
        "Hybrid (ours)": HYBRID_WEIGHTS,
    }
    results = {name: {"precision": [], "recall": [], "ndcg": [], "hit": []} for name in strategies}
    for uid, hidden in test.items():
        g = train[train["user_id"] == uid].sort_values("created_at", ascending=False)
        events = g[["product_id", "event_type"]].to_dict("records")
        w, strongest = model.user_vector(events)
        seen = set(g["product_id"])
        for name, wts in strategies.items():
            if name == "Popularity":
                ranked = [int(model.ids[j]) for j in np.argsort(-model.popularity) if int(model.ids[j]) not in seen][:k]
            else:
                recs = model.recommend(w, strongest, n=k, exclude_ids=seen, diversify=False, weights=wts)
                ranked = [r["product_id"] for r in recs]
            hits = [1 if pid in hidden else 0 for pid in ranked]
            dcg = sum(h / math.log2(r + 2) for r, h in enumerate(hits))
            idcg = sum(1 / math.log2(r + 2) for r in range(min(len(hidden), k)))
            results[name]["precision"].append(sum(hits) / k)
            results[name]["recall"].append(sum(hits) / len(hidden))
            results[name]["ndcg"].append(dcg / idcg)
            results[name]["hit"].append(1 if sum(hits) else 0)
    summary = {name: {f"{m}@{k}": round(float(np.mean(v)), 4) for m, v in r.items()} for name, r in results.items()}
    return {"k": k, "test_users": len(test), "holdout_per_user": holdout, "results": summary}
