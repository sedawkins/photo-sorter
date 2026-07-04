"""auth.py — Microsoft Graph token + on-demand thumbnail fetch.

Mirrors the proven MSAL flow in onedrive_sync.acquire_token(): silent token
from the existing token_cache.json beside the DB, falling back to device-code
flow once if the cache has expired. The only Graph call we make is fetching
small thumbnails by path — never the full-resolution originals.
"""

import json
import os
import threading
from pathlib import Path
from urllib.parse import quote

import msal
import requests

SYSTEM_DIR = Path(
    os.environ.get(
        "PHOTO_SORTER_SYSTEM_DIR",
        r"C:\Users\sedaw\OneDrive\Photos\Sorted\_system",
    )
)
TOKEN_CACHE_PATH = SYSTEM_DIR / "token_cache.json"
CONFIG_PATH = SYSTEM_DIR / "config.json"

SCOPES = ["Files.Read.All", "User.Read"]
GRAPH = "https://graph.microsoft.com/v1.0"

_lock = threading.Lock()
_session = requests.Session()
_token = None


def _config():
    return json.loads(CONFIG_PATH.read_text())


def _build_app():
    cache = msal.SerializableTokenCache()
    if TOKEN_CACHE_PATH.exists():
        cache.deserialize(TOKEN_CACHE_PATH.read_text())
    app = msal.PublicClientApplication(
        _config()["client_id"],
        authority="https://login.microsoftonline.com/consumers",
        token_cache=cache,
    )
    return app, cache


def acquire_token():
    """Return a valid access token, refreshing silently when possible."""
    app, cache = _build_app()
    accounts = app.get_accounts()
    result = app.acquire_token_silent(SCOPES, account=accounts[0]) if accounts else None
    if not result:
        flow = app.initiate_device_flow(scopes=SCOPES)
        if "user_code" not in flow:
            raise RuntimeError(f"Device flow failed: {flow.get('error_description')}")
        print("\n" + flow["message"] + "\n")
        result = app.acquire_token_by_device_flow(flow)
    if "access_token" not in result:
        raise RuntimeError(f"Auth failed: {result.get('error_description')}")
    if cache.has_state_changed:
        try:
            TOKEN_CACHE_PATH.write_text(cache.serialize())
        except Exception:
            pass  # cache write is best-effort; refreshing the photo DB is not our job
    return result["access_token"]


def _token_cached(force=False):
    global _token
    with _lock:
        if _token is None or force:
            _token = acquire_token()
        return _token


def fetch_thumbnail(onedrive_path, size="medium"):
    """Fetch a small thumbnail by OneDrive path. Returns JPEG bytes or None (404).

    size is one of Graph's named sizes: small (~96px), medium (~176px),
    large (~800px). Full originals are never downloaded.
    """
    enc = quote(onedrive_path, safe="/")
    url = f"{GRAPH}/me/drive/root:{enc}:/thumbnails/0/{size}/content"

    def _do(token):
        return _session.get(
            url,
            headers={"Authorization": f"Bearer {token}"},
            timeout=30,
            allow_redirects=True,
        )

    resp = _do(_token_cached())
    if resp.status_code == 401:
        resp = _do(_token_cached(force=True))  # token expired mid-session
    if resp.status_code == 404:
        return None
    resp.raise_for_status()
    return resp.content
