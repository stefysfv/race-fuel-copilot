"""SHELL_CACHE must move when a precached shell file changes.

The recorded hash covers every SHELL_FILES body with LF newlines. The
SHELL_FILES_HASH line in runtime_manifest.js is omitted so the hash can
sit in that file without changing itself.
"""

from __future__ import annotations

import ast
import hashlib
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "frontend" / "runtime_manifest.js"
FRONTEND = ROOT / "frontend"

_ARRAY = re.compile(r"export const (RACE_FILES|SHELL_FILES) = \[(.*?)\];", re.S)
_HASH = re.compile(r'^export const SHELL_FILES_HASH = "([0-9a-f]+)";?$', re.M)
_BINARY = {".png", ".jpg", ".jpeg", ".webp", ".ico", ".wasm", ".zip"}


def _shell_urls(source: str) -> list[str]:
    arrays: dict[str, list[str]] = {}
    for match in _ARRAY.finditer(source):
        items: list[str] = []
        for raw in match.group(2).split(","):
            token = raw.strip()
            if not token:
                continue
            if token.startswith("..."):
                items.extend(arrays[token[3:]])
                continue
            items.append(ast.literal_eval(token))
        arrays[match.group(1)] = items
    return arrays["SHELL_FILES"]


def _body(url: str, manifest: str) -> bytes:
    path = FRONTEND / "index.html" if url == "/" else FRONTEND / url.lstrip("/")
    data = path.read_bytes()
    if path.suffix.lower() in _BINARY:
        return data
    text = data.replace(b"\r\n", b"\n").replace(b"\r", b"\n").decode("utf-8")
    if path.name == "runtime_manifest.js":
        kept = [
            line
            for line in text.splitlines(keepends=True)
            if not line.startswith("export const SHELL_FILES_HASH ")
        ]
        text = "".join(kept)
    return text.encode("utf-8")


def shell_files_hash(manifest: str | None = None) -> str:
    """sha256 of the shell list. The hash line itself is not an input."""

    source = manifest if manifest is not None else MANIFEST.read_text(encoding="utf-8")
    digest = hashlib.sha256()
    for url in _shell_urls(source):
        body = _body(url, source)
        digest.update(url.encode("utf-8"))
        digest.update(b"\0")
        digest.update(len(body).to_bytes(8, "big"))
        digest.update(body)
    return digest.hexdigest()


def test_shell_cache_hash_matches_shell_files() -> None:
    source = MANIFEST.read_text(encoding="utf-8")
    recorded = _HASH.search(source)
    computed = shell_files_hash(source)
    assert recorded is not None, (
        "SHELL_FILES_HASH is missing from frontend/runtime_manifest.js. "
        "Bump SHELL_CACHE and set SHELL_FILES_HASH to the hash of the shell files."
    )
    assert recorded.group(1) == computed, (
        "A precached shell file changed. Bump SHELL_CACHE in "
        "frontend/runtime_manifest.js and set SHELL_FILES_HASH to "
        f"{computed}."
    )
