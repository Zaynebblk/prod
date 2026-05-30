from datetime import datetime, timedelta
import json
import os
import re
import traceback
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QFrame,
    QComboBox, QScrollArea, QLineEdit, QDialog, QMessageBox, QProgressBar,
    QListWidget, QListWidgetItem, QAbstractItemView, QLayout
)
from PyQt6.QtCore import Qt, QDate, pyqtSignal, QTimer
from PyQt6.QtGui import QPixmap, QPainter, QPainterPath
from pages.tasks_page import AddTaskDialog, TaskCard, ViewTaskDialog
from database.db_manager import get_db_connection
from resources.priority import quadrant_from_flags, normalize_priority
from resources.task_types import normalize_task_type
from resources.theme import get_theme, FONT_FAMILY, rgba
from resources.time_format import format_duration_minutes
from resources.api_client import (
    ApiError,
    api_list_teams,
    api_create_team,
    api_join_team,
    api_get_team,
    api_search_users,
    api_get_user_profile,
    api_list_team_join_requests,
    api_accept_team_join_request,
    api_reject_team_join_request,
    api_invite_team_member,
    api_get_user_avatar,
    api_list_team_members,
    api_list_team_tasks,
    api_team_analytics,
    api_create_team_task,
    api_update_team_task,
    api_delete_team_task,
    api_list_team_messages,
    api_send_team_message,
    api_send_team_alert,
    api_update_team_member_role,
    api_remove_team_member,
    api_list_team_events,
    api_list_team_task_comments,
    api_add_team_task_comment,
    get_base_url,
    load_settings,
)
from pages.user_profile_dialog import UserProfileDialog
from pages.history_page import FocusBarChart


_NO_AVATAR = object()


class NoWheelComboBox(QComboBox):
    def wheelEvent(self, event):
        event.ignore()


class _TeamTaskDetailsDialog(QDialog):
    def __init__(
        self,
        parent,
        *,
        team_id: int,
        task: dict,
        priority: str,
        task_type: str | None,
        due_pretty: str,
        created_pretty: str,
        total_focus_min: int = 0,
        total_sessions: int = 0,
        sessions_widgets=None,
        theme: str = "Light",
        members_by_id=None,
    ):
        super().__init__(parent)
        self.team_id = int(team_id)
        self.task = dict(task or {})
        self.task_id = int(self.task.get("id") or 0)
        self.current_theme = theme
        self.members_by_id = dict(members_by_id or {})
        self.setWindowTitle("Task Details")
        self.setMinimumWidth(520)
        if sessions_widgets is None:
            sessions_widgets = []
        self.sessions_widgets = list(sessions_widgets)

        c = get_theme(theme)
        is_dark = theme == "Dark"
        bg = "#000000" if is_dark else "#FFFFFF"
        txt = c["text"]
        card_bg = rgba(c["card_alt"], 0.92) if is_dark else rgba(c["card"], 0.95)
        border = rgba(c["border"], 0.7)
        self._card_bg = card_bg
        self._border = border
        self.setStyleSheet(f"QDialog {{ background: {bg}; }} QLabel {{ color: {txt}; }}")

        root = QVBoxLayout(self)
        root.setContentsMargins(22, 22, 22, 22)
        root.setSpacing(12)

        header = QFrame()
        header.setStyleSheet(f"QFrame {{ background: {card_bg}; border: 1px solid {border}; border-radius: 16px; }}")
        hl = QVBoxLayout(header)
        hl.setContentsMargins(16, 14, 16, 14)
        hl.setSpacing(6)

        title = QLabel(self.task.get("title") or "")
        title.setWordWrap(True)
        title.setStyleSheet("font-size: 20px; font-weight: 900;")
        hl.addWidget(title)

        meta = []
        if task_type:
            meta.append(f"Type: {task_type}")
        if priority:
            meta.append(f"Priority: {priority}")
        assigned_to = self.task.get("assigned_to")
        try:
            assigned_to = int(assigned_to) if assigned_to is not None else None
        except Exception:
            assigned_to = None
        if assigned_to is not None:
            assignee_name = self.members_by_id.get(assigned_to) or f"User {assigned_to}"
            meta.append(f"Assigned to: {assignee_name}")
        else:
            meta.append("Unassigned")
        meta.append(f"Created: {created_pretty}")
        meta.append(f"Due: {due_pretty}")
        meta_lbl = QLabel("  •  ".join(meta))
        meta_lbl.setWordWrap(True)
        meta_lbl.setStyleSheet(f"font-size: 11px; color: {c['sub']}; font-weight: 600;")
        hl.addWidget(meta_lbl)

        desc = QLabel(self.task.get("description") or "")
        desc.setWordWrap(True)
        desc.setStyleSheet("font-size: 12px; font-weight: 600;")
        hl.addWidget(desc)

        root.addWidget(header)

        comments_card = QFrame()
        comments_card.setStyleSheet(f"QFrame {{ background: {card_bg}; border: 1px solid {border}; border-radius: 16px; }}")
        cl = QVBoxLayout(comments_card)
        cl.setContentsMargins(16, 14, 16, 14)
        cl.setSpacing(10)

        comments_title = QLabel("Comments")
        comments_title.setStyleSheet("font-size: 13px; font-weight: 900;")
        cl.addWidget(comments_title)

        self.comments_list = QVBoxLayout()
        self.comments_list.setSpacing(10)
        self.comments_container = QWidget()
        self.comments_container.setObjectName("TeamTaskCommentsContainer")
        self.comments_container.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.comments_container.setStyleSheet(f"QWidget#TeamTaskCommentsContainer {{ background: {card_bg}; }}")
        self.comments_container.setLayout(self.comments_list)
        self.comments_scroll = QScrollArea()
        self.comments_scroll.setWidgetResizable(True)
        self.comments_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.comments_scroll.setStyleSheet(
            f"QScrollArea {{ background: {card_bg}; border: none; }}"
            f"QScrollArea::viewport {{ background: {card_bg}; }}"
        )
        self.comments_scroll.viewport().setStyleSheet(f"background: {card_bg};")
        self.comments_scroll.setWidget(self.comments_container)
        self.comments_scroll.setFixedHeight(220)
        cl.addWidget(self.comments_scroll)

        input_row = QHBoxLayout()
        input_row.setSpacing(8)
        self.comment_input = QLineEdit()
        self.comment_input.setPlaceholderText("Write a comment…")
        self.comment_input.returnPressed.connect(self.send_comment)
        self.comment_input.setStyleSheet(
            f"background: {c['input_bg']}; border: 1px solid {border}; border-radius: 14px; padding: 8px 10px; color: {txt};"
        )
        input_row.addWidget(self.comment_input, 1)

        send_btn = QPushButton("Send")
        send_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        send_btn.clicked.connect(self.send_comment)
        input_row.addWidget(send_btn)
        cl.addLayout(input_row)

        root.addWidget(comments_card)

        if total_sessions is not None:
            focus_card = QFrame()
            focus_card.setStyleSheet(f"QFrame {{ background: {card_bg}; border: 1px solid {border}; border-radius: 16px; }}")
            fl = QVBoxLayout(focus_card)
            fl.setContentsMargins(16, 14, 16, 14)
            fl.setSpacing(10)
            summary = QLabel(f"Focus total: {format_duration_minutes(int(total_focus_min or 0))}  •  Sessions: {int(total_sessions or 0)}")
            summary.setStyleSheet(f"font-size: 11px; color: {c['sub']}; font-weight: 700;")
            fl.addWidget(summary)
            if self.sessions_widgets:
                for w in self.sessions_widgets[:15]:
                    fl.addWidget(w)
            else:
                empty = QLabel("No Pomodoro sessions yet.")
                empty.setStyleSheet(f"font-size: 11px; color: {c['sub']};")
                fl.addWidget(empty)
            root.addWidget(focus_card)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        close_btn = QPushButton("Close")
        close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        close_btn.clicked.connect(self.accept)
        btn_row.addWidget(close_btn)
        root.addLayout(btn_row)

        self.refresh_comments()

    def _format_time(self, raw):
        if not raw:
            return ""
        raw_text = str(raw).replace("T", " ").split(".")[0]
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
            try:
                dt = datetime.strptime(raw_text, fmt)
                return dt.strftime("%b %d, %H:%M")
            except Exception:
                continue
        return raw_text

    def _clear_layout(self, layout):
        while layout.count():
            item = layout.takeAt(0)
            widget = item.widget()
            if widget:
                widget.deleteLater()

    def refresh_comments(self):
        if not self.team_id or not self.task_id:
            return
        try:
            res = api_list_team_task_comments(self.team_id, self.task_id, limit=100)
        except Exception:
            return
        comments = res.get("comments", []) if isinstance(res, dict) else []
        self._clear_layout(self.comments_list)
        c = get_theme(self.current_theme)
        for cm in comments:
            if not isinstance(cm, dict):
                continue
            username = cm.get("username") or "User"
            message = cm.get("message") or ""
            created_at = cm.get("created_at")
            bubble = QFrame()
            bubble.setStyleSheet(
                f"QFrame {{ background: {rgba(c['card'], 0.7)}; border: 1px solid {rgba(c['border'], 0.5)}; border-radius: 14px; }}"
            )
            bl = QVBoxLayout(bubble)
            bl.setContentsMargins(12, 10, 12, 10)
            bl.setSpacing(4)
            head = QHBoxLayout()
            name = QLabel(str(username).upper())
            name.setStyleSheet(f"font-size: 9px; font-weight: 800; color: {c['accent']}; letter-spacing: 1px;")
            head.addWidget(name)
            head.addStretch()
            stamp = self._format_time(created_at)
            if stamp:
                tl = QLabel(stamp)
                tl.setStyleSheet(f"font-size: 9px; color: {c['sub']};")
                head.addWidget(tl)
            bl.addLayout(head)
            body = QLabel(str(message))
            body.setWordWrap(True)
            body.setStyleSheet(f"font-size: 11px; font-weight: 600; color: {c['text']};")
            bl.addWidget(body)
            self.comments_list.addWidget(bubble)
        self.comments_list.addStretch()
        try:
            bar = self.comments_scroll.verticalScrollBar()
            bar.setValue(bar.maximum())
        except Exception:
            pass

    def send_comment(self):
        msg = self.comment_input.text().strip() if hasattr(self, "comment_input") else ""
        if not msg or not self.team_id or not self.task_id:
            return
        try:
            api_add_team_task_comment(self.team_id, self.task_id, msg)
            self.comment_input.clear()
            self.refresh_comments()
        except ApiError as e:
            QMessageBox.warning(self, "Comments", str(e))
        except Exception:
            pass


class _TeamHistoryDialog(QDialog):
    def __init__(self, parent, *, theme: str, team_name: str, team_id: int):
        super().__init__(parent)
        self.setWindowTitle("Team History")
        self.setMinimumSize(680, 640)
        self.current_theme = theme
        self.team_id = int(team_id)

        c = get_theme(theme)
        is_dark = theme == "Dark"
        bg = "#000000" if is_dark else "#FFFFFF"
        card_bg = rgba(c["card_alt"], 0.92) if is_dark else "#FFFFFF"
        chip_bg = rgba(c["accent_soft"], 0.92) if is_dark else c["accent_soft"]
        soft_border = rgba(c["border"], 0.45)
        self._card_bg = card_bg
        self._chip_bg = chip_bg
        self._soft_border = soft_border
        self.setStyleSheet(
            f"QDialog {{ background: {bg}; }}"
            f"QLabel {{ color: {c['text']}; background: transparent; border: none; }}"
            f"QPushButton {{ background: {c['accent']}; color: white; border: none; border-radius: 10px; padding: 9px 18px; font-weight: 800; }}"
            f"QPushButton:hover {{ background: {c['deep']}; }}"
        )

        root = QVBoxLayout(self)
        root.setContentsMargins(22, 22, 22, 22)
        root.setSpacing(12)

        header_row = QHBoxLayout()
        header_row.setSpacing(12)
        title = QLabel(team_name or "Team")
        title.setStyleSheet("font-size: 18px; font-weight: 900;")
        header_row.addWidget(title, 1)
        close_btn = QPushButton("Close")
        close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        close_btn.clicked.connect(self.accept)
        header_row.addWidget(close_btn)
        root.addLayout(header_row)

        # Weekly activity chart
        chart_heading = QLabel("Weekly Activity")
        chart_heading.setStyleSheet(f"color: {c['sub']}; font-size: 12px; font-weight: 800;")
        root.addWidget(chart_heading)

        self.chart = FocusBarChart()
        self.chart.set_theme({
            "primary": c.get("accent"),
            "sub": c.get("sub"),
        })
        root.addWidget(self.chart)

        # Member Activity breakdown
        member_heading = QLabel("Member Activity")
        member_heading.setStyleSheet(f"color: {c['sub']}; font-size: 12px; font-weight: 800;")
        root.addWidget(member_heading)

        self.member_container = QWidget()
        self.member_container.setObjectName("TeamHistoryMemberContainer")
        self.member_container.setStyleSheet(f"QWidget#TeamHistoryMemberContainer {{ background: {bg}; }}")
        self.member_layout = QVBoxLayout(self.member_container)
        self.member_layout.setContentsMargins(0, 0, 0, 0)
        self.member_layout.setSpacing(8)
        
        self.member_scroll = QScrollArea()
        self.member_scroll.setObjectName("TeamHistoryMemberScroll")
        self.member_scroll.setWidgetResizable(True)
        self.member_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.member_scroll.setStyleSheet(f"QScrollArea {{ background: {bg}; border: none; }} QScrollArea::viewport {{ background: {bg}; }}")
        self.member_scroll.viewport().setStyleSheet(f"background: {bg};")
        self.member_scroll.setWidget(self.member_container)
        self.member_scroll.setFixedHeight(140)
        root.addWidget(self.member_scroll)

        # Events list
        events_heading = QLabel("Event Timeline")
        events_heading.setStyleSheet(f"color: {c['sub']}; font-size: 12px; font-weight: 800;")
        root.addWidget(events_heading)

        self.events_container = QWidget()
        self.events_container.setObjectName("TeamHistoryEventsContainer")
        self.events_container.setStyleSheet(f"QWidget#TeamHistoryEventsContainer {{ background: {bg}; }}")
        self.events_layout = QVBoxLayout(self.events_container)
        self.events_layout.setContentsMargins(0, 0, 0, 0)
        self.events_layout.setSpacing(10)

        self.events_scroll = QScrollArea()
        self.events_scroll.setObjectName("TeamHistoryEventsScroll")
        self.events_scroll.setWidgetResizable(True)
        self.events_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.events_scroll.setWidget(self.events_container)
        self.events_scroll.setMinimumHeight(260)
        self.events_scroll.setStyleSheet(f"QScrollArea {{ background: {bg}; border: none; }} QScrollArea::viewport {{ background: {bg}; }}")
        self.events_scroll.viewport().setStyleSheet(f"background: {bg};")
        root.addWidget(self.events_scroll, 1)

        self.refresh()

    def _clear_events(self):
        while self.events_layout.count():
            item = self.events_layout.takeAt(0)
            widget = item.widget() if item else None
            if widget:
                widget.deleteLater()

    def _clear_members(self):
        while self.member_layout.count():
            item = self.member_layout.takeAt(0)
            widget = item.widget() if item else None
            if widget:
                widget.deleteLater()

    def refresh(self):
        self._clear_events()
        self._clear_members()
        c = get_theme(self.current_theme)
        try:
            res = api_list_team_events(self.team_id, after_id=0, limit=200)
        except ApiError as e:
            lbl = QLabel(str(e))
            lbl.setStyleSheet("font-size: 11px; font-weight: 700;")
            self.events_layout.addWidget(lbl)
            return
        except Exception:
            lbl = QLabel("Failed to load history.")
            lbl.setStyleSheet("font-size: 11px; font-weight: 700;")
            self.events_layout.addWidget(lbl)
            return

        events = res.get("events", []) if isinstance(res, dict) else []
        # compute counts for last 7 days
        today = datetime.utcnow().date()
        days = [(today - timedelta(days=i)) for i in range(6, -1, -1)]
        counts = {d.isoformat(): 0 for d in days}
        for ev in events:
            try:
                raw = str(ev.get("created_at") or "")
                if not raw:
                    continue
                dt = datetime.fromisoformat(raw.split(".")[0])
                key = dt.date().isoformat()
                if key in counts:
                    counts[key] += 1
            except Exception:
                continue

        values = [counts.get(d.isoformat(), 0) for d in days]
        labels = [d.strftime("%a")[0] for d in days]
        try:
            self.chart.set_data(values, labels=labels, highlight_index=len(values) - 1)
        except Exception:
            pass

        # Build member activity breakdown from events
        member_actions = {}  # {username: {"completed": n, "created": n, "modified": n, "commented": n}}
        for ev in events:
            try:
                actor = ev.get("actor_username") or "System"
                event_type = str(ev.get("event_type") or "").lower()
                if actor not in member_actions:
                    member_actions[actor] = {"completed": 0, "created": 0, "modified": 0, "commented": 0}
                if "completed" in event_type or "finish" in event_type:
                    member_actions[actor]["completed"] += 1
                elif "created" in event_type or "added" in event_type:
                    member_actions[actor]["created"] += 1
                elif "commented" in event_type:
                    member_actions[actor]["commented"] += 1
                else:
                    member_actions[actor]["modified"] += 1
            except Exception:
                pass
        
        if member_actions:
            for username in sorted(member_actions.keys()):
                stats = member_actions[username]
                total = sum(stats.values())
                card = QFrame()
                card.setObjectName("TeamHistoryMemberCard")
                card.setStyleSheet(
                    f"QFrame#TeamHistoryMemberCard {{ background: {self._card_bg}; "
                    f"border: 1px solid {self._soft_border}; border-radius: 12px; }}"
                )
                cl = QHBoxLayout(card)
                cl.setContentsMargins(14, 10, 14, 10)
                cl.setSpacing(8)

                name_lbl = QLabel(str(username))
                name_lbl.setStyleSheet(f"color: {c['text']}; font-size: 12px; font-weight: 900;")
                cl.addWidget(name_lbl, 1)

                for key, label in (
                    ("completed", "Done"),
                    ("created", "Created"),
                    ("commented", "Comments"),
                    ("modified", "Updates"),
                ):
                    count = int(stats.get(key) or 0)
                    if count <= 0:
                        continue
                    cl.addWidget(self._build_history_chip(f"{label}: {count}", c))

                total_lbl = QLabel(f"{total} total")
                total_lbl.setStyleSheet(
                    f"background: {rgba(c['accent'], 0.14)}; color: {c['accent']}; "
                    f"border: 1px solid {rgba(c['accent'], 0.28)}; border-radius: 9px; "
                    "padding: 3px 9px; font-size: 10px; font-weight: 900;"
                )
                cl.addWidget(total_lbl)
                self.member_layout.addWidget(card)
        else:
            empty = QLabel("No member activity recorded.")
            empty.setStyleSheet(f"color: {c['sub']}; font-size: 11px; font-weight: 700;")
            self.member_layout.addWidget(empty)

        if not events:
            empty = QLabel("No recent team events.")
            empty.setStyleSheet(f"color: {c['sub']}; font-size: 12px; font-weight: 700;")
            self.events_layout.addWidget(empty)
            return

        for ev in reversed(events[-200:]):
            card = QFrame()
            card.setObjectName("TeamHistoryEventCard")
            card.setStyleSheet(
                f"QFrame#TeamHistoryEventCard {{ background: {self._card_bg}; "
                f"border: 1px solid {self._soft_border}; border-radius: 14px; }}"
            )
            cl = QHBoxLayout(card)
            cl.setContentsMargins(14, 12, 14, 12)
            cl.setSpacing(12)
            typ = str(ev.get("event_type") or "event")
            actor = ev.get("actor_username") or "System"
            created = str(ev.get("created_at") or "")
            payload = ev.get("payload") or {}

            badge = QLabel(self._event_initial(typ))
            badge.setFixedSize(34, 34)
            badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
            badge.setStyleSheet(
                f"background: {rgba(c['accent'], 0.16)}; color: {c['accent']}; "
                f"border: 1px solid {rgba(c['accent'], 0.24)}; border-radius: 17px; "
                "font-size: 12px; font-weight: 900;"
            )
            cl.addWidget(badge)

            text_col = QVBoxLayout()
            text_col.setContentsMargins(0, 0, 0, 0)
            text_col.setSpacing(3)
            title_lbl = QLabel(self._event_title(typ))
            title_lbl.setStyleSheet(f"color: {c['text']}; font-size: 12px; font-weight: 900;")
            text_col.addWidget(title_lbl)

            detail = self._event_detail(payload)
            if detail:
                detail_lbl = QLabel(detail)
                detail_lbl.setWordWrap(True)
                detail_lbl.setStyleSheet(f"color: {c['sub']}; font-size: 11px; font-weight: 700;")
                text_col.addWidget(detail_lbl)
            cl.addLayout(text_col, 1)

            meta_lbl = QLabel(f"{self._format_event_date(created)}\n{actor}")
            meta_lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            meta_lbl.setStyleSheet(f"color: {c['sub']}; font-size: 10px; font-weight: 800;")
            cl.addWidget(meta_lbl)
            self.events_layout.addWidget(card)

    def _build_history_chip(self, text, c):
        chip = QLabel(str(text))
        chip.setStyleSheet(
            f"background: {self._chip_bg}; color: {c['chip_text']}; "
            f"border: 1px solid {self._soft_border}; border-radius: 9px; "
            "padding: 3px 9px; font-size: 10px; font-weight: 900;"
        )
        return chip

    def _event_title(self, event_type):
        key = str(event_type or "").strip().lower()
        names = {
            "member_added": "Member added",
            "member_removed": "Member removed",
            "member_role_updated": "Role updated",
            "task_created": "Task created",
            "task_updated": "Task updated",
            "task_completed": "Task completed",
            "task_deleted": "Task deleted",
            "task_comment": "Task comment",
            "team_message": "Team message",
            "team_alert": "Team alert",
        }
        if key in names:
            return names[key]
        label = key.replace("_", " ").strip()
        return label.title() if label else "Team event"

    def _event_initial(self, event_type):
        words = [w for w in self._event_title(event_type).split() if w]
        return "".join(w[0] for w in words[:2]).upper() or "E"

    def _event_detail(self, payload):
        if not isinstance(payload, dict):
            return ""
        parts = []
        task_title = payload.get("title") or payload.get("task_title")
        if task_title:
            parts.append(str(task_title))
        target = payload.get("username") or payload.get("target_username") or payload.get("member_username")
        if target:
            parts.append(f"Member: {target}")
        old_role = payload.get("old_role")
        new_role = payload.get("new_role") or payload.get("role")
        if old_role or new_role:
            role_text = f"{old_role or 'role'} -> {new_role or 'role'}"
            parts.append(role_text)
        message = payload.get("message")
        if message:
            parts.append(str(message))
        return " | ".join(parts[:2])

    def _format_event_date(self, raw):
        raw = str(raw or "").strip()
        if not raw:
            return "Unknown"
        normalized = raw.replace("Z", "+00:00").replace("T", " ").split(".")[0]
        try:
            return datetime.fromisoformat(normalized).strftime("%Y-%m-%d %H:%M")
        except Exception:
            return normalized[:16] if normalized else "Unknown"


class _TeamAnalyticsDialog(QDialog):
    def __init__(self, parent, *, theme: str, team_id: int, team_name: str, data: dict):
        super().__init__(parent)
        self.setWindowTitle("Team Analytics")
        self.setMinimumSize(720, 560)
        self.resize(900, 720)
        self.current_theme = theme
        self.team_id = int(team_id)
        self.data = dict(data or {})

        c = get_theme(theme)
        is_dark = theme == "Dark"
        bg = c["bg"]
        self._card_bg = rgba(c["card_alt"], 0.92) if is_dark else rgba(c["card"], 0.95)
        self._border = rgba(c["border"], 0.7)
        self.setStyleSheet(f"QDialog {{ background: {bg}; }} QLabel {{ color: {c['text']}; }}")

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        self.analytics_scroll = QScrollArea()
        self.analytics_scroll.setWidgetResizable(True)
        self.analytics_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.analytics_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.analytics_scroll.setStyleSheet(
            f"QScrollArea {{ background: {bg}; border: none; }}"
            f"QScrollArea::viewport {{ background: {bg}; }}"
            f"QScrollBar:vertical {{ background: {bg}; width: 10px; margin: 0; }}"
            f"QScrollBar::handle:vertical {{ background: {rgba(c['border'], 0.85)}; border-radius: 5px; min-height: 24px; }}"
            f"QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}"
            f"QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: {bg}; }}"
        )
        self.analytics_scroll.viewport().setStyleSheet(f"background: {bg};")
        outer.addWidget(self.analytics_scroll)

        self.analytics_content = QWidget()
        self.analytics_content.setObjectName("TeamAnalyticsContent")
        self.analytics_content.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.analytics_content.setStyleSheet(f"QWidget#TeamAnalyticsContent {{ background: {bg}; }}")
        self.analytics_scroll.setWidget(self.analytics_content)

        root = QVBoxLayout(self.analytics_content)
        root.setContentsMargins(22, 22, 22, 22)
        root.setSpacing(14)

        title = QLabel(team_name or "Team")
        title.setStyleSheet("font-size: 24px; font-weight: 900;")
        root.addWidget(title)

        tasks = (self.data.get("tasks") or {}) if isinstance(self.data, dict) else {}
        total = int(tasks.get("total") or 0)
        completed = int(tasks.get("completed") or 0)
        active = int(tasks.get("active") or 0)
        overdue = int(tasks.get("overdue") or 0)
        rate = float(tasks.get("completion_rate") or 0.0)
        productivity = int(rate * 100)

        focus_section = QFrame()
        focus_section.setStyleSheet(f"QFrame {{ background: {self._card_bg}; border: 1px solid {self._border}; border-radius: 18px; }}")
        focus_layout = QVBoxLayout(focus_section)
        focus_layout.setContentsMargins(18, 18, 18, 18)
        focus_layout.setSpacing(14)

        focus_heading = QLabel("Focus Hours")
        focus_heading.setStyleSheet("font-size: 14px; font-weight: 900;")
        focus_layout.addWidget(focus_heading)

        focus_top = QHBoxLayout()
        self.focus_value = QLabel(f"{productivity}%")
        self.focus_value.setStyleSheet("font-size: 36px; font-weight: 900;")
        focus_top.addWidget(self.focus_value)

        self.focus_delta = QLabel("+0%")
        self.focus_delta.setStyleSheet(f"color: {c['primary']}; font-size: 12px; font-weight: 800;")
        focus_top.addWidget(self.focus_delta)
        focus_top.addStretch()
        focus_layout.addLayout(focus_top)

        self.focus_chart = FocusBarChart()
        self.focus_chart.set_theme(c)
        focus_layout.addWidget(self.focus_chart)
        root.addWidget(focus_section)
        self._refresh_productivity_chart()

        self.quick_container = QWidget()
        quick_layout = QHBoxLayout(self.quick_container)
        quick_layout.setContentsMargins(0, 0, 0, 0)
        quick_layout.setSpacing(14)
        quick_layout.addWidget(self._build_metric_card("Total Tasks", str(total), "All tasks"))
        quick_layout.addWidget(self._build_metric_card("Completed", str(completed), "Done this team"))
        quick_layout.addWidget(self._build_metric_card("Active", str(active), "In progress"))
        quick_layout.addWidget(self._build_metric_card("Overdue", str(overdue), "Needs attention"))
        root.addWidget(self.quick_container)

        # Member Leaders
        leaders_heading = QLabel("Top Contributors")
        leaders_heading.setStyleSheet(f"color: {c['sub']}; font-size: 12px; font-weight: 800;")
        root.addWidget(leaders_heading)

        self.leaders_container = QWidget()
        leaders_layout = QHBoxLayout(self.leaders_container)
        leaders_layout.setContentsMargins(0, 0, 0, 0)
        leaders_layout.setSpacing(12)

        # Top completers
        completed_members = (self.data.get("members", {}).get("completed", []) if isinstance(self.data.get("members", {}), dict) else [])
        if completed_members:
            card = self._build_member_stat_card("Completed Tasks", completed_members[:5])
            leaders_layout.addWidget(card)

        # Top creators
        created_members = (self.data.get("members", {}).get("created", []) if isinstance(self.data.get("members", {}), dict) else [])
        if created_members:
            card = self._build_member_stat_card("Tasks Created", created_members[:5])
            leaders_layout.addWidget(card)

        # Top commenters
        comment_members = (self.data.get("members", {}).get("comments", []) if isinstance(self.data.get("members", {}), dict) else [])
        if comment_members:
            card = self._build_member_stat_card("Comments", comment_members[:5])
            leaders_layout.addWidget(card)

        if completed_members or created_members or comment_members:
            root.addWidget(self.leaders_container)

        self.latest_frame = QFrame()
        self.latest_frame.setStyleSheet(
            f"QFrame {{ background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 {c['primary_soft']}, stop:1 {c['card']}); border: 1px solid {c['primary_soft_border']}; border-radius: 18px; }}"
        )
        latest_layout = QHBoxLayout(self.latest_frame)
        latest_layout.setContentsMargins(16, 14, 16, 14)
        latest_layout.setSpacing(10)

        latest_left = QVBoxLayout()
        latest_title = QLabel("Latest Report")
        latest_title.setStyleSheet(f"color: {c['primary']}; font-size: 10px; font-weight: 800;")
        latest_left.addWidget(latest_title)
        latest_sub = QLabel("Recent team task and activity data is shown here.")
        latest_sub.setStyleSheet(f"color: {c['text']}; font-size: 12px; font-weight: 700;")
        latest_left.addWidget(latest_sub)
        latest_layout.addLayout(latest_left)

        self.latest_action = QPushButton("Refresh Data")
        self.latest_action.setObjectName("HistoryLinkButton")
        self.latest_action.setCursor(Qt.CursorShape.PointingHandCursor)
        self.latest_action.clicked.connect(self._refresh_all)
        latest_layout.addStretch()
        latest_layout.addWidget(self.latest_action)
        root.addWidget(self.latest_frame)

        self.insight_frame = QFrame()
        self.insight_frame.setStyleSheet(
            f"QFrame {{ background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 {c['primary_soft']}, stop:1 {c['card']}); border: 1px solid {c['primary_soft_border']}; border-radius: 18px; }}"
        )
        insight_layout = QVBoxLayout(self.insight_frame)
        insight_layout.setContentsMargins(16, 16, 16, 16)
        insight_layout.setSpacing(10)

        self.insight_kicker = QLabel("Weekly AI Insight")
        self.insight_kicker.setStyleSheet(f"color: {c['primary']}; font-size: 10px; font-weight: 800;")
        insight_layout.addWidget(self.insight_kicker)

        self.insight_text = QLabel("")
        self.insight_text.setWordWrap(True)
        self.insight_text.setStyleSheet(f"color: {c['text']}; font-size: 12px; font-weight: 600;")
        insight_layout.addWidget(self.insight_text)

        self.insight_action = QPushButton("Analyze Team Performance")
        self.insight_action.setObjectName("HistoryPrimaryButton")
        self.insight_action.setCursor(Qt.CursorShape.PointingHandCursor)
        self.insight_action.clicked.connect(self._analyze_team_performance)
        insight_layout.addWidget(self.insight_action)
        root.addWidget(self.insight_frame)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        close_btn = QPushButton("Close")
        close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        close_btn.clicked.connect(self.accept)
        btn_row.addWidget(close_btn)
        root.addLayout(btn_row)

        self._refresh_all()

    def _build_metric_card(self, title: str, value: str, subtitle: str) -> QFrame:
        c = get_theme(self.current_theme)
        card = QFrame()
        card.setStyleSheet(f"QFrame {{ background: {self._card_bg}; border: 1px solid {self._border}; border-radius: 16px; }}")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(6)

        title_lbl = QLabel(title)
        title_lbl.setStyleSheet(f"color: {c['sub']}; font-size: 10px; font-weight: 800;")
        layout.addWidget(title_lbl)

        value_lbl = QLabel(value)
        value_lbl.setStyleSheet(f"color: {c['text']}; font-size: 24px; font-weight: 900;")
        layout.addWidget(value_lbl)

        subtitle_lbl = QLabel(subtitle)
        subtitle_lbl.setStyleSheet(f"color: {c['sub']}; font-size: 10px; font-weight: 700;")
        layout.addWidget(subtitle_lbl)
        return card

    def _refresh_productivity_chart(self):
        events = []
        try:
            res = api_list_team_events(self.team_id, after_id=0, limit=200)
            events = res.get("events", []) if isinstance(res, dict) else []
        except Exception:
            pass
        today = datetime.utcnow().date()
        days = [(today - timedelta(days=i)) for i in range(6, -1, -1)]
        counts = {d.isoformat(): 0 for d in days}
        for ev in events:
            try:
                raw = str(ev.get("created_at") or "")
                if not raw:
                    continue
                dt = datetime.fromisoformat(raw.split(".")[0])
                key = dt.date().isoformat()
                if key in counts:
                    counts[key] += 1
            except Exception:
                continue
        values = [counts.get(d.isoformat(), 0) for d in days]
        labels = [d.strftime("%a")[0] for d in days]
        try:
            self.focus_chart.set_data(values, labels=labels, highlight_index=len(values) - 1)
        except Exception:
            pass

    def _refresh_all(self):
        tasks = (self.data.get("tasks") or {}) if isinstance(self.data, dict) else {}
        completed = int(tasks.get("completed") or 0)
        overdue = int(tasks.get("overdue") or 0)
        rate = float(tasks.get("completion_rate") or 0.0)
        productivity = int(rate * 100)
        if productivity >= 80 and overdue == 0:
            self.insight_text.setText("Great work — the team is highly productive and staying on track.")
        elif productivity >= 50:
            self.insight_text.setText("Good progress, but a few overdue tasks could use attention.")
        else:
            self.insight_text.setText("The team needs to focus on completing tasks and reducing overdue work.")
        self.focus_value.setText(f"{productivity}%")
        self._refresh_productivity_chart()

    def _build_member_stat_card(self, title: str, members: list) -> QFrame:
        c = get_theme(self.current_theme)
        card = QFrame()
        card.setStyleSheet(f"QFrame {{ background: {self._card_bg}; border: 1px solid {self._border}; border-radius: 14px; }}")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(6)

        title_lbl = QLabel(title)
        title_lbl.setStyleSheet(f"color: {c['sub']}; font-size: 10px; font-weight: 800;")
        layout.addWidget(title_lbl)

        for m in members[:5]:
            name = m.get("username") or f"User {m.get('user_id') or ''}"
            count = int(m.get("count") or 0)
            row = QLabel(f"{name}: {count}")
            row.setStyleSheet(f"color: {c['text']}; font-size: 11px; font-weight: 600;")
            layout.addWidget(row)

        return card
        
    def _analyze_team_performance(self):
        # Show a simple breakdown dialog with member stats.
        data = self.data or {}
        try:
            # Try to refresh from server for freshest data
            res = api_team_analytics(int(self.team_id))
            if isinstance(res, dict):
                data = res
        except Exception:
            pass

        members = (data.get("members") or {}) if isinstance(data, dict) else {}
        completed = members.get("completed") if isinstance(members, dict) else members.get("completed") if isinstance(members, dict) else (data.get("members", {}).get("completed") if isinstance(data.get("members", {}), dict) else data.get("members", {}).get("completed"))
        # Fallbacks: ensure lists
        completed_list = []
        try:
            completed_list = data.get("members", {}).get("completed", [])
        except Exception:
            completed_list = []

        dlg = QDialog(self)
        dlg.setWindowTitle("Team Performance Breakdown")
        dlg.setMinimumWidth(420)
        c = get_theme(self.current_theme)
        is_dark = self.current_theme == "Dark"
        bg = c["bg"] if not is_dark else c["deep"]
        dlg.setStyleSheet(f"QDialog {{ background: {bg}; }} QLabel {{ color: {c['text']}; }}")
        layout = QVBoxLayout(dlg)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)

        heading = QLabel("Top contributors (by completed tasks)")
        heading.setStyleSheet(f"color: {c['sub']}; font-size: 12px; font-weight: 800;")
        layout.addWidget(heading)

        if not completed_list:
            lbl = QLabel("No member completion data available.")
            lbl.setStyleSheet(f"color: {c['sub']}; font-size: 11px;")
            layout.addWidget(lbl)
        else:
            for m in completed_list[:10]:
                name = m.get("username") or f"User {m.get('user_id') or ''}"
                count = int(m.get("count") or 0)
                row = QLabel(f"{name}: {count} completed")
                row.setStyleSheet(f"color: {c['text']}; font-size: 12px;")
                layout.addWidget(row)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        close_btn = QPushButton("Close")
        close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        close_btn.clicked.connect(dlg.accept)
        btn_row.addWidget(close_btn)
        layout.addLayout(btn_row)

        dlg.exec()


class _TeamJoinRequestsDialog(QDialog):
    def __init__(self, parent, *, team_id: int, theme: str = "Light", on_members_changed=None):
        super().__init__(parent)
        self.team_id = int(team_id)
        self.current_theme = theme
        self._on_members_changed = on_members_changed
        self.setWindowTitle("Access Requests")
        self.setMinimumWidth(520)

        c = get_theme(theme)
        is_dark = theme == "Dark"
        bg = c["bg"] if not is_dark else c["deep"]
        card_bg = rgba(c["card_alt"], 0.92) if is_dark else rgba(c["card"], 0.95)
        border = rgba(c["border"], 0.7)
        self._card_bg = card_bg
        self._border = border
        self.setStyleSheet(f"QDialog {{ background: {bg}; }} QLabel {{ color: {c['text']}; }}")

        root = QVBoxLayout(self)
        root.setContentsMargins(22, 22, 22, 22)
        root.setSpacing(12)

        header = QHBoxLayout()
        title = QLabel("Pending access requests")
        title.setStyleSheet("font-size: 16px; font-weight: 900;")
        header.addWidget(title)
        header.addStretch()
        refresh_btn = QPushButton("Refresh")
        refresh_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        refresh_btn.clicked.connect(self.refresh)
        header.addWidget(refresh_btn)
        root.addLayout(header)

        self.list_layout = QVBoxLayout()
        self.list_layout.setSpacing(10)
        self.list_container = QWidget()
        self.list_container.setLayout(self.list_layout)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setStyleSheet("QScrollArea { background: transparent; }")
        self.scroll.setWidget(self.list_container)
        self.scroll.setFixedHeight(320)
        root.addWidget(self.scroll)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        close_btn = QPushButton("Close")
        close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        close_btn.clicked.connect(self.accept)
        btn_row.addWidget(close_btn)
        root.addLayout(btn_row)

        self.refresh()

    def _clear_list(self):
        while self.list_layout.count():
            item = self.list_layout.takeAt(0)
            widget = item.widget() if item else None
            if widget:
                self.list_layout.removeWidget(widget)
                widget.deleteLater()

    def refresh(self):
        self._clear_list()
        try:
            res = api_list_team_join_requests(self.team_id)
        except ApiError as e:
            msg = QLabel(str(e))
            msg.setStyleSheet("font-size: 11px; font-weight: 700;")
            self.list_layout.addWidget(msg)
            return
        except Exception:
            msg = QLabel("Failed to load requests.")
            msg.setStyleSheet("font-size: 11px; font-weight: 700;")
            self.list_layout.addWidget(msg)
            return

        requests = res.get("requests", []) if isinstance(res, dict) else []
        if not requests:
            empty = QLabel("No pending requests.")
            empty.setStyleSheet("font-size: 11px; font-weight: 700;")
            self.list_layout.addWidget(empty)
            return

        for req in requests:
            if not isinstance(req, dict):
                continue
            try:
                req_id = int(req.get("id") or 0)
            except Exception:
                req_id = 0
            if req_id <= 0:
                continue
            username = req.get("username") or "User"
            created_at = str(req.get("created_at") or "").split(".")[0].replace("T", " ")
            if created_at:
                subtitle = f"{username}  \u2022  {created_at}"
            else:
                subtitle = str(username)

            card = QFrame()
            card.setStyleSheet(f"QFrame {{ background: {self._card_bg}; border: 1px solid {self._border}; border-radius: 16px; }}")
            layout = QHBoxLayout(card)
            layout.setContentsMargins(14, 12, 14, 12)
            layout.setSpacing(10)

            text = QLabel(subtitle)
            text.setStyleSheet("font-size: 12px; font-weight: 800;")
            layout.addWidget(text, 1)

            btn_reject = QPushButton("Reject")
            btn_reject.setCursor(Qt.CursorShape.PointingHandCursor)
            btn_reject.clicked.connect(lambda _=False, rid=req_id: self._decide(rid, accept=False))
            layout.addWidget(btn_reject)

            btn_accept = QPushButton("Accept")
            btn_accept.setCursor(Qt.CursorShape.PointingHandCursor)
            btn_accept.clicked.connect(lambda _=False, rid=req_id: self._decide(rid, accept=True))
            layout.addWidget(btn_accept)

            self.list_layout.addWidget(card)

    def _decide(self, request_id: int, *, accept: bool):
        try:
            if accept:
                api_accept_team_join_request(self.team_id, int(request_id))
            else:
                api_reject_team_join_request(self.team_id, int(request_id))
        except ApiError as e:
            QMessageBox.warning(self, "Access Requests", str(e))
            return
        except Exception:
            return
        try:
            if callable(self._on_members_changed):
                self._on_members_changed()
        except Exception:
            pass
        self.refresh()


class _TeamInviteDialog(QDialog):
    def __init__(
        self,
        parent,
        *,
        team_id: int,
        theme: str = "Light",
        existing_member_ids=None,
        current_user_id: int | None = None,
        on_members_changed=None,
    ):
        super().__init__(parent)
        self.team_id = int(team_id)
        self.current_theme = theme
        self._existing_member_ids = set(existing_member_ids or set())
        self._current_user_id = int(current_user_id) if current_user_id is not None else None
        self._on_members_changed = on_members_changed
        self.setWindowTitle("Invite Member")
        self.setMinimumWidth(520)

        c = get_theme(theme)
        is_dark = theme == "Dark"
        bg = c["bg"] if not is_dark else c["deep"]
        txt = c["text"]
        card_bg = rgba(c["card_alt"], 0.92) if is_dark else rgba(c["card"], 0.95)
        border = rgba(c["border"], 0.7)
        self._border = border
        self.setStyleSheet(f"QDialog {{ background: {bg}; }} QLabel {{ color: {txt}; }}")

        root = QVBoxLayout(self)
        root.setContentsMargins(22, 22, 22, 22)
        root.setSpacing(12)

        title = QLabel("Search accounts")
        title.setStyleSheet("font-size: 16px; font-weight: 900;")
        root.addWidget(title)

        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("Type a username\u2026")
        self.search_input.setStyleSheet(
            f"background: {c['input_bg']}; border: 1px solid {border}; border-radius: 14px; padding: 10px 12px; color: {txt};"
        )
        self.search_input.textChanged.connect(self._queue_search)
        root.addWidget(self.search_input)

        self.results = QListWidget()
        self.results.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.results.setStyleSheet(
            f"QListWidget {{ background: transparent; border: 0; color: {txt}; }} "
            f"QListWidget::item {{ background: {card_bg}; border: 1px solid {border}; border-radius: 14px; padding: 10px 12px; }} "
            f"QListWidget::item:selected {{ background: rgba(48, 120, 205, 40); border: 1px solid #82AFF2; }}"
        )
        self.results.itemSelectionChanged.connect(self._sync_invite_button)
        self.results.itemDoubleClicked.connect(lambda _item: self._invite_selected())
        root.addWidget(self.results)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        cancel_btn = QPushButton("Cancel")
        cancel_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(cancel_btn)

        self.invite_btn = QPushButton("Invite")
        self.invite_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.invite_btn.setEnabled(False)
        self.invite_btn.clicked.connect(self._invite_selected)
        btn_row.addWidget(self.invite_btn)
        root.addLayout(btn_row)

        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.timeout.connect(self._do_search)

        self.search_input.setFocus()
        self._render_hint("Start typing to search.")

    def _render_hint(self, text: str):
        self.results.clear()
        item = QListWidgetItem(str(text))
        item.setFlags(Qt.ItemFlag.NoItemFlags)
        self.results.addItem(item)
        self.invite_btn.setEnabled(False)

    def _queue_search(self, _text):
        try:
            self._search_timer.start(220)
        except Exception:
            self._do_search()

    def _do_search(self):
        query = self.search_input.text().strip()
        if not query:
            self._render_hint("Start typing to search.")
            return
        if len(query) < 2:
            self._render_hint("Type at least 2 characters.")
            return
        try:
            res = api_search_users(query, limit=15)
        except ApiError as e:
            self._render_hint(str(e))
            return
        except Exception:
            self._render_hint("Search failed.")
            return

        users = res.get("users", []) if isinstance(res, dict) else []
        self.results.clear()
        any_selectable = False
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

            label = username
            selectable = True
            if self._current_user_id is not None and user_id == self._current_user_id:
                label = f"{username} (you)"
                selectable = False
            elif user_id in self._existing_member_ids:
                label = f"{username} (already in team)"
                selectable = False

            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, {"user_id": user_id, "username": username})
            if not selectable:
                item.setFlags(Qt.ItemFlag.NoItemFlags)
            else:
                any_selectable = True
            self.results.addItem(item)

        if self.results.count() == 0:
            self._render_hint("No results.")
            return
        if any_selectable:
            self._select_first_selectable()
        self._sync_invite_button()

    def _select_first_selectable(self):
        for i in range(self.results.count()):
            item = self.results.item(i)
            if item and bool(item.flags() & Qt.ItemFlag.ItemIsSelectable):
                self.results.setCurrentRow(i)
                return

    def _selected_user(self):
        try:
            item = self.results.currentItem()
        except Exception:
            item = None
        if not item:
            return None
        if not bool(item.flags() & Qt.ItemFlag.ItemIsSelectable):
            return None
        try:
            data = item.data(Qt.ItemDataRole.UserRole)
        except Exception:
            data = None
        if not isinstance(data, dict):
            return None
        username = str(data.get("username") or "").strip()
        if not username:
            return None
        return data

    def _sync_invite_button(self):
        self.invite_btn.setEnabled(bool(self._selected_user()))

    def _invite_selected(self):
        user = self._selected_user()
        if not user:
            return
        username = str(user.get("username") or "").strip()
        if not username:
            return
        try:
            api_invite_team_member(self.team_id, username)
        except ApiError as e:
            QMessageBox.warning(self, "Invite Member", str(e))
            return
        except Exception:
            return
        try:
            if callable(self._on_members_changed):
                self._on_members_changed()
        except Exception:
            pass
        self.accept()


class TeamPage(QWidget):
    task_added = pyqtSignal()
    team_pomodoro_requested = pyqtSignal(int, bool)
    team_task_pomodoro_requested = pyqtSignal(str, str, object, object)
    def __init__(self):
        super().__init__()
        self.setObjectName("TeamPage")
        self.current_theme = "Light"
        self.current_team_id = None
        self.team_meta = {}
        self._current_role = "member"
        self._tasks_cache = {}
        self._members_by_id = {}
        self._member_role_by_id = {}
        self._avatar_pixmap_by_user_id = {}
        self._last_event_id_by_team = {}
        self.chat_timer = QTimer()
        self.chat_timer.timeout.connect(self.refresh_chat)
        self.events_timer = QTimer()
        self.events_timer.timeout.connect(self.refresh_events)
        self._build_ui()

    def _open_user_profile(self, user_id: int):
        try:
            uid = int(user_id)
        except Exception:
            return
        if uid <= 0:
            return
        try:
            prof = api_get_user_profile(uid)
        except ApiError as e:
            prof = {"user_id": uid, "username": f"User {uid}", "bio": str(e)}
        except Exception:
            prof = {"user_id": uid, "username": f"User {uid}", "bio": "Could not load profile."}

        dlg = UserProfileDialog(self, theme=self.current_theme, profile=prof if isinstance(prof, dict) else {})
        try:
            dlg.exec()
        except Exception:
            pass

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        self.scroll = QScrollArea()
        self.scroll.setObjectName("TeamPageScroll")
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setStyleSheet("QScrollArea { background: transparent; }")
        root.addWidget(self.scroll)

        self.content = QWidget()
        self.content.setObjectName("TeamContent")
        self.content.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.scroll.setWidget(self.content)
        layout = QVBoxLayout(self.content)
        layout.setContentsMargins(40, 40, 40, 40)
        layout.setSpacing(18)

        self.title = QLabel("Team Workspace")
        self.title.setObjectName("TeamTitle")
        layout.addWidget(self.title)

        self.server_label = QLabel(f"Server: {get_base_url()}")
        self.server_label.setObjectName("TeamServer")
        layout.addWidget(self.server_label)

        team_bar = QFrame()
        team_bar.setObjectName("TeamBar")
        bar_layout = QHBoxLayout(team_bar)
        bar_layout.setContentsMargins(12, 12, 12, 12)
        bar_layout.setSpacing(10)

        self.team_combo = NoWheelComboBox()
        self.team_combo.setMinimumWidth(240)
        self.team_combo.currentIndexChanged.connect(self.on_team_changed)
        bar_layout.addWidget(self.team_combo, stretch=1)

        self.btn_refresh = QPushButton("Refresh")
        self.btn_refresh.clicked.connect(self.refresh_teams)
        bar_layout.addWidget(self.btn_refresh)

        self.btn_create = QPushButton("Create Team")
        self.btn_create.clicked.connect(self.create_team)
        bar_layout.addWidget(self.btn_create)

        self.btn_join = QPushButton("Request Access")
        self.btn_join.clicked.connect(self.join_team)
        bar_layout.addWidget(self.btn_join)

        self.btn_history = QPushButton("History")
        self.btn_history.clicked.connect(self.open_history)
        bar_layout.addWidget(self.btn_history)

        self.btn_analytics = QPushButton("Analytics")
        self.btn_analytics.clicked.connect(self.open_analytics)
        bar_layout.addWidget(self.btn_analytics)

        layout.addWidget(team_bar)

        self.join_code_label = QLabel("Join code: -")
        self.join_code_label.setObjectName("TeamJoinCode")
        layout.addWidget(self.join_code_label)

        self.members_card = QFrame()
        self.members_card.setObjectName("TeamMembersCard")
        members_layout = QVBoxLayout(self.members_card)
        members_layout.setContentsMargins(16, 14, 16, 14)
        members_layout.setSpacing(12)

        members_header = QHBoxLayout()
        self.members_title = QLabel("Team Members")
        self.members_title.setObjectName("TeamMembersTitle")
        self.members_count = QLabel("0 ACTIVE")
        self.members_count.setObjectName("TeamMembersCount")
        members_header.addWidget(self.members_title)
        members_header.addStretch()
        members_header.addWidget(self.members_count)

        self.btn_access_requests = QPushButton("Requests")
        self.btn_access_requests.clicked.connect(self.open_join_requests)
        members_header.addWidget(self.btn_access_requests)

        self.btn_invite_member = QPushButton("Invite")
        self.btn_invite_member.clicked.connect(self.invite_member)
        members_header.addWidget(self.btn_invite_member)

        self.btn_access_requests.setVisible(False)
        self.btn_invite_member.setVisible(False)
        members_layout.addLayout(members_header)

        self.members_list = QHBoxLayout()
        self.members_list.setSpacing(12)
        self.members_list.setContentsMargins(2, 2, 2, 2)
        self.members_list.setAlignment(Qt.AlignmentFlag.AlignLeft)
        try:
            self.members_list.setSizeConstraint(QLayout.SizeConstraint.SetMinAndMaxSize)
        except Exception:
            pass

        self.members_container = QWidget()
        self.members_container.setObjectName("TeamMembersContainer")
        self.members_container.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.members_container.setLayout(self.members_list)
        self.members_scroll = QScrollArea()
        self.members_scroll.setObjectName("TeamMembersScroll")
        self.members_scroll.setWidgetResizable(True)
        self.members_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.members_scroll.setStyleSheet("QScrollArea { background: transparent; }")
        self.members_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.members_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.members_scroll.setWidget(self.members_container)
        self.members_scroll.setFixedHeight(148)
        members_layout.addWidget(self.members_scroll)

        self.members_empty = QLabel("No members yet.")
        self.members_empty.setObjectName("TeamEmpty")
        self.members_list.addWidget(self.members_empty)

        self.chat_card = QFrame()
        self.chat_card.setObjectName("TeamChatCard")
        self.chat_card.setMinimumWidth(560)
        chat_layout = QVBoxLayout(self.chat_card)
        chat_layout.setContentsMargins(16, 14, 16, 14)
        chat_layout.setSpacing(12)

        chat_header = QHBoxLayout()
        self.chat_title = QLabel("#team-workspace")
        self.chat_title.setObjectName("TeamChatTitle")
        self.chat_hint = QLabel("")
        self.chat_hint.setObjectName("TeamChatHint")
        chat_header.addWidget(self.chat_title)
        chat_header.addStretch()

        self.btn_send_alert = QPushButton("Alert")
        self.btn_send_alert.setObjectName("TeamAlertButton")
        self.btn_send_alert.clicked.connect(self.send_team_alert)
        self.btn_send_alert.setVisible(False)
        chat_header.addWidget(self.btn_send_alert)

        chat_header.addWidget(self.chat_hint)
        chat_layout.addLayout(chat_header)

        self.chat_list = QVBoxLayout()
        self.chat_list.setSpacing(12)
        self.chat_list.setContentsMargins(6, 6, 6, 6)

        self.chat_container = QWidget()
        self.chat_container.setObjectName("TeamChatContainer")
        self.chat_container.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.chat_container.setLayout(self.chat_list)
        self.chat_scroll = QScrollArea()
        self.chat_scroll.setObjectName("TeamChatScroll")
        self.chat_scroll.setWidgetResizable(True)
        self.chat_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.chat_scroll.setStyleSheet("QScrollArea { background: transparent; }")
        self.chat_scroll.setWidget(self.chat_container)
        self.chat_scroll.setFixedHeight(452)
        chat_layout.addWidget(self.chat_scroll)

        self.chat_empty = QLabel("No messages yet.")
        self.chat_empty.setObjectName("TeamEmpty")
        self.chat_list.addWidget(self.chat_empty)

        chat_input_row = QHBoxLayout()
        chat_input_row.setSpacing(10)

        self.chat_input_card = QFrame()
        self.chat_input_card.setObjectName("TeamChatInputCard")
        input_layout = QHBoxLayout(self.chat_input_card)
        input_layout.setContentsMargins(14, 6, 14, 6)
        input_layout.setSpacing(8)

        self.chat_input = QLineEdit()
        self.chat_input.setObjectName("TeamChatInput")
        self.chat_input.setPlaceholderText("Message #team-workspace...")
        self.chat_input.setFixedHeight(32)
        self.chat_input.setFrame(False)
        self.chat_input.returnPressed.connect(self.send_chat_message)
        input_layout.addWidget(self.chat_input, 1)

        chat_input_row.addWidget(self.chat_input_card, 1)

        self.chat_send_btn = QPushButton(">")
        self.chat_send_btn.setObjectName("TeamChatSend")
        self.chat_send_btn.setFixedSize(44, 44)
        self.chat_send_btn.clicked.connect(self.send_chat_message)
        chat_input_row.addWidget(self.chat_send_btn)
        chat_layout.addLayout(chat_input_row)
        layout.addWidget(self.members_card)

        main_row = QHBoxLayout()
        main_row.setSpacing(18)
        layout.addLayout(main_row)
        main_row.addWidget(self.chat_card, 3)

        self.recent_tasks_card = QFrame()
        self.recent_tasks_card.setObjectName("TeamRecentTasksCard")
        self.recent_tasks_card.setMinimumWidth(360)
        recent_layout = QVBoxLayout(self.recent_tasks_card)
        recent_layout.setContentsMargins(16, 14, 16, 14)
        recent_layout.setSpacing(12)

        tasks_header = QHBoxLayout()
        self.tasks_title = QLabel("Recent Tasks")
        self.tasks_title.setObjectName("TeamRecentTasksTitle")
        tasks_header.addWidget(self.tasks_title)
        tasks_header.addStretch()
        self.btn_add_task = QPushButton("Add Task")
        self.btn_add_task.clicked.connect(self.add_task)
        tasks_header.addWidget(self.btn_add_task)
        recent_layout.addLayout(tasks_header)

        self.tasks_container = QWidget()
        self.tasks_container.setObjectName("TeamTasksContainer")
        self.tasks_container.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.tasks_layout = QVBoxLayout(self.tasks_container)
        self.tasks_layout.setContentsMargins(0, 0, 0, 0)
        self.tasks_layout.setSpacing(14)
        self.tasks_layout.setAlignment(Qt.AlignmentFlag.AlignTop)

        self.recent_tasks_scroll = QScrollArea()
        self.recent_tasks_scroll.setObjectName("TeamRecentTasksScroll")
        self.recent_tasks_scroll.setWidgetResizable(True)
        self.recent_tasks_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.recent_tasks_scroll.setStyleSheet("QScrollArea { background: transparent; }")
        self.recent_tasks_scroll.setWidget(self.tasks_container)
        self.recent_tasks_scroll.setFixedHeight(452)
        recent_layout.addWidget(self.recent_tasks_scroll)

        self.empty_label = QLabel("No team tasks yet.")
        self.empty_label.setObjectName("TeamEmpty")
        self.tasks_layout.addWidget(self.empty_label)

        main_row.addWidget(self.recent_tasks_card, 2)

    def update_theme(self, theme):
        self.current_theme = theme
        c = get_theme(theme)
        page_bg = c["bg"]
        panel_bg = rgba(c["card_alt"], 0.92) if theme == "Dark" else c["card_alt"]
        chat_bg = rgba(c["card"], 0.85)
        button_bg = c["accent"]
        button_hover = c["deep"] if theme == "Light" else c["accent2"]
        button_disabled_bg = c["deep"] if theme == "Light" else rgba(c["card_alt"], 0.72)
        button_disabled_text = "#FFFFFF" if theme == "Light" else c["sub"]
        self.setStyleSheet(
            f"QWidget#TeamPage {{ background: {page_bg}; font-family: '{FONT_FAMILY}', 'Segoe UI'; }}"
            f"QWidget#TeamContent {{ background: {page_bg}; }}"
            f"QWidget#TeamMembersContainer {{ background: {page_bg}; }}"
            f"QWidget#TeamTasksContainer {{ background: {panel_bg}; }}"
            f"QScrollArea#TeamPageScroll {{ background: {page_bg}; border: none; }}"
            f"QScrollArea#TeamPageScroll > QWidget > QWidget {{ background: {page_bg}; }}"
            f"QScrollArea#TeamPageScroll QAbstractScrollArea::viewport {{ background: {page_bg}; }}"
            f"QLabel#TeamTitle {{ color: {c['text']}; font-size: 30px; font-weight: 900; }}"
            f"QLabel#TeamServer {{ color: {c['sub']}; font-size: 12px; }}"
            f"QFrame#TeamBar {{ background: {rgba(c['card'], 0.96)}; border: 1px solid {c['border']}; border-radius: 14px; }}"
            f"QComboBox {{ background-color: {c['input_bg']}; border: 1px solid {c['border']}; border-radius: 8px; padding: 6px 10px; color: {c['text']}; }}"
            f"QComboBox::drop-down {{ border: none; width: 24px; }}"
            f"QComboBox QAbstractItemView {{ background-color: {c['card']}; color: {c['text']}; selection-background-color: {c['accent_soft']}; selection-color: {c['deep']}; border: 1px solid {c['border']}; }}"
            f"QPushButton {{ background-color: {button_bg}; color: #FFFFFF; border: 1px solid {button_bg}; border-radius: 8px; padding: 8px 14px; font-weight: 800; }}"
            f"QPushButton:hover {{ background-color: {button_hover}; border-color: {button_hover}; color: #FFFFFF; }}"
            f"QPushButton:disabled {{ background-color: {button_disabled_bg}; color: {button_disabled_text}; border: 1px solid {c['border']}; }}"
            f"QLabel#TeamJoinCode {{ color: {c['sub']}; font-weight: 600; }}"
            f"QLabel#TeamSectionTitle {{ color: {c['text']}; font-size: 18px; font-weight: 800; }}"
            f"QLabel#TeamRecentTasksTitle {{ color: {c['text']}; font-size: 14px; font-weight: 900; }}"
            f"QLabel#TeamEmpty {{ color: {c['sub']}; font-size: 12px; }}"
            f"QFrame#TeamMembersCard {{ background: transparent; border: none; }}"
            f"QFrame#TeamChatCard {{ background: {rgba(c['card_alt'], 0.92)}; border: 1px solid {rgba(c['border'], 0.7)}; border-radius: 20px; }}"
            f"QFrame#TeamRecentTasksCard {{ background: {rgba(c['card_alt'], 0.92)}; border: 1px solid {rgba(c['border'], 0.7)}; border-radius: 20px; }}"
            f"QLabel#TeamMembersTitle {{ color: {c['text']}; font-size: 14px; font-weight: 800; }}"
            f"QLabel#TeamChatTitle {{ color: {c['text']}; font-size: 14px; font-weight: 900; }}"
            f"QLabel#TeamMembersCount {{ background: transparent; color: {c['sub']}; padding: 2px 2px; font-size: 10px; font-weight: 800; }}"
            f"QLabel#TeamChatHint {{ background: {rgba(c['accent'], 0.2)}; color: {c['accent']}; border-radius: 10px; padding: 2px 8px; font-size: 9px; font-weight: 800; }}"
            f"QPushButton#TeamAlertButton {{ background-color: {c['bad']}; color: white; border: 1px solid {c['bad']}; border-radius: 10px; padding: 6px 12px; font-size: 10px; font-weight: 900; }}"
            f"QPushButton#TeamAlertButton:hover {{ background-color: #B91C1C; border-color: #B91C1C; }}"
            f"QFrame#TeamChatInputCard {{ background: {rgba(c['card_alt'], 0.85)}; border: 1px solid {rgba(c['border'], 0.7)}; border-radius: 22px; }}"
            f"QLineEdit#TeamChatInput {{ background: transparent; border: none; padding: 0 4px; color: {c['text']}; font-size: 12px; }}"
            f"QPushButton#TeamChatSend {{ background: {c['primary_gradient']}; color: white; border: 1px solid {c['accent']}; border-radius: 22px; padding: 0; font-weight: 900; }}"
            f"QPushButton#TeamChatSend:hover {{ background-color: {c['accent']}; border-color: {c['accent']}; }}"
        )
        self._apply_scroll_background(self.scroll, page_bg)
        self._apply_scroll_background(self.members_scroll, page_bg)
        self._apply_scroll_background(self.chat_scroll, chat_bg)
        self._apply_scroll_background(self.recent_tasks_scroll, panel_bg)
        if hasattr(self, "members_container"):
            self.members_container.setStyleSheet(f"QWidget#TeamMembersContainer {{ background: {page_bg}; }}")
        if hasattr(self, "chat_container"):
            self.chat_container.setStyleSheet(
                f"QWidget#TeamChatContainer {{ background: {chat_bg}; border: 1px solid {rgba(c['border'], 0.6)}; border-radius: 16px; }}"
            )
        if hasattr(self, "tasks_container"):
            self.tasks_container.setStyleSheet(f"QWidget#TeamTasksContainer {{ background: {panel_bg}; }}")
        button_style = (
            f"QPushButton {{ background-color: {button_bg}; color: #FFFFFF; "
            f"border: 1px solid {button_bg}; border-radius: 8px; padding: 8px 14px; font-weight: 800; }}"
            f"QPushButton:hover {{ background-color: {button_hover}; border-color: {button_hover}; color: #FFFFFF; }}"
            f"QPushButton:disabled {{ background-color: {button_disabled_bg}; color: {button_disabled_text}; "
            f"border: 1px solid {c['border']}; }}"
            f"QPushButton:!enabled {{ background-color: {button_disabled_bg}; color: {button_disabled_text}; "
            f"border: 1px solid {c['border']}; }}"
        )
        for attr in (
            "btn_refresh", "btn_create", "btn_join", "btn_history", "btn_analytics",
            "btn_access_requests", "btn_invite_member", "btn_add_task",
        ):
            btn = getattr(self, attr, None)
            if btn:
                try:
                    btn.setStyleSheet(button_style)
                except Exception:
                    pass
        for i in range(self.tasks_layout.count()):
            item = self.tasks_layout.itemAt(i)
            widget = item.widget() if item else None
            if widget and hasattr(widget, "update_theme"):
                try:
                    widget.update_theme(theme)
                except Exception:
                    pass

    def _apply_scroll_background(self, scroll, bg):
        try:
            scroll.setStyleSheet(
                f"QScrollArea {{ background: {bg}; border: none; }}"
                f"QScrollArea::viewport {{ background: {bg}; }}"
                f"QScrollBar:vertical {{ background: {bg}; width: 10px; margin: 0; }}"
                f"QScrollBar::handle:vertical {{ background: {rgba(get_theme(self.current_theme)['border'], 0.85)}; border-radius: 5px; min-height: 24px; }}"
                f"QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}"
                f"QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: {bg}; }}"
                f"QScrollBar:horizontal {{ background: {bg}; height: 10px; margin: 0; }}"
                f"QScrollBar::handle:horizontal {{ background: {rgba(get_theme(self.current_theme)['border'], 0.85)}; border-radius: 5px; min-width: 24px; }}"
                f"QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{ width: 0; }}"
                f"QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal {{ background: {bg}; }}"
            )
            scroll.viewport().setStyleSheet(f"background: {bg};")
        except Exception:
            pass

    def refresh_teams(self):
        self.server_label.setText(f"Server: {get_base_url()}")
        try:
            res = api_list_teams()
        except ApiError as e:
            QMessageBox.warning(self, "Team Error", str(e))
            return
        teams = res.get("teams", []) if isinstance(res, dict) else []
        self.team_combo.blockSignals(True)
        self.team_combo.clear()
        self.team_meta = {}
        if not teams:
            self.team_combo.addItem("No teams yet", None)
        else:
            for t in teams:
                team_id = t.get("id")
                name = t.get("name") or f"Team {team_id}"
                self.team_combo.addItem(name, team_id)
                self.team_meta[team_id] = t
        self.team_combo.blockSignals(False)
        self.on_team_changed(self.team_combo.currentIndex())

    def on_team_changed(self, idx):
        team_id = self.team_combo.currentData()
        self.current_team_id = team_id
        if not team_id:
            self.join_code_label.setText("Join code: -")
            self._render_tasks([])
            self._render_members([])
            self._render_chat([])
            try:
                self.chat_title.setText("#team-workspace")
                self.chat_input.setPlaceholderText("Message #team-workspace...")
            except Exception:
                pass
            self._current_role = "member"
            self._update_admin_controls()
            if self.chat_timer.isActive():
                self.chat_timer.stop()
            if self.events_timer.isActive():
                self.events_timer.stop()
            return
        try:
            info = api_get_team(team_id)
            join_code = info.get("join_code") if isinstance(info, dict) else None
            role = info.get("role") if isinstance(info, dict) else None
            self._current_role = str(role or "member").strip().lower() or "member"
            if join_code:
                self.join_code_label.setText(f"Join code: {join_code}")
            else:
                self.join_code_label.setText("Join code: -")
        except ApiError:
            self.join_code_label.setText("Join code: -")
            self._current_role = "member"

        try:
            raw_name = str(self.team_combo.currentText() or "team-workspace").strip()
            slug = re.sub(r"[^a-z0-9]+", "-", raw_name.strip().lower()).strip("-") or "team-workspace"
            channel = f"#{slug}"
            self.chat_title.setText(channel)
            self.chat_input.setPlaceholderText(f"Message {channel}...")
        except Exception:
            pass
        self._update_admin_controls()
        self.refresh_members()
        self.refresh_chat()
        if not self.chat_timer.isActive():
            self.chat_timer.start(5000)
        if not self.events_timer.isActive():
            self.events_timer.start(2500)
        # Prime event cursor so we don't replay old events on first poll.
        try:
            res = api_list_team_events(team_id, after_id=0, limit=200)
            events = res.get("events", []) if isinstance(res, dict) else []
            max_id = 0
            for ev in events:
                try:
                    max_id = max(max_id, int((ev or {}).get("id") or 0))
                except Exception:
                    pass
            self._last_event_id_by_team[int(team_id)] = int(max_id or 0)
        except Exception:
            self._last_event_id_by_team[int(team_id)] = int((self._last_event_id_by_team or {}).get(int(team_id), 0) or 0)
        self.refresh_tasks()

    def refresh_tasks(self):
        if not self.current_team_id:
            self._render_tasks([])
            return
        try:
            res = api_list_team_tasks(self.current_team_id)
        except ApiError as e:
            QMessageBox.warning(self, "Team Tasks", str(e))
            return
        tasks = res.get("tasks", []) if isinstance(res, dict) else []
        self._tasks_cache = {t.get("id"): t for t in tasks if isinstance(t, dict)}
        focus_map = self._load_focus_minutes(tasks)
        self._render_tasks(tasks, focus_map)

    def refresh_members(self):
        if not self.current_team_id:
            self._render_members([])
            return
        try:
            res = api_list_team_members(self.current_team_id)
        except ApiError as e:
            QMessageBox.warning(self, "Team Members", str(e))
            return
        members = res.get("members", []) if isinstance(res, dict) else []
        # Avoid caching "no avatar" forever: allow newly uploaded avatars to appear without restart.
        self._avatar_pixmap_by_user_id = {}
        by_id = {}
        role_by_id = {}
        for m in members:
            if not isinstance(m, dict):
                continue
            uid = m.get("user_id")
            uname = m.get("username")
            role = m.get("role")
            if uid is None:
                continue
            by_id[int(uid)] = str(uname or "").strip() or f"User {uid}"
            role_by_id[int(uid)] = str(role or "member").strip().lower() or "member"
        self._members_by_id = by_id
        self._member_role_by_id = role_by_id
        self._render_members(members)

    def refresh_chat(self):
        if not self.current_team_id:
            self._render_chat([])
            return
        try:
            res = api_list_team_messages(self.current_team_id, limit=50)
        except ApiError:
            return
        except Exception:
            return
        messages = res.get("messages", []) if isinstance(res, dict) else []
        self._render_chat(messages)

    def refresh_events(self):
        if not self.current_team_id:
            return
        team_id = int(self.current_team_id)
        last_id = int((self._last_event_id_by_team or {}).get(team_id, 0) or 0)
        try:
            res = api_list_team_events(team_id, after_id=last_id, limit=50)
        except Exception:
            return
        events = res.get("events", []) if isinstance(res, dict) else []
        if not events:
            return
        refresh_tasks = False
        refresh_members = False
        refresh_chat = False
        for ev in events:
            if not isinstance(ev, dict):
                continue
            try:
                ev_id = int(ev.get("id") or 0)
            except Exception:
                ev_id = 0
            if ev_id > last_id:
                last_id = ev_id
            et = str(ev.get("event_type") or "").strip()
            if et in ("task_created", "task_updated", "task_deleted"):
                refresh_tasks = True
            elif et in ("member_role_updated", "member_added"):
                refresh_members = True
            elif et in ("member_removed",):
                refresh_members = True
                refresh_tasks = True
            elif et in ("team_message", "team_alert"):
                refresh_chat = True
            elif et in ("task_comment",):
                # Comments are shown inside the task dialog; keep the main list fresh.
                refresh_tasks = True
        self._last_event_id_by_team[team_id] = last_id
        if refresh_members:
            try:
                self.refresh_members()
            except Exception:
                pass
        if refresh_tasks:
            try:
                self.refresh_tasks()
            except Exception:
                pass
        if refresh_chat:
            try:
                self.refresh_chat()
            except Exception:
                pass

    def pause_network(self):
        if self.chat_timer.isActive():
            self.chat_timer.stop()

    def send_chat_message(self):
        if not self.current_team_id:
            QMessageBox.information(self, "Team Chat", "Please select a team first.")
            return
        msg = self.chat_input.text().strip() if hasattr(self, "chat_input") else ""
        if not msg:
            return
        try:
            api_send_team_message(self.current_team_id, msg)
        except ApiError as e:
            QMessageBox.warning(self, "Team Chat", str(e))
            return
        self.chat_input.clear()
        self.refresh_chat()

    def send_team_alert(self):
        if not self.current_team_id:
            QMessageBox.information(self, "Team Alert", "Please select a team first.")
            return
        if not self._can_send_alerts():
            QMessageBox.information(self, "Team Alert", "Only managers and admins can send alerts.")
            return
        msg, ok = self._prompt_text("Send Alert", "Alert message")
        if not ok or not msg:
            return
        try:
            api_send_team_alert(self.current_team_id, msg)
        except ApiError as e:
            QMessageBox.warning(self, "Team Alert", str(e))
            return
        self.refresh_chat()

    def _render_tasks(self, tasks, focus_map=None):
        while self.tasks_layout.count():
            item = self.tasks_layout.takeAt(0)
            widget = item.widget()
            if widget:
                self.tasks_layout.removeWidget(widget)
                if widget is not self.empty_label:
                    widget.deleteLater()
        try:
            self.empty_label.hide()
        except Exception:
            pass
        if not tasks:
            try:
                self.empty_label.show()
            except Exception:
                pass
            self.tasks_layout.addWidget(self.empty_label)
        else:
            for t in tasks:
                focus_minutes = 0
                if focus_map is not None:
                    try:
                        focus_minutes = int(focus_map.get(t.get("id"), 0) or 0)
                    except Exception:
                        focus_minutes = 0
                card = self._build_task_card(t, focus_minutes)
                if card:
                    self.tasks_layout.addWidget(card)

    def _current_username(self):
        try:
            data = load_settings()
            username = data.get("cloud_username") if isinstance(data, dict) else None
            username = str(username or "").strip()
            return username or None
        except Exception:
            return None
        return None

    def _current_user_id(self):
        try:
            data = load_settings()
            uid = data.get("cloud_user_id") if isinstance(data, dict) else None
            return int(uid) if uid is not None else None
        except Exception:
            return None

    def _role_rank(self, role):
        ranks = {"member": 0, "manager": 1, "admin": 2, "owner": 3}
        try:
            return int(ranks.get(str(role or "").strip().lower(), 0))
        except Exception:
            return 0

    def _can_manage_roles(self):
        return self._role_rank(self._current_role) >= self._role_rank("admin")

    def _can_assign_tasks(self):
        return self._role_rank(self._current_role) >= self._role_rank("admin")

    def _is_team_admin(self):
        return self._role_rank(self._current_role) >= self._role_rank("admin")

    def _can_send_alerts(self):
        return self._role_rank(self._current_role) >= self._role_rank("manager")

    def _update_admin_controls(self):
        is_admin = bool(self.current_team_id) and self._is_team_admin()
        for attr in ("btn_access_requests", "btn_invite_member"):
            btn = getattr(self, attr, None)
            if not btn:
                continue
            try:
                btn.setVisible(bool(is_admin))
            except Exception:
                pass
        alert_btn = getattr(self, "btn_send_alert", None)
        if alert_btn:
            try:
                alert_btn.setVisible(bool(self.current_team_id) and self._can_send_alerts())
            except Exception:
                pass

    def _set_member_role(self, user_id: int, new_role: str):
        if not self.current_team_id:
            return
        role = str(new_role or "").strip().lower()
        if role not in ("member", "manager", "admin"):
            return
        try:
            api_update_team_member_role(self.current_team_id, int(user_id), role)
        except ApiError as e:
            QMessageBox.warning(self, "Roles", str(e))
        except Exception:
            pass
        self.refresh_members()

    def _remove_member(self, user_id: int, username: str | None = None):
        if not self.current_team_id:
            return
        if not self._is_team_admin():
            QMessageBox.information(self, "Remove Member", "Only the team admin can remove members.")
            return
        try:
            uid = int(user_id)
        except Exception:
            return
        me = self._current_user_id()
        if me is not None and uid == int(me):
            QMessageBox.information(self, "Remove Member", "Admin cannot remove themselves.")
            return
        uname = str(username or "").strip() or f"User {uid}"
        confirm = QMessageBox.question(
            self,
            "Remove Member",
            f"Remove {uname} from this team?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if confirm != QMessageBox.StandardButton.Yes:
            return
        try:
            api_remove_team_member(int(self.current_team_id), uid)
        except ApiError as e:
            QMessageBox.warning(self, "Remove Member", str(e))
            return
        except Exception:
            return
        self.refresh_members()
        self.refresh_tasks()

    def _member_avatar_colors(self, username):
        c = get_theme(self.current_theme)
        palette = [c["accent"], c["accent2"], c["deep"], c["chip_text"]]
        key = sum(ord(ch) for ch in (username or "")) % len(palette)
        base = palette[key]
        return rgba(base, 0.22), base

    def _circle_pixmap(self, source: QPixmap, size: int) -> QPixmap | None:
        try:
            size_i = int(size)
        except Exception:
            size_i = 46
        size_i = max(8, size_i)
        if source is None or source.isNull():
            return None
        try:
            scaled = source.scaled(
                size_i,
                size_i,
                Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                Qt.TransformationMode.SmoothTransformation,
            )
        except Exception:
            scaled = source
        result = QPixmap(size_i, size_i)
        result.fill(Qt.GlobalColor.transparent)
        painter = QPainter(result)
        try:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        except Exception:
            pass
        path = QPainterPath()
        path.addEllipse(0, 0, size_i, size_i)
        painter.setClipPath(path)
        try:
            dx = int((scaled.width() - size_i) / 2)
            dy = int((scaled.height() - size_i) / 2)
        except Exception:
            dx, dy = 0, 0
        painter.drawPixmap(-dx, -dy, scaled)
        painter.end()
        return result

    def _get_avatar_pixmap_for_user(self, user_id: int, *, size: int = 46) -> QPixmap | None:
        try:
            uid = int(user_id)
        except Exception:
            return None
        if uid <= 0:
            return None
        cached = (self._avatar_pixmap_by_user_id or {}).get(uid, None)
        if cached is _NO_AVATAR:
            return None
        if isinstance(cached, QPixmap) and not cached.isNull():
            return cached

        data = None
        try:
            data = api_get_user_avatar(uid)
        except ApiError:
            data = None
        except Exception:
            data = None

        if not data:
            self._avatar_pixmap_by_user_id[uid] = _NO_AVATAR
            return None
        pixmap = QPixmap()
        try:
            ok = pixmap.loadFromData(data)
        except Exception:
            ok = False
        if not ok or pixmap.isNull():
            self._avatar_pixmap_by_user_id[uid] = _NO_AVATAR
            return None
        circle = self._circle_pixmap(pixmap, size)
        if circle is None or circle.isNull():
            self._avatar_pixmap_by_user_id[uid] = _NO_AVATAR
            return None
        self._avatar_pixmap_by_user_id[uid] = circle
        return circle

    def _build_member_card(self, user_id, username, role):
        c = get_theme(self.current_theme)
        card = QFrame()
        card.setObjectName("TeamMemberCard")
        card.setFixedWidth(228)
        card.setMinimumHeight(132)
        card.setStyleSheet(
            f"QFrame#TeamMemberCard {{ background: {rgba(c['card_alt'], 0.92)}; border: 1px solid {rgba(c['border'], 0.65)}; border-radius: 18px; }}"
            f"QFrame#TeamMemberCard:hover {{ border: 1px solid {rgba(c['accent'], 0.6)}; }}"
        )
        layout = QVBoxLayout(card)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(8)

        initials = "".join([part[:1] for part in re.split(r"[^A-Za-z0-9]+", username or "") if part])[:2].upper()
        if not initials:
            initials = "U"
        r = str(role or "member").strip().lower() or "member"

        top = QHBoxLayout()
        top.setSpacing(10)

        avatar = QLabel()
        avatar.setAlignment(Qt.AlignmentFlag.AlignCenter)
        avatar.setFixedSize(46, 46)
        avatar.setCursor(Qt.CursorShape.PointingHandCursor)
        avatar_pix = self._get_avatar_pixmap_for_user(user_id, size=46) if user_id is not None else None
        if avatar_pix is not None and not avatar_pix.isNull():
            avatar.setPixmap(avatar_pix)
            avatar.setStyleSheet("background: transparent; border-radius: 23px;")
        else:
            avatar_bg, avatar_fg = self._member_avatar_colors(username)
            avatar.setText(initials)
            avatar.setStyleSheet(
                f"background: {avatar_bg}; color: {avatar_fg}; border-radius: 23px; font-weight: 900; font-size: 14px;"
            )
        top.addWidget(avatar)
        top.addStretch()

        badge_text = ("admin" if r == "owner" else r).upper()
        if r == "owner":
            badge_bg = rgba(c["accent"], 0.22)
            badge_fg = c["accent"]
        elif r == "admin":
            badge_bg = rgba(c["accent2"], 0.18)
            badge_fg = c["accent2"]
        elif r == "manager":
            badge_bg = rgba(c["chip"], 0.18)
            badge_fg = c["chip_text"]
        else:
            badge_bg = rgba(c["chip_text"], 0.18)
            badge_fg = c["sub"]
        badge = QLabel(badge_text)
        badge.setStyleSheet(
            f"background: {badge_bg}; color: {badge_fg}; border-radius: 10px; padding: 2px 10px; "
            f"font-size: 9px; font-weight: 800; letter-spacing: 1px;"
        )
        top.addWidget(badge)
        layout.addLayout(top)

        name = QLabel(username or "Unknown")
        name.setStyleSheet(f"font-size: 12px; font-weight: 900; color: {c['text']};")
        name.setCursor(Qt.CursorShape.PointingHandCursor)
        layout.addWidget(name)

        role_text = {"owner": "Admin", "admin": "Admin", "manager": "Manager", "member": "Member"}.get(r, "Member")
        subtitle = QLabel(role_text)
        subtitle.setStyleSheet(f"font-size: 10px; color: {c['sub']}; font-weight: 650;")
        layout.addWidget(subtitle)
        layout.addStretch()

        # Open member profile when clicking avatar/name.
        if user_id is not None:
            try:
                uid_int = int(user_id)
            except Exception:
                uid_int = 0
            if uid_int > 0:
                try:
                    avatar.mousePressEvent = lambda _e, uid=uid_int: self._open_user_profile(uid)
                    name.mousePressEvent = lambda _e, uid=uid_int: self._open_user_profile(uid)
                except Exception:
                    pass

        if self._can_manage_roles() and r != "owner" and user_id is not None:
            actions = QHBoxLayout()
            actions.setSpacing(8)

            role_combo = NoWheelComboBox()
            role_combo.setMinimumHeight(28)
            role_combo.setCursor(Qt.CursorShape.PointingHandCursor)
            role_combo.addItem("member")
            role_combo.addItem("manager")
            role_combo.addItem("admin")
            role_combo.setCurrentText(r if r in ("member", "manager", "admin") else "member")
            role_combo.currentTextChanged.connect(lambda new_r, uid=int(user_id): self._set_member_role(uid, new_r))
            role_combo.setStyleSheet(
                f"QComboBox {{ background: {c['input_bg']}; border: 1px solid {rgba(c['border'], 0.8)}; border-radius: 10px; padding: 4px 8px; color: {c['text']}; }}"
            )
            actions.addWidget(role_combo, 1)

            if self._is_team_admin() and r != "owner":
                remove_btn = QPushButton("Remove")
                remove_btn.setCursor(Qt.CursorShape.PointingHandCursor)
                remove_btn.setMinimumHeight(28)
                remove_btn.setStyleSheet(
                    "QPushButton { background-color: #ef4444; border: 1px solid #b91c1c; color: white; "
                    "border-radius: 10px; font-weight: 900; font-size: 10px; padding: 0px 12px; }"
                    "QPushButton:hover { background-color: #b91c1c; border-color: #7f1d1d; }"
                )
                remove_btn.clicked.connect(lambda _=False, uid=int(user_id), uname=str(username or ""): self._remove_member(uid, uname))
                actions.addWidget(remove_btn)

            layout.addLayout(actions)
        return card

    def _render_members(self, members):
        while self.members_list.count():
            item = self.members_list.takeAt(0)
            widget = item.widget()
            if widget:
                self.members_list.removeWidget(widget)
                if widget is not self.members_empty:
                    widget.deleteLater()
        try:
            self.members_empty.hide()
        except Exception:
            pass
        if not members:
            try:
                self.members_empty.show()
            except Exception:
                pass
            self.members_list.addWidget(self.members_empty)
            if hasattr(self, "members_count"):
                self.members_count.setText("0 ACTIVE")
            return
        if hasattr(self, "members_count"):
            self.members_count.setText(f"{len(members)} ACTIVE")
        for m in members:
            username = m.get("username") if isinstance(m, dict) else None
            role = m.get("role") if isinstance(m, dict) else None
            user_id = m.get("user_id") if isinstance(m, dict) else None
            card = self._build_member_card(user_id, username, role)
            self.members_list.addWidget(card)

    def _render_chat(self, messages):
        c = get_theme(self.current_theme)
        while self.chat_list.count():
            item = self.chat_list.takeAt(0)
            widget = item.widget()
            if widget:
                self.chat_list.removeWidget(widget)
                if widget is not self.chat_empty:
                    widget.deleteLater()
        try:
            self.chat_empty.hide()
        except Exception:
            pass
        if not messages:
            try:
                self.chat_empty.show()
            except Exception:
                pass
            self.chat_list.addWidget(self.chat_empty)
            if hasattr(self, "chat_hint"):
                self.chat_hint.setText("Live")
            self._update_chat_avatars([])
            return
        if hasattr(self, "chat_hint"):
            self.chat_hint.setText("")

        current_user = (self._current_username() or "").strip().lower()
        participants = []
        for msg in messages:
            username = msg.get("username") if isinstance(msg, dict) else None
            if not username:
                continue
            uname = str(username)
            if uname not in participants:
                participants.append(uname)
        self._update_chat_avatars(participants)
        for msg in messages:
            username = msg.get("username") if isinstance(msg, dict) else None
            text = msg.get("message") if isinstance(msg, dict) else None
            created_at = msg.get("created_at") if isinstance(msg, dict) else None
            is_own = bool(current_user and username and str(username).strip().lower() == current_user)
            raw_text = str(text or "")
            is_alert = raw_text.startswith("[ALERT] ")
            display_text = raw_text[len("[ALERT] "):] if is_alert else raw_text

            bubble = QFrame()
            if is_alert:
                bubble_bg = rgba(c["bad"], 0.16)
                border = rgba(c["bad"], 0.72)
                body_color = c["text"]
                time_color = c["sub"]
            elif is_own:
                bubble_bg = c["primary_gradient"]
                border = rgba(c["accent2"], 0.55)
                body_color = "white"
                time_color = rgba("#FFFFFF", 0.75)
            else:
                bubble_bg = rgba(c["card"], 0.85)
                border = rgba(c["border"], 0.55)
                body_color = c["text"]
                time_color = c["sub"]
            bubble.setStyleSheet(
                f"QFrame {{ background: {bubble_bg}; border: 1px solid {border}; border-radius: 18px; }}"
            )
            bubble.setMaximumWidth(520)
            bubble_layout = QVBoxLayout(bubble)
            bubble_layout.setContentsMargins(12, 10, 12, 10)
            bubble_layout.setSpacing(4)

            header = QHBoxLayout()
            if is_alert:
                alert_badge = QLabel("ALERT")
                alert_badge.setStyleSheet(
                    f"background: {c['bad']}; color: white; border-radius: 8px; padding: 1px 8px; "
                    "font-size: 9px; font-weight: 900;"
                )
                header.addWidget(alert_badge)
            if not is_own:
                name = QLabel(str(username or "User").upper())
                name.setStyleSheet(f"font-size: 9px; font-weight: 800; color: {c['accent']}; letter-spacing: 1px;")
                header.addWidget(name)
            header.addStretch()
            stamp = self._format_chat_time(created_at)
            if stamp:
                time_lbl = QLabel(stamp)
                time_lbl.setStyleSheet(f"font-size: 9px; color: {time_color};")
                header.addWidget(time_lbl)
            bubble_layout.addLayout(header)

            body = QLabel(display_text)
            body.setWordWrap(True)
            body.setStyleSheet(f"font-size: 11px; font-weight: 650; color: {body_color};")
            bubble_layout.addWidget(body)

            wrapper = QWidget()
            row = QHBoxLayout(wrapper)
            row.setContentsMargins(0, 0, 0, 0)
            if is_own:
                row.addStretch()
                row.addWidget(bubble)
            else:
                row.addWidget(bubble)
                row.addStretch()
            self.chat_list.addWidget(wrapper)

        try:
            bar = self.chat_scroll.verticalScrollBar()
            bar.setValue(bar.maximum())
        except Exception:
            pass

    def _update_chat_avatars(self, usernames):
        return

    def _format_chat_time(self, raw):
        if not raw:
            return ""
        raw_text = str(raw).replace("T", " ").split(".")[0]
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
            try:
                dt = datetime.strptime(raw_text, fmt)
                return dt.strftime("%I:%M %p").lstrip("0")
            except Exception:
                continue
        return raw_text

    def create_team(self):
        name, ok = self._prompt_text("Create Team", "Team name")
        if not ok or not name:
            return
        try:
            api_create_team(name)
        except ApiError as e:
            QMessageBox.warning(self, "Create Team", str(e))
            return
        self.refresh_teams()

    def join_team(self):
        code, ok = self._prompt_text("Request Access", "Join code")
        if not ok or not code:
            return
        try:
            res = api_join_team(code)
        except ApiError as e:
            QMessageBox.warning(self, "Join Team", str(e))
            return
        status = res.get("status") if isinstance(res, dict) else None
        if str(status or "").strip().lower() == "pending":
            QMessageBox.information(self, "Request Sent", "Your access request was sent. Wait for the team admin to accept it.")
            return
        self.refresh_teams()

    def invite_member(self):
        if not self.current_team_id:
            QMessageBox.information(self, "Invite Member", "Please select a team first.")
            return
        if not self._is_team_admin():
            QMessageBox.information(self, "Invite Member", "Only the team admin can invite members.")
            return
        existing_ids = set((self._members_by_id or {}).keys())
        _TeamInviteDialog(
            self,
            team_id=int(self.current_team_id),
            theme=self.current_theme,
            existing_member_ids=existing_ids,
            current_user_id=self._current_user_id(),
            on_members_changed=self.refresh_members,
        ).exec()

    def open_join_requests(self):
        if not self.current_team_id:
            QMessageBox.information(self, "Access Requests", "Please select a team first.")
            return
        if not self._is_team_admin():
            QMessageBox.information(self, "Access Requests", "Only the team admin can manage access requests.")
            return
        _TeamJoinRequestsDialog(
            self,
            team_id=int(self.current_team_id),
            theme=self.current_theme,
            on_members_changed=self.refresh_members,
        ).exec()

    def open_analytics(self):
        if not self.current_team_id:
            QMessageBox.information(self, "Analytics", "Please select a team first.")
            return
        try:
            res = api_team_analytics(int(self.current_team_id))
        except ApiError as e:
            QMessageBox.warning(self, "Analytics", str(e))
            return
        except Exception:
            return
        team_name = ""
        try:
            team_name = self.team_combo.currentText()
        except Exception:
            team_name = ""
        _TeamAnalyticsDialog(
            self,
            theme=self.current_theme,
            team_id=int(self.current_team_id),
            team_name=team_name,
            data=res if isinstance(res, dict) else {},
        ).exec()

    def open_history(self):
        if not self.current_team_id:
            QMessageBox.information(self, "History", "Please select a team first.")
            return
        try:
            _TeamHistoryDialog(self, theme=self.current_theme, team_name=self.team_combo.currentText(), team_id=int(self.current_team_id)).exec()
        except Exception as e:
            tb = traceback.format_exc()
            QMessageBox.warning(self, "History", f"Failed to open history dialog:\n{str(e)}")
            print("Failed to open history dialog:\n", tb)

    def add_task(self):
        if not self.current_team_id:
            QMessageBox.information(self, "Team Tasks", "Please select a team first.")
            return
        can_assign = self._can_assign_tasks()
        assignees = []
        if can_assign:
            assignees = sorted([(uid, name) for uid, name in (self._members_by_id or {}).items()], key=lambda p: p[1].lower())
        dlg = AddTaskDialog(
            self,
            "New Team Task",
            theme=self.current_theme,
            enable_recurrence=False,
            enable_assignment=can_assign,
            assignees=assignees if can_assign else None,
        )
        if dlg.exec():
            data = dlg.get_data()
            if not data["title"]:
                QMessageBox.warning(self, "Team Tasks", "Title is required.")
                return
            try:
                api_create_team_task(
                    self.current_team_id,
                    data["title"],
                    data.get("description", ""),
                    data.get("date"),
                    data.get("important"),
                    data.get("task_type"),
                    assigned_to=data.get("assigned_to") if can_assign else None,
                )
            except ApiError as e:
                QMessageBox.warning(self, "Team Tasks", str(e))
                return
            self.refresh_tasks()

    def set_task_completed(self, task_id, is_completed):
        if not self.current_team_id or not task_id:
            return
        try:
            api_update_team_task(self.current_team_id, task_id, is_completed=is_completed)
        except ApiError as e:
            QMessageBox.warning(self, "Team Tasks", str(e))
        self.refresh_tasks()

    def mark_task_completed(self, task_id, checked):
        self.set_task_completed(task_id, bool(checked))

    def delete_task(self, task_id):
        if not self.current_team_id or not task_id:
            return
        if not self._is_team_admin():
            QMessageBox.information(self, "Team Tasks", "Only the team admin can delete team tasks.")
            return
        try:
            api_delete_team_task(self.current_team_id, task_id)
        except ApiError as e:
            QMessageBox.warning(self, "Team Tasks", str(e))
        self.refresh_tasks()

    def edit_task(self, t_id):
        if not self._is_team_admin():
            QMessageBox.information(self, "Team Tasks", "Only the team admin can edit team tasks.")
            return
        task = self._tasks_cache.get(t_id)
        if not task:
            return
        assignees = sorted([(uid, name) for uid, name in (self._members_by_id or {}).items()], key=lambda p: p[1].lower())
        dlg = AddTaskDialog(
            self,
            "Edit Team Task",
            theme=self.current_theme,
            enable_recurrence=False,
            enable_assignment=True,
            assignees=assignees,
        )
        dlg.load_data(
            task.get("title") or "",
            task.get("description") or "",
            task.get("due_date") or "",
            bool(task.get("is_important")),
            task.get("task_type"),
            assigned_to=task.get("assigned_to"),
        )
        if dlg.exec():
            data = dlg.get_data()
            try:
                api_update_team_task(
                    self.current_team_id,
                    t_id,
                    title=data.get("title"),
                    description=data.get("description"),
                    due_date=data.get("date"),
                    is_important=data.get("important"),
                    task_type=data.get("task_type"),
                    assigned_to=data.get("assigned_to"),
                )
            except ApiError as e:
                QMessageBox.warning(self, "Team Tasks", str(e))
                return
            self.refresh_tasks()

    def show_task_details(self, t_id):
        task = self._tasks_cache.get(t_id)
        if not task:
            return
        due_pretty = self._pretty_due(task.get("due_date"))
        created_pretty = self._pretty_created(task.get("created_date"), task.get("created_at"))
        is_imp = bool(task.get("is_important"))
        is_urg = task.get("is_urgent")
        if is_urg is None:
            is_urg = self._deadline_is_urgent(task.get("due_date"))
        priority = task.get("priority") or quadrant_from_flags(is_urg, is_imp)
        task_type = normalize_task_type(task.get("task_type"))
        total_focus_min = 0
        total_sessions = 0
        sessions_widgets = []
        session_key = None
        if self.current_team_id:
            session_key = self._team_session_key(self.current_team_id, t_id)
        if session_key:
            conn = None
            try:
                conn = get_db_connection()
                total_row = conn.execute(
                    "SELECT COALESCE(SUM(duration_min), 0) FROM pomodoro_sessions WHERE task_id=? AND status='completed'",
                    (session_key,)
                ).fetchone()
                if total_row:
                    total_focus_min = int(total_row[0] or 0)

                total_sessions_row = conn.execute(
                    "SELECT COUNT(*) FROM pomodoro_sessions WHERE task_id=?",
                    (session_key,)
                ).fetchone()
                if total_sessions_row:
                    total_sessions = int(total_sessions_row[0] or 0)

                sess_rows = conn.execute(
                    "SELECT started_at, duration_min, status FROM pomodoro_sessions WHERE task_id=? ORDER BY started_at DESC",
                    (session_key,)
                ).fetchall()
                for started_at, duration_min, status in sess_rows:
                    display_time = "Unknown time"
                    if started_at:
                        raw = str(started_at).split(".")[0]
                        dt = None
                        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
                            try:
                                dt = datetime.strptime(raw, fmt)
                                break
                            except ValueError:
                                continue
                        if dt:
                            display_time = dt.strftime("%b %d, %H:%M")
                    dur = int(duration_min or 0)
                    st = str(status or "").lower()
                    status_text = st if st else "completed"
                    lbl = QLabel(f"{display_time}  -  {format_duration_minutes(dur)}  ({status_text})")
                    lbl.setStyleSheet("font-size: 11px; font-weight: 600;")
                    sessions_widgets.append(lbl)
            except Exception:
                sessions_widgets = []
            finally:
                if conn:
                    conn.close()
        if not self.current_team_id:
            return
        _TeamTaskDetailsDialog(
            self,
            team_id=int(self.current_team_id),
            task=task,
            priority=priority,
            task_type=task_type,
            due_pretty=due_pretty,
            created_pretty=created_pretty,
            total_focus_min=total_focus_min,
            total_sessions=total_sessions,
            sessions_widgets=sessions_widgets,
            theme=self.current_theme,
            members_by_id=self._members_by_id,
        ).exec()

    def start_pomodoro(self, t_id, title, priority=None, task_type=None):
        if not self.current_team_id:
            QMessageBox.information(self, "Team Focus", "Select a team before starting a team session.")
            return
        session_key = self._team_session_key(self.current_team_id, t_id)
        prio = normalize_priority(priority) or "too low"
        self.team_task_pomodoro_requested.emit(session_key, title, prio, normalize_task_type(task_type))

    def _build_task_card(self, task, focus_minutes=0):
        if not isinstance(task, dict):
            return None
        due_pretty = self._pretty_due(task.get("due_date"))
        created_pretty = self._pretty_created(task.get("created_date"), task.get("created_at"))
        is_imp = bool(task.get("is_important"))
        is_urg = task.get("is_urgent")
        if is_urg is None:
            is_urg = self._deadline_is_urgent(task.get("due_date"))
        priority = task.get("priority") or quadrant_from_flags(is_urg, is_imp)
        task_type = normalize_task_type(task.get("task_type"))
        assignee = None
        assigned_to = task.get("assigned_to")
        try:
            assigned_to_int = int(assigned_to) if assigned_to is not None else None
        except Exception:
            assigned_to_int = None
        if assigned_to_int is not None:
            assignee = (self._members_by_id or {}).get(assigned_to_int) or f"User {assigned_to_int}"
        extra_meta = f"Assigned to: {assignee}" if assignee else "Unassigned"
        card = TaskCard(
            task.get("id"),
            task.get("title") or "",
            task.get("description") or "",
            due_pretty,
            created_pretty,
            priority,
            focus_minutes,
            self,
            bool(task.get("is_completed")),
            task_type,
            extra_meta=extra_meta,
        )
        card.update_theme(self.current_theme)
        is_admin = self._is_team_admin()
        try:
            card.btn_edit.setVisible(bool(is_admin))
            card.btn_del.setVisible(bool(is_admin))
        except Exception:
            pass
        return card

    def _pretty_due(self, due_date_str):
        if not due_date_str:
            return "No Deadline"
        d = QDate.fromString(str(due_date_str)[:10], "yyyy-MM-dd")
        if d.isValid():
            return d.toString("dddd d MMMM yyyy")
        return str(due_date_str)

    def _pretty_created(self, created_date_str, created_at_str=None):
        raw = created_date_str or created_at_str
        if not raw:
            return "Unknown"
        raw = str(raw)
        date_part = raw[:10]
        d = QDate.fromString(date_part, "yyyy-MM-dd")
        if d.isValid():
            return d.toString("d MMM yyyy")
        return date_part

    def _deadline_is_urgent(self, due_date_str):
        if not due_date_str:
            return 0
        try:
            due = QDate.fromString(str(due_date_str)[:10], "yyyy-MM-dd")
        except Exception:
            return 0
        if not due.isValid():
            return 0
        today = QDate.currentDate()
        days_to = today.daysTo(due)
        return 1 if days_to <= 2 else 0

    def _team_session_key(self, team_id, task_id):
        return f"team:{team_id}:{task_id}"

    def _load_focus_minutes(self, tasks):
        if not self.current_team_id:
            return {}
        if not tasks:
            return {}
        keys = []
        task_ids = []
        for t in tasks:
            if not isinstance(t, dict):
                continue
            t_id = t.get("id")
            if t_id is None:
                continue
            keys.append(self._team_session_key(self.current_team_id, t_id))
            task_ids.append(t_id)
        if not keys:
            return {}

        conn = None
        rows = []
        try:
            conn = get_db_connection()
            placeholders = ",".join("?" for _ in keys)
            rows = conn.execute(
                f"SELECT task_id, COALESCE(SUM(duration_min), 0) "
                f"FROM pomodoro_sessions "
                f"WHERE task_id IN ({placeholders}) AND status IN ('completed', 'stopped') "
                f"GROUP BY task_id",
                tuple(keys),
            ).fetchall()
        except Exception:
            rows = []
        finally:
            if conn:
                conn.close()

        minutes_by_key = {row[0]: int(row[1] or 0) for row in rows}
        focus_map = {}
        for idx, t_id in enumerate(task_ids):
            key = keys[idx]
            focus_map[t_id] = minutes_by_key.get(key, 0)
        return focus_map

    def _prompt_text(self, title, placeholder):
        dlg = QDialog(self)
        dlg.setWindowTitle(title)
        layout = QVBoxLayout(dlg)
        layout.setContentsMargins(20, 20, 20, 20)
        label = QLabel(placeholder)
        input_box = QLineEdit()
        layout.addWidget(label)
        layout.addWidget(input_box)
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        btn_cancel = QPushButton("Cancel")
        btn_ok = QPushButton("OK")
        btn_cancel.clicked.connect(dlg.reject)
        btn_ok.clicked.connect(dlg.accept)
        btn_row.addWidget(btn_cancel)
        btn_row.addWidget(btn_ok)
        layout.addLayout(btn_row)
        ok = dlg.exec() == QDialog.DialogCode.Accepted
        return input_box.text().strip(), ok
