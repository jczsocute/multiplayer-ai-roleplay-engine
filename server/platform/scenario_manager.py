import copy
import json
import re
import shutil
from pathlib import Path

from server.gameserver.roles import (
    LEGACY_ROLES_FILENAME, RoleConfig, TemplateMetadata, validate_role_count,
    validate_role_limits, write_template_metadata,
)


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
        RoleConfig.load(
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
        opening_dir = character_dir / "opening"
        statusbar_dir = character_dir / "statusbar"
        character_templates = sorted(character_dir.glob("player_*.md"))
        statusbar_templates = sorted(statusbar_dir.glob("player_*.json"))
        character_texts = [path.read_text(encoding="utf-8") for path in character_templates]
        statusbar_values = [
            json.loads(path.read_text(encoding="utf-8")) for path in statusbar_templates
        ]
        if not character_texts or not statusbar_values:
            raise ValueError("base template is missing generic role files")
        opening_dir.mkdir(parents=True, exist_ok=True)
        opening_templates = sorted(opening_dir.glob("player_*.md"))
        for path in character_templates + opening_templates + statusbar_templates:
            path.unlink()

        names = [f"角色{index}" for index in range(1, role_count + 1)]
        # A scaffolded payload only ever carries metadata.json; a legacy file
        # copied in from an old base template must not survive the scaffold.
        (target / LEGACY_ROLES_FILENAME).unlink(missing_ok=True)
        write_template_metadata(
            target,
            TemplateMetadata(count=role_count, names=tuple(names), title=title.strip()),
        )
        for index in range(1, role_count + 1):
            character = character_texts[min(index - 1, len(character_texts) - 1)]
            character = re.sub(
                r"(?m)^# .+$", f"# 角色 P{index}", character, count=1
            )
            character = re.sub(
                r"(?m)^姓名[：:].+$", f"姓名：{names[index - 1]}", character, count=1
            )
            character_dir.joinpath(f"player_{index}.md").write_text(
                character, encoding="utf-8"
            )
            opening_dir.joinpath(f"player_{index}.md").write_text(
                f"这是角色{index}的开场白。请在这里描述角色当前看到、听到、知道的情况，"
                "以及第一轮行动所需的必要背景。\n",
                encoding="utf-8",
            )
            statusbar = statusbar_values[min(index - 1, len(statusbar_values) - 1)]
            statusbar_dir.joinpath(f"player_{index}.json").write_text(
                json.dumps(statusbar, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )

        state_path = target / "world" / "initial_state.json"
        state = json.loads(state_path.read_text(encoding="utf-8"))
        existing = list((state.get("characters") or {}).values())
        base = existing or [{"name": "", "relationships": {}}]
        characters = {}
        for index, name in enumerate(names, 1):
            value = copy.deepcopy(base[min(index - 1, len(base) - 1)])
            value["name"] = name
            value["relationships"] = {}
            characters[f"P{index}"] = value
        state["characters"] = characters
        state_path.write_text(
            json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )

        schema_path = target / "schemas" / "world_updater_output.json"
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        schema["player_views"] = {f"P{index}": {} for index in range(1, role_count + 1)}
        schema["player_statusbar"] = {
            f"P{index}": {} for index in range(1, role_count + 1)
        }
        schema_path.write_text(
            json.dumps(schema, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )

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
