"""Shared by DMs and server channels: turn an upload request into a list of attachments."""
from media import save_attachment, normalize_gif_url, delete_media

MAX_AT_ONCE = 10


def collect(request):
    """Returns [(path_or_url, kind, name), ...] from the request's `files` and/or `gif` fields.
    Raises ValueError (after removing anything already saved) if something is not allowed."""
    saved = []
    try:
        for file in request.files.getlist("files"):
            if not file or not file.filename:
                continue
            if len(saved) >= MAX_AT_ONCE:
                raise ValueError(f"Send at most {MAX_AT_ONCE} files at a time.")
            saved.append(save_attachment(file))
        gif = request.form.get("gif", "").strip()
        if gif:
            url = normalize_gif_url(gif)
            if not url:
                raise ValueError("That doesn't look like a GIF or image link (it must start with https://).")
            saved.append((url, "image", "gif"))
    except ValueError:
        for path, _, _ in saved:
            delete_media(path)
        raise
    if not saved:
        raise ValueError("Nothing to send.")
    return saved
