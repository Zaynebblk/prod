from PyQt6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QFrame,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QButtonGroup
)
from PyQt6.QtCore import Qt, pyqtSignal, QRectF, QPointF
from PyQt6.QtGui import QColor, QPainter, QPen
from datetime import datetime, timedelta
from database.db_manager import get_db_connection
from resources.theme import get_theme
from resources.priority import normalize_priority, PRIORITY_LEVELS
from resources.time_format import format_duration_minutes


class FocusRingWidget(QFrame):
    def __init__(self):
        super().__init__()
        self.percent = 0
        self.label = "Deep"
        self.colors = {}
        self.setMinimumSize(120, 120)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)

    def set_data(self, percent, label):
        self.percent = max(0, min(int(percent), 100))
        self.label = label
        self.update()

    def set_theme(self, colors):
        self.colors = colors or {}
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        try:
            rect = QRectF(self.rect())
            size = min(rect.width(), rect.height()) - 8
            x = rect.center().x() - size / 2
            y = rect.center().y() - size / 2
            ring_rect = QRectF(x, y, size, size)

            base_color = QColor(self.colors.get("primary_soft_border", "#2f7c98"))
            arc_color = QColor(self.colors.get("primary", "#11a4d4"))

            pen_bg = QPen(base_color, 6)
            pen_bg.setCapStyle(Qt.PenCapStyle.RoundCap)
            painter.setPen(pen_bg)
            painter.drawArc(ring_rect, 0, 360 * 16)

            pen_fg = QPen(arc_color, 6)
            pen_fg.setCapStyle(Qt.PenCapStyle.RoundCap)
            painter.setPen(pen_fg)
            span = int(-self.percent * 360 / 100 * 16)
            painter.drawArc(ring_rect, 90 * 16, span)

            painter.setPen(QColor(self.colors.get("text", "#f1f5f9")))
            font = painter.font()
            font.setPointSize(12)
            font.setBold(True)
            painter.setFont(font)
            painter.drawText(ring_rect, Qt.AlignmentFlag.AlignCenter, f"{self.percent}%")

            label_rect = QRectF(ring_rect.left(), ring_rect.center().y() + 12, ring_rect.width(), 18)
            painter.setPen(QColor(self.colors.get("sub", "#94a3b8")))
            font.setPointSize(8)
            font.setBold(True)
            painter.setFont(font)
            painter.drawText(label_rect, Qt.AlignmentFlag.AlignCenter, self.label)
        finally:
            painter.end()


class CompletionLadderWidget(QFrame):
    def __init__(self):
        super().__init__()
        self.rows = []
        self.colors = {}
        self.setMinimumHeight(140)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)

    def set_data(self, values):
        if values is not None:
            self.rows = list(values)
        self.update()

    def set_theme(self, colors):
        self.colors = colors or {}
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        try:
            rect = QRectF(self.rect())
            left_pad = 90
            right_pad = 16
            top_pad = 10
            bottom_pad = 10

            label_color = QColor(self.colors.get("text", "#0B132B"))
            sub_color = QColor(self.colors.get("sub", "#94A3B8"))

            if not self.rows:
                painter.setPen(sub_color)
                painter.drawText(QPointF(rect.left() + 10, rect.center().y()), "No tasks yet.")
                return

            color_map = {
                "high": QColor(self.colors.get("bad", "#ef4444")),
                "medium": QColor(self.colors.get("primary", "#11a4d4")),
                "low": QColor(self.colors.get("accent2", "#82AFF2")),
                "too low": QColor(self.colors.get("border", "#94a3b8")),
            }

            label_font = painter.font()
            label_font.setPointSize(9)
            label_font.setBold(True)

            value_font = painter.font()
            value_font.setPointSize(10)
            value_font.setBold(True)

            painter.setFont(value_font)
            value_texts = []
            for row in self.rows:
                completed = int(row.get("completed", 0) or 0)
                total = int(row.get("total", 0) or 0)
                value_texts.append(f"{completed}/{total}")
            value_metrics = painter.fontMetrics()
            max_value_w = 0
            for text in value_texts:
                max_value_w = max(max_value_w, value_metrics.horizontalAdvance(text))
            value_col_w = max(28, max_value_w + 6)
            value_gap = 8

            chart_w = rect.width() - left_pad - right_pad - value_col_w - value_gap
            if chart_w < 20:
                chart_w = 20
            chart_rect = QRectF(
                rect.left() + left_pad,
                rect.top() + top_pad,
                chart_w,
                rect.height() - top_pad - bottom_pad
            )

            row_gap = 12
            step_h = 12
            step_w = 10
            step_gap = 4
            max_steps = max(5, int(chart_rect.width() // (step_w + step_gap)))

            for idx, row in enumerate(self.rows):
                label = row.get("label", "")
                level = row.get("level", "too low")
                completed = int(row.get("completed", 0) or 0)
                total = int(row.get("total", 0) or 0)
                y = rect.top() + top_pad + idx * (step_h + row_gap)

                painter.setPen(label_color)
                painter.setFont(label_font)
                painter.drawText(QPointF(rect.left() + 8, y + step_h), label)

                steps = max(1, min(total, max_steps)) if total > 0 else max_steps
                done_steps = int(round((completed / total) * steps)) if total > 0 else 0

                for i in range(steps):
                    x = chart_rect.left() + i * (step_w + step_gap)
                    step_rect = QRectF(x, y, step_w, step_h)
                    if i < done_steps:
                        painter.setBrush(color_map.get(level, sub_color))
                    else:
                        painter.setBrush(QColor(self.colors.get("primary_soft_border", "#cbd5e1")))
                    painter.setPen(Qt.PenStyle.NoPen)
                    painter.drawRoundedRect(step_rect, 3, 3)

                value_text = value_texts[idx] if idx < len(value_texts) else f"{completed}/{total}"
                value_x = rect.right() - right_pad - value_col_w
                value_rect = QRectF(value_x, y - 1, value_col_w, step_h + 2)
                painter.setPen(label_color)
                painter.setFont(value_font)
                painter.drawText(value_rect, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, value_text)
        finally:
            painter.end()


class SessionReportPage(QWidget):
    request_history = pyqtSignal()

    def __init__(self):
        super().__init__()
        self.current_theme = "Light"
        self.colors = {}
        self.current_period = "Today"
        self._delta_values = {"high": 0, "comp": 0, "sess": 0}
        self.setObjectName("ReportPage")

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
        self.header.setObjectName("ReportHeader")
        header_layout = QHBoxLayout(self.header)
        header_layout.setContentsMargins(6, 6, 6, 6)
        header_layout.setSpacing(10)

        self.back_btn = QPushButton("<")
        self.back_btn.setObjectName("ReportBackButton")
        self.back_btn.setFixedSize(36, 36)
        self.back_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.back_btn.clicked.connect(self.request_history.emit)

        self.header_title = QLabel("Session Report")
        self.header_title.setObjectName("ReportTitle")

        header_layout.addWidget(self.back_btn)
        header_layout.addStretch()
        header_layout.addWidget(self.header_title)
        header_layout.addStretch()

        self.content.addWidget(self.header)

        # Date & time
        self.date_label = QLabel("Date")
        self.time_label = QLabel("Time")
        self.date_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.time_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.content.addWidget(self.date_label)
        self.content.addWidget(self.time_label)

        # Period selector
        self.period_frame = QFrame()
        self.period_frame.setObjectName("ReportPeriod")
        period_layout = QHBoxLayout(self.period_frame)
        period_layout.setContentsMargins(4, 4, 4, 4)
        period_layout.setSpacing(4)

        self.period_group = QButtonGroup(self)
        self.period_today = QPushButton("Today")
        self.period_week = QPushButton("This Week")
        self.period_month = QPushButton("This Month")
        for btn in (self.period_today, self.period_week, self.period_month):
            btn.setCheckable(True)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            self.period_group.addButton(btn)
            period_layout.addWidget(btn)
        self.period_today.setChecked(True)
        self.period_group.buttonClicked.connect(self._on_period_changed)
        self.content.addWidget(self.period_frame)

        # Summary cards
        self.summary_container = QWidget()
        self.summary_layout = QHBoxLayout(self.summary_container)
        self.summary_layout.setContentsMargins(0, 0, 0, 0)
        self.summary_layout.setSpacing(12)
        self.summary_cards = []
        for title in ("High Priority Focus", "Completion Rate", "Sessions"):
            card = QFrame()
            card.setObjectName("ReportSummaryCard")
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
            self.summary_layout.addWidget(card)
            self.summary_cards.append({
                "card": card,
                "title": title_lbl,
                "value": value_lbl,
                "delta": delta_lbl
            })
        self.content.addWidget(self.summary_container)

        # Focus breakdown
        self.breakdown_title = QLabel("Priority Breakdown")
        self.content.addWidget(self.breakdown_title)

        self.breakdown_frame = QFrame()
        self.breakdown_frame.setObjectName("ReportBreakdown")
        breakdown_layout = QHBoxLayout(self.breakdown_frame)
        breakdown_layout.setContentsMargins(16, 14, 16, 14)
        breakdown_layout.setSpacing(16)

        self.ring = FocusRingWidget()
        breakdown_layout.addWidget(self.ring)

        breakdown_right = QVBoxLayout()
        breakdown_right.setSpacing(10)
        self.deep_row = QLabel("Deep Focus")
        self.deep_value = QLabel("0 min")
        self.minor_row = QLabel("Minor Distractions")
        self.minor_value = QLabel("0 min")
        self.breakdown_note = QLabel(" ")
        self.breakdown_note.setWordWrap(True)

        breakdown_right.addWidget(self.deep_row)
        breakdown_right.addWidget(self.deep_value)
        breakdown_right.addWidget(self.minor_row)
        breakdown_right.addWidget(self.minor_value)
        breakdown_right.addWidget(self.breakdown_note)
        breakdown_layout.addLayout(breakdown_right)

        self.content.addWidget(self.breakdown_frame)

        # Timeline
        self.timeline_title = QLabel("Task Completion Ladder")
        self.content.addWidget(self.timeline_title)

        self.timeline_frame = QFrame()
        self.timeline_frame.setObjectName("ReportTimeline")
        timeline_layout = QVBoxLayout(self.timeline_frame)
        timeline_layout.setContentsMargins(12, 10, 12, 10)
        timeline_layout.setSpacing(8)
        self.timeline_chart = CompletionLadderWidget()
        timeline_layout.addWidget(self.timeline_chart)
        self.content.addWidget(self.timeline_frame)

        # Tasks
        self.tasks_title = QLabel("Tasks Completed")
        self.content.addWidget(self.tasks_title)
        self.tasks_container = QWidget()
        self.tasks_layout = QVBoxLayout(self.tasks_container)
        self.tasks_layout.setContentsMargins(0, 0, 0, 0)
        self.tasks_layout.setSpacing(10)
        self.content.addWidget(self.tasks_container)

        # Back to history
        self.back_action = QPushButton("Back to History")
        self.back_action.setObjectName("ReportPrimaryButton")
        self.back_action.setCursor(Qt.CursorShape.PointingHandCursor)
        self.back_action.clicked.connect(self.request_history.emit)
        self.content.addWidget(self.back_action)

        self.content.addStretch()

        self.update_theme("Light")

    def update_theme(self, theme):
        self.current_theme = theme
        self.colors = get_theme(theme)

        self.apply_styles()

    def apply_styles(self):
        c = self.colors
        self.setStyleSheet(f"QWidget#ReportPage {{ background: {c['bg']}; }}")
        self.header.setStyleSheet(
            f"QFrame#ReportHeader {{ background: {c['bg']}; border-bottom: 1px solid {c['primary_soft_border']}; }}"
        )
        self.back_btn.setStyleSheet(
            f"QPushButton#ReportBackButton {{ background-color: {c['card']}; border: 1px solid {c['border']}; border-radius: 18px; color: {c['text']}; font-weight: bold; }}"
        )
        self.header_title.setStyleSheet(
            f"color: {c['text']}; font-size: 16px; font-weight: 800;"
        )

        self.date_label.setStyleSheet(
            f"color: {c['primary']}; font-size: 12px; font-weight: 900;"
        )
        self.time_label.setStyleSheet(
            f"color: {c['sub']}; font-size: 11px; font-weight: 700;"
        )

        self.period_frame.setStyleSheet(
            f"QFrame#ReportPeriod {{ background: {c['primary_soft']}; border: 1px solid {c['primary_soft_border']}; border-radius: 10px; }}"
            "QPushButton { border: none; padding: 6px 10px; border-radius: 8px; font-size: 10px; font-weight: bold; }"
            f"QPushButton:checked {{ background-color: {c['card']}; color: {c['primary']}; }}"
            f"QPushButton:unchecked {{ color: {c['sub']}; }}"
        )

        for item in self.summary_cards:
            item["card"].setStyleSheet(
                f"QFrame#ReportSummaryCard {{ background: {c['primary_soft']}; border: 1px solid {c['primary_soft_border']}; border-radius: 14px; }}"
            )
            item["title"].setStyleSheet(
                f"color: {c['sub']}; font-size: 10px; font-weight: 700;"
            )
            item["value"].setStyleSheet(
                f"color: {c['primary']}; font-size: 20px; font-weight: 900;"
            )
            item["delta"].setStyleSheet(
                f"color: {c['sub']}; font-size: 10px; font-weight: 800;"
            )

        self.breakdown_title.setStyleSheet(
            f"color: {c['text']}; font-size: 16px; font-weight: 800;"
        )
        self.breakdown_frame.setStyleSheet(
            f"QFrame#ReportBreakdown {{ background: {c['primary_soft']}; border: 1px solid {c['primary_soft_border']}; border-radius: 16px; }}"
        )
        self.deep_row.setStyleSheet(
            f"color: {c['text']}; font-size: 12px; font-weight: 700;"
        )
        self.deep_value.setStyleSheet(
            f"color: {c['text']}; font-size: 12px; font-weight: 900;"
        )
        self.minor_row.setStyleSheet(
            f"color: {c['text']}; font-size: 12px; font-weight: 700;"
        )
        self.minor_value.setStyleSheet(
            f"color: {c['text']}; font-size: 12px; font-weight: 900;"
        )
        self.breakdown_note.setStyleSheet(
            f"color: {c['sub']}; font-size: 10px; font-weight: 600;"
        )
        self.ring.set_theme(c)

        self.timeline_title.setStyleSheet(
            f"color: {c['text']}; font-size: 16px; font-weight: 800;"
        )
        self.timeline_frame.setStyleSheet(
            f"QFrame#ReportTimeline {{ background: {c['primary_soft']}; border: 1px solid {c['primary_soft_border']}; border-radius: 14px; }}"
        )
        self.timeline_chart.set_theme(c)
        # Completion ladder uses in-row labels only

        self.tasks_title.setStyleSheet(
            f"color: {c['text']}; font-size: 16px; font-weight: 800;"
        )

        self.back_action.setStyleSheet(
            f"QPushButton#ReportPrimaryButton {{ background-color: {c['primary']}; color: white; border: none; border-radius: 14px; padding: 10px; font-size: 12px; font-weight: bold; }}"
        )
        self._apply_delta_styles()

    def _apply_delta_styles(self):
        if not hasattr(self, "summary_cards"):
            return
        c = self.colors or {}
        deltas = self._delta_values or {}

        def _style(label, value):
            if value < 0:
                color = c.get("bad", "#EF4444")
            elif value > 0:
                color = c.get("good", "#22C55E")
            else:
                color = c.get("sub", "#94A3B8")
            label.setStyleSheet(f"color: {color}; font-size: 10px; font-weight: 800;")

        _style(self.summary_cards[0]["delta"], deltas.get("high", 0))
        _style(self.summary_cards[1]["delta"], deltas.get("comp", 0))
        _style(self.summary_cards[2]["delta"], deltas.get("sess", 0))

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

    def _format_time(self, dt):
        return dt.strftime("%I:%M %p").lstrip("0") if dt else ""

    def _period_bounds(self):
        today = datetime.now().date()
        if self.current_period == "Week":
            start = today - timedelta(days=6)
            end = today
        elif self.current_period == "Month":
            start = today - timedelta(days=29)
            end = today
        else:
            start = today
            end = today
        return start, end

    def _on_period_changed(self, button):
        label = button.text().strip() if button else "Today"
        if label == "This Week":
            self.current_period = "Week"
        elif label == "This Month":
            self.current_period = "Month"
        elif label == "Today":
            self.current_period = "Today"
        else:
            return
        self._refresh_report()

    


    def load_report(self, activity_id=None):
        self._refresh_report()

    def _refresh_report(self):
        start_date, end_date = self._period_bounds()
        period_days = (end_date - start_date).days + 1
        prev_end = start_date - timedelta(days=1)
        prev_start = prev_end - timedelta(days=period_days - 1)
        start_dt = datetime.combine(start_date, datetime.min.time())
        end_dt = datetime.combine(end_date, datetime.max.time())
        start_str = start_dt.strftime("%Y-%m-%d %H:%M:%S")
        end_str = end_dt.strftime("%Y-%m-%d %H:%M:%S")

        conn = get_db_connection()
        def _load_period(period_start, period_end):
            p_start = datetime.combine(period_start, datetime.min.time())
            p_end = datetime.combine(period_end, datetime.max.time())
            p_start_str = p_start.strftime("%Y-%m-%d %H:%M:%S")
            p_end_str = p_end.strftime("%Y-%m-%d %H:%M:%S")
            try:
                sessions = conn.execute(
                    "SELECT id, task_id, task_title, task_priority, started_at, ended_at, duration_min, status "
                    "FROM pomodoro_sessions WHERE started_at >= ? AND started_at <= ?",
                    (p_start_str, p_end_str)
                ).fetchall()
            except Exception:
                sessions = []
            try:
                tasks = conn.execute(
                    "SELECT id, title, priority, is_completed, completed_at FROM tasks "
                    "WHERE completed_at >= ? AND completed_at <= ?",
                    (p_start_str, p_end_str)
                ).fetchall()
            except Exception:
                tasks = []
            try:
                session_task_ids = {row["task_id"] for row in sessions if row["task_id"] is not None}
                task_ids = {row["id"] for row in tasks}
                missing = list(session_task_ids - task_ids)
                if missing:
                    placeholders = ",".join(["?"] * len(missing))
                    extra = conn.execute(
                        f"SELECT id, title, priority, is_completed, completed_at FROM tasks WHERE id IN ({placeholders})",
                        tuple(missing)
                    ).fetchall()
                    tasks = list(tasks) + list(extra)
            except Exception:
                tasks = list(tasks)
            return list(sessions), list(tasks)

        def _build_metrics(sessions, tasks, period_start, period_end):
            tasks_by_id = {}
            for row in tasks:
                prio = normalize_priority(row["priority"])
                if prio not in PRIORITY_LEVELS:
                    prio = None
                tasks_by_id[row["id"]] = prio

            sessions_filtered = []
            total_minutes = 0
            sessions_count = 0
            priority_minutes = {level: 0 for level in PRIORITY_LEVELS}
            for row in sessions:
                status = str(row["status"] or "").strip().lower()
                if status and status not in ("completed", "stopped"):
                    continue
                sessions_filtered.append(row)
                mins = int(row["duration_min"] or 0)
                total_minutes += mins
                sessions_count += 1
                prio = normalize_priority(row["task_priority"])
                if prio not in PRIORITY_LEVELS:
                    prio = tasks_by_id.get(row["task_id"])
                if prio not in PRIORITY_LEVELS:
                    prio = "too low"
                priority_minutes[prio] += mins

            completed_map = {}
            for row in tasks:
                if not row["is_completed"]:
                    continue
                dt_completed = self._parse_dt(row["completed_at"])
                if not dt_completed:
                    continue
                if period_start <= dt_completed.date() <= period_end:
                    completed_map[row["id"]] = row

            minutes_by_task = {}
            for row in sessions_filtered:
                t_id = row["task_id"]
                t_title = row["task_title"] or "Focus Session"
                mins = int(row["duration_min"] or 0)
                key = (t_id, t_title)
                minutes_by_task[key] = minutes_by_task.get(key, 0) + mins

            tasks_list = []
            for (t_id, t_title), mins in minutes_by_task.items():
                is_done = t_id in completed_map if t_id is not None else False
                completed_at = completed_map[t_id]["completed_at"] if is_done else None
                tasks_list.append({
                    "title": t_title,
                    "minutes": mins,
                    "done": is_done,
                    "completed_at": completed_at,
                    "priority": tasks_by_id.get(t_id)
                })

            for row in tasks:
                key = (row["id"], row["title"])
                if key not in minutes_by_task:
                    is_done = row["id"] in completed_map
                    completed_at = completed_map[row["id"]]["completed_at"] if is_done else None
                    tasks_list.append({
                        "title": row["title"],
                        "minutes": 0,
                        "done": is_done,
                        "completed_at": completed_at,
                        "priority": tasks_by_id.get(row["id"])
                    })

            total_tasks = len(tasks_list)
            completed_tasks = sum(1 for item in tasks_list if item["done"])
            completion_rate = int(round((completed_tasks / total_tasks) * 100)) if total_tasks else 0

            return {
                "sessions": sessions_filtered,
                "tasks_list": tasks_list,
                "total_minutes": total_minutes,
                "sessions_count": sessions_count,
                "priority_minutes": priority_minutes,
                "completion_rate": completion_rate,
                "completed_tasks": completed_tasks,
                "total_tasks": total_tasks
            }

        sessions_all, tasks_all = _load_period(start_date, end_date)
        sessions_prev, tasks_prev = _load_period(prev_start, prev_end)
        current_data = _build_metrics(sessions_all, tasks_all, start_date, end_date)
        prev_data = _build_metrics(sessions_prev, tasks_prev, prev_start, prev_end)
        conn.close()

        sessions_filtered = current_data["sessions"]
        tasks_list = current_data["tasks_list"]
        total_minutes = current_data["total_minutes"]
        sessions_count = current_data["sessions_count"]
        priority_minutes = current_data["priority_minutes"]
        completion_rate = current_data["completion_rate"]

        dt_start = None
        dt_end = None
        for row in sessions_filtered:
            dt = self._parse_dt(row["started_at"])
            mins = int(row["duration_min"] or 0)
            if dt and (dt_start is None or dt < dt_start):
                dt_start = dt
            if dt:
                end_dt = self._parse_dt(row["ended_at"]) if row["ended_at"] else dt + timedelta(minutes=mins)
                if end_dt and (dt_end is None or end_dt > dt_end):
                    dt_end = end_dt

        if self.current_period == "Today":
            date_text = datetime.now().strftime("%b %d, %Y")
        else:
            date_text = f"{start_date.strftime('%b %d')} - {end_date.strftime('%b %d, %Y')}"

        if len(sessions_filtered) == 1 and dt_start and dt_end:
            time_text = (
                f"{self._format_time(dt_start)} - {self._format_time(dt_end)} "
                f"({format_duration_minutes(total_minutes)})"
            )
        elif total_minutes > 0:
            time_text = f"Total focus: {format_duration_minutes(total_minutes)}"
        else:
            time_text = "No sessions in this range"
        self.date_label.setText(date_text)
        self.time_label.setText(time_text)

        if self.current_period == "Week":
            period_label = "This Week"
        elif self.current_period == "Month":
            period_label = "This Month"
        else:
            period_label = "Today"
        self.header_title.setText(f"Session Report - {period_label}")

        high_minutes = priority_minutes.get("high", 0)
        other_minutes = max(total_minutes - high_minutes, 0)
        high_pct = int(round((high_minutes / total_minutes) * 100)) if total_minutes else 0

        def _delta_pct(current, previous):
            if previous == 0:
                return 0 if current == 0 else 100
            return int(round(((current - previous) / previous) * 100))

        prev_high = prev_data["priority_minutes"].get("high", 0)
        prev_completion = prev_data["completion_rate"]
        prev_sessions = prev_data["sessions_count"]

        high_delta = _delta_pct(high_minutes, prev_high)
        comp_delta = completion_rate - prev_completion
        sess_delta = _delta_pct(sessions_count, prev_sessions)

        high_prefix = "+" if high_delta >= 0 else ""
        comp_prefix = "+" if comp_delta >= 0 else ""
        sess_prefix = "+" if sess_delta >= 0 else ""

        self.summary_cards[0]["value"].setText(format_duration_minutes(high_minutes))
        self.summary_cards[1]["value"].setText(f"{completion_rate}%")
        self.summary_cards[2]["value"].setText(str(sessions_count))

        self.summary_cards[0]["delta"].setText(f"{high_prefix}{high_delta}%")
        self.summary_cards[1]["delta"].setText(f"{comp_prefix}{comp_delta}%")
        self.summary_cards[2]["delta"].setText(f"{sess_prefix}{sess_delta}%")
        self._delta_values = {"high": high_delta, "comp": comp_delta, "sess": sess_delta}
        self._apply_delta_styles()

        self.ring.set_data(high_pct, "High")
        self.deep_row.setText("High Priority")
        self.deep_value.setText(format_duration_minutes(high_minutes))
        self.minor_row.setText("Other Priority")
        self.minor_value.setText(format_duration_minutes(other_minutes))
        if total_minutes > 0:
            self.breakdown_note.setText(f"High priority focus accounts for {high_pct}% of total time.")
        else:
            self.breakdown_note.setText("No focus data available for this period.")

        def _norm_prio(value):
            pr = normalize_priority(value)
            return pr if pr in PRIORITY_LEVELS else "too low"

        ladder_rows = []
        label_map = [("high", "High"), ("medium", "Medium"), ("low", "Low"), ("too low", "Too Low")]
        for level, label in label_map:
            total = sum(1 for item in tasks_list if _norm_prio(item.get("priority")) == level)
            completed = sum(1 for item in tasks_list if _norm_prio(item.get("priority")) == level and item.get("done"))
            ladder_rows.append({
                "label": label,
                "level": level,
                "completed": completed,
                "total": total
            })
        self.timeline_chart.set_data(ladder_rows)

        def _task_sort_key(item):
            done_rank = 1 if item.get("done") else 0
            completed_dt = self._parse_dt(item.get("completed_at")) if item.get("done") else None
            completed_ts = completed_dt.timestamp() if completed_dt else 0
            return (done_rank, completed_ts, item.get("minutes", 0))

        if tasks_list:
            tasks_list.sort(key=_task_sort_key, reverse=True)

        while self.tasks_layout.count():
            item = self.tasks_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        if not tasks_list:
            tasks_list = []

        for task_item in tasks_list:
            row = QFrame()
            row.setObjectName("ReportTaskRow")
            row.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
            row_layout = QHBoxLayout(row)
            row_layout.setContentsMargins(12, 10, 12, 10)
            row_layout.setSpacing(10)

            title = QLabel(task_item["title"])
            subtitle = QLabel(f"{format_duration_minutes(task_item['minutes'])} spent")
            title.setStyleSheet(f"color: {self.colors['text']}; font-size: 12px; font-weight: 700;")
            subtitle.setStyleSheet(f"color: {self.colors['sub']}; font-size: 10px; font-weight: 600;")
            text_col = QVBoxLayout()
            text_col.setSpacing(4)
            text_col.addWidget(title)
            text_col.addWidget(subtitle)

            status = QLabel("DONE" if task_item["done"] else "PARTIAL")
            status_color = "#34d399" if task_item["done"] else self.colors["primary"]
            status.setStyleSheet(
                f"color: {status_color}; background: rgba(17, 164, 212, 31); font-size: 9px; font-weight: 800; padding: 4px 8px; border-radius: 10px;"
            )

            row_layout.addLayout(text_col)
            row_layout.addStretch()
            row_layout.addWidget(status)
            row.setStyleSheet(
                f"QFrame#ReportTaskRow {{ background: {self.colors['card']}; border: 1px solid {self.colors['primary_soft_border']}; border-radius: 12px; }}"
            )
            self.tasks_layout.addWidget(row)

