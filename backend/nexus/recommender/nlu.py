"""
Natural-language query understanding for AI Search and the shopping assistant.
Extracts category, needs (tags), budget and intent from plain English such as
"best laptop under 70k for AI/ML" or "skincare for oily skin".
"""
import re

KEYWORDS = [
    (r"\b(phones?|mobiles?|smartphones?|iphone|android phone)\b", "cat", "mobiles"),
    (r"\b(laptops?|notebooks?|macbook)\b", "cat", "laptops"),
    (r"\b(headphones?|earbuds?|earphones?|audio|airpods)\b", "tag", "audio"),
    (r"\b(smart ?watch(es)?|watch(es)?)\b", "tag", "smartwatch"),
    (r"\b(shoes?|sneakers?|footwear)\b", "cat", "footwear"),
    (r"\b(skin|skincare|face ?wash|moisturi[sz]er|serum|sunscreen)\b", "cat", "beauty"),
    (r"\b(books?|novel|read)\b", "cat", "books"),
    (r"\b(kitchen|cook|cooking|appliance)\b", "cat", "home"),
    (r"\b(bags?|backpacks?|luggage|trolley)\b", "cat", "bags"),
    (r"\b(toys?|kids|children)\b", "cat", "toys"),
    (r"\b(jeans|shirts?|t-?shirts?|kurta|saree|hoodie|clothes|clothing|fashion)\b", "cat", "fashion"),
    (r"\b(protein|snacks?|grocery|groceries|tea|oats)\b", "cat", "groceries"),
    (r"\b(ai|ml|ai/ml|machine learning|deep learning|data science)\b", "tag", "ai-ml"),
    (r"\b(gaming|gamer|games?)\b", "tag", "gaming"),
    (r"\boily\b", "tag", "oily-skin"),
    (r"\bdry\b", "tag", "dry-skin"),
    (r"\bacne|pimples?\b", "tag", "acne"),
    (r"\b(running|run|jogging|marathon)\b", "tag", "running"),
    (r"\b(camera|photos?|photography)\b", "tag", "camera"),
    (r"\b(travel|trip|flight)\b", "tag", "travel"),
    (r"\b(budget|cheap|affordable|low price)\b", "tag", "budget"),
    (r"\b(student|college|study)\b", "tag", "student"),
    (r"\b(coding|programming|developer|code)\b", "tag", "coding"),
    (r"\b(gym|fitness|workout|yoga)\b", "tag", "fitness"),
    (r"\bnoise.?cancel", "tag", "noise-cancelling"),
    (r"\b(healthy|diet|health)\b", "tag", "healthy"),
    (r"\b(ethnic|festive|wedding|diwali)\b", "tag", "ethnic"),
    (r"\b(cricket)\b", "tag", "cricket"),
    (r"\b(battery|long battery)\b", "tag", "battery"),
    (r"\b(premium|flagship|high.?end)\b", "tag", "premium"),
    (r"\b(men|mens|men's)\b", "tag", "men"),
    (r"\b(women|womens|women's|ladies)\b", "tag", "women"),
]
_COMPILED = [(re.compile(p), t, v) for p, t, v in KEYWORDS]
_BUDGET = re.compile(r"(?:under|below|less than|within|up ?to|max|budget(?: of)?|around)\s*(?:rs\.?|₹|inr)?\s*([\d,.]+)\s*(k|thousand|lakh|l)?\b")
_MIN = re.compile(r"(?:above|over|more than|min(?:imum)?)\s*(?:rs\.?|₹|inr)?\s*([\d,.]+)\s*(k|thousand|lakh|l)?\b")


def _amount(num, unit):
    v = float(num.replace(",", ""))
    if unit in ("k", "thousand"):
        v *= 1000
    elif unit in ("lakh", "l"):
        v *= 100000
    return v


def parse_query(text):
    s = text.lower()
    category, tags = None, []
    for rx, kind, value in _COMPILED:
        if rx.search(s):
            if kind == "cat" and category is None:
                category = value
            elif kind == "tag" and value not in tags:
                tags.append(value)
    budget = min_price = None
    m = _BUDGET.search(s)
    if m:
        budget = _amount(m.group(1), m.group(2))
    m = _MIN.search(s)
    if m:
        min_price = _amount(m.group(1), m.group(2))
    return {
        "category": category,
        "tags": tags,
        "budget": budget,
        "min_price": min_price,
        "trending": bool(re.search(r"\b(trending|popular|hot|best ?sell)", s)),
        "intent": "greeting" if re.fullmatch(r"\s*(hi|hello|hey|hii+)\W*", s) else "search",
    }


def describe(parsed, category_labels):
    parts = []
    if parsed["tags"]:
        from .engine import nice
        parts.append(" + ".join(nice(t) for t in parsed["tags"]))
    if parsed["category"]:
        parts.append(category_labels.get(parsed["category"], parsed["category"]).lower())
    if parsed["budget"]:
        parts.append(f"under ₹{int(parsed['budget']):,}")
    return " ".join(parts) or "your search"
