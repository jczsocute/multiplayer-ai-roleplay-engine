from dataclasses import dataclass
from enum import StrEnum


class PlayerStatus(StrEnum):
    LOBBY = "LOBBY"
    EDITING = "EDITING"
    READY = "READY"
    PAUSED = "PAUSED"
    PROCESSING = "PROCESSING"


class RoundStage(StrEnum):
    WAITING_INPUT = "WAITING_INPUT"
    WORLD_UPDATING = "WORLD_UPDATING"
    WORLD_DONE = "WORLD_DONE"
    VIEW_GENERATING = "VIEW_GENERATING"
    VIEW_DONE = "VIEW_DONE"
    NARRATION_GENERATING = "NARRATION_GENERATING"
    FINISHED = "FINISHED"


@dataclass
class Player:
    player_id: str
    status: PlayerStatus = PlayerStatus.EDITING
    action: str = ""
    last_ack_round: int = 0


@dataclass(frozen=True)
class CompletedRound:
    round_number: int
    actions: dict[str, str]
