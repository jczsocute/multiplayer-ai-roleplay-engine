from server.models import CompletedRound, Player, PlayerStatus, RoundStage
from server.protocol import MAX_ACTION_LENGTH


class RoundError(ValueError):
    pass


class RoundManager:
    """Keep the small, in-memory state machine for one two-player game."""

    def __init__(self, round_number: int = 1) -> None:
        self.round_number = round_number
        self.stage = RoundStage.WAITING_INPUT
        self.players = {player_id: Player(player_id) for player_id in ("A", "B")}

    def set_action(self, player_id: str, text: str) -> None:
        player = self._player(player_id)
        if player.status != PlayerStatus.EDITING:
            raise RoundError("actions can only be edited while EDITING")
        if len(text) > MAX_ACTION_LENGTH:
            raise RoundError(
                f"action is too long (maximum {MAX_ACTION_LENGTH} characters)"
            )
        player.action = text

    def enter_lobby(self) -> None:
        for player in self.players.values():
            player.status = PlayerStatus.LOBBY
            player.action = ""

    def activate_lobby(self) -> None:
        if not all(
            player.status == PlayerStatus.LOBBY for player in self.players.values()
        ):
            raise RoundError("roles can only be assigned while players are in LOBBY")
        for player in self.players.values():
            player.status = PlayerStatus.EDITING

    def submit(self, player_id: str) -> CompletedRound | None:
        player = self._player(player_id)
        if player.status != PlayerStatus.EDITING:
            raise RoundError("only an EDITING player can submit")
        if not player.action.strip():
            raise RoundError("enter an action before submitting")
        player.status = PlayerStatus.READY

        if all(p.status == PlayerStatus.READY for p in self.players.values()):
            for current in self.players.values():
                current.status = PlayerStatus.PROCESSING
            return CompletedRound(
                round_number=self.round_number,
                actions={key: value.action for key, value in self.players.items()},
            )
        return None

    def cancel_submit(self, player_id: str) -> None:
        player = self._player(player_id)
        if player.status != PlayerStatus.READY:
            raise RoundError("only a READY player can cancel submission")
        player.status = PlayerStatus.EDITING

    def pause(self, player_id: str) -> None:
        player = self._player(player_id)
        if player.status == PlayerStatus.PROCESSING:
            raise RoundError("cannot pause while a round is processing")
        if player.status == PlayerStatus.PAUSED:
            raise RoundError("player is already paused")
        player.status = PlayerStatus.PAUSED

    def resume(self, player_id: str) -> None:
        player = self._player(player_id)
        if player.status != PlayerStatus.PAUSED:
            raise RoundError("only a PAUSED player can resume")
        player.status = PlayerStatus.EDITING

    def start_next_round(self) -> None:
        if not self.is_processing():
            raise RoundError("the current round is not processing")
        self.round_number += 1
        self.stage = RoundStage.WAITING_INPUT
        for player in self.players.values():
            player.status = PlayerStatus.EDITING
            player.action = ""

    def abort_processing(self) -> None:
        if not self.is_processing():
            raise RoundError("the current round is not processing")
        for player in self.players.values():
            player.status = PlayerStatus.EDITING

    def is_processing(self) -> bool:
        return all(
            player.status == PlayerStatus.PROCESSING
            for player in self.players.values()
        )

    def set_stage(self, stage: RoundStage) -> None:
        self.stage = stage

    def restore_processing(self, completed: CompletedRound, stage: RoundStage) -> None:
        self.round_number = completed.round_number
        self.stage = stage
        for player_id, player in self.players.items():
            player.action = completed.actions[player_id]
            player.status = PlayerStatus.PROCESSING

    def restore_waiting(self, players: dict) -> None:
        self.stage = RoundStage.WAITING_INPUT
        for player_id, saved in players.items():
            player = self._player(player_id)
            player.status = PlayerStatus(saved["status"])
            player.action = saved["action"]
            player.last_ack_round = saved.get("last_ack_round", 0)

    def snapshot(self) -> dict:
        return {
            "type": "state",
            "round": self.round_number,
            "stage": self.stage.value,
            "players": {
                player_id: {
                    "status": player.status.value,
                    "has_action": bool(player.action),
                }
                for player_id, player in self.players.items()
            },
        }

    def _player(self, player_id: str) -> Player:
        try:
            return self.players[player_id]
        except KeyError as exc:
            raise RoundError(f"unknown player: {player_id}") from exc
