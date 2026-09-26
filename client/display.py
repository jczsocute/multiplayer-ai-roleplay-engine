import yaml


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
