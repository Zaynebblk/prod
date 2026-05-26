from PyQt6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QFrame,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QGridLayout
)
from PyQt6.QtCore import Qt, pyqtSignal, QRectF, QPointF
from PyQt6.QtGui import QColor, QPainter, QPainterPath, QPen
from datetime import datetime, timedelta
from database.db_manager import get_db_connection
from resources.theme import get_theme
from resources.time_format import format_duration_minutes


class EnergyTrendWidget(QFrame):
    def __init__(self):
        super().__init__()
        self.values = [0.7, 0.85, 0.95, 1.0, 0.9, 0.55, 0.7, 0.92, 0.88, 0.6]
        self.colors = {}
        self.setMinimumHeight(120)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)

    def set_theme(self, colors):
        self.colors = colors or {}
        self.update()

    def set_data(self, values):
        if values:
            self.values = list(values)
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        try:
            rect = QRectF(self.rect())
            left_pad = 6
            right_pad = 6
            top_pad = 6
            bottom_pad = 10
            chart_rect = QRectF(
                rect.left() + left_pad,
                rect.top() + top_pad,
                rect.width() - left_pad - right_pad,
                rect.height() - top_pad - bottom_pad
            )

            values = self.values or []
            if not values:
                return

            count = len(values)
            gap = 8
            bar_w = (chart_rect.width() - gap * (count - 1)) / max(count, 1)
            bar_w = max(6, bar_w)

            primary = QColor(self.colors.get("primary", "#11a4d4"))
            soft = QColor(primary)
            soft.setAlpha(90)

            for idx, value in enumerate(values):
                val = max(0.0, min(value, 1.0))
                h = val * chart_rect.height()
                x = chart_rect.left() + idx * (bar_w + gap)
                y = chart_rect.bottom() - h
                bar_rect = QRectF(x, y, bar_w, h)
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(soft if idx < count - 1 else primary)
                painter.drawRoundedRect(bar_rect, 6, 6)
        finally:
            painter.end()


class QuickStatsPage(QWidget):
    request_history = pyqtSignal()

    def __init__(self):
        super().__init__()
        self.current_theme = "Light"
        self.colors = {}
        self.setObjectName("QuickStatsPage")

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setStyleSheet("border: none; background: transparent;")
        root.addWidget(self.scroll)

        self.container = QWidget()
        self.content = QVBoxLayout(self.container)
        self.content.setContentsMargins(18, 16, 18, 24)
        self.content.setSpacing(16)
        self.scroll.setWidget(self.container)

        # Header
        self.header = QFrame()
        self.header.setObjectName("QuickHeader")
        header_layout = QHBoxLayout(self.header)
        header_layout.setContentsMargins(6, 6, 6, 6)
        header_layout.setSpacing(10)

        self.back_btn = QPushButton("<")
        self.back_btn.setObjectName("QuickBackButton")
        self.back_btn.setFixedSize(36, 36)
        self.back_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.back_btn.clicked.connect(self.request_history.emit)

        self.header_title = QLabel("Quick Stats")
        self.header_title.setObjectName("QuickTitle")

        header_layout.addWidget(self.back_btn)
        header_layout.addStretch()
        header_layout.addWidget(self.header_title)
        header_layout.addStretch()
        self.content.addWidget(self.header)

        # Title block
        self.kicker = QLabel("Quick Stats")
        self.kicker.setAlignment(Qt.AlignmentFlag.AlignLeft)
        self.session_title = QLabel("Session")
        self.session_subtitle = QLabel("Session details")
        self.content.addWidget(self.kicker)
        self.content.addWidget(self.session_title)
        self.content.addWidget(self.session_subtitle)

        # Metrics grid
        self.metrics_container = QWidget()
        self.metrics_grid = QGridLayout(self.metrics_container)
        self.metrics_grid.setHorizontalSpacing(10)
        self.metrics_grid.setVerticalSpacing(10)
        self.metric_cards = []
        for idx, title in enumerate(("Session Duration", "Session Status", "Total Focus", "Sessions")):
            card = QFrame()
            card.setObjectName("QuickMetricCard")
            card.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
            card_layout = QVBoxLayout(card)
            card_layout.setContentsMargins(12, 12, 12, 12)
            card_layout.setSpacing(6)
            title_lbl = QLabel(title)
            value_lbl = QLabel("0")
            delta_lbl = QLabel("+0%")
            card_layout.addWidget(title_lbl)
            card_layout.addWidget(value_lbl)
            card_layout.addWidget(delta_lbl)
            row = idx // 2
            col = idx % 2
            self.metrics_grid.addWidget(card, row, col)
            self.metric_cards.append({
                "card": card,
                "title": title_lbl,
                "value": value_lbl,
                "delta": delta_lbl
            })
        self.content.addWidget(self.metrics_container)

        # Recent sessions
        self.energy_title = QLabel("Recent Sessions")
        self.energy_sub = QLabel("Last 10 sessions (duration)")
        self.content.addWidget(self.energy_title)
        self.content.addWidget(self.energy_sub)
        self.energy_chart = EnergyTrendWidget()
        self.content.addWidget(self.energy_chart)

        self.trend_labels = QHBoxLayout()
        self.trend_start = QLabel("Oldest")
        self.trend_mid1 = QLabel("")
        self.trend_mid2 = QLabel("")
        self.trend_end = QLabel("Latest")
        self.trend_labels.addWidget(self.trend_start)
        self.trend_labels.addStretch()
        self.trend_labels.addWidget(self.trend_mid1)
        self.trend_labels.addStretch()
        self.trend_labels.addWidget(self.trend_mid2)
        self.trend_labels.addStretch()
        self.trend_labels.addWidget(self.trend_end)
        self.content.addLayout(self.trend_labels)

        # Details
        self.details_container = QWidget()
        self.details_layout = QVBoxLayout(self.details_container)
        self.details_layout.setContentsMargins(0, 0, 0, 0)
        self.details_layout.setSpacing(0)
        self.detail_rows = []
        for title in ("Start Time", "Duration", "Task Type"):
            row = QFrame()
            row.setObjectName("QuickDetailRow")
            row_layout = QHBoxLayout(row)
            row_layout.setContentsMargins(6, 8, 6, 8)
            row_layout.setSpacing(8)
            left = QLabel(title)
            right = QLabel("--")
            row_layout.addWidget(left)
            row_layout.addStretch()
            row_layout.addWidget(right)
            self.details_layout.addWidget(row)
            self.detail_rows.append({
                "row": row,
                "left": left,
                "right": right
            })
        self.content.addWidget(self.details_container)

        # Done button
        self.done_btn = QPushButton("Done")
        self.done_btn.setObjectName("QuickDoneButton")
        self.done_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.done_btn.clicked.connect(self.request_history.emit)
        self.content.addWidget(self.done_btn)

        self.content.addStretch()
        self.update_theme("Light")

    def update_theme(self, theme):
        self.current_theme = theme
        self.colors = get_theme(theme)

        self.apply_styles()

    def apply_styles(self):
        c = self.colors
        self.setStyleSheet(f"QWidget#QuickStatsPage {{ background: {c['bg']}; }}")
        self.header.setStyleSheet(
            f"QFrame#QuickHeader {{ background: {c['bg']}; border-bottom: 1px solid {c['primary_soft_border']}; }}"
        )
        self.back_btn.setStyleSheet(
            f"QPushButton#QuickBackButton {{ background-color: {c['card']}; border: 1px solid {c['border']}; border-radius: 18px; color: {c['text']}; font-weight: bold; }}"
        )
        self.header_title.setStyleSheet(
            f"color: {c['text']}; font-size: 16px; font-weight: 800;"
        )
        self.kicker.setStyleSheet(
            f"color: {c['primary']}; font-size: 10px; font-weight: 900;"
        )
        self.session_title.setStyleSheet(
            f"color: {c['text']}; font-size: 18px; font-weight: 900;"
        )
        self.session_subtitle.setStyleSheet(
            f"color: {c['sub']}; font-size: 11px; font-weight: 600;"
        )

        for item in self.metric_cards:
            item["card"].setStyleSheet(
                f"QFrame#QuickMetricCard {{ background: {c['primary_soft']}; border: 1px solid {c['primary_soft_border']}; border-radius: 12px; }}"
            )
            item["title"].setStyleSheet(
                f"color: {c['sub']}; font-size: 9px; font-weight: 700;"
            )
            item["value"].setStyleSheet(
                f"color: {c['text']}; font-size: 18px; font-weight: 900;"
            )
            item["delta"].setStyleSheet(
                f"color: {c['good']}; font-size: 9px; font-weight: 700;"
            )

        self.energy_title.setStyleSheet(
            f"color: {c['text']}; font-size: 14px; font-weight: 800;"
        )
        self.energy_sub.setStyleSheet(
            f"color: {c['sub']}; font-size: 10px; font-weight: 600;"
        )
        for lbl in (self.trend_start, self.trend_mid1, self.trend_mid2, self.trend_end):
            lbl.setStyleSheet(
                f"color: {c['sub']}; font-size: 9px; font-weight: 800;"
            )

        for item in self.detail_rows:
            item["row"].setStyleSheet(
                f"QFrame#QuickDetailRow {{ border-bottom: 1px solid {c['primary_soft_border']}; }}"
            )
            item["left"].setStyleSheet(
                f"color: {c['sub']}; font-size: 10px; font-weight: 700;"
            )
            item["right"].setStyleSheet(
                f"color: {c['text']}; font-size: 10px; font-weight: 700;"
            )

        self.done_btn.setStyleSheet(
            f"QPushButton#QuickDoneButton {{ background-color: {c['primary']}; color: white; border: none; border-radius: 12px; padding: 10px; font-size: 12px; font-weight: bold; }}"
        )
        self.energy_chart.set_theme(c)

    def _parse_dt(self, value):
        if not value:
            return None
        raw = str(value)
        for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S"):
            try:
                return datetime.strptime(raw, fmt)
            except Exception:
                continue
        try:
            return datetime.fromisoformat(raw)
        except Exception:
            return None

    def _format_minutes(self, minutes):
        return format_duration_minutes(minutes)

    def load_activity(self, activity_id):
        if not activity_id:
            return

        conn = get_db_connection()
        session = None
        task = None

        if activity_id.startswith("session:"):
            try:
                session_id = int(activity_id.split(":", 1)[1])
            except Exception:
                session_id = None
            if session_id is not None:
                session = conn.execute(
                    "SELECT id, task_id, task_title, started_at, ended_at, duration_min, status FROM pomodoro_sessions WHERE id = ?",
                    (session_id,)
                ).fetchone()
                if session and session["task_id"]:
                    task = conn.execute(
                        "SELECT id, title, is_completed, completed_at FROM tasks WHERE id = ?",
                        (session["task_id"],)
                    ).fetchone()
        elif activity_id.startswith("task:"):
            try:
                task_id = int(activity_id.split(":", 1)[1])
            except Exception:
                task_id = None
            if task_id is not None:
                task = conn.execute(
                    "SELECT id, title, is_completed, completed_at FROM tasks WHERE id = ?",
                    (task_id,)
                ).fetchone()
                session = conn.execute(
                    "SELECT id, task_id, task_title, started_at, ended_at, duration_min, status FROM pomodoro_sessions WHERE task_id = ? ORDER BY started_at DESC",
                    (task_id,)
                ).fetchone()

        task_id = None
        has_session = False
        if session:
            has_session = True
            dt_start = self._parse_dt(session["started_at"])
            duration = int(session["duration_min"] or 0)
            if session["ended_at"]:
                dt_end = self._parse_dt(session["ended_at"])
            else:
                dt_end = dt_start + timedelta(minutes=duration) if dt_start else None
            title = session["task_title"] or (task["title"] if task else "Focus Session")
            status_raw = str(session["status"] or "").strip().lower()
            if status_raw == "completed":
                status = "Completed"
            elif status_raw == "stopped":
                status = "Stopped"
            elif status_raw:
                status = status_raw.title()
            else:
                status = "Focus Session"
            task_id = session["task_id"]
        elif task:
            dt_start = self._parse_dt(task["completed_at"])
            duration = 0
            dt_end = None
            title = task["title"]
            status = "Completed" if task["is_completed"] else "Task"
            task_id = task["id"]
        else:
            dt_start = datetime.now()
            duration = 0
            dt_end = None
            title = "Session"
            status = "Session"

        date_label = dt_start.strftime("%b %d") if dt_start else "Recent"
        self.session_title.setText(title)
        self.session_subtitle.setText(f"{status} - {date_label}")

        total_sessions = 0
        total_minutes = 0
        recent_durations = []
        if task_id is not None:
            summary_row = conn.execute(
                "SELECT COUNT(*), COALESCE(SUM(duration_min), 0) "
                "FROM pomodoro_sessions "
                "WHERE task_id = ? AND status IN ('completed', 'stopped')",
                (task_id,)
            ).fetchone()
            if summary_row:
                total_sessions = int(summary_row[0] or 0)
                total_minutes = int(summary_row[1] or 0)
            recent_rows = conn.execute(
                "SELECT duration_min FROM pomodoro_sessions "
                "WHERE task_id = ? AND status IN ('completed', 'stopped') "
                "ORDER BY started_at DESC LIMIT 10",
                (task_id,)
            ).fetchall()
            recent_durations = [int(row[0] or 0) for row in reversed(recent_rows)]
        elif duration > 0:
            total_sessions = 1
            total_minutes = duration
            recent_durations = [duration]

        metrics = [
            (self._format_minutes(duration), "This session"),
            (status, "Logged status"),
            (self._format_minutes(total_minutes), "All sessions"),
            (str(total_sessions), "For this task")
        ]
        for idx, item in enumerate(self.metric_cards):
            value, delta = metrics[idx]
            item["value"].setText(value)
            item["delta"].setText(delta)

        start_time = dt_start.strftime("%I:%M %p").lstrip("0") if dt_start else "--"
        duration_text = format_duration_minutes(duration) if duration else "--"
        if has_session:
            task_type = "Deep Work"
        else:
            task_type = "Completed" if task and task["is_completed"] else "Task"
        self.detail_rows[0]["right"].setText(start_time)
        self.detail_rows[1]["right"].setText(duration_text)
        self.detail_rows[2]["right"].setText(task_type)
        if recent_durations:
            max_val = max(recent_durations) if recent_durations else 0
            if max_val > 0:
                chart_values = [d / max_val for d in recent_durations]
            else:
                chart_values = [0.0 for _ in recent_durations]
            self.energy_chart.set_data(chart_values)
            min_d = min(recent_durations)
            max_d = max_val
            avg_d = int(round(sum(recent_durations) / len(recent_durations)))
            self.energy_sub.setText(
                "Last %s sessions | Min %s | Avg %s | Max %s" % (
                    len(recent_durations),
                    format_duration_minutes(min_d),
                    format_duration_minutes(avg_d),
                    format_duration_minutes(max_d),
                )
            )
            if len(recent_durations) > 1:
                self.trend_start.setText("Oldest")
                self.trend_end.setText("Latest")
            else:
                self.trend_start.setText("Session")
                self.trend_end.setText("")
        else:
            self.energy_chart.set_data([])
            self.energy_sub.setText("No session history yet")
            self.trend_start.setText("")
            self.trend_end.setText("")
        self.trend_mid1.setText("")
        self.trend_mid2.setText("")
        # Trend labels are handled above based on session history
        conn.close()
