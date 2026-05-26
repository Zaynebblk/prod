import argparse
import os
import shutil
import sqlite3
from datetime import datetime, timezone


def _project_root() -> str:
    return os.path.dirname(os.path.abspath(__file__))


def _ts() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def _qmarks(n: int) -> str:
    return ", ".join(["?"] * int(n))


def _as_int_set(values) -> set[int]:
    out: set[int] = set()
    for v in values or []:
        try:
            i = int(v)
        except Exception:
            continue
        if i > 0:
            out.add(i)
    return out


def reset_local_user_db(user_id: int, *, dry_run: bool) -> None:
    try:
        from database import db_manager
    except Exception as e:
        raise RuntimeError(f"Failed to import database.db_manager: {e}") from e

    db_path = db_manager.get_user_db_path(int(user_id))
    if not os.path.exists(db_path):
        print(f"[local] No per-user DB found for user_id={user_id} ({db_path})")
    else:
        backup_path = f"{db_path}.bak_reset_{_ts()}"
        print(f"[local] Backing up {db_path} -> {backup_path}")
        if not dry_run:
            shutil.copy2(db_path, backup_path)
            try:
                os.remove(db_path)
            except OSError:
                # If the DB is locked, keep the backup and fail loudly.
                raise

    print(f"[local] Creating fresh empty DB at {db_path}")
    if not dry_run:
        conn = sqlite3.connect(db_path)
        try:
            db_manager._ensure_schema(conn)  # intentionally reuse app schema
        finally:
            conn.close()

    # Ensure we don't re-import the legacy shared DB into this account.
    print(f"[local] Marking legacy local DB import as skipped for user_id={user_id}")
    if not dry_run:
        db_manager.set_legacy_import_decision(int(user_id), "skipped")


def _cloud_db_path() -> str:
    return os.path.join(_project_root(), "server", "prodsmart_cloud.db")


def cleanup_cloud_db(*, keep_usernames: list[str], wipe_usernames: list[str], dry_run: bool) -> None:
    db_path = _cloud_db_path()
    if not os.path.exists(db_path):
        raise FileNotFoundError(f"Cloud DB not found: {db_path}")

    keep = [str(u or "").strip() for u in (keep_usernames or []) if str(u or "").strip()]
    wipe = [str(u or "").strip() for u in (wipe_usernames or []) if str(u or "").strip()]
    keep_l = {u.lower() for u in keep}
    wipe_l = {u.lower() for u in wipe}

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        users = conn.execute("SELECT id, username, avatar_filename FROM users ORDER BY id").fetchall()
        username_to_id = {str(r["username"]).lower(): int(r["id"]) for r in users}

        missing = [u for u in keep_l if u not in username_to_id]
        if missing:
            raise RuntimeError(f"Keep usernames not found in cloud DB: {', '.join(sorted(missing))}")

        keep_user_ids = {username_to_id[u] for u in keep_l}
        wipe_user_ids = {username_to_id[u] for u in wipe_l if u in username_to_id}

        all_user_ids = {int(r["id"]) for r in users}
        delete_user_ids = sorted(all_user_ids - keep_user_ids)
        purge_user_ids = sorted(_as_int_set(delete_user_ids) | _as_int_set(wipe_user_ids))

        print(f"[cloud] Keep users: {sorted(keep_user_ids)} ({', '.join(sorted(keep_l))})")
        print(f"[cloud] Delete users: {delete_user_ids}")
        print(f"[cloud] Wipe users (keep accounts): {sorted(wipe_user_ids)}")

        # Teams to delete: owned by deleted users OR owned by wiped users.
        team_ids_to_delete: list[int] = []
        if purge_user_ids:
            team_ids_to_delete = [
                int(r[0])
                for r in conn.execute(
                    f"SELECT id FROM teams WHERE owner_id IN ({_qmarks(len(purge_user_ids))})",
                    purge_user_ids,
                ).fetchall()
            ]
        print(f"[cloud] Delete teams: {sorted(team_ids_to_delete)}")

        avatar_files_to_delete: list[str] = []
        if delete_user_ids:
            for r in users:
                uid = int(r["id"])
                if uid in set(delete_user_ids):
                    fn = str(r["avatar_filename"] or "").strip()
                    if fn:
                        avatar_files_to_delete.append(fn)

        def exec_count(sql: str, params=()):
            if dry_run:
                return 0
            cur = conn.execute(sql, params)
            return int(cur.rowcount or 0)

        now_iso = datetime.now(timezone.utc).replace(microsecond=0).isoformat()

        if not dry_run:
            conn.execute("BEGIN")

        # Delete team-scoped data first (teams owned by purge users).
        if team_ids_to_delete:
            ph = _qmarks(len(team_ids_to_delete))
            exec_count(f"DELETE FROM team_task_comments WHERE team_id IN ({ph})", team_ids_to_delete)
            exec_count(f"DELETE FROM team_messages WHERE team_id IN ({ph})", team_ids_to_delete)
            exec_count(f"DELETE FROM team_events WHERE team_id IN ({ph})", team_ids_to_delete)
            exec_count(f"DELETE FROM team_join_requests WHERE team_id IN ({ph})", team_ids_to_delete)
            exec_count(f"DELETE FROM team_members WHERE team_id IN ({ph})", team_ids_to_delete)
            exec_count(f"DELETE FROM team_tasks WHERE team_id IN ({ph})", team_ids_to_delete)
            exec_count(f"DELETE FROM team_pomodoro WHERE team_id IN ({ph})", team_ids_to_delete)
            exec_count(f"DELETE FROM teams WHERE id IN ({ph})", team_ids_to_delete)

        # Remove purge users from remaining team membership/join requests.
        if purge_user_ids:
            phu = _qmarks(len(purge_user_ids))
            exec_count(f"DELETE FROM team_members WHERE user_id IN ({phu})", purge_user_ids)
            exec_count(f"DELETE FROM team_join_requests WHERE user_id IN ({phu})", purge_user_ids)
            exec_count(f"UPDATE team_join_requests SET decided_by = NULL WHERE decided_by IN ({phu})", purge_user_ids)

            # Remove user-authored content.
            exec_count(f"DELETE FROM team_messages WHERE user_id IN ({phu})", purge_user_ids)
            exec_count(f"DELETE FROM team_task_comments WHERE user_id IN ({phu})", purge_user_ids)
            exec_count(f"DELETE FROM team_events WHERE actor_user_id IN ({phu})", purge_user_ids)

            # Unassign/clear completion fields for tasks involving purged users.
            exec_count(
                f"UPDATE team_tasks SET assigned_to = NULL, updated_at = ? WHERE assigned_to IN ({phu})",
                (now_iso, *purge_user_ids),
            )
            exec_count(
                f"UPDATE team_tasks SET is_completed = 0, completed_at = NULL, completed_by = NULL, updated_at = ? WHERE completed_by IN ({phu})",
                (now_iso, *purge_user_ids),
            )
            exec_count(
                f"DELETE FROM team_tasks WHERE created_by IN ({phu})",
                purge_user_ids,
            )
            exec_count(
                f"UPDATE team_pomodoro SET started_by = NULL, updated_at = ? WHERE started_by IN ({phu})",
                (now_iso, *purge_user_ids),
            )

        # Sessions and users for deleted accounts.
        if delete_user_ids:
            ph = _qmarks(len(delete_user_ids))
            exec_count(f"DELETE FROM sessions WHERE user_id IN ({ph})", delete_user_ids)
            exec_count(f"DELETE FROM users WHERE id IN ({ph})", delete_user_ids)

        if not dry_run:
            conn.commit()

        # Remove avatar files of deleted users (best-effort).
        avatar_dir = os.path.join(_project_root(), "server", "uploads", "avatars")
        for fn in avatar_files_to_delete:
            safe = os.path.basename(fn)
            path = os.path.join(avatar_dir, safe)
            if os.path.isfile(path):
                print(f"[cloud] Removing avatar file: {path}")
                if not dry_run:
                    try:
                        os.remove(path)
                    except OSError:
                        pass
    finally:
        conn.close()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Cleanup demo/test data: purge extra cloud accounts and reset a user's local DB."
    )
    parser.add_argument(
        "--keep-usernames",
        default="test,zayneb",
        help="Comma-separated cloud usernames to keep (default: test,zayneb).",
    )
    parser.add_argument(
        "--wipe-usernames",
        default="zayneb",
        help="Comma-separated cloud usernames to wipe (keep account but delete their cloud teams/content; default: zayneb).",
    )
    parser.add_argument(
        "--reset-local-user",
        default="zayneb",
        help="Cloud username whose local per-user DB should be reset (default: zayneb).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would be removed without changing anything.",
    )
    args = parser.parse_args()

    keep_usernames = [p.strip() for p in str(args.keep_usernames or "").split(",") if p.strip()]
    wipe_usernames = [p.strip() for p in str(args.wipe_usernames or "").split(",") if p.strip()]
    reset_local_username = str(args.reset_local_user or "").strip()
    if not reset_local_username:
        raise SystemExit("--reset-local-user must not be empty")

    # Resolve user id for local reset from the cloud DB user list.
    conn = sqlite3.connect(_cloud_db_path())
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            "SELECT id FROM users WHERE lower(username) = lower(?) LIMIT 1",
            (reset_local_username,),
        ).fetchone()
    finally:
        conn.close()
    if not row:
        raise SystemExit(f"User not found in cloud DB: {reset_local_username}")

    user_id = int(row["id"])
    reset_local_user_db(user_id, dry_run=bool(args.dry_run))
    cleanup_cloud_db(
        keep_usernames=keep_usernames,
        wipe_usernames=wipe_usernames,
        dry_run=bool(args.dry_run),
    )

    print("[done] Cleanup finished.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
