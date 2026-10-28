"""The demo switch and the bundled demo files. LOREG_DEMO=1 turns the demo on; any other value leaves it off."""

import os
from pathlib import Path

DEMO_DIR = Path(__file__).resolve().parent.parent / "data" / "demo"
DEMO_FILE = DEMO_DIR / "upload_demo_200.csv"
NEWS_ITEMS = DEMO_DIR / "news_items.json"    # the real items read in one scan, kept as stored copies so the demo shows real news when robots.txt times out
NEWS_COPY = DEMO_DIR / "news_copy.json"      # a stored copy of one real article, if one has been captured (scripts/capture_demo_news.py)


def demo_enabled() -> bool:
    return os.environ.get("LOREG_DEMO") == "1"
