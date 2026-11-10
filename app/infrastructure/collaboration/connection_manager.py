import asyncio
import json
import logging
from typing import Dict, List, Optional
from fastapi import WebSocket

from app.domain.models.user import User, UserPresence

logger = logging.getLogger(__name__)

# Max time one client may take to accept a message. Broadcasts run while callers
# hold the room lock, so this bounds how long a stalled client can freeze a room.
BROADCAST_SEND_TIMEOUT_S = 2.0


class ConnectionManager:
    def __init__(self):
        # diagram_id -> { WebSocket: UserPresence }
        self._rooms: Dict[str, Dict[WebSocket, UserPresence]] = {}

    def get_room(self, diagram_id: str) -> Dict[WebSocket, UserPresence]:
        if diagram_id not in self._rooms:
            self._rooms[diagram_id] = {}
        return self._rooms[diagram_id]

    async def connect(self, websocket: WebSocket, diagram_id: str, user: User) -> UserPresence:
        await websocket.accept()
        room = self.get_room(diagram_id)

        presence = UserPresence(
            user_id=user.id,
            name=user.name,
            email=user.email,
            avatar_color=user.avatar_color,
        )
        room[websocket] = presence

        # 1. Send current room state (all active users) to the new joiner.
        # The socket is already registered: if this send fails it must not leak.
        active_users = self.get_active_presences(diagram_id)
        try:
            await asyncio.wait_for(
                websocket.send_text(
                    json.dumps({
                        "type": "ROOM_STATE",
                        "diagram_id": diagram_id,
                        "users": [p.model_dump(mode="json") for p in active_users],
                    })
                ),
                timeout=BROADCAST_SEND_TIMEOUT_S,
            )
        except BaseException:
            room.pop(websocket, None)
            self._cleanup_room(diagram_id)
            await self._close_quietly(websocket)
            raise

        # 2. Notify all others that this user joined
        await self.broadcast(
            diagram_id,
            {
                "type": "USER_JOINED",
                "diagram_id": diagram_id,
                "user": presence.model_dump(mode="json"),
            },
            exclude_ws=websocket,
        )

        logger.info(f"User {user.name} ({user.id}) joined diagram room {diagram_id}")
        return presence

    async def disconnect(self, websocket: WebSocket, diagram_id: str):
        room = self.get_room(diagram_id)
        presence = room.pop(websocket, None)

        if presence:
            # Idempotent: a socket already dropped by broadcast() has no presence here.
            await self._announce_left(diagram_id, presence)
            logger.info(f"WebSocket disconnected for {presence.name} in room {diagram_id}")

        self._cleanup_room(diagram_id)

    def _cleanup_room(self, diagram_id: str) -> None:
        room = self._rooms.get(diagram_id)
        if room is not None and not room:
            del self._rooms[diagram_id]

    async def _announce_left(self, diagram_id: str, presence: UserPresence) -> None:
        """USER_LEFT, only when that user has no other socket in the room."""
        room = self._rooms.get(diagram_id, {})
        if any(p.user_id == presence.user_id for p in room.values()):
            return
        await self.broadcast(
            diagram_id,
            {
                "type": "USER_LEFT",
                "diagram_id": diagram_id,
                "user_id": presence.user_id,
                "name": presence.name,
            },
        )

    @staticmethod
    async def _close_quietly(websocket: WebSocket) -> None:
        """Best-effort close; never raises (except cancellation) and never hangs."""
        try:
            await asyncio.wait_for(websocket.close(), timeout=BROADCAST_SEND_TIMEOUT_S)
        except Exception:
            pass

    def get_active_presences(self, diagram_id: str) -> List[UserPresence]:
        room = self.get_room(diagram_id)
        # Deduplicate by user_id
        seen = set()
        presences = []
        for p in room.values():
            if p.user_id not in seen:
                seen.add(p.user_id)
                presences.append(p)
        return presences

    async def broadcast(
        self,
        diagram_id: str,
        message: dict,
        exclude_ws: Optional[WebSocket] = None,
    ):
        room = self.get_room(diagram_id)
        msg_str = json.dumps(message)
        targets = [ws for ws in list(room.keys()) if ws != exclude_ws]
        if not targets:
            return

        # Concurrent sends, each bounded: one stalled client cannot delay the rest.
        if len(targets) == 1:
            # No gather task hop for the common single-peer case (keeps ordering
            # tight relative to other coroutines writing to the same socket).
            results = [await self._send(targets[0], msg_str)]
        else:
            results = await asyncio.gather(
                *(self._send(ws, msg_str) for ws in targets), return_exceptions=True
            )
        dead = [ws for ws, ok in zip(targets, results) if ok is not True]
        if not dead:
            return

        gone: List[UserPresence] = []
        for ws in dead:
            presence = room.pop(ws, None)
            if presence:
                gone.append(presence)
        await asyncio.gather(*(self._close_quietly(ws) for ws in dead), return_exceptions=True)

        # Same as disconnect(): one USER_LEFT per user who has no socket left.
        announced = set()
        for presence in gone:
            if presence.user_id in announced:
                continue
            announced.add(presence.user_id)
            logger.info(f"Dropped dead socket of {presence.name} in room {diagram_id}")
            await self._announce_left(diagram_id, presence)
        self._cleanup_room(diagram_id)

    @staticmethod
    async def _send(ws: WebSocket, msg_str: str) -> bool:
        try:
            await asyncio.wait_for(ws.send_text(msg_str), timeout=BROADCAST_SEND_TIMEOUT_S)
            return True
        except Exception as e:
            logger.warning(f"Error sending message to client: {e!r}")
            return False

    async def broadcast_cursor(
        self,
        diagram_id: str,
        sender_ws: WebSocket,
        x: float,
        y: float,
    ):
        room = self.get_room(diagram_id)
        presence = room.get(sender_ws)
        if presence:
            presence.cursor_x = x
            presence.cursor_y = y
            await self.broadcast(
                diagram_id,
                {
                    "type": "CURSOR_MOVE",
                    "diagram_id": diagram_id,
                    "user_id": presence.user_id,
                    "name": presence.name,
                    "avatar_color": presence.avatar_color,
                    "x": x,
                    "y": y,
                },
                exclude_ws=sender_ws,
            )

    async def broadcast_selection(
        self,
        diagram_id: str,
        sender_ws: WebSocket,
        selected_class_id: Optional[str],
    ):
        room = self.get_room(diagram_id)
        presence = room.get(sender_ws)
        if presence:
            presence.selected_class_id = selected_class_id
            await self.broadcast(
                diagram_id,
                {
                    "type": "SELECTION_CHANGE",
                    "diagram_id": diagram_id,
                    "user_id": presence.user_id,
                    "selected_class_id": selected_class_id,
                },
                exclude_ws=sender_ws,
            )

    async def broadcast_document_update(
        self,
        diagram_id: str,
        document_dict: dict,
        exclude_ws: Optional[WebSocket] = None,
        command_dict: Optional[dict] = None,
    ):
        await self.broadcast(
            diagram_id,
            {
                "type": "DOCUMENT_UPDATED",
                "diagram_id": diagram_id,
                "document": document_dict,
                "version": document_dict.get("version"),
                "command": command_dict,
            },
            exclude_ws=exclude_ws,
        )

    async def broadcast_chat_message(
        self,
        diagram_id: str,
        message_dict: dict,
    ):
        await self.broadcast(
            diagram_id,
            {
                "type": "CHAT_MESSAGE",
                "diagram_id": diagram_id,
                "message": message_dict,
            },
        )

    async def broadcast_typing(
        self,
        diagram_id: str,
        user_id: str,
        name: str,
        is_typing: bool,
        exclude_ws: Optional[WebSocket] = None,
    ):
        await self.broadcast(
            diagram_id,
            {
                "type": "TYPING_INDICATOR",
                "diagram_id": diagram_id,
                "user_id": user_id,
                "name": name,
                "is_typing": is_typing,
            },
            exclude_ws=exclude_ws,
        )


connection_manager = ConnectionManager()
