"""
Download REAL product photos into backend/images/  (run once, needs internet)

    python download_images.py              # free photos from Wikimedia Commons for every product
    python download_images.py --force      # re-download, replacing existing photos
    python download_images.py --only iph16 xm5
    python download_images.py --from-csv my_urls.csv   # your own photo links: columns sku,url

Photos from Wikimedia Commons are free to use with credit. The author and licence of
each photo are saved to images/credits.json and shown under the photo on the product page.
After downloading, refresh the website: photos replace the emoji automatically.
Any photo you don't like: delete it, or save your own as images/<sku>.jpg.
"""
import argparse
import csv
import html
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
IMAGES = os.path.join(HERE, "images")
PRODUCTS = os.path.join(HERE, "data", "products.csv")
CREDITS = os.path.join(IMAGES, "credits.json")
API = "https://commons.wikimedia.org/w/api.php"
# Wikimedia asks every script to identify itself with a descriptive User-Agent
HEADERS = {"User-Agent": "NEXUS-AI-student-project/1.0 (product image downloader; python-urllib)"}

# What to search for on Wikimedia Commons, most specific first.
QUERIES = {
    "iph16": ["iPhone 16", "iPhone 15", "iPhone 14"],
    "s24fe": ["Samsung Galaxy S24", "Samsung Galaxy S23", "Samsung Galaxy smartphone"],
    "op13r": ["OnePlus 12", "OnePlus 11", "OnePlus smartphone"],
    "rn14p": ["Redmi Note 13", "Redmi Note 12", "Xiaomi Redmi smartphone"],
    "px9a": ["Google Pixel 9", "Google Pixel 8", "Google Pixel smartphone"],
    "m35": ["Samsung Galaxy M", "Samsung Galaxy A54", "Samsung smartphone"],
    "ideap5": ["Lenovo IdeaPad", "Lenovo laptop"],
    "tufF15": ["ASUS TUF Gaming laptop", "ASUS ROG laptop", "gaming laptop"],
    "mba3": ["MacBook Air M2", "MacBook Air"],
    "victus": ["HP Victus", "HP Pavilion laptop", "HP laptop"],
    "aspire7": ["Acer Aspire laptop", "Acer laptop"],
    "vivobook": ["ASUS VivoBook", "ASUS laptop"],
    "xm5": ["Sony WH-1000XM5", "Sony WH-1000XM4", "Sony headphones"],
    "airdopes": ["true wireless earbuds", "wireless earbuds case"],
    "budsfe": ["Samsung Galaxy Buds", "wireless earbuds"],
    "noisepro": ["smartwatch fitness", "smartwatch"],
    "fbninja": ["smart watch black", "smartwatch"],
    "mx3s": ["Logitech MX Master", "wireless computer mouse"],
    "k380": ["Logitech K380", "Bluetooth keyboard", "compact keyboard"],
    "anker20": ["Anker power bank", "power bank"],
    "spigen": ["iPhone case", "smartphone case"],
    "charger": ["USB-C charger", "USB wall charger"],
    "levis511": ["Levi's jeans", "blue jeans denim"],
    "polo": ["polo shirt"],
    "kurta": ["kurta women", "salwar kameez"],
    "saree": ["Banarasi saree", "silk saree", "saree"],
    "hoodie": ["hoodie sweatshirt", "hoodie"],
    "rev7": ["Nike running shoes", "Nike shoe", "running shoes"],
    "softride": ["Puma running shoes", "Puma shoe", "running shoes"],
    "gowalk": ["Skechers shoes", "walking shoes"],
    "ultraboost": ["Adidas Ultraboost", "Adidas running shoes"],
    "cetaphil": ["Cetaphil", "moisturizing lotion bottle"],
    "niacin": ["serum dropper bottle", "cosmetic dropper bottle"],
    "nfw": ["Neutrogena", "face wash tube", "facial cleanser"],
    "reequil": ["sunscreen tube", "sunscreen lotion"],
    "hyaluronic": ["skincare serum bottle", "serum bottle"],
    "lipbalm": ["Nivea lip balm", "lip balm"],
    "airfry": ["air fryer"],
    "prestige": ["non-stick frying pan", "cookware set", "frying pan"],
    "milton": ["stainless steel water bottle", "insulated water bottle"],
    "mixer": ["mixer grinder", "electric blender"],
    "lamp": ["LED desk lamp", "desk lamp"],
    "atomic": ["self-help books", "books stack"],
    "dlpy": ["computer programming books", "programming books"],
    "homl": ["machine learning books", "technical books shelf"],
    "cleancode": ["programming books", "books on desk"],
    "psychmoney": ["books stack", "paperback books"],
    "yogamat": ["yoga mat"],
    "football": ["association football ball", "soccer ball"],
    "dumbbell": ["dumbbells", "dumbbell"],
    "cricketbat": ["cricket bat"],
    "safari": ["laptop backpack", "backpack"],
    "wildcraft": ["hiking backpack", "backpack"],
    "trolley": ["cabin suitcase", "trolley suitcase", "suitcase"],
    "oats": ["oatmeal bowl", "rolled oats"],
    "ybar": ["protein bar", "granola bar"],
    "whey": ["whey protein powder", "protein powder"],
    "greentea": ["green tea bag", "green tea"],
    "lego": ["Lego bricks", "Lego"],
    "rccar": ["radio-controlled car", "RC car toy"],
}


RETRY_WAITS = [10, 20, 40, 60]   # seconds to wait after "429 Too Many Requests"


def fetch(url, timeout=25):
    """Download a URL. If Wikimedia says "Too Many Requests", wait and try again."""
    for attempt in range(len(RETRY_WAITS) + 1):
        req = urllib.request.Request(url, headers=HEADERS)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            if e.code not in (429, 503) or attempt == len(RETRY_WAITS):
                raise
            wait = RETRY_WAITS[attempt]
            retry_after = e.headers.get("Retry-After") if e.headers else None
            if retry_after and retry_after.isdigit():
                wait = max(wait, int(retry_after))
            print(f"     (server busy, waiting {wait}s and trying again...)")
            time.sleep(wait)


def clean(text):
    """Strip the HTML that Commons puts in author/licence fields."""
    return html.unescape(re.sub(r"<[^>]+>", "", text or "")).strip()


def search_commons(query, width=600):
    """Return candidate photos for a query, best first."""
    params = {
        "action": "query", "format": "json", "generator": "search",
        "gsrsearch": f"filetype:bitmap {query}", "gsrnamespace": "6", "gsrlimit": "8",
        "prop": "imageinfo", "iiprop": "url|mime|size|extmetadata", "iiurlwidth": str(width),
    }
    data = json.loads(fetch(API + "?" + urllib.parse.urlencode(params)))
    pages = sorted((data.get("query") or {}).get("pages", {}).values(), key=lambda p: p.get("index", 99))
    out = []
    for p in pages:
        info = (p.get("imageinfo") or [{}])[0]
        if info.get("mime") not in ("image/jpeg", "image/png", "image/webp"):
            continue
        if info.get("width", 0) < 300 or info.get("height", 0) < 300:
            continue
        meta = info.get("extmetadata", {})
        out.append({
            "url": info.get("thumburl") or info.get("url"),
            "mime": info["mime"],
            "title": p.get("title", ""),
            "page": info.get("descriptionurl", ""),
            "author": clean(meta.get("Artist", {}).get("value")) or "Unknown",
            "license": clean(meta.get("LicenseShortName", {}).get("value")) or "see source",
        })
    return out


def existing(sku):
    for ext in (".jpg", ".jpeg", ".png", ".webp"):
        if os.path.exists(os.path.join(IMAGES, sku + ext)):
            return True
    return False


def save(sku, data, mime):
    ext = {"image/png": ".png", "image/webp": ".webp"}.get(mime, ".jpg")
    for old in (".jpg", ".jpeg", ".png", ".webp"):          # one photo per product
        path = os.path.join(IMAGES, sku + old)
        if os.path.exists(path):
            os.remove(path)
    with open(os.path.join(IMAGES, sku + ext), "wb") as f:
        f.write(data)


def load_credits():
    if os.path.exists(CREDITS):
        with open(CREDITS, encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_credits(credits):
    with open(CREDITS, "w", encoding="utf-8") as f:
        json.dump(credits, f, indent=2, ensure_ascii=False)


def from_commons(skus, names, force=False, pause=2.0):
    credits = load_credits()
    got, skipped, failed = 0, 0, []
    for sku in skus:
        if existing(sku) and not force:
            skipped += 1
            continue
        found = None
        for q in QUERIES.get(sku, [names.get(sku, sku)]):
            try:
                cands = search_commons(q)
            except Exception as e:  # network hiccup: try the next query
                print(f"  ! search failed for '{q}': {e}")
                cands = []
            if cands:
                found = cands[0]
                break
            time.sleep(pause)
        time.sleep(pause)   # be gentle with Wikimedia between searches and downloads
        if not found:
            failed.append(sku)
            print(f"  -- {sku:<11} no photo found")
            continue
        try:
            save(sku, fetch(found["url"]), found["mime"])
        except Exception as e:
            failed.append(sku)
            print(f"  -- {sku:<11} download failed: {e}")
            continue
        credits[sku] = {"author": found["author"], "license": found["license"],
                        "source": found["page"], "title": found["title"]}
        save_credits(credits)
        got += 1
        print(f"  OK {sku:<11} {names.get(sku, '')[:32]:<32} <- {found['title'][5:60]}")
        time.sleep(pause)
    return got, skipped, failed


def from_csv(path, force=False):
    credits = load_credits()
    got, skipped, failed = 0, 0, []
    with open(path, encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            sku, url = (row.get("sku") or "").strip(), (row.get("url") or "").strip()
            if not sku or not url:
                continue
            if existing(sku) and not force:
                skipped += 1
                continue
            try:
                data = fetch(url)
                mime = "image/png" if url.lower().endswith(".png") else "image/webp" if url.lower().endswith(".webp") else "image/jpeg"
                save(sku, data, mime)
                credits[sku] = {"author": row.get("credit") or "", "license": row.get("license") or "", "source": url, "title": ""}
                save_credits(credits)
                got += 1
                print(f"  OK {sku:<11} <- {url[:70]}")
            except Exception as e:
                failed.append(sku)
                print(f"  -- {sku:<11} {e}")
    return got, skipped, failed


def main(argv=None):
    ap = argparse.ArgumentParser(description="Download real product photos into backend/images")
    ap.add_argument("--force", action="store_true", help="replace photos that already exist")
    ap.add_argument("--only", nargs="*", help="only these SKUs")
    ap.add_argument("--from-csv", help="CSV with columns sku,url (optional: credit,license)")
    args = ap.parse_args(argv)
    os.makedirs(IMAGES, exist_ok=True)
    with open(PRODUCTS, encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    names = {r["sku"]: r["name"] for r in rows}
    skus = args.only or [r["sku"] for r in rows]

    print(f"Saving photos to {IMAGES}\n")
    if args.from_csv:
        got, skipped, failed = from_csv(args.from_csv, args.force)
    else:
        got, skipped, failed = from_commons(skus, names, args.force)
    print(f"\nDone: {got} downloaded, {skipped} already had a photo, {len(failed)} not found.")
    if failed:
        print("Not found:", ", ".join(failed), "\n-> save your own photo as images/<sku>.jpg for these.")
    print("Refresh the website to see the photos.")
    return 0 if got or skipped else 1


if __name__ == "__main__":
    sys.exit(main())