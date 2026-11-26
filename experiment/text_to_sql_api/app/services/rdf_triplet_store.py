import json
import os
from pathlib import Path
from tempfile import NamedTemporaryFile

from app.schemas.triplet import RDF_TRIPLETS, RdfTriplet


class RdfTripletStore:
    def __init__(self, root: Path):
        self.root = root
        self.path = root / "triplets.json"

    def load(self) -> list[RdfTriplet]:
        """Read one complete snapshot without creating directories or files."""
        return RDF_TRIPLETS.validate_json(self.path.read_bytes())

    def save(self, triplets: list[RdfTriplet]) -> None:
        """Publish a complete JSON snapshot; concurrent writers use last-completion-wins."""
        validated = RDF_TRIPLETS.validate_python(triplets)
        payload = RDF_TRIPLETS.dump_python(validated, mode="json")
        self.root.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            with NamedTemporaryFile(mode="w", encoding="utf-8", dir=self.root,
                                    prefix=".triplets-", suffix=".tmp", delete=False) as stream:
                temporary = Path(stream.name)
                json.dump(payload, stream, ensure_ascii=False, indent=2, allow_nan=False)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
