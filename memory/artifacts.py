from pathlib import Path
from typing import Dict, Any, List
import json
from core.protocol import Artifact

WORKSPACE_ROOT = Path(__file__).resolve().parent.parent

class ArtifactMemory:
    """
    Saves and manages versioned artifacts to local workspace.
    """
    def __init__(self):
        self.artifacts_dir = WORKSPACE_ROOT / ".sai" / "artifacts"
        self.artifacts_dir.mkdir(parents=True, exist_ok=True)

    def save(self, artifact: Artifact) -> None:
        file_path = self.artifacts_dir / f"{artifact.id}.json"
        try:
            data = {
                "id": artifact.id,
                "kind": artifact.kind.value,
                "title": artifact.title,
                "author": artifact.author,
                "created": artifact.created.isoformat(),
                "content": artifact.content,
                "path": artifact.path,
                "version": artifact.version
            }
            file_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        except Exception:
            pass

    def list_artifacts(self) -> List[Dict[str, Any]]:
        artifacts_list = []
        for f in self.artifacts_dir.glob("*.json"):
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                artifacts_list.append(data)
            except Exception:
                pass
        return artifacts_list
