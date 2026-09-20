import asyncio
import json
import logging
from typing import Dict, List, Optional
from fastapi import WebSocket

from app.domain.models.user import User, UserPresence

logger = logging.getLogger(__name__)


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

        # 1. Send current room state (all active users) to the new joiner
        active_users = self.get_active_presences(diagram_id)
        await websocket.send_text(
            json.dumps({
                "type": "ROOM_STATE",
                "diagram_id": diagram_id,
                "users": [p.model_dump(mode="json") for p in active_users],
            })
        )

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
            # Check if this user has any other connection in this diagram room
            user_still_connected = any(p.user_id == presence.user_id for p in room.values())
            if not user_still_connected:
                await self.broadcast(
                    diagram_id,
                    {
                        "type": "USER_LEFT",
                        "diagram_id": diagram_id,
                        "user_id": presence.user_id,
                        "name": presence.name,
                    },
                )
            logger.info(f"WebSocket disconnected for {presence.name} in room {diagram_id}")

        if not room and diagram_id in self._rooms:
            del self._rooms[diagram_id]

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
        dead_sockets = []

        for ws in list(room.keys()):
            if ws == exclude_ws:
                continue
            try:
                await ws.send_text(msg_str)
            except Exception as e:
                logger.warning(f"Error sending message to client: {e}")
                dead_sockets.append(ws)

        for ws in dead_sockets:
            room.pop(ws, None)

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


connection_manager = ConnectionManager()
