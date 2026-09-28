import json
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class RoleConfig:
    role_ids: tuple[str, ...]
    names: dict[str, str]

    def __post_init__(self) -> None:
        expected = tuple(f"P{index}" for index in range(1, len(self.role_ids) + 1))
        if not self.role_ids or self.role_ids != expected:
            raise ValueError("role ids must be the ordered sequence P1...PN")
        if set(self.names) != set(self.role_ids) or not all(
            isinstance(name, str) and name.strip() for name in self.names.values()
        ):
            raise ValueError("role names must contain one non-empty name for every role")

    @classmethod
    def from_data(
        cls,
        data: object,
        *,
        min_count: int | None = None,
        max_count: int | None = None,
    ) -> "RoleConfig":
        if not isinstance(data, dict):
            raise ValueError("roles.json must contain a JSON object")
        count = data.get("count")
        names = data.get("names")
        if isinstance(count, bool) or not isinstance(count, int) or count < 1:
            raise ValueError("roles.json count must be a positive integer")
        if not isinstance(names, list) or len(names) != count:
            raise ValueError("roles.json names length must equal count")
        if not all(isinstance(name, str) and name.strip() for name in names):
            raise ValueError("every role name must be a non-empty string")
        validate_role_count(count, min_count, max_count)
        role_ids = tuple(f"P{index}" for index in range(1, count + 1))
        return cls(role_ids, dict(zip(role_ids, (name.strip() for name in names), strict=True)))

    @classmethod
    def load(
        cls,
        path: str | Path,
        *,
        min_count: int | None = None,
        max_count: int | None = None,
    ) -> "RoleConfig":
        try:
            data = json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"cannot read roles.json: {exc}") from exc
        return cls.from_data(data, min_count=min_count, max_count=max_count)

    @property
    def definitions(self) -> list[dict[str, str]]:
        return [{"id": role_id, "name": self.names[role_id]} for role_id in self.role_ids]


def validate_role_limits(min_count: int, max_count: int) -> None:
    if min_count < 1:
        raise ValueError("MIN_ROLE_COUNT must be at least 1")
    if max_count < min_count:
        raise ValueError("MAX_ROLE_COUNT must be greater than or equal to MIN_ROLE_COUNT")


def validate_role_count(
    count: int, min_count: int | None, max_count: int | None
) -> None:
    if isinstance(count, bool) or not isinstance(count, int) or count < 1:
        raise ValueError("role count must be a positive integer")
    if min_count is not None and count < min_count:
        raise ValueError(f"role count must be at least {min_count}")
    if max_count is not None and count > max_count:
        raise ValueError(f"role count must be at most {max_count}")
