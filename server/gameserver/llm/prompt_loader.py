import json
from pathlib import Path

from server.gameserver.roles import RoleConfig
from server.gameserver.template import validate_template


class PromptLoader:
    """Read scenario text and JSON files without a template framework."""

    def __init__(
        self,
        scenario_dir: str = "templates/default",
        role_config: RoleConfig | None = None,
    ) -> None:
        self.root = Path(scenario_dir)
        self.roles = validate_template(self.root)
        if role_config is not None and self.roles.role_ids != role_config.role_ids:
            raise ValueError("Template roles do not match RoleConfig")
        self.role_ids = self.roles.role_ids

    def text(self, relative_path: str) -> str:
        return (self.root / relative_path).read_text(encoding="utf-8").strip()

    def json(self, relative_path: str) -> dict:
        return json.loads(self.text(relative_path))

    def character(self, role_id: str) -> str:
        index = self._role_index(role_id)
        return self.text(f"characters/{index}/character.md")

    def character_name(self, role_id: str) -> str:
        self._role_index(role_id)
        return self.roles.names[role_id]

    def opening(self, role_id: str) -> str:
        index = self._role_index(role_id)
        path = self.root / "characters" / str(index) / "opening.md"
        if not path.is_file():
            raise ValueError(f"opening file is missing for role {role_id}: {path}")
        return path.read_text(encoding="utf-8").strip()

    def character_view_schema(self, role_id: str) -> dict:
        index = self._role_index(role_id)
        return self.json(f"characters/{index}/character_view_schema.json")

    def character_status_schema(self, role_id: str) -> dict | None:
        index = self._role_index(role_id)
        path = f"characters/{index}/character_status_schema.json"
        return self.json(path) if (self.root / path).is_file() else None

    def character_status_initial(self, role_id: str) -> dict | None:
        index = self._role_index(role_id)
        path = f"characters/{index}/character_status_initial.json"
        return self.json(path) if (self.root / path).is_file() else None

    def _role_index(self, role_id: str) -> int:
        try:
            return self.role_ids.index(role_id) + 1
        except ValueError as exc:
            raise ValueError(f"unknown role: {role_id}") from exc
