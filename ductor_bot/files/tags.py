"""File path tag parsing, MIME detection, and file classification."""

from __future__ import annotations

import mimetypes
import os
import re
from pathlib import Path
from urllib.parse import unquote, urlparse

import filetype as _filetype

from ductor_bot.infra.platform import is_windows

FILE_PATH_RE = re.compile(r"<file:([^>]+)>")

_SVG_SUFFIXES = frozenset({".svg", ".svgz"})


def extract_file_paths(text: str) -> list[str]:
    """Return all ``<file:/path>`` references from *text*."""
    return FILE_PATH_RE.findall(text)


def path_from_file_tag(file_tag: str) -> Path:
    """Convert one ``<file:...>`` payload to a local filesystem path.

    Handles plain paths and ``file:`` URIs. On Windows it normalizes
    drive-letter variants such as ``/C/...`` and ``/C:/...``.
    """
    value = file_tag.strip()
    if not value:
        return Path(value)

    parsed = urlparse(value)
    if parsed.scheme == "file":
        if parsed.netloc and parsed.path:
            # file://server/share/path or file://C:/Users/...
            value = f"//{parsed.netloc}{parsed.path}"
        elif parsed.netloc:
            value = f"//{parsed.netloc}"
        else:
            value = parsed.path or ""

    value = unquote(value)
    if is_windows():
        value = _normalize_windows_tag_path(value)
    return _resolve_container_path(Path(value))


def guess_mime(path: Path | str) -> str:
    """Guess MIME type using magic bytes first, then extension fallback.

    Uses the ``filetype`` library for binary format detection (images, audio,
    video, archives).  Falls back to ``mimetypes`` for text-based formats
    (source code, SVG, plain text) that lack magic byte signatures.
    """
    kind = _filetype.guess(str(path))
    if kind is not None:
        return str(kind.mime)
    return mimetypes.guess_type(str(path))[0] or "application/octet-stream"


def classify_mime(mime: str) -> str:
    """Classify a MIME type string into a category.

    Returns ``"photo"``, ``"audio"``, ``"video"``, or ``"document"``.
    """
    if mime.startswith("image/"):
        return "photo"
    if mime.startswith("audio/"):
        return "audio"
    if mime.startswith("video/"):
        return "video"
    return "document"


def is_image_path(path_str: str) -> bool:
    """Check if a file path points to a raster image based on extension/MIME.

    Uses extension-based detection only (no file access).  For content-based
    detection on files that exist on disk, use ``guess_mime`` instead.
    """
    mime = mimetypes.guess_type(path_str)[0] or ""
    return mime.startswith("image/") and Path(path_str).suffix.lower() not in _SVG_SUFFIXES


_DOCKER_MOUNT = "/ductor"


def _resolve_container_path(path: Path) -> Path:
    """Translate Docker container paths to host paths.

    Inside the sandbox container ``~/.ductor`` is mounted at ``/ductor``.
    CLI output references container-side paths like
    ``/ductor/workspace/output_to_user/file.png`` which don't exist on the
    host.  This rewrites them to the real host path.
    """
    try:
        relative = path.relative_to(_DOCKER_MOUNT)
    except ValueError:
        return path
    ductor_home = Path(
        os.environ.get("DUCTOR_HOME", str(Path.home() / ".ductor")),
    ).expanduser()
    return ductor_home / relative


def _normalize_windows_tag_path(value: str) -> str:
    """Normalize Windows drive-letter path variants from file tags."""
    path = value.replace("\\", "/")

    # Handle Docker-on-Windows mount mapping.
    if path.startswith("/ductor/"):
        import os
        # 1. First, try DUCTOR_HOME environment variable
        h = str(Path.home() / ".ductor")
        dh = os.environ.get("DUCTOR_HOME", h).replace("\\", "/")
        
        # 2. Heuristic: if DUCTOR_HOME doesn't exist, try to infer from package location.
        # This is very robust on Windows host where we run the bot.
        if not Path(dh).exists():
            pkg_root = Path(__file__).parent.parent.parent.parent # ~/.ductor/ductor_bot/...
            if pkg_root.name == ".ductor" or (pkg_root.parent.name == ".ductor"):
                 dh = str(pkg_root if pkg_root.name == ".ductor" else pkg_root.parent).replace("\\", "/")

        return path.replace("/ductor/", f"{dh}/", 1)

    # file://C:/Users/... -> //C:/Users/... after URI parsing
    if len(path) >= 4 and path.startswith("//") and path[2].isalpha() and path[3] == ":":
        path = path[2:]

    # /C:/Users/... -> C:/Users/...
    if len(path) >= 3 and path[0] == "/" and path[1].isalpha() and path[2] == ":":
        return path[1:]

    # /C/Users/... -> C:/Users/...
    if (
        len(path) >= 2
        and path[0] == "/"
        and path[1].isalpha()
        and (len(path) == 2 or path[2] == "/")
    ):
        tail = path[2:]
        if not tail:
            tail = "/"
        elif not tail.startswith("/"):
            tail = f"/{tail}"
        return f"{path[1]}:{tail}"

    return path
