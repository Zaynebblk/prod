import sqlite3
import os
import hashlib
import json
import shutil

# Get the absolute path to the project root directory
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

_LEGACY_DB_FILENAME = "prodsmart.db"
_USER_DB_PREFIX = "prodsmart_user_"
_DB_SCHEMA_VERSION = 2
_SETTINGS_FILENAME = "settings.json"

# Legacy import tracking (per-user).
_LEGACY_IMPORTED_USERS_KEY = "local_db_legacy_imported_users"
_LEGACY_SKIPPED_USERS_KEY = "local_db_legacy_skipped_users"
# Back-compat: older builds stored a single boolean flag after auto-migrating once.
_LEGACY_AUTO_MIGRATION_FLAG_KEY = "local_db_migrated_to_user_db"
# Deprecated: kept so older helper code still imports cleanly.
_MIGRATION_FLAG_KEY = _LEGACY_AUTO_MIGRATION_FLAG_KEY


def _settings_path():
    return os.path.join(BASE_DIR, _SETTINGS_FILENAME)


def _load_settings():
    path = _settings_path()
    if os.path.exists(path):
        try:
            with open(path, "r") as f:
                data = json.load(f)
                return data if isinstance(data, dict) else {}
        except Exception:
            return {}
    return {}


def _save_settings(data):
    path = _settings_path()
    try:
        with open(path, "w") as f:
            json.dump(data if isinstance(data, dict) else {}, f, indent=4)
    except Exception:
        pass


def _get_cloud_user_id(settings: dict | None = None) -> int | None:
    data = settings if isinstance(settings, dict) else _load_settings()
    raw = data.get("cloud_user_id") if isinstance(data, dict) else None
    try:
        uid = int(raw)
    except Exception:
        return None
    return uid if uid > 0 else None


def get_legacy_db_path() -> str:
    return os.path.join(BASE_DIR, _LEGACY_DB_FILENAME)


def get_user_db_path(user_id: int) -> str:
    try:
        uid = int(user_id)
    except Exception:
        uid = 0
    if uid <= 0:
        return get_legacy_db_path()
    return os.path.join(BASE_DIR, f"{_USER_DB_PREFIX}{uid}.db")


def get_db_path():
    """Return the local SQLite database path.

    Behavior:
    - If a cloud user is logged in (cloud_user_id present in settings.json), use a per-user DB file.
    - Otherwise, use the legacy shared DB file (prodsmart.db).
    """
    settings = _load_settings()
    uid = _get_cloud_user_id(settings)
    if uid:
        return get_user_db_path(uid)
    return get_legacy_db_path()


def _maybe_migrate_legacy_db(user_db_path: str, settings: dict) -> None:
    """One-time migration: copy the legacy shared DB into the first user DB.

    Prior versions stored all local tasks/history in a single prodsmart.db file, shared by all accounts.
    We can’t reliably split legacy rows by user, so we migrate the whole legacy DB into the first
    account that initializes a user DB and mark it as migrated.
    """
    if not isinstance(settings, dict):
        return
    if settings.get(_MIGRATION_FLAG_KEY):
        return

    legacy_path = os.path.join(BASE_DIR, _LEGACY_DB_FILENAME)
    if os.path.exists(user_db_path) or not os.path.exists(legacy_path):
        return

    # Only migrate if legacy DB seems to have content.
    should_migrate = False
    try:
        conn = sqlite3.connect(legacy_path)
        cur = conn.cursor()
        cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='tasks'")
        if cur.fetchone():
            cur.execute("SELECT COUNT(1) FROM tasks")
            count = int(cur.fetchone()[0] or 0)
            should_migrate = count > 0
        conn.close()
    except Exception:
        should_migrate = False

    if not should_migrate:
        settings[_MIGRATION_FLAG_KEY] = True
        _save_settings(settings)
        return

    try:
        shutil.copy2(legacy_path, user_db_path)
        settings[_MIGRATION_FLAG_KEY] = True
        _save_settings(settings)
    except Exception:
        # Best-effort; if migration fails, the app will create an empty user DB.
        return


def _db_has_any_app_data(db_path: str) -> bool:
    """Return True if the SQLite DB appears to contain app data (tasks or pomodoro sessions)."""
    if not db_path or not os.path.exists(db_path):
        return False
    conn = None
    try:
        conn = sqlite3.connect(db_path)
        cur = conn.cursor()
        for table in ("tasks", "pomodoro_sessions"):
            cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,))
            if not cur.fetchone():
                continue
            try:
                cur.execute(f"SELECT COUNT(1) FROM {table}")
                count = int(cur.fetchone()[0] or 0)
            except Exception:
                count = 0
            if count > 0:
                return True
        return False
    except Exception:
        return False
    finally:
        try:
            if conn is not None:
                conn.close()
        except Exception:
            pass


def legacy_db_has_data() -> bool:
    return _db_has_any_app_data(get_legacy_db_path())


def user_db_has_any_data(user_id: int) -> bool:
    return _db_has_any_app_data(get_user_db_path(user_id))


def _get_user_id_list(settings: dict, key: str) -> list[int]:
    raw = settings.get(key)
    if isinstance(raw, list):
        items = raw
    elif isinstance(raw, tuple):
        items = list(raw)
    else:
        items = []
    out: list[int] = []
    for item in items:
        try:
            uid = int(item)
        except Exception:
            continue
        if uid > 0:
            out.append(uid)
    return out


def get_legacy_import_decision(user_id: int) -> str | None:
    """Return 'imported', 'skipped', or None for this user."""
    try:
        uid = int(user_id)
    except Exception:
        return None
    if uid <= 0:
        return None

    settings = _load_settings()
    imported = set(_get_user_id_list(settings, _LEGACY_IMPORTED_USERS_KEY))
    if uid in imported:
        return "imported"

    skipped = set(_get_user_id_list(settings, _LEGACY_SKIPPED_USERS_KEY))
    if uid in skipped:
        return "skipped"

    return None


def set_legacy_import_decision(user_id: int, decision: str) -> None:
    """Persist a per-user decision so the UI doesn't re-prompt."""
    try:
        uid = int(user_id)
    except Exception:
        return
    if uid <= 0:
        return

    settings = _load_settings()
    imported = set(_get_user_id_list(settings, _LEGACY_IMPORTED_USERS_KEY))
    skipped = set(_get_user_id_list(settings, _LEGACY_SKIPPED_USERS_KEY))

    if decision == "imported":
        imported.add(uid)
        skipped.discard(uid)
    elif decision == "skipped":
        skipped.add(uid)
        imported.discard(uid)
    else:
        return

    settings[_LEGACY_IMPORTED_USERS_KEY] = sorted(imported)
    settings[_LEGACY_SKIPPED_USERS_KEY] = sorted(skipped)
    _save_settings(settings)


def import_legacy_db_to_user(
    user_id: int,
    *,
    overwrite_if_empty: bool = True,
    backup_existing: bool = True,
) -> tuple[bool, str]:
    """Copy prodsmart.db into the per-user DB for user_id (best-effort).

    This is intentionally explicit (opt-in). Older versions stored all local tasks/history
    in a single shared DB file; we cannot reliably split legacy rows by user.
    """
    try:
        uid = int(user_id)
    except Exception:
        return False, "Invalid user id."
    if uid <= 0:
        return False, "Invalid user id."

    legacy_path = get_legacy_db_path()
    if not os.path.exists(legacy_path) or not legacy_db_has_data():
        return False, "No legacy local data found."

    user_db_path = get_user_db_path(uid)
    if os.path.exists(user_db_path):
        if user_db_has_any_data(uid):
            return False, "This account already has local data."
        if not overwrite_if_empty:
            return False, "Local DB already exists."
        if backup_existing:
            try:
                from datetime import datetime

                ts = datetime.now().strftime("%Y%m%d_%H%M%S")
                backup_path = f"{user_db_path}.bak_{ts}"
                shutil.copy2(user_db_path, backup_path)
            except Exception:
                pass

    try:
        shutil.copy2(legacy_path, user_db_path)
    except Exception as e:
        return False, f"Copy failed: {e}"

    # Ensure schema upgrades are applied after copying.
    try:
        conn = sqlite3.connect(user_db_path)
        _ensure_schema(conn)
        conn.close()
    except Exception:
        pass

    try:
        set_legacy_import_decision(uid, "imported")
    except Exception:
        pass

    return True, "Imported legacy local data into this account."


def _table_exists(conn: sqlite3.Connection, schema: str, table: str) -> bool:
    try:
        row = conn.execute(
            f"SELECT 1 FROM {schema}.sqlite_master WHERE type='table' AND name=? LIMIT 1",
            (str(table),),
        ).fetchone()
        return bool(row)
    except Exception:
        return False


def _table_columns(conn: sqlite3.Connection, schema: str, table: str) -> list[str]:
    try:
        rows = conn.execute(f"PRAGMA {schema}.table_info('{table}')").fetchall()
    except Exception:
        return []
    cols: list[str] = []
    for r in rows or []:
        try:
            name = str(r[1] or "").strip()
        except Exception:
            name = ""
        if name:
            cols.append(name)
    return cols


def _merge_table_with_offsets(
    conn: sqlite3.Connection,
    *,
    table: str,
    src_schema: str,
    dst_schema: str,
    id_offset: int = 0,
    fk_task_offset: int = 0,
    fk_task_cols: tuple[str, ...] = (),
    task_anchor_cols: tuple[str, ...] = (),
    ignore_conflicts: bool = False,
) -> None:
    """Merge rows from src_schema.table into dst_schema.table with ID remapping.

    The merge preserves intra-source relationships by applying the same `fk_task_offset`
    to task-referencing columns.
    """
    if not _table_exists(conn, src_schema, table) or not _table_exists(conn, dst_schema, table):
        return

    dst_cols = _table_columns(conn, dst_schema, table)
    if not dst_cols:
        return

    src_cols = set(_table_columns(conn, src_schema, table))

    def qident(name: str) -> str:
        # Double-quote identifiers to avoid issues with reserved words like "date".
        safe = str(name or "").replace('"', '""')
        return f'"{safe}"'

    select_exprs: list[str] = []
    for col in dst_cols:
        if col == "id" and "id" in src_cols:
            select_exprs.append(
                f"{src_schema}.{qident(table)}.{qident('id')} + {int(id_offset)} AS {qident('id')}"
            )
            continue

        if col in fk_task_cols and col in src_cols:
            select_exprs.append(
                f"CASE WHEN {src_schema}.{qident(table)}.{qident(col)} IS NULL THEN NULL ELSE {src_schema}.{qident(table)}.{qident(col)} + {int(fk_task_offset)} END AS {qident(col)}"
            )
            continue

        if col in task_anchor_cols and col in src_cols:
            select_exprs.append(
                f"CASE WHEN {src_schema}.{qident(table)}.{qident(col)} IS NULL THEN NULL ELSE {src_schema}.{qident(table)}.{qident(col)} + {int(fk_task_offset)} END AS {qident(col)}"
            )
            continue

        if col in src_cols:
            select_exprs.append(f"{src_schema}.{qident(table)}.{qident(col)} AS {qident(col)}")
        else:
            select_exprs.append(f"NULL AS {qident(col)}")

    cols_sql = ", ".join([qident(col) for col in dst_cols])
    sel_sql = ", ".join(select_exprs)

    verb = "INSERT OR IGNORE" if ignore_conflicts else "INSERT"
    conn.execute(
        f"{verb} INTO {dst_schema}.{qident(table)} ({cols_sql}) SELECT {sel_sql} FROM {src_schema}.{qident(table)}"
    )


def merge_legacy_db_to_user(
    user_id: int,
    *,
    backup_existing: bool = True,
) -> tuple[bool, str]:
    """Merge legacy prodsmart.db into this user's per-user DB.

    Unlike `import_legacy_db_to_user`, this does not require the user's DB to be empty.
    It merges by offsetting IDs to avoid primary-key collisions and preserve relationships.

    Note: This may duplicate tasks if the user DB already contains the legacy data.
    """
    try:
        uid = int(user_id)
    except Exception:
        return False, "Invalid user id."
    if uid <= 0:
        return False, "Invalid user id."

    legacy_path = get_legacy_db_path()
    if not os.path.exists(legacy_path) or not legacy_db_has_data():
        return False, "No legacy local data found."

    user_db_path = get_user_db_path(uid)
    if not os.path.exists(user_db_path):
        # Create an empty user DB first so schema exists.
        try:
            conn = sqlite3.connect(user_db_path)
            _ensure_schema(conn)
            conn.close()
        except Exception:
            pass

    if backup_existing and os.path.exists(user_db_path):
        try:
            from datetime import datetime

            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            backup_path = f"{user_db_path}.bak_{ts}"
            shutil.copy2(user_db_path, backup_path)
        except Exception:
            pass

    conn = None
    try:
        conn = sqlite3.connect(user_db_path)
        conn.row_factory = sqlite3.Row
        _ensure_schema(conn)
        conn.execute("ATTACH DATABASE ? AS src", (legacy_path,))

        # Offsets to avoid ID collisions.
        max_task = int(conn.execute("SELECT COALESCE(MAX(id), 0) FROM main.tasks").fetchone()[0] or 0)
        max_sub = int(conn.execute("SELECT COALESCE(MAX(id), 0) FROM main.task_subtasks").fetchone()[0] or 0)
        max_pomo = int(conn.execute("SELECT COALESCE(MAX(id), 0) FROM main.pomodoro_sessions").fetchone()[0] or 0)

        _merge_table_with_offsets(
            conn,
            table="tasks",
            src_schema="src",
            dst_schema="main",
            id_offset=max_task,
            fk_task_offset=max_task,
            task_anchor_cols=("recurrence_anchor_id",),
        )
        _merge_table_with_offsets(
            conn,
            table="task_subtasks",
            src_schema="src",
            dst_schema="main",
            id_offset=max_sub,
            fk_task_offset=max_task,
            fk_task_cols=("task_id",),
        )
        _merge_table_with_offsets(
            conn,
            table="task_dependencies",
            src_schema="src",
            dst_schema="main",
            fk_task_offset=max_task,
            fk_task_cols=("task_id", "depends_on_task_id"),
            ignore_conflicts=True,
        )
        _merge_table_with_offsets(
            conn,
            table="pomodoro_sessions",
            src_schema="src",
            dst_schema="main",
            id_offset=max_pomo,
            fk_task_offset=max_task,
            fk_task_cols=("task_id",),
        )

        conn.commit()
        try:
            conn.execute("DETACH DATABASE src")
        except Exception:
            pass
        conn.close()
        conn = None
    except Exception as e:
        try:
            if conn is not None:
                conn.close()
        except Exception:
            pass
        return False, f"Merge failed: {e}"

    try:
        set_legacy_import_decision(uid, "imported")
    except Exception:
        pass

    return True, "Merged legacy local data into this account."


def _ensure_schema(conn: sqlite3.Connection) -> None:
    cur = conn.cursor()
    try:
        row = cur.execute("PRAGMA user_version").fetchone()
        current_version = int(row[0] or 0) if row else 0
    except Exception:
        current_version = 0

    if current_version >= _DB_SCHEMA_VERSION:
        return

    # Core tables
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT,
            description TEXT,
            due_date TEXT,
            priority TEXT,
            created_date TEXT,
            completed_at TEXT,
            task_type TEXT,
            is_urgent INTEGER DEFAULT 0,
            is_important INTEGER DEFAULT 0,
            is_completed INTEGER DEFAULT 0,
            recurrence_kind TEXT,
            recurrence_interval INTEGER DEFAULT 1,
            recurrence_anchor_id INTEGER
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS recurrence_skips (
            recurrence_anchor_id INTEGER NOT NULL,
            recurrence_kind TEXT NOT NULL,
            due_date TEXT NOT NULL,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(recurrence_anchor_id, recurrence_kind, due_date)
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS task_subtasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            task_id INTEGER NOT NULL,
            title TEXT NOT NULL,
            is_completed INTEGER DEFAULT 0,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            completed_at TEXT
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS task_dependencies (
            task_id INTEGER NOT NULL,
            depends_on_task_id INTEGER NOT NULL,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(task_id, depends_on_task_id)
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS pomodoro_sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            task_id INTEGER,
            task_title TEXT,
            started_at TEXT,
            ended_at TEXT,
            duration_min INTEGER,
            status TEXT,
            task_priority TEXT,
            task_type TEXT
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            latitude REAL,
            longitude REAL,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
        """
    )

    # Best-effort schema upgrades for older DBs.
    for stmt in (
        "ALTER TABLE tasks ADD COLUMN created_date TEXT",
        "ALTER TABLE tasks ADD COLUMN completed_at TEXT",
        "ALTER TABLE tasks ADD COLUMN task_type TEXT",
        "ALTER TABLE tasks ADD COLUMN recurrence_kind TEXT",
        "ALTER TABLE tasks ADD COLUMN recurrence_interval INTEGER DEFAULT 1",
        "ALTER TABLE tasks ADD COLUMN recurrence_anchor_id INTEGER",
        "ALTER TABLE pomodoro_sessions ADD COLUMN task_priority TEXT",
        "ALTER TABLE pomodoro_sessions ADD COLUMN task_type TEXT",
        "ALTER TABLE users ADD COLUMN latitude REAL",
        "ALTER TABLE users ADD COLUMN longitude REAL",
    ):
        try:
            cur.execute(stmt)
        except sqlite3.OperationalError:
            pass
        except Exception:
            pass

    try:
        cur.execute(f"PRAGMA user_version = {_DB_SCHEMA_VERSION}")
    except Exception:
        pass
    conn.commit()

def get_db_connection():
    """Establishes a connection to the local database (per-account when logged in)."""
    settings = _load_settings()
    uid = _get_cloud_user_id(settings)
    db_path = get_user_db_path(uid) if uid else get_legacy_db_path()

    conn = sqlite3.connect(db_path)
    # This allows accessing columns by name if needed, though not strictly necessary
    conn.row_factory = sqlite3.Row 
    try:
        _ensure_schema(conn)
    except Exception:
        pass
    return conn

def init_db():
    """Ensures the local database schema is initialized (best-effort)."""
    conn = get_db_connection()
    conn.close()

def hash_password(password):
    """Hash a password using SHA-256."""
    return hashlib.sha256(password.encode()).hexdigest()

def create_user(username, password, latitude=None, longitude=None):
    """Create a new user account."""
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            "INSERT INTO users (username, password_hash, latitude, longitude) VALUES (?, ?, ?, ?)",
            (username, hash_password(password), latitude, longitude)
        )
        conn.commit()
        return True
    except sqlite3.IntegrityError:
        return False  # Username already exists
    finally:
        conn.close()

def authenticate_user(username, password):
    """Authenticate a user. Returns user_id if successful, None otherwise."""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT id FROM users WHERE username = ? AND password_hash = ?",
        (username, hash_password(password))
    )
    result = cursor.fetchone()
    conn.close()
    return result[0] if result else None

def get_current_user():
    """Get the currently logged in user ID from settings."""
    # This would be implemented to store session state
    # For now, return None (no user logged in)
    return None
