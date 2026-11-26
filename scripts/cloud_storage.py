"""Object storage for cloud mode: Cloudflare R2 (via boto3) and an in-memory stand-in for tests."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol
from urllib.parse import quote

PUT_EXPIRY_SECONDS = 3600


def _part(value: str) -> str:
    if not value or "/" in value or "\\" in value or ".." in value:
        raise ValueError(f"invalid key part: {value!r}")
    return value


def upload_key(uid: str, upload_id: str) -> str:
    return f"uploads/{_part(uid)}/{_part(upload_id)}"


def export_key(uid: str, pid: str, file: str) -> str:
    return f"exports/{_part(uid)}/{_part(pid)}/{_part(file)}"


def zip_key(uid: str, bid: str) -> str:
    return f"zips/{_part(uid)}/{_part(bid)}.zip"


def sync_video_key(uid: str, pid: str) -> str:
    """A cloud project's video published for the desktop app to download once. It lives with the zips: a 24 h
    download, counted toward storage and removed by the retention sweep's zip rule after a day."""
    return f"zips/{_part(uid)}/{_part(pid)}.video"


def is_sync_video_key(key: str) -> bool:
    """A temporary download copy of a project video that is already counted on the Volume."""
    return key.startswith("zips/") and key.endswith(".video")


def uploads_prefix(uid: str) -> str:
    return f"uploads/{_part(uid)}/"


def exports_prefix(uid: str, pid: str | None = None) -> str:
    if pid is None:
        return f"exports/{_part(uid)}/"
    return f"exports/{_part(uid)}/{_part(pid)}/"


def zips_prefix(uid: str) -> str:
    return f"zips/{_part(uid)}/"


def _check_prefix(prefix: str) -> str:
    if not prefix or not prefix.endswith("/"):
        raise ValueError(f"prefix must be non-empty and end with '/': {prefix!r}")
    return prefix


class Storage(Protocol):
    def presign_put(self, key: str, content_type: str, size: int) -> str: ...
    def presign_get(self, key: str, filename: str, seconds: int = 86400) -> str: ...
    def size(self, key: str) -> int | None: ...
    def download(self, key: str, dest: Path) -> None: ...
    def upload(self, src: Path, key: str, content_type: str) -> None: ...
    def delete(self, key: str) -> None: ...
    def delete_prefix(self, prefix: str) -> int: ...
    def usage_bytes(self, prefix: str) -> int: ...
    def list_keys(self, prefix: str) -> list[str]: ...
    def list(self, prefix: str) -> list[tuple[str, int, datetime]]: ...  # (key, size, last_modified, tz-aware UTC)


def _content_disposition(filename: str) -> str:
    ascii_name = "".join(c if 32 <= ord(c) < 127 and c not in ('"', "\\") else "_" for c in filename)
    return f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(filename, safe='')}"


class R2Storage:
    def __init__(self, account_id: str, bucket: str, access_key: str, secret_key: str):
        import boto3  # lazy: desktop installs do not need the cloud extra
        from botocore.config import Config

        self.bucket = bucket
        self._client = boto3.client(
            "s3",
            endpoint_url=f"https://{account_id}.r2.cloudflarestorage.com",
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
            region_name="auto",
            config=Config(
                signature_version="s3v4",
                s3={"addressing_style": "path"},
                request_checksum_calculation="when_required",
                response_checksum_validation="when_required",
            ),
        )

    def presign_put(self, key: str, content_type: str, size: int) -> str:
        return self._client.generate_presigned_url(
            "put_object",
            Params={"Bucket": self.bucket, "Key": key, "ContentType": content_type, "ContentLength": size},
            ExpiresIn=PUT_EXPIRY_SECONDS,
        )

    def presign_get(self, key: str, filename: str, seconds: int = 86400) -> str:
        return self._client.generate_presigned_url(
            "get_object",
            Params={"Bucket": self.bucket, "Key": key, "ResponseContentDisposition": _content_disposition(filename)},
            ExpiresIn=seconds,
        )

    def size(self, key: str) -> int | None:
        from botocore.exceptions import ClientError

        try:
            return int(self._client.head_object(Bucket=self.bucket, Key=key)["ContentLength"])
        except ClientError as e:
            status = e.response.get("ResponseMetadata", {}).get("HTTPStatusCode")
            if status == 404 or e.response.get("Error", {}).get("Code") in ("404", "NoSuchKey", "NotFound"):
                return None
            raise

    def download(self, key: str, dest: Path) -> None:
        dest = Path(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        self._client.download_file(self.bucket, key, str(dest))

    def upload(self, src: Path, key: str, content_type: str) -> None:
        self._client.upload_file(str(src), self.bucket, key, ExtraArgs={"ContentType": content_type})

    def delete(self, key: str) -> None:
        self._client.delete_object(Bucket=self.bucket, Key=key)

    def _pages(self, prefix: str):
        token = None
        while True:
            kw = {"Bucket": self.bucket, "Prefix": prefix}
            if token:
                kw["ContinuationToken"] = token
            resp = self._client.list_objects_v2(**kw)
            yield resp.get("Contents", [])
            if not resp.get("IsTruncated"):
                return
            token = resp["NextContinuationToken"]

    def delete_prefix(self, prefix: str) -> int:
        _check_prefix(prefix)
        n = 0
        for objs in self._pages(prefix):
            for i in range(0, len(objs), 1000):
                batch = [{"Key": o["Key"]} for o in objs[i:i + 1000]]
                resp = self._client.delete_objects(Bucket=self.bucket, Delete={"Objects": batch, "Quiet": True})
                errors = resp.get("Errors") or []
                if errors:
                    failed = [e.get("Key") for e in errors]
                    raise RuntimeError(f"failed to delete {len(failed)} object(s): {failed}")
                n += len(batch)
        return n

    def usage_bytes(self, prefix: str) -> int:
        _check_prefix(prefix)
        return sum(o["Size"] for objs in self._pages(prefix) for o in objs)

    def list_keys(self, prefix: str) -> list[str]:
        _check_prefix(prefix)
        return sorted(o["Key"] for objs in self._pages(prefix) for o in objs)

    def list(self, prefix: str) -> list[tuple[str, int, datetime]]:
        _check_prefix(prefix)
        return sorted((o["Key"], int(o["Size"]), o["LastModified"]) for objs in self._pages(prefix) for o in objs)


class MemoryStorage:
    """Dict-backed Storage for tests; presigned URLs are mem:// markers."""

    def __init__(self):
        self.objects: dict[str, bytes] = {}
        self.content_types: dict[str, str] = {}
        self.modified: dict[str, datetime] = {}  # last-modified per key; a key put straight into `objects` is "now"

    def presign_put(self, key: str, content_type: str, size: int) -> str:
        return f"mem://put/{key}?type={quote(content_type)}&size={size}"

    def presign_get(self, key: str, filename: str, seconds: int = 86400) -> str:
        return f"mem://get/{key}?filename={quote(filename)}&expires={seconds}"

    def size(self, key: str) -> int | None:
        data = self.objects.get(key)
        return None if data is None else len(data)

    def download(self, key: str, dest: Path) -> None:
        dest = Path(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(self.objects[key])

    def upload(self, src: Path, key: str, content_type: str) -> None:
        self.objects[key] = Path(src).read_bytes()
        self.content_types[key] = content_type
        self.modified[key] = datetime.now(timezone.utc)

    def delete(self, key: str) -> None:
        self.objects.pop(key, None)
        self.content_types.pop(key, None)
        self.modified.pop(key, None)

    def delete_prefix(self, prefix: str) -> int:
        _check_prefix(prefix)
        keys = [k for k in self.objects if k.startswith(prefix)]
        for k in keys:
            self.delete(k)
        return len(keys)

    def usage_bytes(self, prefix: str) -> int:
        _check_prefix(prefix)
        return sum(len(v) for k, v in self.objects.items() if k.startswith(prefix))

    def list_keys(self, prefix: str) -> list[str]:
        _check_prefix(prefix)
        return sorted(k for k in self.objects if k.startswith(prefix))

    def list(self, prefix: str) -> list[tuple[str, int, datetime]]:
        _check_prefix(prefix)
        now = datetime.now(timezone.utc)
        return sorted((k, len(v), self.modified.get(k, now)) for k, v in self.objects.items() if k.startswith(prefix))
