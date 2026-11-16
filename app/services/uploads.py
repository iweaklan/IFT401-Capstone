"""Logo uploads: the file's content must be a PNG/JPEG/GIF/WebP image of at most 1 MB.

Files are stored under random names; SVG is refused because it can contain script.
"""

import os
import re
import uuid

from app.services.trading import ValidationError

MAX_LOGO_BYTES = 1024 * 1024  # 1 MB

# Only names this module creates (32 hex characters + an image extension).
STORED_NAME_PATTERN = re.compile(r"^[0-9a-f]{32}\.(png|jpg|gif|webp)$")

LOGO_TYPE_MESSAGE = "The logo must be a PNG, JPEG, GIF or WebP image."
LOGO_SIZE_MESSAGE = "The logo must be 1 MB or smaller."


def detect_image_extension(data):
    """Look at the file's first bytes and return 'png', 'jpg', 'gif' or 'webp', else None."""
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if data.startswith(b"\xff\xd8\xff"):
        return "jpg"
    if data.startswith((b"GIF87a", b"GIF89a")):
        return "gif"
    # WebP files are a "RIFF" container whose bytes 8-11 say "WEBP".
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    return None


def check_logo(data):
    """Validate the uploaded bytes and return the extension to store them under."""
    if len(data) > MAX_LOGO_BYTES:
        raise ValidationError(LOGO_SIZE_MESSAGE)
    extension = detect_image_extension(data)
    if extension is None:
        raise ValidationError(LOGO_TYPE_MESSAGE)
    return extension


def save_logo(folder, data, extension):
    """Write the bytes into ``folder`` under a new random name and return that name."""
    os.makedirs(folder, exist_ok=True)
    name = f"{uuid.uuid4().hex}.{extension}"  # uuid4 = 32 random hex characters
    with open(os.path.join(folder, name), "wb") as f:
        f.write(data)
    return name


def delete_logo(folder, name):
    """Remove a stored logo. Does nothing unless ``name`` looks like one we created."""
    if not name or not STORED_NAME_PATTERN.match(name):
        return
    try:
        os.remove(os.path.join(folder, name))
    except FileNotFoundError:
        pass  # already gone: nothing to do
