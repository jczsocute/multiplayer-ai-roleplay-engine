COMMANDS = {
    "/submit": {"type": "submit"},
    "/cancel": {"type": "cancel_submit"},
    "/pause": {"type": "pause"},
    "/resume": {"type": "resume"},
    "/status": {"type": "status"},
}


def parse_input(text: str) -> dict:
    command = text.strip()
    parts = command.split()
    if len(parts) == 2 and parts[0] == "/view" and parts[1].upper() in ("A", "B"):
        return {"type": "view", "role": parts[1].upper()}
    if command in COMMANDS:
        return COMMANDS[command]
    return {"type": "action", "text": command}


def format_message(message: dict) -> str:
    message_type = message.get("type")
    if message_type == "joined":
        role = message.get("role")
        identity = f"Player {role}" if role else "等待 Host 分配角色"
        return f"昵称：{message['name']}\n{identity}"
    if message_type == "error":
        return f"Error: {message.get('detail', 'unknown error')}"
    if message_type == "round_complete":
        return f"Round {message['round']} completed."
    if message_type == "room_message":
        kind = message.get("kind")
        if kind == "system":
            prefix = "系统"
        elif kind == "host":
            prefix = "管理员"
        elif kind == "player":
            prefix = f"{message.get('sender')} ({message.get('character_name')})"
        else:
            prefix = str(message.get("sender", ""))
        return f"[{prefix}] {message.get('text', '')}"
    if message_type == "narration":
        prefix = "Last scene:\n\n" if message.get("last_scene") else ""
        return prefix + message.get("text", "")
    if message_type == "last_scene":
        return f"Last scene:\n\n{message.get('text', '')}"
    if message_type == "history":
        return "\n".join(
            f"[Round {item['round']}] {item['role']}: {item['content']}"
            for item in message.get("messages", [])
        ) or "No history."
    if message_type == "status":
        players = "\n".join(f"{key}: {value}" for key, value in message["players"].items())
        return (
            "====================\n\n"
            f"Scenario:\n{message['scenario']}\n\n"
            f"Role:\nPlayer {message['role']}\n\n"
            f"Current round:\n{message['round']}\n\n"
            f"Player status:\n{players}\n\n"
            f"Character statusbar:\n{message.get('statusbar', {})}\n\n"
            "===================="
        )
    if message_type == "state":
        players = message["players"]
        status = " | ".join(
            f"{player_id}: {data['status']}{' (action set)' if data['has_action'] else ''}"
            for player_id, data in players.items()
        )
        return f"Round {message['round']} | {status}"
    return str(message)
