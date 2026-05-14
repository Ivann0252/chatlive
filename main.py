from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, RedirectResponse
import json, httpx, os
from datetime import datetime, timedelta
from supabase import create_client, Client

app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID")
GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET")
APP_URL = os.getenv("APP_URL", "https://chatlive-78y8.onrender.com")
REDIRECT_URI = f"{APP_URL}/auth/callback"

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

def now_time(): return datetime.now().strftime("%H:%M")
def now_full(): return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

# ── USERS ──
def db_get_user_by_google(google_id):
    try:
        res = supabase.table("users").select("*").eq("google_id", google_id).execute()
        return res.data[0] if res.data else None
    except: return None

def db_get_user(username):
    try:
        res = supabase.table("users").select("*").eq("username", username).execute()
        return res.data[0] if res.data else None
    except: return None

def db_upsert_user(google_id, username, email, avatar):
    try:
        existing = db_get_user_by_google(google_id)
        if existing:
            supabase.table("users").update({"last_active": now_full(), "status": "online"}).eq("google_id", google_id).execute()
            return existing
        res = supabase.table("users").insert({
            "google_id": google_id, "username": username, "email": email,
            "avatar": avatar, "color": "#1D9E75", "accent_color": "#1D9E75",
            "status": "online", "bio": "", "pseudo_changed": False,
            "keep_messages": True, "ephemeral_days": 7,
            "notif_mentions": True, "notif_messages": True,
            "notif_dm": True, "notif_requests": True, "notif_join": True, "notif_sound": True,
            "joined_at": now_full(), "last_active": now_full(), "msg_count": 0
        }).execute()
        return res.data[0] if res.data else None
    except Exception as e:
        print(f"upsert error: {e}"); return None

# ── MESSAGES ──
def db_get_history(room, limit=60):
    try:
        res = supabase.table("messages").select("*").eq("room", room).eq("deleted", False).order("id", desc=True).limit(limit).execute()
        result = []
        for r in reversed(res.data or []):
            reply = None
            if r.get("reply_to"):
                try: reply = json.loads(r["reply_to"]) if isinstance(r["reply_to"], str) else r["reply_to"]
                except: reply = None
            result.append({
                "id": r["id"], "username": r["username"], "color": r.get("color", "#1D9E75"),
                "text": r.get("text", ""), "type": r.get("msg_type", "message"),
                "edited": r.get("edited", False), "deleted": False,
                "pinned": r.get("pinned", False),
                "time": r.get("created_at", "")[-8:-3] if r.get("created_at") else "",
                "avatar": r.get("avatar"), "reply_to": reply,
                "file_url": r.get("file_url"), "file_type": r.get("file_type")
            })
        return result
    except Exception as e:
        print(f"history error: {e}"); return []

def db_save_message(room, username, color, text, avatar=None, msg_type="message", reply_to=None, file_url=None, file_type=None):
    try:
        res = supabase.table("messages").insert({
            "room": room, "username": username, "color": color, "text": text,
            "avatar": avatar, "msg_type": msg_type,
            "reply_to": json.dumps(reply_to) if reply_to else None,
            "file_url": file_url, "file_type": file_type,
            "edited": False, "deleted": False, "pinned": False,
            "created_at": now_full()
        }).execute()
        try:
            u = supabase.table("users").select("msg_count").eq("username", username).execute()
            if u.data:
                supabase.table("users").update({"msg_count": (u.data[0].get("msg_count") or 0) + 1}).eq("username", username).execute()
        except: pass
        return res.data[0]["id"] if res.data else None
    except Exception as e:
        print(f"save_msg error: {e}"); return None

def db_get_pinned(room):
    try:
        res = supabase.table("messages").select("*").eq("room", room).eq("pinned", True).order("id", desc=True).limit(1).execute()
        if res.data:
            r = res.data[0]
            return {"id": r["id"], "username": r["username"], "text": r.get("text",""), "time": r.get("created_at","")[-8:-3]}
        return None
    except: return None

def db_purge_ephemeral():
    """Purge messages based on per-user ephemeral settings"""
    try:
        users = supabase.table("users").select("username,ephemeral_days,keep_messages").execute().data or []
        purged = {}
        for u in users:
            if u.get("keep_messages"): continue
            days = u.get("ephemeral_days") or 7
            cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
            res = supabase.table("messages").select("id,room").eq("username", u["username"]).lt("created_at", cutoff).eq("msg_type","message").eq("deleted", False).execute()
            ids = [r["id"] for r in (res.data or [])]
            if ids:
                supabase.table("messages").update({"deleted": True}).in_("id", ids).execute()
                purged[u["username"]] = len(ids)
        return purged
    except Exception as e:
        print(f"purge error: {e}"); return {}

# ── ROOMS ──
def db_init_rooms():
    defaults = [("general", "💬 General")]
    for name, label in defaults:
        try:
            if not supabase.table("rooms").select("name").eq("name", name).execute().data:
                supabase.table("rooms").insert({"name": name, "label": label, "owner": None, "is_private": False, "welcome_msg": None, "created_at": now_full()}).execute()
        except: pass

def db_get_rooms():
    try:
        res = supabase.table("rooms").select("*").order("id").execute()
        return res.data or []
    except: return []

def db_can_access(room, username):
    try:
        res = supabase.table("rooms").select("is_private,owner").eq("name", room).execute()
        if not res.data: return True
        r = res.data[0]
        if not r["is_private"]: return True
        if r["owner"] == username: return True
        inv = supabase.table("room_invites").select("id").eq("room_name", room).eq("username", username).execute()
        return bool(inv.data)
    except: return True

def db_is_banned(room, username):
    try:
        res = supabase.table("room_members").select("banned").eq("room_name", room).eq("username", username).execute()
        return bool(res.data and res.data[0].get("banned"))
    except: return False

def db_is_muted(room, username):
    try:
        res = supabase.table("room_members").select("muted").eq("room_name", room).eq("username", username).execute()
        return bool(res.data and res.data[0].get("muted"))
    except: return False

def db_get_role(room, username):
    try:
        r = supabase.table("rooms").select("owner").eq("name", room).execute()
        if r.data and r.data[0].get("owner") == username: return "owner"
        m = supabase.table("room_members").select("role").eq("room_name", room).eq("username", username).execute()
        return m.data[0].get("role", "member") if m.data else "member"
    except: return "member"

def db_count_admins(room):
    try:
        res = supabase.table("room_members").select("id", count="exact").eq("room_name", room).eq("role", "admin").execute()
        return res.count or 0
    except: return 0

def db_get_room_stats(room):
    try:
        mc = supabase.table("messages").select("id", count="exact").eq("room", room).eq("deleted", False).execute()
        mem = supabase.table("room_members").select("id", count="exact").eq("room_name", room).eq("banned", False).execute()
        return {"messages": mc.count or 0, "members": (mem.count or 0) + 1}
    except: return {"messages": 0, "members": 1}

# ── DM ──
def dm_conv_id(u1, u2): return "__".join(sorted([u1, u2]))

def db_get_dm_history(conv_id, limit=60):
    try:
        res = supabase.table("dm_messages").select("*").eq("conversation_id", conv_id).order("id", desc=True).limit(limit).execute()
        result = []
        for r in reversed(res.data or []):
            result.append({
                "id": r["id"], "from_user": r["from_user"], "to_user": r["to_user"],
                "text": "[supprimé]" if r.get("deleted") else r.get("text",""),
                "deleted": r.get("deleted", False), "avatar": r.get("avatar"),
                "color": r.get("color"), "time": r.get("created_at","")[-8:-3]
            })
        return result
    except: return []

def db_save_dm(conv_id, from_user, to_user, text, avatar, color):
    try:
        res = supabase.table("dm_messages").insert({
            "conversation_id": conv_id, "from_user": from_user, "to_user": to_user,
            "text": text, "avatar": avatar, "color": color,
            "deleted": False, "created_at": now_full()
        }).execute()
        return res.data[0]["id"] if res.data else None
    except: return None

def db_get_my_dms(username):
    try:
        res = supabase.table("dm_requests").select("*").or_(f"from_user.eq.{username},to_user.eq.{username}").eq("status", "accepted").execute()
        return res.data or []
    except: return []

def db_get_pending(username):
    try:
        res = supabase.table("dm_requests").select("*").eq("to_user", username).eq("status", "pending").execute()
        return res.data or []
    except: return []

db_init_rooms()

# ── MANAGER ──
class Manager:
    def __init__(self):
        self.rooms: dict[str, list[dict]] = {}
        self.connected: dict[str, str] = {}  # username -> room

    def force_disconnect(self, username):
        """Remove user from all rooms without closing WS"""
        room = self.connected.get(username)
        if room and room in self.rooms:
            self.rooms[room] = [c for c in self.rooms[room] if c["username"] != username]
        if username in self.connected:
            del self.connected[username]

    async def connect(self, ws, room, username, color, avatar):
        await ws.accept()
        if room not in self.rooms: self.rooms[room] = []
        self.rooms[room].append({"ws": ws, "username": username, "color": color, "avatar": avatar})
        self.connected[username] = room

    def disconnect(self, ws, room):
        username = ""
        if room in self.rooms:
            for c in self.rooms[room]:
                if c["ws"] == ws: username = c["username"]; break
            self.rooms[room] = [c for c in self.rooms[room] if c["ws"] != ws]
        if username and self.connected.get(username) == room:
            del self.connected[username]
        return username

    def is_taken(self, username): return username in self.connected
    def get_members(self, room): return [{"username": c["username"], "color": c["color"], "avatar": c["avatar"]} for c in self.rooms.get(room, [])]
    def count(self, room): return len(self.rooms.get(room, []))

    def get_info(self, ws, room):
        for c in self.rooms.get(room, []):
            if c["ws"] == ws: return c["username"], c["color"], c.get("avatar","")
        return None, None, None

    def update_avatar(self, username, avatar):
        for rl in self.rooms.values():
            for c in rl:
                if c["username"] == username: c["avatar"] = avatar

    async def broadcast(self, room, msg):
        dead = []
        for c in self.rooms.get(room, []):
            try: await c["ws"].send_text(json.dumps(msg))
            except: dead.append(c)
        for d in dead:
            if room in self.rooms and d in self.rooms[room]: self.rooms[room].remove(d)

    async def send_to(self, username, msg):
        room = self.connected.get(username)
        if room:
            for c in self.rooms.get(room, []):
                if c["username"] == username:
                    try: await c["ws"].send_text(json.dumps(msg))
                    except: pass

mgr = Manager()

# ── GOOGLE AUTH ──
@app.get("/auth/login")
async def auth_login():
    from urllib.parse import urlencode
    params = {"client_id": GOOGLE_CLIENT_ID, "redirect_uri": REDIRECT_URI, "response_type": "code", "scope": "openid email profile", "access_type": "offline"}
    return RedirectResponse("https://accounts.google.com/o/oauth2/v2/auth?" + urlencode(params))

@app.get("/auth/callback")
async def auth_callback(code: str = None, error: str = None):
    if error or not code: return RedirectResponse("/?error=auth_failed")
    async with httpx.AsyncClient() as client:
        t = await client.post("https://oauth2.googleapis.com/token", data={"code": code, "client_id": GOOGLE_CLIENT_ID, "client_secret": GOOGLE_CLIENT_SECRET, "redirect_uri": REDIRECT_URI, "grant_type": "authorization_code"})
        tokens = t.json()
        access_token = tokens.get("access_token")
        if not access_token: return RedirectResponse("/?error=no_token")
        u = await client.get("https://www.googleapis.com/oauth2/v3/userinfo", headers={"Authorization": f"Bearer {access_token}"})
        info = u.json()
    google_id = info.get("sub")
    username = info.get("name", "User").replace(" ", "_")[:20]
    email = info.get("email", "")
    avatar = info.get("picture", "")
    user = db_upsert_user(google_id, username, email, avatar)
    if not user: return RedirectResponse("/?error=db_error")
    from urllib.parse import urlencode
    p = urlencode({"username": user.get("username", username), "color": user.get("color", "#1D9E75"), "accent": user.get("accent_color", "#1D9E75"), "avatar": avatar})
    return RedirectResponse(f"/?{p}")

# ── MAIN WS ──
@app.websocket("/ws/{room}/{username}/{color}")
async def ws_main(ws: WebSocket, room: str, username: str, color: str):
    color = "#" + color

    # Force disconnect old session if exists
    if mgr.is_taken(username):
        mgr.force_disconnect(username)

    if db_is_banned(room, username):
        await ws.accept()
        await ws.send_text(json.dumps({"type": "error", "code": "banned", "text": "Tu as été banni de ce salon."}))
        await ws.close(); return

    if not db_can_access(room, username):
        await ws.accept()
        await ws.send_text(json.dumps({"type": "error", "code": "access_denied", "text": "Accès refusé à ce salon privé."}))
        await ws.close(); return

    user = db_get_user(username) or {}
    avatar = user.get("avatar") or ""

    try: supabase.table("users").update({"last_active": now_full(), "status": "online"}).eq("username", username).execute()
    except: pass
    try: supabase.table("room_members").upsert({"room_name": room, "username": username, "role": "member"}, on_conflict="room_name,username").execute()
    except: pass

    await mgr.connect(ws, room, username, color, avatar)

    # Send initial data
    await ws.send_text(json.dumps({"type": "history", "messages": db_get_history(room)}))
    await ws.send_text(json.dumps({"type": "rooms", "rooms": db_get_rooms()}))
    await ws.send_text(json.dumps({"type": "my_role", "role": db_get_role(room, username)}))
    await ws.send_text(json.dumps({"type": "my_profile", "user": user}))
    pinned = db_get_pinned(room)
    if pinned: await ws.send_text(json.dumps({"type": "pinned", "message": pinned}))

    # Welcome + room info (only for this user)
    try:
        room_data_res = supabase.table("rooms").select("label,welcome_msg").eq("name", room).execute()
        room_label = room_data_res.data[0].get("label", room) if room_data_res.data else room
        welcome_msg = room_data_res.data[0].get("welcome_msg") if room_data_res.data else None
    except:
        try:
            room_data_res = supabase.table("rooms").select("label").eq("name", room).execute()
            room_label = room_data_res.data[0].get("label", room) if room_data_res.data else room
        except:
            room_label = room
        welcome_msg = None
    await ws.send_text(json.dumps({"type": "self_join", "room_label": room_label, "welcome_msg": welcome_msg}))

    # Broadcast join to others
    await mgr.broadcast(room, {"type": "member_join", "username": username, "members": mgr.get_members(room), "online": mgr.count(room)})

    try:
        while True:
            data = await ws.receive_text()
            msg = json.loads(data)
            uname, ucolor, uavatar = mgr.get_info(ws, room)
            if not uname: break

            try: supabase.table("users").update({"last_active": now_full()}).eq("username", uname).execute()
            except: pass

            mtype = msg.get("type", "message")

            if mtype == "message":
                text = msg.get("text", "").strip()
                if not text and not msg.get("file_url"): continue
                if db_is_muted(room, uname):
                    await ws.send_text(json.dumps({"type": "error", "text": "Tu es en sourdine."})); continue
                reply_to = msg.get("reply_to")
                file_url = msg.get("file_url")
                file_type = msg.get("file_type")
                msg_id = db_save_message(room, uname, ucolor, text, uavatar, reply_to=reply_to, file_url=file_url, file_type=file_type)
                payload = {
                    "type": "message", "id": msg_id, "username": uname, "color": ucolor,
                    "avatar": uavatar, "text": text, "time": now_time(),
                    "reply_to": reply_to, "edited": False,
                    "file_url": file_url, "file_type": file_type,
                    "members": mgr.get_members(room), "online": mgr.count(room)
                }
                await mgr.broadcast(room, payload)
                # Check mentions
                if "@" in text:
                    import re
                    for m_user in re.findall(r'@(\w+)', text):
                        if m_user != uname:
                            await mgr.send_to(m_user, {"type": "mention", "from": uname, "room": room, "room_label": room_label, "text": text[:80]})

            elif mtype == "typing":
                for c in mgr.rooms.get(room, []):
                    if c["ws"] != ws:
                        try: await c["ws"].send_text(json.dumps({"type": "typing", "username": uname}))
                        except: pass

            elif mtype == "reaction":
                await mgr.broadcast(room, {"type": "reaction", "msg_id": msg.get("msg_id"), "emoji": msg.get("emoji"), "username": uname})

            elif mtype == "edit_message":
                msg_id = msg.get("msg_id")
                new_text = msg.get("text", "").strip()
                if not msg_id or not new_text: continue
                try:
                    orig = supabase.table("messages").select("username,created_at").eq("id", msg_id).execute()
                    if orig.data and orig.data[0]["username"] == uname:
                        created = datetime.strptime(orig.data[0]["created_at"], "%Y-%m-%d %H:%M:%S")
                        if (datetime.now() - created).seconds <= 300:
                            supabase.table("messages").update({"text": new_text, "edited": True}).eq("id", msg_id).execute()
                            await mgr.broadcast(room, {"type": "message_edited", "msg_id": msg_id, "new_text": new_text})
                        else:
                            await ws.send_text(json.dumps({"type": "error", "text": "Modification impossible après 5 min."}))
                except: pass

            elif mtype == "delete_message":
                msg_id = msg.get("msg_id")
                try:
                    orig = supabase.table("messages").select("username").eq("id", msg_id).execute()
                    role = db_get_role(room, uname)
                    if orig.data and (orig.data[0]["username"] == uname or role in ["admin", "owner"]):
                        supabase.table("messages").update({"deleted": True}).eq("id", msg_id).execute()
                        await mgr.broadcast(room, {"type": "message_deleted", "msg_id": msg_id})
                except: pass

            elif mtype == "pin_message":
                if db_get_role(room, uname) in ["admin", "owner"]:
                    msg_id = msg.get("msg_id")
                    try:
                        supabase.table("messages").update({"pinned": False}).eq("room", room).execute()
                        supabase.table("messages").update({"pinned": True}).eq("id", msg_id).execute()
                        await mgr.broadcast(room, {"type": "pinned", "message": db_get_pinned(room)})
                    except: pass
                else:
                    await ws.send_text(json.dumps({"type": "error", "text": "Réservé aux admins."}))

            elif mtype == "create_room":
                rname = msg.get("name", "").strip().lower().replace(" ", "-")
                rlabel = msg.get("label", "").strip()
                welcome = msg.get("welcome_msg", "").strip()
                if not rname or not rlabel:
                    await ws.send_text(json.dumps({"type": "error", "text": "Nom invalide."})); continue
                try:
                    # Limit: 1 private room per user
                    cnt = supabase.table("rooms").select("id", count="exact").eq("owner", uname).eq("is_private", True).execute()
                    if (cnt.count or 0) >= 1:
                        await ws.send_text(json.dumps({"type": "error", "text": "Tu as déjà un salon privé. Tu ne peux en créer qu'un seul."})); continue
                    supabase.table("rooms").insert({"name": rname, "label": rlabel, "owner": uname, "is_private": True, "welcome_msg": welcome or None, "created_at": now_full()}).execute()
                    await ws.send_text(json.dumps({"type": "room_created", "name": rname, "label": rlabel}))
                    await mgr.broadcast(room, {"type": "rooms", "rooms": db_get_rooms()})
                except:
                    await ws.send_text(json.dumps({"type": "error", "text": "Ce nom de salon existe déjà."}))

            elif mtype == "invite":
                if db_get_role(room, uname) not in ["admin", "owner"]:
                    await ws.send_text(json.dumps({"type": "error", "text": "Réservé aux admins."})); continue
                invitee = msg.get("username", "").strip()
                try:
                    supabase.table("room_invites").upsert({"room_name": room, "username": invitee}, on_conflict="room_name,username").execute()
                    await ws.send_text(json.dumps({"type": "info", "text": f"{invitee} a été invité !"}))
                    await mgr.send_to(invitee, {"type": "invited", "room": room, "by": uname})
                except: pass

            elif mtype == "add_admin":
                if db_get_role(room, uname) != "owner":
                    await ws.send_text(json.dumps({"type": "error", "text": "Réservé au créateur."})); continue
                target = msg.get("username", "").strip()
                if db_count_admins(room) >= 2:
                    await ws.send_text(json.dumps({"type": "error", "text": "Maximum 2 admins."})); continue
                try:
                    supabase.table("room_members").upsert({"room_name": room, "username": target, "role": "admin"}, on_conflict="room_name,username").execute()
                    await ws.send_text(json.dumps({"type": "info", "text": f"{target} est admin !"}))
                    await mgr.send_to(target, {"type": "role_update", "room": room, "role": "admin"})
                except: pass

            elif mtype == "ban_admin":
                if db_get_role(room, uname) != "owner":
                    await ws.send_text(json.dumps({"type": "error", "text": "Réservé au créateur."})); continue
                target = msg.get("username", "").strip()
                try: supabase.table("room_members").update({"role": "member"}).eq("room_name", room).eq("username", target).execute()
                except: pass
                await ws.send_text(json.dumps({"type": "info", "text": f"{target} n'est plus admin."}))

            elif mtype == "ban_member":
                if db_get_role(room, uname) not in ["admin", "owner"]:
                    await ws.send_text(json.dumps({"type": "error", "text": "Réservé aux admins."})); continue
                target = msg.get("username", "").strip()
                try:
                    supabase.table("room_members").upsert({"room_name": room, "username": target, "role": "member", "banned": True}, on_conflict="room_name,username").execute()
                    await mgr.send_to(target, {"type": "error", "code": "banned", "text": "Tu as été banni."})
                    await ws.send_text(json.dumps({"type": "info", "text": f"{target} banni."}))
                except: pass

            elif mtype == "mute_member":
                if db_get_role(room, uname) not in ["admin", "owner"]:
                    await ws.send_text(json.dumps({"type": "error", "text": "Réservé aux admins."})); continue
                target = msg.get("username", "").strip()
                muted = bool(msg.get("muted", True))
                try:
                    supabase.table("room_members").upsert({"room_name": room, "username": target, "muted": muted}, on_conflict="room_name,username").execute()
                    status = "mis en sourdine" if muted else "réactivé"
                    await ws.send_text(json.dumps({"type": "info", "text": f"{target} {status}."}))
                except: pass

            elif mtype == "rename_room":
                if db_get_role(room, uname) != "owner":
                    await ws.send_text(json.dumps({"type": "error", "text": "Réservé au créateur."})); continue
                new_label = msg.get("label", "").strip()
                if new_label:
                    try:
                        supabase.table("rooms").update({"label": new_label}).eq("name", room).execute()
                        await mgr.broadcast(room, {"type": "rooms", "rooms": db_get_rooms()})
                    except: pass

            elif mtype == "set_welcome":
                if db_get_role(room, uname) != "owner":
                    await ws.send_text(json.dumps({"type": "error", "text": "Réservé au créateur."})); continue
                welcome = msg.get("welcome_msg", "").strip()
                try:
                    supabase.table("rooms").update({"welcome_msg": welcome or None}).eq("name", room).execute()
                    await ws.send_text(json.dumps({"type": "info", "text": "Message de bienvenue mis à jour !"}))
                except: pass

            elif mtype == "delete_room":
                if db_get_role(room, uname) != "owner":
                    await ws.send_text(json.dumps({"type": "error", "text": "Réservé au créateur."})); continue
                try:
                    supabase.table("rooms").delete().eq("name", room).execute()
                    await mgr.broadcast(room, {"type": "room_deleted", "room": room, "text": f"Salon '{room}' supprimé."})
                except: pass

            elif mtype == "clear_chat":
                if db_get_role(room, uname) != "owner":
                    await ws.send_text(json.dumps({"type": "error", "text": "Réservé au créateur."})); continue
                try:
                    supabase.table("messages").update({"deleted": True}).eq("room", room).execute()
                    await mgr.broadcast(room, {"type": "chat_cleared", "by": uname})
                except: pass

            elif mtype == "room_stats":
                if db_get_role(room, uname) not in ["admin", "owner"]: continue
                stats = db_get_room_stats(room)
                await ws.send_text(json.dumps({"type": "room_stats", "stats": stats}))

            elif mtype == "update_status":
                new_status = msg.get("status", "online")
                try: supabase.table("users").update({"status": new_status}).eq("username", uname).execute()
                except: pass
                await mgr.broadcast(room, {"type": "status_update", "username": uname, "status": new_status, "members": mgr.get_members(room)})

            elif mtype == "update_avatar":
                avatar_data = msg.get("avatar", "")
                try: supabase.table("users").update({"avatar": avatar_data}).eq("username", uname).execute()
                except: pass
                mgr.update_avatar(uname, avatar_data)
                await mgr.broadcast(room, {"type": "avatar_update", "username": uname, "avatar": avatar_data, "members": mgr.get_members(room)})

            elif mtype == "update_profile":
                updates = {}
                if "bio" in msg: updates["bio"] = str(msg["bio"])[:120]
                if "color" in msg: updates["color"] = msg["color"]
                if "accent_color" in msg: updates["accent_color"] = msg["accent_color"]
                if updates:
                    try: supabase.table("users").update(updates).eq("username", uname).execute()
                    except: pass
                    await ws.send_text(json.dumps({"type": "profile_updated", "updates": updates}))

            elif mtype == "update_settings":
                valid_keys = ["keep_messages", "ephemeral_days", "notif_mentions", "notif_messages", "notif_dm", "notif_requests", "notif_join", "notif_sound"]
                updates = {k: msg[k] for k in valid_keys if k in msg}
                if updates:
                    try: supabase.table("users").update(updates).eq("username", uname).execute()
                    except: pass
                    # If ephemeral settings changed, run purge and notify
                    if "ephemeral_days" in updates or "keep_messages" in updates:
                        purged = db_purge_ephemeral()
                        if uname in purged and purged[uname] > 0:
                            await ws.send_text(json.dumps({"type": "messages_purged", "count": purged[uname]}))

            elif mtype == "change_pseudo":
                try:
                    ur = supabase.table("users").select("pseudo_changed").eq("username", uname).execute()
                    if ur.data and ur.data[0].get("pseudo_changed"):
                        await ws.send_text(json.dumps({"type": "error", "text": "Tu as déjà changé ton pseudo une fois."})); continue
                    new_pseudo = msg.get("pseudo", "").strip()
                    if not new_pseudo or mgr.is_taken(new_pseudo):
                        await ws.send_text(json.dumps({"type": "error", "text": "Pseudo invalide ou déjà utilisé."})); continue
                    supabase.table("users").update({"username": new_pseudo, "pseudo_changed": True}).eq("username", uname).execute()
                    await ws.send_text(json.dumps({"type": "pseudo_changed", "text": "Pseudo changé ! Reconnecte-toi."}))
                except: pass

            elif mtype == "get_profile":
                target = msg.get("username", "").strip()
                try:
                    res = supabase.table("users").select("*").eq("username", target).execute()
                    if res.data:
                        u2 = res.data[0]
                        la = u2.get("last_active", "")
                        try:
                            diff = datetime.now() - datetime.strptime(la, "%Y-%m-%d %H:%M:%S")
                            if diff.seconds < 60: la_str = "À l'instant"
                            elif diff.seconds < 3600: la_str = f"Il y a {diff.seconds // 60} min"
                            elif diff.days == 0: la_str = f"Il y a {diff.seconds // 3600}h"
                            else: la_str = f"Il y a {diff.days} jour(s)"
                        except: la_str = la
                        ja = u2.get("joined_at", "")
                        try: ja_str = datetime.strptime(ja, "%Y-%m-%d %H:%M:%S").strftime("%d/%m/%Y")
                        except: ja_str = ja
                        await ws.send_text(json.dumps({"type": "profile", "user": {
                            "username": u2["username"], "color": u2.get("color", "#1D9E75"),
                            "accent_color": u2.get("accent_color", "#1D9E75"),
                            "avatar": u2.get("avatar"), "status": u2.get("status", "online"),
                            "bio": u2.get("bio", ""), "joined_at": ja_str, "last_active": la_str,
                            "email": u2.get("email", ""), "msg_count": u2.get("msg_count", 0)
                        }}))
                except: pass

    except WebSocketDisconnect:
        uname = mgr.disconnect(ws, room)
        if uname:
            try: supabase.table("users").update({"status": "offline", "last_active": now_full()}).eq("username", uname).execute()
            except: pass
            await mgr.broadcast(room, {"type": "member_leave", "username": uname, "members": mgr.get_members(room), "online": mgr.count(room)})

# ── DM WS ──
@app.websocket("/dm/{username}/{color}")
async def ws_dm(ws: WebSocket, username: str, color: str):
    color = "#" + color
    dm_key = username + "_dm"
    if dm_key in mgr.connected:
        del mgr.connected[dm_key]
    await ws.accept()
    mgr.connected[dm_key] = "dm"

    pending = db_get_pending(username)
    if pending: await ws.send_text(json.dumps({"type": "dm_pending_requests", "requests": pending}))
    dms = db_get_my_dms(username)
    await ws.send_text(json.dumps({"type": "dm_list", "dms": dms, "my_username": username}))

    try:
        while True:
            data = await ws.receive_text()
            msg = json.loads(data)
            mtype = msg.get("type")

            if mtype == "dm_request":
                target = msg.get("to", "").strip()
                if not target or target == username:
                    await ws.send_text(json.dumps({"type": "error", "text": "Pseudo invalide."})); continue
                try:
                    u = supabase.table("users").select("username").eq("username", target).execute()
                    if not u.data:
                        await ws.send_text(json.dumps({"type": "error", "text": f"'{target}' n'existe pas."})); continue
                    existing = supabase.table("dm_requests").select("*").or_(
                        f"and(from_user.eq.{username},to_user.eq.{target}),and(from_user.eq.{target},to_user.eq.{username})"
                    ).execute()
                    if existing.data:
                        s = existing.data[0].get("status")
                        if s == "accepted": await ws.send_text(json.dumps({"type": "error", "text": "Conversation déjà active."})); continue
                        if s == "pending": await ws.send_text(json.dumps({"type": "error", "text": "Demande déjà envoyée."})); continue
                    supabase.table("dm_requests").insert({"from_user": username, "to_user": target, "status": "pending", "created_at": now_full()}).execute()
                    await ws.send_text(json.dumps({"type": "info", "text": f"Demande envoyée à {target} !"}))
                    await mgr.send_to(target + "_dm", {"type": "dm_new_request", "from_user": username})
                except Exception as e:
                    await ws.send_text(json.dumps({"type": "error", "text": "Erreur."}))

            elif mtype == "dm_accept":
                from_user = msg.get("from_user", "").strip()
                try:
                    supabase.table("dm_requests").update({"status": "accepted"}).eq("from_user", from_user).eq("to_user", username).execute()
                    dms = db_get_my_dms(username)
                    await ws.send_text(json.dumps({"type": "dm_list", "dms": dms, "my_username": username}))
                    await mgr.send_to(from_user + "_dm", {"type": "dm_accepted", "by": username})
                except: pass

            elif mtype == "dm_refuse":
                from_user = msg.get("from_user", "").strip()
                try: supabase.table("dm_requests").update({"status": "refused"}).eq("from_user", from_user).eq("to_user", username).execute()
                except: pass

            elif mtype == "dm_open":
                target = msg.get("target", "").strip()
                conv_id = dm_conv_id(username, target)
                history = db_get_dm_history(conv_id)
                await ws.send_text(json.dumps({"type": "dm_history", "messages": history, "target": target, "conv_id": conv_id}))

            elif mtype == "dm_message":
                target = msg.get("to", "").strip()
                text = msg.get("text", "").strip()
                if not text or not target: continue
                try:
                    req = supabase.table("dm_requests").select("status").or_(
                        f"and(from_user.eq.{username},to_user.eq.{target}),and(from_user.eq.{target},to_user.eq.{username})"
                    ).execute()
                    if not req.data or req.data[0].get("status") != "accepted":
                        await ws.send_text(json.dumps({"type": "error", "text": "Pas de conversation active."})); continue
                except: pass
                conv_id = dm_conv_id(username, target)
                try:
                    u2 = supabase.table("users").select("avatar,color").eq("username", username).execute()
                    ud = u2.data[0] if u2.data else {}
                except: ud = {}
                msg_id = db_save_dm(conv_id, username, target, text, ud.get("avatar"), ud.get("color", color))
                payload = {"type": "dm_message", "id": msg_id, "from_user": username, "to_user": target, "text": text, "time": now_time(), "avatar": ud.get("avatar"), "color": ud.get("color", color), "conv_id": conv_id}
                await ws.send_text(json.dumps(payload))
                await mgr.send_to(target + "_dm", payload)

            elif mtype == "dm_typing":
                target = msg.get("to", "").strip()
                await mgr.send_to(target + "_dm", {"type": "dm_typing", "from_user": username})

            elif mtype == "dm_delete":
                msg_id = msg.get("msg_id")
                target = msg.get("to", "").strip()
                try:
                    supabase.table("dm_messages").update({"deleted": True}).eq("id", msg_id).eq("from_user", username).execute()
                    conv_id = dm_conv_id(username, target)
                    payload = {"type": "dm_deleted", "msg_id": msg_id, "conv_id": conv_id}
                    await ws.send_text(json.dumps(payload))
                    await mgr.send_to(target + "_dm", payload)
                except: pass

    except WebSocketDisconnect:
        if dm_key in mgr.connected: del mgr.connected[dm_key]

@app.get("/")
async def root():
    with open("index.html", "r", encoding="utf-8") as f:
        return HTMLResponse(f.read())
