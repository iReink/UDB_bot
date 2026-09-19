"""One release fingerprint keeps cooperating browser scripts/styles in sync."""
import hashlib
from pathlib import Path

STATIC = Path(__file__).resolve().parent / 'static'
PHOTO_ASSET_VERSION = hashlib.sha256(b''.join(
    (STATIC / name).read_bytes() for name in (
        'app.js', 'daily-discovery.js', 'daily-photos.css', 'album.js', 'album.css'
    )
)).hexdigest()[:16]
