from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
import json, sqlite3
from datetime import datetime

app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

DB = "chat.db"

def now_time():
    return datetime.now().strftime("%H:%M")

def now_full():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

def get_db():
    return sqlite3.connect(DB)

def init_db():
    con = get_db()
    cur = con.cursor()
    cur.execute("""CREATE TABLE IF NOT EXISTS messages (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        room TEXT, username TEXT, color TEXT,
        text TEXT, msg_type TEXT DEFAULT 'message',
        created_at TEXT
    )""")
    cur.execute("""CREATE TABLE IF NOT EXISTS rooms (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT UNIQUE, label TEXT, owner TEXT,
        is_private INTEGER DEFAULT 0,
        created_at TEXT
    )""")
    cur.execute("""CREATE TABLE IF NOT EXISTS room_invites (
        room_name TEXT, username TEXT,
        PRIMARY KEY (room_name, username)
    )""")
    defaults = [("general","💬 General"),("idees","💡 Idées"),("design","🎨 Design"),("gaming","🎮 Gaming")]
    for name, label in defaults:
        cur.execute("INSERT OR IGNORE INTO rooms(name,label,owner,is_private,created_at) VALUES(?,?,?,?,?)",
                    (name, label, None, 0, now_full()))
    con.commit()
    con.close()

def save_message(room, username, color, text, msg_type="message"):
    con = get_db()
    con.execute("INSERT INTO messages(room,username,color,text,msg_type,created_at) VALUES(?,?,?,?,?,?)",
                (room, username, color, text, msg_type, now_full()))
    con.commit()
    con.close()

def get_history(room, limit=50):
    con = get_db()
    rows = con.execute(
        "SELECT username,color,text,msg_type,created_at FROM messages WHERE room=? ORDER BY id DESC LIMIT ?",
        (room, limit)
    ).fetchall()
    con.close()
    result = []
    for r in reversed(rows):
        t = datetime.strptime(r[4], "%Y-%m-%d %H:%M:%S").strftime("%H:%M")
        result.append({"username": r[0], "color": r[1], "text": r[2], "type": r[3], "time": t})
    return result

def get_rooms():
    con = get_db()
    rows = con.execute("SELECT name,label,owner,is_private FROM rooms ORDER BY id").fetchall()
    con.close()
    return [{"name": r[0], "label": r[1], "owner": r[2], "is_private": bool(r[3])} for r in rows]

def can_access(room_name, username):
    con = get_db()
    row = con.execute("SELECT is_private,owner FROM rooms WHERE name=?", (room_name,)).fetchone()
    if not row:
        con.close()
        return False
    is_private, owner = row
    if not is_private:
        con.close()
        return True
    if owner == username:
        con.close()
        return True
    invite = con.execute("SELECT 1 FROM room_invites WHERE room_name=? AND username=?", (room_name, username)).fetchone()
    con.close()
    return invite is not None

def count_user_rooms(username, is_private):
    con = get_db()
    count = con.execute("SELECT COUNT(*) FROM rooms WHERE owner=? AND is_private=?",
                        (username, 1 if is_private else 0)).fetchone()[0]
    con.close()
    return count

def do_create_room(name, label, owner, is_private):
    con = get_db()
    try:
        con.execute("INSERT INTO rooms(name,label,owner,is_private,created_at) VALUES(?,?,?,?,?)",
                    (name, label, owner, 1 if is_private else 0, now_full()))
        con.commit()
        return True
    except:
        return False
    finally:
        con.close()

def do_invite(room_name, inviter, invitee):
    con = get_db()
    row = con.execute("SELECT owner FROM rooms WHERE name=?", (room_name,)).fetchone()
    if not row or row[0] != inviter:
        con.close()
        return False, "Tu n'es pas le propriétaire de ce salon"
    try:
        con.execute("INSERT OR IGNORE INTO room_invites(room_name,username) VALUES(?,?)", (room_name, invitee))
        con.commit()
        con.close()
        return True, "ok"
    except:
        con.close()
        return False, "Erreur"

init_db()

class Manager:
    def __init__(self):
        self.rooms: dict[str, list[dict]] = {}
        self.connected: dict[str, str] = {}

    async def connect(self, ws, room, username, color):
        await ws.accept()
        if room not in self.rooms:
            self.rooms[room] = []
        self.rooms[room].append({"ws": ws, "username": username, "color": color})
        self.connected[username] = room

    def disconnect(self, ws, room):
        username = ""
        if room in self.rooms:
            for c in self.rooms[room]:
                if c["ws"] == ws:
                    username = c["username"]
                    break
            self.rooms[room] = [c for c in self.rooms[room] if c["ws"] != ws]
        if username in self.connected:
            del self.connected[username]
        return username

    def is_taken(self, username):
        return username in self.connected

    def get_members(self, room):
        return [{"username": c["username"], "color": c["color"]} for c in self.rooms.get(room, [])]

    def count(self, room):
        return len(self.rooms.get(room, []))

    def get_info(self, ws, room):
        for c in self.rooms.get(room, []):
            if c["ws"] == ws:
                return c["username"], c["color"]
        return None, None

    async def broadcast(self, room, msg):
        dead = []
        for c in self.rooms.get(room, []):
            try:
                await c["ws"].send_text(json.dumps(msg))
            except:
                dead.append(c)
        for d in dead:
            self.rooms[room].remove(d)

    async def send_to_user(self, username, msg):
        room = self.connected.get(username)
        if room:
            for c in self.rooms.get(room, []):
                if c["username"] == username:
                    try:
                        await c["ws"].send_text(json.dumps(msg))
                    except:
                        pass

mgr = Manager()

@app.websocket("/ws/{room}/{username}/{color}")
async def endpoint(ws: WebSocket, room: str, username: str, color: str):
    color = "#" + color

    if mgr.is_taken(username):
        await ws.accept()
        await ws.send_text(json.dumps({"type": "error", "code": "username_taken",
                                       "text": f"Le pseudo '{username}' est déjà utilisé."}))
        await ws.close()
        return

    if not can_access(room, username):
        await ws.accept()
        await ws.send_text(json.dumps({"type": "error", "code": "access_denied",
                                       "text": "Tu n'as pas accès à ce salon privé."}))
        await ws.close()
        return

    await mgr.connect(ws, room, username, color)

    await ws.send_text(json.dumps({"type": "history", "messages": get_history(room)}))
    await ws.send_text(json.dumps({"type": "rooms", "rooms": get_rooms()}))

    sys_text = f"{username} a rejoint le salon"
    save_message(room, "system", "", sys_text, "system")
    await mgr.broadcast(room, {
        "type": "system", "text": sys_text, "time": now_time(),
        "members": mgr.get_members(room), "online": mgr.count(room)
    })

    try:
        while True:
            data = await ws.receive_text()
            msg = json.loads(data)
            uname, ucolor = mgr.get_info(ws, room)
            if not uname:
                break

            mtype = msg.get("type", "message")

            if mtype == "message":
                text = msg.get("text", "").strip()
                if not text:
                    continue
                save_message(room, uname, ucolor, text)
                await mgr.broadcast(room, {
                    "type": "message", "username": uname, "color": ucolor,
                    "text": text, "time": now_time(),
                    "reply_to": msg.get("reply_to"),
                    "members": mgr.get_members(room), "online": mgr.count(room)
                })

            elif mtype == "typing":
                for c in mgr.rooms.get(room, []):
                    if c["ws"] != ws:
                        try:
                            await c["ws"].send_text(json.dumps({"type": "typing", "username": uname}))
                        except:
                            pass

            elif mtype == "reaction":
                await mgr.broadcast(room, {
                    "type": "reaction",
                    "msg_id": msg.get("msg_id"),
                    "emoji": msg.get("emoji"),
                    "username": uname
                })

            elif mtype == "create_room":
                rname = msg.get("name", "").strip().lower().replace(" ", "-")
                rlabel = msg.get("label", "").strip()
                is_priv = bool(msg.get("is_private", False))
                if not rname or not rlabel:
                    await ws.send_text(json.dumps({"type": "error", "text": "Nom invalide."}))
                    continue
                if count_user_rooms(uname, is_priv) >= 1:
                    kind = "privé" if is_priv else "public"
                    await ws.send_text(json.dumps({"type": "error", "text": f"Tu as déjà un salon {kind}."}))
                    continue
                if do_create_room(rname, rlabel, uname, is_priv):
                    await ws.send_text(json.dumps({"type": "room_created", "name": rname, "label": rlabel}))
                    await mgr.broadcast(room, {"type": "rooms", "rooms": get_rooms()})
                else:
                    await ws.send_text(json.dumps({"type": "error", "text": "Ce salon existe déjà."}))

            elif mtype == "invite":
                invitee = msg.get("username", "").strip()
                ok, err = do_invite(room, uname, invitee)
                if ok:
                    await ws.send_text(json.dumps({"type": "info", "text": f"{invitee} a été invité !"}))
                    await mgr.send_to_user(invitee, {"type": "invited", "room": room, "by": uname})
                else:
                    await ws.send_text(json.dumps({"type": "error", "text": err}))

    except WebSocketDisconnect:
        uname = mgr.disconnect(ws, room)
        if uname:
            sys_text = f"{uname} a quitté le salon"
            save_message(room, "system", "", sys_text, "system")
            await mgr.broadcast(room, {
                "type": "system", "text": sys_text, "time": now_time(),
                "members": mgr.get_members(room), "online": mgr.count(room)
            })

@app.get("/")
async def root():
    with open("index.html", "r", encoding="utf-8") as f:
        return HTMLResponse(f.read())
