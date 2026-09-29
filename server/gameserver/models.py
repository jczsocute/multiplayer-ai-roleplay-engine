from dataclasses import dataclass
from enum import StrEnum


class PlayerStatus(StrEnum):
    EDITING = "EDITING"
    READY = "READY"
    PAUSED = "PAUSED"
    PROCESSING = "PROCESSING"


class RoundStage(StrEnum):
    WAITING_INPUT = "WAITING_INPUT"
    WORLD_UPDATING = "WORLD_UPDATING"
    WORLD_DONE = "WORLD_DONE"
    # Old game.db rows may contain these stages; recovery reruns the whole round.
    VIEW_GENERATING = "VIEW_GENERATING"
    VIEW_DONE = "VIEW_DONE"
    NARRATION_GENERATING = "NARRATION_GENERATING"
    FINISHED = "FINISHED"


@dataclass
class Player:
    player_id: str
    status: PlayerStatus = PlayerStatus.EDITING
    action: str = ""


@dataclass(frozen=True)
class CompletedRound:
    round_number: int
    actions: dict[str, str]
