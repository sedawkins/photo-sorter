"""
graph.py — Microsoft Graph API helpers for OneDrive access.
Handles pagination, metadata retrieval, file download, and server-side copy.
"""

import logging
import time
import requests
from urllib.parse import quote

_log = logging.getLogger("photo_sorter")

BASE = "https://graph.microsoft.com/v1.0"

IMAGE_EXTENSIONS = {
    ".jpg", ".jpeg", ".png", ".heic", ".tiff", ".tif", ".bmp", ".webp",
}
MOVIE_EXTENSIONS = {".mov", ".mp4", ".avi", ".mkv", ".m4v", ".wmv", ".3gp"}
SIDECAR_EXTENSIONS = {".json"}

# Folders that are internal app caches, not photo content — never recurse
# into these. "Data.noindex" is iPhoto's internal preview-render cache: every
# file in it is a low-res, no-EXIF derivative of a photo already present in
# the library's "Originals" folder, so it carries a real .jpg extension but
# no unique content. Confirmed by inspection on the production library —
# see SPEC.md "Source cleanup utility" for details.
SKIP_FOLDER_NAMES = {"data.noindex"}


class GraphClient:
    def __init__(self, token: str, token_refresher=None):
        self._token = token
        self._token_refresher = token_refresher
        self._session = requests.Session()
        self._session.headers.update({"Authorization": f"Bearer {token}"})

    def _refresh_token(self):
        if self._token_refresher:
            self._token = self._token_refresher()
            self._session.headers.update({"Authorization": f"Bearer {self._token}"})

    def _retry(self, make_request, max_attempts: int = 6) -> requests.Response:
        """
        Execute make_request(), retrying on transient errors with backoff.
          401 — refresh token and retry immediately
          429 — sleep Retry-After seconds, then retry
          500/503 — exponential backoff (5s → 10s → 20s → 40s → 60s), then retry
        Gives up after max_attempts and returns the last response for the caller
        to raise_for_status() on.
        """
        delay = 5
        for attempt in range(max_attempts):
            resp = make_request()
            if resp.status_code == 401 and self._token_refresher:
                self._refresh_token()
                continue
            if resp.status_code == 429:
                wait = int(resp.headers.get("Retry-After", 30))
                _log.warning(f"  429 Too Many Requests — backing off {wait}s")
                time.sleep(wait)
                continue
            if resp.status_code in (500, 503, 504) and attempt < max_attempts - 1:
                _log.warning(
                    f"  {resp.status_code} transient error — retrying in {delay}s "
                    f"(attempt {attempt + 1}/{max_attempts})"
                )
                time.sleep(delay)
                delay = min(delay * 2, 60)
                continue
            break
        return resp

    def _get(self, url: str, **kwargs) -> dict:
        resp = self._retry(lambda: self._session.get(url, timeout=30, **kwargs))
        resp.raise_for_status()
        return resp.json()

    def _post(self, url: str, json: dict) -> dict:
        resp = self._retry(lambda: self._session.post(url, json=json, timeout=30))
        resp.raise_for_status()
        return resp.json()

    def _put(self, url: str, data: bytes) -> dict:
        def make_put():
            return self._session.put(url, data=data, headers={
                "Authorization": f"Bearer {self._token}",
                "Content-Type": "application/octet-stream",
            }, timeout=120)
        resp = self._retry(make_put)
        resp.raise_for_status()
        return resp.json()

    def list_photos(self, folder_path: str) -> list[dict]:
        """
        Recursively list all image and movie files under folder_path.
        Returns a flat list of Graph API driveItem dicts, each tagged with
        a '_media_type' key ('photo' or 'movie').

        Live Photo companion MOVs are skipped: iPhone Live Photos save as a
        paired JPEG + short MOV with identical base filenames. Any MOV whose
        base name matches a still image in the same folder is dropped — it is
        motion data for the still, not a standalone video.
        """
        url = (
            f"{BASE}/me/drive/root:{folder_path}:/children"
            "?$select=id,name,file,folder,photo,location,size,parentReference"
            "&$top=200"
        )
        folder_files = []  # all files in this folder level
        sub_paths = []     # subfolders to recurse into

        while url:
            data = self._get(url)
            for item in data.get("value", []):
                if "folder" in item:
                    if item["name"].lower() in SKIP_FOLDER_NAMES:
                        continue
                    sub_path = (
                        item["parentReference"]["path"].replace("/drive/root:", "")
                        + "/" + item["name"]
                    )
                    sub_paths.append(sub_path)
                elif "file" in item:
                    folder_files.append(item)
            url = data.get("@odata.nextLink")

        # Build the set of still-photo base names present in this folder.
        # Used to detect Live Photo companion MOVs (same base name, .mov extension).
        still_bases = {
            item["name"].rsplit(".", 1)[0].lower()
            for item in folder_files
            if "." in item["name"]
            and "." + item["name"].rsplit(".", 1)[-1].lower() in IMAGE_EXTENSIONS
        }

        items = []
        for item in folder_files:
            ext = "." + item["name"].rsplit(".", 1)[-1].lower() if "." in item["name"] else ""
            if ext in IMAGE_EXTENSIONS:
                item["_media_type"] = "photo"
                items.append(item)
            elif ext in MOVIE_EXTENSIONS:
                base = item["name"].rsplit(".", 1)[0].lower()
                if base in still_bases:
                    continue  # Live Photo companion — skip
                item["_media_type"] = "movie"
                items.append(item)
            elif ext in SIDECAR_EXTENSIONS:
                item["_media_type"] = "sidecar"
                items.append(item)
            # Silently skip all other file types

        for sub_path in sub_paths:
            items.extend(self.list_photos(sub_path))

        return items

    def get_item_id_for_path(self, onedrive_path: str) -> str | None:
        """Resolve a OneDrive file path to a driveItem ID."""
        try:
            encoded = quote(onedrive_path, safe='/')
            resp = self._get(f"{BASE}/me/drive/root:{encoded}?$select=id")
            return resp.get("id")
        except requests.HTTPError as e:
            if e.response.status_code == 404:
                return None
            raise

    def get_item_metadata(self, item_id: str) -> dict:
        """Fetch full metadata for a single driveItem by ID."""
        return self._get(
            f"{BASE}/me/drive/items/{item_id}"
            "?$select=id,name,file,photo,location,size,image,parentReference"
        )

    def download_item(self, item_id: str) -> bytes:
        """Download the content of a driveItem, streaming to avoid OOM on large files."""
        resp = self._session.get(
            f"{BASE}/me/drive/items/{item_id}/content",
            timeout=120,
            allow_redirects=True,
            stream=True,
        )
        resp.raise_for_status()
        chunks = []
        for chunk in resp.iter_content(chunk_size=1024 * 1024):
            if chunk:
                chunks.append(chunk)
        return b"".join(chunks)

    def ensure_folder(self, parent_id: str, folder_name: str) -> str:
        """
        Create a folder under parent_id if it doesn't exist.
        Returns the folder's driveItem ID.
        """
        # Check if it exists
        try:
            data = self._get(
                f"{BASE}/me/drive/items/{parent_id}/children"
                f"?$filter=name eq '{folder_name}' and folder ne null"
                "&$select=id,name"
            )
            items = data.get("value", [])
            if items:
                return items[0]["id"]
        except requests.HTTPError:
            pass

        # Create it
        try:
            result = self._post(
                f"{BASE}/me/drive/items/{parent_id}/children",
                json={
                    "name": folder_name,
                    "folder": {},
                    "@microsoft.graph.conflictBehavior": "fail",
                },
            )
            return result["id"]
        except requests.HTTPError as e:
            if e.response.status_code == 409:
                # Folder was created between our check and create — fetch it
                data = self._get(
                    f"{BASE}/me/drive/items/{parent_id}/children"
                    f"?$filter=name eq '{folder_name}'"
                    "&$select=id,name"
                )
                items = data.get("value", [])
                if items:
                    return items[0]["id"]
            raise

    def ensure_folder_path(self, root_path: str, path_parts: list[str]) -> str:
        """
        Walk/create a folder hierarchy under root_path, creating root_path
        itself if it doesn't exist yet.
        root_path is a OneDrive path like /Photos/Sorted/Primary
        path_parts is e.g. ["2019", "June", "Texas", "Austin"]
        Returns the final folder's driveItem ID.
        """
        # Build root by walking each segment, creating as needed
        segments = [s for s in root_path.strip("/").split("/") if s]
        folder_id = self._get(f"{BASE}/me/drive/root")["id"]
        for segment in segments:
            folder_id = self.ensure_folder(folder_id, segment)
        for part in path_parts:
            folder_id = self.ensure_folder(folder_id, part)
        return folder_id

    def delete_item_if_exists(self, parent_folder_id: str, filename: str):
        """Delete a file in a folder if it exists, to avoid nameAlreadyExists on copy."""
        try:
            data = self._get(
                f"{BASE}/me/drive/items/{parent_folder_id}/children"
                f"?$filter=name eq '{filename}'&$select=id,name"
            )
            for item in data.get("value", []):
                self._session.delete(
                    f"{BASE}/me/drive/items/{item['id']}", timeout=30
                ).raise_for_status()
        except Exception:
            pass  # If it doesn't exist or delete fails, the copy will surface the real error

    def copy_item(self, item_id: str, dest_folder_id: str, filename: str) -> str:
        """
        Server-side copy of item_id into dest_folder_id with the given filename.
        Returns the new item's ID (polls until complete).
        """
        # Personal OneDrive copy ignores conflictBehavior, so delete first if exists
        self.delete_item_if_exists(dest_folder_id, filename)

        resp = self._session.post(
            f"{BASE}/me/drive/items/{item_id}/copy",
            json={
                "parentReference": {"id": dest_folder_id},
                "name": filename,
            },
            timeout=30,
        )
        if resp.status_code == 409:
            # File already exists in dest — treat as success (parallel race)
            return "exists"
        resp.raise_for_status()

        # Graph returns 202 Accepted with a monitor URL
        monitor_url = resp.headers.get("Location")
        if not monitor_url:
            raise RuntimeError("No monitor URL returned from copy operation")

        # Poll until complete — monitor URL contains a self-authenticating tempauth
        # token, so do NOT pass our Bearer token (it conflicts and causes 401)
        for _ in range(60):
            time.sleep(2)
            status = requests.get(monitor_url, timeout=30)
            status.raise_for_status()
            data = status.json()
            if data.get("status") == "completed":
                return data["resourceId"]
            if data.get("status") == "failed":
                err_code = data.get("error", {}).get("code", "")
                if err_code == "nameAlreadyExists":
                    return data.get("resourceId") or "exists"
                raise RuntimeError(f"Copy failed: {data}")
        raise TimeoutError(f"Copy timed out for item {item_id}")

    def upload_small_file(self, dest_folder_id: str, filename: str, content: bytes) -> str:
        """Upload a small file (< 4MB) to a folder. Returns new item ID."""
        result = self._put(
            f"{BASE}/me/drive/items/{dest_folder_id}:/{filename}:/content",
            data=content,
        )
        return result["id"]
