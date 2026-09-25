import re
import shutil
from pathlib import Path


class ScenarioManager:
    BASE_TEMPLATE = "default"

    def __init__(
        self,
        templates_dir: str | Path = "templates",
        games_dir: str | Path = "games",
    ) -> None:
        self.templates_dir = Path(templates_dir)
        self.games_dir = Path(games_dir)

    def list_scenarios(self) -> list[str]:
        if not self.templates_dir.exists():
            return []
        return sorted(
            path.name
            for path in self.templates_dir.iterdir()
            if path.is_dir() and path.name != self.BASE_TEMPLATE
        )

    def list_templates(self) -> list[str]:
        """Compatibility name for callers that need playable scenarios."""
        return self.list_scenarios()

    def create_scenario(self, scenario_name: str) -> Path:
        self._validate_name(scenario_name)
        if scenario_name == self.BASE_TEMPLATE:
            raise ValueError("default is the protected base template")
        source = self.templates_dir / self.BASE_TEMPLATE
        target = self.templates_dir / scenario_name
        if not source.is_dir():
            raise ValueError("base template is missing: default")
        if target.exists():
            raise ValueError(f"scenario already exists: {scenario_name}")
        shutil.copytree(source, target)
        return target

    def delete_scenario(self, scenario_name: str) -> None:
        self._validate_name(scenario_name)
        if scenario_name == self.BASE_TEMPLATE:
            raise ValueError("default is the protected base template")
        target = self.templates_dir / scenario_name
        if not target.is_dir():
            raise ValueError(f"unknown scenario: {scenario_name}")
        shutil.rmtree(target)

    def list_games(self) -> list[str]:
        if not self.games_dir.exists():
            return []
        return sorted(path.name for path in self.games_dir.iterdir() if path.is_dir())

    def create_game(self, template_name: str, game_name: str) -> Path:
        self._validate_name(template_name)
        self._validate_name(game_name)
        if template_name == self.BASE_TEMPLATE:
            raise ValueError("default is not a playable scenario")
        source = self.templates_dir / template_name
        target = self.games_dir / game_name
        if not source.is_dir():
            raise ValueError(f"unknown template: {template_name}")
        self.games_dir.mkdir(parents=True, exist_ok=True)
        if target.exists():
            return target
        shutil.copytree(source, target)
        return target

    def delete_game(self, game_name: str) -> None:
        self._validate_name(game_name)
        target = self.games_dir / game_name
        if not target.is_dir():
            raise ValueError(f"unknown game: {game_name}")
        shutil.rmtree(target)

    @staticmethod
    def _validate_name(name: str) -> None:
        if not re.fullmatch(r"[A-Za-z0-9_-]+", name):
            raise ValueError("names may contain only letters, numbers, _ and -")
