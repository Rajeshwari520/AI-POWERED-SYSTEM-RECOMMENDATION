"""Tests for download_images.py with the network replaced by fake Wikimedia responses."""
import io
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import download_images as di  # noqa: E402

PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 100


def commons_reply(title, mime="image/jpeg", w=800, h=800, artist="<a href='x'>Jane Doe</a>", lic="CC BY-SA 4.0"):
    return {"query": {"pages": {"1": {"title": title, "index": 1, "imageinfo": [{
        "thumburl": "https://upload.wikimedia.org/thumb/x.jpg", "url": "https://upload.wikimedia.org/x.jpg",
        "descriptionurl": "https://commons.wikimedia.org/wiki/" + title, "mime": mime, "width": w, "height": h,
        "extmetadata": {"Artist": {"value": artist}, "LicenseShortName": {"value": lic}}}]}}}}


@pytest.fixture()
def tmp_images(tmp_path, monkeypatch):
    monkeypatch.setattr(di, "IMAGES", str(tmp_path))
    monkeypatch.setattr(di, "CREDITS", str(tmp_path / "credits.json"))
    return tmp_path


def test_search_parses_and_filters(monkeypatch):
    def fake(url, timeout=25):
        assert "filetype%3Abitmap" in url and di.HEADERS["User-Agent"]
        return json.dumps(commons_reply("File:iPhone.jpg")).encode()
    monkeypatch.setattr(di, "fetch", fake)
    c = di.search_commons("iPhone")
    assert c[0]["author"] == "Jane Doe" and c[0]["license"] == "CC BY-SA 4.0"
    monkeypatch.setattr(di, "fetch", lambda u, timeout=25: json.dumps(commons_reply("File:logo.svg", mime="image/svg+xml")).encode())
    assert di.search_commons("x") == []          # SVG logos are skipped
    monkeypatch.setattr(di, "fetch", lambda u, timeout=25: json.dumps(commons_reply("File:tiny.jpg", w=100, h=100)).encode())
    assert di.search_commons("x") == []          # tiny images are skipped


def test_downloads_with_fallback_query_and_credits(tmp_images, monkeypatch):
    calls = []

    def fake(url, timeout=25):
        calls.append(url)
        if "api.php" in url:
            if "iPhone+16" in url:                # first, most specific query finds nothing
                return json.dumps({"batchcomplete": ""}).encode()
            return json.dumps(commons_reply("File:iPhone 15.jpg")).encode()
        return b"JPEGDATA"
    monkeypatch.setattr(di, "fetch", fake)
    got, skipped, failed = di.from_commons(["iph16"], {"iph16": "iPhone 16"}, pause=0)
    assert (got, skipped, failed) == (1, 0, [])
    assert (tmp_images / "iph16.jpg").read_bytes() == b"JPEGDATA"
    credits = json.loads((tmp_images / "credits.json").read_text())
    assert credits["iph16"]["author"] == "Jane Doe"
    # second run keeps the existing photo unless --force
    assert di.from_commons(["iph16"], {}, pause=0) == (0, 1, [])


def test_not_found_and_csv_mode(tmp_images, monkeypatch):
    monkeypatch.setattr(di, "fetch", lambda u, timeout=25: json.dumps({}).encode() if "api.php" in u else PNG)
    assert di.from_commons(["lego"], {}, pause=0) == (0, 0, ["lego"])
    csv_path = tmp_images / "urls.csv"
    csv_path.write_text("sku,url,credit\nlego,https://example.com/lego.png,Me\n", encoding="utf-8")
    assert di.from_csv(str(csv_path)) == (1, 0, [])
    assert (tmp_images / "lego.png").exists()
