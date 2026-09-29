import json
import re
import shutil
from pathlib import Path

from server.gameserver.roles import (
    LEGACY_ROLES_FILENAME, TemplateMetadata, validate_role_count,
    validate_role_limits, write_template_metadata,
)
from server.gameserver.template import validate_template


class ScenarioManager:
    BASE_TEMPLATE = "default"

    def __init__(
        self,
        templates_dir: str | Path = "templates",
        games_dir: str | Path = "games",
        min_role_count: int = 2,
        max_role_count: int = 4,
    ) -> None:
        validate_role_limits(min_role_count, max_role_count)
        self.templates_dir = Path(templates_dir)
        self.games_dir = Path(games_dir)
        self.min_role_count = min_role_count
        self.max_role_count = max_role_count

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

    def create_scenario(self, scenario_name: str, role_count: int) -> Path:
        self._validate_name(scenario_name)
        validate_role_count(role_count, self.min_role_count, self.max_role_count)
        if scenario_name == self.BASE_TEMPLATE:
            raise ValueError("default is the protected base template")
        source = self.templates_dir / self.BASE_TEMPLATE
        target = self.templates_dir / scenario_name
        if not source.is_dir():
            raise ValueError("base template is missing: default")
        if target.exists():
            raise ValueError(f"scenario already exists: {scenario_name}")
        shutil.copytree(source, target)
        self.scaffold_roles(target, role_count, title=scenario_name)
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
        validate_template(
            source,
            min_count=self.min_role_count,
            max_count=self.max_role_count,
        )
        self.games_dir.mkdir(parents=True, exist_ok=True)
        if target.exists():
            return target
        shutil.copytree(source, target)
        return target

    @staticmethod
    def scaffold_roles(target: Path, role_count: int, *, title: str = "") -> None:
        """Write a playable N-role payload into ``target`` from the base template.

        Shared by the CLI scenario command and the Platform Template create API so
        scaffolding never has two implementations. ``title`` becomes the payload's
        Script title."""
        ScenarioManager._scaffold_payload(target, role_count, title)

    @staticmethod
    def _scaffold_payload(target: Path, role_count: int, title: str = "") -> None:
        character_dir = target / "characters"
        base = character_dir / "1"
        if not base.is_dir():
            raise ValueError("base template is missing generic role files")
        character_text = (base / "character.md").read_text(encoding="utf-8")
        view_schema = (base / "character_view_schema.json").read_text(encoding="utf-8")
        status_schema = base / "character_status_schema.json"
        status_initial = base / "character_status_initial.json"
        status_schema_text = status_schema.read_text(encoding="utf-8") if status_schema.is_file() else None
        status_initial_text = status_initial.read_text(encoding="utf-8") if status_initial.is_file() else None
        for directory in character_dir.iterdir():
            if directory.is_dir():
                shutil.rmtree(directory)
        names = [f"角色{index}" for index in range(1, role_count + 1)]
        # A scaffolded payload only ever carries metadata.json; a legacy file
        # copied in from an old base template must not survive the scaffold.
        (target / LEGACY_ROLES_FILENAME).unlink(missing_ok=True)
        write_template_metadata(
            target,
            TemplateMetadata(count=role_count, names=tuple(names), title=title.strip()),
        )
        for index in range(1, role_count + 1):
            directory = character_dir / str(index)
            directory.mkdir()
            character = re.sub(r"(?m)^# .+$", f"# 角色 P{index}", character_text, count=1)
            character = re.sub(
                r"(?m)^姓名[：:].+$", f"姓名：{names[index - 1]}", character, count=1
            )
            (directory / "character.md").write_text(character, encoding="utf-8")
            (directory / "character_view_schema.json").write_text(view_schema, encoding="utf-8")
            (directory / "opening.md").write_text(
                f"这是角色{index}的开场白。请在这里描述角色当前看到、听到、知道的情况，"
                "以及第一轮行动所需的必要背景。\n",
                encoding="utf-8",
            )
            if status_schema_text is not None and status_initial_text is not None:
                (directory / "character_status_schema.json").write_text(status_schema_text, encoding="utf-8")
                (directory / "character_status_initial.json").write_text(status_initial_text, encoding="utf-8")
        (target / "world" / "world_state_initial.json").write_text(
            json.dumps({"world_information": "请在此填写剧本初始世界事实。"}, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        validate_template(target)

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
