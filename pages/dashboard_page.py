from datetime import datetime, timedelta
from math import ceil

from PyQt6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QFrame,
    QPushButton,
    QScrollArea,
    QProgressBar,
    QSizePolicy,
    QGridLayout,
    QLayout
)
from PyQt6.QtCore import Qt, QTimer, QPointF, QRectF, pyqtSignal
from PyQt6.QtGui import QPainter, QPainterPath, QPen, QLinearGradient, QColor
from database.db_manager import get_db_connection
from resources.theme import get_theme, FONT_FAMILY
from resources.time_format import format_duration_minutes
from resources.priority import (
    normalize_priority,
    priority_weight,
    quadrant_from_flags,
    PRIORITY_LEVELS,
)
from resources.task_types import TASK_TYPES, TASK_TYPE_COLORS, UNCATEGORIZED_LABEL, normalize_task_type


def _heatmap_time_labels_2h():
    labels = []
    for hour in range(0, 24, 2):
        end = hour + 2
        end_label = "24:00" if end == 24 else f"{end:02d}:00"
        labels.append(f"{hour:02d}:00-{end_label}")
    return labels


class FocusConsistencyChart(QFrame):
    def __init__(self):
        super().__init__()
        self.values = [0.2] * 7
        self.labels = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
        self.colors = {}
        self.is_dark = False
        self.setMinimumHeight(180)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)

    def set_data(self, values, labels=None):
        if values:
            padded = list(values) + [0.0] * (7 - len(values))
            self.values = padded[:7]
        if labels:
            padded = list(labels) + [""] * (7 - len(labels))
            self.labels = padded[:7]
        self.update()

    def set_theme(self, colors, theme):
        self.colors = colors or {}
        self.is_dark = theme == "Dark"
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        try:
            if not self.values:
                return

            rect = QRectF(self.rect())
            left_pad = 12
            right_pad = 12
            top_pad = 12
            bottom_pad = 28
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
            if max_val > 1:
                values = [v / max_val for v in values]

            points = []
            span = 6 if len(values) > 1 else 1
            for idx, value in enumerate(values):
                x = chart_rect.left() + (chart_rect.width() / span) * idx
                y = chart_rect.bottom() - (value * chart_rect.height())
                points.append(QPointF(x, y))

            path = QPainterPath()
            path.moveTo(points[0])
            for i in range(1, len(points)):
                path.lineTo(points[i])

            fill_path = QPainterPath(path)
            fill_path.lineTo(chart_rect.right(), chart_rect.bottom())
            fill_path.lineTo(chart_rect.left(), chart_rect.bottom())
            fill_path.closeSubpath()

            line_color = QColor(self.colors.get("accent", "#38BDF8"))
            gradient = QLinearGradient(chart_rect.topLeft(), chart_rect.bottomLeft())
            gradient.setColorAt(0, QColor(line_color.red(), line_color.green(), line_color.blue(), 110))
            gradient.setColorAt(1, QColor(line_color.red(), line_color.green(), line_color.blue(), 0))

            painter.fillPath(fill_path, gradient)

            pen = QPen(line_color, 4)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            painter.setPen(pen)
            painter.drawPath(path)

            # Markers per day (to show exact zeros)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(line_color)
            for pt in points:
                painter.drawEllipse(pt, 4, 4)

            label_color = QColor(self.colors.get("sub", "#94A3B8"))
            painter.setPen(label_color)
            font = painter.font()
            font.setPointSize(9)
            font.setBold(True)
            painter.setFont(font)

            for idx, label in enumerate(self.labels):
                if not label:
                    continue
                metrics = painter.fontMetrics()
                text_width = metrics.horizontalAdvance(label)
                x = points[idx].x() - text_width / 2
                y = rect.bottom() - 6
                painter.drawText(QPointF(x, y), label)
        finally:
            painter.end()


class HeatmapWidget(QFrame):
    def __init__(self):
        super().__init__()
        self.values = [[0] * 12 for _ in range(3)]
        self.day_labels = ["M", "T", "W"]
        self.time_labels = _heatmap_time_labels_2h()
        self.colors = {}
        self.setMinimumHeight(180)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)

    def set_data(self, values, day_labels=None, time_labels=None):
        if values:
            self.values = values
        if day_labels:
            self.day_labels = list(day_labels)
        if time_labels:
            self.time_labels = list(time_labels)
        self.update()

    def set_theme(self, colors, theme):
        self.colors = colors or {}
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        try:
            rows = len(self.values)
            cols = len(self.values[0]) if rows else 0
            if rows == 0 or cols == 0:
                return

            rect = QRectF(self.rect())
            right_pad = 16
            top_pad = 24
            bottom_pad = 34
            legend_height = 18

            label_color = QColor(self.colors.get("sub", "#94A3B8"))
            label_font = painter.font()
            label_font.setPointSize(9)
            label_font.setBold(True)

            day_font = painter.font()
            day_font.setPointSize(10)
            day_font.setBold(True)

            painter.setFont(day_font)
            metrics = painter.fontMetrics()
            max_day_label_w = max(
                (metrics.horizontalAdvance(label) for label in self.day_labels[:rows] if label),
                default=0
            )
            label_gap = 10
            left_pad = max(28, max_day_label_w + label_gap + 2)

            grid_rect = QRectF(
                rect.left() + left_pad,
                rect.top() + top_pad,
                rect.width() - left_pad - right_pad,
                rect.height() - top_pad - bottom_pad
            )

            gap = 8
            cell_w = (grid_rect.width() - gap * (cols - 1)) / cols
            cell_h = (grid_rect.height() - gap * (rows - 1)) / rows

            painter.setPen(label_color)
            label_step = 1
            if cell_w < 60:
                label_step = 2
            if cell_w < 42:
                label_step = 3
            if cell_w < 30:
                label_step = 4
            if cell_w < 44:
                label_font.setPointSize(8)
            if cell_w < 32:
                label_font.setPointSize(7)
            painter.setFont(label_font)

            use_labels = self.time_labels
            if cell_w < 70:
                short_labels = []
                for label in self.time_labels:
                    parts = label.split("-")
                    if len(parts) == 2:
                        start = parts[0][:2]
                        end = parts[1][:2]
                        short_labels.append(f"{start}-{end}")
                    else:
                        short_labels.append(label)
                use_labels = short_labels
            if cell_w < 50:
                tiny_labels = []
                for label in self.time_labels:
                    parts = label.split("-")
                    if parts:
                        tiny_labels.append(parts[0][:2])
                    else:
                        tiny_labels.append(label)
                use_labels = tiny_labels

            # Time labels
            for c, label in enumerate(use_labels[:cols]):
                if label_step > 1 and (c % label_step) != 0:
                    continue
                text_width = painter.fontMetrics().horizontalAdvance(label)
                x = grid_rect.left() + c * (cell_w + gap) + (cell_w - text_width) / 2
                y = rect.top() + 16
                painter.drawText(QPointF(x, y), label)

            # Day labels
            day_label_color = QColor(self.colors.get("text", "#0B132B"))
            painter.setPen(day_label_color)
            painter.setFont(day_font)
            for r, label in enumerate(self.day_labels[:rows]):
                label_rect = QRectF(
                    rect.left(),
                    grid_rect.top() + r * (cell_h + gap),
                    left_pad - label_gap,
                    cell_h
                )
                painter.drawText(
                    label_rect,
                    int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight),
                    label
                )

            heat_colors = [
                QColor(self.colors.get("heat0", "#1F2937")),
                QColor(self.colors.get("heat1", "#0B4A6E")),
                QColor(self.colors.get("heat2", "#0284C7")),
                QColor(self.colors.get("heat3", "#38BDF8")),
            ]

            for r in range(rows):
                for c in range(cols):
                    value = self.values[r][c]
                    color = heat_colors[max(0, min(value, 3))]
                    x = grid_rect.left() + c * (cell_w + gap)
                    y = grid_rect.top() + r * (cell_h + gap)
                    cell_rect = QRectF(x, y, cell_w, cell_h)
                    painter.setPen(Qt.PenStyle.NoPen)
                    painter.setBrush(color)
                    painter.drawRoundedRect(cell_rect, 6, 6)

            # Legend
            legend_y = rect.bottom() - legend_height
            painter.setPen(label_color)
            painter.drawText(QPointF(rect.left() + left_pad - 12, legend_y + 12), "Lower priority focus")

            legend_x = rect.left() + left_pad + 120
            box_size = 12
            for idx, color in enumerate(heat_colors):
                box_rect = QRectF(legend_x + idx * (box_size + 6), legend_y, box_size, box_size)
                painter.setBrush(color)
                painter.setPen(Qt.PenStyle.NoPen)
                painter.drawRoundedRect(box_rect, 3, 3)

            painter.setPen(label_color)
            painter.drawText(
                QPointF(legend_x + len(heat_colors) * (box_size + 6) + 8, legend_y + 12),
                "Higher priority focus"
            )
        finally:
            painter.end()


class VelocityChartWidget(QFrame):
    def __init__(self):
        super().__init__()
        self.counts = [0] * 7
        self.capacity = [0] * 7
        self.labels = ["M", "T", "W", "T", "F", "S", "S"]
        self.point_value = 0
        self.point_index = 3
        self.colors = {}
        self.setMinimumHeight(200)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)

    def set_data(self, counts, capacity, labels=None, point_value=0, point_index=3):
        if counts:
            self.counts = list(counts) + [0] * (7 - len(counts))
            self.counts = self.counts[:7]
        if capacity:
            self.capacity = list(capacity) + [0] * (7 - len(capacity))
            self.capacity = self.capacity[:7]
        if labels:
            self.labels = list(labels) + [""] * (7 - len(labels))
            self.labels = self.labels[:7]
        self.point_value = point_value
        self.point_index = max(0, min(point_index, 6))
        self.update()

    def set_theme(self, colors, theme):
        self.colors = colors or {}
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        try:
            rect = QRectF(self.rect())
            left_pad = 36
            right_pad = 16
            top_pad = 18
            bottom_pad = 34
            chart_rect = QRectF(
                rect.left() + left_pad,
                rect.top() + top_pad,
                rect.width() - left_pad - right_pad,
                rect.height() - top_pad - bottom_pad
            )

            max_val = max(max(self.counts), max(self.capacity), self.point_value, 1)

            grid_color = QColor(self.colors.get("chart_grid", "#1F2937"))
            painter.setPen(QPen(grid_color, 1))
            for i in range(3):
                y = chart_rect.top() + (chart_rect.height() / 2) * i
                painter.drawLine(QPointF(chart_rect.left(), y), QPointF(chart_rect.right(), y))

            # Y-axis labels (max, mid, zero)
            label_color = QColor(self.colors.get("sub", "#94A3B8"))
            painter.setPen(label_color)
            font = painter.font()
            font.setPointSize(9)
            font.setBold(True)
            painter.setFont(font)
            metrics = painter.fontMetrics()
            tick_values = [max_val, max_val / 2, 0]
            for i, value in enumerate(tick_values):
                display = value
                if isinstance(display, float) and display.is_integer():
                    display = int(display)
                label = f"{display}"
                y = chart_rect.top() + (chart_rect.height() / 2) * i
                text_width = metrics.horizontalAdvance(label)
                text_x = rect.left() + max(0, left_pad - text_width - 6)
                text_y = y + metrics.ascent() / 2
                painter.drawText(QPointF(text_x, text_y), label)

            def to_point(idx, value):
                x = chart_rect.left() + (chart_rect.width() / 6) * idx
                y = chart_rect.bottom() - (value / max_val) * chart_rect.height()
                return QPointF(x, y)

            # Actual line (daily sessions)
            actual_color = QColor(self.colors.get("accent", "#38BDF8"))
            actual_pen = QPen(actual_color, 3)
            actual_pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            actual_pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            painter.setPen(actual_pen)

            actual_path = QPainterPath()
            actual_points = [to_point(i, v) for i, v in enumerate(self.counts)]
            actual_path.moveTo(actual_points[0])
            for i in range(1, len(actual_points)):
                actual_path.lineTo(actual_points[i])
            painter.drawPath(actual_path)

            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(actual_color)
            for pt in actual_points:
                painter.drawEllipse(pt, 4, 4)

            # Average line (7-day avg)
            avg_color = QColor(self.colors.get("capacity", self.colors.get("sub", "#94A3B8")))
            avg_pen = QPen(avg_color, 2, Qt.PenStyle.DotLine)
            avg_pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            painter.setPen(avg_pen)

            avg_path = QPainterPath()
            avg_points = [to_point(i, v) for i, v in enumerate(self.capacity)]
            avg_path.moveTo(avg_points[0])
            for i in range(1, len(avg_points)):
                avg_path.lineTo(avg_points[i])
            painter.drawPath(avg_path)

            if self.point_value:
                painter.setPen(avg_color)
                font = painter.font()
                font.setPointSize(9)
                font.setBold(True)
                painter.setFont(font)
                avg_value = self.point_value
                if isinstance(avg_value, float) and avg_value.is_integer():
                    avg_value = int(avg_value)
                avg_text = f"Avg {avg_value}"
                metrics = painter.fontMetrics()
                text_width = metrics.horizontalAdvance(avg_text)
                avg_y = chart_rect.bottom() - (self.point_value / max_val) * chart_rect.height()
                x = chart_rect.right() - text_width
                y = max(chart_rect.top() + 10, min(chart_rect.bottom() - 4, avg_y - 6))
                painter.drawText(QPointF(x, y), avg_text)

            painter.setPen(label_color)
            painter.setFont(font)
            for idx, label in enumerate(self.labels):
                text_width = painter.fontMetrics().horizontalAdvance(label)
                x = chart_rect.left() + (chart_rect.width() / 6) * idx - text_width / 2
                y = rect.bottom() - 6
                painter.drawText(QPointF(x, y), label)
        finally:
            painter.end()

class PomodoroBarChart(QFrame):
    def __init__(self):
        super().__init__()
        self.values = [0] * 7
        self.labels = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
        self.colors = {}
        self.setMinimumHeight(180)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)

    def set_data(self, values, labels=None):
        if values:
            padded = list(values) + [0] * (7 - len(values))
            self.values = padded[:7]
        if labels:
            padded = list(labels) + [""] * (7 - len(labels))
            self.labels = padded[:7]
        self.update()

    def set_theme(self, colors, theme):
        self.colors = colors or {}
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        try:
            rect = QRectF(self.rect())
            left_pad = 16
            right_pad = 16
            top_pad = 12
            bottom_pad = 26
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

            bar_count = len(values)
            gap = 8
            bar_w = (chart_rect.width() - gap * (bar_count - 1)) / bar_count

            accent = QColor(self.colors.get("accent", "#38BDF8"))
            accent2 = QColor(self.colors.get("accent2", "#22D3EE"))
            grid = QColor(self.colors.get("chart_grid", "#1F2937"))

            painter.setPen(QPen(grid, 1))
            for i in range(1, 3):
                y = chart_rect.top() + (chart_rect.height() / 3) * i
                painter.drawLine(QPointF(chart_rect.left(), y), QPointF(chart_rect.right(), y))

            for idx, value in enumerate(values):
                x = chart_rect.left() + idx * (bar_w + gap)
                h = (value / max_val) * chart_rect.height()
                y = chart_rect.bottom() - h
                gradient = QLinearGradient(QPointF(x, y), QPointF(x, chart_rect.bottom()))
                gradient.setColorAt(0, accent2)
                gradient.setColorAt(1, accent)
                painter.setBrush(gradient)
                painter.setPen(Qt.PenStyle.NoPen)
                painter.drawRoundedRect(QRectF(x, y, bar_w, h), 6, 6)

            label_color = QColor(self.colors.get("sub", "#94A3B8"))
            painter.setPen(label_color)
            font = painter.font()
            font.setPointSize(9)
            font.setBold(True)
            painter.setFont(font)
            for idx, label in enumerate(self.labels):
                if not label:
                    continue
                text_width = painter.fontMetrics().horizontalAdvance(label)
                x = chart_rect.left() + idx * (bar_w + gap) + (bar_w - text_width) / 2
                y = rect.bottom() - 6
                painter.drawText(QPointF(x, y), label)
        finally:
            painter.end()


class PriorityBreakdownChart(QFrame):
    def __init__(self):
        super().__init__()
        self.values = {level: 0 for level in PRIORITY_LEVELS}
        self.labels = ["High", "Medium", "Low", "Too Low"]
        self.colors = {}
        self.setMinimumHeight(170)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)

    def set_data(self, values):
        if values:
            for level in PRIORITY_LEVELS:
                self.values[level] = int(values.get(level, 0) or 0)
        self.update()

    def set_theme(self, colors, theme):
        self.colors = colors or {}
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        try:
            rect = QRectF(self.rect())
            left_pad = 90
            right_pad = 20
            top_pad = 16
            row_gap = 14
            bar_h = 16

            values = [self.values.get(level, 0) for level in PRIORITY_LEVELS]
            max_val = max(values) if values else 0

            label_color = QColor(self.colors.get("text", "#0B132B"))
            sub_color = QColor(self.colors.get("sub", "#94A3B8"))
            painter.setPen(label_color)
            font = painter.font()
            font.setPointSize(9)
            font.setBold(True)
            painter.setFont(font)

            if max_val == 0:
                painter.setPen(sub_color)
                painter.drawText(QPointF(rect.left() + 10, rect.center().y()), "No priority sessions yet.")
                return

            color_map = {
                "high": QColor(self.colors.get("bad", "#ef4444")),
                "medium": QColor(self.colors.get("accent", "#3078CD")),
                "low": QColor(self.colors.get("accent2", "#82AFF2")),
                "too low": QColor(self.colors.get("border", "#94a3b8")),
            }

            value_font = painter.font()
            value_font.setPointSize(10)
            value_font.setBold(True)
            painter.setFont(value_font)
            metrics = painter.fontMetrics()
            padding_x = 6
            padding_y = 3
            pill_gap = 12
            right_margin = 10

            max_pill_w = 0
            for level in PRIORITY_LEVELS:
                value = self.values.get(level, 0)
                value_text = format_duration_minutes(value)
                pill_w = metrics.horizontalAdvance(value_text) + padding_x * 2
                if pill_w > max_pill_w:
                    max_pill_w = pill_w

            max_chart = rect.width() - left_pad - right_pad
            reserve = max_pill_w + pill_gap + right_margin
            chart_w = max_chart - reserve
            if chart_w < 40:
                chart_w = max(20, max_chart - right_margin)

            for idx, level in enumerate(PRIORITY_LEVELS):
                value = self.values.get(level, 0)
                y = rect.top() + top_pad + idx * (bar_h + row_gap)
                bar_w = 0 if max_val == 0 else (value / max_val) * chart_w
                label = self.labels[idx]

                painter.setPen(label_color)
                painter.drawText(QPointF(rect.left() + 8, y + bar_h), label)

                bar_rect = QRectF(rect.left() + left_pad, y, max(4, bar_w), bar_h)
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(color_map.get(level, sub_color))
                painter.drawRoundedRect(bar_rect, 6, 6)

                value_text = format_duration_minutes(value)
                painter.setFont(value_font)
                text_width = metrics.horizontalAdvance(value_text)
                pill_w = text_width + padding_x * 2
                pill_h = max(bar_h + 2, metrics.height() + padding_y * 2)
                pill_y = y + (bar_h - pill_h) / 2
                outside_x = rect.left() + left_pad + chart_w + pill_gap
                max_outside = rect.right() - pill_w - 10
                if outside_x > max_outside:
                    outside_x = max_outside
                pill_rect = QRectF(outside_x, pill_y, pill_w, pill_h)

                pill_color = QColor(color_map.get(level, sub_color))
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(pill_color)
                painter.drawRoundedRect(pill_rect, 7, 7)

                text_y = pill_y + (pill_h + metrics.ascent() - metrics.descent()) / 2
                painter.setPen(QColor("#ffffff"))
                painter.drawText(QPointF(outside_x + padding_x, text_y), value_text)
        finally:
            painter.end()


class TypeBreakdownChart(QFrame):
    def __init__(self):
        super().__init__()
        self.order = TASK_TYPES + [UNCATEGORIZED_LABEL]
        self.values = {label: 0 for label in self.order}
        self.colors = {}
        self.is_dark = False
        self.setMinimumHeight(200)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)

    def set_data(self, values):
        if values:
            for label in self.order:
                self.values[label] = int(values.get(label, 0) or 0)
        self.update()

    def set_theme(self, colors, theme):
        self.colors = colors or {}
        self.is_dark = theme == "Dark"
        self.update()

    def _color_for_type(self, label):
        hex_color = TASK_TYPE_COLORS.get(label)
        if hex_color:
            return QColor(hex_color)
        return QColor(self.colors.get("accent2", "#82AFF2"))

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        try:
            rect = QRectF(self.rect())
            if not self.order:
                return

            values = [self.values.get(label, 0) for label in self.order]
            max_val = max(values) if values else 0

            label_font = painter.font()
            label_font.setPointSize(9)
            label_font.setBold(True)
            painter.setFont(label_font)
            label_metrics = painter.fontMetrics()
            max_label_w = max(
                (label_metrics.horizontalAdvance(label) for label in self.order),
                default=60,
            )

            left_pad = max(80, max_label_w + 14)
            right_pad = 10
            top_pad = 12
            bottom_pad = 8

            value_font = painter.font()
            value_font.setPointSize(9)
            value_font.setBold(True)
            painter.setFont(value_font)
            value_metrics = painter.fontMetrics()
            value_text_w = 0
            for value in values:
                value_text = format_duration_minutes(value)
                value_text_w = max(value_text_w, value_metrics.horizontalAdvance(value_text))

            value_gap = 10
            chart_w = rect.width() - left_pad - right_pad - value_text_w - value_gap
            chart_w = max(30, chart_w)

            available_h = rect.height() - top_pad - bottom_pad
            rows = len(self.order)
            row_h = available_h / max(1, rows)
            bar_h = min(16, max(8, row_h * 0.55))
            row_gap = max(6, row_h - bar_h)

            label_color = QColor(self.colors.get("text", "#113356"))
            value_color = QColor(self.colors.get("sub", "#94A3B8"))
            track_color = QColor(self.colors.get("border", "#BAD2E0"))
            track_color.setAlpha(80)

            if max_val == 0:
                painter.setPen(value_color)
                painter.drawText(
                    QPointF(rect.left() + 10, rect.center().y()),
                    "No type sessions yet.",
                )
                return

            for idx, label in enumerate(self.order):
                value = self.values.get(label, 0)
                y = rect.top() + top_pad + idx * (bar_h + row_gap)
                bar_x = rect.left() + left_pad

                # Label
                painter.setPen(label_color)
                painter.setFont(label_font)
                painter.drawText(QPointF(rect.left() + 8, y + bar_h), label)

                # Track
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(track_color)
                track_rect = QRectF(bar_x, y, chart_w, bar_h)
                painter.drawRoundedRect(track_rect, 6, 6)

                # Bar
                bar_w = 0 if max_val == 0 else (value / max_val) * chart_w
                if value > 0:
                    bar_w = max(3, bar_w)
                bar_rect = QRectF(bar_x, y, bar_w, bar_h)
                painter.setBrush(self._color_for_type(label))
                painter.drawRoundedRect(bar_rect, 6, 6)

                # Value text
                value_text = format_duration_minutes(value)
                painter.setFont(value_font)
                painter.setPen(value_color if value == 0 else label_color)
                text_x = rect.right() - right_pad - value_metrics.horizontalAdvance(value_text)
                painter.drawText(QPointF(text_x, y + bar_h), value_text)
        finally:
            painter.end()
class DashboardPage(QWidget):
    action_requested = pyqtSignal(str)

    def __init__(self):
        super().__init__()
        self.current_theme = "Light"
        self.cards = []
        self.section_titles = []
        self.labels_main = []
        self.labels_sub = []
        self.badges = []
        self.progress_bars = []
        self.energy_bars = []
        self.energy_label = None
        self.energy_note = None
        self.consistency_chart = None
        self.heatmap_chart = None
        self.velocity_chart = None
        self.consistency_labels_data = []
        self.heatmap_day_labels_data = []
        self.heatmap_time_labels_data = []
        self.velocity_day_labels_data = []
        self.refresh_button = None
        self.schedule_button = None
        self.header_status = None
        self.type_chart = None
        self._colors = {}
        self.pomo_minutes_week = 0
        self.pomo_sessions_week = 0
        self.pomo_minutes_total = 0
        self.pomo_sessions_total = 0
        self.pomo_avg_minutes = 0
        self.pomo_best_day_label = "-"
        self.pomo_best_day_minutes = 0
        self.pomo_day_minutes = [0, 0, 0, 0, 0, 0, 0]
        self.pomo_day_labels = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
        self.pomo_day_sessions = [0, 0, 0, 0, 0, 0, 0]
        self.pomo_day_sessions = [0, 0, 0, 0, 0, 0, 0]
        self.priority_chart = None
        self._debug_grid_once = False

        self.setObjectName("DashboardPage")
        self._set_default_metrics()
        self._build_ui()
        self.update_theme("Light")
        self._load_metrics_from_db()

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setStyleSheet(
            "QScrollArea { border: none; background: transparent; }"
            "QScrollArea QWidget { background: transparent; }"
            "QScrollArea::viewport { background: transparent; }"
        )

        self.container = QWidget()
        self.container.setObjectName("DashContainer")
        self.container.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.content = QVBoxLayout(self.container)
        self.content.setContentsMargins(32, 24, 32, 24)
        self.content.setSpacing(18)

        self.scroll.setWidget(self.container)
        root.addWidget(self.scroll)

        # Header
        header = QFrame()
        header.setObjectName("DashHeader")
        header.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(20, 18, 20, 18)
        header_layout.setSpacing(12)

        header_left = QVBoxLayout()
        header_left.setSpacing(6)

        self.kicker = QLabel("AI DASHBOARD")
        self.title = QLabel("Focus Overview")
        self.subtitle = QLabel("Weekly insights to keep you in flow")

        self.kicker.setObjectName("DashKicker")
        self.title.setObjectName("DashTitle")
        self.subtitle.setObjectName("DashSubtitle")

        header_left.addWidget(self.kicker)
        header_left.addWidget(self.title)
        header_left.addWidget(self.subtitle)

        header_layout.addLayout(header_left)
        header_layout.addStretch()

        self.header_chip = QLabel("Live")
        self.header_chip.setObjectName("DashChip")
        self.header_chip.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.header_chip.setFixedSize(60, 26)
        self.header_chip.setProperty("state", "live")
        self.header_status = QLabel("Updated just now")
        self.header_status.setObjectName("DashStatus")

        header_right = QVBoxLayout()
        header_right.setSpacing(6)
        header_right.addWidget(self.header_chip, alignment=Qt.AlignmentFlag.AlignRight)
        header_right.addWidget(self.header_status, alignment=Qt.AlignmentFlag.AlignRight)
        header_layout.addLayout(header_right)

        self.content.addWidget(header)

        # Top cards
        self.top_grid_container = QWidget()
        self.top_grid_container.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.top_grid_container.setStyleSheet("background: transparent;")
        self.top_grid_container.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.top_grid = QGridLayout(self.top_grid_container)
        self.top_grid.setSizeConstraint(QLayout.SizeConstraint.SetMinimumSize)
        self.top_grid.setSpacing(14)

        focus_card = self._make_card("Focus Score")
        focus_layout = focus_card.layout()
        focus_layout.setSpacing(8)

        self.focus_score = QLabel("82")
        self.focus_score.setObjectName("DashScore")
        self.labels_main.append(self.focus_score)

        self.focus_delta = QLabel("+5% vs last week")
        self.focus_delta.setObjectName("DashDelta")
        self.labels_sub.append(self.focus_delta)

        self.focus_bar = QProgressBar()
        self.focus_bar.setRange(0, 100)
        self.focus_bar.setValue(82)
        self.focus_bar.setTextVisible(False)
        self.focus_bar.setFixedHeight(10)
        self.progress_bars.append(self.focus_bar)

        focus_layout.addWidget(self.focus_score)
        focus_layout.addWidget(self.focus_bar)
        focus_layout.addWidget(self.focus_delta)
        focus_layout.addStretch()

        energy_card = self._make_card("Energy Level")
        energy_layout = energy_card.layout()
        energy_layout.setSpacing(10)

        energy_card.setVisible(False)

        pomodoro_card = self._make_card("Pomodoro")
        pomodoro_layout = pomodoro_card.layout()
        pomodoro_layout.setSpacing(8)

        self.pomo_minutes_label = QLabel("0 min")
        self.pomo_minutes_label.setObjectName("DashScore")
        self.labels_main.append(self.pomo_minutes_label)

        self.pomo_sessions_label = QLabel("0 sessions this week")
        self.labels_sub.append(self.pomo_sessions_label)

        pomodoro_layout.addWidget(self.pomo_minutes_label)
        pomodoro_layout.addWidget(self.pomo_sessions_label)
        self.pomo_avg_label = QLabel("Avg session: 0 min")
        self.labels_sub.append(self.pomo_avg_label)
        pomodoro_layout.addWidget(self.pomo_avg_label)

        self.pomo_best_label = QLabel("Best day: -")
        self.labels_sub.append(self.pomo_best_label)
        pomodoro_layout.addWidget(self.pomo_best_label)
        pomodoro_layout.addStretch()

        self._top_cards = [focus_card, pomodoro_card]
        self._layout_card_grid(self.top_grid, self._top_cards, min_width=240, max_cols=3)
        self.content.addWidget(self.top_grid_container)
        self._sync_grid_container_heights()

        # AI Insights
        ai_header = self._section_header("AI Insights")
        self.content.addLayout(ai_header)

        primary = QFrame()
        primary.setObjectName("DashPrimary")
        primary.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        primary_layout = QVBoxLayout(primary)
        primary_layout.setContentsMargins(20, 18, 20, 18)
        primary_layout.setSpacing(10)

        badge = QLabel("Recommended")
        badge.setObjectName("DashBadge")
        badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        badge.setFixedSize(110, 24)
        self.badges.append(badge)
        self.ai_badge = badge

        title = QLabel("Best time for deep work")
        title.setObjectName("DashCardTitle")
        self.labels_main.append(title)

        desc = QLabel(
            "High focus window starting at 10:30 AM. Block 1h 30m for design work."
        )
        desc.setWordWrap(True)
        self.labels_sub.append(desc)

        rec = QLabel("Recommendation: Start the day with a high-priority session.")
        rec.setObjectName("DashReco")
        rec.setWordWrap(True)
        self.labels_sub.append(rec)

        self.ai_primary_title = title
        self.ai_primary_desc = desc
        self.ai_reco_label = rec

        btn = QPushButton("Schedule Deep Work")
        btn.setObjectName("DashAction")
        btn.clicked.connect(self._on_schedule_clicked)
        self.schedule_button = btn

        primary_layout.addWidget(badge, alignment=Qt.AlignmentFlag.AlignLeft)
        primary_layout.addWidget(title)
        primary_layout.addWidget(desc)
        primary_layout.addWidget(rec)
        primary_layout.addStretch()
        primary_layout.addWidget(btn)

        secondary = QFrame()
        secondary.setObjectName("DashSecondary")
        secondary.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        secondary_layout = QHBoxLayout(secondary)
        secondary_layout.setContentsMargins(16, 14, 16, 14)
        secondary_layout.setSpacing(12)

        icon = QFrame()
        icon.setFixedSize(40, 40)
        icon.setObjectName("DashIcon")
        icon.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)

        sec_text = QVBoxLayout()
        sec_title = QLabel("Low energy task")
        sec_title.setObjectName("DashCardTitle")
        self.labels_main.append(sec_title)
        sec_desc = QLabel("Save inbox cleanup for 4:00 PM when energy dips.")
        sec_desc.setWordWrap(True)
        self.labels_sub.append(sec_desc)
        self.ai_secondary_title = sec_title
        self.ai_secondary_desc = sec_desc
        sec_text.addWidget(sec_title)
        sec_text.addWidget(sec_desc)

        secondary_layout.addWidget(icon)
        secondary_layout.addLayout(sec_text)
        secondary_layout.addStretch()

        self.content.addWidget(primary)
        self.content.addWidget(secondary)

        # Weekly Predictions
        weekly_header = self._section_header("Weekly Predictions")
        self.content.addLayout(weekly_header)

        completion = self._make_card("Estimated Project Completion")
        completion_layout = completion.layout()
        completion_layout.setSpacing(8)

        completion_row = QHBoxLayout()
        self.completion_label = QLabel("Fri, Oct 27")
        self.completion_label.setObjectName("DashAccent")
        self.labels_main.append(self.completion_label)
        completion_row.addWidget(self.completion_label)
        completion_row.addStretch()

        self.completion_bar = QProgressBar()
        self.completion_bar.setRange(0, 100)
        self.completion_bar.setValue(65)
        self.completion_bar.setTextVisible(False)
        self.completion_bar.setFixedHeight(10)
        self.progress_bars.append(self.completion_bar)

        self.completion_note = QLabel("65% predicted progress")
        self.labels_sub.append(self.completion_note)

        completion_layout.addLayout(completion_row)
        completion_layout.addWidget(self.completion_bar)
        completion_layout.addWidget(self.completion_note)

        consistency = self._make_card("Focus Consistency")
        consistency_layout = consistency.layout()
        consistency_layout.setSpacing(10)

        self.consistency_chart = FocusConsistencyChart()
        consistency_layout.addWidget(self.consistency_chart)

        type_card = self._make_card("Time by Type")
        type_layout = type_card.layout()
        type_layout.setSpacing(8)
        self.type_chart = TypeBreakdownChart()
        type_layout.addWidget(self.type_chart)

        heatmap = self._make_card("Priority Focus Heatmap")
        heatmap_layout = heatmap.layout()
        heatmap_layout.setSpacing(10)

        self.heatmap_chart = HeatmapWidget()
        heatmap_layout.addWidget(self.heatmap_chart)

        velocity = self._make_card("Pomodoro Sessions (7-Day)")
        velocity_layout = velocity.layout()
        velocity_layout.setSpacing(10)

        self.velocity_chart = VelocityChartWidget()
        velocity_layout.addWidget(self.velocity_chart)

        self.weekly_grid_container = QWidget()
        self.weekly_grid_container.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.weekly_grid_container.setStyleSheet("background: transparent;")
        self.weekly_grid_container.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.weekly_grid = QGridLayout(self.weekly_grid_container)
        self.weekly_grid.setSizeConstraint(QLayout.SizeConstraint.SetMinimumSize)
        self.weekly_grid.setSpacing(14)
        self._weekly_cards = [completion, consistency, type_card]
        self._layout_card_grid(self.weekly_grid, self._weekly_cards, min_width=320, max_cols=2)
        self.content.addWidget(self.weekly_grid_container)
        self._sync_grid_container_heights()

        pomodoro_trend = self._make_card("Pomodoro Trend")
        pomodoro_trend_layout = pomodoro_trend.layout()
        pomodoro_trend_layout.setSpacing(10)
        self.pomo_chart = PomodoroBarChart()
        pomodoro_trend_layout.addWidget(self.pomo_chart)
        self.content.addWidget(pomodoro_trend)

        priority_breakdown = self._make_card("Priority Breakdown")
        priority_layout = priority_breakdown.layout()
        priority_layout.setSpacing(10)
        self.priority_chart = PriorityBreakdownChart()
        priority_layout.addWidget(self.priority_chart)
        self.content.addWidget(priority_breakdown)

        self.content.addWidget(heatmap)
        self.content.addWidget(velocity)

        self.content.addStretch()
        QTimer.singleShot(0, self._sync_grid_container_heights)

    def _make_card(self, title_text):
        card = QFrame()
        card.setObjectName("DashCard")
        card.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        card.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        layout = QVBoxLayout(card)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(6)

        title = QLabel(title_text)
        title.setObjectName("DashCardTitle")
        self.labels_main.append(title)
        layout.addWidget(title)

        self.cards.append(card)
        return card

    def _layout_card_grid(self, layout, widgets, min_width=260, max_cols=None):
        if layout is None:
            return
        while layout.count():
            layout.takeAt(0)

        try:
            available = self.scroll.viewport().width()
        except Exception:
            available = self.width()
        margins = self.content.contentsMargins()
        available = max(1, available - margins.left() - margins.right())
        spacing = layout.spacing()
        cols = max(1, int((available + spacing) // (min_width + spacing)))
        if max_cols:
            cols = min(cols, max_cols)

        for idx, widget in enumerate(widgets):
            row = idx // cols
            col = idx % cols
            layout.addWidget(widget, row, col)

        for col in range(cols):
            layout.setColumnStretch(col, 1)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, "top_grid") and hasattr(self, "_top_cards"):
            self._layout_card_grid(self.top_grid, self._top_cards, min_width=240, max_cols=3)
        if hasattr(self, "weekly_grid") and hasattr(self, "_weekly_cards"):
            self._layout_card_grid(self.weekly_grid, self._weekly_cards, min_width=320, max_cols=2)
        self._sync_grid_container_heights()

    def _sync_grid_container_heights(self):
        top_hint = None
        top_fallback = None
        weekly_hint = None
        weekly_fallback = None
        top_count = None
        weekly_count = None
        if hasattr(self, "top_grid_container") and hasattr(self, "top_grid"):
            try:
                self.top_grid.activate()
                self.top_grid_container.adjustSize()
                top_hint = self.top_grid_container.sizeHint().height()
                try:
                    top_count = self.top_grid.count()
                except Exception:
                    top_count = None
                top_h = top_hint or 0
                if top_h <= 0:
                    top_fallback = self._grid_min_height(self.top_grid)
                    top_h = top_fallback
                if top_h:
                    self.top_grid_container.setMinimumHeight(top_h)
            except Exception:
                pass
        if hasattr(self, "weekly_grid_container") and hasattr(self, "weekly_grid"):
            try:
                self.weekly_grid.activate()
                self.weekly_grid_container.adjustSize()
                weekly_hint = self.weekly_grid_container.sizeHint().height()
                try:
                    weekly_count = self.weekly_grid.count()
                except Exception:
                    weekly_count = None
                weekly_h = weekly_hint or 0
                if weekly_h <= 0:
                    weekly_fallback = self._grid_min_height(self.weekly_grid)
                    weekly_h = weekly_fallback
                if weekly_h:
                    self.weekly_grid_container.setMinimumHeight(weekly_h)
            except Exception:
                pass

        self._debug_grid_once = True

    def _grid_min_height(self, layout):
        if not layout:
            return 0
        rows = {}
        try:
            count = layout.count()
        except Exception:
            return 0
        for i in range(count):
            try:
                item = layout.itemAt(i)
            except Exception:
                item = None
            if not item:
                continue
            try:
                row, col, rowspan, colspan = layout.getItemPosition(i)
            except Exception:
                continue
            widget = item.widget()
            if widget is None:
                continue
            try:
                hint_h = widget.sizeHint().height()
            except Exception:
                hint_h = 0
            rows[row] = max(rows.get(row, 0), hint_h)
        if not rows:
            return 0
        total = sum(rows.values())
        try:
            spacing = layout.spacing()
        except Exception:
            spacing = 0
        if len(rows) > 1:
            total += spacing * (len(rows) - 1)
        try:
            margins = layout.contentsMargins()
            total += margins.top() + margins.bottom()
        except Exception:
            pass
        return total

    def _section_header(self, title_text):
        row = QHBoxLayout()
        title = QLabel(title_text)
        title.setObjectName("DashSection")
        self.section_titles.append(title)
        row.addWidget(title)
        row.addStretch()
        if title_text == "AI Insights":
            btn = QPushButton("Refresh")
            btn.setObjectName("DashLink")
            btn.clicked.connect(self._on_refresh_clicked)
            self.refresh_button = btn
            row.addWidget(btn)
        return row

    def _set_default_metrics(self):
        self.focus_value = 0
        self.focus_delta_value = 0
        self.energy_values = [12, 12, 12, 12, 12]
        self.energy_level_text = "No data"
        self.energy_drop_text = "Need more history"
        self.pomo_minutes_week = 0
        self.pomo_sessions_week = 0
        self.pomo_minutes_total = 0
        self.pomo_sessions_total = 0
        self.pomo_avg_minutes = 0
        self.pomo_best_day_label = "-"
        self.pomo_best_day_minutes = 0
        self.pomo_day_minutes = [0, 0, 0, 0, 0, 0, 0]
        self.pomo_day_labels = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
        self.consistency_levels = [0, 0, 0, 0, 0, 0, 0]
        self.consistency_values = [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]
        self.consistency_labels_data = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
        self.heatmap_values = [
            [0] * 12,
            [0] * 12,
            [0] * 12
        ]
        self.heatmap_day_labels_data = ["Mon", "Tue", "Wed"]
        self.heatmap_time_labels_data = _heatmap_time_labels_2h()
        self.velocity_values = [4, 4, 4, 4, 4, 4, 4]
        self.velocity_counts = [0, 0, 0, 0, 0, 0, 0]
        self.velocity_capacity = [0, 0, 0, 0, 0, 0, 0]
        self.velocity_point_value = 0
        self.velocity_point_index = 3
        self.velocity_day_labels_data = ["M", "T", "W", "T", "F", "S", "S"]
        self.completion_value = 0
        self.completion_date = "No forecast"
        self.priority_minutes_week = {level: 0 for level in PRIORITY_LEVELS}
        self.priority_sessions_week = {level: 0 for level in PRIORITY_LEVELS}
        self.type_minutes = {label: 0 for label in (TASK_TYPES + [UNCATEGORIZED_LABEL])}
        self.ai_badge_text = "Recommended"
        self.ai_action_text = "Schedule Deep Work"
        self.ai_recommendation_text = "Start the day with a high-priority session."
        self.ai_primary_title_text = "Priority focus score"
        self.ai_primary_desc_text = "Add tasks to see weighted productivity insights."
        self.ai_secondary_title_text = "Balance check"
        self.ai_secondary_desc_text = "We will surface workload warnings here."

    def _apply_metrics(self):
        self.focus_score.setText(str(self.focus_value))
        self.focus_bar.setValue(self.focus_value)
        delta_prefix = "+" if self.focus_delta_value >= 0 else ""
        self.focus_delta.setText(f"{delta_prefix}{self.focus_delta_value}% vs last week")
        self.focus_delta.setProperty("trend", "up" if self.focus_delta_value >= 0 else "down")

        if self.energy_label is not None:
            self.energy_label.setText(self.energy_level_text)
        if self.energy_note is not None:
            self.energy_note.setText(self.energy_drop_text)
        if self.energy_bars:
            for bar, height in zip(self.energy_bars, self.energy_values):
                bar.setFixedHeight(height)

        if hasattr(self, "pomo_minutes_label"):
            self.pomo_minutes_label.setText(self._format_minutes(self.pomo_minutes_week))
        if hasattr(self, "pomo_sessions_label"):
            self.pomo_sessions_label.setText(f"{self.pomo_sessions_week} sessions this week")
        if hasattr(self, "pomo_avg_label"):
            self.pomo_avg_label.setText(f"Avg session: {self._format_minutes(self.pomo_avg_minutes)}")
        if hasattr(self, "pomo_best_label"):
            if self.pomo_best_day_minutes > 0:
                best_txt = f"Best day: {self.pomo_best_day_label} ({self._format_minutes(self.pomo_best_day_minutes)})"
            else:
                best_txt = "Best day: -"
            self.pomo_best_label.setText(best_txt)

        if self.type_chart:
            self.type_chart.set_data(self.type_minutes)

        self.completion_bar.setValue(self.completion_value)
        self.completion_label.setText(self.completion_date)
        self.completion_note.setText(f"{self.completion_value}% predicted progress")

        if self.consistency_chart:
            self.consistency_chart.set_data(self.consistency_values, self.consistency_labels_data)

        if self.heatmap_chart:
            self.heatmap_chart.set_data(
                self.heatmap_values,
                self.heatmap_day_labels_data,
                self.heatmap_time_labels_data
            )

        if self.velocity_chart:
            counts = self.velocity_counts if self.velocity_counts else self.velocity_values
            self.velocity_chart.set_data(
                counts,
                self.velocity_capacity if self.velocity_capacity else counts,
                self.velocity_day_labels_data,
                self.velocity_point_value,
                self.velocity_point_index
            )
        if hasattr(self, "pomo_chart") and self.pomo_chart:
            self.pomo_chart.set_data(self.pomo_day_minutes, self.pomo_day_labels)
        if hasattr(self, "priority_chart") and self.priority_chart:
            self.priority_chart.set_data(self.priority_minutes_week)

        if hasattr(self, "ai_primary_title"):
            self.ai_primary_title.setText(self.ai_primary_title_text)
        if hasattr(self, "ai_primary_desc"):
            self.ai_primary_desc.setText(self.ai_primary_desc_text)
        if hasattr(self, "ai_secondary_title"):
            self.ai_secondary_title.setText(self.ai_secondary_title_text)
        if hasattr(self, "ai_secondary_desc"):
            self.ai_secondary_desc.setText(self.ai_secondary_desc_text)
        if hasattr(self, "ai_reco_label"):
            self.ai_reco_label.setText(self.ai_recommendation_text)
        if hasattr(self, "ai_badge"):
            self.ai_badge.setText(self.ai_badge_text)
        if hasattr(self, "schedule_button"):
            self.schedule_button.setText(self.ai_action_text)

        self._apply_dynamic_styles()

    def _on_refresh_clicked(self):
        self._load_metrics_from_db()

    def refresh_dashboard(self):
        """Public method to refresh dashboard metrics (used by MainApp)."""
        self._load_metrics_from_db()

    def _on_schedule_clicked(self):
        action = (self.ai_action_text or (self.schedule_button.text() if self.schedule_button else "") or "").strip()
        self.header_chip.setProperty("state", "saved")
        self.header_chip.setText("Saved")
        if action == "Plan Recovery":
            status = "Recovery break ready"
        elif action == "Start a Pomodoro":
            status = "Pomodoro opened"
        elif action in ("Schedule Deep Work", "Block High Priority"):
            status = "Tasks opened"
        else:
            status = "Action opened"
        self._set_status(status)
        self._apply_dynamic_styles()
        QTimer.singleShot(2200, self._reset_header_chip)
        try:
            self.action_requested.emit(action)
        except Exception:
            pass

    def _reset_header_chip(self):
        self.header_chip.setProperty("state", "live")
        self.header_chip.setText("Live")
        self._set_status("Ready for the next action")
        self._apply_dynamic_styles()

    def _set_status(self, text):
        if self.header_status:
            self.header_status.setText(text)

    def _format_minutes(self, minutes):
        return format_duration_minutes(minutes)

    def _parse_date(self, value):
        if not value:
            return None, False
        text = str(value).strip()
        if not text:
            return None, False
        text = text.split(".")[0]
        if "T" in text:
            text = text.replace("T", " ")
        formats = ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d")
        for fmt in formats:
            try:
                parsed = datetime.strptime(text, fmt)
                return parsed, fmt != "%Y-%m-%d"
            except ValueError:
                continue
        return None, False

    def _load_metrics_from_db(self):
        tasks = []
        select_cols = []
        conn = get_db_connection()
        try:
            info = conn.execute("PRAGMA table_info(tasks)").fetchall()
            if not info:
                self._set_default_metrics()
                self._apply_metrics()
                self._set_status("No history yet")
                return
            cols = {row[1] for row in info}
            base_cols = [
                "id",
                "title",
                "priority",
                "created_date",
                "due_date",
                "is_completed",
                "completed_at",
                "is_urgent",
                "is_important",
            ]
            select_cols = [col for col in base_cols if col in cols]
            if not select_cols:
                self._set_default_metrics()
                self._apply_metrics()
                self._set_status("No history yet")
                return
            rows = conn.execute(f"SELECT {', '.join(select_cols)} FROM tasks").fetchall()
        except Exception as exc:
            print("DB Error (Dashboard):", exc)
            rows = []
        finally:
            conn.close()

        for row in rows:
            data = {}
            for idx, col in enumerate(select_cols):
                data[col] = row[idx]
            tasks.append(data)

        if not tasks:
            self._set_default_metrics()
            now = datetime.now().strftime("%I:%M %p").lstrip("0")
            self._set_status(f"Updated {now}")
            self.header_chip.setProperty("state", "live")
            self.header_chip.setText("Live")
            self._load_pomodoro_metrics()
            self._apply_metrics()
            return

        today = datetime.now().date()
        last_7_start = today - timedelta(days=6)
        prev_7_start = today - timedelta(days=13)
        prev_7_end = today - timedelta(days=7)

        created_dates = []
        completed_dates = []
        completed_times = []
        completed_time_prios = []
        completed_day_weights = {}
        completed_total = 0
        priority_counts = {level: 0 for level in PRIORITY_LEVELS}
        priority_completed = {level: 0 for level in PRIORITY_LEVELS}
        weight_total = 0
        weight_completed = 0
        weight_completed_last7 = 0
        weight_completed_prev7 = 0
        tasks_by_id = {}

        for task in tasks:
            created_dt, _ = self._parse_date(task.get("created_date"))
            if not created_dt:
                created_dt, _ = self._parse_date(task.get("due_date"))
            if created_dt:
                created_dates.append(created_dt.date())

            completed_dt = None
            completed_has_time = False
            completed_day = None
            is_completed = int(task.get("is_completed") or 0) == 1
            if is_completed:
                completed_total += 1
                completed_dt, completed_has_time = self._parse_date(task.get("completed_at"))
                if completed_dt:
                    completed_dates.append(completed_dt.date())
                    completed_day = completed_dt.date()
                    if completed_has_time:
                        completed_times.append(completed_dt)
                else:
                    if created_dt:
                        completed_dates.append(created_dt.date())
                        completed_day = created_dt.date()

            prio = normalize_priority(task.get("priority"))
            if prio not in PRIORITY_LEVELS:
                prio = quadrant_from_flags(task.get("is_urgent"), task.get("is_important"))
            if prio not in PRIORITY_LEVELS:
                prio = "too low"
            tasks_by_id[task.get("id")] = prio
            priority_counts[prio] += 1
            weight = priority_weight(prio)
            weight_total += weight
            if is_completed:
                priority_completed[prio] += 1
                weight_completed += weight
                if completed_dt and last_7_start <= completed_dt.date() <= today:
                    weight_completed_last7 += weight
                elif completed_dt and prev_7_start <= completed_dt.date() <= prev_7_end:
                    weight_completed_prev7 += weight
                if completed_day:
                    completed_day_weights[completed_day] = completed_day_weights.get(completed_day, 0) + weight
                if completed_dt and completed_has_time:
                    completed_time_prios.append((completed_dt, prio))

        created_last7 = sum(1 for d in created_dates if last_7_start <= d <= today)
        created_prev7 = sum(1 for d in created_dates if prev_7_start <= d <= prev_7_end)
        completed_last7 = sum(1 for d in completed_dates if last_7_start <= d <= today)
        completed_prev7 = sum(1 for d in completed_dates if prev_7_start <= d <= prev_7_end)

        total_tasks = len(tasks)
        overall_rate = completed_total / total_tasks if total_tasks else 0
        focus_rate = completed_last7 / created_last7 if created_last7 else overall_rate
        prev_rate = completed_prev7 / created_prev7 if created_prev7 else (focus_rate if created_last7 else overall_rate)

        self.focus_value = int(round(focus_rate * 100))
        self.focus_delta_value = int(round((focus_rate - prev_rate) * 100))

        # Energy level from completion times (fallback to last 5 days)
        bucket_counts = [0, 0, 0, 0, 0]
        time_buckets = [(6, 9), (9, 12), (12, 15), (15, 18), (18, 21)]
        recent_cutoff = today - timedelta(days=14)
        for dt in completed_times:
            if dt.date() < recent_cutoff:
                continue
            for idx, (start, end) in enumerate(time_buckets):
                if start <= dt.hour < end:
                    bucket_counts[idx] += 1
                    break

        if sum(bucket_counts) == 0:
            last_5_days = [today - timedelta(days=4 - i) for i in range(5)]
            bucket_counts = [sum(1 for d in completed_dates if d == day) for day in last_5_days]
            energy_has_time = False
        else:
            energy_has_time = True

        max_bucket = max(bucket_counts) if bucket_counts else 0
        if max_bucket == 0:
            self.energy_values = [12, 12, 12, 12, 12]
            self.energy_level_text = "No data"
            self.energy_drop_text = "Need more history"
        else:
            self.energy_values = [12 + int(round((count / max_bucket) * 18)) for count in bucket_counts]
            avg_bucket = sum(bucket_counts) / len(bucket_counts)
            if max_bucket >= max(2, avg_bucket * 1.5):
                self.energy_level_text = "Peak"
            elif max_bucket >= avg_bucket * 1.1:
                self.energy_level_text = "Steady"
            else:
                self.energy_level_text = "Low"
            if energy_has_time:
                drop_labels = ["9:00 AM", "12:00 PM", "3:00 PM", "6:00 PM", "9:00 PM"]
                min_idx = bucket_counts.index(min(bucket_counts))
                self.energy_drop_text = f"Next drop: {drop_labels[min_idx]}"
            else:
                self.energy_drop_text = "Need more history"

        # Focus consistency (last 7 days)
        last_7_days = [today - timedelta(days=6 - i) for i in range(7)]
        day_counts = [sum(1 for d in completed_dates if d == day) for day in last_7_days]
        max_day = max(day_counts) if day_counts else 0
        self.consistency_levels = []
        self.consistency_values = []
        for count in day_counts:
            if max_day == 0 or count == 0:
                level = 0
                value = 0.0
            else:
                ratio = count / max_day
                value = ratio
                if ratio <= 0.33:
                    level = 1
                elif ratio <= 0.66:
                    level = 2
                else:
                    level = 3
            self.consistency_levels.append(level)
            self.consistency_values.append(value)

        self.consistency_labels_data = [day.strftime("%a") for day in last_7_days]

        # Velocity (last 7 days completions)
        max_velocity = max(day_counts) if day_counts else 0
        if max_velocity == 0:
            self.velocity_values = [4 for _ in range(7)]
        else:
            self.velocity_values = [
                4 + int(round((count / max_velocity) * 8)) for count in day_counts
            ]

        self.velocity_day_labels_data = [day.strftime("%a")[0] for day in last_7_days]
        self.velocity_counts = day_counts
        avg_velocity = sum(day_counts) / len(day_counts) if day_counts else 0
        self.velocity_capacity = []
        for i, count in enumerate(day_counts):
            left = day_counts[i - 1] if i > 0 else count
            right = day_counts[i + 1] if i < len(day_counts) - 1 else count
            smooth = (left + count + right) / 3
            capacity = max(smooth, avg_velocity) + avg_velocity * 0.2
            self.velocity_capacity.append(round(capacity, 2))
        self.velocity_point_value = sum(day_counts)
        self.velocity_point_index = last_7_days.index(today) if today in last_7_days else 3

        # Completion forecast
        remaining = max(total_tasks - completed_total, 0)
        if remaining == 0 and total_tasks > 0:
            self.completion_date = "All caught up"
        else:
            avg_recent = completed_last7 / 7 if completed_last7 > 0 else 0
            if avg_recent == 0 and completed_dates:
                first_done = min(completed_dates)
                days_span = max((today - first_done).days + 1, 1)
                avg_recent = len(completed_dates) / days_span
            if avg_recent > 0:
                days_left = ceil(remaining / avg_recent)
                forecast = today + timedelta(days=days_left)
                self.completion_date = forecast.strftime("%a, %b %d")
            else:
                self.completion_date = "No forecast"

        self.completion_value = int(round(overall_rate * 100))

        # --- Priority-weighted insights ---
        quality_score = int(round((weight_completed / max(weight_total, 1)) * 100)) if weight_total else 0
        high_total = priority_counts.get("high", 0)
        high_completed = priority_completed.get("high", 0)
        high_completion_pct = int(round((high_completed / high_total) * 100)) if high_total else 0

        priority_minutes = {level: 0 for level in PRIORITY_LEVELS}
        priority_sessions = {level: 0 for level in PRIORITY_LEVELS}
        recent_sessions = []
        last_3_days = [today - timedelta(days=2 - i) for i in range(3)]
        bucket_labels = _heatmap_time_labels_2h()
        bucket_count = len(bucket_labels)
        heat_weights = [[0.0 for _ in range(bucket_count)] for _ in range(3)]
        heat_has_data = False
        priority_day_weights = {}
        priority_day_minutes = {}
        priority_bucket_weights = [0.0 for _ in range(bucket_count)]
        conn = get_db_connection()
        try:
            info = conn.execute("PRAGMA table_info(pomodoro_sessions)").fetchall()
            if info:
                cols = {row[1] for row in info}
                select_cols = ["task_id", "started_at", "duration_min", "status"]
                if "task_priority" in cols:
                    select_cols.insert(1, "task_priority")
                rows = conn.execute(
                    f"SELECT {', '.join(select_cols)} FROM pomodoro_sessions"
                ).fetchall()
                has_priority_col = "task_priority" in cols
            else:
                rows = []
                has_priority_col = False
        except Exception as exc:
            print("DB Error (Priority Pomodoro):", exc)
            rows = []
        finally:
            conn.close()

        for row in rows:
            if has_priority_col:
                task_id, task_priority, started_at, duration_min, status = row
            else:
                task_id, started_at, duration_min, status = row
            status_norm = str(status).strip().lower() if status is not None else ""
            if status_norm and status_norm not in ("completed", "stopped"):
                continue
            prio = None
            if has_priority_col:
                prio = normalize_priority(task_priority)
            if prio not in PRIORITY_LEVELS:
                prio = tasks_by_id.get(task_id)
            if prio not in PRIORITY_LEVELS:
                continue
            dt, _ = self._parse_date(started_at)
            if dt:
                recent_sessions.append((dt, prio))
                if last_7_start <= dt.date() <= today:
                    priority_minutes[prio] += int(duration_min or 0)
                    priority_sessions[prio] += 1
                    minutes = int(duration_min or 0)
                    if minutes > 0:
                        weighted = minutes * priority_weight(prio)
                        priority_day_weights[dt.date()] = priority_day_weights.get(dt.date(), 0) + weighted
                        priority_day_minutes[dt.date()] = priority_day_minutes.get(dt.date(), 0) + minutes
                        priority_bucket_weights[dt.hour // 2] += weighted
                if dt.date() in last_3_days:
                    row_idx = last_3_days.index(dt.date())
                    bucket = dt.hour // 2
                    duration = int(duration_min or 0)
                    if duration <= 0:
                        duration = 1
                    heat_weights[row_idx][bucket] += priority_weight(prio) * duration
                    heat_has_data = True

        if not heat_has_data and completed_time_prios:
            heat_weights = [[0.0 for _ in range(bucket_count)] for _ in range(3)]
            for dt, prio in completed_time_prios:
                if dt.date() in last_3_days:
                    row_idx = last_3_days.index(dt.date())
                    bucket = dt.hour // 2
                    heat_weights[row_idx][bucket] += priority_weight(prio)
            heat_has_data = any(sum(row) > 0 for row in heat_weights)

        def _heat_level(value, max_val):
            if max_val <= 0 or value <= 0:
                return 0
            ratio = value / max_val
            if ratio <= 0.33:
                return 1
            if ratio <= 0.66:
                return 2
            return 3

        if heat_has_data:
            max_heat = max(max(row) for row in heat_weights) if heat_weights else 0
            self.heatmap_values = []
            for row in heat_weights:
                self.heatmap_values.append([_heat_level(value, max_heat) for value in row])
        else:
            fallback_weights = [completed_day_weights.get(day, 0) for day in last_3_days]
            max_fallback = max(fallback_weights) if fallback_weights else 0
            self.heatmap_values = []
            for weight in fallback_weights:
                level = _heat_level(weight, max_fallback)
                self.heatmap_values.append([level] * bucket_count)

        self.heatmap_day_labels_data = [day.strftime("%a") for day in last_3_days]
        self.heatmap_time_labels_data = bucket_labels

        total_priority_minutes = sum(priority_minutes.values())
        low_minutes = priority_minutes.get("low", 0) + priority_minutes.get("too low", 0)
        low_time_pct = int(round((low_minutes / total_priority_minutes) * 100)) if total_priority_minutes else 0
        high_time_pct = (priority_minutes.get("high", 0) / total_priority_minutes) if total_priority_minutes else 0.0
        high_medium_minutes = priority_minutes.get("high", 0) + priority_minutes.get("medium", 0)
        high_medium_pct = int(round((high_medium_minutes / total_priority_minutes) * 100)) if total_priority_minutes else 0

        peak_window_label = None
        if any(priority_bucket_weights):
            peak_idx = max(range(bucket_count), key=lambda i: (priority_bucket_weights[i], -i))
            if 0 <= peak_idx < len(bucket_labels):
                peak_window_label = bucket_labels[peak_idx]

        best_day_label = None
        best_day_minutes = 0
        if priority_day_weights:
            best_day = max(priority_day_weights.items(), key=lambda item: (item[1], item[0]))[0]
            best_day_label = best_day.strftime("%a")
            best_day_minutes = priority_day_minutes.get(best_day, 0)

        self.priority_minutes_week = dict(priority_minutes)
        self.priority_sessions_week = dict(priority_sessions)

        recent_sessions.sort(key=lambda item: item[0], reverse=True)
        recent_priorities = [p for _, p in recent_sessions[:3]]
        high_streak = len(recent_priorities) >= 3 and all(p == "high" for p in recent_priorities)
        high_load = max(high_total - high_completed, 0)
        burnout = (high_time_pct >= 0.7 and high_load >= 3) or high_streak

        def _join_sentences(*parts):
            return " ".join(part.strip() for part in parts if part and str(part).strip())

        peak_note = f"Peak priority window: {peak_window_label}." if peak_window_label else ""
        best_day_note = (
            f"Best priority day: {best_day_label} ({format_duration_minutes(best_day_minutes)})."
            if best_day_label
            else ""
        )

        if weight_total == 0:
            self.ai_primary_title_text = "Priority focus score"
            self.ai_primary_desc_text = "Add priority tasks to unlock quality insights."
        else:
            self.ai_primary_title_text = f"Quality score: {quality_score}"
            base_primary = (
                f"High-priority completion: {high_completion_pct}%. "
                f"Low/too low time: {low_time_pct}%."
            )
            self.ai_primary_desc_text = _join_sentences(base_primary, peak_note)

        if burnout:
            self.ai_secondary_title_text = "Burnout warning"
            self.ai_secondary_desc_text = _join_sentences(
                "High-priority load is intense. Add recovery time or delegate low-value work.",
                best_day_note
            )
        elif total_priority_minutes == 0 and sum(priority_counts.values()) > 0:
            self.ai_secondary_title_text = "Priority mix"
            self.ai_secondary_desc_text = _join_sentences(
                f"{high_total} high, {priority_counts.get('medium', 0)} medium, "
                f"{priority_counts.get('low', 0)} low, {priority_counts.get('too low', 0)} too low.",
                best_day_note
            )
        elif low_time_pct >= 50 and total_priority_minutes > 0:
            self.ai_secondary_title_text = "Refocus"
            self.ai_secondary_desc_text = _join_sentences(
                f"You spent {low_time_pct}% on low-value work. Start with a high-priority Pomodoro.",
                best_day_note
            )
        elif high_total > 0 and high_completion_pct < 40:
            self.ai_secondary_title_text = "Critical gap"
            self.ai_secondary_desc_text = _join_sentences(
                "High-priority tasks are lagging. Plan a deep-focus block early.",
                best_day_note
            )
        else:
            self.ai_secondary_title_text = "Balance check"
            self.ai_secondary_desc_text = _join_sentences(
                f"Priority mix looks healthy. High/medium focus: {high_medium_pct}%.",
                best_day_note
            )

        if burnout:
            self.ai_badge_text = "Warning"
            self.ai_action_text = "Plan Recovery"
        elif low_time_pct >= 50 and total_priority_minutes > 0:
            self.ai_badge_text = "Refocus"
            self.ai_action_text = "Block High Priority"
        elif high_total > 0 and high_completion_pct < 40:
            self.ai_badge_text = "Focus"
            self.ai_action_text = "Schedule Deep Work"
        elif total_priority_minutes == 0:
            self.ai_badge_text = "Needs Data"
            self.ai_action_text = "Start a Pomodoro"
        else:
            self.ai_badge_text = "Recommended"
            self.ai_action_text = "Schedule Deep Work"

        if high_total > 0 and priority_minutes.get("high", 0) == 0 and total_priority_minutes > 0:
            self.ai_recommendation_text = "Start the day with a high-priority session."
        elif low_time_pct >= 50 and total_priority_minutes > 0:
            self.ai_recommendation_text = "Protect your first Pomodoro for high-priority work."
        elif priority_minutes.get("medium", 0) > 0 and high_time_pct >= 0.7:
            self.ai_recommendation_text = "Schedule medium-priority tasks after peak focus hours."
        elif high_total == 0 and priority_counts.get("medium", 0) > 0:
            self.ai_recommendation_text = "Use your first Pomodoro to plan a medium-priority task."
        else:
            self.ai_recommendation_text = "Start the day with a high-priority session."

        now = datetime.now().strftime("%I:%M %p").lstrip("0")
        self._set_status(f"Updated {now}")
        self.header_chip.setProperty("state", "live")
        self.header_chip.setText("Live")
        self._load_pomodoro_metrics()
        self._apply_metrics()

    def _load_pomodoro_metrics(self):
        self.pomo_minutes_week = 0
        self.pomo_sessions_week = 0
        self.pomo_minutes_total = 0
        self.pomo_sessions_total = 0
        self.type_minutes = {label: 0 for label in (TASK_TYPES + [UNCATEGORIZED_LABEL])}
        today = datetime.now().date()
        last_7_start = today - timedelta(days=6)

        conn = get_db_connection()
        try:
            info = conn.execute("PRAGMA table_info(pomodoro_sessions)").fetchall()
            if not info:
                return
            cols = {row[1] for row in info}
            select_cols = ["started_at", "duration_min", "status"]
            has_type = "task_type" in cols
            if has_type:
                select_cols.append("task_type")
            rows = conn.execute(
                f"SELECT {', '.join(select_cols)} FROM pomodoro_sessions"
            ).fetchall()
        except Exception as exc:
            print("DB Error (Pomodoro Stats):", exc)
            rows = []
        finally:
            conn.close()

        day_map = {i: 0 for i in range(7)}
        day_sessions = {i: 0 for i in range(7)}
        for row in rows:
            if len(row) >= 4:
                started_at, duration_min, status, task_type = row[:4]
            else:
                started_at, duration_min, status = row[:3]
                task_type = None
            status_norm = str(status).strip().lower() if status is not None else ""
            if status_norm and status_norm not in ("completed", "stopped"):
                continue
            dur = int(duration_min or 0)
            self.pomo_minutes_total += dur
            self.pomo_sessions_total += 1
            norm_type = normalize_task_type(task_type) or UNCATEGORIZED_LABEL
            if norm_type in self.type_minutes:
                self.type_minutes[norm_type] += dur
            dt, _ = self._parse_date(started_at)
            if dt and last_7_start <= dt.date() <= today:
                self.pomo_minutes_week += dur
                self.pomo_sessions_week += 1
                day_index = (dt.date() - last_7_start).days
                if 0 <= day_index <= 6:
                    day_map[day_index] += dur
                    day_sessions[day_index] += 1

        self.pomo_day_minutes = [day_map[i] for i in range(7)]
        self.pomo_day_sessions = [day_sessions[i] for i in range(7)]
        self.pomo_day_labels = [(last_7_start + timedelta(days=i)).strftime("%a") for i in range(7)]
        if self.pomo_sessions_week > 0:
            self.pomo_avg_minutes = int(round(self.pomo_minutes_week / max(self.pomo_sessions_week, 1)))
        else:
            self.pomo_avg_minutes = 0

        if self.pomo_day_minutes:
            max_val = max(self.pomo_day_minutes)
            if max_val > 0:
                best_idx = self.pomo_day_minutes.index(max_val)
                self.pomo_best_day_minutes = max_val
                self.pomo_best_day_label = self.pomo_day_labels[best_idx]

        avg_sessions = sum(self.pomo_day_sessions) / 7 if self.pomo_day_sessions else 0
        avg_sessions = round(avg_sessions, 1)
        self.velocity_counts = list(self.pomo_day_sessions)
        self.velocity_capacity = [avg_sessions for _ in range(7)]
        self.velocity_point_value = avg_sessions
        self.velocity_point_index = 6
        self.velocity_day_labels_data = [label[:1] for label in self.pomo_day_labels]

    def _apply_dynamic_styles(self):
        if not self._colors:
            return
        colors = self._colors
        trend = self.focus_delta.property("trend")
        delta_color = colors["good"] if trend == "up" else colors["bad"]
        self.focus_delta.setStyleSheet(
            f"color: {delta_color}; font-size: 11px; font-weight: 700;"
        )

        max_energy = max(self.energy_values) if self.energy_values else 1
        for bar, value in zip(self.energy_bars, self.energy_values):
            if value >= max_energy * 0.85:
                c = colors["accent"]
            elif value >= max_energy * 0.65:
                c = colors["accent2"]
            else:
                c = colors["border"]
            bar.setStyleSheet(f"QFrame#DashEnergyBar {{ background: {c}; border-radius: 4px; }}")

        state = self.header_chip.property("state") or "live"
        if state == "saved":
            chip_bg = colors["good"]
            chip_text = "#ffffff"
        else:
            chip_bg = colors["chip"]
            chip_text = colors["chip_text"]
        self.header_chip.setStyleSheet(
            f"background: {chip_bg}; color: {chip_text}; border-radius: 12px; font-size: 11px; font-weight: 800;"
        )

    def update_theme(self, theme):
        self.current_theme = theme
        colors = get_theme(theme)

        self._colors = colors

        self.setStyleSheet(
            "QWidget#DashboardPage { background: %s; font-family: '%s', 'Segoe UI'; }" %
            (colors["bg"], FONT_FAMILY)
        )

        if hasattr(self, "container") and self.container:
            self.container.setStyleSheet(
                "QWidget#DashContainer { background: %s; }" % colors["bg"]
            )

        self.kicker.setStyleSheet(
            "color: %s; font-size: 11px; font-weight: 800;" % colors["accent"]
        )
        self.title.setStyleSheet(
            "color: %s; font-size: 30px; font-weight: 900;" % colors["text"]
        )
        self.subtitle.setStyleSheet(
            "color: %s; font-size: 13px; font-weight: 600;" % colors["sub"]
        )
        if self.header_status:
            status_color = colors["sub"] if theme == "Dark" else colors["text"]
            self.header_status.setStyleSheet(
                "color: %s; font-size: 11px; font-weight: 600;" % status_color
            )

        for title in self.section_titles:
            title.setStyleSheet(
                "color: %s; font-size: 16px; font-weight: 800;" % colors["text"]
            )

        for lbl in self.labels_main:
            if lbl.objectName() == "DashScore":
                lbl.setStyleSheet(
                    "color: %s; font-size: 34px; font-weight: 900;" % colors["text"]
                )
            elif hasattr(self, "energy_label") and self.energy_label is not None and lbl is self.energy_label:
                lbl.setStyleSheet(
                    "color: %s; font-size: 18px; font-weight: 800;" % colors["accent"]
                )
            elif lbl.objectName() == "DashCardTitle":
                lbl.setStyleSheet(
                    "color: %s; font-size: 14px; font-weight: 800;" % colors["text"]
                )
            elif lbl.objectName() == "DashAccent":
                lbl.setStyleSheet(
                    "color: %s; font-size: 12px; font-weight: 800;" % colors["accent"]
                )
            else:
                lbl.setStyleSheet(
                    "color: %s; font-size: 13px; font-weight: 700;" % colors["text"]
                )

        for lbl in self.labels_sub:
            if lbl.objectName() in ("DashDay", "DashDaySmall"):
                lbl.setStyleSheet(
                    "color: %s; font-size: 10px; font-weight: 700;" % colors["sub"]
                )
            else:
                lbl.setStyleSheet(
                    "color: %s; font-size: 11px; font-weight: 600;" % colors["sub"]
                )

        for card in self.cards:
            card.setStyleSheet(
                "QFrame#DashCard { background: %s; border: 1px solid %s; border-radius: 20px; }" %
                (colors["card"], colors["border"])
            )

        header = self.findChild(QFrame, "DashHeader")
        if header:
            header.setStyleSheet(
                "QFrame#DashHeader { background: %s; border: 1px solid %s; border-radius: 20px; }" %
                (colors["primary_gradient"], colors["border"])
            )

        for badge in self.badges:
            badge.setStyleSheet(
                "background: %s; color: %s; border-radius: 10px; font-size: 10px; font-weight: 800; padding: 2px 6px;" %
                (colors["accent_soft"], colors["accent"])
            )

        for bar in self.progress_bars:
            bar.setStyleSheet(
                "QProgressBar { background: %s; border: none; border-radius: 5px; }"
                "QProgressBar::chunk { background: qlineargradient(x1:0, y1:0, x2:1, y2:0, "
                "stop:0 %s, stop:1 %s); border-radius: 5px; }" %
                (colors["border"], colors["accent2"], colors["accent"])
            )

        primary = self.findChild(QFrame, "DashPrimary")
        if primary:
            primary.setStyleSheet(
                "QFrame#DashPrimary { background: %s; border: 1px solid %s; border-radius: 22px; }" %
                (colors["primary_gradient"], colors["border"])
            )

        secondary = self.findChild(QFrame, "DashSecondary")
        if secondary:
            secondary.setStyleSheet(
                "QFrame#DashSecondary { background: %s; border: 1px solid %s; border-radius: 18px; }" %
                (colors["secondary"], colors["border"])
            )

        icon = self.findChild(QFrame, "DashIcon")
        if icon:
            icon.setStyleSheet(
                "QFrame#DashIcon { background: %s; border-radius: 20px; border: 1px solid %s; }" %
                (colors["accent_soft"], colors["border"])
            )

        for btn in self.findChildren(QPushButton, "DashAction"):
            btn.setStyleSheet(
                "QPushButton { background: qlineargradient(x1:0, y1:0, x2:1, y2:0, "
                "stop:0 %s, stop:1 %s); color: white; border-radius: 12px; padding: 10px; font-weight: bold; }"
                "QPushButton:hover { background: qlineargradient(x1:0, y1:0, x2:1, y2:0, "
                "stop:0 %s, stop:1 %s); }" %
                (colors["accent2"], colors["accent"], colors["accent"], colors["accent2"])
            )

        for link in self.findChildren(QPushButton, "DashLink"):
            link.setStyleSheet(
                "QPushButton { background-color: transparent; color: %s; border: none; font-weight: bold; }"
                "QPushButton:hover { color: %s; }" % (colors["accent"], colors["accent2"])
            )

        for acc in self.findChildren(QLabel, "DashAccent"):
            acc.setStyleSheet(
                "color: %s; font-size: 12px; font-weight: 800;" % colors["accent"]
            )

        if self.consistency_chart:
            self.consistency_chart.set_theme(colors, theme)
        if hasattr(self, "type_chart") and self.type_chart:
            self.type_chart.set_theme(colors, theme)
        if self.heatmap_chart:
            self.heatmap_chart.set_theme(colors, theme)
        if self.velocity_chart:
            self.velocity_chart.set_theme(colors, theme)
        if hasattr(self, "pomo_chart") and self.pomo_chart:
            self.pomo_chart.set_theme(colors, theme)
        if hasattr(self, "priority_chart") and self.priority_chart:
            self.priority_chart.set_theme(colors, theme)

        self._apply_dynamic_styles()
