from PyQt6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QFrame,
    QScrollArea,
    QPushButton,
    QButtonGroup,
    QSizePolicy,
    QDialog,
    QGridLayout,
    QDateEdit,
    QComboBox,
    QMessageBox
)
from PyQt6.QtCore import Qt, pyqtSignal, QRectF, QPointF, QTimer, QPoint, QDate, QLocale
from PyQt6.QtGui import QColor, QPainter, QPainterPath, QPen
from database.db_manager import get_db_connection
from resources.priority import normalize_priority, priority_weight, PRIORITY_LEVELS
from resources.theme import get_theme
from resources.time_format import format_duration_minutes
from datetime import datetime, timedelta
import calendar


class NoWheelComboBox(QComboBox):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._ensure_font_size()

    def _ensure_font_size(self):
        font = self.font()
        if font.pointSize() <= 0:
            font.setPointSize(10)
            self.setFont(font)
        view = self.view()
        if view:
            view_font = view.font()
            if view_font.pointSize() <= 0:
                view_font.setPointSize(10)
                view.setFont(view_font)

    def wheelEvent(self, event):
        event.ignore()


class FocusBarChart(QFrame):
    def __init__(self):
        super().__init__()
        self.values = [0] * 7
        self.labels = ["M", "T", "W", "T", "F", "S", "S"]
        self.highlight_index = 4
        self.colors = {}
        self.setMinimumHeight(150)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)

    def set_data(self, values, labels=None, highlight_index=None):
        if values is not None:
            self.values = list(values)
        if labels is not None:
            padded = list(labels) + [""] * max(0, len(self.values) - len(labels))
            self.labels = padded[:len(self.values)] if self.values else []
        if highlight_index is not None:
            max_idx = max(len(self.values) - 1, 0)
            self.highlight_index = max(0, min(int(highlight_index), max_idx))
        self.update()

    def set_theme(self, colors):
        self.colors = colors or {}
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        try:
            rect = QRectF(self.rect())
            left_pad = 6
            right_pad = 6
            top_pad = 6
            bottom_pad = 22
            chart_rect = QRectF(
                rect.left() + left_pad,
                rect.top() + top_pad,
                rect.width() - left_pad - right_pad,
                rect.height() - top_pad - bottom_pad
            )

            values = list(self.values)
            max_val = max(values) if values else 1
            if max_val <= 0:
                max_val = 1

            count = len(values)
            if count == 0:
                return

            gap = 6
            bar_w = (chart_rect.width() - gap * (count - 1)) / max(count, 1)

            primary = QColor(self.colors.get("primary", "#11a4d4"))
            soft = QColor(primary)
            soft.setAlpha(45)

            for idx, val in enumerate(values):
                height = (val / max_val) * chart_rect.height()
                x = chart_rect.left() + idx * (bar_w + gap)
                y = chart_rect.bottom() - height
                bar_rect = QRectF(x, y, bar_w, height)
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(primary if idx == self.highlight_index else soft)
                painter.drawRoundedRect(bar_rect, 4, 4)

            label_color = QColor(self.colors.get("sub", "#64748b"))
            painter.setPen(label_color)
            font = painter.font()
            font.setPointSize(8)
            font.setBold(True)
            painter.setFont(font)

            for idx, label in enumerate(self.labels):
                if not label:
                    continue
                text_width = painter.fontMetrics().horizontalAdvance(label)
                x = chart_rect.left() + idx * (bar_w + gap) + (bar_w - text_width) / 2
                y = rect.bottom() - 4
                painter.drawText(QPointF(x, y), label)
        finally:
            painter.end()


class EnergyTrendWidget(QFrame):
    def __init__(self):
        super().__init__()
        self.values = [0.7, 0.85, 0.95, 1.0, 0.9, 0.55, 0.7, 0.92, 0.88, 0.6]
        self.colors = {}
        self.setMinimumHeight(110)
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
            step = chart_rect.width() / max(count - 1, 1)
            points = []
            for idx, value in enumerate(values):
                x = chart_rect.left() + idx * step
                y = chart_rect.bottom() - (max(0.0, min(value, 1.0)) * chart_rect.height())
                points.append(QPointF(x, y))

            path = QPainterPath()
            path.moveTo(points[0])
            for pt in points[1:]:
                path.lineTo(pt)

            fill_path = QPainterPath(path)
            fill_path.lineTo(chart_rect.right(), chart_rect.bottom())
            fill_path.lineTo(chart_rect.left(), chart_rect.bottom())
            fill_path.closeSubpath()

            primary = QColor(self.colors.get("primary", "#11a4d4"))
            fill = QColor(primary)
            fill.setAlpha(60)
            painter.fillPath(fill_path, fill)

            pen = QPen(primary, 2.6)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            painter.setPen(pen)
            painter.drawPath(path)
        finally:
            painter.end()


class QuickStatsDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Dialog)
        self.setModal(True)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.colors = {}

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self.overlay = QFrame()
        self.overlay.setObjectName("QuickStatsOverlay")
        overlay_layout = QVBoxLayout(self.overlay)
        overlay_layout.setContentsMargins(0, 0, 0, 0)
        overlay_layout.setSpacing(0)
        overlay_layout.addStretch()

        self.sheet = QFrame()
        self.sheet.setObjectName("QuickStatsSheet")
        sheet_layout = QVBoxLayout(self.sheet)
        sheet_layout.setContentsMargins(16, 12, 16, 16)
        sheet_layout.setSpacing(12)

        self.handle = QFrame()
        self.handle.setFixedSize(48, 5)
        self.handle.setObjectName("QuickStatsHandle")
        handle_row = QHBoxLayout()
        handle_row.setContentsMargins(0, 0, 0, 0)
        handle_row.addStretch()
        handle_row.addWidget(self.handle)
        handle_row.addStretch()
        sheet_layout.addLayout(handle_row)

        header_row = QHBoxLayout()
        self.header_kicker = QLabel("Quick Stats")
        self.close_btn = QPushButton("x")
        self.close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.close_btn.clicked.connect(self.close)
        header_row.addWidget(self.header_kicker)
        header_row.addStretch()
        header_row.addWidget(self.close_btn)
        sheet_layout.addLayout(header_row)

        self.title_label = QLabel("Session")
        self.subtitle_label = QLabel("Session details")
        sheet_layout.addWidget(self.title_label)
        sheet_layout.addWidget(self.subtitle_label)

        self.metrics_grid = QGridLayout()
        self.metrics_grid.setHorizontalSpacing(10)
        self.metrics_grid.setVerticalSpacing(10)
        self.metric_cards = []
        for idx, title in enumerate(("Focus Score", "Efficiency", "Distractions", "Energy")):
            card = QFrame()
            card.setObjectName("QuickStatsCard")
            card.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
            card_layout = QVBoxLayout(card)
            card_layout.setContentsMargins(10, 10, 10, 10)
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
        sheet_layout.addLayout(self.metrics_grid)

        trend_header = QHBoxLayout()
        self.trend_title = QLabel("Energy Trend")
        self.trend_sub = QLabel("Based on focus peaks")
        trend_header.addWidget(self.trend_title)
        trend_header.addStretch()
        sheet_layout.addLayout(trend_header)
        sheet_layout.addWidget(self.trend_sub)

        self.energy_chart = EnergyTrendWidget()
        sheet_layout.addWidget(self.energy_chart)

        self.trend_labels = QHBoxLayout()
        self.trend_start = QLabel("Start")
        self.trend_mid1 = QLabel("15m")
        self.trend_mid2 = QLabel("30m")
        self.trend_end = QLabel("End")
        self.trend_labels.addWidget(self.trend_start)
        self.trend_labels.addStretch()
        self.trend_labels.addWidget(self.trend_mid1)
        self.trend_labels.addStretch()
        self.trend_labels.addWidget(self.trend_mid2)
        self.trend_labels.addStretch()
        self.trend_labels.addWidget(self.trend_end)
        sheet_layout.addLayout(self.trend_labels)

        self.details_container = QFrame()
        self.details_container.setObjectName("QuickStatsDetails")
        details_layout = QVBoxLayout(self.details_container)
        details_layout.setContentsMargins(0, 0, 0, 0)
        details_layout.setSpacing(0)

        self.detail_rows = []
        for title in ("Start Time", "Duration", "Task Type"):
            row = QFrame()
            row.setObjectName("QuickStatsDetailRow")
            row_layout = QHBoxLayout(row)
            row_layout.setContentsMargins(4, 6, 4, 6)
            row_layout.setSpacing(8)
            left = QLabel(title)
            right = QLabel("--")
            row_layout.addWidget(left)
            row_layout.addStretch()
            row_layout.addWidget(right)
            details_layout.addWidget(row)
            self.detail_rows.append({
                "row": row,
                "left": left,
                "right": right
            })
        sheet_layout.addWidget(self.details_container)

        self.done_btn = QPushButton("Done")
        self.done_btn.setObjectName("QuickStatsDone")
        self.done_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.done_btn.clicked.connect(self.close)
        sheet_layout.addWidget(self.done_btn)

        overlay_layout.addWidget(self.sheet)
        root.addWidget(self.overlay)

    def showEvent(self, event):
        super().showEvent(event)
        parent = self.parentWidget()
        if parent:
            self.resize(parent.size())
            self.move(parent.mapToGlobal(QPoint(0, 0)))

    def mousePressEvent(self, event):
        if self.sheet and not self.sheet.geometry().contains(event.pos()):
            self.close()
            return
        super().mousePressEvent(event)

    def update_theme(self, colors):
        self.colors = colors or {}
        c = self.colors
        self.overlay.setStyleSheet(
            f"QFrame#QuickStatsOverlay {{ background: rgba(16, 29, 34, 199); }}"
        )
        self.sheet.setStyleSheet(
            f"QFrame#QuickStatsSheet {{ background: {c['bg']}; border-top: 1px solid {c['primary_soft_border']}; border-radius: 16px; }}"
        )
        self.handle.setStyleSheet(
            f"QFrame#QuickStatsHandle {{ background: {c['primary_soft_border']}; border-radius: 3px; }}"
        )
        self.header_kicker.setStyleSheet(
            f"color: {c['primary']}; font-size: 11px; font-weight: 900;"
        )
        self.close_btn.setStyleSheet(
            f"QPushButton {{ color: {c['sub']}; background-color: transparent; border: none; font-size: 12px; font-weight: bold; }}"
        )
        self.title_label.setStyleSheet(
            f"color: {c['text']}; font-size: 16px; font-weight: 900;"
        )
        self.subtitle_label.setStyleSheet(
            f"color: {c['sub']}; font-size: 11px; font-weight: 600;"
        )
        for item in self.metric_cards:
            item["card"].setStyleSheet(
                f"QFrame#QuickStatsCard {{ background: {c['primary_soft']}; border: 1px solid {c['primary_soft_border']}; border-radius: 12px; }}"
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
        self.trend_title.setStyleSheet(
            f"color: {c['text']}; font-size: 12px; font-weight: 800;"
        )
        self.trend_sub.setStyleSheet(
            f"color: {c['sub']}; font-size: 9px; font-weight: 600;"
        )
        for lbl in (self.trend_start, self.trend_mid1, self.trend_mid2, self.trend_end):
            lbl.setStyleSheet(
                f"color: {c['sub']}; font-size: 8px; font-weight: 800;"
            )
        for idx in range(self.details_container.layout().count()):
            row = self.details_container.layout().itemAt(idx).widget()
            if row:
                row.setStyleSheet(
                    f"QFrame#QuickStatsDetailRow {{ border-bottom: 1px solid {c['primary_soft_border']}; }}"
                )
        for item in self.detail_rows:
            item["left"].setStyleSheet(
                f"color: {c['sub']}; font-size: 10px; font-weight: 700;"
            )
            item["right"].setStyleSheet(
                f"color: {c['text']}; font-size: 10px; font-weight: 700;"
            )
        self.done_btn.setStyleSheet(
            f"QPushButton#QuickStatsDone {{ background-color: {c['primary']}; color: white; border: none; border-radius: 12px; padding: 10px; font-size: 12px; font-weight: bold; }}"
        )
        self.energy_chart.set_theme(c)

    def load_activity(self, title, subtitle, focus_score, efficiency, distractions, energy_text, start_time, duration_text, task_type):
        self.title_label.setText(title)
        self.subtitle_label.setText(subtitle)

        metrics = [
            (f"{focus_score:.1f}/10", "+5%"),
            (f"{efficiency}%", "+2%" if efficiency >= 80 else "-2%"),
            (f"{distractions} minor", "No impact"),
            (energy_text, "Constant")
        ]
        for idx, item in enumerate(self.metric_cards):
            value, delta = metrics[idx]
            item["value"].setText(value)
            item["delta"].setText(delta)

        if self.detail_rows:
            self.detail_rows[0]["right"].setText(start_time)
            self.detail_rows[1]["right"].setText(duration_text)
            self.detail_rows[2]["right"].setText(task_type)

class ActivityCard(QFrame):
    clicked = pyqtSignal(str)

    def __init__(self, activity_id):
        super().__init__()
        self.activity_id = activity_id

    def mousePressEvent(self, event):
        super().mousePressEvent(event)
        if event.button() == Qt.MouseButton.LeftButton:
            QTimer.singleShot(0, lambda: self.clicked.emit(self.activity_id))


class HistoryPage(QWidget):
    task_restored = pyqtSignal()
    request_dashboard = pyqtSignal()
    request_report = pyqtSignal(str)
    request_quick_stats = pyqtSignal(str)

    def __init__(self):
        super().__init__()
        self.current_theme = "Light"
        self.colors = {}
        self.selected_activity_id = None
        self.activity_cards = {}
        self.latest_activity_id = None
        self.selected_date = datetime.now().date()
        self.setObjectName("HistoryPage")

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setStyleSheet("border: none; background: transparent;")

        self.container = QWidget()
        self.content = QVBoxLayout(self.container)
        self.content.setContentsMargins(22, 18, 22, 28)
        self.content.setSpacing(16)
        self.scroll.setWidget(self.container)

        root.addWidget(self.scroll)

        # Period selector
        self.period_frame = QFrame()
        self.period_frame.setObjectName("HistoryPeriod")
        period_layout = QHBoxLayout(self.period_frame)
        period_layout.setContentsMargins(4, 4, 4, 4)
        period_layout.setSpacing(4)

        self.period_group = QButtonGroup(self)
        self.period_day = QPushButton("Day")
        self.period_week = QPushButton("Week")
        self.period_month = QPushButton("Month")
        for btn in (self.period_day, self.period_week, self.period_month):
            btn.setCheckable(True)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            self.period_group.addButton(btn)
            period_layout.addWidget(btn)
        self.period_week.setChecked(True)
        self.current_period = "Week"
        self.period_group.setExclusive(True)
        self.period_group.buttonClicked.connect(self._on_period_changed)

        self.content.addWidget(self.period_frame)

        # Date selector
        self.date_frame = QFrame()
        self.date_frame.setObjectName("HistoryDatePicker")
        date_layout = QHBoxLayout(self.date_frame)
        date_layout.setContentsMargins(6, 4, 6, 4)
        date_layout.setSpacing(8)

        self.date_label = QLabel("Select Day")
        self.date_picker = QDateEdit()
        self.date_picker.setCalendarPopup(True)
        self.date_picker.setDisplayFormat("dd MMM yyyy")
        self.date_picker.setDate(QDate.currentDate())
        self.date_picker.setCursor(Qt.CursorShape.PointingHandCursor)
        self.date_picker.dateChanged.connect(self._on_date_changed)

        self.day_picker_container = QWidget()
        day_layout = QHBoxLayout(self.day_picker_container)
        day_layout.setContentsMargins(0, 0, 0, 0)
        day_layout.setSpacing(6)

        locale = QLocale.system()
        self.month_names = [locale.monthName(i, QLocale.FormatType.LongFormat) for i in range(1, 13)]

        self.day_combo = NoWheelComboBox()
        self.day_combo.setCursor(Qt.CursorShape.PointingHandCursor)
        self.day_combo.currentIndexChanged.connect(self._on_day_picker_changed)

        self.day_month_combo = NoWheelComboBox()
        self.day_month_combo.setCursor(Qt.CursorShape.PointingHandCursor)
        self.day_month_combo.addItems(self.month_names)
        self.day_month_combo.currentIndexChanged.connect(self._on_day_picker_changed)

        self.day_year_combo = NoWheelComboBox()
        self.day_year_combo.setCursor(Qt.CursorShape.PointingHandCursor)
        self.day_year_combo.addItems([str(y) for y in range(2000, 2101)])
        self.day_year_combo.currentIndexChanged.connect(self._on_day_picker_changed)

        day_layout.addWidget(self.day_combo)
        day_layout.addWidget(self.day_month_combo)
        day_layout.addWidget(self.day_year_combo)
        self.day_picker_container.setVisible(False)

        self.week_picker_container = QWidget()
        week_layout = QHBoxLayout(self.week_picker_container)
        week_layout.setContentsMargins(0, 0, 0, 0)
        week_layout.setSpacing(6)

        self.week_combo = NoWheelComboBox()
        self.week_combo.setCursor(Qt.CursorShape.PointingHandCursor)
        self.week_combo.currentIndexChanged.connect(self._on_week_changed)

        self.week_month_combo = NoWheelComboBox()
        self.week_month_combo.setCursor(Qt.CursorShape.PointingHandCursor)
        self.week_month_combo.addItems(self.month_names)
        self.week_month_combo.currentIndexChanged.connect(self._on_week_changed)

        self.week_year_combo = NoWheelComboBox()
        self.week_year_combo.setCursor(Qt.CursorShape.PointingHandCursor)
        self.week_year_combo.addItems([str(y) for y in range(2000, 2101)])
        self.week_year_combo.setCurrentText(str(self.selected_date.year))
        self.week_year_combo.currentIndexChanged.connect(self._on_week_changed)

        week_layout.addWidget(self.week_combo)
        week_layout.addWidget(self.week_month_combo)
        week_layout.addWidget(self.week_year_combo)
        self.week_picker_container.setVisible(False)

        self.month_picker_container = QWidget()
        month_layout = QHBoxLayout(self.month_picker_container)
        month_layout.setContentsMargins(0, 0, 0, 0)
        month_layout.setSpacing(6)

        self.month_combo = NoWheelComboBox()
        self.month_combo.addItems(self.month_names)
        self.month_combo.setCursor(Qt.CursorShape.PointingHandCursor)
        self.month_combo.currentIndexChanged.connect(self._on_month_changed)

        self.year_combo = NoWheelComboBox()
        self.year_combo.setCursor(Qt.CursorShape.PointingHandCursor)
        self.year_combo.addItems([str(y) for y in range(2000, 2101)])
        self.year_combo.setCurrentText(str(self.selected_date.year))
        self.year_combo.currentIndexChanged.connect(self._on_month_changed)

        month_layout.addWidget(self.month_combo)
        month_layout.addWidget(self.year_combo)
        self.month_picker_container.setVisible(False)

        date_layout.addWidget(self.date_label)
        date_layout.addStretch()
        date_layout.addWidget(self.date_picker)
        date_layout.addWidget(self.day_picker_container)
        date_layout.addWidget(self.week_picker_container)
        date_layout.addWidget(self.month_picker_container)

        self.content.addWidget(self.date_frame)

        self.date_picker.setVisible(False)
        self.day_picker_container.setVisible(False)
        self.week_picker_container.setVisible(True)
        self.month_picker_container.setVisible(False)
        self._sync_week_picker()

        # Focus section
        self.focus_section = QFrame()
        self.focus_section.setObjectName("HistoryFocus")
        focus_layout = QVBoxLayout(self.focus_section)
        focus_layout.setContentsMargins(8, 6, 8, 6)
        focus_layout.setSpacing(8)

        self.focus_label = QLabel("Focus Hours")
        self.focus_value = QLabel("0h")
        self.focus_delta = QLabel("+0%")

        focus_top = QHBoxLayout()
        focus_top.addWidget(self.focus_value)
        focus_top.addWidget(self.focus_delta)
        focus_top.addStretch()

        focus_layout.addWidget(self.focus_label)
        focus_layout.addLayout(focus_top)

        self.focus_chart = FocusBarChart()
        focus_layout.addWidget(self.focus_chart)

        self.content.addWidget(self.focus_section)

        # Quick stats
        self.quick_container = QWidget()
        self.quick_container.setObjectName("HistoryQuickStats")
        self.quick_layout = QHBoxLayout(self.quick_container)
        self.quick_layout.setContentsMargins(0, 0, 0, 0)
        self.quick_layout.setSpacing(12)

        self.stat_cards = []
        self._add_stat_card("Completed Tasks")
        self._add_stat_card("Avg Session")

        self.content.addWidget(self.quick_container)

        # Latest report
        self.latest_frame = QFrame()
        self.latest_frame.setObjectName("HistoryLatest")
        latest_layout = QHBoxLayout(self.latest_frame)
        latest_layout.setContentsMargins(14, 12, 14, 12)
        latest_layout.setSpacing(10)

        latest_left = QVBoxLayout()
        latest_left.setSpacing(4)
        self.latest_label = QLabel("Latest Report Available")
        self.latest_subtitle = QLabel("No recent sessions")
        latest_left.addWidget(self.latest_label)
        latest_left.addWidget(self.latest_subtitle)

        self.latest_action = QPushButton("View Report")
        self.latest_action.setObjectName("HistoryLinkButton")
        self.latest_action.setCursor(Qt.CursorShape.PointingHandCursor)
        self.latest_action.clicked.connect(self._open_latest_report)

        latest_layout.addLayout(latest_left)
        latest_layout.addStretch()
        latest_layout.addWidget(self.latest_action)
        self.content.addWidget(self.latest_frame)

        # Activity log
        self.activity_header = QFrame()
        activity_header_layout = QHBoxLayout(self.activity_header)
        activity_header_layout.setContentsMargins(0, 0, 0, 0)
        activity_header_layout.setSpacing(8)
        self.activity_title = QLabel("Activity Log")
        self.activity_button = QPushButton("History")
        self.activity_button.setObjectName("HistoryLinkButton")
        self.activity_button.setCursor(Qt.CursorShape.PointingHandCursor)
        activity_header_layout.addWidget(self.activity_title)
        activity_header_layout.addStretch()
        activity_header_layout.addWidget(self.activity_button)
        self.content.addWidget(self.activity_header)

        self.activity_container = QWidget()
        self.activity_layout = QVBoxLayout(self.activity_container)
        self.activity_layout.setContentsMargins(0, 0, 0, 0)
        self.activity_layout.setSpacing(10)
        self.content.addWidget(self.activity_container)

        # Insight section
        self.insight_frame = QFrame()
        self.insight_frame.setObjectName("HistoryInsight")
        insight_layout = QVBoxLayout(self.insight_frame)
        insight_layout.setContentsMargins(16, 16, 16, 16)
        insight_layout.setSpacing(10)

        self.insight_kicker = QLabel("Weekly AI Insight")
        self.insight_text = QLabel(
            "Your productivity peaked between 9:00 AM and 11:00 AM this week. Sessions started during this window were longer than average."
        )
        self.insight_text.setWordWrap(True)
        self.insight_action = QPushButton("Full Analytics Breakdown")
        self.insight_action.setObjectName("HistoryPrimaryButton")
        self.insight_action.setCursor(Qt.CursorShape.PointingHandCursor)
        self.insight_action.clicked.connect(lambda: self.request_dashboard.emit())

        insight_layout.addWidget(self.insight_kicker)
        insight_layout.addWidget(self.insight_text)
        insight_layout.addWidget(self.insight_action)

        self.content.addWidget(self.insight_frame)
        self.content.addStretch()

        self.update_theme("Light")
        self.refresh_history()

    def _add_stat_card(self, title):
        card = QFrame()
        card.setObjectName("HistoryStatCard")
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

        self.quick_layout.addWidget(card)
        self.stat_cards.append({
            "card": card,
            "title": title_lbl,
            "value": value_lbl,
            "delta": delta_lbl
        })

    def apply_styles(self):
        colors = self.colors
        self.setStyleSheet(f"QWidget#HistoryPage {{ background: {colors['bg']}; }}")

        self.period_frame.setStyleSheet(
            f"QFrame#HistoryPeriod {{ background: {colors['card_alt']}; border: 1px solid {colors['border']}; border-radius: 10px; }}"
            "QPushButton { border: none; padding: 6px 10px; border-radius: 8px; font-size: 11px; font-weight: bold; }"
            f"QPushButton:checked {{ background-color: {colors['card']}; color: {colors['primary']}; }}"
            f"QPushButton:unchecked {{ color: {colors['sub']}; }}"
        )
        self.date_frame.setStyleSheet(
            f"QFrame#HistoryDatePicker {{ background: {colors['card_alt']}; border: 1px solid {colors['border']}; border-radius: 10px; }}"
        )
        self.date_label.setStyleSheet(
            f"color: {colors['sub']}; font-size: 10px; font-weight: 700;"
        )
        self.date_picker.setStyleSheet(
            f"QDateEdit {{ background: {colors['card']}; color: {colors['text']}; border: 1px solid {colors['border']}; "
            f"border-radius: 8px; padding: 4px 8px; font-size: 10px; font-weight: 700; }}"
        )
        self.month_combo.setStyleSheet(
            f"QComboBox {{ background: {colors['card']}; color: {colors['text']}; border: 1px solid {colors['border']}; "
            f"border-radius: 8px; padding: 4px 8px; font-size: 7.5pt; font-weight: 700; }}"
            f"QComboBox QAbstractItemView {{ background: {colors['card']}; color: {colors['text']}; "
            f"selection-background-color: {colors['primary_soft']}; selection-color: {colors['text']}; border: 1px solid {colors['border']}; }}"
        )
        combo_style = (
            f"QComboBox {{ background: {colors['card']}; color: {colors['text']}; border: 1px solid {colors['border']}; "
            f"border-radius: 8px; padding: 4px 8px; font-size: 7.5pt; font-weight: 700; }}"
            f"QComboBox QAbstractItemView {{ background: {colors['card']}; color: {colors['text']}; "
            f"selection-background-color: {colors['primary_soft']}; selection-color: {colors['text']}; border: 1px solid {colors['border']}; }}"
        )
        self.day_combo.setStyleSheet(combo_style)
        self.day_month_combo.setStyleSheet(combo_style)
        self.day_year_combo.setStyleSheet(combo_style)
        self.week_combo.setStyleSheet(combo_style)
        self.week_month_combo.setStyleSheet(combo_style)
        self.week_year_combo.setStyleSheet(combo_style)
        self.year_combo.setStyleSheet(
            f"QComboBox {{ background: {colors['card']}; color: {colors['text']}; border: 1px solid {colors['border']}; "
            f"border-radius: 8px; padding: 4px 8px; font-size: 7.5pt; font-weight: 700; }}"
            f"QComboBox QAbstractItemView {{ background: {colors['card']}; color: {colors['text']}; "
            f"selection-background-color: {colors['primary_soft']}; selection-color: {colors['text']}; border: 1px solid {colors['border']}; }}"
        )
        for combo in (
            self.month_combo,
            self.day_combo,
            self.day_month_combo,
            self.day_year_combo,
            self.week_combo,
            self.week_month_combo,
            self.week_year_combo,
            self.year_combo,
        ):
            try:
                combo._ensure_font_size()
            except Exception:
                pass

        self.focus_label.setStyleSheet(
            f"color: {colors['sub']}; font-size: 11px; font-weight: 700;"
        )
        self.focus_value.setStyleSheet(
            f"color: {colors['text']}; font-size: 30px; font-weight: 900;"
        )

        self.focus_chart.set_theme(colors)

        for item in self.stat_cards:
            item["card"].setStyleSheet(
                f"QFrame#HistoryStatCard {{ background: {colors['primary_soft']}; border: 1px solid {colors['primary_soft_border']}; border-radius: 14px; }}"
            )
            item["title"].setStyleSheet(
                f"color: {colors['sub']}; font-size: 9px; font-weight: 800;"
            )
            item["value"].setStyleSheet(
                f"color: {colors['primary']}; font-size: 20px; font-weight: 900;"
            )

        self.latest_frame.setStyleSheet(
            "QFrame#HistoryLatest {"
            f" background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 {colors['primary_soft']}, stop:1 {colors['card']});"
            f" border: 1px solid {colors['primary_soft_border']}; border-radius: 14px; }}"
        )
        self.latest_label.setStyleSheet(
            f"color: {colors['primary']}; font-size: 10px; font-weight: 800;"
        )
        self.latest_subtitle.setStyleSheet(
            f"color: {colors['text']}; font-size: 12px; font-weight: 700;"
        )

        self.activity_title.setStyleSheet(
            f"color: {colors['text']}; font-size: 16px; font-weight: 800;"
        )

        self.activity_button.setStyleSheet(
            f"QPushButton#HistoryLinkButton {{ color: {colors['primary']}; background-color: transparent; border: none; font-size: 10px; font-weight: bold; }}"
        )
        self.latest_action.setStyleSheet(
            f"QPushButton#HistoryLinkButton {{ color: {colors['primary']}; background-color: transparent; border: none; font-size: 10px; font-weight: bold; }}"
        )

        self.insight_frame.setStyleSheet(
            "QFrame#HistoryInsight {"
            f" background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 {colors['primary_soft']}, stop:1 {colors['card']});"
            f" border: 1px solid {colors['primary_soft_border']}; border-radius: 18px; }}"
        )
        self.insight_kicker.setStyleSheet(
            f"color: {colors['primary']}; font-size: 10px; font-weight: 800;"
        )
        self.insight_text.setStyleSheet(
            f"color: {colors['text']}; font-size: 12px; font-weight: 600;"
        )
        self.insight_action.setStyleSheet(
            f"QPushButton#HistoryPrimaryButton {{ background-color: {colors['primary_soft']}; color: {colors['primary']}; border: none; border-radius: 10px; padding: 8px 10px; font-size: 10px; font-weight: bold; }}"
        )

    def update_theme(self, theme):
        self.current_theme = theme
        self.colors = get_theme(theme)

        self.apply_styles()
        self.refresh_history()

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

    def _clear_layout(self, layout):
        while layout.count():
            item = layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

    def _format_hours(self, minutes):
        return format_duration_minutes(minutes)

    def _delta_percent(self, current, previous):
        if previous <= 0:
            return 0 if current == 0 else 100
        return int(round(((current - previous) / previous) * 100))

    def _format_hour_label(self, hour):
        base = datetime(2000, 1, 1, hour % 24, 0)
        return base.strftime("%I:%M %p").lstrip("0")

    def _compute_period_insight(self, session_rows, start_date, end_date, tasks_by_id):
        sessions = []
        for row in session_rows:
            if len(row) >= 7:
                _, task_id, _, task_priority, started_at, duration_min, status = row[:7]
            else:
                _, task_id, _, started_at, duration_min, status = row[:6]
                task_priority = None

            status_norm = str(status).strip().lower() if status is not None else ""
            if status_norm and status_norm not in ("completed", "stopped"):
                continue

            dt = self._parse_dt(started_at)
            if not dt or not (start_date <= dt.date() <= end_date):
                continue
            minutes = int(duration_min or 0)
            if minutes <= 0:
                continue

            prio = normalize_priority(task_priority) if task_priority else None
            if prio not in PRIORITY_LEVELS:
                prio = tasks_by_id.get(task_id)
            if prio not in PRIORITY_LEVELS:
                prio = "too low"
            sessions.append((dt, minutes, prio))

        if not sessions:
            if self.current_period == "Day":
                return "No focus sessions yet today. Start a session to unlock insights."
            if self.current_period == "Month":
                return "No focus sessions yet this month. Start a session to unlock insights."
            return "No focus sessions yet this week. Start a session to unlock insights."

        total_minutes = sum(minutes for _, minutes, _ in sessions)
        total_sessions = len(sessions)
        avg_minutes = int(round(total_minutes / max(total_sessions, 1)))
        active_days = len({dt.date() for dt, _, _ in sessions})

        priority_minutes = {level: 0 for level in PRIORITY_LEVELS}
        minutes_by_day = {}
        weighted_by_day = {}
        raw_buckets = [0] * 12
        weighted_buckets = [0.0] * 12
        for dt, minutes, prio in sessions:
            priority_minutes[prio] += minutes
            minutes_by_day[dt.date()] = minutes_by_day.get(dt.date(), 0) + minutes
            weight = priority_weight(prio)
            weighted = minutes * weight
            weighted_by_day[dt.date()] = weighted_by_day.get(dt.date(), 0) + weighted
            bucket = dt.hour // 2
            raw_buckets[bucket] += minutes
            weighted_buckets[bucket] += weighted

        high_minutes = priority_minutes.get("high", 0)
        medium_minutes = priority_minutes.get("medium", 0)
        high_medium = high_minutes + medium_minutes
        high_medium_pct = int(round((high_medium / total_minutes) * 100)) if total_minutes else 0

        bucket_source = weighted_buckets if max(weighted_buckets) > 0 else raw_buckets
        peak_idx = max(range(len(bucket_source)), key=lambda i: (bucket_source[i], -i))
        start_hour = peak_idx * 2
        end_hour = (start_hour + 2) % 24
        window_text = f"{self._format_hour_label(start_hour)} and {self._format_hour_label(end_hour)}"

        if self.current_period == "Day":
            longest = max(minutes for _, minutes, _ in sessions)
            return (
                f"Today you logged {format_duration_minutes(total_minutes)} across {total_sessions} sessions. "
                f"High/medium priority time: {high_medium_pct}%. "
                f"Peak priority window was {window_text}, and your longest session was {format_duration_minutes(longest)}."
            )

        if self.current_period == "Month":
            span_days = (end_date - start_date).days + 1
            week_bins = max(1, (span_days + 6) // 7)
            week_weighted = [0.0] * week_bins
            for dt, minutes, prio in sessions:
                idx = (dt.date() - start_date).days // 7
                idx = min(max(idx, 0), week_bins - 1)
                week_weighted[idx] += minutes * priority_weight(prio)
            best_week_idx = max(range(week_bins), key=lambda i: (week_weighted[i], -i))
            best_start = start_date + timedelta(days=best_week_idx * 7)
            best_end = min(best_start + timedelta(days=6), end_date)
            best_range = f"{best_start.strftime('%b %d')} - {best_end.strftime('%b %d')}"
            return (
                f"This month you focused {format_duration_minutes(total_minutes)} across {total_sessions} sessions on {active_days} days. "
                f"High/medium priority time: {high_medium_pct}%. "
                f"Your strongest priority stretch was {best_range}, with a peak window at {window_text}."
            )

        best_by_source = weighted_by_day if max(weighted_by_day.values(), default=0) > 0 else minutes_by_day
        best_day = max(best_by_source.items(), key=lambda item: (item[1], item[0]))
        best_day_label = best_day[0].strftime("%a")
        return (
            f"This week you focused {format_duration_minutes(total_minutes)} across {total_sessions} sessions on {active_days} days. "
            f"High/medium priority time: {high_medium_pct}%. "
            f"Your best priority day was {best_day_label} ({format_duration_minutes(minutes_by_day.get(best_day[0], 0))}), "
            f"with a peak window at {window_text}."
        )

    def _on_period_changed(self, button):
        label = button.text().strip() if button else ""
        if label in ("Day", "Week", "Month"):
            self.current_period = label
            if self.current_period == "Day":
                self.date_label.setText("Select Day")
                self.date_picker.setDisplayFormat("dd MMM yyyy")
                self.date_picker.setVisible(False)
                self.day_picker_container.setVisible(True)
                self.week_picker_container.setVisible(False)
                self.month_picker_container.setVisible(False)
                self._sync_day_picker()
            elif self.current_period == "Month":
                self.date_label.setText("Select Month")
                self.date_picker.setVisible(False)
                self.day_picker_container.setVisible(False)
                self.week_picker_container.setVisible(False)
                self.month_picker_container.setVisible(True)
                self.month_combo.blockSignals(True)
                self.year_combo.blockSignals(True)
                self.month_combo.setCurrentIndex(max(0, self.selected_date.month - 1))
                self.year_combo.setCurrentText(str(self.selected_date.year))
                self.month_combo.blockSignals(False)
                self.year_combo.blockSignals(False)
            else:
                self.date_label.setText("Select Week")
                self.date_picker.setDisplayFormat("dd MMM yyyy")
                self.date_picker.setVisible(False)
                self.day_picker_container.setVisible(False)
                self.week_picker_container.setVisible(True)
                self.month_picker_container.setVisible(False)
                self.selected_date = (self.selected_date or datetime.now().date()).replace(day=1)
                self._sync_week_picker()
            self.refresh_history()

    def _on_date_changed(self, qdate):
        if not qdate:
            return
        try:
            self.selected_date = qdate.toPyDate()
        except Exception:
            self.selected_date = datetime.now().date()
        self.refresh_history()

    def _sync_day_picker(self):
        try:
            self.day_month_combo.blockSignals(True)
            self.day_year_combo.blockSignals(True)
            self.day_month_combo.setCurrentIndex(max(0, self.selected_date.month - 1))
            self.day_year_combo.setCurrentText(str(self.selected_date.year))
            self.day_month_combo.blockSignals(False)
            self.day_year_combo.blockSignals(False)
            self._update_day_combo(self.selected_date.year, self.selected_date.month, self.selected_date.day)
        except Exception:
            pass

    def _update_day_combo(self, year, month, selected_day=None):
        days_in_month = calendar.monthrange(year, month)[1]
        if selected_day is None:
            selected_day = min(self.selected_date.day, days_in_month)
        selected_day = max(1, min(selected_day, days_in_month))
        self.day_combo.blockSignals(True)
        self.day_combo.clear()
        for d in range(1, days_in_month + 1):
            self.day_combo.addItem(str(d))
        self.day_combo.setCurrentIndex(selected_day - 1)
        self.day_combo.blockSignals(False)

    def _on_day_picker_changed(self, _=None):
        try:
            month = self.day_month_combo.currentIndex() + 1
            year = int(self.day_year_combo.currentText())
            current_day = int(self.day_combo.currentText() or "1")
            self._update_day_combo(year, month, current_day)
            day = int(self.day_combo.currentText() or "1")
            self.selected_date = datetime(year, month, day).date()
        except Exception:
            self.selected_date = datetime.now().date()
        self.refresh_history()

    def _sync_week_picker(self):
        try:
            year = self.selected_date.year
            self.week_year_combo.blockSignals(True)
            self.week_year_combo.setCurrentText(str(year))
            self.week_year_combo.blockSignals(False)
            self.week_month_combo.blockSignals(True)
            self.week_month_combo.setCurrentIndex(max(0, self.selected_date.month - 1))
            self.week_month_combo.blockSignals(False)
            self._update_week_combo(year, self.selected_date.month, self._month_week_index(self.selected_date))
        except Exception:
            pass

    def _weeks_in_month(self, year, month):
        return 4

    def _update_week_combo(self, year, month, selected_week=None):
        if selected_week is None:
            selected_week = 1
        weeks_count = self._weeks_in_month(year, month)
        try:
            today = datetime.now().date()
            if year == today.year and month == today.month:
                current_week = self._month_week_index(today)
                weeks_count = max(1, min(weeks_count, current_week))
        except Exception:
            pass
        selected_week = max(1, min(selected_week, weeks_count))
        self.week_combo.blockSignals(True)
        self.week_combo.clear()
        for w in range(1, weeks_count + 1):
            self.week_combo.addItem(f"Week {w}")
        self.week_combo.setCurrentIndex(selected_week - 1)
        self.week_combo.blockSignals(False)

    def _on_week_changed(self, _=None):
        try:
            year = int(self.week_year_combo.currentText())
            month = self.week_month_combo.currentIndex() + 1
            week = self.week_combo.currentIndex() + 1
            self._update_week_combo(year, month, week)
            self.selected_date = self._month_week_start(year, month, week)
        except Exception:
            self.selected_date = datetime.now().date()
        self.refresh_history()

    def _month_week_index(self, date_value):
        first_of_month = date_value.replace(day=1)
        day_offset = (date_value - first_of_month).days
        weeks_count = self._weeks_in_month(date_value.year, date_value.month)
        return min(weeks_count, (day_offset // 7) + 1)

    def _month_week_start(self, year, month, week):
        weeks_count = self._weeks_in_month(year, month)
        week = max(1, min(int(week), weeks_count))
        start_day = 1 + (week - 1) * 7
        days_in_month = calendar.monthrange(year, month)[1]
        start_day = min(start_day, days_in_month)
        return datetime(year, month, start_day).date()

    def _week_month_bounds(self):
        try:
            year = int(self.week_year_combo.currentText())
            month = self.week_month_combo.currentIndex() + 1
            week = self.week_combo.currentIndex() + 1
        except Exception:
            base = self.selected_date or datetime.now().date()
            year = base.year
            month = base.month
            week = self._month_week_index(base)
        start = self._month_week_start(year, month, week)
        days_in_month = calendar.monthrange(year, month)[1]
        end_of_month = datetime(year, month, days_in_month).date()
        if week >= self._weeks_in_month(year, month):
            end = end_of_month
        else:
            end = min(start + timedelta(days=6), end_of_month)
        prev_end = start - timedelta(days=1)
        prev_start = prev_end - timedelta(days=6)
        return start, end, prev_start, prev_end

    def _on_month_changed(self, _=None):
        try:
            month = self.month_combo.currentIndex() + 1
            year = int(self.year_combo.currentText())
            self.selected_date = datetime(year, month, 1).date()
        except Exception:
            self.selected_date = datetime.now().date()
        self.refresh_history()

    def _period_bounds(self):
        base_date = self.selected_date or datetime.now().date()
        if self.current_period == "Day":
            start = base_date
            end = base_date
            prev_start = base_date - timedelta(days=1)
            prev_end = prev_start
        elif self.current_period == "Month":
            start = base_date.replace(day=1)
            if start.month == 12:
                next_month = start.replace(year=start.year + 1, month=1, day=1)
            else:
                next_month = start.replace(month=start.month + 1, day=1)
            end = next_month - timedelta(days=1)
            prev_end = start - timedelta(days=1)
            prev_start = prev_end.replace(day=1)
        else:
            start, end, prev_start, prev_end = self._week_month_bounds()
        return start, end, prev_start, prev_end

    def _build_focus_buckets(self, sessions, start, end):
        today = datetime.now().date()
        selected_date = self.selected_date or today
        if self.current_period == "Day":
            values = [0] * 8
            labels = ["0", "3", "6", "9", "12", "15", "18", "21"]
            for dt, minutes in sessions:
                if dt.date() == start:
                    idx = dt.hour // 3
                    values[idx] += minutes
            if selected_date == today:
                highlight = datetime.now().hour // 3
            else:
                highlight = values.index(max(values)) if max(values) > 0 else 0
            return values, labels, highlight

        if self.current_period == "Month":
            days = (end - start).days + 1
            values = [0] * days
            labels = []
            for i in range(days):
                day = start + timedelta(days=i)
                if i % 5 == 0 or i == days - 1:
                    labels.append(str(day.day))
                else:
                    labels.append("")
            for dt, minutes in sessions:
                if start <= dt.date() <= end:
                    idx = (dt.date() - start).days
                    if 0 <= idx < days:
                        values[idx] += minutes
            highlight = min(max((selected_date - start).days, 0), max(days - 1, 0))
            return values, labels, highlight

        values = [0] * 7
        labels = [(start + timedelta(days=i)).strftime("%a")[0] for i in range(7)]
        for dt, minutes in sessions:
            if start <= dt.date() <= end:
                idx = (dt.date() - start).days
                if 0 <= idx < 7:
                    values[idx] += minutes
        highlight = min(max((selected_date - start).days, 0), 6)
        return values, labels, highlight

    def refresh_history(self):
        conn = get_db_connection()
        try:
            task_rows = conn.execute(
                "SELECT id, title, priority, completed_at FROM tasks WHERE is_completed = 1"
            ).fetchall()
        except Exception as exc:
            task_rows = []

        try:
            task_priority_rows = conn.execute(
                "SELECT id, priority FROM tasks"
            ).fetchall()
        except Exception:
            task_priority_rows = []

        tasks_by_id = {}
        for task_id, prio in task_priority_rows:
            norm = normalize_priority(prio)
            if norm in PRIORITY_LEVELS:
                tasks_by_id[task_id] = norm

        try:
            info = conn.execute("PRAGMA table_info(pomodoro_sessions)").fetchall()
            cols = {row[1] for row in info} if info else set()
            select_cols = ["id", "task_id", "task_title", "started_at", "duration_min", "status"]
            if "task_priority" in cols:
                select_cols.insert(3, "task_priority")
            session_rows = conn.execute(
                f"SELECT {', '.join(select_cols)} FROM pomodoro_sessions"
            ).fetchall()
        except Exception:
            session_rows = []
        finally:
            conn.close()

        start, end, prev_start, prev_end = self._period_bounds()

        completed_current = 0
        completed_prev = 0
        completed_tasks_current = []
        for row in task_rows:
            dt = self._parse_dt(row[3])
            if not dt:
                continue
            if start <= dt.date() <= end:
                completed_current += 1
                completed_tasks_current.append(row)
            elif prev_start <= dt.date() <= prev_end:
                completed_prev += 1

        focus_minutes_current = 0
        focus_minutes_prev = 0
        sessions_current = []
        sessions_count_current = 0
        sessions_count_prev = 0
        minutes_by_task_period = {}

        for row in session_rows:
            if len(row) >= 7:
                session_id, task_id, task_title, task_priority, started_at, duration_min, status = row[:7]
            else:
                session_id, task_id, task_title, started_at, duration_min, status = row[:6]
                task_priority = None

            dt = self._parse_dt(started_at)
            if not dt:
                continue
            minutes = int(duration_min or 0)

            if start <= dt.date() <= end:
                sessions_current.append((session_id, task_id, task_title, dt, minutes, status))
                focus_minutes_current += minutes
                if minutes > 0:
                    sessions_count_current += 1
                if task_id is not None:
                    minutes_by_task_period[task_id] = minutes_by_task_period.get(task_id, 0) + minutes
            elif prev_start <= dt.date() <= prev_end:
                focus_minutes_prev += minutes
                if minutes > 0:
                    sessions_count_prev += 1

        focus_delta = self._delta_percent(focus_minutes_current, focus_minutes_prev)
        focus_delta_prefix = "+" if focus_delta >= 0 else ""
        self.focus_value.setText(self._format_hours(focus_minutes_current))
        self.focus_delta.setText(f"{focus_delta_prefix}{focus_delta}%")
        self.focus_delta.setStyleSheet(
            f"color: {self.colors['good'] if focus_delta >= 0 else self.colors['bad']}; font-size: 11px; font-weight: 800;"
        )

        chart_values, chart_labels, highlight_index = self._build_focus_buckets(
            [(dt, minutes) for _, _, _, dt, minutes, _ in sessions_current],
            start,
            end
        )
        self.focus_chart.set_data(chart_values, chart_labels, highlight_index)

        if self.stat_cards:
            completed_delta = self._delta_percent(completed_current, completed_prev)
            completed_prefix = "+" if completed_delta >= 0 else ""
            self.stat_cards[0]["value"].setText(str(completed_current))
            self.stat_cards[0]["delta"].setText(f"{completed_prefix}{completed_delta}%")
            self.stat_cards[0]["delta"].setStyleSheet(
                f"color: {self.colors['good'] if completed_delta >= 0 else self.colors['bad']}; font-size: 10px; font-weight: 800;"
            )

            avg_session = int(round(focus_minutes_current / max(sessions_count_current, 1))) if sessions_count_current else 0
            prev_avg = int(round(focus_minutes_prev / max(sessions_count_prev, 1))) if sessions_count_prev else 0
            avg_delta = self._delta_percent(avg_session, prev_avg)
            avg_prefix = "+" if avg_delta >= 0 else ""
            self.stat_cards[1]["value"].setText(format_duration_minutes(avg_session))
            self.stat_cards[1]["delta"].setText(f"{avg_prefix}{avg_delta}%")
            self.stat_cards[1]["delta"].setStyleSheet(
                f"color: {self.colors['good'] if avg_delta >= 0 else self.colors['bad']}; font-size: 10px; font-weight: 800;"
            )

        latest_label = "No recent sessions"
        latest_dt = None
        latest_title = None
        if sessions_current:
            latest = max(sessions_current, key=lambda item: item[3])
            latest_dt = latest[3]
            latest_title = latest[2] or "Focus Session"
        elif completed_tasks_current:
            latest_task = max(completed_tasks_current, key=lambda item: self._parse_dt(item[3]) or datetime.min)
            latest_dt = self._parse_dt(latest_task[3])
            latest_title = latest_task[1]

        if latest_dt:
            date_label = latest_dt.strftime("%A %d %B %Y")
            latest_label = date_label
        self.latest_subtitle.setText(latest_label)

        activities = []
        for row in completed_tasks_current:
            dt = self._parse_dt(row[3])
            if not dt:
                continue
            activities.append({
                "activity_id": f"task:{row[0]}",
                "kind": "task",
                "title": row[1],
                "time": dt,
                "duration": minutes_by_task_period.get(row[0], 0),
                "status": "Completed"
            })

        for session_id, task_id, task_title, dt, minutes, status in sessions_current:
            activities.append({
                "activity_id": f"session:{session_id}",
                "kind": "session",
                "title": task_title or "Focus Session",
                "time": dt,
                "duration": minutes,
                "status": "Focus Session"
            })

        activities.sort(key=lambda item: item["time"], reverse=True)

        if hasattr(self, "activity_title"):
            self.activity_title.setText(f"Activity Log - {self.current_period}")

        if activities:
            valid_ids = {item["activity_id"] for item in activities}
            if self.selected_activity_id not in valid_ids:
                self.selected_activity_id = activities[0]["activity_id"]
            self.latest_activity_id = activities[0]["activity_id"]

        if hasattr(self, "insight_text"):
            if self.current_period == "Day":
                self.insight_kicker.setText("Daily AI Insight")
            elif self.current_period == "Month":
                self.insight_kicker.setText("Monthly AI Insight")
            else:
                self.insight_kicker.setText("Weekly AI Insight")
            self.insight_text.setText(self._compute_period_insight(session_rows, start, end, tasks_by_id))

        self._clear_layout(self.activity_layout)
        self.activity_cards = {}
        if not activities:
            empty = QLabel("No activity yet for this period.")
            empty.setWordWrap(True)
            empty.setStyleSheet(
                f"color: {self.colors['sub']}; font-size: 12px; font-weight: 600;"
            )
            self.activity_layout.addWidget(empty)
            return

        if self.current_period == "Day":
            max_items = 12
        elif self.current_period == "Week":
            max_items = 24
        else:
            max_items = 45

        for activity in activities[:max_items]:
            card = self._make_activity_card(activity, activity["activity_id"] == self.selected_activity_id)
            card.clicked.connect(self._on_activity_selected)
            self.activity_layout.addWidget(card)
            self.activity_cards[activity["activity_id"]] = card

        self._update_activity_selection()

    def _on_activity_selected(self, activity_id):
        if self.selected_activity_id == activity_id:
            return
        self.selected_activity_id = activity_id
        self._update_activity_selection()

    def _update_activity_selection(self):
        for activity_id, card in self.activity_cards.items():
            self._apply_activity_card_style(card, activity_id == self.selected_activity_id)

    def _calc_efficiency(self, minutes):
        if minutes <= 0:
            return 0
        return min(100, 60 + int(minutes * 0.64))

    def _calc_focus_score(self, minutes):
        if minutes <= 0:
            return 0.0
        return min(9.9, 5.0 + minutes * 0.068)

    def _open_latest_report(self):
        if self.latest_activity_id:
            self.request_report.emit(self.latest_activity_id)

    def _open_report(self, activity_id):
        if activity_id:
            self.request_report.emit(activity_id)

    def _open_quick_stats(self, activity):
        if activity:
            self.request_quick_stats.emit(activity["activity_id"])

    def _parse_activity_id(self, activity):
        if not activity:
            return None, None
        activity_id = activity.get("activity_id") if isinstance(activity, dict) else None
        if not activity_id:
            return None, None
        if activity_id.startswith("task:"):
            try:
                return "task", int(activity_id.split(":", 1)[1])
            except Exception:
                return "task", None
        if activity_id.startswith("session:"):
            try:
                return "session", int(activity_id.split(":", 1)[1])
            except Exception:
                return "session", None
        return None, None

    def _confirm_delete_activity(self, activity):
        kind, item_id = self._parse_activity_id(activity)
        if kind is None or item_id is None:
            return
        if kind == "task":
            title = activity.get("title") if isinstance(activity, dict) else None
            msg = f"Remove this task{f' ({title})' if title else ''}?"
            title_text = "Delete Task"
        else:
            msg = "Remove this session?"
            title_text = "Delete Session"
        confirm = QMessageBox.question(
            self,
            title_text,
            msg,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        if confirm != QMessageBox.StandardButton.Yes:
            return
        conn = get_db_connection()
        try:
            if kind == "task":
                conn.execute("DELETE FROM tasks WHERE id=?", (item_id,))
            else:
                conn.execute("DELETE FROM pomodoro_sessions WHERE id=?", (item_id,))
            conn.commit()
        finally:
            conn.close()
        self.refresh_history()

    def _make_activity_card(self, activity, selected):
        card = ActivityCard(activity["activity_id"])
        card.setObjectName("HistoryActivityCard")
        card.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        card.setCursor(Qt.CursorShape.PointingHandCursor)

        layout = QVBoxLayout(card)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(8)

        top_row = QHBoxLayout()
        top_row.setSpacing(12)

        is_session = activity["kind"] == "session"
        icon_text = "P" if is_session else "OK"
        icon_bg = "#f59e0b" if is_session else self.colors["primary"]

        icon = QLabel(icon_text)
        icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        icon.setFixedSize(32, 32)
        icon.setStyleSheet(
            f"background: {icon_bg}; color: white; border-radius: 16px; font-size: 10px; font-weight: 900;"
        )

        info_layout = QVBoxLayout()
        info_layout.setSpacing(3)
        title = QLabel(activity["title"])
        title.setStyleSheet(
            f"color: {self.colors['text']}; font-size: 13px; font-weight: 800;"
        )

        dt = activity.get("time")
        time_label = dt.strftime("%I:%M %p").lstrip("0") if dt else "recently"
        subtitle = QLabel(f"{activity['status']} - {time_label}")
        subtitle.setStyleSheet(
            f"color: {self.colors['sub']}; font-size: 10px; font-weight: 700;"
        )
        info_layout.addWidget(title)
        info_layout.addWidget(subtitle)

        duration_text = format_duration_minutes(activity["duration"]) if activity["duration"] else ""
        duration = QLabel(duration_text)
        duration.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignTop)
        duration.setStyleSheet(
            f"color: {self.colors['text']}; font-size: 11px; font-weight: 800;"
        )

        top_row.addWidget(icon)
        top_row.addLayout(info_layout)
        top_row.addStretch()
        top_row.addWidget(duration)

        layout.addLayout(top_row)

        detail_frame = QFrame()
        detail_frame.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        detail_frame.setStyleSheet(
            f"border-top: 1px solid {self.colors['primary_soft_border']};"
        )
        detail_layout = QHBoxLayout(detail_frame)
        detail_layout.setContentsMargins(0, 6, 0, 0)
        detail_layout.setSpacing(20)

        efficiency = self._calc_efficiency(activity["duration"])
        focus_score = self._calc_focus_score(activity["duration"])

        eff_col = QVBoxLayout()
        eff_label = QLabel("EFFICIENCY")
        eff_label.setStyleSheet(
            f"color: {self.colors['sub']}; font-size: 9px; font-weight: 800;"
        )
        eff_val = QLabel(f"{efficiency}%")
        eff_val.setStyleSheet(
            f"color: {self.colors['good']}; font-size: 11px; font-weight: 900;"
        )
        eff_col.addWidget(eff_label)
        eff_col.addWidget(eff_val)

        focus_col = QVBoxLayout()
        focus_label = QLabel("FOCUS SCORE")
        focus_label.setStyleSheet(
            f"color: {self.colors['sub']}; font-size: 9px; font-weight: 800;"
        )
        focus_val = QLabel(f"{focus_score:.1f}")
        focus_val.setStyleSheet(
            f"color: {self.colors['primary']}; font-size: 11px; font-weight: 900;"
        )
        focus_col.addWidget(focus_label)
        focus_col.addWidget(focus_val)

        view_btn = QPushButton("View Stats")
        view_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        view_btn.setStyleSheet(
            f"QPushButton {{ color: {self.colors['primary']}; background-color: transparent; border: none; font-size: 10px; font-weight: bold; }}"
        )
        view_btn.clicked.connect(lambda: self._open_quick_stats(activity))

        delete_btn = QPushButton("Delete")
        delete_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        delete_btn.setStyleSheet(
            f"QPushButton {{ color: {self.colors['bad']}; background-color: transparent; border: none; font-size: 10px; font-weight: bold; }}"
        )
        delete_btn.clicked.connect(lambda: self._confirm_delete_activity(activity))

        detail_layout.addLayout(eff_col)
        detail_layout.addLayout(focus_col)
        detail_layout.addStretch()
        detail_layout.addWidget(view_btn)
        detail_layout.addWidget(delete_btn)

        card._detail_frame = detail_frame
        layout.addWidget(detail_frame)
        self._apply_activity_card_style(card, selected)

        return card

    def _apply_activity_card_style(self, card, selected):
        border_color = self.colors["card_selected_border"] if selected else self.colors["border"]
        bg_color = self.colors["card_selected"] if selected else self.colors["card"]
        card.setStyleSheet(
            f"QFrame#HistoryActivityCard {{ background: {bg_color}; border: 1px solid {border_color}; border-radius: 14px; }}"
        )
        detail_frame = getattr(card, "_detail_frame", None)
        if detail_frame:
            detail_frame.setVisible(selected)

    def restore_task(self, t_id):
        conn = get_db_connection()
        conn.execute("UPDATE tasks SET is_completed = 0, completed_at = NULL WHERE id = ?", (t_id,))
        conn.commit()
        conn.close()

        self.task_restored.emit()
        self.refresh_history()
