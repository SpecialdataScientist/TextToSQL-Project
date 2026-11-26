from contextlib import contextmanager
import fcntl
import json
import os
from pathlib import Path
import shutil
from tempfile import mkdtemp
from uuid import uuid4

import faiss

from app.schemas.triplet_index import INDEXED_TRIPLETS
from app.services.schema_index_store import IndexConflictError


class TripletIndexStore:
    def __init__(self, root: Path):
        self.root = root
        self.directory = root / "triplets"

    @contextmanager
    def locked(self, *, shared=False):
        """Hold across loading, selecting additions, embedding and publishing."""
        if not shared:
            self.root.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_SH if shared else fcntl.LOCK_EX)
            yield
        finally:
            os.close(descriptor)

    def load(self, embedding_model: str):
        """Load and validate a complete snapshot while holding locked()."""
        try:
            if self.directory.is_symlink():
                raise ValueError("The index directory must not be a symbolic link")
            if not self.directory.exists():
                return None, [], None
            for name in ("index.faiss", "metadata.json", "raw_data.json"):
                path = self.directory / name
                if path.is_symlink() or not path.is_file():
                    raise ValueError(f"Missing or invalid index file: {name}")
            index = faiss.read_index(str(self.directory / "index.faiss"))
            metadata = json.loads((self.directory / "metadata.json").read_text(encoding="utf-8"))
            triplets = INDEXED_TRIPLETS.validate_json((self.directory / "raw_data.json").read_bytes())
            self._validate_snapshot(index, metadata, triplets, embedding_model)
            return index, [triplet.model_dump(mode="json") for triplet in triplets], metadata
        except Exception as error:
            raise IndexConflictError(f"Existing triplet index is incompatible or damaged: {error}") from error

    @staticmethod
    def _validate_snapshot(index, metadata, triplets, embedding_model):
        if not isinstance(index, faiss.IndexFlatIP) or index.d < 1 or not index.is_trained:
            raise ValueError("Expected a trained FAISS inner-product flat index")
        if not isinstance(metadata, dict):
            raise ValueError("Index metadata must be an object")
        if metadata.get("embedding_model") != embedding_model:
            raise ValueError("The configured embedding model differs from the stored model")
        if (type(metadata.get("dimension")) is not int or metadata["dimension"] != index.d
                or metadata.get("metric") != "cosine"):
            raise ValueError("Index dimension or metric differs from metadata")
        items = metadata.get("items")
        if not isinstance(items, list) or len(items) != index.ntotal or len(triplets) != index.ntotal:
            raise ValueError("Index, metadata and raw-data counts differ")
        ids = [triplet.triplet_id for triplet in triplets]
        if len(ids) != len(set(ids)):
            raise ValueError("Duplicate triplet IDs are not allowed")
        for row, (item, triplet) in enumerate(zip(items, triplets)):
            if (not isinstance(item, dict) or type(item.get("index")) is not int
                    or item["index"] != row or item.get("triplet_id") != triplet.triplet_id):
                raise ValueError("Metadata rows must match the original triplet order")

    @staticmethod
    def _write_json(path: Path, value):
        with path.open("w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.write("\n")

    def save(self, index, records, embedding_model: str):
        """Publish the three validated files together under the storage lock, with rollback."""
        triplets = INDEXED_TRIPLETS.validate_python(records)
        metadata = {
            "embedding_model": embedding_model,
            "dimension": index.d,
            "metric": "cosine",
            "items": [{"index": row, "triplet_id": triplet.triplet_id} for row, triplet in enumerate(triplets)],
        }
        self._validate_snapshot(index, metadata, triplets, embedding_model)
        staging = Path(mkdtemp(prefix=".triplet-staging-", dir=self.root))
        backup = self.root / f".triplet-backup-{uuid4().hex}"
        try:
            faiss.write_index(index, str(staging / "index.faiss"))
            self._write_json(staging / "metadata.json", metadata)
            self._write_json(staging / "raw_data.json", [triplet.model_dump(mode="json") for triplet in triplets])
            if self.directory.exists():
                self.directory.rename(backup)
            try:
                staging.rename(self.directory)
            except BaseException:
                if backup.exists():
                    backup.rename(self.directory)
                raise
            if backup.exists():
                shutil.rmtree(backup, ignore_errors=True)
        finally:
            shutil.rmtree(staging, ignore_errors=True)
        return metadata
