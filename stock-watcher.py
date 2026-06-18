#!/usr/bin/env python3
"""
stock-watcher.py
Watches a local folder for new image files, POSTs each one to an n8n
Stock Photo Keyworder webhook, and embeds IPTC/XMP metadata into the
processed file using exiftool.

Requirements:
    pip3 install watchdog requests --break-system-packages
    brew install exiftool  (macOS)

Usage:
    python3 stock-watcher.py

To run at login on macOS, install the launchd plist:
    cp com.yourname.stock-watcher.plist ~/Library/LaunchAgents/
    launchctl load ~/Library/LaunchAgents/com.yourname.stock-watcher.plist
"""

import os
import sys
import time
import base64
import logging
import subprocess
import requests
from pathlib import Path

# ── Config ────────────────────────────────────────────────────────────────────
WATCH_DIR        = Path.home() / "Images to keyword for stock"
PROCESSED_DIR    = WATCH_DIR / "processed"
FAILED_DIR       = WATCH_DIR / "failed"
WEBHOOK_URL      = "https://YOUR_N8N_HOST/webhook/stock-keyworder"
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".tiff", ".tif", ".webp"}
MIN_FILE_AGE_SECONDS = 3
WEBHOOK_TIMEOUT      = 120   # seconds — n8n needs ~30-40s for Claude API + Sheets write
EXIFTOOL             = "/opt/homebrew/bin/exiftool"  # macOS Homebrew path

# Copyright defaults — update these
CREATOR   = "Your Name"
COPYRIGHT = "© 2026 Your Name. All rights reserved."

# ── Logging ───────────────────────────────────────────────────────────────────
log_path = Path.home() / "Library" / "Logs" / "stock-watcher.log"
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)s  %(message)s",
    handlers=[
        logging.FileHandler(log_path),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger(__name__)


def _mime_type(filepath: Path) -> str:
    return {
        ".jpg":  "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png":  "image/png",
        ".tiff": "image/tiff",
        ".tif":  "image/tiff",
        ".webp": "image/webp",
    }.get(filepath.suffix.lower(), "application/octet-stream")


def embed_metadata(filepath: Path, meta: dict) -> bool:
    """Write IPTC RIM u Metadata into the file using exiftool."""
    if not os.path.exists(EXIFTOOL):
        log.warning(f"exiftool not found at {EXIFTOOL}, skipping metadata embedding")
        return False

    title        = meta.get("title", "")[:200]
    description  = meta.get("description", "")[:2000]
    keywords_str = meta.get("keywords", "")
    keywords     = [k.strip() for k in keywords_str.split(",") if k.strip()]

    args = [
        EXIFTOOL,
        "-overwrite_original",
        f"-IPTC:ObjectName={title}",
        f"-IPTC:Caption-Abstract={description}",
        f"-IPTC:By-line={CREATOR}",
        f"-IPTC:CopyrightNotice={COPYRIGHT}",
        f"-XMP-dc:Title={title}",
        f"-XMP-dc:Description={description}",
        f"-XMP-dc:Creator={CREATOR}",
        f"-XMP-dc:Rights={COPYRIGHT}",
        # Clear existing keyword fields before writing
        "-IPTC:Keywords=",
        "-XMP-dc:Subject=",
    ]

    # Add each keyword individually
    for kw in keywords:
        args.append(f"-IPTC:Keywords+={kw}")
        args.append(f"-XMP-dc:Subject+={kw}")

    args.append(str(filepath))

    try:
        result = subprocess.run(args, capture_output=True, text=True, timeout=30)
        if result.returncode == 0:
            log.info(f"Metadata embedded: {filepath.name} ({len(keywords)} keywords)")
            return True
        else:
            log.error(f"exiftool error for {filepath.name}: {result.stderr[:300]}")
            return False
    except subprocess.TimeoutExpired:
        log.error(f"exiftool timed out for {filepath.name}")
        return False
    except Exception as e:
        log.error(f"exiftool exception for {filepath.name}: {e}")
        return False


def upload_image(filepath: Path):
    """
    POST image as base64 JSON to n8n webhook.
    Returns (success: bool, metadata: dict | None).
    n8n is configured with responseMode=lastNode so the response
    contains the generated metadata once the full workflow completes.
    """
    def _post(verify_ssl: bool):
        with open(filepath, "rb") as f:
            image_data = f.read()
        payload = {
            "filename": filepath.name,
            "imageBase64": base64.b64encode(image_data).decode("utf-8"),
            "mimeType": _mime_type(filepath),
        }
        return requests.post(
            WEBHOOK_URL,
            json=payload,
            timeout=WEBHOOK_TIMEOUT,
            verify=verify_ssl,
        )

    try:
        response = _post(verify_ssl=True)
    except requests.exceptions.SSLError:
        log.warning(f"SSL error for {filepath.name}, retrying without verification...")
        try:
            response = _post(verify_ssl=False)
        except Exception as e:
            log.error(f"Upload exception (no-verify): {filepath.name} → {e}")
            return False, None
    except Exception as e:
        log.error(f"Upload exception: {filepath.name} → {e}")
        return False, None

    if response.status_code != 200:
        log.error(f"Upload failed: {filepath.name} → HTTP {response.status_code}: {response.text[:200]}")
        return False, None

    # Parse metadata from lastNode response
    metadata = None
    try:
        body = response.json()
        # n8n lastNode returns array of items
        if isinstance(body, list) and body:
            metadata = body[0]
        elif isinstance(body, dict):
            metadata = body
        log.info(f"Uploaded: {filepath.name} → metadata received ({len(metadata.get('keywords','').split(','))} keywords)")
    except Exception as e:
        log.warning(f"Could not parse metadata response for {filepath.name}: {e}")
        log.info(f"Uploaded: {filepath.name} → no metadata in response")

    return True, metadata


def process_file(filepath: Path) -> None:
    if not filepath.exists():
        return
    if filepath.parent != WATCH_DIR:
        return
    if filepath.suffix.lower() not in IMAGE_EXTENSIONS:
        return
    if filepath.name.startswith("."):
        return

    time.sleep(MIN_FILE_AGE_SECONDS)
    if not filepath.exists():
        return

    log.info(f"Processing: {filepath.name}")
    success, metadata = upload_image(filepath)

    dest_dir = PROCESSED_DIR if success else FAILED_DIR
    dest_dir.mkdir(exist_ok=True)

    # Build destination path (handle name collisions)
    dest_path = dest_dir / filepath.name
    if dest_path.exists():
        stem, suffix = filepath.stem, filepath.suffix
        i = 1
        while dest_path.exists():
            dest_path = dest_dir / f"{listem}_{i}{suffix}"
            i += 1

    filepath.rename(dest_path)
    log.info(f"Moved to {dest_dir.name}/: {dest_path.name}")

    # Embed IPTC/XMP metadata into the processed file
    if success and metadata:
        embed_metadata(dest_path, metadata)


try:
    from watchdog.observers import Observer
    from watchdog.events import FileSystemEventHandler

    class ImageHandler(FileSystemEventHandler):
        def on_created(self, event):
            if not event.is_directory:
                process_file(Path(event.src_path))

        def on_moved(self, event):
            if not event.is_directory:
                process_file(Path(event.dest_path))

    def scan_existing(directory: Path) -> None:
        for filepath in sorted(directory.iterdir()):
            if filepath.is_file() and filepath.suffix.lower() in IMAGE_EXTENSIONS:
                log.info(f"Found existing file on startup: {filepath.name}")
                process_file(filepath)

    def main():
        WATCH_DIR.mkdir(parents=True, exist_ok=True)
        PROCESSED_DIR.mkdir(exist_ok=True)
        FAILED_DIR.mkdir(exist_ok=True)

        log.info(f"Watching:  {WATCH_DIR}")
        log.info(f"Webhook:   {WEBHOOK_URL}")
        log.info(f"exiftool:  {EXIFTOOL}")
        log.info(f"Timeout:   {WEBHOOK_TIMEOUT}s")

        scan_existing(WATCH_DIR)

        observer = Observer()
        observer.schedule(ImageHandler(), str(WATCH_DIR), recursive=False)
        observer.start()
        log.info("Watcher started.")

        try:
            while True:
                time.sleep(5)
        except KeyboardInterrupt:
            log.info("Stopping watcher.")
            observer.stop()
        observer.join()

except ImportError:
    log.error("watchdog not installed. Run: pip3 install watchdog requests --break-system-packages")
    sys.exit(1)

if __name__ == "__main__":
    main()
