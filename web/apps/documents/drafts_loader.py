__all__ = ()

import json
import logging
from pathlib import Path

from django.conf import settings

logger = logging.getLogger(__name__)

DRAFTS_DIR = Path(settings.BASE_DIR) / "static_dev" / "drafts"
MANIFEST_FILE = DRAFTS_DIR / "manifest.json"


def load_draft_categories():
    """
    Читает manifest.json и все .txt-файлы. Возвращает список категорий
    в виде [{code, title, icon, items: [{label, quality, text}]}].
    """
    if not MANIFEST_FILE.exists():
        logger.warning("drafts_loader: нет %s", MANIFEST_FILE)
        return []

    try:
        with open(MANIFEST_FILE, encoding="utf-8") as f:
            manifest = json.load(f)
    except Exception:
        logger.exception("drafts_loader: не удалось прочитать manifest.json")
        return []

    result = []
    for cat in manifest.get("categories", []):
        items = []
        for item in cat.get("items", []):
            path = DRAFTS_DIR / item["file"]
            if not path.exists():
                logger.warning("drafts_loader: файл не найден %s", path)
                continue
            try:
                text = path.read_text(encoding="utf-8").strip()
            except Exception:
                logger.exception("drafts_loader: не удалось прочитать %s", path)
                continue
            items.append({
                "label": item.get("label", item["file"]),
                "quality": item.get("quality", "medium"),
                "text": text,
            })
        if items:
            result.append({
                "code": cat.get("code", ""),
                "title": cat.get("title", ""),
                "icon": cat.get("icon", "bi-file-text"),
                "items": items,
            })
    return result
