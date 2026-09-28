from server.gameserver.models import CompletedRound, Player, PlayerStatus, RoundStage
from server.gameserver.protocol import MAX_ACTION_LENGTH


class RoundError(ValueError):
    pass


class RoundManager:
    """Keep the small, in-memory state machine for one synchronous game."""

    def __init__(
        self, role_ids: tuple[str, ...] = ("P1", "P2"), round_number: int = 1
    ) -> None:
        if not role_ids:
            raise ValueError("at least one role is required")
        if len(set(role_ids)) != len(role_ids):
            raise ValueError("role ids must be unique")
        self.role_ids = tuple(role_ids)
        self.round_number = round_number
        self.stage = RoundStage.WAITING_INPUT
        self.players = {player_id: Player(player_id) for player_id in self.role_ids}

    def set_action(self, player_id: str, text: str) -> None:
        player = self._player(player_id)
        if player.status != PlayerStatus.EDITING:
            raise RoundError("只有编辑中的玩家可以修改行动")
        if len(text) > MAX_ACTION_LENGTH:
            raise RoundError("action_too_long")
        player.action = text

    def submit(self, player_id: str) -> CompletedRound | None:
        player = self._player(player_id)
        if player.status != PlayerStatus.EDITING:
            raise RoundError("只有处于编辑状态的玩家可以提交")
        if not player.action.strip():
            raise RoundError("提交前请先填写行动")
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
            raise RoundError("只有已提交的玩家可以取消提交")
        player.status = PlayerStatus.EDITING

    def pause(self, player_id: str) -> None:
        player = self._player(player_id)
        if player.status == PlayerStatus.PROCESSING:
            raise RoundError("本回合正在处理，暂时不能暂停")
        if player.status == PlayerStatus.PAUSED:
            raise RoundError("该玩家已经处于暂停状态")
        player.status = PlayerStatus.PAUSED

    def resume(self, player_id: str) -> None:
        player = self._player(player_id)
        if player.status != PlayerStatus.PAUSED:
            raise RoundError("只有暂停中的玩家可以恢复")
        player.status = PlayerStatus.EDITING

    def start_next_round(self) -> None:
        if not self.is_processing():
            raise RoundError("本回合当前没有在处理")
        self.round_number += 1
        self.stage = RoundStage.WAITING_INPUT
        for player in self.players.values():
            player.status = PlayerStatus.EDITING
            player.action = ""

    def is_processing(self) -> bool:
        return all(
            player.status == PlayerStatus.PROCESSING
            for player in self.players.values()
        )

    def set_stage(self, stage: RoundStage) -> None:
        self.stage = stage

    def begin_reprocess(self, completed: CompletedRound) -> None:
        """Enter PROCESSING for a full re-run of an already-locked round."""
        self.round_number = completed.round_number
        self.stage = RoundStage.WAITING_INPUT
        for player_id, player in self.players.items():
            player.action = completed.actions[player_id]
            player.status = PlayerStatus.PROCESSING

    def reset_to_round(self, round_number: int) -> None:
        """Jump to ``round_number`` with every role EDITING and no pending input."""
        self.round_number = round_number
        self.stage = RoundStage.WAITING_INPUT
        for player in self.players.values():
            player.status = PlayerStatus.EDITING
            player.action = ""

    def restore_waiting(self, players: dict) -> None:
        self.stage = RoundStage.WAITING_INPUT
        for player_id, saved in players.items():
            player = self._player(player_id)
            player.status = PlayerStatus(saved["status"])
            player.action = saved["action"]

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
            raise RoundError(f"未知玩家：{player_id}") from exc
