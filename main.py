from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
import json
import os
from datetime import datetime

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Gestionnaire de connexions WebSocket
class ConnectionManager:
    def __init__(self):
        # { room: [ {ws, username, color} ] }
        self.rooms: dict[str, list[dict]] = {}

    async def connect(self, websocket: WebSocket, room: str, username: str, color: str):
        await websocket.accept()
        if room not in self.rooms:
            self.rooms[room] = []
        self.rooms[room].append({"ws": websocket, "username": username, "color": color})
        await self.broadcast(room, {
            "type": "system",
            "text": f"{username} a rejoint le salon",
            "time": now(),
            "room": room,
            "online": self.count(room)
        })

    def disconnect(self, websocket: WebSocket, room: str):
        username = ""
        if room in self.rooms:
            for conn in self.rooms[room]:
                if conn["ws"] == websocket:
                    username = conn["username"]
                    break
            self.rooms[room] = [c for c in self.rooms[room] if c["ws"] != websocket]
        return username

    async def broadcast(self, room: str, message: dict):
        if room in self.rooms:
            dead = []
            for conn in self.rooms[room]:
                try:
                    await conn["ws"].send_text(json.dumps(message))
                except:
                    dead.append(conn)
            for d in dead:
                self.rooms[room].remove(d)

    def count(self, room: str) -> int:
        return len(self.rooms.get(room, []))

    def get_user_info(self, websocket: WebSocket, room: str):
        for conn in self.rooms.get(room, []):
            if conn["ws"] == websocket:
                return conn["username"], conn["color"]
        return "Anonyme", "#1D9E75"

def now():
    return datetime.now().strftime("%H:%M")

manager = ConnectionManager()

@app.websocket("/ws/{room}/{username}/{color}")
async def websocket_endpoint(websocket: WebSocket, room: str, username: str, color: str):
    color = "#" + color  # color passé sans #
    await manager.connect(websocket, room, username, color)
    try:
        while True:
            data = await websocket.receive_text()
            msg = json.loads(data)
            uname, ucolor = manager.get_user_info(websocket, room)
            await manager.broadcast(room, {
                "type": "message",
                "username": uname,
                "color": ucolor,
                "text": msg.get("text", ""),
                "time": now(),
                "room": room,
                "online": manager.count(room)
            })
    except WebSocketDisconnect:
        username = manager.disconnect(websocket, room)
        await manager.broadcast(room, {
            "type": "system",
            "text": f"{username} a quitté le salon",
            "time": now(),
            "room": room,
            "online": manager.count(room)
        })

@app.get("/")
async def root():
    with open("index.html", "r") as f:
        return HTMLResponse(f.read())
