"""Simple snapshot-based undo/redo history."""
from __future__ import annotations

from core.project import Project


class History:
    def __init__(self, limit: int = 80):
        self.limit = limit
        self.undos: list[tuple[str, dict]] = []
        self.redos: list[tuple[str, dict]] = []

    def push(self, project: Project, label: str):
        self.undos.append((label, project.to_dict()))
        if len(self.undos) > self.limit:
            self.undos.pop(0)
        self.redos.clear()

    def can_undo(self) -> bool:
        return bool(self.undos)

    def can_redo(self) -> bool:
        return bool(self.redos)

    def undo(self, project: Project) -> Optional[str]:
        if not self.undos:
            return None
        label, state = self.undos.pop()
        self.redos.append((label, project.to_dict()))
        restored = Project.from_dict(state)
        project.restore_from(restored)
        return label

    def redo(self, project: Project) -> Optional[str]:
        if not self.redos:
            return None
        label, state = self.redos.pop()
        self.undos.append((label, project.to_dict()))
        restored = Project.from_dict(state)
        project.restore_from(restored)
        return label
