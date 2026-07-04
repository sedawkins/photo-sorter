"""server.py — FastAPI app for the calm, read-only archive muse.

Run from the repo root:  uvicorn gui.server:app --reload
Then open http://localhost:8000

Every endpoint is read-only. The only bytes that leave OneDrive are small
thumbnails, fetched on demand and cached on disk so we never re-pull them.
"""

import hashlib
import tempfile
from pathlib import Path

from fastapi import FastAPI, Query, Response
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from . import auth, data

app = FastAPI(title="Photo archive — a place to muse")

STATIC_DIR = Path(__file__).parent / "static"
CACHE_DIR = Path(tempfile.gettempdir()) / "photo_muse_thumbs"
CACHE_DIR.mkdir(parents=True, exist_ok=True)


@app.get("/api/summary")
def api_summary():
    return data.summary()


@app.get("/api/timeline")
def api_timeline():
    return data.timeline()


@app.get("/api/places")
def api_places():
    return data.places()


@app.get("/api/story")
def api_story():
    return data.story()


@app.get("/api/serendipity")
def api_serendipity():
    row = data.serendipity()
    if not row:
        return JSONResponse({"error": "no photo"}, status_code=404)
    return row


@app.get("/api/browse")
def api_browse(
    year: str | None = None,
    state: str | None = None,
    city: str | None = None,
    limit: int = Query(120, le=500),
    offset: int = 0,
):
    return data.browse(year=year, state=state, city=city, limit=limit, offset=offset)


@app.get("/api/thumb")
def api_thumb(path: str, size: str = "medium"):
    """Return a small thumbnail for a stored new_path, cached on disk."""
    key = hashlib.sha1(f"{size}:{path}".encode()).hexdigest()
    cached = CACHE_DIR / f"{key}.jpg"
    if cached.exists():
        return Response(cached.read_bytes(), media_type="image/jpeg")

    onedrive_path = data.onedrive_path_for(path)
    try:
        content = auth.fetch_thumbnail(onedrive_path, size=size)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=502)
    if content is None:
        return Response(status_code=404)
    try:
        cached.write_bytes(content)
    except Exception:
        pass
    return Response(content, media_type="image/jpeg")


app.mount("/", StaticFiles(directory=str(STATIC_DIR), html=True), name="static")
