"""Saving and deleting the photos, videos and profile pictures people upload."""
import io
import os
import uuid

from PIL import Image, ImageOps

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
UPLOAD_DIR = os.path.join(BASE_DIR, "static", "uploads")
IMAGE_EXT = {"jpg", "jpeg", "png", "gif", "webp"}
VIDEO_EXT = {"mp4", "webm", "mov"}
MAX_PER_POST = 10
AUDIO_EXT = {"mp3", "wav", "ogg", "m4a"}
# Documents people may share. Nothing a browser could run (html, svg, js, exe...) is allowed.
FILE_EXT = {"pdf", "doc", "docx", "xls", "xlsx", "ppt", "pptx", "txt", "csv", "md", "json",
            "zip", "rar", "7z"}
ATTACH_LIMIT_MB = {"image": 15, "video": 100, "audio": 25, "file": 50}
GIF_HOSTS = ("giphy.com", "tenor.com")
MAX_FILE_MB = {"image": 15, "video": 100}
AVATAR_SIZE = 400


def save_media(file):
    """Store an uploaded file. Returns (relative path, 'image'|'video'), or (None, None)
    if nothing was chosen. Raises ValueError for unsupported files."""
    if file is None or not file.filename:
        return None, None
    ext = file.filename.rsplit(".", 1)[-1].lower() if "." in file.filename else ""
    if ext in IMAGE_EXT:
        kind = "image"
    elif ext in VIDEO_EXT:
        kind = "video"
    else:
        raise ValueError("Use photos (jpg, png, gif, webp) or videos (mp4, webm, mov).")
    file.stream.seek(0, os.SEEK_END)
    size = file.stream.tell()
    file.stream.seek(0)
    if size > MAX_FILE_MB[kind] * 1024 * 1024:
        raise ValueError(f"{file.filename} is too big. Photos can be up to {MAX_FILE_MB['image']} MB, "
                         f"videos up to {MAX_FILE_MB['video']} MB.")
    os.makedirs(UPLOAD_DIR, exist_ok=True)
    name = f"{uuid.uuid4().hex}.{ext}"
    file.save(os.path.join(UPLOAD_DIR, name))
    return f"uploads/{name}", kind


def save_attachment(file):
    """Store a chat attachment. Returns (path, kind, original name); kind is image/video/audio/file."""
    name = os.path.basename((file.filename or "").replace("\\", "/"))
    name = "".join(ch for ch in name if ch.isprintable())[:120] or "file"
    ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    if ext in IMAGE_EXT:
        kind = "image"
    elif ext in VIDEO_EXT:
        kind = "video"
    elif ext in AUDIO_EXT:
        kind = "audio"
    elif ext in FILE_EXT:
        kind = "file"
    else:
        raise ValueError(f"{name}: that file type can't be sent. Photos, videos, audio, PDFs, "
                         "Office documents, text and zip files are fine.")
    file.stream.seek(0, os.SEEK_END)
    size = file.stream.tell()
    file.stream.seek(0)
    if size > ATTACH_LIMIT_MB[kind] * 1024 * 1024:
        raise ValueError(f"{name} is too big (limit {ATTACH_LIMIT_MB[kind]} MB for this type).")
    os.makedirs(UPLOAD_DIR, exist_ok=True)
    stored = f"{uuid.uuid4().hex}.{ext}"
    file.save(os.path.join(UPLOAD_DIR, stored))
    return f"uploads/{stored}", kind, name


def normalize_gif_url(text):
    """If `text` is one https link to a GIF/image, return the direct image URL, else None.
    Direct image links (.gif .png .jpg .webp) work from any site. Normal Giphy page links
    (giphy.com/gifs/funny-cat-AbC123) are converted to their image file."""
    import re
    from urllib.parse import urlparse
    text = (text or "").strip()
    if not text or len(text) > 500 or re.search(r"\s", text):
        return None
    try:
        parts = urlparse(text)
    except ValueError:
        return None
    if parts.scheme != "https" or not parts.hostname:
        return None
    host = parts.hostname.lower()
    if host in ("giphy.com", "www.giphy.com"):
        match = re.match(r"^/(?:gifs|stickers|embed|clips)/(?:.*-)?([A-Za-z0-9]+)/?$", parts.path)
        if match:
            return f"https://media.giphy.com/media/{match.group(1)}/giphy.gif"
        return None
    if parts.path.lower().rsplit(".", 1)[-1] in IMAGE_EXT:
        return text
    return None


def valid_gif_url(url):
    return normalize_gif_url(url) is not None


def save_many(files, limit):
    """Save every chosen file (up to `limit`). Returns [(path, type), ...].
    If any file is invalid, the ones already saved are removed again."""
    saved = []
    try:
        for file in files:
            if not file or not file.filename:
                continue
            if len(saved) >= limit:
                raise ValueError(f"A post can have at most {MAX_PER_POST} photos or videos.")
            saved.append(save_media(file))
    except ValueError:
        for path, _ in saved:
            delete_media(path)
        raise
    return saved


def save_avatar(file):
    """Crop an uploaded picture to a square, shrink it, and store it as JPEG."""
    ext = file.filename.rsplit(".", 1)[-1].lower() if "." in file.filename else ""
    if ext not in IMAGE_EXT:
        raise ValueError("Profile photos must be jpg, png, gif or webp.")
    try:
        image = ImageOps.exif_transpose(Image.open(io.BytesIO(file.read())))
        image = ImageOps.fit(image.convert("RGB"), (AVATAR_SIZE, AVATAR_SIZE))
    except Exception:
        raise ValueError("That file isn't a picture we can read.")
    os.makedirs(UPLOAD_DIR, exist_ok=True)
    name = f"avatar-{uuid.uuid4().hex}.jpg"
    image.save(os.path.join(UPLOAD_DIR, name), "JPEG", quality=88)
    return f"uploads/{name}"


def delete_media(path):
    if path and not path.startswith("http"):
        try:
            os.remove(os.path.join(os.path.dirname(UPLOAD_DIR), path))
        except OSError:
            pass
