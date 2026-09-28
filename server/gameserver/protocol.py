PROTOCOL_VERSION = 6

MAX_USERNAME_LENGTH = 32
MAX_ROOM_CHAT_LENGTH = 4_000
MAX_ACTION_LENGTH = 20_000

# Large enough for a 20,000-character UTF-8 action plus JSON framing, while still
# placing a firm ceiling on messages accepted by either WebSocket transport.
MAX_WEBSOCKET_MESSAGE_BYTES = 128 * 1024
