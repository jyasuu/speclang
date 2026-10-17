"""Remote includes: fetch spec files from a git repository or an HTTPS URL into a local cache.

Forms accepted in an `include:` list:

  git+https://host/org/repo.git@v1.2.0#path=spec     a folder (or file) inside a repository, at a tag, branch or commit
  git+file:///work/shared-spec@main                   any URL git can clone, including local paths
  https://host/path/platform.yaml                     a single YAML file (plain http is accepted only for localhost)

Fetched content is cached (default ~/.cache/speclang, override with SPECLANG_CACHE) and reused on later runs, so
pin a tag or a commit id: a branch name is cached at the commit it had when first fetched.
Set SPECLANG_OFFLINE=1 to forbid network access; anything not already cached then fails with a clear error.
"""
import hashlib
import os
import re
import shutil
import subprocess
import sys
import urllib.request
from pathlib import Path
from urllib.parse import urlparse

MAX_BYTES = 2 * 1024 * 1024
TIMEOUT = 20


class RemoteError(Exception):
    """A remote include could not be fetched or is not allowed."""


def is_remote(include):
    return isinstance(include, str) and include.startswith(("git+", "https://", "http://"))


def cache_dir():
    return Path(os.environ.get("SPECLANG_CACHE") or Path.home() / ".cache" / "speclang")


def offline():
    return os.environ.get("SPECLANG_OFFLINE", "") not in ("", "0")


def _key(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def parse_git(include):
    """git+URL[@ref][#path=sub] -> (url, ref or None, sub or None)."""
    body = include[len("git+"):]
    sub = None
    if "#" in body:
        body, fragment = body.split("#", 1)
        m = re.fullmatch(r"path=(.+)", fragment)
        if not m:
            raise RemoteError(f"{include}: the part after # must be path=<folder or file>")
        sub = m.group(1).strip("/")
    ref = None
    m = re.fullmatch(r"(.+?)@([^@/]+)", body)       # a ref has no slash, so user@host:path is not mistaken for one
    if m:
        body, ref = m.group(1), m.group(2)
    if not body:
        raise RemoteError(f"{include}: no repository URL")
    return body, ref, sub


def _git(args):
    try:
        done = subprocess.run(["git", *args], capture_output=True, text=True, timeout=120,
                              env={**os.environ, "GIT_TERMINAL_PROMPT": "0"})
    except FileNotFoundError as exc:
        raise RemoteError("git is not installed, so a git include cannot be fetched") from exc
    except subprocess.TimeoutExpired as exc:
        raise RemoteError("git timed out") from exc
    if done.returncode != 0:
        raise RemoteError(done.stderr.strip().splitlines()[-1] if done.stderr.strip() else "git failed")
    return done.stdout.strip()


def fetch_git(include):
    """Clone (once) and return (path to the include, repository root)."""
    url, ref, sub = parse_git(include)
    root = cache_dir() / "git" / _key(f"{url}@{ref or ''}")
    if not (root / ".git").exists():
        if offline():
            raise RemoteError(f"{include}: not in the cache and SPECLANG_OFFLINE is set")
        print(f"speclint: fetching {include}", file=sys.stderr)
        root.parent.mkdir(parents=True, exist_ok=True)
        tmp = root.with_name(root.name + ".tmp")
        shutil.rmtree(tmp, ignore_errors=True)
        try:
            _git(["clone", "--quiet", "--depth", "1", *(["--branch", ref] if ref else []), url, str(tmp)])
        except RemoteError as first:
            if not ref:
                raise RemoteError(f"{include}: {first}") from first
            # a commit id cannot be cloned with --branch: fetch that commit directly
            shutil.rmtree(tmp, ignore_errors=True)
            tmp.mkdir(parents=True)
            try:
                _git(["init", "--quiet", str(tmp)])
                _git(["-C", str(tmp), "fetch", "--quiet", "--depth", "1", url, ref])
                _git(["-C", str(tmp), "checkout", "--quiet", "FETCH_HEAD"])
            except RemoteError as second:
                shutil.rmtree(tmp, ignore_errors=True)
                raise RemoteError(f"{include}: could not fetch '{ref}' ({first})") from second
        tmp.rename(root)
    target = (root / sub) if sub else root
    resolved_root = root.resolve()
    if resolved_root != target.resolve() and resolved_root not in target.resolve().parents:
        raise RemoteError(f"{include}: the path leaves the repository")
    if not target.exists():
        raise RemoteError(f"{include}: '{sub}' does not exist in the repository")
    return target, root


def fetch_url(include):
    """Download (once) a single YAML file and return (path to it, its folder)."""
    parsed = urlparse(include)
    if parsed.scheme == "http" and parsed.hostname not in ("localhost", "127.0.0.1", "::1"):
        raise RemoteError(f"{include}: use https (plain http is only accepted for localhost)")
    folder = cache_dir() / "url" / _key(include)
    name = Path(parsed.path).name or "spec.yaml"
    if not name.endswith((".yaml", ".yml")):
        raise RemoteError(f"{include}: a URL include must point to a .yaml or .yml file")
    dest = folder / name
    if not dest.exists():
        if offline():
            raise RemoteError(f"{include}: not in the cache and SPECLANG_OFFLINE is set")
        print(f"speclint: fetching {include}", file=sys.stderr)
        try:
            with urllib.request.urlopen(include, timeout=TIMEOUT) as response:      # noqa: S310 (scheme checked above)
                data = response.read(MAX_BYTES + 1)
        except OSError as exc:
            raise RemoteError(f"{include}: {exc}") from exc
        if len(data) > MAX_BYTES:
            raise RemoteError(f"{include}: larger than {MAX_BYTES // 1024} KB")
        folder.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
    return dest, folder


def fetch(include):
    """Return (local path, the root that files fetched this way may not leave)."""
    return fetch_git(include) if include.startswith("git+") else fetch_url(include)
