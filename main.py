from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
import json, sqlite3, base64, os
from datetime import datetime, timedelta

app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

DB = "chat.db"

def now_time():
    return datetime.now().strftime("%H:%M")

def now_full():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

def get_db():
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    return con

def init_db():
    con = get_db()
    cur = con.cursor()
    cur.execute("""CREATE TABLE IF NOT EXISTS users (
        username TEXT PRIMARY KEY,
        color TEXT,
        avatar TEXT,
        status TEXT DEFAULT 'online',
        pseudo_changed INTEGER DEFAULT 0,
        keep_messages INTEGER DEFAULT 1,
        joined_at TEXT,
        last_active TEXT
    )""")
    cur.execute("""CREATE TABLE IF NOT EXISTS messages (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        room TEXT, username TEXT, color TEXT,
        text TEXT, msg_type TEXT DEFAULT 'message',
        edited INTEGER DEFAULT 0,
        deleted INTEGER DEFAULT 0,
        pinned INTEGER DEFAULT 0,
        created_at TEXT,
        edited_at TEXT
    )""")
    cur.execute("""CREATE TABLE IF NOT EXISTS rooms (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT UNIQUE, label TEXT, owner TEXT,
        is_private INTEGER DEFAULT 0,
        created_at TEXT
    )""")
    cur.execute("""CREATE TABLE IF NOT EXISTS room_members (
        room_name TEXT, username TEXT, role TEXT DEFAULT 'member',
        muted INTEGER DEFAULT 0, banned INTEGER DEFAULT 0,
        PRIMARY KEY (room_name, username)
    )""")
    cur.execute("""CREATE TABLE IF NOT EXISTS room_invites (
        room_name TEXT, username TEXT,
        PRIMARY KEY (room_name, username)
    )""")
    defaults = [("general","💬 General"),("idees","💡 Idées"),("design","🎨 Design"),("gaming","🎮 Gaming")]
    for name, label in defaults:
        cur.execute("INSERT OR IGNORE INTO rooms(name,label,owner,is_private,created_at) VALUES(?,?,?,?,?)",
                    (name, label, None, 0, now_full()))
    con.commit(); con.close()

def now_full():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

def now_time():
    return datetime.now().strftime("%H:%M")

def purge_old_messages():
    con = get_db()
    cutoff = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d %H:%M:%S")
    # Only delete messages for users who don't have keep_messages=1
    con.execute("""DELETE FROM messages WHERE created_at < ? AND username NOT IN (
        SELECT username FROM users WHERE keep_messages=1
    ) AND msg_type='message'""", (cutoff,))
    con.commit(); con.close()

def upsert_user(username, color):
    con = get_db()
    existing = con.execute("SELECT username FROM users WHERE username=?", (username,)).fetchone()
    if not existing:
        con.execute("INSERT INTO users(username,color,status,joined_at,last_active) VALUES(?,?,'online',?,?)",
                    (username, color, now_full(), now_full()))
    else:
        con.execute("UPDATE users SET status='online', last_active=?, color=? WHERE username=?",
                    (now_full(), color, username))
    con.commit(); con.close()

def update_last_active(username):
    con = get_db()
    con.execute("UPDATE users SET last_active=? WHERE username=?", (now_full(), username))
    con.commit(); con.close()

def get_user(username):
    con = get_db()
    row = con.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
    con.close()
    return dict(row) if row else None

def get_history(room, limit=60):
    con = get_db()
    rows = con.execute(
        """SELECT m.id, m.username, m.color, m.text, m.msg_type, m.edited, m.deleted, m.pinned, m.created_at,
           u.avatar, u.status
           FROM messages m LEFT JOIN users u ON m.username=u.username
           WHERE m.room=? ORDER BY m.id DESC LIMIT ?""",
        (room, limit)
    ).fetchall()
    con.close()
    result = []
    for r in reversed(rows):
        t = datetime.strptime(r['created_at'], "%Y-%m-%d %H:%M:%S").strftime("%H:%M")
        result.append({
            "id": r['id'], "username": r['username'], "color": r['color'],
            "text": "[message supprimé]" if r['deleted'] else r['text'],
            "type": r['msg_type'], "edited": bool(r['edited']),
            "deleted": bool(r['deleted']), "pinned": bool(r['pinned']),
            "time": t, "avatar": r['avatar']
        })
    return result

def get_pinned(room):
    con = get_db()
    row = con.execute("SELECT id,username,text,created_at FROM messages WHERE room=? AND pinned=1 ORDER BY id DESC LIMIT 1", (room,)).fetchone()
    con.close()
    if row:
        t = datetime.strptime(row['created_at'], "%Y-%m-%d %H:%M:%S").strftime("%H:%M")
        return {"id": row['id'], "username": row['username'], "text": row['text'], "time": t}
    return None

def save_message(room, username, color, text, msg_type="message"):
    con = get_db()
    cur = con.execute("INSERT INTO messages(room,username,color,text,msg_type,created_at) VALUES(?,?,?,?,?,?)",
                (room, username, color, text, msg_type, now_full()))
    msg_id = cur.lastrowid
    con.commit(); con.close()
    return msg_id

def get_rooms():
    con = get_db()
    rows = con.execute("SELECT name,label,owner,is_private FROM rooms ORDER BY id").fetchall()
    con.close()
    return [{"name": r['name'], "label": r['label'], "owner": r['owner'], "is_private": bool(r['is_private'])} for r in rows]

def can_access(room_name, username):
    con = get_db()
    row = con.execute("SELECT is_private,owner FROM rooms WHERE name=?", (room_name,)).fetchone()
    if not row: con.close(); return False
    if not row['is_private']: con.close(); return True
    if row['owner'] == username: con.close(); return True
    banned = con.execute("SELECT banned FROM room_members WHERE room_name=? AND username=?", (room_name, username)).fetchone()
    if banned and banned['banned']: con.close(); return False
    invite = con.execute("SELECT 1 FROM room_invites WHERE room_name=? AND username=?", (room_name, username)).fetchone()
    con.close()
    return invite is not None

def is_banned(room_name, username):
    con = get_db()
    row = con.execute("SELECT banned FROM room_members WHERE room_name=? AND username=?", (room_name, username)).fetchone()
    con.close()
    return bool(row and row['banned'])

def is_muted(room_name, username):
    con = get_db()
    row = con.execute("SELECT muted FROM room_members WHERE room_name=? AND username=?", (room_name, username)).fetchone()
    con.close()
    return bool(row and row['muted'])

def get_role(room_name, username):
    con = get_db()
    room = con.execute("SELECT owner FROM rooms WHERE name=?", (room_name,)).fetchone()
    if room and room['owner'] == username:
        con.close(); return 'owner'
    row = con.execute("SELECT role FROM room_members WHERE room_name=? AND username=?", (room_name, username)).fetchone()
    con.close()
    return row['role'] if row else 'member'

def count_admins(room_name):
    con = get_db()
    count = con.execute("SELECT COUNT(*) as c FROM room_members WHERE room_name=? AND role='admin'", (room_name,)).fetchone()['c']
    con.close()
    return count

def get_room_stats(room_name):
    con = get_db()
    msg_count = con.execute("SELECT COUNT(*) as c FROM messages WHERE room=? AND deleted=0 AND msg_type='message'", (room_name,)).fetchone()['c']
    member_count = con.execute("SELECT COUNT(*) as c FROM room_members WHERE room_name=? AND banned=0", (room_name,)).fetchone()['c']
    con.close()
    return {"messages": msg_count, "members": member_count + 1}

def count_user_rooms(username, is_private):
    con = get_db()
    count = con.execute("SELECT COUNT(*) as c FROM rooms WHERE owner=? AND is_private=?",
                        (username, 1 if is_private else 0)).fetchone()['c']
    con.close()
    return count

def do_create_room(name, label, owner, is_private):
    con = get_db()
    try:
        con.execute("INSERT INTO rooms(name,label,owner,is_private,created_at) VALUES(?,?,?,?,?)",
                    (name, label, owner, 1 if is_private else 0, now_full()))
        con.commit(); return True
    except: return False
    finally: con.close()

init_db()
purge_old_messages()

class Manager:
    def __init__(self):
        self.rooms: dict[str, list[dict]] = {}
        self.connected: dict[str, str] = {}

    async def connect(self, ws, room, username, color, avatar):
        await ws.accept()
        if room not in self.rooms:
            self.rooms[room] = []
        self.rooms[room].append({"ws": ws, "username": username, "color": color, "avatar": avatar})
        self.connected[username] = room

    def disconnect(self, ws, room):
        username = ""
        if room in self.rooms:
            for c in self.rooms[room]:
                if c["ws"] == ws:
                    username = c["username"]; break
            self.rooms[room] = [c for c in self.rooms[room] if c["ws"] != ws]
        if username in self.connected:
            del self.connected[username]
        return username

    def is_taken(self, username):
        return username in self.connected

    def get_members(self, room):
        return [{"username": c["username"], "color": c["color"], "avatar": c["avatar"]} for c in self.rooms.get(room, [])]

    def count(self, room):
        return len(self.rooms.get(room, []))

    def get_info(self, ws, room):
        for c in self.rooms.get(room, []):
            if c["ws"] == ws:
                return c["username"], c["color"], c["avatar"]
        return None, None, None

    def update_avatar(self, username, avatar):
        for room_list in self.rooms.values():
            for c in room_list:
                if c["username"] == username:
                    c["avatar"] = avatar

    async def broadcast(self, room, msg):
        dead = []
        for c in self.rooms.get(room, []):
            try: await c["ws"].send_text(json.dumps(msg))
            except: dead.append(c)
        for d in dead:
            if room in self.rooms and d in self.rooms[room]:
                self.rooms[room].remove(d)

    async def send_to(self, username, msg):
        room = self.connected.get(username)
        if room:
            for c in self.rooms.get(room, []):
                if c["username"] == username:
                    try: await c["ws"].send_text(json.dumps(msg))
                    except: pass

mgr = Manager()

@app.websocket("/ws/{room}/{username}/{color}")
async def endpoint(ws: WebSocket, room: str, username: str, color: str):
    color = "#" + color

    if mgr.is_taken(username):
        await ws.accept()
        await ws.send_text(json.dumps({"type": "error", "code": "username_taken",
                                       "text": f"Le pseudo '{username}' est déjà utilisé."}))
        await ws.close(); return

    if is_banned(room, username):
        await ws.accept()
        await ws.send_text(json.dumps({"type": "error", "code": "banned",
                                       "text": "Tu as été banni de ce salon."}))
        await ws.close(); return

    if not can_access(room, username):
        await ws.accept()
        await ws.send_text(json.dumps({"type": "error", "code": "access_denied",
                                       "text": "Tu n'as pas accès à ce salon privé."}))
        await ws.close(); return

    upsert_user(username, color)
    user = get_user(username)
    avatar = user.get('avatar') if user else None

    # Add to room_members if not there
    con = get_db()
    con.execute("INSERT OR IGNORE INTO room_members(room_name,username,role) VALUES(?,?,'member')", (room, username))
    con.commit(); con.close()

    await mgr.connect(ws, room, username, color, avatar)

    await ws.send_text(json.dumps({"type": "history", "messages": get_history(room)}))
    await ws.send_text(json.dumps({"type": "rooms", "rooms": get_rooms()}))
    await ws.send_text(json.dumps({"type": "my_role", "role": get_role(room, username)}))
    pinned = get_pinned(room)
    if pinned:
        await ws.send_text(json.dumps({"type": "pinned", "message": pinned}))
    await ws.send_text(json.dumps({"type": "my_profile", "user": user}))

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
            uname, ucolor, uavatar = mgr.get_info(ws, room)
            if not uname: break
            update_last_active(uname)
            mtype = msg.get("type", "message")

            if mtype == "message":
                text = msg.get("text", "").strip()
                if not text: continue
                if is_muted(room, uname):
                    await ws.send_text(json.dumps({"type": "error", "text": "Tu es en sourdine dans ce salon."}))
                    continue
                msg_id = save_message(room, uname, ucolor, text)
                await mgr.broadcast(room, {
                    "type": "message", "id": msg_id, "username": uname, "color": ucolor,
                    "avatar": uavatar, "text": text, "time": now_time(),
                    "reply_to": msg.get("reply_to"), "edited": False,
                    "members": mgr.get_members(room), "online": mgr.count(room)
                })

            elif mtype == "typing":
                typingCooldown = True
                for c in mgr.rooms.get(room, []):
                    if c["ws"] != ws:
                        try: await c["ws"].send_text(json.dumps({"type": "typing", "username": uname}))
                        except: pass

            elif mtype == "reaction":
                await mgr.broadcast(room, {"type": "reaction", "msg_id": msg.get("msg_id"),
                                           "emoji": msg.get("emoji"), "username": uname})

            elif mtype == "edit_message":
                msg_id = msg.get("msg_id")
                new_text = msg.get("text", "").strip()
                if not msg_id or not new_text: continue
                con = get_db()
                row = con.execute("SELECT username, created_at FROM messages WHERE id=?", (msg_id,)).fetchone()
                if row and row['username'] == uname:
                    created = datetime.strptime(row['created_at'], "%Y-%m-%d %H:%M:%S")
                    if (datetime.now() - created).seconds <= 300:
                        con.execute("UPDATE messages SET text=?, edited=1, edited_at=? WHERE id=?",
                                    (new_text, now_full(), msg_id))
                        con.commit()
                        await mgr.broadcast(room, {"type": "message_edited", "msg_id": msg_id,
                                                   "new_text": new_text, "username": uname})
                    else:
                        await ws.send_text(json.dumps({"type": "error", "text": "Modification impossible après 5 minutes."}))
                con.close()

            elif mtype == "delete_message":
                msg_id = msg.get("msg_id")
                con = get_db()
                row = con.execute("SELECT username FROM messages WHERE id=?", (msg_id,)).fetchone()
                role = get_role(room, uname)
                if row and (row['username'] == uname or role in ['admin','owner']):
                    con.execute("UPDATE messages SET deleted=1 WHERE id=?", (msg_id,))
                    con.commit()
                    await mgr.broadcast(room, {"type": "message_deleted", "msg_id": msg_id})
                con.close()

            elif mtype == "pin_message":
                role = get_role(room, uname)
                if role in ['admin', 'owner']:
                    msg_id = msg.get("msg_id")
                    con = get_db()
                    con.execute("UPDATE messages SET pinned=0 WHERE room=?", (room,))
                    con.execute("UPDATE messages SET pinned=1 WHERE id=?", (msg_id,))
                    con.commit(); con.close()
                    pinned = get_pinned(room)
                    await mgr.broadcast(room, {"type": "pinned", "message": pinned})
                else:
                    await ws.send_text(json.dumps({"type": "error", "text": "Action réservée aux admins."}))

            elif mtype == "create_room":
                rname = msg.get("name", "").strip().lower().replace(" ", "-")
                rlabel = msg.get("label", "").strip()
                is_priv = bool(msg.get("is_private", False))
                if not rname or not rlabel:
                    await ws.send_text(json.dumps({"type": "error", "text": "Nom invalide."})); continue
                if count_user_rooms(uname, is_priv) >= 1:
                    kind = "privé" if is_priv else "public"
                    await ws.send_text(json.dumps({"type": "error", "text": f"Tu as déjà un salon {kind}."})); continue
                if do_create_room(rname, rlabel, uname, is_priv):
                    await ws.send_text(json.dumps({"type": "room_created", "name": rname, "label": rlabel}))
                    await mgr.broadcast(room, {"type": "rooms", "rooms": get_rooms()})
                else:
                    await ws.send_text(json.dumps({"type": "error", "text": "Ce salon existe déjà."}))

            elif mtype == "invite":
                invitee = msg.get("username", "").strip()
                role = get_role(room, uname)
                if role not in ['admin', 'owner']:
                    await ws.send_text(json.dumps({"type": "error", "text": "Action réservée aux admins."})); continue
                con = get_db()
                con.execute("INSERT OR IGNORE INTO room_invites(room_name,username) VALUES(?,?)", (room, invitee))
                con.commit(); con.close()
                await ws.send_text(json.dumps({"type": "info", "text": f"{invitee} a été invité !"}))
                await mgr.send_to(invitee, {"type": "invited", "room": room, "by": uname})

            elif mtype == "add_admin":
                if get_role(room, uname) != 'owner':
                    await ws.send_text(json.dumps({"type": "error", "text": "Réservé au créateur."})); continue
                target = msg.get("username", "").strip()
                if count_admins(room) >= 2:
                    await ws.send_text(json.dumps({"type": "error", "text": "Maximum 2 admins par salon."})); continue
                con = get_db()
                con.execute("INSERT OR REPLACE INTO room_members(room_name,username,role) VALUES(?,?,'admin')", (room, target))
                con.commit(); con.close()
                await ws.send_text(json.dumps({"type": "info", "text": f"{target} est maintenant admin !"}))
                await mgr.send_to(target, {"type": "role_update", "room": room, "role": "admin"})

            elif mtype == "ban_admin":
                if get_role(room, uname) != 'owner':
                    await ws.send_text(json.dumps({"type": "error", "text": "Réservé au créateur."})); continue
                target = msg.get("username", "").strip()
                con = get_db()
                con.execute("UPDATE room_members SET role='member' WHERE room_name=? AND username=?", (room, target))
                con.commit(); con.close()
                await ws.send_text(json.dumps({"type": "info", "text": f"{target} n'est plus admin."}))

            elif mtype == "ban_member":
                role = get_role(room, uname)
                if role not in ['admin', 'owner']:
                    await ws.send_text(json.dumps({"type": "error", "text": "Réservé aux admins."})); continue
                target = msg.get("username", "").strip()
                if get_role(room, target) == 'owner':
                    await ws.send_text(json.dumps({"type": "error", "text": "Impossible de bannir le créateur."})); continue
                con = get_db()
                con.execute("INSERT OR REPLACE INTO room_members(room_name,username,role,banned) VALUES(?,?,'member',1)", (room, target))
                con.commit(); con.close()
                await mgr.send_to(target, {"type": "error", "code": "banned", "text": "Tu as été banni de ce salon."})
                await ws.send_text(json.dumps({"type": "info", "text": f"{target} a été banni."}))

            elif mtype == "mute_member":
                role = get_role(room, uname)
                if role not in ['admin', 'owner']:
                    await ws.send_text(json.dumps({"type": "error", "text": "Réservé aux admins."})); continue
                target = msg.get("username", "").strip()
                muted = bool(msg.get("muted", True))
                con = get_db()
                con.execute("INSERT OR IGNORE INTO room_members(room_name,username) VALUES(?,?)", (room, target))
                con.execute("UPDATE room_members SET muted=? WHERE room_name=? AND username=?", (1 if muted else 0, room, target))
                con.commit(); con.close()
                status = "mis en sourdine" if muted else "réactivé"
                await ws.send_text(json.dumps({"type": "info", "text": f"{target} a été {status}."}))

            elif mtype == "rename_room":
                if get_role(room, uname) != 'owner':
                    await ws.send_text(json.dumps({"type": "error", "text": "Réservé au créateur."})); continue
                new_label = msg.get("label", "").strip()
                if not new_label: continue
                con = get_db()
                con.execute("UPDATE rooms SET label=? WHERE name=?", (new_label, room))
                con.commit(); con.close()
                await mgr.broadcast(room, {"type": "rooms", "rooms": get_rooms(),
                                           "info": f"Salon renommé : {new_label}"})

            elif mtype == "delete_room":
                if get_role(room, uname) != 'owner':
                    await ws.send_text(json.dumps({"type": "error", "text": "Réservé au créateur."})); continue
                con = get_db()
                con.execute("DELETE FROM rooms WHERE name=?", (room,))
                con.commit(); con.close()
                await mgr.broadcast(room, {"type": "room_deleted", "room": room,
                                           "text": f"Le salon '{room}' a été supprimé."})

            elif mtype == "room_stats":
                role = get_role(room, uname)
                if role not in ['admin', 'owner']:
                    await ws.send_text(json.dumps({"type": "error", "text": "Réservé aux admins."})); continue
                stats = get_room_stats(room)
                await ws.send_text(json.dumps({"type": "room_stats", "stats": stats}))

            elif mtype == "update_status":
                new_status = msg.get("status", "online")
                con = get_db()
                con.execute("UPDATE users SET status=? WHERE username=?", (new_status, uname))
                con.commit(); con.close()
                await mgr.broadcast(room, {"type": "status_update", "username": uname, "status": new_status,
                                           "members": mgr.get_members(room)})

            elif mtype == "update_avatar":
                avatar_data = msg.get("avatar", "")
                con = get_db()
                con.execute("UPDATE users SET avatar=? WHERE username=?", (avatar_data, uname))
                con.commit(); con.close()
                mgr.update_avatar(uname, avatar_data)
                await mgr.broadcast(room, {"type": "avatar_update", "username": uname, "avatar": avatar_data,
                                           "members": mgr.get_members(room)})

            elif mtype == "change_pseudo":
                con = get_db()
                row = con.execute("SELECT pseudo_changed FROM users WHERE username=?", (uname,)).fetchone()
                if row and row['pseudo_changed']:
                    await ws.send_text(json.dumps({"type": "error", "text": "Tu as déjà changé ton pseudo une fois."}))
                    con.close(); continue
                new_pseudo = msg.get("pseudo", "").strip()
                if not new_pseudo or mgr.is_taken(new_pseudo):
                    await ws.send_text(json.dumps({"type": "error", "text": "Pseudo invalide ou déjà utilisé."}))
                    con.close(); continue
                con.execute("UPDATE users SET username=?, pseudo_changed=1 WHERE username=?", (new_pseudo, uname))
                con.commit(); con.close()
                await ws.send_text(json.dumps({"type": "pseudo_changed", "new_pseudo": new_pseudo,
                                               "text": "Pseudo changé ! Reconnecte-toi avec ton nouveau pseudo."}))

            elif mtype == "get_profile":
                target = msg.get("username", "").strip()
                con = get_db()
                row = con.execute("SELECT username,color,avatar,status,joined_at,last_active FROM users WHERE username=?",
                                  (target,)).fetchone()
                con.close()
                if row:
                    la = row['last_active']
                    if la:
                        try:
                            diff = datetime.now() - datetime.strptime(la, "%Y-%m-%d %H:%M:%S")
                            if diff.seconds < 60: la_str = "À l'instant"
                            elif diff.seconds < 3600: la_str = f"Il y a {diff.seconds//60} min"
                            elif diff.days == 0: la_str = f"Il y a {diff.seconds//3600}h"
                            else: la_str = f"Il y a {diff.days} jour(s)"
                        except: la_str = la
                    else: la_str = "Inconnu"
                    ja = row['joined_at']
                    if ja:
                        try: ja_str = datetime.strptime(ja, "%Y-%m-%d %H:%M:%S").strftime("%d/%m/%Y")
                        except: ja_str = ja
                    else: ja_str = "Inconnu"
                    await ws.send_text(json.dumps({"type": "profile", "user": {
                        "username": row['username'], "color": row['color'],
                        "avatar": row['avatar'], "status": row['status'],
                        "joined_at": ja_str, "last_active": la_str
                    }}))

            elif mtype == "toggle_keep_messages":
                keep = bool(msg.get("keep", True))
                con = get_db()
                con.execute("UPDATE users SET keep_messages=? WHERE username=?", (1 if keep else 0, uname))
                con.commit(); con.close()
                await ws.send_text(json.dumps({"type": "info", "text": "Préférence sauvegardée."}))

    except WebSocketDisconnect:
        uname = mgr.disconnect(ws, room)
        if uname:
            con = get_db()
            con.execute("UPDATE users SET status='offline', last_active=? WHERE username=?", (now_full(), uname))
            con.commit(); con.close()
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
