"""
server.py — FastAPI backend for the photo tagger GUI.
Runs on the Azure VM. Vercel serves the static frontend which calls this API.

Start with:
    uvicorn tagger.server:app --host 0.0.0.0 --port 8000
"""

import os
import time
import logging
from functools import lru_cache
from pathlib import Path

import requests
from fastapi import FastAPI, HTTPException, Request, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response

from tagger.data import (
    connect, get_stats, get_years, get_months, get_photos_by_month,
    get_locations, get_years_for_location, get_photos_by_location,
    onedrive_path_for_photo,
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

# ── App ───────────────────────────────────────────────────────────────────────

app = FastAPI(title="Photo Sorter API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[VERCEL_ORIGIN, "http://localhost:5500", "http://127.0.0.1:5500"],
    allow_methods=["GET"],
    allow_headers=["*"],
)


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


@app.get("/api/thumb", dependencies=[Depends(require_api_key)])
def thumbnail(path: str, conn=Depends(get_db)):
    """
    Fetch a ~200px thumbnail for a photo via the Graph API.
    Results are disk-cached by path hash so repeat requests are instant.
    """
    import hashlib, json, msal

    cache_key = hashlib.md5(path.encode()).hexdigest()
    cache_file = THUMB_CACHE_DIR / f"{cache_key}.jpg"
    if cache_file.exists():
        return Response(content=cache_file.read_bytes(), media_type="image/jpeg")

    # Load token from MSAL cache
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

    token = result["access_token"]
    from urllib.parse import quote
    encoded = quote(path, safe="/")
    url = f"https://graph.microsoft.com/v1.0/me/drive/root:{encoded}:/thumbnails/0/medium/content"
    resp = requests.get(url, headers={"Authorization": f"Bearer {token}"}, timeout=30)
    if resp.status_code == 404:
        raise HTTPException(status_code=404, detail="Photo not found in OneDrive")
    resp.raise_for_status()

    cache_file.write_bytes(resp.content)
    return Response(content=resp.content, media_type="image/jpeg")


@app.get("/api/health")
def health():
    return {"status": "ok", "db": str(DB_PATH), "db_exists": DB_PATH.exists()}
