"""
Backblaze B2 client wrapper. B2 is S3-compatible so we use boto3.

Single class so the management commands depend on a typed surface
and tests can mock it cleanly. Real config comes from env vars; the
factory `build_default_storage()` resolves them.

Required env vars (production via Render dashboard secrets):
    B2_ENDPOINT_URL      e.g. https://s3.us-east-005.backblazeb2.com
    B2_KEY_ID            access key id
    B2_APPLICATION_KEY   secret access key
    B2_BUCKET_NAME       e.g. family-books-backups
    B2_PREFIX            e.g. prod/  (empty string for none)
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

import boto3
from botocore.config import Config

from books.core.backup.retention import BackupObject


@dataclass(frozen=True)
class B2Config:
    endpoint_url: str
    key_id: str
    application_key: str
    bucket: str
    prefix: str  # may be "" for root

    @classmethod
    def from_env(cls) -> B2Config:
        try:
            return cls(
                endpoint_url=os.environ["B2_ENDPOINT_URL"],
                key_id=os.environ["B2_KEY_ID"],
                application_key=os.environ["B2_APPLICATION_KEY"],
                bucket=os.environ["B2_BUCKET_NAME"],
                prefix=os.environ.get("B2_PREFIX", ""),
            )
        except KeyError as exc:
            raise RuntimeError(
                f"Missing required B2 env var: {exc.args[0]}. See "
                "books/core/backup/storage.py for the full list."
            ) from exc


class B2Storage:
    """Thin wrapper around boto3 S3 client pointing at B2."""

    def __init__(self, config: B2Config):
        self.config = config
        self._client = boto3.client(
            "s3",
            endpoint_url=config.endpoint_url,
            aws_access_key_id=config.key_id,
            aws_secret_access_key=config.application_key,
            config=Config(signature_version="s3v4"),
        )

    def upload(self, local_path: Path, key: str) -> int:
        """Upload local file to B2. Returns file size in bytes."""
        full_key = self._full_key(key)
        size = local_path.stat().st_size
        self._client.upload_file(str(local_path), self.config.bucket, full_key)
        return size

    def download(self, key: str, local_path: Path) -> None:
        """Download a B2 object to local_path."""
        self._client.download_file(
            self.config.bucket, self._full_key(key), str(local_path),
        )

    def list_objects(self) -> list[BackupObject]:
        """List backup objects under the configured prefix.

        Parses the date out of each key via parse_object_key(). Keys
        that don't match the expected shape are skipped (not raised
        — they may be unrelated objects in the bucket prefix).
        """
        paginator = self._client.get_paginator("list_objects_v2")
        objects: list[BackupObject] = []
        kwargs = {"Bucket": self.config.bucket}
        if self.config.prefix:
            kwargs["Prefix"] = self.config.prefix
        for page in paginator.paginate(**kwargs):
            for entry in page.get("Contents", []) or []:
                key = entry["Key"]
                # Strip the prefix so callers see consistent keys.
                relative = key[len(self.config.prefix):] if self.config.prefix else key
                taken = parse_object_key(relative)
                if taken is None:
                    continue
                objects.append(BackupObject(key=relative, taken_on=taken))
        return objects

    def delete(self, key: str) -> None:
        self._client.delete_object(
            Bucket=self.config.bucket, Key=self._full_key(key),
        )

    def _full_key(self, key: str) -> str:
        if not self.config.prefix:
            return key
        if key.startswith(self.config.prefix):
            return key
        return f"{self.config.prefix}{key}"


# ---------------------------------------------------------------------------
# Object key conventions
# ---------------------------------------------------------------------------


def make_object_key(taken_at_utc) -> str:
    """Object key for a backup taken at the given UTC datetime.

    Format: db-YYYY-MM-DD-HHMMSS.sql.age
    The date is prominent so retention parsing is trivial; the time
    suffix prevents collisions if two backups land in the same day.
    """
    return f"db-{taken_at_utc.strftime('%Y-%m-%d-%H%M%S')}.sql.age"


def parse_object_key(key: str):
    """Extract the date from a backup object key.

    Returns None if the key doesn't match the shape `db-YYYY-MM-DD-...`.
    Used by list_objects() to filter unrelated objects in the bucket.
    """
    from datetime import date

    # Expected: db-YYYY-MM-DD-HHMMSS.sql.age
    if not key.startswith("db-"):
        return None
    rest = key[len("db-"):]
    if len(rest) < 10:
        return None
    date_part = rest[:10]
    try:
        return date.fromisoformat(date_part)
    except ValueError:
        return None


def build_default_storage() -> B2Storage:
    """Production entry point — resolves config from env."""
    return B2Storage(B2Config.from_env())
