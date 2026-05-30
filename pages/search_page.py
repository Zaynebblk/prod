import sqlite3
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse

from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QCheckBox,
    QFrame,
    QScrollArea,
    QPushButton,
)

from database.db_manager import get_db_connection
from resources.api_client import (
    ApiError,
    api_get_user_profile,
    api_search_users,
    api_list_teams,
    api_list_team_tasks,
)
from resources.priority import quadrant_from_flags
from resources.task_types import normalize_task_type
from resources.theme import FONT_FAMILY, get_theme, rgba

from pages.user_profile_dialog import UserProfileDialog

class SearchPage(QWidget):
    open_local_task = pyqtSignal(int)
    open_team_task = pyqtSignal(int, int)

    def __init__(self):
        super().__init__()
        self.setObjectName("SearchPage")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.current_theme = "Light"
        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(220)
        self._debounce.timeout.connect(self._run_search)
        self._team_cache: Dict[int, Dict[str, Any]] = {}

        self._build_ui()
        self.update_theme("Light")

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self.scroll = QScrollArea()
        self.scroll.setObjectName("SearchScroll")
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        root.addWidget(self.scroll)

        self.container = QWidget()
        self.container.setObjectName("SearchContainer")
        self.container.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.scroll.setWidget(self.container)
        layout = QVBoxLayout(self.container)
        layout.setContentsMargins(40, 40, 40, 40)
        layout.setSpacing(16)

        title = QLabel("Search")
        title.setObjectName("SearchTitle")
        layout.addWidget(title)

        subtitle = QLabel("Search across local tasks and team workspace")
        subtitle.setObjectName("SearchSubtitle")
        layout.addWidget(subtitle)

        box = QFrame()
        box.setObjectName("SearchBox")
        bl = QVBoxLayout(box)
        bl.setContentsMargins(14, 14, 14, 14)
        bl.setSpacing(10)

        self.input_query = QLineEdit()
        self.input_query.setPlaceholderText("Type to search… (title/description/username/profile URL)")
        self.input_query.setClearButtonEnabled(True)
        self.input_query.textChanged.connect(lambda *_: self._debounce.start())
        bl.addWidget(self.input_query)

        toggles = QHBoxLayout()
        toggles.setSpacing(12)
        self.chk_local = QCheckBox("Local tasks")
        self.chk_local.setChecked(True)
        self.chk_local.stateChanged.connect(lambda *_: self._debounce.start())
        self.chk_team = QCheckBox("Teams")
        self.chk_team.setChecked(True)
        self.chk_team.stateChanged.connect(lambda *_: self._debounce.start())
        self.chk_people = QCheckBox("People")
        self.chk_people.setChecked(True)
        self.chk_people.stateChanged.connect(lambda *_: self._debounce.start())
        toggles.addWidget(self.chk_local)
        toggles.addWidget(self.chk_team)
        toggles.addWidget(self.chk_people)
        toggles.addStretch()
        bl.addLayout(toggles)

        layout.addWidget(box)

        self.results_title = QLabel("Results")
        self.results_title.setObjectName("ResultsTitle")
        layout.addWidget(self.results_title)

        self.results_container = QWidget()
        self.results_container.setObjectName("SearchResultsContainer")
        self.results_container.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.results_layout = QVBoxLayout(self.results_container)
        self.results_layout.setContentsMargins(0, 0, 0, 0)
        self.results_layout.setSpacing(12)
        layout.addWidget(self.results_container)

        self.empty = QLabel("Type a query to start searching.")
        self.empty.setObjectName("SearchEmpty")
        self.results_layout.addWidget(self.empty)
        self.results_layout.addStretch()

    def update_theme(self, theme: str):
        self.current_theme = theme
        c = get_theme(theme)
        box_bg = rgba(c["card_alt"], 0.92) if theme == "Dark" else rgba(c["card"], 0.96)
        border = rgba(c["border"], 0.8)
        self.setStyleSheet(
            f"QWidget#SearchPage {{ background: {c['bg']}; font-family: '{FONT_FAMILY}', 'Segoe UI'; }}"
            f"QWidget#SearchContainer, QWidget#SearchResultsContainer {{ background: {c['bg']}; }}"
            f"QScrollArea#SearchScroll {{ border: none; background: {c['bg']}; }}"
            f"QLabel#SearchTitle {{ color: {c['text']}; font-size: 30px; font-weight: 900; }}"
            f"QLabel#SearchSubtitle {{ color: {c['sub']}; font-size: 12px; font-weight: 600; }}"
            f"QFrame#SearchBox {{ background: {box_bg}; border: 1px solid {border}; border-radius: 18px; }}"
            f"QLineEdit {{ background: {c['input_bg']}; border: 1px solid {border}; border-radius: 12px; padding: 10px; color: {c['text']}; }}"
            f"QCheckBox {{ color: {c['text']}; font-weight: 700; }}"
            f"QLabel#ResultsTitle {{ color: {c['text']}; font-size: 14px; font-weight: 900; }}"
            f"QLabel#SearchEmpty {{ color: {c['sub']}; font-size: 12px; font-weight: 600; }}"
        )
        if hasattr(self, "scroll"):
            try:
                self.scroll.viewport().setStyleSheet(f"background: {c['bg']};")
            except Exception:
                pass

    def _clear_results(self):
        while self.results_layout.count():
            item = self.results_layout.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()

    def _add_result_card(self, *, title: str, meta: str, on_open):
        c = get_theme(self.current_theme)
        card = QFrame()
        card.setStyleSheet(
            f"QFrame {{ background: {rgba(c['card_alt'], 0.9)}; border: 1px solid {rgba(c['border'], 0.6)}; border-radius: 16px; }}"
        )
        l = QHBoxLayout(card)
        l.setContentsMargins(14, 12, 14, 12)
        l.setSpacing(12)

        text_col = QVBoxLayout()
        text_col.setSpacing(2)
        t = QLabel(title)
        t.setWordWrap(True)
        t.setStyleSheet(f"color: {c['text']}; font-size: 12px; font-weight: 900;")
        m = QLabel(meta)
        m.setWordWrap(True)
        m.setStyleSheet(f"color: {c['sub']}; font-size: 10px; font-weight: 600;")
        text_col.addWidget(t)
        text_col.addWidget(m)
        l.addLayout(text_col, 1)

        btn = QPushButton("Open")
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.setStyleSheet(
            f"QPushButton {{ background: {c['accent']}; color: white; border-radius: 12px; padding: 8px 14px; font-weight: 800; }}"
            f"QPushButton:hover {{ background: {c['deep']}; }}"
        )
        btn.clicked.connect(on_open)
        l.addWidget(btn)
        self.results_layout.addWidget(card)

    def _run_search(self):
        query = str(self.input_query.text() or "").strip()
        self._clear_results()
        if not query:
            self.empty = QLabel("Type a query to start searching.")
            self.empty.setObjectName("SearchEmpty")
            self.results_layout.addWidget(self.empty)
            self.results_layout.addStretch()
            return

        results_found = 0
        user_id = self._extract_user_id_from_query(query)
        if user_id is not None:
            results_found += self._search_user_by_id(user_id)
            if results_found > 0:
                self.results_layout.addStretch()
                return

        if self.chk_local.isChecked():
            results_found += self._search_local(query)
        if self.chk_team.isChecked():
            results_found += self._search_teams(query)
        if self.chk_people.isChecked():
            results_found += self._search_people(query)

        if results_found == 0:
            empty = QLabel("No results.")
            empty.setObjectName("SearchEmpty")
            self.results_layout.addWidget(empty)
        self.results_layout.addStretch()

    def _search_local(self, query: str) -> int:
        q = f"%{query.lower()}%"
        rows = []
        conn = None
        try:
            conn = get_db_connection()
            rows = conn.execute(
                """SELECT id, title, description, due_date, created_date, is_urgent, is_important, task_type
                   FROM tasks
                   WHERE lower(title) LIKE ? OR lower(description) LIKE ?
                   ORDER BY due_date
                   LIMIT 50""",
                (q, q),
            ).fetchall()
        except Exception:
            rows = []
        finally:
            if conn:
                conn.close()

        count = 0
        for r in rows:
            try:
                task_id = int(r[0])
            except Exception:
                continue
            title = str(r[1] or "").strip() or f"Task {task_id}"
            desc = str(r[2] or "").strip()
            due = str(r[3] or "").strip() or "-"
            created = str(r[4] or "").strip() or "-"
            urg = int(r[5] or 0)
            imp = int(r[6] or 0)
            prio = quadrant_from_flags(urg, imp)
            ttype = normalize_task_type(r[7])
            meta = f"Local • Due {due} • Created {created} • {prio}{(' • ' + ttype) if ttype else ''}"
            if desc:
                meta = f"{meta} • {desc[:80]}"
            self._add_result_card(
                title=title,
                meta=meta,
                on_open=lambda _=None, tid=task_id: self.open_local_task.emit(int(tid)),
            )
            count += 1
        return count

    def _load_team_cache(self):
        # Cache teams list to reduce repeated roundtrips while typing.
        try:
            res = api_list_teams()
        except Exception:
            return
        teams = res.get("teams", []) if isinstance(res, dict) else []
        cache = {}
        for t in teams:
            if not isinstance(t, dict):
                continue
            tid = t.get("id")
            if tid is None:
                continue
            cache[int(tid)] = t
        self._team_cache = cache

    def _search_teams(self, query: str) -> int:
        if not self._team_cache:
            self._load_team_cache()
        q = query.lower()
        count = 0
        for team_id, meta in (self._team_cache or {}).items():
            try:
                res = api_list_team_tasks(team_id)
            except ApiError:
                continue
            except Exception:
                continue
            tasks = res.get("tasks", []) if isinstance(res, dict) else []
            for task in tasks:
                if not isinstance(task, dict):
                    continue
                title = str(task.get("title") or "")
                desc = str(task.get("description") or "")
                if q not in title.lower() and q not in desc.lower():
                    continue
                task_id = int(task.get("id") or 0)
                due = str(task.get("due_date") or "-")
                team_name = meta.get("name") or f"Team {team_id}"
                meta_txt = f"Team • {team_name} • Due {due}"
                self._add_result_card(
                    title=title or f"Task {task_id}",
                    meta=meta_txt,
                    on_open=lambda _=None, t=team_id, tid=task_id: self.open_team_task.emit(int(t), int(tid)),
                )
                count += 1
                if count >= 50:
                    return count
        return count

    def _search_people(self, query: str) -> int:
        q = str(query or "").strip()
        if len(q) < 2:
            return 0
        try:
            res = api_search_users(q, limit=15)
        except Exception:
            return 0
        users = res.get("users", []) if isinstance(res, dict) else []
        count = 0
        for u in users:
            if not isinstance(u, dict):
                continue
            try:
                user_id = int(u.get("user_id") or 0)
            except Exception:
                user_id = 0
            username = str(u.get("username") or "").strip()
            if not user_id or not username:
                continue
            meta = "People • Account"
            self._add_result_card(
                title=username,
                meta=meta,
                on_open=lambda _=None, uid=user_id: self._open_user_profile(uid),
            )
            count += 1
        return count

    def _open_user_profile(self, user_id: int):
        try:
            prof = api_get_user_profile(int(user_id))
        except ApiError as e:
            prof = {"username": "User", "bio": str(e)}
        except Exception:
            prof = {"username": "User", "bio": "Could not load profile."}
        dlg = UserProfileDialog(self, theme=self.current_theme, profile=prof if isinstance(prof, dict) else {})
        try:
            dlg.exec()
        except Exception:
            pass

    def _extract_user_id_from_query(self, query: str) -> Optional[int]:
        if not query:
            return None
        trimmed = query.strip()
        if trimmed.isdigit():
            try:
                uid = int(trimmed)
                return uid if uid > 0 else None
            except Exception:
                return None

        parsed = urlparse(trimmed)
        path = parsed.path or trimmed
        if path.startswith("/"):
            path = path[1:]
        parts = [p for p in path.split("/") if p]
        if len(parts) >= 3 and parts[0].lower() == "users" and parts[2].lower() == "profile":
            try:
                uid = int(parts[1])
                return uid if uid > 0 else None
            except Exception:
                return None
        return None

    def _search_user_by_id(self, user_id: int) -> int:
        try:
            profile = api_get_user_profile(user_id)
        except Exception:
            return 0
        if not isinstance(profile, dict) or not profile.get("username"):
            return 0

        username = str(profile.get("username") or "User").strip() or "User"
        meta = "People • Shared profile"
        self._add_result_card(
            title=username,
            meta=meta,
            on_open=lambda _=None, uid=user_id: self._open_user_profile(uid),
        )
        return 1
