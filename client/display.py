import yaml
from rich.text import Text


def to_yaml(value) -> str:
    """Render structured server data for terminal display only."""
    if value is None or value == {} or value == [] or value == "":
        return "暂无"
    if isinstance(value, str):
        return value
    return yaml.safe_dump(
        value,
        allow_unicode=True,
        sort_keys=False,
        default_flow_style=False,
    ).strip()


def room_message_text(message: dict) -> Text:
    """Render every Room Plane message through one protocol-aware path."""
    kind = message.get("kind")
    sender = message.get("sender") or ""
    character_name = message.get("character_name")
    text = str(message.get("text", ""))
    if kind == "system":
        return Text(f"[系统] {text}", style="dim cyan")
    result = Text()
    if kind == "host":
        result.append("[管理员]", style="bold magenta")
    elif kind == "player":
        suffix = f" ({character_name})" if character_name else ""
        result.append(f"[{sender}{suffix}]", style="bold cyan")
    else:
        result.append(f"[{sender}]", style="green")
    result.append(f" {text}")
    return result
