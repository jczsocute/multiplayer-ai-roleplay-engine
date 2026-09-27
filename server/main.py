import asyncio
import argparse
import ipaddress
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
from server.models import CompletedRound, PlayerStatus, RoundStage
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
        character_names: dict[str, str] | None = None,
    ) -> None:
        self.database = database
        self.world_updater = world_updater
        self.player_views = player_views
        self.narrator = narrator
        self.rounds = RoundManager(round_number)
        self.sessions = Sessions(max_users=100)
        self.command_lock = asyncio.Lock()
        self.scenario_name = scenario_name
        self.character_names = character_names or {"A": "Player A", "B": "Player B"}

    async def handler(self, websocket: ServerConnection) -> None:
        user_name: str | None = None
        try:
            registration = await asyncio.wait_for(websocket.recv(), timeout=15)
            message = self._decode(registration)
            if message.get("type") == "join_host":
                await self._serve_host(websocket)
                return
            if message.get("type") != "join":
                await self._send_error(websocket, "first message must be join or join_host")
                await websocket.close(code=1008)
                return

            user = await self.sessions.join(str(message.get("name", "")), websocket)
            if user is None:
                await self._send_error(websocket, "room already has 100 connected users")
                await websocket.close(code=1008)
                return
            user_name = user.name

            logger.info("User %s connected", user_name)
            await websocket.send(json.dumps({
                "type": "joined",
                "name": user.name,
                "role": None,
                "view_role": None,
                "scenario": self.scenario_name,
            }, ensure_ascii=False))
            await self._broadcast_presence()
            await self._broadcast_state()
            await self._broadcast_room_message(
                "system", f"{user.name} 已加入房间。"
            )

            async for raw_message in websocket:
                await self._handle_command(user_name, websocket, raw_message)
        except (ConnectionClosed, asyncio.TimeoutError):
            pass
        except (ValueError, json.JSONDecodeError) as exc:
            await self._send_error(websocket, str(exc))
        finally:
            if user_name is not None:
                removed = await self.sessions.remove(user_name, websocket)
                if removed is not None:
                    logger.info("User %s disconnected", user_name)
                    await self._broadcast_presence()
                    await self._broadcast_state()
                    text = f"{removed.name} 已离开房间。"
                    if removed.role:
                        text += f" Player {removed.role} 当前无人扮演。"
                    await self._broadcast_room_message("system", text)

    async def _serve_host(self, websocket: ServerConnection) -> None:
        if not self._is_local_host(websocket):
            await self._send_error(websocket, "host connections are limited to localhost")
            await websocket.close(code=1008)
            return
        if not await self.sessions.join_host(websocket):
            await self._send_error(websocket, "a host is already connected")
            await websocket.close(code=1008)
            return
        try:
            await websocket.send(json.dumps({
                "type": "host_joined",
                "scenario": self.scenario_name,
            }, ensure_ascii=False))
            await websocket.send(json.dumps(
                await self.sessions.presence_snapshot(), ensure_ascii=False
            ))
            await websocket.send(json.dumps(
                await self._round_snapshot(), ensure_ascii=False
            ))
            latest = await self.database.get_latest_world_update()
            if latest is not None:
                await websocket.send(json.dumps({
                    "type": "world_update",
                    **latest,
                }, ensure_ascii=False))
            async for raw_message in websocket:
                await self._handle_host_command(websocket, raw_message)
        finally:
            await self.sessions.remove_host(websocket)

    async def _handle_host_command(
        self, websocket: ServerConnection, raw_message: str
    ) -> None:
        try:
            message = self._decode(raw_message)
            command = message.get("type")
            if command == "room_chat":
                await self._send_room_chat("host", None, str(message.get("text", "")))
                return
            if command == "view":
                view = str(message.get("view", ""))
                await self.sessions.set_host_view(view)
                if view == "world":
                    latest = await self.database.get_latest_world_update()
                    await websocket.send(json.dumps({
                        "type": "world_view",
                        "result": latest["result"] if latest else {},
                        "round": latest["round"] if latest else None,
                    }, ensure_ascii=False))
                else:
                    await self._send_role_view(
                        websocket, view, include_current_view=True
                    )
                return
            if command == "status":
                await websocket.send(json.dumps(await self.sessions.presence_snapshot(), ensure_ascii=False))
                await websocket.send(json.dumps(await self._round_snapshot(), ensure_ascii=False))
                return
            if command == "retry_ai":
                if not self.rounds.is_processing():
                    raise RoundError("there is no AI stage to retry")
                await self.recover_round()
                return
            if command != "assign_roles":
                raise ValueError("unknown host command")
            async with self.command_lock:
                await self._assign_roles(
                    str(message.get("player_a", "")),
                    str(message.get("player_b", "")),
                )
        except (RoundError, ValueError, json.JSONDecodeError) as exc:
            await self._send_error(websocket, str(exc))

    async def _handle_command(
        self, user_name: str, websocket: ServerConnection, raw_message: str
    ) -> None:
        try:
            message = self._decode(raw_message)
            command = message.get("type")
            completed = None
            async with self.command_lock:
                player_id = await self.sessions.role_for(user_name)
                if command == "room_chat":
                    kind = "player" if player_id else "spectator"
                    await self._send_room_chat(
                        kind, user_name, str(message.get("text", "")), player_id
                    )
                    return
                if command == "view":
                    if player_id is not None:
                        raise RoundError("players cannot switch role views")
                    role = str(message.get("role", "")).upper()
                    await self.sessions.set_view(user_name, role)
                    await self._send_role_view(websocket, role)
                    return
                if player_id is None:
                    raise RoundError("spectators may only use /chat, /view A, /view B, and /quit")
                if command == "status":
                    await self._send_status(user_name, websocket)
                    return
                if command in ("retry_views", "retry_narration"):
                    raise RoundError("only Host can retry AI stages")
                if command == "action":
                    self.rounds.set_action(player_id, str(message.get("text", "")))
                elif command == "submit":
                    completed = self.rounds.submit(player_id)
                elif command == "cancel_submit":
                    self.rounds.cancel_submit(player_id)
                elif command == "pause":
                    self.rounds.pause(player_id)
                    await self._broadcast_room_message(
                        "system", f"Player {player_id} 已暂停。"
                    )
                elif command == "resume":
                    self.rounds.resume(player_id)
                    await self._broadcast_room_message(
                        "system", f"Player {player_id} 已恢复。"
                    )
                else:
                    raise RoundError(f"unknown command: {command}")

                player = self.rounds.players[player_id]
                await self.database.save_player(player_id, player.status, player.action)
                if command != "action":
                    await self._broadcast_state()

                if completed is not None:
                    for current_id in ("A", "B"):
                        current = self.rounds.players[current_id]
                        await self.database.save_player(current_id, current.status, current.action)
                    await self._process_round(completed)
        except (RoundError, ValueError, json.JSONDecodeError) as exc:
            await self._send_error(websocket, str(exc))

    async def _assign_roles(self, player_a: str, player_b: str) -> None:
        if self.rounds.is_processing():
            raise RoundError("roles cannot be reassigned while AI processing is active")
        before = await self.sessions.role_assignments()
        for role, name in before.items():
            if name and self.rounds.players[role].status != PlayerStatus.PAUSED:
                raise RoundError(
                    "reassign requires each currently occupied role to be PAUSED or empty"
                )

        assignments = await self.sessions.assign_roles(player_a, player_b)
        after = await self.sessions.role_assignments()
        changed_roles = [
            role for role in ("A", "B") if before.get(role) != after.get(role)
        ]
        for role in changed_roles:
            player = self.rounds.players[role]
            player.status = PlayerStatus.EDITING
            player.action = ""
            await self.database.save_player(role, player.status, player.action)

        changed_names = {
            name for name in set(before.values()) | set(after.values())
            if name and next(
                (role for role, current in before.items() if current == name), None
            ) != next((role for role, current in after.items() if current == name), None)
        }
        role_message = {
            "type": "role_assigned",
            "assignments": assignments,
            "changed_users": sorted(changed_names),
        }
        await self.sessions.broadcast(role_message)
        await self.sessions.send_host(role_message)

        for name in changed_names:
            user = self.sessions.users.get(name)
            if user is None:
                continue
            await self.sessions.send_user(name, {
                "type": "identity_changed",
                "role": user.role,
                "view_role": user.view_role,
                "reset": True,
            })
            if user.view_role:
                await self._send_role_view(
                    user.websocket,
                    user.view_role,
                    include_draft=user.role is not None,
                    include_current_view=user.role is not None,
                )

        await self._broadcast_presence()
        await self._broadcast_state()
        await self._broadcast_room_message(
            "system",
            f"Host 已完成角色分配：{player_a} → A，{player_b} → B。",
        )

    async def _send_role_view(
        self,
        websocket: ServerConnection,
        role: str,
        *,
        include_draft: bool = False,
        include_current_view: bool = False,
    ) -> None:
        if role not in ("A", "B"):
            raise RoundError("role view must be A or B")
        display = await self.database.get_player_display(role)
        message = {
            "type": "role_view",
            "role": role,
            "character_name": self.character_names[role],
            "history": await self.database.get_role_history(role),
            "statusbar": display["statusbar"],
            "reset": True,
        }
        if include_current_view:
            message["current_view"] = await self.database.get_latest_player_view(role)
        if include_draft:
            message["draft"] = self.rounds.players[role].action
        await websocket.send(json.dumps(message, ensure_ascii=False))

    async def _send_room_chat(
        self,
        kind: str,
        sender: str | None,
        text: str,
        role: str | None = None,
    ) -> None:
        text = text.strip()
        if not text:
            raise RoundError("chat message cannot be empty")
        if len(text) > 4000:
            raise RoundError("chat message is too long")
        # Room Plane is broadcast-only; never write this text to story chat_messages.
        await self._broadcast_room_message(kind, text, sender, role)

    async def _broadcast_room_message(
        self,
        kind: str,
        text: str,
        sender: str | None = None,
        role: str | None = None,
    ) -> None:
        message = {
            "type": "room_message",
            "kind": kind,
            "sender": sender,
            "role": role,
            "character_name": self.character_names.get(role) if role else None,
            "text": text,
        }
        await self.sessions.broadcast(message)
        await self.sessions.send_host(message)

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
            await self.sessions.send_host_view("world", {
                "type": "world_update",
                "round": completed.round_number,
                "result": world_result,
            })
            await self._set_stage(RoundStage.WORLD_DONE)
        except Exception:
            logger.exception("World update failed for round %s", completed.round_number)
            self.rounds.abort_processing()
            await self._set_stage(RoundStage.WAITING_INPUT)
            for player_id, player in self.rounds.players.items():
                await self.database.save_player(player_id, player.status, player.action)
            await self._broadcast_message({
                "type": "error",
                "detail": "world update failed; actions can be edited and resubmitted",
            })
            await self._broadcast_state()
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
                await self._broadcast_message({
                    "type": "error",
                    "detail": "player view generation failed; Host can use /retry",
                })
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
        displays = await asyncio.gather(
            *(self.database.get_player_display(player_id) for player_id in missing)
        )
        for player_id, history in zip(missing, histories, strict=True):
            history.append({"role": "player", "content": completed.actions[player_id]})

        results = await asyncio.gather(
            *(
                self.narrator.narrate(
                    player_id,
                    data["public_world_info"],
                    data["player_views"][player_id],
                    display["statusbar"],
                    history,
                )
                for player_id, history, display in zip(
                    missing, histories, displays, strict=True
                )
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
            await self._broadcast_message({
                "type": "error",
                "detail": "narration generation failed; Host can use /retry",
            })
            return

        data = await self.database.get_recovery_data(completed.round_number)
        await self.database.finish_round(completed)
        self.rounds.set_stage(RoundStage.FINISHED)
        for player_id, narration in data["narrations"].items():
            display = await self.database.get_player_display(player_id)
            await self.sessions.send_viewers(
                player_id,
                {
                    "type": "role_round",
                    "role": player_id,
                    "round": completed.round_number,
                    "entries": [
                        {
                            "kind": "action",
                            "content": completed.actions[player_id],
                        },
                        {
                            "kind": "narration",
                            "content": narration["text"],
                        },
                        {
                            "kind": "statusbar",
                            "content": display["statusbar"],
                        },
                    ],
                    "statusbar": display["statusbar"],
                },
            )
        await self.sessions.broadcast(
            {"type": "round_complete", "round": completed.round_number}
        )
        await self.sessions.send_host(
            {"type": "round_complete", "round": completed.round_number}
        )
        self.rounds.start_next_round()
        await self.database.create_round(self.rounds.round_number)
        await self._broadcast_state()

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
        message = {
            "type": "processing_stage",
            "round": self.rounds.round_number,
            "stage": stage.value,
        }
        await self.sessions.broadcast(message)
        await self.sessions.send_host(message)

    async def _send_status(
        self, user_name: str, websocket: ServerConnection
    ) -> None:
        player_id = await self.sessions.role_for(user_name)
        if player_id is None:
            raise RoundError("roles have not been assigned")
        display = await self.database.get_player_display(player_id)
        snapshot = await self._round_snapshot()
        await websocket.send(json.dumps({
            "type": "status",
            "scenario": self.scenario_name,
            "role": player_id,
            "round": self.rounds.round_number,
            "stage": snapshot["stage"],
            "players": snapshot["players"],
            "draft": self.rounds.players[player_id].action,
            **display,
        }, ensure_ascii=False))

    async def _round_snapshot(self) -> dict:
        snapshot = self.rounds.snapshot()
        assignments = await self.sessions.role_assignments()
        for player_id, player in snapshot["players"].items():
            player["connected"] = player_id in assignments
            player["user"] = assignments.get(player_id)
        return snapshot

    async def _broadcast_presence(self) -> None:
        message = await self.sessions.presence_snapshot()
        await self.sessions.broadcast(message)
        await self.sessions.send_host(message)

    async def _broadcast_state(self) -> None:
        message = await self._round_snapshot()
        await self.sessions.broadcast(message)
        await self.sessions.send_host(message)

    async def _broadcast_message(self, message: dict) -> None:
        await self.sessions.broadcast(message)
        await self.sessions.send_host(message)

    @staticmethod
    def _is_local_host(websocket: ServerConnection) -> bool:
        remote = getattr(websocket, "remote_address", None)
        if remote is None:
            return True
        address = remote[0] if isinstance(remote, tuple) else str(remote)
        try:
            return ipaddress.ip_address(address).is_loopback
        except ValueError:
            return address == "localhost"

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
    game = GameServer(
        database,
        world_updater,
        player_views,
        narrator,
        await database.current_round(),
        scenario_name,
        {"A": loader.character_name("A"), "B": loader.character_name("B")},
    )
    await game.recover_round()

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
