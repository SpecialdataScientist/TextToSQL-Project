from contextlib import contextmanager
import fcntl
import json
import os
from pathlib import Path
import shutil
from tempfile import mkdtemp
from uuid import uuid4

import faiss

from app.schemas.request import SCHEMA_TABLES, validate_unique_tables


class IndexConflictError(ValueError):
    """An existing snapshot cannot safely be extended."""


class SchemaIndexStore:
    def __init__(self, root: Path):
        self.root = root
        self.directory = root / "db_schema"

    @contextmanager
    def locked(self, *, shared=False):
        """Coordinate snapshot readers and writers without creating storage on reads."""
        if not shared:
            self.root.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_SH if shared else fcntl.LOCK_EX)
            yield
        finally:
            os.close(descriptor)

    def load(self, embedding_model: str):
        """Call while holding locked(); an absent snapshot is a fresh index."""
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
            tables = SCHEMA_TABLES.validate_json((self.directory / "raw_data.json").read_bytes())
            validate_unique_tables(tables)
            self._validate_snapshot(index, metadata, tables, embedding_model)
            records = [table.model_dump(mode="json", by_alias=True) for table in tables]
            return index, records, metadata
        except Exception as error:
            raise IndexConflictError(f"Existing schema index is incompatible or damaged: {error}") from error

    @staticmethod
    def _validate_snapshot(index, metadata, tables, embedding_model):
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
        if not isinstance(items, list) or len(items) != index.ntotal or len(tables) != index.ntotal:
            raise ValueError("Index, metadata and raw-data counts differ")
        for row, (item, table) in enumerate(zip(items, tables)):
            if (not isinstance(item, dict) or type(item.get("index")) is not int
                    or item["index"] != row or item.get("table_name") != table.table_name):
                raise ValueError("Metadata rows must match the original table order")

    @staticmethod
    def _write_json(path: Path, value):
        with path.open("w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.write("\n")

    def save(self, index, records, embedding_model: str):
        """Publish all three files together under the storage lock, with rollback."""
        metadata = {
            "embedding_model": embedding_model,
            "dimension": index.d,
            "metric": "cosine",
            "items": [
                {"index": row, "table_name": record["table_name"]}
                for row, record in enumerate(records)
            ],
        }
        staging = Path(mkdtemp(prefix=".schema-staging-", dir=self.root))
        backup = self.root / f".schema-backup-{uuid4().hex}"
        try:
            faiss.write_index(index, str(staging / "index.faiss"))
            self._write_json(staging / "metadata.json", metadata)
            self._write_json(staging / "raw_data.json", records)
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
