import asyncio
import json
import logging
from collections.abc import Awaitable, Callable

from websockets.exceptions import ConnectionClosed

from server.config import normalize_room_key
from server.gameserver.database import Database
from server.gameserver.llm.narrator import Narrator
from server.gameserver.llm.world_update import WorldUpdater
from server.gameserver.models import CompletedRound, PlayerStatus, RoundStage
from server.gameserver.protocol import (
    MAX_ROOM_CHAT_LENGTH,
    PROTOCOL_VERSION,
)
from server.gameserver.round_manager import RoundError, RoundManager
from server.gameserver.roles import RoleConfig
from server.gameserver.session import AccountIdentity, Connection, JoinResult, Sessions, User

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

USER_ERRORS = {
    "action_too_long": "行动内容过长",
    "empty_chat_message": "聊天消息不能为空",
    "chat_message_too_long": "聊天消息过长",
    "invalid_retry_round": "重试需要有效的回合编号",
    "invalid_rollback_round": "回滚需要有效的回合编号",
    "role_reassign_not_paused": "重新分配前，已占用角色必须暂停",
    "invalid_json_object": "消息必须是 JSON 对象",
    "unknown_role": "未知角色",
    "roles_not_assigned": "角色尚未分配",
}


class GameServer:
    def __init__(
        self,
        database: Database,
        world_updater: WorldUpdater,
        narrator: Narrator,
        round_number: int = 1,
        scenario_name: str = "default",
        character_names: dict[str, str] | None = None,
        room_key: str = "test-key",
        disconnect_grace_seconds: int = 60,
        role_config: RoleConfig | None = None,
        openings: dict[str, str] | None = None,
        narrator_history_rounds: int = 20,
        owner_user_id: int | None = None,
        max_users: int = 100,
    ) -> None:
        if role_config is None:
            role_ids = tuple(character_names or database.role_ids)
            names = character_names or {role_id: role_id for role_id in role_ids}
            role_config = RoleConfig(role_ids, names)
        if tuple(database.role_ids) != role_config.role_ids:
            raise ValueError("database roles and RoleConfig do not match")
        self.role_config = role_config
        self.role_ids = role_config.role_ids
        self.database = database
        self.world_updater = world_updater
        self.narrator = narrator
        self.rounds = RoundManager(self.role_ids, round_number)
        self.sessions = Sessions(
            self.role_ids,
            max_users=max_users, disconnect_grace_seconds=disconnect_grace_seconds,
        )
        self.sessions.expiry_handler = self._on_grace_expired
        # Injected by the platform RoomManager; GameServer stays unaware of
        # PlatformDatabase, game ids and room codes.
        self.on_member_left: Callable[[int], Awaitable[None]] | None = None
        self.on_game_changed: Callable[[], Awaitable[None]] | None = None
        self.command_lock = asyncio.Lock()
        self.scenario_name = scenario_name
        self.character_names = role_config.names
        if openings is not None and set(openings) != set(self.role_ids):
            raise ValueError("openings must contain every role exactly once")
        self.openings = openings or {role_id: "" for role_id in self.role_ids}
        if narrator_history_rounds < 1:
            raise ValueError("narrator_history_rounds must be a positive integer")
        self.narrator_history_rounds = narrator_history_rounds
        self.owner_user_id = owner_user_id
        self.room_key = room_key
        self.disconnect_grace_seconds = disconnect_grace_seconds

    def is_host(self, user_id: int) -> bool:
        """Host is a capability (game owner), never a role."""
        return self.owner_user_id is not None and user_id == self.owner_user_id

    async def public_handler(
        self, websocket: Connection, account: AccountIdentity
    ) -> None:
        user_id: int | None = None
        try:
            registration = await asyncio.wait_for(websocket.recv(), timeout=15)
            message = self._decode(registration)
            command = message.get("type")
            if command == "join_host":
                await self._send_error(
                    websocket, "公共连接入口不接受房主连接"
                )
                await websocket.close(code=1008)
                return
            if command not in ("join", "resume"):
                await self._send_error(websocket, "连接的第一条消息必须是 join 或 resume")
                await websocket.close(code=1008)
                return
            if not self._room_key_ok(message.get("room_key")):
                await self._send_error(websocket, "房间密钥无效")
                await websocket.close(code=1008)
                return

            if command == "join":
                result = await self.sessions.join(account, websocket)
                if result is None:
                    await self._send_error(
                        websocket,
                        f"房间人数已满（上限 {self.sessions.max_users} 人）",
                        code="room_full",
                    )
                    await websocket.close(code=1008)
                    return
                participant = result.user
                user_id = participant.user_id
                logger.info(
                    "User %s (%s) connected", participant.username, participant.user_id
                )
                await websocket.send(json.dumps(
                    self._identity_payload("joined", participant, result),
                    ensure_ascii=False,
                ))
                await self._broadcast_presence()
                await self._broadcast_state()
                if participant.view_role:
                    await self._send_role_view(
                        websocket,
                        participant.view_role,
                        include_draft=participant.view_role in participant.assigned_roles,
                    )
                await self._announce_join(result)
                await self._send_identity_notice(participant, websocket)
            else:
                try:
                    participant = await self.sessions.resume(
                        account.id,
                        str(message.get("resume_token", "")),
                        websocket,
                    )
                except ValueError as exc:
                    await self._send_error(websocket, str(exc))
                    await websocket.close(code=1008)
                    return
                user_id = participant.user_id
                logger.info("User %s (%s) resumed", participant.username, account.id)
                await websocket.send(json.dumps(
                    self._identity_payload("resumed", participant, None),
                    ensure_ascii=False,
                ))
                await self._broadcast_presence()
                await self._broadcast_state()
                await self._send_resume_view(participant, websocket)
                await self._broadcast_room_message(
                    "system", f"{participant.username} 已重新连接。",
                    message_key="user_reconnected", params={"name": participant.username},
                )
                await self._send_identity_notice(participant, websocket)

            async for raw_message in websocket:
                await self._handle_command(user_id, websocket, raw_message)
        except (ConnectionClosed, asyncio.TimeoutError):
            pass
        except (ValueError, json.JSONDecodeError) as exc:
            code = str(exc)
            await self._send_error(
                websocket, USER_ERRORS.get(code, code),
                code=code if code in USER_ERRORS else None,
            )
            await websocket.close(code=1008)
        finally:
            if user_id is not None:
                user = await self.sessions.mark_disconnected(user_id, websocket)
                if user is not None:
                    logger.info(
                        "User %s disconnected; grace period started", user.username
                    )
                    await self._broadcast_presence()
                    await self._broadcast_state()

    def _identity_payload(
        self, message_type: str, user, result: JoinResult | None
    ) -> dict:
        payload = {
            "type": message_type,
            "user": {"id": user.user_id, "username": user.username},
            "role": user.role,
            "assigned_roles": [role for role in self.role_ids if role in user.assigned_roles],
            "view_role": user.view_role,
            "is_host": self.is_host(user.user_id),
            "scenario": self.scenario_name,
            "protocol_version": PROTOCOL_VERSION,
            "resume_token": user.resume_token,
            "roles": self.role_config.definitions,
        }
        if result is not None:
            payload["reclaimed"] = result.reclaimed
        return payload

    async def _announce_join(self, result: JoinResult) -> None:
        if result.replaced_connection is not None:
            await self._notify_replaced(result)
            text = f"{result.user.username} 已在新的连接中接管本局。"
            key = "user_replaced"
        elif result.created:
            text = f"{result.user.username} 已加入房间。"
            key = "user_joined"
        else:
            text = f"{result.user.username} 已重新连接。"
            key = "user_reconnected"
        await self._broadcast_room_message(
            "system", text, message_key=key, params={"name": result.user.username}
        )

    async def _send_identity_notice(self, participant: User, websocket: Connection) -> None:
        if self.is_host(participant.user_id):
            text = "您目前身份为 <房主>。"
            key, params = "identity_host", {}
        elif participant.assigned_roles:
            names = "、".join(self.character_names[role] for role in self.role_ids
                             if role in participant.assigned_roles)
            text = f"您目前扮演 <{names}>。请继续游戏。"
            key, params = "identity_roles", {"names": names}
        else:
            text = "您目前身份为 <观众>。请等待房主分配角色。"
            key, params = "identity_spectator", {}
        await websocket.send(json.dumps({
            "type": "room_message", "kind": "system", "sender": None,
            "role": None, "character_name": None, "text": text,
            "message_key": key, "params": params,
        }, ensure_ascii=False))

    async def _notify_replaced(self, result: JoinResult) -> None:
        """Newest authenticated connection wins; retire the previous one."""
        connection = result.replaced_connection
        if connection is None:
            return
        try:
            await connection.send(json.dumps({
                "type": "session_replaced",
                "detail": "该账号已在新的连接中接管本局。",
            }, ensure_ascii=False))
            await connection.close(code=1000)
        except Exception:
            pass

    def _room_key_ok(self, provided: object) -> bool:
        if not self.room_key:
            return True
        return normalize_room_key(str(provided or "")) == normalize_room_key(self.room_key)

    async def _send_resume_view(self, user, websocket: Connection) -> None:
        if user.view_role in user.assigned_roles:
            await self._send_role_view(
                websocket, user.view_role, include_draft=True
            )
        elif user.view_role:
            await self._send_role_view(websocket, user.view_role)

    async def _on_grace_expired(self, user) -> None:
        """Room disconnect timeout: the participant is already out of Sessions."""
        logger.info("User %s disconnect timeout expired", user.username)
        await self._finish_member_exit(user.user_id, user.username, user.assigned_roles)

    async def evict_user(
        self, user_id: int, *, code: str | None = "kicked",
        reason: str = "你已被房主移出房间",
    ) -> bool:
        """Remove one participant now: notify, close, release role and seat.

        Shared by host kick and explicit leave. The timeout uses the same
        _finish_member_exit step after its grace session expires.
        Returns False when the user is not in this Room.
        """
        user = self.sessions.users.get(user_id)
        if user is None:
            return False
        logger.info("User %s evicted from the room", user.username)
        connection = user.websocket
        # Notify before removing: the participant is gone from Sessions afterwards.
        if connection is not None and code is not None:
            try:
                await connection.send(json.dumps(
                    {"type": code, "reason": reason}, ensure_ascii=False
                ))
            except Exception:
                logger.debug("Could not notify %s before closing", user.username)
        await self.sessions.evict(user_id)
        if connection is not None:
            try:
                await connection.close(code=1008 if code else 1000)
            except Exception:
                logger.debug("Could not close the socket of %s", user.username)
        await self._finish_member_exit(user_id, user.username, user.assigned_roles, reason)
        return True

    async def leave_user(self, user_id: int) -> bool:
        """Explicit platform leave uses the shared participant cleanup, without kick."""
        return await self.evict_user(user_id, code=None, reason="")

    async def _finish_member_exit(
        self, user_id: int, username: str, roles: set[str], reason: str = ""
    ) -> None:
        """Release everything tied to a participant that definitively left."""
        if not self.rounds.is_processing():
            for role in roles:
                player = self.rounds.players[role]
                player.status = PlayerStatus.EDITING
                player.action = ""
                await self.database.save_player(role, player.status, player.action)
        await self._broadcast_presence()
        await self._broadcast_state()
        text = f"{username} 已离开房间。"
        if reason:
            text = f"{username} 已被移出房间。"
        for role in self.role_ids:
            if role in roles:
                text += f" Player {role} 当前无人扮演。"
        await self._broadcast_room_message(
            "system", text, message_key="user_kicked" if reason else "user_left",
            params={"name": username, "roles": ", ".join(sorted(roles))},
        )
        await self._notify_member_left(user_id)

    async def _notify_member_left(self, user_id: int) -> None:
        if self.on_member_left is not None:
            await self.on_member_left(user_id)

    async def _touch_game(self) -> None:
        """Tell the platform layer this Game's content changed."""
        if self.on_game_changed is not None:
            await self.on_game_changed()

    async def _handle_command(
        self, user_id: int, websocket: Connection, raw_message: str
    ) -> None:
        try:
            message = self._decode(raw_message)
            command = message.get("type")
            if command == "room_chat":
                # Room Plane is independent of Story Plane processing. Handling chat
                # before command_lock keeps it responsive while AI runs, and it never
                # touches world state, rounds, or AI history.
                player_id = await self.sessions.role_for(user_id)
                username = await self.sessions.username(user_id) or str(user_id)
                kind = "player" if player_id else "spectator"
                await self._send_room_chat(
                    kind, username, str(message.get("text", "")), player_id
                )
                return
            if command in ("retry", "rollback") and self.is_ai_active():
                raise RoundError("game_processing")
            completed = None
            async with self.command_lock:
                if command == "leave":
                    current = self.sessions.users.get(user_id)
                    if current is not None and current.websocket is websocket:
                        await self.leave_user(user_id)
                    return
                player_id = await self.sessions.role_for(user_id)
                if command in ("retry", "rollback"):
                    if self.is_ai_active():
                        raise RoundError("game_processing")
                    if not self.is_host(user_id):
                        raise RoundError(
                            "forbidden: only the game owner can manage the timeline"
                        )
                    if command == "retry":
                        await self._retry_round_now(None)
                    else:
                        await self._rollback_to_round_now(message.get("round"))
                    return
                if command == "kick_user":
                    if not self.is_host(user_id):
                        raise RoundError(
                            "只有房主可以移出成员"
                        )
                    target = message.get("user_id")
                    if isinstance(target, bool) or not isinstance(target, int):
                        raise RoundError("踢人操作需要提供用户 ID")
                    if target == user_id or target == self.owner_user_id:
                        raise RoundError("cannot_kick_owner")
                    if target not in self.sessions.users:
                        raise RoundError("user_not_in_room")
                    await self.evict_user(target)
                    return
                if command == "assign_roles":
                    if not self.is_host(user_id):
                        raise RoundError("只有房主可以分配角色")
                    assignments = message.get("assignments")
                    if not isinstance(assignments, dict) or not all(
                        isinstance(role, str) and isinstance(value, int)
                        and not isinstance(value, bool)
                        for role, value in assignments.items()
                    ):
                        raise RoundError("角色分配格式无效")
                    await self._assign_role_ids(assignments)
                    return
                if command == "view":
                    role = str(message.get("role", "")).upper()
                    await self.sessions.set_view(user_id, role)
                    await self._send_role_view(
                        websocket, role,
                        include_draft=role in self.sessions.users[user_id].assigned_roles,
                    )
                    return
                player_id = await self.sessions.role_for(user_id)
                if player_id is None:
                    raise RoundError("观众只能使用房间聊天、查看视角和退出")
                if command == "status":
                    await self._send_status(user_id, websocket)
                    return
                if command == "action":
                    self.rounds.set_action(player_id, str(message.get("text", "")))
                elif command == "submit":
                    completed = self.rounds.submit(player_id)
                elif command == "cancel_submit":
                    self.rounds.cancel_submit(player_id)
                elif command == "pause":
                    self.rounds.pause(player_id)
                    await self._broadcast_room_message(
                        "system", f"Player {player_id} 已暂停。",
                        message_key="role_paused", params={"role": player_id},
                    )
                elif command == "resume":
                    self.rounds.resume(player_id)
                    await self._broadcast_room_message(
                        "system", f"Player {player_id} 已恢复。",
                        message_key="role_resumed", params={"role": player_id},
                    )
                else:
                    raise RoundError(f"未知指令：{command}")

                player = self.rounds.players[player_id]
                await self.database.save_player(player_id, player.status, player.action)
                if command != "action":
                    await self._broadcast_state()

                if completed is not None:
                    for current_id in self.role_ids:
                        current = self.rounds.players[current_id]
                        await self.database.save_player(current_id, current.status, current.action)
                    await self._process_round(completed)
        except (RoundError, ValueError, json.JSONDecodeError) as exc:
            code = str(exc)
            await self._send_error(
                websocket, USER_ERRORS.get(code, code),
                code=code if code in USER_ERRORS else None,
            )

    async def _assign_role_ids(
        self, resolved: dict[str, int]
    ) -> None:
        if self.rounds.is_processing():
            raise RoundError("AI 正在处理本回合，暂时不能重新分配角色")
        if set(resolved) != set(self.role_ids):
            raise RoundError("每个角色都必须分配且只能分配一个成员")
        for user_id in resolved.values():
            participant = self.sessions.users.get(user_id)
            if participant is None or not participant.connected:
                raise RoundError(f"该用户不在房间内：{user_id}")
        before = await self.sessions.role_assignments()
        for role, assigned_id in before.items():
            if assigned_id is not None and self.rounds.players[role].status != PlayerStatus.PAUSED:
                raise RoundError("role_reassign_not_paused")
        await self.sessions.assign_roles(resolved)
        after = await self.sessions.role_assignments()
        changed_roles = [
            role for role in self.role_ids if before.get(role) != after.get(role)
        ]
        for role in changed_roles:
            player = self.rounds.players[role]
            player.status = PlayerStatus.EDITING
            player.action = ""
            await self.database.save_player(role, player.status, player.action)

        before_by_id: dict[int, set[str]] = {}
        after_by_id: dict[int, set[str]] = {}
        for role, user_id in before.items():
            before_by_id.setdefault(user_id, set()).add(role)
        for role, user_id in after.items():
            after_by_id.setdefault(user_id, set()).add(role)
        changed_ids = sorted(
            user_id
            for user_id in set(before_by_id) | set(after_by_id)
            if before_by_id.get(user_id) != after_by_id.get(user_id)
        )
        role_message = {
            "type": "role_assigned",
            "assignments": await self.sessions.role_usernames(),
            "changed_users": [
                self.sessions.users[user_id].username
                for user_id in changed_ids
                if user_id in self.sessions.users
            ],
        }
        await self.sessions.broadcast(role_message)

        for user_id in changed_ids:
            user = self.sessions.users.get(user_id)
            if user is None:
                continue
            await self.sessions.send_user(user_id, {
                "type": "identity_changed",
                "role": user.role,
                "assigned_roles": [role for role in self.role_ids if role in user.assigned_roles],
                "view_role": user.view_role,
                "reset": True,
            })
            if user.view_role and user.websocket is not None:
                await self._send_role_view(
                    user.websocket,
                    user.view_role,
                    include_draft=user.view_role in user.assigned_roles,
                )

        await self._broadcast_presence()
        await self._broadcast_state()
        labels = await self.sessions.role_usernames()
        await self._broadcast_room_message(
            "system",
            "Host 已完成角色分配："
            + "，".join(
                f"{labels[role]} → {role}" for role in self.role_ids
            )
            + "。",
            message_key="roles_assigned",
            params={"assignments": ", ".join(f"{labels[role]} → {role}" for role in self.role_ids)},
        )

    async def close_connections(self, detail: str = "房间已关闭") -> None:
        await self.sessions.close_all({
            "type": "room_closed", "detail": detail,
        })

    async def _build_role_view(
        self,
        role: str,
        *,
        include_draft: bool = False,
    ) -> dict:
        if role not in self.role_ids:
            raise RoundError("unknown_role")
        display = await self.database.get_player_display(role)
        message = {
            "type": "role_view",
            "role": role,
            "character_name": self.character_names[role],
            "opening": self.openings[role],
            "history": await self.database.get_role_history(role),
            "character_status": display["character_status"],
            "reset": True,
        }
        if include_draft:
            message["draft"] = self.rounds.players[role].action
        return message

    async def _send_role_view(
        self,
        websocket: Connection,
        role: str,
        *,
        include_draft: bool = False,
    ) -> None:
        message = await self._build_role_view(
            role, include_draft=include_draft
        )
        await websocket.send(json.dumps(message, ensure_ascii=False))

    async def _broadcast_role_views(self) -> None:
        """Rebuild every role view in place, e.g. after retry or rollback."""
        for role in self.role_ids:
            await self.sessions.send_viewers(role, await self._build_role_view(role))

    async def _send_room_chat(
        self,
        kind: str,
        sender: str | None,
        text: str,
        role: str | None = None,
    ) -> None:
        text = text.strip()
        if not text:
            raise RoundError("empty_chat_message")
        if len(text) > MAX_ROOM_CHAT_LENGTH:
            raise RoundError("chat_message_too_long")
        # Room Plane is broadcast-only; never write this text to story chat_messages.
        await self._broadcast_room_message(kind, text, sender, role)

    async def _broadcast_room_message(
        self,
        kind: str,
        text: str,
        sender: str | None = None,
        role: str | None = None,
        message_key: str | None = None,
        params: dict | None = None,
    ) -> None:
        message = {
            "type": "room_message",
            "kind": kind,
            "sender": sender,
            "role": role,
            "character_name": self.character_names.get(role) if role else None,
            "text": text,
        }
        if message_key is not None:
            message["message_key"] = message_key
            message["params"] = params or {}
        await self.sessions.broadcast(message)

    async def _process_round(self, completed: CompletedRound) -> None:
        try:
            await self.database.lock_round_actions(completed)
            await self._set_stage(RoundStage.WORLD_UPDATING)
            current_world_state = await self.database.get_world_state()
            world_result = await self.world_updater.update(
                current_world_state=current_world_state,
                actions=completed.actions,
            )
            await self.database.save_world_update(completed, world_result)
            await self._set_stage(RoundStage.WORLD_DONE)
        except Exception:
            logger.exception("World update failed for round %s", completed.round_number)
            await self._set_stage(RoundStage.FAILED)
            await self._broadcast_message({
                "type": "error",
                "code": "world_update_failed",
                "detail": "世界更新失败，房主可以重试本回合。",
            })
            await self._broadcast_state()
            return

        await self._continue_narrations(completed)

    async def _continue_narrations(self, completed: CompletedRound) -> None:
        await self._set_stage(RoundStage.NARRATION_GENERATING)
        data = await self.database.get_recovery_data(completed.round_number)
        role_ids = list(self.role_ids)
        histories = await asyncio.gather(
            *(
                self.database.get_narrator_history(
                    player_id, self.narrator_history_rounds
                )
                for player_id in role_ids
            )
        )
        displays = await asyncio.gather(
            *(self.database.get_player_display(player_id) for player_id in role_ids)
        )
        for player_id, history in zip(role_ids, histories, strict=True):
            history.append({"role": "player", "content": completed.actions[player_id]})

        results = await asyncio.gather(
            *(
                self.narrator.narrate(
                    player_id,
                    data["character_views"][player_id],
                    display["character_status"],
                    history,
                )
                for player_id, history, display in zip(
                    role_ids, histories, displays, strict=True
                )
            ),
            return_exceptions=True,
        )
        narrations = {
            player_id: result
            for player_id, result in zip(role_ids, results, strict=True)
            if isinstance(result, dict)
        }
        if len(narrations) != len(role_ids):
            # No partial save: a retry always regenerates the whole round.
            logger.error(
                "Narration generation failed for round %s: %s",
                completed.round_number,
                [result for result in results if not isinstance(result, dict)],
            )
            await self._set_stage(RoundStage.FAILED)
            await self._broadcast_message({
                "type": "error",
                "detail": "叙事生成失败，房主可以重试本回合",
            })
            return
        await self.database.save_narrations(completed.round_number, narrations)

        data = await self.database.get_recovery_data(completed.round_number)
        await self.database.finish_round(completed)
        self.rounds.set_stage(RoundStage.FINISHED)
        for player_id in self.role_ids:
            narration = data["narrations"][player_id]
            display = await self.database.get_player_display(player_id)
            await self.sessions.send_viewers(
                player_id,
                {
                    "type": "role_round",
                    "role": player_id,
                    "round": completed.round_number,
                    "entries": [
                        {
                            "round": completed.round_number,
                            "kind": "action",
                            "content": completed.actions[player_id],
                        },
                        {
                            "round": completed.round_number,
                            "kind": "narration",
                            "content": narration["text"],
                        },
                    ] + ([{
                        "round": completed.round_number,
                        "kind": "character_status",
                        "content": display["character_status"],
                    }] if display["character_status"] is not None else []),
                    "character_status": display["character_status"],
                },
            )
        await self.sessions.broadcast(
            {"type": "round_complete", "round": completed.round_number}
        )
        await self._touch_game()
        self.rounds.start_next_round()
        await self.database.create_round(self.rounds.round_number)
        await self._broadcast_state()

    async def recover_round(self) -> None:
        """Resume an interrupted round.

        The project keeps a single linear timeline and no partial-retry checkpoints:
        any round that is not ``FINISHED`` is re-run in full from its base world
        with its preserved actions. Only ``WAITING_INPUT`` rounds are left untouched
        for the players to submit normally.
        """
        current = self.rounds.round_number
        data = await self.database.get_recovery_data(current)
        stage = data["stage"]
        if stage == RoundStage.WAITING_INPUT:
            self.rounds.restore_waiting(data["players"])
            if self.rounds.is_processing() and all(data["actions"].values()):
                # The round had locked but processing never started (crash in
                # between): re-run it in full through the same path.
                await self._retry_round_now(current)
            return
        if stage == RoundStage.FINISHED:
            return
        logger.info("Recovering unfinished round %s with a full retry", current)
        await self._retry_round_now(current)

    async def retry_round(self, round_number: int | None = None) -> None:
        async with self.command_lock:
            await self._retry_round_now(round_number)

    def is_ai_active(self) -> bool:
        return self.rounds.is_ai_active() or (
            self.command_lock.locked() and self.rounds.is_processing()
            and self.rounds.stage != RoundStage.FAILED
        )

    async def rollback_to_round(self, round_number: object) -> None:
        async with self.command_lock:
            await self._rollback_to_round_now(round_number)

    async def _default_retry_round(self) -> int:
        current = self.rounds.round_number
        data = await self.database.get_recovery_data(current)
        if data["locked"]:
            # The in-progress round already has AI output; redo it rather than the
            # previous one.
            return current
        last = await self.database.last_completed_round()
        if last is None:
            raise RoundError("没有可重新生成的回合")
        return last

    async def _retry_round_now(self, round_number: int | None) -> None:
        """Full re-run of one round: same actions, fresh WorldUpdater + Narrators."""
        if round_number is None:
            round_number = await self._default_retry_round()
        if isinstance(round_number, bool) or not isinstance(round_number, int):
            raise RoundError("invalid_retry_round")
        logger.info("Retrying round %s", round_number)
        actions = await self.database.prepare_retry_round(round_number)
        completed = CompletedRound(round_number, actions)
        self.rounds.begin_reprocess(completed)
        for role_id in self.role_ids:
            player = self.rounds.players[role_id]
            await self.database.save_player(role_id, player.status, player.action)
        await self._broadcast_room_message(
            "system", f"正在使用原行动重新生成 Round {round_number}…",
            message_key="round_retry", params={"round": round_number},
        )
        await self._broadcast_state()
        await self._process_round(completed)
        await self._broadcast_role_views()

    async def _rollback_to_round_now(self, round_number: object) -> None:
        if isinstance(round_number, bool) or not isinstance(round_number, int):
            raise RoundError("invalid_rollback_round")
        if round_number < 1:
            raise RoundError("invalid_rollback_round")
        logger.info("Rolling back to round %s", round_number)
        next_round = await self.database.rollback_to_round(round_number)
        self.rounds.reset_to_round(next_round)
        for role_id in self.role_ids:
            player = self.rounds.players[role_id]
            await self.database.save_player(role_id, player.status, player.action)
        await self._broadcast_room_message(
            "system", f"已回滚到 Round {round_number}，之后的剧情已删除。",
            message_key="round_rollback", params={"round": round_number},
        )
        await self._broadcast_state()
        await self._broadcast_role_views()
        await self._touch_game()

    async def _set_stage(self, stage: RoundStage) -> None:
        self.rounds.set_stage(stage)
        await self.database.set_round_stage(self.rounds.round_number, stage)
        message = {
            "type": "processing_stage",
            "round": self.rounds.round_number,
            "stage": stage.value,
        }
        await self.sessions.broadcast(message)

    async def _send_status(
        self, user_id: int, websocket: Connection
    ) -> None:
        player_id = await self.sessions.role_for(user_id)
        if player_id is None:
            raise RoundError("roles_not_assigned")
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
        usernames = await self.sessions.role_usernames()
        connections = await self.sessions.role_connections()
        for player_id, player in snapshot["players"].items():
            player["connected"] = connections.get(player_id, False)
            player["user"] = usernames.get(player_id)
            player["user_id"] = assignments.get(player_id)
            player["character_name"] = self.character_names[player_id]
        return snapshot

    async def _broadcast_presence(self) -> None:
        message = await self.sessions.presence_snapshot()
        await self.sessions.broadcast(message)

    async def _broadcast_state(self) -> None:
        message = await self._round_snapshot()
        await self.sessions.broadcast(message)

    async def _broadcast_message(self, message: dict) -> None:
        await self.sessions.broadcast(message)

    @staticmethod
    def _decode(raw_message: str) -> dict:
        message = json.loads(raw_message)
        if not isinstance(message, dict):
            raise ValueError("invalid_json_object")
        return message

    @staticmethod
    async def _send_error(
        websocket: Connection, detail: str, code: str | None = None
    ) -> None:
        message: dict = {"type": "error", "detail": detail}
        if code is not None:
            message["code"] = code
        await websocket.send(json.dumps(message, ensure_ascii=False))
