"""Cover images uploaded by the user, stored under data/covers/ and served by the web server."""
import base64
import binascii
import hashlib
import re

from .store import DATA_DIR

COVERS_DIR = DATA_DIR / "covers"
MAX_BYTES = 5 * 1024 * 1024
NAME_RE = re.compile(r"^[0-9a-f]{40}\.(png|jpg|webp)$")
CONTENT_TYPES = {"png": "image/png", "jpg": "image/jpeg", "webp": "image/webp"}


def _extension(raw: bytes) -> str | None:
    if raw.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if raw.startswith(b"\xff\xd8\xff"):
        return "jpg"
    if raw[:4] == b"RIFF" and raw[8:12] == b"WEBP":
        return "webp"
    return None


def save_data_url(data_url: str) -> str:
    """Store an image sent as a data: URL and return the /covers/<name> URL that serves it."""
    header, sep, payload = data_url.partition(",")
    if not sep or not header.startswith("data:image/") or not header.endswith(";base64"):
        raise ValueError("envie uma imagem PNG, JPG ou WebP")
    try:
        raw = base64.b64decode(payload, validate=True)
    except binascii.Error:
        raise ValueError("imagem inválida") from None
    if len(raw) > MAX_BYTES:
        raise ValueError("a imagem tem mais de 5 MB")
    ext = _extension(raw)  # trust the bytes, not the declared type
    if ext is None:
        raise ValueError("envie uma imagem PNG, JPG ou WebP")
    name = f"{hashlib.sha1(raw).hexdigest()}.{ext}"
    COVERS_DIR.mkdir(parents=True, exist_ok=True)
    (COVERS_DIR / name).write_bytes(raw)
    return f"/covers/{name}"


def read(name: str) -> tuple[bytes, str] | None:
    if not NAME_RE.match(name):  # also rules out path traversal
        return None
    path = COVERS_DIR / name
    if not path.is_file():
        return None
    return path.read_bytes(), CONTENT_TYPES[name.rsplit(".", 1)[1]]
