"""Versioned resume evidence and write-ahead intent for ETL staging.

The filesystem checkpoint is not atomic with PostgreSQL. An outstanding
intent therefore forces a fresh staging build rather than guessing whether
an interrupted database write committed.
"""

from __future__ import annotations

import json
import logging
import os
import re
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath

from app.config import settings

logger = logging.getLogger(__name__)
CHECKPOINT_VERSION = 2


def is_logical_file_id(value: object) -> bool:
    return (
        isinstance(value, str)
        and bool(value)
        and not PurePosixPath(value).is_absolute()
        and "\\" not in value
        and ":" not in value
        and all(part not in ("", ".", "..") for part in value.split("/"))
    )


class CheckpointManager:
    """Keep source evidence, committed staging stock and pending write intent."""

    def __init__(self, dataset_name: str, staging_table: str) -> None:
        self.dataset_name = dataset_name
        self.staging_table = staging_table
        self._dir = Path(settings.etl_temp_dir) / f"datacommune_{dataset_name}"
        self._path = self._dir / "checkpoint.json"

    @property
    def path(self) -> Path:
        return self._path

    def exists(self) -> bool:
        return self._path.exists()

    def _valid(self, data: object) -> bool:  # noqa: C901
        if not isinstance(data, dict):
            return False
        if (
            type(data.get("version")) is not int
            or data["version"] != CHECKPOINT_VERSION
            or data.get("dataset_name") != self.dataset_name
            or data.get("staging_table") != self.staging_table
            or not isinstance(data.get("scope_key"), str)
            or not isinstance(data.get("identity_key"), str)
            or not data["identity_key"]
            or type(data.get("staging_rows")) is not int
            or data["staging_rows"] < 0
            or not isinstance(data.get("source_files"), list)
            or not isinstance(data.get("loaded_files"), list)
            or "pending" not in data
            or not isinstance(data.get("scope_filtered", False), bool)
        ):
            return False
        identifiers = set()
        for entry in data["source_files"]:
            if (
                not isinstance(entry, dict)
                or not is_logical_file_id(entry.get("id"))
                or entry["id"] in identifiers
                or type(entry.get("bytes")) is not int
                or entry["bytes"] < 0
                or not isinstance(entry.get("sha256"), str)
                or not re.fullmatch(r"[a-f0-9]{64}", entry["sha256"])
            ):
                return False
            identifiers.add(entry["id"])
        loaded = data["loaded_files"]
        if any(
            not is_logical_file_id(name) or name not in identifiers for name in loaded
        ):
            return False
        if len(loaded) != len(set(loaded)):
            return False
        pending = data["pending"]
        if pending is not None:
            if not isinstance(pending, dict) or set(pending) != {
                "operation",
                "file_id",
            }:
                return False
            if pending["operation"] == "stage":
                if (
                    not is_logical_file_id(pending["file_id"])
                    or pending["file_id"] not in identifiers
                ):
                    return False
            elif pending["operation"] != "filter" or pending["file_id"] is not None:
                return False
        return True

    def load(self) -> dict | None:
        if not self._path.exists():
            return None
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
        except (ValueError, OSError) as exc:
            logger.warning(
                "Checkpoint %s unreadable (%s); rebuilding staging", self._path, exc
            )
            return None
        if not self._valid(data):
            logger.warning(
                "Checkpoint %s legacy or incompatible; rebuilding staging", self._path
            )
            return None
        return data

    def get_loaded_files(self) -> set[str]:
        data = self.load()
        return set(data["loaded_files"]) if data is not None else set()

    def save(
        self,
        loaded_files: list[str],
        *,
        scope_key: str = "",
        identity_key: str,
        source_files: list[dict],
        staging_rows: int,
        pending: dict | None = None,
        scope_filtered: bool = False,
    ) -> None:
        payload = {
            "version": CHECKPOINT_VERSION,
            "dataset_name": self.dataset_name,
            "staging_table": self.staging_table,
            "scope_key": scope_key,
            "identity_key": identity_key,
            "run_started_at": datetime.now(UTC).isoformat(),
            "source_files": source_files,
            "loaded_files": sorted(set(loaded_files)),
            "staging_rows": staging_rows,
            "pending": pending,
            # The scope filter has run and its whole-file check passed: a
            # resume must not recheck the filtered table (04.1-REVIEW WR-R1).
            "scope_filtered": scope_filtered,
        }
        if not self._valid(payload):
            raise ValueError("Incomplete or invalid resume evidence")
        self._dir.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(".json.tmp")
        try:
            with tmp.open("w", encoding="utf-8") as stream:
                json.dump(payload, stream, indent=2)
                stream.flush()
                os.fsync(stream.fileno())
            tmp.replace(self._path)
            if os.name == "posix":
                descriptor = os.open(self._dir, os.O_DIRECTORY)
                try:
                    os.fsync(descriptor)
                finally:
                    os.close(descriptor)
        finally:
            tmp.unlink(missing_ok=True)

    def clear(self) -> None:
        try:
            self._path.unlink(missing_ok=True)
        except OSError as exc:
            logger.warning("Failed to clear checkpoint %s: %s", self._path, exc)
