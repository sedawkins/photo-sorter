"""
server.py — FastAPI backend for the photo tagger GUI.
Runs on the Azure VM. Vercel serves the static frontend which calls this API.

Start with:
    uvicorn tagger.server:app --host 0.0.0.0 --port 8000
"""

import os
import time
import logging
import uuid
from contextvars import ContextVar
from functools import lru_cache
from pathlib import Path

import requests
from fastapi import FastAPI, HTTPException, Request, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response

from pydantic import BaseModel
from tagger.data import (
    connect, get_stats, get_years, get_months, get_photos_by_month,
    get_locations, get_years_for_location, get_photos_by_location,
    onedrive_path_for_photo,
    get_clumps, get_clump_photos, tag_clump, trash_clump, save_ai_hint,
)

# ── Config ────────────────────────────────────────────────────────────────────

APP_DIR = Path(__file__).parent.parent
SYSTEM_DIR = APP_DIR / "_system"
DB_PATH = SYSTEM_DIR / "photo_sorter.db"
THUMB_CACHE_DIR = SYSTEM_DIR / "thumb_cache"
THUMB_CACHE_DIR.mkdir(parents=True, exist_ok=True)

SORTED_ROOT = os.environ.get("SORTED_ROOT", "/Photos/Sorted")
API_KEY = os.environ.get("PHOTO_API_KEY", "")
VERCEL_ORIGIN = os.environ.get("VERCEL_ORIGIN", "https://photo-sorter-git-master-scotts-photo-sorter.vercel.app")

logger = logging.getLogger("tagger")

# ── Trace ID (per-request context) ───────────────────────────────────────────

_trace_id: ContextVar[str] = ContextVar("trace_id", default="-")


class _TraceFilter(logging.Filter):
    def filter(self, record):
        record.trace_id = _trace_id.get()
        return True


_handler = logging.StreamHandler()
_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s [%(trace_id)s] %(message)s"))
_handler.addFilter(_TraceFilter())
logging.getLogger("tagger").addHandler(_handler)
logging.getLogger("tagger").setLevel(logging.INFO)


# ── App ───────────────────────────────────────────────────────────────────────

app = FastAPI(title="Photo Sorter API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[VERCEL_ORIGIN, "http://localhost:5500", "http://127.0.0.1:5500"],
    allow_methods=["GET"],
    allow_headers=["*"],
)


@app.middleware("http")
async def trace_middleware(request: Request, call_next):
    # Accept X-Request-ID from proxy (so Vercel and VM share the same ID),
    # or mint a new one if the request arrives directly.
    trace_id = request.headers.get("X-Request-ID") or uuid.uuid4().hex[:12]
    token = _trace_id.set(trace_id)
    logger.info(f"{request.method} {request.url.path}")
    try:
        response = await call_next(request)
        response.headers["X-Trace-ID"] = trace_id
        logger.info(f"{request.method} {request.url.path} → {response.status_code}")
        return response
    except Exception as exc:
        logger.exception(f"{request.method} {request.url.path} → unhandled exception")
        raise
    finally:
        _trace_id.reset(token)


# ── Auth middleware ───────────────────────────────────────────────────────────

async def require_api_key(request: Request):
    if not API_KEY:
        return  # No key configured — dev mode, allow all
    key = request.headers.get("X-API-Key", "")
    if key != API_KEY:
        raise HTTPException(status_code=401, detail="Invalid API key")


# ── DB connection (per request) ───────────────────────────────────────────────

def get_db():
    conn = connect(DB_PATH)
    try:
        yield conn
    finally:
        conn.close()


# ── Routes ────────────────────────────────────────────────────────────────────

@app.get("/api/stats", dependencies=[Depends(require_api_key)])
def stats(conn=Depends(get_db)):
    return get_stats(conn)


@app.get("/api/years", dependencies=[Depends(require_api_key)])
def years(conn=Depends(get_db)):
    return get_years(conn)


@app.get("/api/years/{year}/months", dependencies=[Depends(require_api_key)])
def months(year: str, conn=Depends(get_db)):
    return get_months(conn, year)


@app.get("/api/years/{year}/months/{month}/photos", dependencies=[Depends(require_api_key)])
def photos_by_month(year: str, month: str,
                    limit: int = 200, offset: int = 0,
                    conn=Depends(get_db)):
    return get_photos_by_month(conn, year, month, limit, offset)


@app.get("/api/locations", dependencies=[Depends(require_api_key)])
def locations(conn=Depends(get_db)):
    return get_locations(conn)


@app.get("/api/locations/{country}/{city}/years", dependencies=[Depends(require_api_key)])
def years_for_location(country: str, city: str, conn=Depends(get_db)):
    return get_years_for_location(conn, city, country)


@app.get("/api/locations/{country}/{city}/photos", dependencies=[Depends(require_api_key)])
def photos_by_location(country: str, city: str,
                       year: str | None = None, month: str | None = None,
                       limit: int = 200, offset: int = 0,
                       conn=Depends(get_db)):
    return get_photos_by_location(conn, city, country, year, month, limit, offset)


def _graph_token() -> str:
    import json, msal
    token_cache_path = SYSTEM_DIR / "token_cache.json"
    config_path = SYSTEM_DIR / "config.json"
    if not token_cache_path.exists() or not config_path.exists():
        raise HTTPException(status_code=503, detail="Auth not configured on VM")
    with open(config_path) as f:
        config = json.load(f)
    cache = msal.SerializableTokenCache()
    cache.deserialize(token_cache_path.read_text())
    pca = msal.PublicClientApplication(
        config["client_id"],
        authority="https://login.microsoftonline.com/consumers",
        token_cache=cache,
    )
    accounts = pca.get_accounts()
    if not accounts:
        raise HTTPException(status_code=503, detail="No cached token — authenticate on VM first")
    result = pca.acquire_token_silent(["Files.Read.All"], account=accounts[0])
    if not result or "access_token" not in result:
        raise HTTPException(status_code=503, detail="Could not acquire token silently")
    return result["access_token"]


def _onedrive_path(path: str) -> str:
    from urllib.parse import quote
    if not path.startswith("/"):
        path = SORTED_ROOT.rstrip("/") + "/" + path
    return quote(path, safe="/")


_THUMB_CACHE_HEADERS = {
    # Thumbnails are content-addressed (path → MD5 key) and never change.
    # Cache aggressively in the browser (max-age) and at Vercel's edge (s-maxage).
    "Cache-Control": "public, max-age=604800, s-maxage=604800, immutable",
}

@app.get("/api/thumb", dependencies=[Depends(require_api_key)])
def thumbnail(path: str, request: Request, conn=Depends(get_db)):
    import hashlib
    cache_key = hashlib.md5(path.encode()).hexdigest()
    etag = f'"{cache_key}"'

    # Conditional request — browser already has this thumbnail
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers={"ETag": etag})

    cache_file = THUMB_CACHE_DIR / f"{cache_key}.jpg"
    if cache_file.exists():
        return Response(
            content=cache_file.read_bytes(),
            media_type="image/jpeg",
            headers={**_THUMB_CACHE_HEADERS, "ETag": etag},
        )

    token = _graph_token()
    encoded = _onedrive_path(path)
    url = f"https://graph.microsoft.com/v1.0/me/drive/root:{encoded}:/thumbnails/0/medium/content"
    resp = requests.get(url, headers={"Authorization": f"Bearer {token}"}, timeout=30)
    if resp.status_code == 404:
        raise HTTPException(status_code=404, detail="Photo not found in OneDrive")
    resp.raise_for_status()
    cache_file.write_bytes(resp.content)
    return Response(
        content=resp.content,
        media_type="image/jpeg",
        headers={**_THUMB_CACHE_HEADERS, "ETag": etag},
    )


@app.get("/api/photo", dependencies=[Depends(require_api_key)])
def photo(path: str):
    token = _graph_token()
    encoded = _onedrive_path(path)
    url = f"https://graph.microsoft.com/v1.0/me/drive/root:{encoded}:/content"
    resp = requests.get(url, headers={"Authorization": f"Bearer {token}"}, timeout=60)
    if resp.status_code == 404:
        raise HTTPException(status_code=404, detail="Photo not found in OneDrive")
    resp.raise_for_status()
    content_type = resp.headers.get("content-type", "image/jpeg")
    return Response(content=resp.content, media_type=content_type)


@app.get("/api/health")
def health():
    return {"status": "ok", "db": str(DB_PATH), "db_exists": DB_PATH.exists()}


# ── Phase 2: Clump endpoints ──────────────────────────────────────────────────

class ClumpRef(BaseModel):
    cam_make: str
    cam_model: str
    start_date: str
    end_date: str

class TagRequest(ClumpRef):
    tagged_city: str
    tagged_country: str

class ClumpScanRequest(ClumpRef):
    paths: list[str] | None = None


@app.get("/api/clumps", dependencies=[Depends(require_api_key)])
def clumps(conn=Depends(get_db)):
    return get_clumps(conn)


@app.get("/api/clumps/photos", dependencies=[Depends(require_api_key)])
def clump_photos(cam_make: str, cam_model: str,
                 start_date: str, end_date: str,
                 conn=Depends(get_db)):
    return get_clump_photos(conn, cam_make, cam_model, start_date, end_date)


@app.post("/api/clumps/tag", dependencies=[Depends(require_api_key)])
def clump_tag(body: TagRequest, conn=Depends(get_db)):
    tag_clump(conn, body.cam_make, body.cam_model,
              body.start_date, body.end_date,
              body.tagged_city, body.tagged_country)
    return {"ok": True}


@app.post("/api/clumps/trash", dependencies=[Depends(require_api_key)])
def clump_trash(body: ClumpRef, conn=Depends(get_db)):
    trash_clump(conn, body.cam_make, body.cam_model,
                body.start_date, body.end_date)
    return {"ok": True}


@app.post("/api/clumps/scan", dependencies=[Depends(require_api_key)])
def clump_scan(body: ClumpScanRequest, conn=Depends(get_db)):
    """Fetch up to 5 sample thumbnails and ask Claude Haiku for location hints."""
    import base64, anthropic

    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        raise HTTPException(status_code=503, detail="ANTHROPIC_API_KEY not set on VM")

    if body.paths:
        samples = [{"new_path": p} for p in body.paths[:5]]
    else:
        photos = get_clump_photos(conn, body.cam_make, body.cam_model,
                                   body.start_date, body.end_date)
        if not photos:
            raise HTTPException(status_code=404, detail="No photos found for clump")
        step = max(1, len(photos) // 5)
        samples = photos[::step][:5]

    import hashlib
    image_blocks = []
    uncached = []
    for p in samples:
        cache_key = hashlib.md5(p["new_path"].encode()).hexdigest()
        cache_file = THUMB_CACHE_DIR / f"{cache_key}.jpg"
        if cache_file.exists():
            b64 = base64.standard_b64encode(cache_file.read_bytes()).decode()
            image_blocks.append({
                "type": "image",
                "source": {"type": "base64", "media_type": "image/jpeg", "data": b64},
            })
        else:
            uncached.append(p)

    # Fetch any thumbnails not yet in the local cache
    if uncached:
        token = _graph_token()
        for p in uncached:
            try:
                encoded_path = _onedrive_path(p["new_path"])
                url = f"https://graph.microsoft.com/v1.0/me/drive/root:{encoded_path}:/thumbnails/0/medium/content"
                resp = requests.get(url, headers={"Authorization": f"Bearer {token}"}, timeout=15)
                if resp.ok:
                    cache_key = hashlib.md5(p["new_path"].encode()).hexdigest()
                    cache_file = THUMB_CACHE_DIR / f"{cache_key}.jpg"
                    cache_file.write_bytes(resp.content)
                    b64 = base64.standard_b64encode(resp.content).decode()
                    image_blocks.append({
                        "type": "image",
                        "source": {"type": "base64", "media_type": "image/jpeg", "data": b64},
                    })
            except Exception:
                continue

    if not image_blocks:
        raise HTTPException(status_code=502, detail="Could not fetch thumbnails for scan")

    prompt = (
        "You are helping to organize a family photo archive. "
        "Look at these photos (all from the same camera session) and answer:\n"
        "1. What LOCATION clues do you see? (landmarks, street signs, business names, "
        "architecture, license plates, scenery, indoor/outdoor)\n"
        "2. What are the main SUBJECTS? (people, pets, activities, events)\n"
        "3. Your best LOCATION GUESS: city and country if possible, or region.\n\n"
        "Be concise. Format: Location guess: [city, country]. Clues: [brief]. Subjects: [brief]."
    )

    client = anthropic.Anthropic(api_key=api_key)
    message = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=300,
        messages=[{"role": "user", "content": image_blocks + [{"type": "text", "text": prompt}]}],
    )
    description = message.content[0].text

    # Extract location hint (first line or "Location guess:" line)
    hint = ""
    for line in description.splitlines():
        if "location guess" in line.lower() or line.strip().startswith("1."):
            hint = line.strip()
            break
    if not hint:
        hint = description.splitlines()[0] if description else ""

    save_ai_hint(conn, body.cam_make, body.cam_model,
                 body.start_date, body.end_date, hint, description)

    return {"hint": hint, "description": description}
