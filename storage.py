import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

CACHE_DIR = Path(__file__).resolve().parent / "cache"
CACHE_FILE = CACHE_DIR / "sent_offers.json"
MAX_CACHE_SIZE = 2000

CACHE_DIR.mkdir(parents=True, exist_ok=True)


def load_sent_ids() -> dict:
    if not CACHE_FILE.exists():
        logger.info("No cache file found, starting fresh")
        return {}
    try:
        with open(CACHE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            total = len(data)
            if total > MAX_CACHE_SIZE:
                logger.info("Cache cheia (%d), mantendo as %d mais recentes",
                            total, MAX_CACHE_SIZE)
                data = dict(list(data.items())[-MAX_CACHE_SIZE:])
            return data
        logger.warning("Invalid cache format, resetting")
        return {}
    except (json.JSONDecodeError, OSError) as e:
        logger.warning("Failed to load cache (%s), resetting", e)
        return {}


def save_sent_ids(ids: dict):
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    trimmed = dict(list(ids.items())[-MAX_CACHE_SIZE:])
    with open(CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(trimmed, f, ensure_ascii=False)
    logger.info("Saved %d sent offer IDs to cache", len(trimmed))
