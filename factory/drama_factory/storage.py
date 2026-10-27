"""Media store shared by the control plane, nodes and review workstations.

Keys are relative POSIX paths ("packages/<id>/...", "takes/<id>/..."). A FileStore needs the same
mount (NVMe-oF / NFS / CephFS) on every machine; an S3Store talks to MinIO and keeps a local cache.
"""
import os
import shutil
from pathlib import Path

from .protocol.manifest import safe_relpath


def check_key(key: str) -> str:
    if not safe_relpath(key):
        raise ValueError(f"非法的存储键：{key}")
    return key


class FileStore:
    def __init__(self, root: Path):
        self.root = Path(root).resolve()

    def path(self, key: str) -> Path:
        return self.root / check_key(key)

    def put(self, src: Path, key: str) -> None:
        dest = self.path(key)
        dest.parent.mkdir(parents=True, exist_ok=True)
        partial = dest.with_name(dest.name + ".part")
        shutil.copyfile(src, partial)
        os.replace(partial, dest)

    def fetch(self, key: str, cache_dir: Path | None = None) -> Path:
        path = self.path(key)
        if not path.is_file():
            raise FileNotFoundError(f"存储中没有 {key}")
        return path

    def exists(self, key: str) -> bool:
        return self.path(key).is_file()


class S3Store:
    def __init__(self, bucket: str, prefix: str = "", cache_dir: Path = Path("./cache"), client=None, **connect):
        if client is None:
            import boto3  # optional dependency: pip install drama-factory[s3]
            client = boto3.client("s3", **{k: v for k, v in connect.items() if v})
        self.client, self.bucket, self.prefix, self.cache_dir = client, bucket, prefix.strip("/"), Path(cache_dir)

    def _object(self, key: str) -> str:
        return f"{self.prefix}/{check_key(key)}" if self.prefix else check_key(key)

    def put(self, src: Path, key: str) -> None:
        self.client.upload_file(str(src), self.bucket, self._object(key))

    def fetch(self, key: str, cache_dir: Path | None = None) -> Path:
        local = Path(cache_dir or self.cache_dir) / check_key(key)
        if not local.is_file():
            local.parent.mkdir(parents=True, exist_ok=True)
            partial = local.with_name(local.name + ".part")
            self.client.download_file(self.bucket, self._object(key), str(partial))
            os.replace(partial, local)
        return local

    def exists(self, key: str) -> bool:
        try:
            self.client.head_object(Bucket=self.bucket, Key=self._object(key))
            return True
        except Exception as exc:  # botocore ClientError without importing botocore
            if getattr(exc, "response", {}).get("Error", {}).get("Code") in ("404", "NoSuchKey", "NotFound"):
                return False
            raise


def make_store(config, cache_dir: Path | None = None):
    if config.kind == "s3":
        return S3Store(config.bucket, config.prefix, cache_dir or Path(config.root) / "cache",
                       endpoint_url=config.endpoint_url, aws_access_key_id=config.access_key_value(),
                       aws_secret_access_key=config.secret_key_value())
    return FileStore(Path(config.root))
