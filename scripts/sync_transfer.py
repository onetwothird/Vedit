"""Moves a project's video between this computer and cloud storage for sync, as a job step of the local engine.

    sync_transfer.py get <part> <dest>             download $VEDIT_SYNC_URL to <part>, then rename it to <dest>
    sync_transfer.py put <src> <content-type>      upload <src> to $VEDIT_SYNC_URL (a presigned PUT)

The URL comes from the environment, never argv (other processes can read argv, and a presigned URL is a credential).
Only https:// URLs are accepted (plus http://127.0.0.1 and http://localhost, for tests); redirects are held to the
same rule. Both directions stream in chunks and report `@@progress` lines. A failed download deletes its partial
file; a killed one leaves <part>, which the engine removes when the job ends.
"""
from __future__ import annotations

import http.client
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parent))

from common import progress  # noqa: E402

CHUNK = 1024 * 1024
TIMEOUT = 60
LOCAL_HOSTS = ("127.0.0.1", "localhost")


def check_url(url) -> str:
    """`url` if it is https:// (or http:// to this computer); ValueError otherwise."""
    if not isinstance(url, str) or any(c.isspace() or ord(c) < 32 for c in url):
        raise ValueError("Only https:// links are allowed")
    try:
        parts = urlsplit(url)
        host = parts.hostname
        parts.port  # noqa: B018 - raises on a malformed port
    except ValueError:
        raise ValueError("Only https:// links are allowed") from None
    if not host:
        raise ValueError("Only https:// links are allowed")
    if parts.scheme == "https" or (parts.scheme == "http" and host in LOCAL_HOSTS):
        return url
    raise ValueError("Only https:// links are allowed")


class _SameRules(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        check_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


_opener = urllib.request.build_opener(_SameRules())


def _pct(done: int, total: int) -> float:
    return 100.0 * done / total if total > 0 else 0.0


def download(url: str, part: Path, dest: Path) -> None:
    check_url(url)
    part.parent.mkdir(parents=True, exist_ok=True)
    try:
        with _opener.open(urllib.request.Request(url, method="GET"), timeout=TIMEOUT) as res, open(part, "wb") as out:
            total = int(res.headers.get("Content-Length") or 0)
            done = 0
            while True:
                chunk = res.read(CHUNK)
                if not chunk:
                    break
                out.write(chunk)
                done += len(chunk)
                progress("download", _pct(done, total), "Downloading the video")
        if total and done != total:
            raise OSError(f"the download stopped early ({done} of {total} bytes)")
        os.replace(part, dest)
    except BaseException:
        part.unlink(missing_ok=True)
        raise


class _Reading:
    """A file that reports how much of it has been read (http.client sends a body by calling read())."""

    def __init__(self, f, total: int):
        self.f, self.total, self.done = f, total, 0

    def read(self, n: int = -1) -> bytes:
        data = self.f.read(CHUNK if n is None or n < 0 else min(n, CHUNK))
        self.done += len(data)
        progress("upload", _pct(self.done, self.total), "Uploading the video")
        return data


def upload(url: str, src: Path, content_type: str) -> None:
    check_url(url)
    size = src.stat().st_size
    with open(src, "rb") as f:
        req = urllib.request.Request(url, data=_Reading(f, size), method="PUT",
                                     headers={"Content-Type": content_type, "Content-Length": str(size)})
        with _opener.open(req, timeout=TIMEOUT) as res:
            if not 200 <= res.status < 300:
                raise OSError(f"the upload was refused ({res.status})")


def main(argv: list[str]) -> int:
    url = os.environ.get("VEDIT_SYNC_URL", "")
    try:
        if len(argv) == 3 and argv[0] == "get":
            download(url, Path(argv[1]), Path(argv[2]))
        elif len(argv) == 3 and argv[0] == "put":
            upload(url, Path(argv[1]), argv[2])
        else:
            print(__doc__, file=sys.stderr)
            return 2
    except urllib.error.HTTPError as e:
        print(f"The server answered {e.code}", file=sys.stderr)
        return 1
    except urllib.error.URLError as e:  # never print the URL: it carries a signature
        print(f"Could not reach the server: {e.reason}", file=sys.stderr)
        return 1
    except http.client.HTTPException as e:
        print(f"Transfer failed: the connection broke ({type(e).__name__})", file=sys.stderr)
        return 1
    except (OSError, ValueError) as e:
        print(f"Transfer failed: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
