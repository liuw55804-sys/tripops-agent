import hashlib
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field


class ArtifactRecord(BaseModel):
    id: str = Field(min_length=1)
    sha256: str = Field(min_length=64, max_length=64)
    mime_type: str = Field(min_length=1)
    size_bytes: int = Field(ge=0)
    source: str = Field(min_length=1)
    created_at: datetime
    metadata: dict[str, Any] = Field(default_factory=dict)


class ArtifactIntegrityError(RuntimeError):
    pass


class FileArtifactStore:
    """Content-addressed artifact store for large tool results and research outputs."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)

    def put_text(
        self,
        content: str,
        *,
        source: str,
        mime_type: str = "text/plain",
        metadata: dict[str, Any] | None = None,
    ) -> ArtifactRecord:
        payload = content.encode("utf-8")
        digest = hashlib.sha256(payload).hexdigest()
        artifact_id = f"art_{digest[:24]}"
        record = ArtifactRecord(
            id=artifact_id,
            sha256=digest,
            mime_type=mime_type,
            size_bytes=len(payload),
            source=source,
            created_at=datetime.now(UTC),
            metadata=metadata or {},
        )

        content_path = self._content_path(artifact_id)
        metadata_path = self._metadata_path(artifact_id)
        if not content_path.exists():
            self._atomic_write(content_path, payload)
        if not metadata_path.exists():
            serialized = record.model_dump_json(indent=2).encode("utf-8")
            self._atomic_write(metadata_path, serialized)
        return self.get_record(artifact_id)

    def get_text(self, artifact_id: str) -> str:
        record = self.get_record(artifact_id)
        payload = self._content_path(artifact_id).read_bytes()
        actual = hashlib.sha256(payload).hexdigest()
        if actual != record.sha256:
            raise ArtifactIntegrityError(f"artifact checksum mismatch: {artifact_id}")
        return payload.decode("utf-8")

    def get_record(self, artifact_id: str) -> ArtifactRecord:
        metadata_path = self._metadata_path(artifact_id)
        if not metadata_path.exists():
            raise KeyError(f"unknown artifact: {artifact_id}")
        return ArtifactRecord.model_validate_json(metadata_path.read_text(encoding="utf-8"))

    def exists(self, artifact_id: str) -> bool:
        content_exists = self._content_path(artifact_id).exists()
        metadata_exists = self._metadata_path(artifact_id).exists()
        return content_exists and metadata_exists

    def _content_path(self, artifact_id: str) -> Path:
        self._validate_id(artifact_id)
        return self.root / f"{artifact_id}.data"

    def _metadata_path(self, artifact_id: str) -> Path:
        self._validate_id(artifact_id)
        return self.root / f"{artifact_id}.json"

    @staticmethod
    def _validate_id(artifact_id: str) -> None:
        if not artifact_id.startswith("art_") or not artifact_id[4:].isalnum():
            raise ValueError("invalid artifact id")

    @staticmethod
    def _atomic_write(path: Path, payload: bytes) -> None:
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_bytes(payload)
        temporary.replace(path)
