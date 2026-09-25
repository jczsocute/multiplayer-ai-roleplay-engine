import asyncio
import argparse
import json
import logging
from pathlib import Path

from websockets.asyncio.server import ServerConnection, serve
from websockets.exceptions import ConnectionClosed

from server.config import load_settings
from server.database import Database
from server.llm.client import LLMClient
from server.llm.narrator import Narrator
from server.llm.player_view import PlayerViewGenerator
from server.llm.prompt_loader import PromptLoader
from server.llm.world_update import WorldUpdater
from server.models import CompletedRound, RoundStage
from server.round_manager import RoundError, RoundManager
from server.scenario_manager import ScenarioManager
from server.session import Sessions

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


class GameServer:
    def __init__(
        self,
        database: Database,
        world_updater: WorldUpdater,
        player_views: PlayerViewGenerator,
        narrator: Narrator,
        round_number: int = 1,
        scenario_name: str = "default",
        participants: list[dict] | None = None,
    ) -> None:
        self.database = database
        self.world_updater = world_updater
        self.player_views = player_views
        self.narrator = narrator
        self.rounds = RoundManager(round_number)
        self.sessions = Sessions(participants)
        if participants is not None and not self.sessions.roles_assigned:
            self.rounds.enter_lobby()
        self.command_lock = asyncio.Lock()
        self.scenario_name = scenario_name

    async def handler(self, websocket: ServerConnection) -> None:
        participant_name: str | None = None
        try:
            registration = await asyncio.wait_for(websocket.recv(), timeout=15)
            message = self._decode(registration)
            if message.get("type") != "join":
                await self._send_error(websocket, "first message must be join")
                await websocket.close(code=1008)
                return

            joined = await self.sessions.join(str(message.get("name", "")), websocket)
            if joined is None:
                await self._send_error(websocket, "game already has two connected players")
                await websocket.close(code=1008)
                return
            participant, is_new = joined
            participant_name = participant.name
            if is_new:
                await self.database.register_participant(
                    participant.name, participant.is_host
                )

            logger.info("Participant %s connected", participant_name)
            await websocket.send(json.dumps({
                "type": "joined",
                "name": participant.name,
                "is_host": participant.is_host,
                "role": participant.role,
                "scenario": self.scenario_name,
            }, ensure_ascii=False))
            await self.sessions.broadcast(await self.sessions.lobby_snapshot())
            await self.sessions.broadcast(await self._round_snapshot())
            if participant.role:
                await self._send_history(participant.role, websocket)
                replayed = await self._replay_narrations(participant.role, websocket)
                if replayed == 0:
                    latest = await self.database.get_latest_narration(participant.role)
                    if latest:
                        display = await self.database.get_player_display(participant.role)
                        await websocket.send(json.dumps({
                            "type": "last_scene", **latest, **display
                        }, ensure_ascii=False))
                await self._send_status(participant_name, websocket)
                if not is_new:
                    await self.sessions.broadcast({
                        "type": "system",
                        "event": "reconnected",
                        "name": participant.name,
                        "text": f"{participant.name} 已重新连接。",
                    })

            async for raw_message in websocket:
                await self._handle_command(participant_name, websocket, raw_message)
        except (ConnectionClosed, asyncio.TimeoutError):
            pass
        except (ValueError, json.JSONDecodeError) as exc:
            await self._send_error(websocket, str(exc))
        finally:
            if participant_name is not None:
                participant = self.sessions.participants[participant_name]
                await self.sessions.remove(participant_name, websocket)
                logger.info("Participant %s disconnected", participant_name)
                await self.sessions.broadcast(await self.sessions.lobby_snapshot())
                await self.sessions.broadcast(await self._round_snapshot())
                if participant.role:
                    await self.sessions.broadcast({
                        "type": "system",
                        "event": "left",
                        "name": participant.name,
                        "text": f"{participant.name} 已离开。",
                    })

    async def _handle_command(
        self, participant_name: str, websocket: ServerConnection, raw_message: str
    ) -> None:
        try:
            message = self._decode(raw_message)
            command = message.get("type")
            completed = None
            async with self.command_lock:
                if command == "assign_roles":
                    assignments = await self.sessions.assign_roles(
                        participant_name, str(message.get("host_role", ""))
                    )
                    await self.database.save_role_assignment(assignments)
                    self.rounds.activate_lobby()
                    for role, player in self.rounds.players.items():
                        await self.database.save_player(role, player.status, player.action)
                    await self.sessions.broadcast(await self.sessions.lobby_snapshot())
                    await self.sessions.broadcast({
                        "type": "role_assigned", "assignments": assignments
                    })
                    await self.sessions.broadcast(await self._round_snapshot())
                    return

                player_id = await self.sessions.role_for(participant_name)
                if player_id is None:
                    raise RoundError("wait for the host to assign Player A and Player B")
                if command == "history":
                    await websocket.send(json.dumps({
                        "type": "notice",
                        "text": "历史记录可直接在上方区域滚动查看。",
                    }, ensure_ascii=False))
                    return
                if command == "status":
                    await self._send_status(participant_name, websocket)
                    return
                if command == "ack":
                    round_id = message.get("round_id")
                    if isinstance(round_id, bool) or not isinstance(round_id, int) or round_id < 1:
                        raise RoundError("ack round_id must be a positive integer")
                    if not await self.database.acknowledge_narration(player_id, round_id):
                        raise RoundError("cannot acknowledge an unknown narration")
                    self.rounds.players[player_id].last_ack_round = max(
                        self.rounds.players[player_id].last_ack_round, round_id
                    )
                    return
                if command in ("retry_views", "retry_narration"):
                    if not self.rounds.is_processing():
                        raise RoundError("there is no AI stage to retry")
                    await self.recover_round()
                    return
                if command == "action":
                    self.rounds.set_action(player_id, str(message.get("text", "")))
                elif command == "submit":
                    completed = self.rounds.submit(player_id)
                elif command == "cancel_submit":
                    self.rounds.cancel_submit(player_id)
                elif command == "pause":
                    self.rounds.pause(player_id)
                elif command == "resume":
                    self.rounds.resume(player_id)
                else:
                    raise RoundError(f"unknown command: {command}")

                player = self.rounds.players[player_id]
                await self.database.save_player(player_id, player.status, player.action)
                if command != "action":
                    await self.sessions.broadcast(await self._round_snapshot())

                if completed is not None:
                    for current_id in ("A", "B"):
                        current = self.rounds.players[current_id]
                        await self.database.save_player(current_id, current.status, current.action)
                    await self._process_round(completed)
        except (RoundError, ValueError, json.JSONDecodeError) as exc:
            await self._send_error(websocket, str(exc))

    async def _process_round(self, completed: CompletedRound) -> None:
        try:
            await self._set_stage(RoundStage.WORLD_UPDATING)
            current_world_state = await self.database.get_world_state()
            world_result = await self.world_updater.update(
                current_world_state=current_world_state,
                action_a=completed.actions["A"],
                action_b=completed.actions["B"],
            )
            await self.database.save_world_update(completed, world_result)
            self.rounds.set_stage(RoundStage.WORLD_DONE)
        except Exception:
            logger.exception("World update failed for round %s", completed.round_number)
            self.rounds.abort_processing()
            await self._set_stage(RoundStage.WAITING_INPUT)
            for player_id, player in self.rounds.players.items():
                await self.database.save_player(player_id, player.status, player.action)
            await self.sessions.broadcast(
                {"type": "error", "detail": "world update failed; actions can be edited and resubmitted"}
            )
            await self.sessions.broadcast(await self._round_snapshot())
            return

        await self._continue_views(completed)

    async def _continue_views(self, completed: CompletedRound) -> None:
        await self._set_stage(RoundStage.VIEW_GENERATING)
        data = await self.database.get_recovery_data(completed.round_number)
        missing = [player_id for player_id in ("A", "B") if player_id not in data["player_views"]]
        if missing:
            results = await asyncio.gather(
                *(self.player_views.generate(player_id, data["world_state"]) for player_id in missing),
                return_exceptions=True,
            )
            views = {
                player_id: result
                for player_id, result in zip(missing, results, strict=True)
                if isinstance(result, str)
            }
            if views:
                await self.database.save_player_views(completed.round_number, views)
            if len(views) != len(missing):
                logger.error("Player view recovery failed for round %s", completed.round_number)
                await self.sessions.broadcast(
                    {"type": "error", "detail": "player view generation failed; use /retry"}
                )
                return
        await self._set_stage(RoundStage.VIEW_DONE)
        await self._continue_narrations(completed)

    async def _continue_narrations(self, completed: CompletedRound) -> None:
        await self._set_stage(RoundStage.NARRATION_GENERATING)
        data = await self.database.get_recovery_data(completed.round_number)
        missing = [player_id for player_id in ("A", "B") if player_id not in data["narrations"]]
        histories = await asyncio.gather(
            *(self.database.get_chat_history(player_id) for player_id in missing)
        )
        for player_id, history in zip(missing, histories, strict=True):
            history.append({"role": "player", "content": completed.actions[player_id]})

        results = await asyncio.gather(
            *(
                self.narrator.narrate(
                    player_id,
                    data["public_world_info"],
                    data["player_views"][player_id],
                    history,
                )
                for player_id, history in zip(missing, histories, strict=True)
            ),
            return_exceptions=True,
        )
        narrations = {
            player_id: result
            for player_id, result in zip(missing, results, strict=True)
            if isinstance(result, dict)
        }
        if narrations:
            await self.database.save_narrations(completed.round_number, narrations)
        if len(narrations) != len(missing):
            logger.error(
                "Narration generation failed for round %s: %s",
                completed.round_number,
                [result for result in results if not isinstance(result, dict)],
            )
            await self.sessions.broadcast(
                {"type": "error", "detail": "narration generation failed; use /retry"}
            )
            return

        data = await self.database.get_recovery_data(completed.round_number)
        await self.database.finish_round(completed)
        self.rounds.set_stage(RoundStage.FINISHED)
        for player_id, narration in data["narrations"].items():
            display = await self.database.get_player_display(player_id)
            await self.sessions.send(
                player_id,
                {
                    "type": "narration",
                    "round": completed.round_number,
                    **narration,
                    **display,
                },
            )
        await self.sessions.broadcast(
            {"type": "round_complete", "round": completed.round_number}
        )
        self.rounds.start_next_round()
        await self.database.create_round(self.rounds.round_number)
        await self.sessions.broadcast(await self._round_snapshot())

    async def recover_round(self) -> None:
        data = await self.database.get_recovery_data(self.rounds.round_number)
        stage = data["stage"]
        if stage == RoundStage.WAITING_INPUT:
            self.rounds.restore_waiting(data["players"])
            if self.rounds.is_processing() and all(data["actions"].values()):
                await self._process_round(
                    CompletedRound(self.rounds.round_number, data["actions"])
                )
            return
        if stage == RoundStage.FINISHED:
            return
        completed = CompletedRound(self.rounds.round_number, data["actions"])
        self.rounds.restore_processing(completed, stage)
        if stage == RoundStage.WORLD_UPDATING:
            await self._process_round(completed)
        elif stage in (RoundStage.WORLD_DONE, RoundStage.VIEW_GENERATING):
            await self._continue_views(completed)
        elif stage in (RoundStage.VIEW_DONE, RoundStage.NARRATION_GENERATING):
            await self._continue_narrations(completed)

    async def _set_stage(self, stage: RoundStage) -> None:
        self.rounds.set_stage(stage)
        await self.database.set_round_stage(self.rounds.round_number, stage)

    async def _replay_narrations(
        self, player_id: str, websocket: ServerConnection
    ) -> int:
        narrations = await self.database.get_unacked_narrations(player_id)
        display = await self.database.get_player_display(player_id)
        for index, narration in enumerate(narrations):
            narration["last_scene"] = index == len(narrations) - 1
            await websocket.send(json.dumps({**narration, **display}, ensure_ascii=False))
        return len(narrations)

    async def _send_history(self, player_id: str, websocket: ServerConnection) -> None:
        await websocket.send(json.dumps({
            "type": "history",
            "messages": await self.database.get_full_history(player_id),
        }, ensure_ascii=False))

    async def _send_status(
        self, participant_name: str, websocket: ServerConnection
    ) -> None:
        player_id = await self.sessions.role_for(participant_name)
        if player_id is None:
            raise RoundError("roles have not been assigned")
        display = await self.database.get_player_display(player_id)
        snapshot = await self._round_snapshot()
        await websocket.send(json.dumps({
            "type": "status",
            "scenario": self.scenario_name,
            "role": player_id,
            "round": self.rounds.round_number,
            "players": snapshot["players"],
            "draft": self.rounds.players[player_id].action,
            **display,
        }, ensure_ascii=False))

    async def _round_snapshot(self) -> dict:
        snapshot = self.rounds.snapshot()
        connected = await self.sessions.connections_by_role()
        for player_id, player in snapshot["players"].items():
            player["connected"] = connected.get(player_id, False)
        return snapshot

    @staticmethod
    def _decode(raw_message: str) -> dict:
        message = json.loads(raw_message)
        if not isinstance(message, dict):
            raise ValueError("message must be a JSON object")
        return message

    @staticmethod
    async def _send_error(websocket: ServerConnection, detail: str) -> None:
        await websocket.send(json.dumps({"type": "error", "detail": detail}))


async def run_server(game_path: Path, scenario_name: str) -> None:
    settings = load_settings()
    loader = PromptLoader(str(game_path))
    database = Database(str(game_path / "game.db"))
    await database.initialize(
        loader.json("world/initial_state.json"),
        {"A": loader.statusbar("A"), "B": loader.statusbar("B")},
    )
    llm = LLMClient(settings.llm_api_key, settings.llm_base_url, settings.llm_model)
    world_updater = WorldUpdater(llm, loader, settings.world_update_max_tokens)
    player_views = PlayerViewGenerator(llm, loader)
    narrator = Narrator(llm, loader, settings.narration_max_tokens)
    participants = await database.get_participants()
    game = GameServer(
        database,
        world_updater,
        player_views,
        narrator,
        await database.current_round(),
        scenario_name,
        participants,
    )
    await game.recover_round()
    if not game.sessions.roles_assigned:
        game.rounds.enter_lobby()
        for player_id, player in game.rounds.players.items():
            await database.save_player(player_id, player.status, player.action)

    async with serve(game.handler, settings.host, settings.port) as websocket_server:
        logger.info("Server listening on ws://%s:%s", settings.host, settings.port)
        await websocket_server.serve_forever()


def main() -> None:
    parser = argparse.ArgumentParser(description="AI RP Engine server")
    management = parser.add_mutually_exclusive_group()
    management.add_argument("--list-games", action="store_true")
    management.add_argument("--delete-game")
    management.add_argument("--list-scenarios", action="store_true")
    management.add_argument("--create-scenario")
    management.add_argument("--delete-scenario")
    parser.add_argument("--game", default=None)
    args = parser.parse_args()
    manager = ScenarioManager()
    if args.list_scenarios:
        print("\n".join(manager.list_scenarios()) or "No playable scenarios.")
        return
    if args.create_scenario:
        try:
            path = manager.create_scenario(args.create_scenario)
        except ValueError as exc:
            parser.error(str(exc))
        print(f"Created scenario: {path}")
        return
    if args.delete_scenario:
        try:
            manager.delete_scenario(args.delete_scenario)
        except ValueError as exc:
            parser.error(str(exc))
        print(f"Deleted scenario: {args.delete_scenario}")
        return
    if args.list_games:
        print("\n".join(manager.list_games()) or "No game instances.")
        return
    if args.delete_game:
        try:
            manager.delete_game(args.delete_game)
        except ValueError as exc:
            parser.error(str(exc))
        print(f"Deleted game: {args.delete_game}")
        return
    scenarios = manager.list_scenarios()
    if args.game:
        game_name = args.game
        if game_name in manager.list_games():
            game_path = manager.games_dir / game_name
        elif game_name in scenarios:
            game_path = manager.create_game(game_name, game_name)
        else:
            parser.error(f"unknown game or same-named scenario: {game_name}")
        try:
            asyncio.run(run_server(game_path, game_name))
        except KeyboardInterrupt:
            logger.info("Server stopped")
        return
    if not scenarios:
        print(
            "当前没有可用剧本。\n"
            "请先使用 --create-scenario <name> 创建新剧本后重试。"
        )
        return
    print("Available scenarios:\n")
    for index, name in enumerate(scenarios, 1):
        print(f"{index}. {name}")
    selection = int(input("\nSelect: ")) - 1
    template_name = scenarios[selection]
    game_name = template_name
    game_path = manager.create_game(template_name, game_name)
    try:
        asyncio.run(run_server(game_path, template_name))
    except KeyboardInterrupt:
        logger.info("Server stopped")


if __name__ == "__main__":
    main()
