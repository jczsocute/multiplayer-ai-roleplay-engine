import json
from pathlib import Path


class PromptLoader:
    """Read scenario text and JSON files without a template framework."""

    def __init__(self, scenario_dir: str = "templates/default") -> None:
        self.root = Path(scenario_dir)

    def text(self, relative_path: str) -> str:
        return (self.root / relative_path).read_text(encoding="utf-8").strip()

    def json(self, relative_path: str) -> dict:
        return json.loads(self.text(relative_path))

    def character(self, player_id: str) -> str:
        filename = {"A": "player_a.md", "B": "player_b.md"}.get(player_id)
        if filename is None:
            raise ValueError(f"unknown player: {player_id}")
        return self.text(f"characters/{filename}")

    def character_name(self, player_id: str) -> str:
        for line in self.character(player_id).splitlines():
            stripped = line.strip()
            for prefix in ("姓名：", "姓名:", "Name:"):
                if stripped.startswith(prefix):
                    name = stripped[len(prefix):].strip()
                    if name:
                        return name
        return f"Player {player_id}"

    def statusbar(self, player_id: str) -> dict:
        filename = {"A": "player_a.json", "B": "player_b.json"}.get(player_id)
        if filename is None:
            raise ValueError(f"unknown player: {player_id}")
        return self.json(f"characters/statusbar/{filename}")
