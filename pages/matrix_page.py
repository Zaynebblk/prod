import sqlite3
from PyQt6.QtWidgets import (QWidget, QGridLayout, QVBoxLayout, QLabel, QFrame, 
                             QScrollArea, QHBoxLayout, QGraphicsDropShadowEffect, QSizePolicy, QMessageBox)
from PyQt6.QtCore import Qt, QMimeData, QSize, pyqtSignal, QTimer, QDate
from PyQt6.QtGui import QDrag, QCursor, QFont, QColor

from database.db_manager import get_db_connection
from resources.theme import get_theme, FONT_FAMILY

PRIORITY_COLORS = {
    "high": "#ef4444",
    "medium": "#f59e0b",
    "low": "#3b82f6",
    "too low": "#94a3b8",
}

PRIORITY_BG_LIGHT = {
    "high": "#fff5f5",
    "medium": "#fffbeb",
    "low": "#eff6ff",
    "too low": "#f1f5f9",
}

PRIORITY_BG_DARK = {
    "high": "#2b1515",
    "medium": "#2b1b0a",
    "low": "#0f1b2d",
    "too low": "#1f2937",
}


def _priority_key(urgent, important):
    if urgent and important:
        return "high"
    if not urgent and important:
        return "medium"
    if urgent and not important:
        return "low"
    return "too low"

# --- 1. DRAGGABLE CARD ---
class TaskCard(QFrame):
    def __init__(self, task_id, title, theme_mode="Light"):
        super().__init__()
        self.task_id = task_id
        self.title_text = title
        self.current_theme = theme_mode
        self.ui_scale = 1.0
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        
        # Setup basic layout
        self.layout_box = QHBoxLayout(self)

        self.grip = QLabel("::") 
        
        self.lbl = QLabel(title)
        self.lbl.setWordWrap(True)

        self.layout_box.addWidget(self.grip)
        self.layout_box.addWidget(self.lbl)
        self.layout_box.addStretch()
        self._apply_layout_scale()
        
        # Apply the initial theme
        self.set_theme(theme_mode)

    def _apply_layout_scale(self):
        scale = self.ui_scale
        pad = max(8, int(round(12 * scale)))
        spacing = max(6, int(round(10 * scale)))
        grip_w = max(12, int(round(15 * scale)))
        self.layout_box.setContentsMargins(pad, pad, pad, pad)
        self.layout_box.setSpacing(spacing)
        self.grip.setFixedWidth(grip_w)

    def set_scale(self, scale):
        scale = max(1.0, min(1.35, float(scale)))
        if abs(scale - self.ui_scale) < 0.01:
            return
        self.ui_scale = scale
        self._apply_layout_scale()
        self.set_theme(self.current_theme)

    def set_theme(self, mode):
        self.current_theme = mode
        colors = get_theme(mode)
        if mode == "Dark":
            bg_color = colors["card_alt"]
            border_color = colors["border"]
            text_color = colors["text"]
            grip_color = colors["sub"]
            hover_bg = colors["card"]
            hover_border = colors["accent2"]
        else:
            bg_color = colors["card"]
            border_color = colors["border"]
            text_color = colors["text"]
            grip_color = colors["sub"]
            hover_bg = colors["accent_soft"]
            hover_border = colors["accent"]

        scale = self.ui_scale
        radius = max(7, int(round(8 * scale)))
        border_w = max(1, int(round(1 * scale)))
        margin_bottom = max(6, int(round(8 * scale)))
        grip_size = max(12, int(round(14 * scale)))
        text_size = max(12, int(round(13 * scale)))

        self.setStyleSheet(f"""
            QFrame {{ 
                background-color: {bg_color}; 
                border: {border_w}px solid {border_color}; 
                border-radius: {radius}px; 
                margin-bottom: {margin_bottom}px;
            }}
            QFrame:hover {{ 
                border: {border_w}px solid {hover_border}; 
                background-color: {hover_bg}; 
            }}
        """)
        
        self.grip.setStyleSheet(
            f"color: {grip_color}; font-weight: bold; font-size: {grip_size}px; border: none; background: transparent;"
        )
        self.lbl.setStyleSheet(
            f"border: none; background: transparent; color: {text_color}; font-size: {text_size}px; font-weight: 500;"
        )

    def mouseMoveEvent(self, event):
        if event.buttons() == Qt.MouseButton.LeftButton:
            drag = QDrag(self)
            mime = QMimeData()
            mime.setText(str(self.task_id))
            drag.setMimeData(mime)
            
            pixmap = self.grab()
            drag.setPixmap(pixmap)
            drag.setHotSpot(event.position().toPoint())
            
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            drag.exec(Qt.DropAction.MoveAction)
            self.setCursor(Qt.CursorShape.OpenHandCursor)

# --- 2. DROP ZONE (QUADRANT) ---
class Quadrant(QFrame):
    def __init__(self, title, sub, urgent, important, parent_mx):
        super().__init__()
        self.setAcceptDrops(True)
        self.u_target = urgent
        self.i_target = important
        self.parent_mx = parent_mx
        self.current_theme = "Light"
        self.ui_scale = 1.0

        priority_key = _priority_key(urgent, important)
        # Light Mode Colors (match Tasks priority palette)
        self.light_bg = PRIORITY_BG_LIGHT[priority_key]
        self.light_border = PRIORITY_COLORS[priority_key]
        self.light_text = PRIORITY_COLORS[priority_key]

        # Dark Mode Colors (same priority hues)
        self.dark_bg = PRIORITY_BG_DARK[priority_key]
        self.dark_border = PRIORITY_COLORS[priority_key]
        self.dark_text = PRIORITY_COLORS[priority_key]

        self.title_str = title
        self.sub_str = sub

        self.setProperty("class", "Quadrant")
        
        self.q_layout = QVBoxLayout(self)
        self.q_layout.setContentsMargins(15, 15, 15, 15)
        self.q_layout.setSpacing(10)
        
        self.header_layout = QVBoxLayout()
        self.header_layout.setSpacing(2)
        
        self.lbl_title = QLabel(title)
        self.lbl_sub = QLabel(sub.upper())
        
        self.header_layout.addWidget(self.lbl_title)
        self.header_layout.addWidget(self.lbl_sub)
        self.q_layout.addLayout(self.header_layout)
        
        self.line = QFrame()
        self.line.setFrameShape(QFrame.Shape.HLine)
        self.q_layout.addWidget(self.line)

        self.container = QWidget()
        self.container.setStyleSheet("background: transparent; border: none;")
        self.c_layout = QVBoxLayout(self.container)
        self.c_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.c_layout.setSpacing(5)
        self.c_layout.setContentsMargins(0, 10, 0, 0)
        
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setWidget(self.container)
        self._apply_scroll_style()
        self.q_layout.addWidget(self.scroll)

        self._apply_layout_scale()

        # Apply initial styles
        self.apply_theme("Light")

    def _apply_scroll_style(self):
        width = max(8, int(round(8 * self.ui_scale)))
        radius = max(3, int(round(4 * self.ui_scale)))
        min_handle = max(18, int(round(20 * self.ui_scale)))
        self.scroll.setStyleSheet(f"""
            QScrollArea {{ border: none; background: transparent; }}
            QScrollBar:vertical {{
                border: none; background: transparent; width: {width}px; margin: 0px;
            }}
            QScrollBar::handle:vertical {{
                background: #cbd5e0; min-height: {min_handle}px; border-radius: {radius}px;
            }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0px; }}
        """)

    def _apply_layout_scale(self):
        scale = self.ui_scale
        pad = max(12, int(round(15 * scale)))
        spacing = max(8, int(round(10 * scale)))
        header_spacing = max(1, int(round(2 * scale)))
        list_spacing = max(4, int(round(5 * scale)))
        top_margin = max(6, int(round(10 * scale)))
        self.q_layout.setContentsMargins(pad, pad, pad, pad)
        self.q_layout.setSpacing(spacing)
        self.header_layout.setSpacing(header_spacing)
        self.c_layout.setSpacing(list_spacing)
        self.c_layout.setContentsMargins(0, top_margin, 0, 0)
        self._apply_scroll_style()

    def set_scale(self, scale):
        scale = max(1.0, min(1.35, float(scale)))
        if abs(scale - self.ui_scale) < 0.01:
            return
        self.ui_scale = scale
        self._apply_layout_scale()
        self.apply_theme(self.current_theme)

    def apply_theme(self, mode):
        self.current_theme = mode
        
        if mode == "Dark":
            c_bg = self.dark_bg
            c_border = self.dark_border
            c_text = self.dark_text
            c_sub_text = "rgba(255, 255, 255, 179)"
        else:
            c_bg = self.light_bg
            c_border = self.light_border
            c_text = self.light_text
            c_sub_text = f"{self.light_text}"

        scale = self.ui_scale
        border_w = max(2, int(round(2 * scale)))
        radius = max(10, int(round(12 * scale)))
        title_size = max(16, int(round(16 * scale)))
        sub_size = max(10, int(round(10 * scale)))
        line_h = max(2, int(round(2 * scale)))

        self.setStyleSheet(f"""
            QFrame.Quadrant {{
                background-color: {c_bg}; 
                border: {border_w}px solid {c_border}; 
                border-radius: {radius}px;
            }}
        """)
        
        self.lbl_title.setStyleSheet(
            f"color: {c_text}; font-size: {title_size}px; font-weight: bold; border: none; background: transparent;"
        )
        self.lbl_sub.setStyleSheet(
            f"color: {c_sub_text}; font-size: {sub_size}px; font-weight: bold; border: none; background: transparent;"
        )
        self.line.setFixedHeight(line_h)
        self.line.setStyleSheet(f"background-color: {c_border};")

        # Update all existing tasks inside
        for i in range(self.c_layout.count()):
            widget = self.c_layout.itemAt(i).widget()
            if isinstance(widget, TaskCard):
                widget.set_scale(self.ui_scale)
                widget.set_theme(mode)

    def add_task(self, t_id, title):
        # Create card with current theme
        card = TaskCard(t_id, title, self.current_theme)
        self.c_layout.addWidget(card)

    def clear(self):
        while self.c_layout.count():
            item = self.c_layout.takeAt(0)
            if item.widget(): item.widget().deleteLater()

    def dragEnterEvent(self, event):
        if event.mimeData().hasText(): 
            event.accept()
            self.setFrameShadow(QFrame.Shadow.Raised)
        else: 
            event.ignore()

    def dragLeaveEvent(self, event):
        self.setFrameShadow(QFrame.Shadow.Plain)
        event.accept()

    def dropEvent(self, event):
        t_id = event.mimeData().text()
        conn = get_db_connection()

        # Enforce urgency based on deadline date.
        due_date = None
        try:
            row = conn.execute("SELECT due_date FROM tasks WHERE id=?", (t_id,)).fetchone()
            if row:
                due_date = row[0]
        except Exception:
            due_date = None

        due_is_urgent = False
        try:
            if due_date:
                due = QDate.fromString(str(due_date), "yyyy-MM-dd")
                if due.isValid():
                    days_to = QDate.currentDate().daysTo(due)
                    due_is_urgent = days_to <= 2
        except Exception:
            due_is_urgent = False

        if bool(self.u_target) != bool(due_is_urgent):
            try:
                QMessageBox.warning(
                    self,
                    "Priority Locked",
                    "modifie deadline date in the tasks page before"
                )
            except Exception:
                pass
            conn.close()
            event.ignore()
            return
        
        p_text = "too low"
        if self.u_target and self.i_target:
            p_text = "high"
        elif not self.u_target and self.i_target:
            p_text = "medium"
        elif self.u_target and not self.i_target:
            p_text = "low"
        else:
            p_text = "too low"
        
        conn.execute("UPDATE tasks SET is_urgent=?, is_important=?, priority=? WHERE id=?", 
                     (1 if self.u_target else 0, 1 if self.i_target else 0, p_text, t_id))
        conn.commit()
        conn.close()
        
        self.parent_mx.refresh_matrix()
        self.parent_mx.task_updated.emit() 
        event.accept()

# --- 3. MAIN PAGE ---
class EisenhowerMatrix(QWidget):
    task_updated = pyqtSignal()

    def __init__(self):
        super().__init__()
        # Initial Theme
        self.current_theme = "Light"
        self._theme_colors = None
        self._ui_scale = None
        self._single_column = False
        
        self.main_layout = QVBoxLayout(self)
        self.main_layout.setContentsMargins(0, 0, 0, 0)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setStyleSheet("QScrollArea { background: transparent; }")

        self.container = QWidget()
        self.container.setStyleSheet("background: transparent;")
        self.container_layout = QVBoxLayout(self.container)
        self.container_layout.setContentsMargins(32, 32, 32, 32)
        self.container_layout.setSpacing(20)

        self.scroll.setWidget(self.container)
        self.main_layout.addWidget(self.scroll)
        
        self.page_title = QLabel("Eisenhower Matrix")
        self.container_layout.addWidget(self.page_title)

        self.grid_layout = QGridLayout()
        self.grid_layout.setSpacing(25)
        
        # Initialize Quadrants
        self.q1 = Quadrant("Urgent & Important", "Do First", True, True, self)

        self.q2 = Quadrant("Important, Not Urgent", "Schedule", False, True, self)

        self.q3 = Quadrant("Urgent, Not Important", "Delegate", True, False, self)

        self.q4 = Quadrant("Not Urgent, Not Important", "Eliminate", False, False, self)
        for q in (self.q1, self.q2, self.q3, self.q4):
            q.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
            q.setMinimumHeight(160)

        self.grid_layout.addWidget(self.q1, 0, 0)
        self.grid_layout.addWidget(self.q2, 0, 1)
        self.grid_layout.addWidget(self.q3, 1, 0)
        self.grid_layout.addWidget(self.q4, 1, 1)

        self.container_layout.addLayout(self.grid_layout, stretch=1)
        
        self._set_grid_mode(single_column=False)

        # Apply Default Light Theme
        self.update_theme("Light")

        # --- KEY CHANGE: Load tasks immediately on startup ---
        self.refresh_matrix()
        self._apply_responsive_sizes()
        QTimer.singleShot(0, self._apply_responsive_sizes)

    def showEvent(self, event):
        super().showEvent(event)
        QTimer.singleShot(0, self._apply_responsive_sizes)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        width = self.width()
        if width < 900:
            self._set_grid_mode(single_column=True)
        else:
            self._set_grid_mode(single_column=False)
        self._apply_responsive_sizes()

    def _set_grid_mode(self, single_column=False):
        self._single_column = single_column
        if single_column:
            self.grid_layout.setVerticalSpacing(18)
            self.grid_layout.addWidget(self.q1, 0, 0)
            self.grid_layout.addWidget(self.q2, 1, 0)
            self.grid_layout.addWidget(self.q3, 2, 0)
            self.grid_layout.addWidget(self.q4, 3, 0)
            self.grid_layout.setColumnStretch(0, 1)
            self.grid_layout.setColumnStretch(1, 0)
            for r in range(4):
                self.grid_layout.setRowStretch(r, 1)
        else:
            self.grid_layout.setVerticalSpacing(25)
            self.grid_layout.addWidget(self.q1, 0, 0)
            self.grid_layout.addWidget(self.q2, 0, 1)
            self.grid_layout.addWidget(self.q3, 1, 0)
            self.grid_layout.addWidget(self.q4, 1, 1)
            self.grid_layout.setColumnStretch(0, 1)
            self.grid_layout.setColumnStretch(1, 1)
            self.grid_layout.setRowStretch(0, 1)
            self.grid_layout.setRowStretch(1, 1)

    def _apply_title_style(self):
        colors = self._theme_colors or get_theme(self.current_theme)
        scale = self._ui_scale or 1.0
        title_size = max(24, int(round(26 * scale)))
        margin_bottom = max(16, int(round(20 * scale)))
        self.page_title.setStyleSheet(
            f"font-size: {title_size}px; font-weight: bold; color: {colors['text']}; margin-bottom: {margin_bottom}px;"
        )

    def _apply_responsive_sizes(self):
        if not hasattr(self, "scroll"):
            return
        viewport = self.scroll.viewport()
        width = max(0, int(viewport.width() or self.width()))
        height = max(0, int(viewport.height() or self.height()))
        scale = 1.0
        if width and height:
            scale = max(1.0, min(1.35, min(width / 1200.0, height / 800.0)))
        elif width:
            scale = max(1.0, min(1.35, width / 1200.0))
        if self._ui_scale is None or abs(scale - self._ui_scale) > 0.01:
            self._ui_scale = scale
            self._apply_title_style()
            for q in (self.q1, self.q2, self.q3, self.q4):
                q.set_scale(scale)

        if height <= 0:
            return
        margins = self.container_layout.contentsMargins()
        title_h = self.page_title.sizeHint().height()
        spacing = self.container_layout.spacing()
        available_h = height - margins.top() - margins.bottom() - title_h - spacing
        if available_h <= 0:
            return
        grid_spacing = self.grid_layout.verticalSpacing()
        rows = 4 if self._single_column else 2
        cols = 1 if self._single_column else 2
        available_w = width - margins.left() - margins.right()
        target_from_height = (available_h - grid_spacing * (rows - 1)) / max(rows, 1)
        target_from_width = (available_w - self.grid_layout.horizontalSpacing() * (cols - 1)) / max(cols, 1)
        target = min(target_from_height, target_from_width) if target_from_width > 0 else target_from_height
        min_h = max(160, int(target))
        for q in (self.q1, self.q2, self.q3, self.q4):
            q.setMinimumHeight(min_h)

    def update_theme(self, theme_name):
        """Called by MainApp to switch themes"""
        self.current_theme = theme_name
        colors = get_theme(theme_name)
        self._theme_colors = colors

        self.setStyleSheet(f"background-color: {colors['bg']}; font-family: '{FONT_FAMILY}', 'Segoe UI';")
        self._apply_title_style()

        # Update all quadrants
        for q in [self.q1, self.q2, self.q3, self.q4]:
            q.apply_theme(theme_name)

    def refresh_matrix(self):
        for q in [self.q1, self.q2, self.q3, self.q4]: q.clear()
        
        conn = get_db_connection()
        rows = conn.execute("SELECT id, title, is_urgent, is_important FROM tasks WHERE is_completed = 0").fetchall()
        conn.close()

        for row in rows:
            t_id, title, u, i = row
            is_u, is_i = bool(u), bool(i)
            
            if is_u and is_i: self.q1.add_task(t_id, title)
            elif not is_u and is_i: self.q2.add_task(t_id, title)
            elif is_u and not is_i: self.q3.add_task(t_id, title)
            else: self.q4.add_task(t_id, title)
