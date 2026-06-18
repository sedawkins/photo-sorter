"""
onedrive_sync.py — Authenticate with Microsoft Graph and sync the _system/
folder between OneDrive and the local VM.

Usage:
    python3 onedrive_sync.py --pull   # download _system/ from OneDrive to VM
    python3 onedrive_sync.py --push   # upload _system/ from VM to OneDrive
"""

import argparse
import json
import os
import sys
from pathlib import Path

import msal
import requests

APP_DIR = Path(__file__).parent
SYSTEM_DIR = APP_DIR / "_system"
TOKEN_CACHE_PATH = SYSTEM_DIR / "token_cache.json"
CONFIG_PATH = SYSTEM_DIR / "config.json"

SCOPES = ["Files.Read.All", "Files.ReadWrite.All", "User.Read"]

# Files we sync between VM and OneDrive _system/
SYNC_FILES = [
    "photo_sorter.db",
    "token_cache.json",
    "config.json",
]


def load_config():
    if not CONFIG_PATH.exists():
        print(f"ERROR: config.json not found at {CONFIG_PATH}")
        print("Run setup.sh first to create it.")
        sys.exit(1)
    with open(CONFIG_PATH) as f:
        return json.load(f)


def build_msal_app(config):
    cache = msal.SerializableTokenCache()
    if TOKEN_CACHE_PATH.exists():
        cache.deserialize(TOKEN_CACHE_PATH.read_text())

    app = msal.PublicClientApplication(
        config["client_id"],
        authority="https://login.microsoftonline.com/consumers",
        token_cache=cache,
    )
    return app, cache


def make_token_refresher(config):
    """Return a callable that silently refreshes the access token when it expires."""
    def refresh():
        return acquire_token(config)
    return refresh


def acquire_token(config):
    app, cache = build_msal_app(config)

    # Try silent auth first (cached token)
    accounts = app.get_accounts()
    result = None
    if accounts:
        result = app.acquire_token_silent(SCOPES, account=accounts[0])

    # Fall back to device code flow
    if not result:
        flow = app.initiate_device_flow(scopes=SCOPES)
        if "user_code" not in flow:
            raise RuntimeError(f"Device flow failed: {flow.get('error_description')}")
        print("\n" + flow["message"])
        print("Waiting for authentication...\n")
        result = app.acquire_token_by_device_flow(flow)

    if "access_token" not in result:
        raise RuntimeError(f"Auth failed: {result.get('error_description')}")

    # Persist updated token cache
    SYSTEM_DIR.mkdir(parents=True, exist_ok=True)
    TOKEN_CACHE_PATH.write_text(cache.serialize())

    return result["access_token"]


def graph_get(token, url):
    resp = requests.get(
        url,
        headers={"Authorization": f"Bearer {token}"},
        timeout=30,
    )
    resp.raise_for_status()
    return resp


def graph_put(token, url, data):
    resp = requests.put(
        url,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/octet-stream",
        },
        data=data,
        timeout=60,
    )
    resp.raise_for_status()
    return resp


def onedrive_download(token, onedrive_path, local_path):
    """Download a single file from OneDrive to local_path."""
    url = f"https://graph.microsoft.com/v1.0/{onedrive_path}:/content"
    try:
        resp = graph_get(token, url)
        local_path.parent.mkdir(parents=True, exist_ok=True)
        local_path.write_bytes(resp.content)
        print(f"  Downloaded: {local_path.name}")
    except requests.HTTPError as e:
        if e.response.status_code == 404:
            print(f"  Not found on OneDrive (skipping): {local_path.name}")
        else:
            raise


def onedrive_upload(token, local_path, onedrive_path):
    """Upload a single local file to OneDrive."""
    if not local_path.exists():
        print(f"  Skipping (not on VM): {local_path.name}")
        return
    url = f"https://graph.microsoft.com/v1.0/{onedrive_path}:/content"
    graph_put(token, url, local_path.read_bytes())
    print(f"  Uploaded: {local_path.name}")


def onedrive_upload_runs(token, config):
    """Upload any new log files from _system/runs/ to OneDrive."""
    runs_dir = SYSTEM_DIR / "runs"
    if not runs_dir.exists():
        return
    system_path = config["system_path"]
    for log_file in sorted(runs_dir.glob("*.log")):
        onedrive_path = f"{system_path}/runs/{log_file.name}"
        # Use upload URL without the me/drive/root: prefix quirk
        url = f"https://graph.microsoft.com/v1.0/me/drive/root:{system_path.split('root:')[1]}/runs/{log_file.name}:/content"
        try:
            graph_put(token, url, log_file.read_bytes())
            print(f"  Uploaded log: {log_file.name}")
        except Exception as e:
            print(f"  Warning: could not upload log {log_file.name}: {e}")


def pull(token, config):
    """Download _system/ files from OneDrive to VM."""
    print("Pulling _system/ from OneDrive...")
    system_path = config["system_path"]
    for filename in SYNC_FILES:
        onedrive_path = f"me/drive/root:{system_path.split('root:')[1]}/{filename}"
        local_path = SYSTEM_DIR / filename
        onedrive_download(token, f"me/drive/root:{system_path.split('root:')[1]}/{filename}", local_path)


def push(token, config):
    """Upload _system/ files from VM to OneDrive."""
    print("Pushing _system/ to OneDrive...")
    system_path = config["system_path"]
    for filename in SYNC_FILES:
        local_path = SYSTEM_DIR / filename
        onedrive_upload(
            token,
            local_path,
            f"me/drive/root:{system_path.split('root:')[1]}/{filename}",
        )
    onedrive_upload_runs(token, config)


def main():
    parser = argparse.ArgumentParser(description="Sync _system/ with OneDrive")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--pull", action="store_true", help="Download from OneDrive to VM")
    group.add_argument("--push", action="store_true", help="Upload from VM to OneDrive")
    args = parser.parse_args()

    config = load_config()
    token = acquire_token(config)

    if args.pull:
        pull(token, config)
    else:
        push(token, config)

    print("Sync complete.")


if __name__ == "__main__":
    main()
