import json
import os
import socket
import urllib.request
import urllib.error
import base64
from urllib.parse import urlparse, quote

DEFAULT_BASE_URL = "http://127.0.0.1:8000"


class ApiError(Exception):
    def __init__(self, message, status=None):
        super().__init__(message)
        self.status = status


def get_project_root():
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def get_project_settings_path():
    return os.path.join(get_project_root(), "settings.json")


def _settings_path():
    # Prefer the project root settings file so app behavior does not depend on cwd.
    project_path = get_project_settings_path()
    if os.path.exists(project_path):
        return project_path
    cwd_path = os.path.join(os.getcwd(), "settings.json")
    if os.path.exists(cwd_path):
        return cwd_path
    return project_path


def load_settings():
    path = _settings_path()
    if os.path.exists(path):
        try:
            with open(path, "r") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def save_settings(data):
    project_path = get_project_settings_path()
    try:
        with open(project_path, "w") as f:
            json.dump(data, f, indent=4)
        return
    except Exception:
        pass

    # Fallback: keep the previous cwd behavior if project root isn't writable.
    try:
        cwd_path = os.path.join(os.getcwd(), "settings.json")
        with open(cwd_path, "w") as f:
            json.dump(data, f, indent=4)
    except Exception:
        pass


def normalize_base_url(url):
    raw = str(url or "").strip()
    if not raw:
        return DEFAULT_BASE_URL

    if "://" not in raw:
        raw = f"http://{raw}"

    try:
        parsed = urlparse(raw)
        scheme = (parsed.scheme or "http").lower()
        host = (parsed.hostname or "").strip()
        port = parsed.port

        if host in ("0.0.0.0", ""):
            host = "127.0.0.1"

        if not host:
            return DEFAULT_BASE_URL

        if port is None:
            if host in ("127.0.0.1", "localhost"):
                port = 8000
            else:
                port = 443 if scheme == "https" else 80

        return f"{scheme}://{host}:{int(port)}"
    except Exception:
        return DEFAULT_BASE_URL


def get_base_url():
    data = load_settings()
    base = str(data.get("cloud_base_url") or "").strip()
    return normalize_base_url(base or DEFAULT_BASE_URL)


def set_base_url(url):
    data = load_settings()
    data["cloud_base_url"] = normalize_base_url(url or DEFAULT_BASE_URL)
    save_settings(data)


def get_token():
    data = load_settings()
    return data.get("cloud_token")


def set_token(token, user_id=None, username=None):
    data = load_settings()
    data["cloud_token"] = token
    if user_id is not None:
        data["cloud_user_id"] = user_id
    if username is not None:
        data["cloud_username"] = username
    save_settings(data)


def clear_token():
    data = load_settings()
    for key in ("cloud_token", "cloud_user_id", "cloud_username"):
        if key in data:
            data.pop(key, None)
    save_settings(data)


def _request(method, path, payload=None, token=None, timeout=8):
    base_url = get_base_url().rstrip("/")
    url = f"{base_url}{path}"
    headers = {"Content-Type": "application/json"}
    auth_token = token or get_token()
    if auth_token:
        headers["Authorization"] = f"Bearer {auth_token}"
    data = None
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8") if resp.readable() else ""
            if not body:
                return None
            try:
                return json.loads(body)
            except Exception:
                return None
    except urllib.error.HTTPError as e:
        body = ""
        try:
            body = e.read().decode("utf-8")
        except Exception:
            body = ""
        message = f"HTTP {e.code}"
        if body:
            try:
                data = json.loads(body)
                if isinstance(data, dict) and data.get("detail"):
                    message = str(data.get("detail"))
                else:
                    message = body
            except Exception:
                message = body
        raise ApiError(message, status=e.code)
    except (TimeoutError, socket.timeout):
        raise ApiError("Request timed out. Is the server running?")
    except urllib.error.URLError as e:
        raise ApiError(f"Network error: {e.reason}")
    except OSError as e:
        raise ApiError(f"Network error: {e}")


def _request_bytes(method, path, *, token=None, timeout=8, headers=None):
    base_url = get_base_url().rstrip("/")
    url = f"{base_url}{path}"
    req_headers = dict(headers or {})
    auth_token = token or get_token()
    if auth_token:
        req_headers["Authorization"] = f"Bearer {auth_token}"
    req = urllib.request.Request(url, headers=req_headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read() if resp.readable() else b""
            content_type = None
            try:
                content_type = resp.headers.get("Content-Type")
            except Exception:
                content_type = None
            return body, content_type
    except urllib.error.HTTPError as e:
        # Let callers treat 404 as "no data".
        if int(getattr(e, "code", 0) or 0) == 404:
            return None, None
        body = ""
        try:
            body = e.read().decode("utf-8")
        except Exception:
            body = ""
        message = f"HTTP {e.code}"
        if body:
            try:
                data = json.loads(body)
                if isinstance(data, dict) and data.get("detail"):
                    message = str(data.get("detail"))
                else:
                    message = body
            except Exception:
                message = body
        raise ApiError(message, status=e.code)
    except (TimeoutError, socket.timeout):
        raise ApiError("Request timed out. Is the server running?")
    except urllib.error.URLError as e:
        raise ApiError(f"Network error: {e.reason}")
    except OSError as e:
        raise ApiError(f"Network error: {e}")


def api_register(username, password):
    return _request("POST", "/auth/register", {"username": username, "password": password})


def api_login(username, password):
    res = _request("POST", "/auth/login", {"username": username, "password": password})
    if isinstance(res, dict) and res.get("token"):
        set_token(res.get("token"), res.get("user_id"), res.get("username"))
    return res


def api_me():
    return _request("GET", "/me")


def api_search_users(query, limit=10):
    params = []
    q = str(query or "").strip()
    if q:
        params.append(f"q={quote(q)}")
    if limit is not None:
        params.append(f"limit={int(limit)}")
    query_str = f"?{'&'.join(params)}" if params else ""
    return _request("GET", f"/users/search{query_str}")


def api_get_user_avatar(user_id, timeout=6):
    try:
        uid = int(user_id)
    except Exception:
        return None
    body, _ct = _request_bytes("GET", f"/users/{uid}/avatar", timeout=timeout)
    return body


def api_get_user_profile(user_id, timeout=8):
    try:
        uid = int(user_id)
    except Exception:
        raise ApiError("Invalid user id.")
    return _request("GET", f"/users/{uid}/profile", timeout=timeout)


def api_get_my_profile(timeout=8):
    return _request("GET", "/users/me/profile", timeout=timeout)


def api_set_my_profile(profile: dict, timeout=10):
    if not isinstance(profile, dict):
        profile = {}
    return _request("POST", "/users/me/profile", profile, timeout=timeout)


def api_set_my_avatar(image_bytes, timeout=10):
    if not image_bytes:
        return {"ok": False}
    try:
        b64 = base64.b64encode(image_bytes).decode("ascii")
    except Exception:
        return {"ok": False}
    return _request("POST", "/users/me/avatar", {"image_base64": b64}, timeout=timeout)


def api_change_password(current_password, new_password):
    return _request(
        "POST",
        "/auth/change_password",
        {"current_password": current_password, "new_password": new_password},
    )


def api_ping(timeout=8):
    return _request("GET", "/", timeout=timeout)


def _is_local_url(url: str) -> bool:
    try:
        parsed = urlparse(normalize_base_url(url or DEFAULT_BASE_URL))
        host = (parsed.hostname or "").strip().lower()
        return host in ("127.0.0.1", "localhost", "0.0.0.0")
    except Exception:
        return False


def api_register_app_instance(pid: int, create_time: int | None = None, timeout: float = 1.0):
    """Tell the local server to stop when this app process exits."""
    url = get_base_url()
    if not _is_local_url(url):
        return {"ok": False, "skipped": True}
    payload = {"pid": int(pid)}
    if create_time is not None:
        try:
            payload["create_time"] = int(create_time)
        except Exception:
            pass
    return _request("POST", "/__internal/register_app", payload, timeout=timeout)


def api_shutdown_local_server(timeout: float = 1.0):
    """Ask the local server to shut down (best-effort)."""
    url = get_base_url()
    if not _is_local_url(url):
        return {"ok": False, "skipped": True}
    return _request("POST", "/__internal/shutdown", {}, timeout=timeout)


def check_server_reachable(base_url=None, timeout=0.5):
    url = normalize_base_url(base_url or get_base_url() or DEFAULT_BASE_URL)
    try:
        parsed = urlparse(url)
        host = parsed.hostname or "127.0.0.1"
        if host in ("0.0.0.0", ""):
            host = "127.0.0.1"
        scheme = (parsed.scheme or "http").lower()
        port = parsed.port or (443 if scheme == "https" else 80)
        try:
            sock = socket.create_connection((host, port), timeout=timeout)
            try:
                sock.close()
            except Exception:
                pass
            return True
        except Exception:
            # Fallback for localhost/127.0.0.1 differences
            alt_host = None
            if host == "127.0.0.1":
                alt_host = "localhost"
            elif host == "localhost":
                alt_host = "127.0.0.1"
            if alt_host:
                try:
                    sock = socket.create_connection((alt_host, port), timeout=timeout)
                    try:
                        sock.close()
                    except Exception:
                        pass
                    return True
                except Exception:
                    return False
            return False
    except Exception:
        return False
def check_server_ready(base_url=None, timeout=0.6):
    url = normalize_base_url(base_url or get_base_url() or DEFAULT_BASE_URL).rstrip("/")
    try:
        req = urllib.request.Request(f"{url}/", method="GET")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8") if resp.readable() else ""
            try:
                data = json.loads(body) if body else None
            except Exception:
                data = None
            return isinstance(data, dict) and data.get("status") == "ok"
    except Exception:
        return False
def check_server_http(base_url=None, timeout=0.6):
    url = normalize_base_url(base_url or get_base_url() or DEFAULT_BASE_URL).rstrip("/")
    try:
        req = urllib.request.Request(f"{url}/", method="GET")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8") if resp.readable() else ""
            try:
                data = json.loads(body) if body else None
            except Exception:
                data = None
            return isinstance(data, dict) and data.get("status") == "ok"
    except Exception:
        return False


def get_server_state(base_url=None, timeout=0.6):
    url = normalize_base_url(base_url or get_base_url() or DEFAULT_BASE_URL)
    if not check_server_reachable(url, timeout=timeout):
        return "not running"
    if check_server_http(url, timeout=timeout):
        return "running"
    return "starting"


def api_list_teams():
    return _request("GET", "/teams")


def api_create_team(name):
    return _request("POST", "/teams", {"name": name})


def api_join_team(code):
    return _request("POST", "/teams/join", {"code": code})


def api_list_team_join_requests(team_id):
    return _request("GET", f"/teams/{int(team_id)}/join-requests")


def api_accept_team_join_request(team_id, request_id):
    return _request("POST", f"/teams/{int(team_id)}/join-requests/{int(request_id)}/accept", {})


def api_reject_team_join_request(team_id, request_id):
    return _request("POST", f"/teams/{int(team_id)}/join-requests/{int(request_id)}/reject", {})


def api_invite_team_member(team_id, username):
    return _request("POST", f"/teams/{int(team_id)}/invites", {"username": username})


def api_get_team(team_id):
    return _request("GET", f"/teams/{team_id}")


def api_list_team_tasks(team_id):
    return _request("GET", f"/teams/{team_id}/tasks")


def api_team_analytics(team_id):
    return _request("GET", f"/teams/{team_id}/analytics")


def api_create_team_task(team_id, title, description="", due_date=None, is_important=None, task_type=None, assigned_to=None):
    payload = {
        "title": title,
        "description": description or "",
        "due_date": due_date,
        "is_important": is_important,
        "task_type": task_type,
        "assigned_to": assigned_to,
    }
    return _request("POST", f"/teams/{team_id}/tasks", payload)


def api_update_team_member_role(team_id, user_id, role):
    payload = {"role": role}
    return _request("PATCH", f"/teams/{team_id}/members/{int(user_id)}", payload)


def api_remove_team_member(team_id, user_id):
    return _request("DELETE", f"/teams/{team_id}/members/{int(user_id)}")


def api_list_team_events(team_id, after_id=0, limit=50):
    params = []
    if after_id is not None:
        params.append(f"after_id={int(after_id)}")
    if limit is not None:
        params.append(f"limit={int(limit)}")
    query = f"?{'&'.join(params)}" if params else ""
    return _request("GET", f"/teams/{team_id}/events{query}")


def api_list_team_task_comments(team_id, task_id, limit=100, before_id=None):
    params = []
    if limit is not None:
        params.append(f"limit={int(limit)}")
    if before_id is not None:
        params.append(f"before_id={int(before_id)}")
    query = f"?{'&'.join(params)}" if params else ""
    return _request("GET", f"/teams/{team_id}/tasks/{int(task_id)}/comments{query}")


def api_add_team_task_comment(team_id, task_id, message):
    payload = {"message": message}
    return _request("POST", f"/teams/{team_id}/tasks/{int(task_id)}/comments", payload)


def api_update_team_task(team_id, task_id, **fields):
    return _request("PATCH", f"/teams/{team_id}/tasks/{task_id}", fields)


def api_delete_team_task(team_id, task_id):
    return _request("DELETE", f"/teams/{team_id}/tasks/{task_id}")


def api_list_team_members(team_id):
    return _request("GET", f"/teams/{team_id}/members")


def api_list_team_messages(team_id, limit=50, before_id=None):
    params = []
    if limit is not None:
        params.append(f"limit={int(limit)}")
    if before_id is not None:
        params.append(f"before_id={int(before_id)}")
    query = f"?{'&'.join(params)}" if params else ""
    return _request("GET", f"/teams/{team_id}/messages{query}")


def api_send_team_message(team_id, message):
    return _request("POST", f"/teams/{team_id}/messages", {"message": message})


def api_send_team_alert(team_id, message):
    return _request("POST", f"/teams/{team_id}/alerts", {"message": message})


def api_get_team_pomodoro(team_id):
    return _request("GET", f"/teams/{team_id}/pomodoro")


def api_start_team_pomodoro(team_id, phase, duration_min):
    payload = {"phase": phase, "duration_min": duration_min}
    return _request("POST", f"/teams/{team_id}/pomodoro/start", payload)


def api_stop_team_pomodoro(team_id):
    return _request("POST", f"/teams/{team_id}/pomodoro/stop", {})
