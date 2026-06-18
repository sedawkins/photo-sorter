"""
graph.py — Microsoft Graph API helpers for OneDrive access.
Handles pagination, metadata retrieval, file download, and server-side copy.
"""

import time
import requests

BASE = "https://graph.microsoft.com/v1.0"

IMAGE_EXTENSIONS = {
    ".jpg", ".jpeg", ".png", ".heic", ".tiff", ".tif", ".bmp", ".webp",
}
SKIP_EXTENSIONS = {".mov", ".mp4", ".avi", ".mkv"}


class GraphClient:
    def __init__(self, token: str):
        self._token = token
        self._session = requests.Session()
        self._session.headers.update({"Authorization": f"Bearer {token}"})

    def _get(self, url: str, **kwargs) -> dict:
        resp = self._session.get(url, timeout=30, **kwargs)
        resp.raise_for_status()
        return resp.json()

    def _post(self, url: str, json: dict) -> dict:
        resp = self._session.post(url, json=json, timeout=30)
        resp.raise_for_status()
        return resp.json()

    def _put(self, url: str, data: bytes) -> dict:
        resp = self._session.put(
            url,
            data=data,
            headers={
                "Authorization": f"Bearer {self._token}",
                "Content-Type": "application/octet-stream",
            },
            timeout=120,
        )
        resp.raise_for_status()
        return resp.json()

    def list_photos(self, folder_path: str) -> list[dict]:
        """
        Recursively list all image files under folder_path.
        folder_path is a Graph API path like /me/drive/root:/Pictures
        Returns a flat list of Graph API driveItem dicts.
        """
        url = (
            f"{BASE}/me/drive/root:{folder_path}:/children"
            "?$select=id,name,file,folder,photo,location,size,parentReference"
            "&$top=200"
        )
        items = []
        while url:
            data = self._get(url)
            for item in data.get("value", []):
                if "folder" in item:
                    # Recurse into subfolders
                    sub_path = (
                        item["parentReference"]["path"].replace("/drive/root:", "")
                        + "/" + item["name"]
                    )
                    items.extend(self.list_photos(sub_path))
                elif "file" in item:
                    ext = "." + item["name"].rsplit(".", 1)[-1].lower() if "." in item["name"] else ""
                    if ext in IMAGE_EXTENSIONS:
                        items.append(item)
                    # Silently skip .mov and other non-image files
            url = data.get("@odata.nextLink")
        return items

    def get_item_id_for_path(self, onedrive_path: str) -> str | None:
        """Resolve a OneDrive file path to a driveItem ID."""
        try:
            resp = self._get(f"{BASE}/me/drive/root:{onedrive_path}?$select=id")
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

    def copy_item(self, item_id: str, dest_folder_id: str, filename: str) -> str:
        """
        Server-side copy of item_id into dest_folder_id with the given filename.
        Returns the new item's ID (polls until complete).
        """
        resp = self._session.post(
            f"{BASE}/me/drive/items/{item_id}/copy",
            json={
                "parentReference": {"id": dest_folder_id},
                "name": filename,
                "@microsoft.graph.conflictBehavior": "replace",
            },
            timeout=30,
        )
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
                raise RuntimeError(f"Copy failed: {data}")
        raise TimeoutError(f"Copy timed out for item {item_id}")

    def upload_small_file(self, dest_folder_id: str, filename: str, content: bytes) -> str:
        """Upload a small file (< 4MB) to a folder. Returns new item ID."""
        result = self._put(
            f"{BASE}/me/drive/items/{dest_folder_id}:/{filename}:/content",
            data=content,
        )
        return result["id"]
