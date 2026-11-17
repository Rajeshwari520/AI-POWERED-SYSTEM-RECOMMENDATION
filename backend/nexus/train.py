"""
Train, tune and evaluate the recommender.

    python -m nexus.train

1. Splits the synthetic shoppers into a VALIDATION half and a TEST half.
2. Grid-searches the hybrid blend weights (content / collaborative / popularity)
   on the validation users, picking the blend with the best NDCG@10.
3. Reports Precision@10, Recall@10, NDCG@10 and HitRate@10 on the unseen TEST users
   for Popularity vs Content-based vs Collaborative vs the tuned Hybrid.
4. Saves models/metrics.json and models/hybrid_weights.json (loaded by the app).

The printed table is ready for the "Results" chapter of the project report.
"""
import json
import os
import random
import time
from datetime import datetime, timezone

from .config import HYBRID_WEIGHTS, Config
from .db import connect
from .recommender import evaluate
from .services import load_frames

GRID_STEP = 0.1


def _grid():
    steps = [round(x * GRID_STEP, 2) for x in range(0, int(1 / GRID_STEP) + 1)]
    for c in steps:
        for f in steps:
            p = round(1 - c - f, 2)
            if p >= 0.05 and c > 0 and f > 0:      # keep all three signals in the blend
                yield {"content": c, "collab": f, "popularity": p}


def train_and_evaluate(db_path=None, model_dir=None, verbose=True):
    db_path = db_path or Config.DATABASE
    model_dir = model_dir or Config.MODEL_DIR
    conn = connect(db_path)
    products, interactions, order_items = load_frames(conn)
    synthetic = sorted(r[0] for r in conn.execute("SELECT id FROM users WHERE is_synthetic=1"))
    conn.close()
    t0 = time.time()

    random.Random(13).shuffle(synthetic)
    half = len(synthetic) // 2
    val_users, test_users = set(synthetic[:half]), set(synthetic[half:])
    inter = interactions[interactions["user_id"].isin(synthetic)]
    orders = order_items[order_items["user_id"].isin(synthetic)]

    # ---- 1. tune blend weights on validation users
    best, best_ndcg, tried = None, -1, []
    for wts in _grid():
        r = evaluate(products, inter, orders, users=val_users, strategies={"Hybrid (ours)": wts})
        ndcg = r["results"]["Hybrid (ours)"]["ndcg@10"]
        tried.append({**wts, "ndcg@10": ndcg})
        if ndcg > best_ndcg:
            best, best_ndcg = wts, ndcg
    if verbose:
        print(f"Tuned on {len(val_users)} validation users -> best blend {best} (val NDCG@10={best_ndcg:.3f})")

    # ---- 2. final report on held-out test users
    strategies = {
        "Popularity": {"content": 0, "collab": 0, "popularity": 1},
        "Content-based": {"content": 1, "collab": 0, "popularity": 0},
        "Collaborative": {"content": 0, "collab": 1, "popularity": 0},
        "Hybrid (ours)": best,
    }
    report = evaluate(products, inter, orders, users=test_users, strategies=strategies)
    report.update({
        "trained_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "seconds": round(time.time() - t0, 2),
        "n_products": len(products),
        "n_interactions": len(interactions),
        "n_orders": int(order_items["order_id"].nunique()),
        "validation_users": len(val_users),
        "best_weights": best,
        "grid_search": sorted(tried, key=lambda x: -x["ndcg@10"])[:10],
    })
    os.makedirs(model_dir, exist_ok=True)
    with open(os.path.join(model_dir, "metrics.json"), "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)
    with open(os.path.join(model_dir, "hybrid_weights.json"), "w", encoding="utf-8") as f:
        json.dump(best, f, indent=2)
    HYBRID_WEIGHTS.update(best)   # the running app picks up the tuned blend

    if verbose:
        k = report["k"]
        print(f"\nTest set: {report['test_users']} unseen users, {report['holdout_per_user']} hidden purchases each\n")
        print(f"{'Model':<16}{'Precision@' + str(k):>14}{'Recall@' + str(k):>12}{'NDCG@' + str(k):>10}{'HitRate@' + str(k):>13}")
        for name, m in report["results"].items():
            print(f"{name:<16}{m[f'precision@{k}']:>14.3f}{m[f'recall@{k}']:>12.3f}{m[f'ndcg@{k}']:>10.3f}{m[f'hit@{k}']:>13.3f}")
        print(f"\nSaved metrics and tuned weights to {model_dir}  ({report['seconds']}s)")
    return report


if __name__ == "__main__":
    train_and_evaluate()
