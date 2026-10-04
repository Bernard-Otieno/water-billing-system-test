import os
from datetime import datetime
from zoneinfo import ZoneInfo


def nairobi_today():
    return datetime.now(ZoneInfo("Africa/Nairobi")).date()


def static_url(filename: str) -> str:
    """Build a /static/... URL with a version query param derived from the
    file's last-modified time, so browsers only re-fetch it when it's actually changed.
    """
    full_path = os.path.join("app", "static", filename)
    version = int(os.path.getmtime(full_path))
    return f"/static/{filename}?v={version}"
