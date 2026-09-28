import json
from pathlib import Path

from server.gameserver.roles import RoleConfig


class PromptLoader:
    """Read scenario text and JSON files without a template framework."""

    def __init__(
        self,
        scenario_dir: str = "templates/default",
        role_config: RoleConfig | None = None,
    ) -> None:
        self.root = Path(scenario_dir)
        self.roles = role_config or RoleConfig.load(self.root)
        self.role_ids = self.roles.role_ids

    def text(self, relative_path: str) -> str:
        return (self.root / relative_path).read_text(encoding="utf-8").strip()

    def json(self, relative_path: str) -> dict:
        return json.loads(self.text(relative_path))

    def character(self, role_id: str) -> str:
        index = self._role_index(role_id)
        return self.text(f"characters/player_{index}.md")

    def character_name(self, role_id: str) -> str:
        self._role_index(role_id)
        return self.roles.names[role_id]

    def opening(self, role_id: str) -> str:
        index = self._role_index(role_id)
        path = self.root / "characters" / "opening" / f"player_{index}.md"
        if not path.is_file():
            raise ValueError(f"opening file is missing for role {role_id}: {path}")
        return path.read_text(encoding="utf-8").strip()

    def statusbar(self, role_id: str) -> dict:
        index = self._role_index(role_id)
        return self.json(f"characters/statusbar/player_{index}.json")

    def _role_index(self, role_id: str) -> int:
        try:
            return self.role_ids.index(role_id) + 1
        except ValueError as exc:
            raise ValueError(f"unknown role: {role_id}") from exc
