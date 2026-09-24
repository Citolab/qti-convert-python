"""Minimal POSIX path helpers for package-relative paths."""

from __future__ import annotations

import re

_ABSOLUTE_URL = re.compile(r"^([a-z][a-z0-9+.-]*:|/|#)", re.IGNORECASE)


def is_relative_url(url: str) -> bool:
    return not _ABSOLUTE_URL.match(url.strip())


def normalize_path(path: str) -> str:
    parts = []
    for part in path.replace("\\", "/").split("/"):
        if part in ("", "."):
            continue
        if part == ".." and parts and parts[-1] != "..":
            parts.pop()
        else:
            parts.append(part)
    return "/".join(parts)


def dirname(path: str) -> str:
    index = path.replace("\\", "/").rfind("/")
    return "" if index == -1 else path[:index]


def join_path(base: str, relative: str) -> str:
    return normalize_path(f"{base}/{relative}") if base else relative


def relative_path(from_dir: str, target: str) -> str:
    """Path of ``target`` relative to the directory ``from_dir`` (both package-relative)."""
    source = [p for p in normalize_path(from_dir).split("/") if p]
    to = [p for p in normalize_path(target).split("/") if p]
    common = 0
    while common < len(source) and common < len(to) - 1 and source[common] == to[common]:
        common += 1
    return "/".join([".."] * (len(source) - common) + to[common:])


_MIME_TYPES = {
    "png": "image/png",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "gif": "image/gif",
    "svg": "image/svg+xml",
    "webp": "image/webp",
    "mp3": "audio/mpeg",
    "wav": "audio/wav",
    "ogg": "audio/ogg",
    "m4a": "audio/mp4",
    "mp4": "video/mp4",
    "webm": "video/webm",
    "ogv": "video/ogg",
}


def mime_type_from_path(path: str) -> str:
    extension = re.split(r"[?#]", path)[0].rsplit(".", 1)[-1].lower()
    return _MIME_TYPES.get(extension, "application/octet-stream")
