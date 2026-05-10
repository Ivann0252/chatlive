from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, RedirectResponse
import json, httpx, os
from datetime import datetime
from supabase import create_client, Client

app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

# ── CONFIG ──
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID")
GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET")
APP_URL = os.getenv("APP_URL", "https://chatlive-78y8.onrender.com")
REDIRECT_URI = f"{APP_URL}/auth/callback"

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

def now_time():
    return datetime.now().strftime("%H:%M")

def now_full():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

# ── SUPABASE HELPERS ──
def db_get_user(google_id: str):
    try:
        res = supabase.table("users").select("*").eq("google_id", google_id).execute()
        return res.data[0] if res.data else None
    except: return None

def db_upsert_user(google_id, username, email, avatar, color="#1D9E75"):
    try:
        existing = db_get_user(google_id)
        if existing:
            supabase.table("users").update({
                "last_active": now_full(), "status": "online"
            }).eq("google_id", google_id).execute()
            return existing
        else:
            data = {
                "google_id": google_id, "username": username, "email": email,
                "avatar": avatar, "color": color, "status": "online",
                "pseudo_changed": False, "keep_messages": True,
                "joined_at": now_full(), "last_active": now_full()
            }
            res = supabase.table("users").insert(data).execute()
            return res.data[0] if res.data else None
    except Exception as e:
        print(f"upsert_user error: {e}")
        return None

def db_get_history(room, limit=60):
    try:
        res = supabase.table("messages").select("*").eq("room", room).order("id", desc=True).limit(limit).execute()
        result = []
        for r in reversed(res.data or []):
            result.append({
                "id": r.get("id"), "username": r.get("username"), "color": r.get("color"),
                "text": "[message supprimé]" if r.get("deleted") else r.get("text"),
                "type": r.get("msg_type", "message"), "edited": r.get("edited", False),
                "deleted": r.get("deleted", False), "pinned": r.get("pinned", False),
                "time": r.get("created_at", "")[-8:-3] if r.get("created_at") else "",
                "avatar": r.get("avatar"), "reply_to": r.get("reply_to")
            })
        return result
    except Exception as e:
        print(f"get_history error: {e}")
        return []

def db_save_message(room, username, color, text, avatar=None, msg_type="message", reply_to=None):
    try:
        data = {
            "room": room, "username": username, "color": color, "text": text,
            "avatar": avatar, "msg_type": msg_type, "reply_to": json.dumps(reply_to) if reply_to else None,
            "edited": False, "deleted": False, "pinned": False, "created_at": now_full()
        }
        res = supabase.table("messages").insert(data).execute()
        return res.data[0]["id"] if res.data else None
    except Exception as e:
        print(f"save_message error: {e}")
        return None

def db_get_rooms():
    try:
        res = supabase.table("rooms").select("*").order("id").execute()
        return res.data or []
    except: return []

def db_init_rooms():
    defaults = [
        {"name": "general", "label": "💬 General", "is_private": False},
        {"name": "idees", "label": "💡 Idées", "is_private": False},
        {"name": "design", "label": "🎨 Design", "is_private": False},
        {"name": "gaming", "label": "🎮 Gaming", "is_private": False},
    ]
    for r in defaults:
        try:
            existing = supabase.table("rooms").select("name").eq("name", r["name"]).execute()
            if not existing.data:
                supabase.table("rooms").insert({**r, "owner": None, "created_at": now_full()}).execute()
        except: pass

def db_get_pinned(room):
    try:
        res = supabase.table("messages").select("*").eq("room", room).eq("pinned", True).order("id", desc=True).limit(1).execute()
        if res.data:
            r = res.data[0]
            return {"id": r["id"], "username": r["username"], "text": r["text"], "time": r.get("created_at","")[-8:-3]}
        return None
    except: return None

def db_get_role(room, username):
    try:
        room_data = supabase.table("rooms").select("owner").eq("name", room).execute()
        if room_data.data and room_data.data[0].get("owner") == username:
            return "owner"
        res = supabase.table("room_members").select("role").eq("room_name", room).eq("username", username).execute()
        if res.data:
            return res.data[0].get("role", "member")
        return "member"
    except: return "member"

def db_can_access(room, username):
    try:
        res = supabase.table("rooms").select("is_private,owner").eq("name", room).execute()
        if not res.data: return False
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

def db_count_admins(room):
    try:
        res = supabase.table("room_members").select("id").eq("room_name", room).eq("role", "admin").execute()
        return len(res.data or [])
    except: return 0

def db_count_user_rooms(username, is_private):
    try:
        res = supabase.table("rooms").select("id").eq("owner", username).eq("is_private", is_private).execute()
        return len(res.data or [])
    except: return 0

def db_purge_old_messages():
    try:
        from datetime import timedelta
        cutoff = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d %H:%M:%S")
        keep_users_res = supabase.table("users").select("username").eq("keep_messages", True).execute()
        keep_users = [u["username"] for u in (keep_users_res.data or [])]
        res = supabase.table("messages").select("id,username").lt("created_at", cutoff).eq("msg_type", "message").execute()
        to_delete = [r["id"] for r in (res.data or []) if r["username"] not in keep_users]
        for mid in to_delete:
            supabase.table("messages").delete().eq("id", mid).execute()
    except Exception as e:
        print(f"purge error: {e}")

db_init_rooms()
db_purge_old_messages()

# ── GOOGLE AUTH ──
@app.get("/auth/login")
async def auth_login():
    params = {
        "client_id": GOOGLE_CLIENT_ID,
        "redirect_uri": REDIRECT_URI,
        "response_type": "code",
        "scope": "openid email profile",
        "access_type": "offline",
    }
    from urllib.parse import urlencode
    url = "https://accounts.google.com/o/oauth2/v2/auth?" + urlencode(params)
    return RedirectResponse(url)

@app.get("/auth/callback")
async def auth_callback(code: str = None, error: str = None):
    if error or not code:
        return RedirectResponse(f"/?error=auth_failed")
    async with httpx.AsyncClient() as client:
        token_res = await client.post("https://oauth2.googleapis.com/token", data={
            "code": code, "client_id": GOOGLE_CLIENT_ID,
            "client_secret": GOOGLE_CLIENT_SECRET,
            "redirect_uri": REDIRECT_URI, "grant_type": "authorization_code"
        })
        tokens = token_res.json()
        access_token = tokens.get("access_token")
        if not access_token:
            return RedirectResponse("/?error=no_token")
        user_res = await client.get("https://www.googleapis.com/oauth2/v3/userinfo",
                                    headers={"Authorization": f"Bearer {access_token}"})
        user_info = user_res.json()

    google_id = user_info.get("sub")
    username = user_info.get("name", "User").replace(" ", "_")[:20]
    email = user_info.get("email", "")
    avatar = user_info.get("picture", "")

    user = db_upsert_user(google_id, username, email, avatar)
    if not user:
        return RedirectResponse("/?error=db_error")

    from urllib.parse import urlencode
    params = urlencode({
        "username": user.get("username", username),
        "color": user.get("color", "#1D9E75"),
        "avatar": avatar,
        "google_id": google_id
    })
    return RedirectResponse(f"/?{params}")

# ── WS MANAGER ──
class Manager:
    def __init__(self):
        self.rooms: dict[str, list[dict]] = {}
        self.connected: dict[str, str] = {}

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
        if username in self.connected: del self.connected[username]
        return username

    def is_taken(self, username):
        return username in self.connected

    def get_members(self, room):
        return [{"username": c["username"], "color": c["color"], "avatar": c["avatar"]} for c in self.rooms.get(room, [])]

    def count(self, room):
        return len(self.rooms.get(room, []))

    def get_info(self, ws, room):
        for c in self.rooms.get(room, []):
            if c["ws"] == ws: return c["username"], c["color"], c["avatar"]
        return None, None, None

    def update_avatar(self, username, avatar):
        for room_list in self.rooms.values():
            for c in room_list:
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

@app.websocket("/ws/{room}/{username}/{color}")
async def endpoint(ws: WebSocket, room: str, username: str, color: str):
    color = "#" + color

    if mgr.is_taken(username):
        await ws.accept()
        await ws.send_text(json.dumps({"type": "error", "code": "username_taken", "text": f"Le pseudo '{username}' est déjà utilisé."}))
        await ws.close(); return

    if db_is_banned(room, username):
        await ws.accept()
        await ws.send_text(json.dumps({"type": "error", "code": "banned", "text": "Tu as été banni de ce salon."}))
        await ws.close(); return

    if not db_can_access(room, username):
        await ws.accept()
        await ws.send_text(json.dumps({"type": "error", "code": "access_denied", "text": "Tu n'as pas accès à ce salon privé."}))
        await ws.close(); return

    user = db_get_user(username) or {}
    avatar = user.get("avatar") or ""

    try:
        supabase.table("room_members").upsert({"room_name": room, "username": username, "role": "member"}, on_conflict="room_name,username").execute()
    except: pass

    await mgr.connect(ws, room, username, color, avatar)
    await ws.send_text(json.dumps({"type": "history", "messages": db_get_history(room)}))
    await ws.send_text(json.dumps({"type": "rooms", "rooms": db_get_rooms()}))
    await ws.send_text(json.dumps({"type": "my_role", "role": db_get_role(room, username)}))
    pinned = db_get_pinned(room)
    if pinned: await ws.send_text(json.dumps({"type": "pinned", "message": pinned}))
    await ws.send_text(json.dumps({"type": "my_profile", "user": user}))

    sys_text = f"{username} a rejoint le salon"
    db_save_message(room, "system", "", sys_text, msg_type="system")
    await mgr.broadcast(room, {"type": "system", "text": sys_text, "time": now_time(), "members": mgr.get_members(room), "online": mgr.count(room)})

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
                if not text: continue
                if db_is_muted(room, uname):
                    await ws.send_text(json.dumps({"type": "error", "text": "Tu es en sourdine."})); continue
                reply_to = msg.get("reply_to")
                msg_id = db_save_message(room, uname, ucolor, text, uavatar, reply_to=reply_to)
                await mgr.broadcast(room, {"type": "message", "id": msg_id, "username": uname, "color": ucolor, "avatar": uavatar, "text": text, "time": now_time(), "reply_to": reply_to, "edited": False, "members": mgr.get_members(room), "online": mgr.count(room)})

            elif mtype == "typing":
                for c in mgr.rooms.get(room, []):
                    if c["ws"] != ws:
                        try: await c["ws"].send_text(json.dumps({"type": "typing", "username": uname}))
                        except: pass

            elif mtype == "reaction":
                await mgr.broadcast(room, {"type": "reaction", "msg_id": msg.get("msg_id"), "emoji": msg.get("emoji"), "username": uname})

            elif mtype == "edit_message":
                msg_id = msg.get("msg_id"); new_text = msg.get("text", "").strip()
                if not msg_id or not new_text: continue
                try:
                    orig = supabase.table("messages").select("username,created_at").eq("id", msg_id).execute()
                    if orig.data and orig.data[0]["username"] == uname:
                        created = datetime.strptime(orig.data[0]["created_at"], "%Y-%m-%d %H:%M:%S")
                        if (datetime.now() - created).seconds <= 300:
                            supabase.table("messages").update({"text": new_text, "edited": True}).eq("id", msg_id).execute()
                            await mgr.broadcast(room, {"type": "message_edited", "msg_id": msg_id, "new_text": new_text})
                        else: await ws.send_text(json.dumps({"type": "error", "text": "Modification impossible après 5 min."}))
                except: pass

            elif mtype == "delete_message":
                msg_id = msg.get("msg_id")
                try:
                    orig = supabase.table("messages").select("username").eq("id", msg_id).execute()
                    role = db_get_role(room, uname)
                    if orig.data and (orig.data[0]["username"] == uname or role in ["admin","owner"]):
                        supabase.table("messages").update({"deleted": True}).eq("id", msg_id).execute()
                        await mgr.broadcast(room, {"type": "message_deleted", "msg_id": msg_id})
                except: pass

            elif mtype == "pin_message":
                if db_get_role(room, uname) in ["admin","owner"]:
                    msg_id = msg.get("msg_id")
                    try:
                        supabase.table("messages").update({"pinned": False}).eq("room", room).execute()
                        supabase.table("messages").update({"pinned": True}).eq("id", msg_id).execute()
                        await mgr.broadcast(room, {"type": "pinned", "message": db_get_pinned(room)})
                    except: pass

            elif mtype == "create_room":
                rname = msg.get("name","").strip().lower().replace(" ","-")
                rlabel = msg.get("label","").strip()
                is_priv = bool(msg.get("is_private", False))
                if not rname or not rlabel: await ws.send_text(json.dumps({"type":"error","text":"Nom invalide."})); continue
                if db_count_user_rooms(uname, is_priv) >= 1:
                    kind = "privé" if is_priv else "public"
                    await ws.send_text(json.dumps({"type":"error","text":f"Tu as déjà un salon {kind}."})); continue
                try:
                    supabase.table("rooms").insert({"name":rname,"label":rlabel,"owner":uname,"is_private":is_priv,"created_at":now_full()}).execute()
                    await ws.send_text(json.dumps({"type":"room_created","name":rname,"label":rlabel}))
                    await mgr.broadcast(room, {"type":"rooms","rooms":db_get_rooms()})
                except: await ws.send_text(json.dumps({"type":"error","text":"Ce salon existe déjà."}))

            elif mtype == "invite":
                if db_get_role(room, uname) not in ["admin","owner"]: await ws.send_text(json.dumps({"type":"error","text":"Réservé aux admins."})); continue
                invitee = msg.get("username","").strip()
                try:
                    supabase.table("room_invites").upsert({"room_name":room,"username":invitee}, on_conflict="room_name,username").execute()
                    await ws.send_text(json.dumps({"type":"info","text":f"{invitee} a été invité !"}))
                    await mgr.send_to(invitee, {"type":"invited","room":room,"by":uname})
                except: pass

            elif mtype == "add_admin":
                if db_get_role(room, uname) != "owner": await ws.send_text(json.dumps({"type":"error","text":"Réservé au créateur."})); continue
                target = msg.get("username","").strip()
                if db_count_admins(room) >= 2: await ws.send_text(json.dumps({"type":"error","text":"Maximum 2 admins."})); continue
                try:
                    supabase.table("room_members").upsert({"room_name":room,"username":target,"role":"admin"}, on_conflict="room_name,username").execute()
                    await ws.send_text(json.dumps({"type":"info","text":f"{target} est admin !"}))
                    await mgr.send_to(target, {"type":"role_update","room":room,"role":"admin"})
                except: pass

            elif mtype == "ban_admin":
                if db_get_role(room, uname) != "owner": await ws.send_text(json.dumps({"type":"error","text":"Réservé au créateur."})); continue
                target = msg.get("username","").strip()
                try:
                    supabase.table("room_members").update({"role":"member"}).eq("room_name",room).eq("username",target).execute()
                    await ws.send_text(json.dumps({"type":"info","text":f"{target} n'est plus admin."}))
                except: pass

            elif mtype == "ban_member":
                if db_get_role(room, uname) not in ["admin","owner"]: await ws.send_text(json.dumps({"type":"error","text":"Réservé aux admins."})); continue
                target = msg.get("username","").strip()
                try:
                    supabase.table("room_members").upsert({"room_name":room,"username":target,"role":"member","banned":True}, on_conflict="room_name,username").execute()
                    await mgr.send_to(target, {"type":"error","code":"banned","text":"Tu as été banni."})
                    await ws.send_text(json.dumps({"type":"info","text":f"{target} banni."}))
                except: pass

            elif mtype == "mute_member":
                if db_get_role(room, uname) not in ["admin","owner"]: await ws.send_text(json.dumps({"type":"error","text":"Réservé aux admins."})); continue
                target = msg.get("username","").strip()
                muted = bool(msg.get("muted", True))
                try:
                    supabase.table("room_members").upsert({"room_name":room,"username":target,"muted":muted}, on_conflict="room_name,username").execute()
                    await ws.send_text(json.dumps({"type":"info","text":f"{target} {'mis en sourdine' if muted else 'réactivé'}."}))
                except: pass

            elif mtype == "rename_room":
                if db_get_role(room, uname) != "owner": await ws.send_text(json.dumps({"type":"error","text":"Réservé au créateur."})); continue
                new_label = msg.get("label","").strip()
                if new_label:
                    try:
                        supabase.table("rooms").update({"label":new_label}).eq("name",room).execute()
                        await mgr.broadcast(room, {"type":"rooms","rooms":db_get_rooms()})
                    except: pass

            elif mtype == "delete_room":
                if db_get_role(room, uname) != "owner": await ws.send_text(json.dumps({"type":"error","text":"Réservé au créateur."})); continue
                try:
                    supabase.table("rooms").delete().eq("name",room).execute()
                    await mgr.broadcast(room, {"type":"room_deleted","room":room,"text":f"Salon '{room}' supprimé."})
                except: pass

            elif mtype == "room_stats":
                if db_get_role(room, uname) not in ["admin","owner"]: continue
                try:
                    msgs = supabase.table("messages").select("id",count="exact").eq("room",room).eq("deleted",False).execute()
                    mems = supabase.table("room_members").select("id",count="exact").eq("room_name",room).eq("banned",False).execute()
                    await ws.send_text(json.dumps({"type":"room_stats","stats":{"messages":msgs.count or 0,"members":(mems.count or 0)+1}}))
                except: pass

            elif mtype == "update_status":
                try:
                    supabase.table("users").update({"status":msg.get("status","online")}).eq("username",uname).execute()
                    await mgr.broadcast(room, {"type":"status_update","username":uname,"status":msg.get("status"),"members":mgr.get_members(room)})
                except: pass

            elif mtype == "update_avatar":
                avatar_data = msg.get("avatar","")
                try:
                    supabase.table("users").update({"avatar":avatar_data}).eq("username",uname).execute()
                    mgr.update_avatar(uname, avatar_data)
                    await mgr.broadcast(room, {"type":"avatar_update","username":uname,"avatar":avatar_data,"members":mgr.get_members(room)})
                except: pass

            elif mtype == "change_pseudo":
                try:
                    user_row = supabase.table("users").select("pseudo_changed").eq("username",uname).execute()
                    if user_row.data and user_row.data[0].get("pseudo_changed"):
                        await ws.send_text(json.dumps({"type":"error","text":"Tu as déjà changé ton pseudo une fois."})); continue
                    new_pseudo = msg.get("pseudo","").strip()
                    if not new_pseudo or mgr.is_taken(new_pseudo):
                        await ws.send_text(json.dumps({"type":"error","text":"Pseudo invalide ou déjà utilisé."})); continue
                    supabase.table("users").update({"username":new_pseudo,"pseudo_changed":True}).eq("username",uname).execute()
                    await ws.send_text(json.dumps({"type":"pseudo_changed","new_pseudo":new_pseudo,"text":"Pseudo changé ! Reconnecte-toi."}))
                except: pass

            elif mtype == "get_profile":
                target = msg.get("username","").strip()
                try:
                    res = supabase.table("users").select("*").eq("username",target).execute()
                    if res.data:
                        u = res.data[0]
                        la = u.get("last_active","")
                        if la:
                            try:
                                diff = datetime.now() - datetime.strptime(la, "%Y-%m-%d %H:%M:%S")
                                if diff.seconds < 60: la_str = "À l'instant"
                                elif diff.seconds < 3600: la_str = f"Il y a {diff.seconds//60} min"
                                elif diff.days == 0: la_str = f"Il y a {diff.seconds//3600}h"
                                else: la_str = f"Il y a {diff.days} jour(s)"
                            except: la_str = la
                        else: la_str = "Inconnu"
                        ja = u.get("joined_at","")
                        try: ja_str = datetime.strptime(ja, "%Y-%m-%d %H:%M:%S").strftime("%d/%m/%Y")
                        except: ja_str = ja
                        await ws.send_text(json.dumps({"type":"profile","user":{"username":u["username"],"color":u.get("color","#1D9E75"),"avatar":u.get("avatar"),"status":u.get("status","online"),"joined_at":ja_str,"last_active":la_str,"email":u.get("email","")}}))
                except: pass

            elif mtype == "toggle_keep_messages":
                keep = bool(msg.get("keep", True))
                try:
                    supabase.table("users").update({"keep_messages":keep}).eq("username",uname).execute()
                    await ws.send_text(json.dumps({"type":"info","text":"Préférence sauvegardée."}))
                except: pass

    except WebSocketDisconnect:
        uname = mgr.disconnect(ws, room)
        if uname:
            try: supabase.table("users").update({"status":"offline","last_active":now_full()}).eq("username",uname).execute()
            except: pass
            sys_text = f"{uname} a quitté le salon"
            db_save_message(room, "system", "", sys_text, msg_type="system")
            await mgr.broadcast(room, {"type":"system","text":sys_text,"time":now_time(),"members":mgr.get_members(room),"online":mgr.count(room)})

# ── DM HELPERS ──
def dm_conv_id(u1, u2):
    return "__".join(sorted([u1, u2]))

def db_get_dm_history(conv_id, limit=60):
    try:
        res = supabase.table("dm_messages").select("*").eq("conversation_id", conv_id).order("id", desc=True).limit(limit).execute()
        result = []
        for r in reversed(res.data or []):
            result.append({
                "id": r.get("id"), "from_user": r.get("from_user"),
                "to_user": r.get("to_user"), "text": "[message supprimé]" if r.get("deleted") else r.get("text"),
                "deleted": r.get("deleted", False), "avatar": r.get("avatar"),
                "color": r.get("color"),
                "time": r.get("created_at","")[-8:-3] if r.get("created_at") else ""
            })
        return result
    except Exception as e:
        print(f"dm_history error: {e}"); return []

def db_save_dm(conv_id, from_user, to_user, text, avatar, color):
    try:
        res = supabase.table("dm_messages").insert({
            "conversation_id": conv_id, "from_user": from_user, "to_user": to_user,
            "text": text, "avatar": avatar, "color": color,
            "deleted": False, "created_at": now_full()
        }).execute()
        return res.data[0]["id"] if res.data else None
    except Exception as e:
        print(f"dm_save error: {e}"); return None

def db_get_dm_request(from_user, to_user):
    try:
        res = supabase.table("dm_requests").select("*").eq("from_user", from_user).eq("to_user", to_user).execute()
        return res.data[0] if res.data else None
    except: return None

def db_get_my_dms(username):
    try:
        res = supabase.table("dm_requests").select("*").or_(
            f"from_user.eq.{username},to_user.eq.{username}"
        ).eq("status", "accepted").execute()
        return res.data or []
    except: return []

def db_get_pending_requests(username):
    try:
        res = supabase.table("dm_requests").select("*").eq("to_user", username).eq("status", "pending").execute()
        return res.data or []
    except: return []

# ── DM WEBSOCKET ──
@app.websocket("/dm/{username}/{color}")
async def dm_endpoint(ws: WebSocket, username: str, color: str):
    color = "#" + color
    if mgr.is_taken(username + "_dm"):
        await ws.accept()
        await ws.send_text(json.dumps({"type": "error", "text": "Déjà connecté"}))
        await ws.close(); return

    await ws.accept()
    mgr.connected[username + "_dm"] = "dm"

    # Send pending requests
    pending = db_get_pending_requests(username)
    if pending:
        await ws.send_text(json.dumps({"type": "dm_pending_requests", "requests": pending}))

    # Send accepted DMs list
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
                # Check user exists
                try:
                    u = supabase.table("users").select("username").eq("username", target).execute()
                    if not u.data:
                        await ws.send_text(json.dumps({"type": "error", "text": f"'{target}' n'existe pas."})); continue
                except: pass
                # Check already exists
                existing = db_get_dm_request(username, target) or db_get_dm_request(target, username)
                if existing:
                    if existing.get("status") == "accepted":
                        await ws.send_text(json.dumps({"type": "error", "text": "Vous avez déjà une conversation."})); continue
                    elif existing.get("status") == "pending":
                        await ws.send_text(json.dumps({"type": "error", "text": "Demande déjà envoyée."})); continue
                try:
                    supabase.table("dm_requests").insert({
                        "from_user": username, "to_user": target,
                        "status": "pending", "created_at": now_full()
                    }).execute()
                    await ws.send_text(json.dumps({"type": "info", "text": f"Demande envoyée à {target} !"}))
                    # Notify target if online
                    await mgr.send_to(target + "_dm", {"type": "dm_new_request", "from_user": username})
                except:
                    await ws.send_text(json.dumps({"type": "error", "text": "Erreur lors de l'envoi."}))

            elif mtype == "dm_accept":
                from_user = msg.get("from_user", "").strip()
                try:
                    supabase.table("dm_requests").update({"status": "accepted"}).eq("from_user", from_user).eq("to_user", username).execute()
                    await ws.send_text(json.dumps({"type": "info", "text": f"Tu peux maintenant chatter avec {from_user} !"}))
                    dms = db_get_my_dms(username)
                    await ws.send_text(json.dumps({"type": "dm_list", "dms": dms, "my_username": username}))
                    await mgr.send_to(from_user + "_dm", {"type": "dm_accepted", "by": username})
                except: pass

            elif mtype == "dm_refuse":
                from_user = msg.get("from_user", "").strip()
                try:
                    supabase.table("dm_requests").update({"status": "refused"}).eq("from_user", from_user).eq("to_user", username).execute()
                    await ws.send_text(json.dumps({"type": "info", "text": f"Demande de {from_user} refusée."}))
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
                # Check accepted
                req = db_get_dm_request(username, target) or db_get_dm_request(target, username)
                if not req or req.get("status") != "accepted":
                    await ws.send_text(json.dumps({"type": "error", "text": "Pas de conversation active."})); continue
                conv_id = dm_conv_id(username, target)
                try:
                    u = supabase.table("users").select("avatar,color").eq("username", username).execute()
                    udata = u.data[0] if u.data else {}
                except: udata = {}
                msg_id = db_save_dm(conv_id, username, target, text, udata.get("avatar"), udata.get("color", color))
                payload = {"type": "dm_message", "id": msg_id, "from_user": username, "to_user": target, "text": text, "time": now_time(), "avatar": udata.get("avatar"), "color": udata.get("color", color), "conv_id": conv_id}
                await ws.send_text(json.dumps(payload))
                await mgr.send_to(target + "_dm", payload)

            elif mtype == "dm_typing":
                target = msg.get("to", "").strip()
                await mgr.send_to(target + "_dm", {"type": "dm_typing", "from_user": username})

            elif mtype == "dm_delete":
                msg_id = msg.get("msg_id")
                try:
                    supabase.table("dm_messages").update({"deleted": True}).eq("id", msg_id).eq("from_user", username).execute()
                    target = msg.get("to", "").strip()
                    conv_id = dm_conv_id(username, target)
                    payload = {"type": "dm_deleted", "msg_id": msg_id, "conv_id": conv_id}
                    await ws.send_text(json.dumps(payload))
                    await mgr.send_to(target + "_dm", payload)
                except: pass

    except WebSocketDisconnect:
        if username + "_dm" in mgr.connected:
            del mgr.connected[username + "_dm"]

@app.get("/")
async def root():
    with open("index.html","r",encoding="utf-8") as f:
        return HTMLResponse(f.read())
