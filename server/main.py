import os
import json
import sqlite3
import secrets
import hashlib
import threading
import time
import base64
from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException, Header, Query, Request
from fastapi.responses import Response
from pydantic import BaseModel
from typing import Optional

APP_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(APP_DIR, "prodsmart_cloud.db")

app = FastAPI(title="ProdSmart Cloud API")

_APP_WATCH_LOCK = threading.Lock()
_APP_WATCH_CANCEL = None
_APP_WATCH_THREAD = None


def _utc_now():
    return datetime.now(timezone.utc)


def _iso(dt):
    if isinstance(dt, str):
        return dt
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat()


def _hash_password(password):
    return hashlib.sha256(password.encode("utf-8")).hexdigest()


def _get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _init_db():
    conn = _get_db()
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            avatar_filename TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS sessions (
            token TEXT PRIMARY KEY,
            user_id INTEGER NOT NULL,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            last_seen TEXT
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS teams (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            owner_id INTEGER NOT NULL,
            join_code TEXT UNIQUE NOT NULL,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS team_members (
            team_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            role TEXT NOT NULL,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(team_id, user_id)
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS team_join_requests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            team_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            decided_at TEXT,
            decided_by INTEGER,
            UNIQUE(team_id, user_id)
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS team_tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            team_id INTEGER NOT NULL,
            title TEXT NOT NULL,
            description TEXT,
            due_date TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            created_by INTEGER,
            assigned_to INTEGER,
            is_completed INTEGER DEFAULT 0,
            completed_at TEXT,
            completed_by INTEGER,
            task_type TEXT,
            is_urgent INTEGER DEFAULT 0,
            is_important INTEGER DEFAULT 0,
            priority TEXT,
            created_date TEXT,
            updated_at TEXT
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS team_pomodoro (
            team_id INTEGER PRIMARY KEY,
            phase TEXT,
            status TEXT,
            started_at TEXT,
            duration_min INTEGER,
            started_by INTEGER,
            updated_at TEXT,
            ended_at TEXT
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS team_messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            team_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            message TEXT NOT NULL,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS team_task_comments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            team_id INTEGER NOT NULL,
            task_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            message TEXT NOT NULL,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS team_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            team_id INTEGER NOT NULL,
            event_type TEXT NOT NULL,
            actor_user_id INTEGER,
            payload TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)
    # Migrate existing installations
    for stmt in (
        "ALTER TABLE users ADD COLUMN avatar_filename TEXT",
        "ALTER TABLE users ADD COLUMN bio TEXT",
        "ALTER TABLE users ADD COLUMN role TEXT",
        "ALTER TABLE users ADD COLUMN department TEXT",
        "ALTER TABLE users ADD COLUMN join_date TEXT",
        "ALTER TABLE users ADD COLUMN skills_json TEXT",
        "ALTER TABLE users ADD COLUMN projects TEXT",
    ):
        try:
            cur.execute(stmt)
        except sqlite3.OperationalError:
            pass
    for stmt in (
        "ALTER TABLE team_tasks ADD COLUMN task_type TEXT",
        "ALTER TABLE team_tasks ADD COLUMN is_urgent INTEGER DEFAULT 0",
        "ALTER TABLE team_tasks ADD COLUMN is_important INTEGER DEFAULT 0",
        "ALTER TABLE team_tasks ADD COLUMN priority TEXT",
        "ALTER TABLE team_tasks ADD COLUMN created_date TEXT",
        "ALTER TABLE team_tasks ADD COLUMN assigned_to INTEGER",
        "ALTER TABLE team_tasks ADD COLUMN updated_at TEXT",
        "ALTER TABLE team_tasks ADD COLUMN completed_by INTEGER",
    ):
        try:
            cur.execute(stmt)
        except sqlite3.OperationalError:
            pass

    # Backfill join_date for existing users (best-effort).
    try:
        cur.execute(
            "UPDATE users SET join_date = substr(created_at, 1, 10) "
            "WHERE join_date IS NULL OR trim(join_date) = ''"
        )
    except Exception:
        pass
    conn.commit()
    conn.close()


_init_db()

_AVATAR_DIR = os.path.join(APP_DIR, "uploads", "avatars")
try:
    os.makedirs(_AVATAR_DIR, exist_ok=True)
except Exception:
    pass


def _create_join_code(conn):
    cur = conn.cursor()
    while True:
        code = secrets.token_urlsafe(6).replace("-", "").replace("_", "")[:8].upper()
        exists = cur.execute("SELECT 1 FROM teams WHERE join_code = ?", (code,)).fetchone()
        if not exists:
            return code


def _extract_token(auth_header):
    if not auth_header:
        return None
    if auth_header.lower().startswith("bearer "):
        return auth_header.split(" ", 1)[1].strip()
    return auth_header.strip()


def _clean_profile_text(value: str | None, *, max_len: int) -> str:
    raw = str(value or "")
    # Collapse Windows newlines and trim.
    raw = raw.replace("\r\n", "\n").strip()
    if len(raw) > int(max_len):
        raw = raw[: int(max_len)].rstrip()
    return raw


def _clean_join_date(value: str | None) -> str | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    # Accept YYYY-MM-DD (store as-is) or ISO datetimes (store date part).
    try:
        dt = datetime.fromisoformat(raw)
        return dt.date().isoformat()
    except Exception:
        pass
    try:
        dt = datetime.strptime(raw, "%Y-%m-%d")
        return dt.date().isoformat()
    except Exception:
        return None


def _clean_skills(values: list[str] | None) -> list[str]:
    if not values:
        return []
    out: list[str] = []
    seen: set[str] = set()
    for v in values:
        s = str(v or "").strip()
        if not s:
            continue
        key = s.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(s[:64])
        if len(out) >= 30:
            break
    return out


def _skills_from_json(raw: str | None) -> list[str]:
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except Exception:
        return []
    return _clean_skills(data if isinstance(data, list) else [])


def _require_user(authorization):
    token = _extract_token(authorization)
    if not token:
        raise HTTPException(status_code=401, detail="Missing authorization token.")
    conn = _get_db()
    row = conn.execute(
        "SELECT users.id, users.username FROM sessions JOIN users ON users.id = sessions.user_id WHERE sessions.token = ?",
        (token,)
    ).fetchone()
    if not row:
        conn.close()
        raise HTTPException(status_code=401, detail="Invalid or expired token.")
    conn.execute(
        "UPDATE sessions SET last_seen = ? WHERE token = ?",
        (_iso(_utc_now()), token)
    )
    conn.commit()
    conn.close()
    return {"id": row["id"], "username": row["username"], "token": token}


def _deadline_is_urgent(due_date_str):
    if not due_date_str:
        return 0
    try:
        due = datetime.fromisoformat(due_date_str)
    except Exception:
        try:
            due = datetime.strptime(due_date_str, "%Y-%m-%d")
        except Exception:
            return 0
    today = _utc_now().date()
    days_to = (due.date() - today).days
    return 1 if days_to <= 2 else 0


def _priority_from_flags(is_urgent, is_important):
    try:
        urg = int(is_urgent or 0)
        imp = int(is_important or 0)
    except Exception:
        urg = 0
        imp = 0
    if urg == 1 and imp == 1:
        return "high"
    if urg == 0 and imp == 1:
        return "medium"
    if urg == 1 and imp == 0:
        return "low"
    return "too low"


_ROLE_RANK = {"member": 0, "manager": 1, "admin": 2, "owner": 3}


def _role_rank(role: str | None) -> int:
    try:
        return int(_ROLE_RANK.get(str(role or "").strip().lower(), 0))
    except Exception:
        return 0


def _require_team_role(actor_role: str, required: str) -> None:
    if _role_rank(actor_role) < _role_rank(required):
        raise HTTPException(status_code=403, detail="Insufficient role for this action.")


def _emit_team_event(conn, team_id: int, event_type: str, actor_user_id: int | None = None, payload=None) -> None:
    try:
        now = _iso(_utc_now())
        payload_str = None
        if payload is not None:
            try:
                payload_str = json.dumps(payload, ensure_ascii=False)
            except Exception:
                payload_str = None
        conn.execute(
            "INSERT INTO team_events (team_id, event_type, actor_user_id, payload, created_at) VALUES (?, ?, ?, ?, ?)",
            (int(team_id), str(event_type), int(actor_user_id) if actor_user_id is not None else None, payload_str, now),
        )
    except Exception:
        pass


def _ensure_member(conn, team_id, user_id):
    row = conn.execute(
        "SELECT role FROM team_members WHERE team_id = ? AND user_id = ?",
        (team_id, user_id)
    ).fetchone()
    if not row:
        raise HTTPException(status_code=403, detail="Not a member of this team.")
    return row["role"]


class AuthPayload(BaseModel):
    username: str
    password: str


class ChangePasswordPayload(BaseModel):
    current_password: str
    new_password: str


def _password_policy_error(password: str) -> Optional[str]:
    pw = str(password or "")
    if len(pw) < 8:
        return "Password must be at least 8 characters."
    if any(ch.isspace() for ch in pw):
        return "Password must not contain spaces."
    has_upper = any(ch.isupper() for ch in pw)
    has_lower = any(ch.islower() for ch in pw)
    has_digit = any(ch.isdigit() for ch in pw)
    has_special = any((not ch.isalnum()) and (not ch.isspace()) for ch in pw)
    if not (has_upper and has_lower and has_digit and has_special):
        return "Password must include: 1 uppercase, 1 lowercase, 1 number, and 1 special character."
    return None


class TeamCreatePayload(BaseModel):
    name: str


class TeamJoinPayload(BaseModel):
    code: str


class TeamInvitePayload(BaseModel):
    username: str


class UserAvatarPayload(BaseModel):
    image_base64: str


class UserProfilePayload(BaseModel):
    bio: Optional[str] = None
    role: Optional[str] = None
    department: Optional[str] = None
    join_date: Optional[str] = None
    skills: list[str] | None = None
    projects: Optional[str] = None


class TeamTaskPayload(BaseModel):
    title: str
    description: Optional[str] = ""
    due_date: Optional[str] = None
    task_type: Optional[str] = None
    is_important: Optional[bool] = None
    assigned_to: Optional[int] = None


class TeamTaskUpdatePayload(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    due_date: Optional[str] = None
    is_completed: Optional[bool] = None
    task_type: Optional[str] = None
    is_important: Optional[bool] = None
    assigned_to: Optional[int] = None


class TeamMemberRolePayload(BaseModel):
    role: str


class TeamTaskCommentPayload(BaseModel):
    message: str


class PomodoroStartPayload(BaseModel):
    phase: str
    duration_min: int


class TeamMessagePayload(BaseModel):
    message: str


class AppRegisterPayload(BaseModel):
    pid: int
    create_time: Optional[int] = None


@app.get("/")
def root():
    return {"status": "ok"}


def _is_loopback_request(request: Request) -> bool:
    try:
        host = str(getattr(getattr(request, "client", None), "host", "") or "").strip().lower()
        return host in ("127.0.0.1", "::1")
    except Exception:
        return False


def _request_server_shutdown(reason: str = "") -> None:
    try:
        server = getattr(app.state, "uvicorn_server", None)
    except Exception:
        server = None
    if server is not None:
        try:
            server.should_exit = True
        except Exception:
            pass
    # Don't wait forever; this endpoint may be the only thing that can stop a
    # detached server instance.
    deadline = time.monotonic() + 2.0
    while time.monotonic() < deadline:
        time.sleep(0.05)
    os._exit(0)


def _start_app_watchdog(*, pid: int, create_time: Optional[int] = None) -> None:
    global _APP_WATCH_CANCEL, _APP_WATCH_THREAD

    cancel = threading.Event()

    def _trigger_shutdown() -> None:
        if cancel.is_set():
            return
        _request_server_shutdown("app_exit")

    def _watch_windows() -> None:
        try:
            import ctypes
            from ctypes import wintypes

            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

            OpenProcess = kernel32.OpenProcess
            OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
            OpenProcess.restype = wintypes.HANDLE

            CloseHandle = kernel32.CloseHandle
            CloseHandle.argtypes = (wintypes.HANDLE,)
            CloseHandle.restype = wintypes.BOOL

            WaitForSingleObject = kernel32.WaitForSingleObject
            WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)
            WaitForSingleObject.restype = wintypes.DWORD

            GetProcessTimes = kernel32.GetProcessTimes
            GetProcessTimes.argtypes = (
                wintypes.HANDLE,
                ctypes.POINTER(wintypes.FILETIME),
                ctypes.POINTER(wintypes.FILETIME),
                ctypes.POINTER(wintypes.FILETIME),
                ctypes.POINTER(wintypes.FILETIME),
            )
            GetProcessTimes.restype = wintypes.BOOL

            SYNCHRONIZE = 0x00100000
            PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            INFINITE = 0xFFFFFFFF

            handle = OpenProcess(SYNCHRONIZE | PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
            has_query = True
            if not handle:
                handle = OpenProcess(SYNCHRONIZE, False, int(pid))
                has_query = False
            if not handle:
                _trigger_shutdown()
                return

            try:
                if create_time is not None and has_query:
                    creation = wintypes.FILETIME()
                    exit_time = wintypes.FILETIME()
                    kernel_time = wintypes.FILETIME()
                    user_time = wintypes.FILETIME()
                    ok = GetProcessTimes(
                        handle,
                        ctypes.byref(creation),
                        ctypes.byref(exit_time),
                        ctypes.byref(kernel_time),
                        ctypes.byref(user_time),
                    )
                    if not ok:
                        _trigger_shutdown()
                        return
                    ft = (int(creation.dwHighDateTime) << 32) | int(creation.dwLowDateTime)
                    if int(ft) != int(create_time):
                        _trigger_shutdown()
                        return

                # Wait until the app process exits...
                WaitForSingleObject(handle, INFINITE)
            finally:
                try:
                    CloseHandle(handle)
                except Exception:
                    pass
            _trigger_shutdown()
        except Exception:
            _trigger_shutdown()

    def _watch_posix() -> None:
        while not cancel.is_set():
            time.sleep(1.0)
            try:
                os.kill(int(pid), 0)
            except PermissionError:
                continue
            except Exception:
                _trigger_shutdown()
                return

    watch_fn = _watch_windows if os.name == "nt" else _watch_posix

    with _APP_WATCH_LOCK:
        if _APP_WATCH_CANCEL is not None:
            try:
                _APP_WATCH_CANCEL.set()
            except Exception:
                pass
        _APP_WATCH_CANCEL = cancel

        t = threading.Thread(
            target=watch_fn,
            name="prodsmart-app-watchdog",
            daemon=True,
        )
        _APP_WATCH_THREAD = t
        t.start()


@app.post("/__internal/register_app", include_in_schema=False)
def register_app(payload: AppRegisterPayload, request: Request):
    # Only allow from local machine.
    if not _is_loopback_request(request):
        raise HTTPException(status_code=403, detail="Forbidden.")

    pid = int(payload.pid or 0)
    if pid <= 0:
        raise HTTPException(status_code=400, detail="Invalid pid.")

    create_time = payload.create_time
    if create_time is not None:
        try:
            create_time = int(create_time)
            if create_time <= 0:
                create_time = None
        except Exception:
            create_time = None

    _start_app_watchdog(pid=pid, create_time=create_time)
    return {"ok": True}


@app.post("/__internal/shutdown", include_in_schema=False)
def shutdown_server(request: Request):
    if not _is_loopback_request(request):
        raise HTTPException(status_code=403, detail="Forbidden.")
    _request_server_shutdown("shutdown_endpoint")
    return {"ok": True}


@app.post("/auth/register")
def register(payload: AuthPayload):
    username = payload.username.strip()
    if not username or not payload.password:
        raise HTTPException(status_code=400, detail="Username and password are required.")
    conn = _get_db()
    cur = conn.cursor()
    try:
        cur.execute(
            "INSERT INTO users (username, password_hash) VALUES (?, ?)",
            (username, _hash_password(payload.password))
        )
        conn.commit()
        user_id = cur.lastrowid
        return {"user_id": user_id, "username": username}
    except sqlite3.IntegrityError:
        raise HTTPException(status_code=400, detail="Username already exists.")
    finally:
        conn.close()


@app.post("/auth/login")
def login(payload: AuthPayload):
    conn = _get_db()
    row = conn.execute(
        "SELECT id, password_hash FROM users WHERE username = ?",
        (payload.username.strip(),)
    ).fetchone()
    if not row:
        conn.close()
        raise HTTPException(status_code=401, detail="Invalid username or password.")
    if row["password_hash"] != _hash_password(payload.password):
        conn.close()
        raise HTTPException(status_code=401, detail="Invalid username or password.")
    token = secrets.token_urlsafe(32)
    conn.execute(
        "INSERT INTO sessions (token, user_id, last_seen) VALUES (?, ?, ?)",
        (token, row["id"], _iso(_utc_now()))
    )
    conn.commit()
    conn.close()
    return {"token": token, "user_id": row["id"], "username": payload.username.strip()}


@app.post("/auth/change_password")
def change_password(
    payload: ChangePasswordPayload,
    authorization: Optional[str] = Header(default=None, alias="Authorization"),
):
    user = _require_user(authorization)
    current_pw = str(payload.current_password or "")
    new_pw = str(payload.new_password or "")
    if not current_pw or not new_pw:
        raise HTTPException(status_code=400, detail="Current and new password are required.")
    policy_err = _password_policy_error(new_pw)
    if policy_err:
        raise HTTPException(status_code=400, detail=policy_err)

    conn = _get_db()
    row = conn.execute("SELECT password_hash FROM users WHERE id = ?", (user["id"],)).fetchone()
    if not row:
        conn.close()
        # Avoid leaking account state; treat as auth failure.
        raise HTTPException(status_code=401, detail="Password incorrect.")
    if row["password_hash"] != _hash_password(current_pw):
        conn.close()
        raise HTTPException(status_code=401, detail="Password incorrect.")

    conn.execute(
        "UPDATE users SET password_hash = ? WHERE id = ?",
        (_hash_password(new_pw), user["id"]),
    )
    conn.commit()
    conn.close()
    return {"status": "ok"}


@app.get("/me")
def me(authorization: Optional[str] = Header(default=None, alias="Authorization")):
    user = _require_user(authorization)
    return {"user_id": user["id"], "username": user["username"]}


@app.get("/users/{user_id}/profile")
def get_user_profile(
    user_id: int,
    authorization: Optional[str] = Header(default=None, alias="Authorization"),
):
    _require_user(authorization)
    uid = int(user_id)
    if uid <= 0:
        raise HTTPException(status_code=400, detail="Invalid user id.")

    conn = _get_db()
    row = conn.execute(
        "SELECT id, username, created_at, bio, role, department, join_date, skills_json, projects "
        "FROM users WHERE id = ?",
        (uid,),
    ).fetchone()
    conn.close()
    if not row:
        raise HTTPException(status_code=404, detail="User not found.")

    created_at = str(row["created_at"] or "").strip()
    join_date = str(row["join_date"] or "").strip()
    if not join_date and created_at:
        join_date = created_at[:10]

    return {
        "user_id": int(row["id"]),
        "username": str(row["username"]),
        "created_at": created_at,
        "bio": str(row["bio"] or ""),
        "role": str(row["role"] or ""),
        "department": str(row["department"] or ""),
        "join_date": join_date,
        "skills": _skills_from_json(row["skills_json"]),
        "projects": str(row["projects"] or ""),
    }


@app.get("/users/me/profile")
def get_my_profile(authorization: Optional[str] = Header(default=None, alias="Authorization")):
    user = _require_user(authorization)
    return get_user_profile(int(user["id"]), authorization)


@app.post("/users/me/profile")
def set_my_profile(
    payload: UserProfilePayload,
    authorization: Optional[str] = Header(default=None, alias="Authorization"),
):
    user = _require_user(authorization)
    bio = _clean_profile_text(payload.bio, max_len=2000) if payload.bio is not None else None
    role = _clean_profile_text(payload.role, max_len=120) if payload.role is not None else None
    dept = _clean_profile_text(payload.department, max_len=120) if payload.department is not None else None
    projects = _clean_profile_text(payload.projects, max_len=3000) if payload.projects is not None else None
    join_date = _clean_join_date(payload.join_date) if payload.join_date is not None else None
    skills = _clean_skills(payload.skills) if payload.skills is not None else None

    fields: list[str] = []
    params: list[object] = []
    if bio is not None:
        fields.append("bio = ?")
        params.append(bio)
    if role is not None:
        fields.append("role = ?")
        params.append(role)
    if dept is not None:
        fields.append("department = ?")
        params.append(dept)
    if projects is not None:
        fields.append("projects = ?")
        params.append(projects)
    if join_date is not None:
        # Only update join_date if parsing succeeded; ignore invalid strings.
        fields.append("join_date = ?")
        params.append(join_date or "")
    if skills is not None:
        fields.append("skills_json = ?")
        try:
            params.append(json.dumps(skills, ensure_ascii=False))
        except Exception:
            params.append("[]")

    if not fields:
        return {"ok": True}

    conn = _get_db()
    conn.execute(
        f"UPDATE users SET {', '.join(fields)} WHERE id = ?",
        (*params, int(user["id"])),
    )
    conn.commit()
    conn.close()
    return {"ok": True}


@app.get("/users/search")
def search_users(
    authorization: Optional[str] = Header(default=None, alias="Authorization"),
    q: str = Query(default="", min_length=0, max_length=64),
    limit: int = Query(default=10, ge=1, le=25),
):
    _require_user(authorization)
    query = str(q or "").strip()
    if not query:
        return {"users": []}
    ql = query.lower()
    like_contains = f"%{ql}%"
    like_prefix = f"{ql}%"
    conn = _get_db()
    rows = conn.execute(
        """SELECT id as user_id, username
           FROM users
           WHERE lower(username) LIKE ?
           ORDER BY (lower(username) LIKE ?) DESC, username ASC
           LIMIT ?""",
        (like_contains, like_prefix, int(limit)),
    ).fetchall()
    conn.close()
    return {"users": [dict(r) for r in rows]}


@app.get("/users/{user_id}/avatar")
def get_user_avatar(
    user_id: int,
    authorization: Optional[str] = Header(default=None, alias="Authorization"),
):
    _require_user(authorization)
    conn = _get_db()
    row = conn.execute("SELECT avatar_filename FROM users WHERE id = ?", (int(user_id),)).fetchone()
    conn.close()
    if not row:
        raise HTTPException(status_code=404, detail="User not found.")
    filename = str(row["avatar_filename"] or "").strip()
    if not filename:
        raise HTTPException(status_code=404, detail="Avatar not set.")
    # Prevent path traversal.
    filename = os.path.basename(filename)
    path = os.path.join(_AVATAR_DIR, filename)
    if not os.path.isfile(path):
        raise HTTPException(status_code=404, detail="Avatar not found.")
    try:
        with open(path, "rb") as f:
            data = f.read()
    except OSError:
        raise HTTPException(status_code=404, detail="Avatar not found.")
    media_type = "image/png"
    low = filename.lower()
    if low.endswith(".jpg") or low.endswith(".jpeg"):
        media_type = "image/jpeg"
    elif low.endswith(".webp"):
        media_type = "image/webp"
    return Response(content=data, media_type=media_type)


@app.post("/users/me/avatar")
def set_my_avatar(
    payload: UserAvatarPayload,
    authorization: Optional[str] = Header(default=None, alias="Authorization"),
):
    user = _require_user(authorization)
    raw = str(payload.image_base64 or "").strip()
    if not raw:
        raise HTTPException(status_code=400, detail="Missing image data.")
    try:
        data = base64.b64decode(raw, validate=True)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid base64 image data.")
    if not data:
        raise HTTPException(status_code=400, detail="Empty image.")
    # 2MB max
    if len(data) > 2_000_000:
        raise HTTPException(status_code=400, detail="Image too large (max 2MB).")

    ext = None
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        ext = "png"
    elif data.startswith(b"\xff\xd8"):
        ext = "jpg"
    elif data.startswith(b"RIFF") and b"WEBP" in data[:16]:
        ext = "webp"
    if not ext:
        raise HTTPException(status_code=400, detail="Unsupported image format. Use PNG or JPEG.")

    filename = f"avatar_{int(user['id'])}_{int(_utc_now().timestamp())}.{ext}"
    path = os.path.join(_AVATAR_DIR, filename)
    try:
        with open(path, "wb") as f:
            f.write(data)
    except OSError:
        raise HTTPException(status_code=500, detail="Failed to save avatar.")

    conn = _get_db()
    conn.execute("UPDATE users SET avatar_filename = ? WHERE id = ?", (filename, int(user["id"])))
    conn.commit()
    conn.close()
    return {"ok": True}


@app.get("/teams")
def list_teams(authorization: Optional[str] = Header(default=None, alias="Authorization")):
    user = _require_user(authorization)
    conn = _get_db()
    rows = conn.execute(
        """SELECT teams.id, teams.name, team_members.role
           FROM team_members
           JOIN teams ON teams.id = team_members.team_id
           WHERE team_members.user_id = ?
           ORDER BY teams.name""",
        (user["id"],)
    ).fetchall()
    conn.close()
    return {"teams": [dict(row) for row in rows]}


@app.post("/teams")
def create_team(payload: TeamCreatePayload, authorization: Optional[str] = Header(default=None, alias="Authorization")):
    user = _require_user(authorization)
    name = payload.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="Team name is required.")
    conn = _get_db()
    join_code = _create_join_code(conn)
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO teams (name, owner_id, join_code) VALUES (?, ?, ?)",
        (name, user["id"], join_code)
    )
    team_id = cur.lastrowid
    cur.execute(
        "INSERT INTO team_members (team_id, user_id, role) VALUES (?, ?, ?)",
        (team_id, user["id"], "owner")
    )
    conn.commit()
    conn.close()
    return {"id": team_id, "name": name, "join_code": join_code, "role": "owner"}


@app.post("/teams/join")
def join_team(payload: TeamJoinPayload, authorization: Optional[str] = Header(default=None, alias="Authorization")):
    user = _require_user(authorization)
    code = payload.code.strip().upper()
    conn = _get_db()
    team = conn.execute(
        "SELECT id, name FROM teams WHERE join_code = ?",
        (code,)
    ).fetchone()
    if not team:
        conn.close()
        raise HTTPException(status_code=404, detail="Invalid team code.")
    existing = conn.execute(
        "SELECT role FROM team_members WHERE team_id = ? AND user_id = ?",
        (int(team["id"]), int(user["id"])),
    ).fetchone()
    if existing:
        conn.close()
        return {"id": team["id"], "name": team["name"], "role": existing["role"], "status": "member"}

    req = conn.execute(
        "SELECT id, status FROM team_join_requests WHERE team_id = ? AND user_id = ?",
        (int(team["id"]), int(user["id"])),
    ).fetchone()
    now = _iso(_utc_now())
    if req:
        status = str(req["status"] or "").strip().lower()
        if status != "pending":
            conn.execute(
                "UPDATE team_join_requests SET status = 'pending', created_at = ?, decided_at = NULL, decided_by = NULL WHERE id = ?",
                (now, int(req["id"])),
            )
            conn.commit()
    else:
        conn.execute(
            "INSERT INTO team_join_requests (team_id, user_id, status, created_at) VALUES (?, ?, 'pending', ?)",
            (int(team["id"]), int(user["id"]), now),
        )
        conn.commit()
    conn.close()
    return {"id": team["id"], "name": team["name"], "status": "pending"}


@app.get("/teams/{team_id}/join-requests")
def list_team_join_requests(team_id: int, authorization: Optional[str] = Header(default=None, alias="Authorization")):
    user = _require_user(authorization)
    conn = _get_db()
    actor_role = _ensure_member(conn, team_id, user["id"])
    _require_team_role(actor_role, "owner")
    rows = conn.execute(
        """SELECT r.id, r.user_id, r.created_at, u.username
           FROM team_join_requests r
           JOIN users u ON u.id = r.user_id
           WHERE r.team_id = ? AND lower(r.status) = 'pending'
           ORDER BY r.id ASC""",
        (int(team_id),),
    ).fetchall()
    conn.close()
    return {"requests": [dict(r) for r in rows]}


@app.post("/teams/{team_id}/join-requests/{request_id}/accept")
def accept_team_join_request(
    team_id: int,
    request_id: int,
    authorization: Optional[str] = Header(default=None, alias="Authorization"),
):
    user = _require_user(authorization)
    conn = _get_db()
    actor_role = _ensure_member(conn, team_id, user["id"])
    _require_team_role(actor_role, "owner")

    req = conn.execute(
        "SELECT id, user_id, status FROM team_join_requests WHERE id = ? AND team_id = ?",
        (int(request_id), int(team_id)),
    ).fetchone()
    if not req:
        conn.close()
        raise HTTPException(status_code=404, detail="Join request not found.")
    if str(req["status"] or "").strip().lower() != "pending":
        conn.close()
        return {"ok": True}

    target_user_id = int(req["user_id"])
    try:
        conn.execute(
            "INSERT INTO team_members (team_id, user_id, role) VALUES (?, ?, ?)",
            (int(team_id), target_user_id, "member"),
        )
    except sqlite3.IntegrityError:
        pass

    now = _iso(_utc_now())
    conn.execute(
        "UPDATE team_join_requests SET status = 'accepted', decided_at = ?, decided_by = ? WHERE id = ?",
        (now, int(user["id"]), int(request_id)),
    )
    _emit_team_event(conn, team_id, "member_added", user["id"], {"user_id": target_user_id, "via": "join_request"})
    conn.commit()
    conn.close()
    return {"ok": True}


@app.post("/teams/{team_id}/join-requests/{request_id}/reject")
def reject_team_join_request(
    team_id: int,
    request_id: int,
    authorization: Optional[str] = Header(default=None, alias="Authorization"),
):
    user = _require_user(authorization)
    conn = _get_db()
    actor_role = _ensure_member(conn, team_id, user["id"])
    _require_team_role(actor_role, "owner")

    req = conn.execute(
        "SELECT id, user_id, status FROM team_join_requests WHERE id = ? AND team_id = ?",
        (int(request_id), int(team_id)),
    ).fetchone()
    if not req:
        conn.close()
        raise HTTPException(status_code=404, detail="Join request not found.")
    if str(req["status"] or "").strip().lower() != "pending":
        conn.close()
        return {"ok": True}

    now = _iso(_utc_now())
    conn.execute(
        "UPDATE team_join_requests SET status = 'rejected', decided_at = ?, decided_by = ? WHERE id = ?",
        (now, int(user["id"]), int(request_id)),
    )
    conn.commit()
    conn.close()
    return {"ok": True}


@app.post("/teams/{team_id}/invites")
def invite_team_member(
    team_id: int,
    payload: TeamInvitePayload,
    authorization: Optional[str] = Header(default=None, alias="Authorization"),
):
    user = _require_user(authorization)
    username = str(payload.username or "").strip()
    if not username:
        raise HTTPException(status_code=400, detail="Username is required.")
    conn = _get_db()
    actor_role = _ensure_member(conn, team_id, user["id"])
    _require_team_role(actor_role, "owner")

    target = conn.execute(
        "SELECT id, username FROM users WHERE lower(username) = lower(?)",
        (username,),
    ).fetchone()
    if not target:
        conn.close()
        raise HTTPException(status_code=404, detail="User not found.")
    target_user_id = int(target["id"])
    if target_user_id == int(user["id"]):
        conn.close()
        raise HTTPException(status_code=400, detail="You are already a member of this team.")

    existing = conn.execute(
        "SELECT 1 FROM team_members WHERE team_id = ? AND user_id = ?",
        (int(team_id), target_user_id),
    ).fetchone()
    if existing:
        conn.close()
        return {"ok": True}

    try:
        conn.execute(
            "INSERT INTO team_members (team_id, user_id, role) VALUES (?, ?, ?)",
            (int(team_id), target_user_id, "member"),
        )
    except sqlite3.IntegrityError:
        conn.close()
        return {"ok": True}
    _emit_team_event(conn, team_id, "member_added", user["id"], {"user_id": target_user_id, "via": "invite"})
    conn.commit()
    conn.close()
    return {"ok": True}


@app.get("/teams/{team_id}")
def get_team(team_id: int, authorization: Optional[str] = Header(default=None, alias="Authorization")):
    user = _require_user(authorization)
    conn = _get_db()
    role = _ensure_member(conn, team_id, user["id"])
    team = conn.execute("SELECT id, name, join_code, owner_id FROM teams WHERE id = ?", (team_id,)).fetchone()
    conn.close()
    if not team:
        raise HTTPException(status_code=404, detail="Team not found.")
    data = {"id": team["id"], "name": team["name"], "role": role}
    if role == "owner" or team["owner_id"] == user["id"]:
        data["join_code"] = team["join_code"]
    return data


@app.get("/teams/{team_id}/members")
def list_team_members(team_id: int, authorization: Optional[str] = Header(default=None, alias="Authorization")):
    user = _require_user(authorization)
    conn = _get_db()
    _ensure_member(conn, team_id, user["id"])
    rows = conn.execute(
        """SELECT users.id as user_id, users.username, team_members.role
           FROM team_members
           JOIN users ON users.id = team_members.user_id
           WHERE team_members.team_id = ?
           ORDER BY users.username""",
        (team_id,)
    ).fetchall()
    conn.close()
    return {"members": [dict(row) for row in rows]}


@app.delete("/teams/{team_id}/members/{member_user_id}")
def remove_team_member(
    team_id: int,
    member_user_id: int,
    authorization: Optional[str] = Header(default=None, alias="Authorization"),
):
    user = _require_user(authorization)
    conn = _get_db()
    actor_role = _ensure_member(conn, team_id, user["id"])
    _require_team_role(actor_role, "owner")

    target = conn.execute(
        "SELECT role FROM team_members WHERE team_id = ? AND user_id = ?",
        (int(team_id), int(member_user_id)),
    ).fetchone()
    if not target:
        conn.close()
        raise HTTPException(status_code=404, detail="Member not found.")
    if str(target["role"] or "").strip().lower() == "owner":
        conn.close()
        raise HTTPException(status_code=400, detail="Cannot remove team owner.")
    if int(member_user_id) == int(user["id"]):
        conn.close()
        raise HTTPException(status_code=400, detail="Owner cannot remove themselves.")

    conn.execute(
        "DELETE FROM team_members WHERE team_id = ? AND user_id = ?",
        (int(team_id), int(member_user_id)),
    )
    now = _iso(_utc_now())
    conn.execute(
        "UPDATE team_tasks SET assigned_to = NULL, updated_at = ? WHERE team_id = ? AND assigned_to = ?",
        (now, int(team_id), int(member_user_id)),
    )
    _emit_team_event(conn, team_id, "member_removed", user["id"], {"user_id": int(member_user_id)})
    conn.commit()
    conn.close()
    return {"ok": True}


@app.patch("/teams/{team_id}/members/{member_user_id}")
def update_team_member_role(
    team_id: int,
    member_user_id: int,
    payload: TeamMemberRolePayload,
    authorization: Optional[str] = Header(default=None, alias="Authorization"),
):
    user = _require_user(authorization)
    new_role = str(payload.role or "").strip().lower()
    if new_role not in ("member", "manager"):
        raise HTTPException(status_code=400, detail="Invalid role.")
    conn = _get_db()
    actor_role = _ensure_member(conn, team_id, user["id"])
    _require_team_role(actor_role, "owner")

    target = conn.execute(
        "SELECT role FROM team_members WHERE team_id = ? AND user_id = ?",
        (team_id, int(member_user_id)),
    ).fetchone()
    if not target:
        conn.close()
        raise HTTPException(status_code=404, detail="Member not found.")
    if str(target["role"] or "").strip().lower() == "owner":
        conn.close()
        raise HTTPException(status_code=400, detail="Cannot change owner role.")

    conn.execute(
        "UPDATE team_members SET role = ? WHERE team_id = ? AND user_id = ?",
        (new_role, team_id, int(member_user_id)),
    )
    _emit_team_event(conn, team_id, "member_role_updated", user["id"], {"user_id": int(member_user_id), "role": new_role})
    conn.commit()
    conn.close()
    return {"ok": True}


@app.get("/teams/{team_id}/events")
def list_team_events(
    team_id: int,
    authorization: Optional[str] = Header(default=None, alias="Authorization"),
    after_id: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=200),
):
    user = _require_user(authorization)
    conn = _get_db()
    _ensure_member(conn, team_id, user["id"])
    rows = conn.execute(
        """SELECT e.id, e.event_type, e.payload, e.created_at,
                  u.id as actor_user_id, u.username as actor_username
           FROM team_events e
           LEFT JOIN users u ON u.id = e.actor_user_id
           WHERE e.team_id = ? AND e.id > ?
           ORDER BY e.id ASC
           LIMIT ?""",
        (team_id, int(after_id), int(limit)),
    ).fetchall()
    conn.close()
    events = []
    for row in rows:
        item = dict(row)
        payload_raw = item.get("payload")
        if payload_raw:
            try:
                item["payload"] = json.loads(payload_raw)
            except Exception:
                item["payload"] = payload_raw
        events.append(item)
    return {"events": events}


@app.get("/teams/{team_id}/tasks")
def list_team_tasks(team_id: int, authorization: Optional[str] = Header(default=None, alias="Authorization")):
    user = _require_user(authorization)
    conn = _get_db()
    _ensure_member(conn, team_id, user["id"])
    rows = conn.execute(
        """SELECT id, title, description, due_date, created_at, created_date, is_completed, completed_at,
                  completed_by, created_by, assigned_to, updated_at, task_type, is_urgent, is_important, priority
           FROM team_tasks WHERE team_id = ? ORDER BY is_completed, created_at DESC""",
        (team_id,)
    ).fetchall()
    conn.close()
    return {"tasks": [dict(row) for row in rows]}


@app.get("/teams/{team_id}/analytics")
def team_analytics(team_id: int, authorization: Optional[str] = Header(default=None, alias="Authorization")):
    user = _require_user(authorization)
    conn = _get_db()
    _ensure_member(conn, team_id, user["id"])
    today = _utc_now().date().isoformat()

    total = int(conn.execute("SELECT COUNT(*) FROM team_tasks WHERE team_id = ?", (team_id,)).fetchone()[0] or 0)
    completed = int(conn.execute(
        "SELECT COUNT(*) FROM team_tasks WHERE team_id = ? AND is_completed = 1",
        (team_id,),
    ).fetchone()[0] or 0)
    active = int(conn.execute(
        "SELECT COUNT(*) FROM team_tasks WHERE team_id = ? AND COALESCE(is_completed, 0) = 0",
        (team_id,),
    ).fetchone()[0] or 0)
    overdue = int(conn.execute(
        "SELECT COUNT(*) FROM team_tasks WHERE team_id = ? AND COALESCE(is_completed, 0) = 0 AND due_date IS NOT NULL AND substr(due_date, 1, 10) < ?",
        (team_id, today),
    ).fetchone()[0] or 0)

    by_completed = conn.execute(
        """SELECT users.id as user_id, users.username, COUNT(*) as count
           FROM team_tasks
           JOIN users ON users.id = team_tasks.completed_by
           WHERE team_tasks.team_id = ? AND team_tasks.is_completed = 1
           GROUP BY users.id, users.username
           ORDER BY count DESC""",
        (team_id,),
    ).fetchall()
    by_created = conn.execute(
        """SELECT users.id as user_id, users.username, COUNT(*) as count
           FROM team_tasks
           JOIN users ON users.id = team_tasks.created_by
           WHERE team_tasks.team_id = ?
           GROUP BY users.id, users.username
           ORDER BY count DESC""",
        (team_id,),
    ).fetchall()
    by_assigned_active = conn.execute(
        """SELECT users.id as user_id, users.username, COUNT(*) as count
           FROM team_tasks
           JOIN users ON users.id = team_tasks.assigned_to
           WHERE team_tasks.team_id = ? AND COALESCE(team_tasks.is_completed, 0) = 0 AND team_tasks.assigned_to IS NOT NULL
           GROUP BY users.id, users.username
           ORDER BY count DESC""",
        (team_id,),
    ).fetchall()
    comments = conn.execute(
        """SELECT users.id as user_id, users.username, COUNT(*) as count
           FROM team_task_comments
           JOIN users ON users.id = team_task_comments.user_id
           WHERE team_task_comments.team_id = ?
           GROUP BY users.id, users.username
           ORDER BY count DESC""",
        (team_id,),
    ).fetchall()
    conn.close()

    completion_rate = (completed / total) if total else 0.0
    return {
        "tasks": {
            "total": total,
            "active": active,
            "completed": completed,
            "overdue": overdue,
            "completion_rate": completion_rate,
        },
        "members": {
            "completed": [dict(r) for r in by_completed],
            "created": [dict(r) for r in by_created],
            "assigned_active": [dict(r) for r in by_assigned_active],
            "comments": [dict(r) for r in comments],
        },
    }


@app.post("/teams/{team_id}/tasks")
def create_team_task(team_id: int, payload: TeamTaskPayload, authorization: Optional[str] = Header(default=None, alias="Authorization")):
    user = _require_user(authorization)
    title = payload.title.strip()
    if not title:
        raise HTTPException(status_code=400, detail="Title is required.")
    conn = _get_db()
    actor_role = _ensure_member(conn, team_id, user["id"])
    is_important = 1 if payload.is_important else 0
    is_urgent = _deadline_is_urgent(payload.due_date)
    priority = _priority_from_flags(is_urgent, is_important)
    created_date = _utc_now().date().isoformat()
    now = _iso(_utc_now())

    assigned_to = payload.assigned_to
    if assigned_to is not None:
        try:
            assigned_to = int(assigned_to)
        except Exception:
            conn.close()
            raise HTTPException(status_code=400, detail="Invalid assigned_to.")
        _require_team_role(actor_role, "owner")
        member = conn.execute(
            "SELECT 1 FROM team_members WHERE team_id = ? AND user_id = ?",
            (team_id, assigned_to),
        ).fetchone()
        if not member:
            conn.close()
            raise HTTPException(status_code=404, detail="Assignee is not a member of this team.")
    cur = conn.cursor()
    cur.execute(
        """INSERT INTO team_tasks (team_id, title, description, due_date, created_by, assigned_to,
                                   task_type, is_urgent, is_important, priority, created_date, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (team_id, title, payload.description or "", payload.due_date, user["id"], assigned_to,
         payload.task_type, is_urgent, is_important, priority, created_date, now)
    )
    conn.commit()
    task_id = cur.lastrowid
    _emit_team_event(
        conn,
        team_id,
        "task_created",
        user["id"],
        {"task_id": int(task_id), "title": title, "assigned_to": assigned_to},
    )
    conn.commit()
    conn.close()
    return {
        "id": task_id,
        "title": title,
        "due_date": payload.due_date,
        "task_type": payload.task_type,
        "is_urgent": is_urgent,
        "is_important": is_important,
        "priority": priority,
        "created_date": created_date,
        "assigned_to": assigned_to,
    }


@app.patch("/teams/{team_id}/tasks/{task_id}")
def update_team_task(team_id: int, task_id: int, payload: TeamTaskUpdatePayload, authorization: Optional[str] = Header(default=None, alias="Authorization")):
    user = _require_user(authorization)
    conn = _get_db()
    actor_role = _ensure_member(conn, team_id, user["id"])
    existing = conn.execute(
        "SELECT title, due_date, is_important, assigned_to, is_completed, created_by FROM team_tasks WHERE team_id = ? AND id = ?",
        (team_id, task_id)
    ).fetchone()
    if not existing:
        conn.close()
        raise HTTPException(status_code=404, detail="Task not found.")
    actor_is_owner = str(actor_role or "").strip().lower() == "owner"
    if not actor_is_owner:
        non_completion_change = bool(
            payload.title is not None
            or payload.description is not None
            or payload.due_date is not None
            or payload.task_type is not None
            or payload.is_important is not None
            or ("assigned_to" in getattr(payload, "model_fields_set", set()))
        )
        if non_completion_change:
            conn.close()
            raise HTTPException(status_code=403, detail="Only the team owner can edit task details.")
        if payload.is_completed is not None:
            try:
                assigned_to = int(existing["assigned_to"]) if existing["assigned_to"] is not None else None
            except Exception:
                assigned_to = None
            try:
                created_by = int(existing["created_by"]) if existing["created_by"] is not None else None
            except Exception:
                created_by = None
            if assigned_to is None:
                if created_by != int(user["id"]):
                    conn.close()
                    raise HTTPException(status_code=403, detail="Only the task creator can change completion status.")
            else:
                if assigned_to != int(user["id"]) and created_by != int(user["id"]):
                    conn.close()
                    raise HTTPException(status_code=403, detail="Only the assignee or creator can change completion status.")
    fields = []
    values = []
    changed = {}
    if payload.title is not None:
        fields.append("title = ?")
        values.append(payload.title.strip())
        changed["title"] = payload.title.strip()
    if payload.description is not None:
        fields.append("description = ?")
        values.append(payload.description)
        changed["description"] = payload.description
    if payload.due_date is not None:
        fields.append("due_date = ?")
        values.append(payload.due_date)
        changed["due_date"] = payload.due_date
    if payload.task_type is not None:
        fields.append("task_type = ?")
        values.append(payload.task_type)
        changed["task_type"] = payload.task_type
    if payload.is_important is not None:
        fields.append("is_important = ?")
        values.append(1 if payload.is_important else 0)
        changed["is_important"] = bool(payload.is_important)
    if "assigned_to" in getattr(payload, "model_fields_set", set()):
        _require_team_role(actor_role, "owner")
        assigned_to = payload.assigned_to
        if assigned_to is not None:
            try:
                assigned_to = int(assigned_to)
            except Exception:
                conn.close()
                raise HTTPException(status_code=400, detail="Invalid assigned_to.")
            member = conn.execute(
                "SELECT 1 FROM team_members WHERE team_id = ? AND user_id = ?",
                (team_id, assigned_to),
            ).fetchone()
            if not member:
                conn.close()
                raise HTTPException(status_code=404, detail="Assignee is not a member of this team.")
        fields.append("assigned_to = ?")
        values.append(assigned_to)
        changed["assigned_to"] = assigned_to
    if payload.is_completed is not None:
        fields.append("is_completed = ?")
        values.append(1 if payload.is_completed else 0)
        fields.append("completed_at = ?")
        values.append(_iso(_utc_now()) if payload.is_completed else None)
        fields.append("completed_by = ?")
        values.append(int(user["id"]) if payload.is_completed else None)
        changed["is_completed"] = bool(payload.is_completed)
        changed["completed_by"] = int(user["id"]) if payload.is_completed else None
    # Recompute urgency/priority when due date or importance changes
    if payload.due_date is not None or payload.is_important is not None:
        new_due = payload.due_date if payload.due_date is not None else existing["due_date"]
        new_imp = payload.is_important if payload.is_important is not None else bool(existing["is_important"])
        new_urg = _deadline_is_urgent(new_due)
        new_prio = _priority_from_flags(new_urg, 1 if new_imp else 0)
        fields.append("is_urgent = ?")
        values.append(new_urg)
        fields.append("priority = ?")
        values.append(new_prio)
        changed["is_urgent"] = int(new_urg)
        changed["priority"] = new_prio
    if fields:
        fields.append("updated_at = ?")
        values.append(_iso(_utc_now()))
        values.extend([team_id, task_id])
        conn.execute(
            f"UPDATE team_tasks SET {', '.join(fields)} WHERE team_id = ? AND id = ?",
            tuple(values)
        )
        _emit_team_event(conn, team_id, "task_updated", user["id"], {"task_id": int(task_id), "changes": changed})
        conn.commit()
    conn.close()
    return {"ok": True}


@app.delete("/teams/{team_id}/tasks/{task_id}")
def delete_team_task(team_id: int, task_id: int, authorization: Optional[str] = Header(default=None, alias="Authorization")):
    user = _require_user(authorization)
    conn = _get_db()
    actor_role = _ensure_member(conn, team_id, user["id"])
    _require_team_role(actor_role, "owner")
    conn.execute("DELETE FROM team_tasks WHERE team_id = ? AND id = ?", (team_id, task_id))
    _emit_team_event(conn, team_id, "task_deleted", user["id"], {"task_id": int(task_id)})
    conn.commit()
    conn.close()
    return {"ok": True}


@app.get("/teams/{team_id}/tasks/{task_id}/comments")
def list_team_task_comments(
    team_id: int,
    task_id: int,
    authorization: Optional[str] = Header(default=None, alias="Authorization"),
    limit: int = Query(default=100, ge=1, le=200),
    before_id: Optional[int] = Query(default=None, ge=1),
):
    user = _require_user(authorization)
    conn = _get_db()
    _ensure_member(conn, team_id, user["id"])
    task = conn.execute(
        "SELECT 1 FROM team_tasks WHERE team_id = ? AND id = ?",
        (team_id, task_id),
    ).fetchone()
    if not task:
        conn.close()
        raise HTTPException(status_code=404, detail="Task not found.")
    params = [team_id, task_id]
    where = "WHERE c.team_id = ? AND c.task_id = ?"
    if before_id is not None:
        where += " AND c.id < ?"
        params.append(before_id)
    params.append(limit)
    rows = conn.execute(
        f"""SELECT c.id, c.message, c.created_at, users.id as user_id, users.username
            FROM team_task_comments c
            JOIN users ON users.id = c.user_id
            {where}
            ORDER BY c.id DESC
            LIMIT ?""",
        tuple(params),
    ).fetchall()
    conn.close()
    comments = [dict(r) for r in rows]
    comments.reverse()
    return {"comments": comments}


@app.post("/teams/{team_id}/tasks/{task_id}/comments")
def add_team_task_comment(
    team_id: int,
    task_id: int,
    payload: TeamTaskCommentPayload,
    authorization: Optional[str] = Header(default=None, alias="Authorization"),
):
    user = _require_user(authorization)
    msg = (payload.message or "").strip()
    if not msg:
        raise HTTPException(status_code=400, detail="Comment cannot be empty.")
    if len(msg) > 2000:
        raise HTTPException(status_code=400, detail="Comment is too long.")
    conn = _get_db()
    _ensure_member(conn, team_id, user["id"])
    task = conn.execute(
        "SELECT title FROM team_tasks WHERE team_id = ? AND id = ?",
        (team_id, task_id),
    ).fetchone()
    if not task:
        conn.close()
        raise HTTPException(status_code=404, detail="Task not found.")
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO team_task_comments (team_id, task_id, user_id, message) VALUES (?, ?, ?, ?)",
        (team_id, task_id, user["id"], msg),
    )
    comment_id = cur.lastrowid
    row = conn.execute(
        """SELECT c.id, c.message, c.created_at, users.id as user_id, users.username
           FROM team_task_comments c
           JOIN users ON users.id = c.user_id
           WHERE c.id = ?""",
        (comment_id,),
    ).fetchone()
    _emit_team_event(conn, team_id, "task_comment", user["id"], {"task_id": int(task_id), "comment_id": int(comment_id)})
    conn.commit()
    conn.close()
    return {"comment": dict(row) if row else None}


@app.get("/teams/{team_id}/pomodoro")
def get_pomodoro_state(team_id: int, authorization: Optional[str] = Header(default=None, alias="Authorization")):
    user = _require_user(authorization)
    conn = _get_db()
    _ensure_member(conn, team_id, user["id"])
    row = conn.execute(
        "SELECT team_id, phase, status, started_at, duration_min, started_by, ended_at FROM team_pomodoro WHERE team_id = ?",
        (team_id,)
    ).fetchone()
    now = _utc_now()
    if not row:
        conn.close()
        return {"status": "idle", "server_time": _iso(now)}
    status = row["status"] or "idle"
    remaining_seconds = None
    if status == "running" and row["started_at"] and row["duration_min"]:
        try:
            started_at = datetime.fromisoformat(row["started_at"])
        except Exception:
            started_at = now
        elapsed = (now - started_at).total_seconds()
        remaining_seconds = int(max(0, row["duration_min"] * 60 - elapsed))
        if remaining_seconds <= 0:
            status = "completed"
            conn.execute(
                "UPDATE team_pomodoro SET status = ?, ended_at = ?, updated_at = ? WHERE team_id = ?",
                ("completed", _iso(now), _iso(now), team_id)
            )
            conn.commit()
    started_by_name = None
    if row["started_by"]:
        user_row = conn.execute("SELECT username FROM users WHERE id = ?", (row["started_by"],)).fetchone()
        if user_row:
            started_by_name = user_row["username"]
    conn.close()
    return {
        "status": status,
        "phase": row["phase"],
        "started_at": row["started_at"],
        "duration_min": row["duration_min"],
        "started_by": started_by_name,
        "remaining_seconds": remaining_seconds,
        "server_time": _iso(now),
    }


@app.get("/teams/{team_id}/messages")
def list_team_messages(
    team_id: int,
    authorization: Optional[str] = Header(default=None, alias="Authorization"),
    limit: int = Query(default=50, ge=1, le=200),
    before_id: Optional[int] = Query(default=None, ge=1),
):
    user = _require_user(authorization)
    conn = _get_db()
    _ensure_member(conn, team_id, user["id"])
    params = [team_id]
    where = "WHERE team_messages.team_id = ?"
    if before_id is not None:
        where += " AND team_messages.id < ?"
        params.append(before_id)
    params.append(limit)
    rows = conn.execute(
        f"""SELECT team_messages.id, team_messages.message, team_messages.created_at,
                   users.id as user_id, users.username
            FROM team_messages
            JOIN users ON users.id = team_messages.user_id
            {where}
            ORDER BY team_messages.id DESC
            LIMIT ?""",
        tuple(params)
    ).fetchall()
    conn.close()
    messages = [dict(row) for row in rows]
    messages.reverse()
    return {"messages": messages}


@app.post("/teams/{team_id}/messages")
def send_team_message(team_id: int, payload: TeamMessagePayload, authorization: Optional[str] = Header(default=None, alias="Authorization")):
    user = _require_user(authorization)
    msg = (payload.message or "").strip()
    if not msg:
        raise HTTPException(status_code=400, detail="Message cannot be empty.")
    if len(msg) > 1000:
        raise HTTPException(status_code=400, detail="Message is too long.")
    conn = _get_db()
    _ensure_member(conn, team_id, user["id"])
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO team_messages (team_id, user_id, message) VALUES (?, ?, ?)",
        (team_id, user["id"], msg)
    )
    msg_id = cur.lastrowid
    row = conn.execute(
        """SELECT team_messages.id, team_messages.message, team_messages.created_at,
                  users.id as user_id, users.username
           FROM team_messages
           JOIN users ON users.id = team_messages.user_id
           WHERE team_messages.id = ?""",
        (msg_id,)
    ).fetchone()
    _emit_team_event(conn, team_id, "team_message", user["id"], {"message_id": int(msg_id)})
    conn.commit()
    conn.close()
    return {"message": dict(row) if row else None}


@app.post("/teams/{team_id}/pomodoro/start")
def start_pomodoro(team_id: int, payload: PomodoroStartPayload, authorization: Optional[str] = Header(default=None, alias="Authorization")):
    user = _require_user(authorization)
    phase = payload.phase.strip().lower()
    if phase not in ("focus", "break"):
        raise HTTPException(status_code=400, detail="Phase must be 'focus' or 'break'.")
    if payload.duration_min < 1 or payload.duration_min > 240:
        raise HTTPException(status_code=400, detail="Duration must be between 1 and 240 minutes.")
    conn = _get_db()
    _ensure_member(conn, team_id, user["id"])
    now = _utc_now()
    conn.execute(
        """INSERT INTO team_pomodoro (team_id, phase, status, started_at, duration_min, started_by, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(team_id) DO UPDATE SET
             phase = excluded.phase,
             status = excluded.status,
             started_at = excluded.started_at,
             duration_min = excluded.duration_min,
             started_by = excluded.started_by,
             updated_at = excluded.updated_at,
             ended_at = NULL""",
        (team_id, phase, "running", _iso(now), payload.duration_min, user["id"], _iso(now))
    )
    conn.commit()
    conn.close()
    return {"status": "running", "phase": phase, "started_at": _iso(now), "duration_min": payload.duration_min}


@app.post("/teams/{team_id}/pomodoro/stop")
def stop_pomodoro(team_id: int, authorization: Optional[str] = Header(default=None, alias="Authorization")):
    user = _require_user(authorization)
    conn = _get_db()
    _ensure_member(conn, team_id, user["id"])
    now = _utc_now()
    conn.execute(
        "UPDATE team_pomodoro SET status = ?, ended_at = ?, updated_at = ? WHERE team_id = ?",
        ("stopped", _iso(now), _iso(now), team_id)
    )
    conn.commit()
    conn.close()
    return {"status": "stopped"}


if __name__ == "__main__":
    import uvicorn

    host = os.environ.get("PRODSMART_HOST") or "0.0.0.0"
    try:
        port = int(os.environ.get("PRODSMART_PORT") or "8000")
    except Exception:
        port = 8000

    def _log(msg: str) -> None:
        try:
            print(f"[ProdSmart Server] {msg}", flush=True)
        except Exception:
            pass

    parent_handle = None
    if os.name == "nt":
        raw = os.environ.get("PRODSMART_PARENT_HANDLE")
        if raw:
            try:
                parent_handle = int(raw)
                if parent_handle <= 0:
                    parent_handle = None
            except Exception:
                parent_handle = None

    parent_pid = None
    parent_pid_raw = os.environ.get("PRODSMART_PARENT_PID")
    if parent_pid_raw:
        try:
            parent_pid = int(parent_pid_raw)
            if parent_pid <= 0:
                parent_pid = None
        except Exception:
            parent_pid = None

    parent_create_time = None
    parent_create_time_raw = os.environ.get("PRODSMART_PARENT_CREATE_TIME")
    if parent_create_time_raw:
        try:
            parent_create_time = int(parent_create_time_raw)
            if parent_create_time <= 0:
                parent_create_time = None
        except Exception:
            parent_create_time = None

    if parent_handle is not None:
        _log(f"Parent watchdog enabled (handle={parent_handle}).")
    elif parent_pid is not None:
        _log(f"Parent watchdog enabled (pid={parent_pid}, create_time={parent_create_time}).")
    else:
        _log("Parent watchdog disabled (no parent info).")

    access_log = str(os.environ.get("PRODSMART_ACCESS_LOG") or "").strip() == "1"
    log_level = str(os.environ.get("PRODSMART_LOG_LEVEL") or "info").strip().lower() or "info"

    config = uvicorn.Config(
        app,
        host=host,
        port=port,
        reload=False,
        access_log=access_log,
        log_level=log_level,
    )
    server = uvicorn.Server(config)
    try:
        app.state.uvicorn_server = server
    except Exception:
        pass

    if parent_handle is not None or parent_pid is not None:
        def _trigger_shutdown() -> None:
            _log("Parent is gone; shutting down.")
            # Graceful shutdown if possible...
            try:
                server.should_exit = True
            except Exception:
                pass
            # ...but hard-exit if we get stuck.
            deadline = time.monotonic() + 2.0
            while time.monotonic() < deadline:
                time.sleep(0.05)
            os._exit(0)

        def _watch_parent_handle_windows() -> None:
            try:
                import ctypes
                from ctypes import wintypes

                kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

                WaitForSingleObject = kernel32.WaitForSingleObject
                WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)
                WaitForSingleObject.restype = wintypes.DWORD

                INFINITE = 0xFFFFFFFF
                _log("Waiting for parent handle to signal.")
                WaitForSingleObject(wintypes.HANDLE(int(parent_handle)), INFINITE)
                _trigger_shutdown()
            except Exception:
                _log("Parent-handle watchdog failed; shutting down.")
                _trigger_shutdown()

        def _watch_parent_posix() -> None:
            while True:
                time.sleep(1.0)
                try:
                    os.kill(parent_pid, 0)
                except PermissionError:
                    continue
                except Exception:
                    _trigger_shutdown()

        def _watch_parent_windows() -> None:
            try:
                import ctypes
                from ctypes import wintypes

                kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

                OpenProcess = kernel32.OpenProcess
                OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
                OpenProcess.restype = wintypes.HANDLE

                CloseHandle = kernel32.CloseHandle
                CloseHandle.argtypes = (wintypes.HANDLE,)
                CloseHandle.restype = wintypes.BOOL

                WaitForSingleObject = kernel32.WaitForSingleObject
                WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)
                WaitForSingleObject.restype = wintypes.DWORD

                GetProcessTimes = kernel32.GetProcessTimes
                GetProcessTimes.argtypes = (
                    wintypes.HANDLE,
                    ctypes.POINTER(wintypes.FILETIME),
                    ctypes.POINTER(wintypes.FILETIME),
                    ctypes.POINTER(wintypes.FILETIME),
                    ctypes.POINTER(wintypes.FILETIME),
                )
                GetProcessTimes.restype = wintypes.BOOL

                SYNCHRONIZE = 0x00100000
                PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
                INFINITE = 0xFFFFFFFF

                handle = OpenProcess(SYNCHRONIZE | PROCESS_QUERY_LIMITED_INFORMATION, False, int(parent_pid))
                has_query = True
                if not handle:
                    handle = OpenProcess(SYNCHRONIZE, False, int(parent_pid))
                    has_query = False
                if not handle:
                    _log("OpenProcess failed; shutting down.")
                    _trigger_shutdown()
                    return

                try:
                    if parent_create_time is not None and has_query:
                        creation = wintypes.FILETIME()
                        exit_time = wintypes.FILETIME()
                        kernel_time = wintypes.FILETIME()
                        user_time = wintypes.FILETIME()
                        ok = GetProcessTimes(
                            handle,
                            ctypes.byref(creation),
                            ctypes.byref(exit_time),
                            ctypes.byref(kernel_time),
                            ctypes.byref(user_time),
                        )
                        if not ok:
                            _log("GetProcessTimes failed; shutting down.")
                            _trigger_shutdown()
                            return
                        ft = (int(creation.dwHighDateTime) << 32) | int(creation.dwLowDateTime)
                        if int(ft) != int(parent_create_time):
                            _log("Parent PID reused; shutting down.")
                            _trigger_shutdown()
                            return

                    # When the parent dies, the process handle becomes signaled.
                    _log("Waiting for parent process handle to signal.")
                    WaitForSingleObject(handle, INFINITE)
                    _trigger_shutdown()
                finally:
                    try:
                        CloseHandle(handle)
                    except Exception:
                        pass
            except Exception:
                # If we cannot reliably watch the parent, do not risk leaving a
                # detached server running forever.
                _log("Watchdog init failed; shutting down.")
                _trigger_shutdown()

        if os.name == "nt" and parent_handle is not None:
            watch_fn = _watch_parent_handle_windows
        else:
            watch_fn = _watch_parent_windows if os.name == "nt" else _watch_parent_posix

        threading.Thread(
            target=watch_fn,
            name="prodsmart-parent-watchdog",
            daemon=True,
        ).start()

    server.run()
