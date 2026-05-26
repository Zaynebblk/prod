import sqlite3
import json
import os
import calendar
from datetime import datetime, timedelta, timezone
from PyQt6.QtWidgets import (QApplication, QWidget, QVBoxLayout, QHBoxLayout, QLabel,
                             QFrame, QPushButton, QScrollArea, QDialog,
                             QLineEdit, QDateEdit, QComboBox, QMessageBox,
                             QCheckBox, QGraphicsDropShadowEffect, QSizePolicy,
                             QSystemTrayIcon, QGridLayout, QSpinBox, QListWidget, QListWidgetItem, QAbstractItemView, QInputDialog, QMenu)
from PyQt6.QtCore import Qt, QDate, pyqtSignal, QTimer, QUrl, QLocale, QSize, QEvent
from PyQt6.QtGui import QColor, QIcon, QFontMetrics
from PyQt6.QtMultimedia import QSoundEffect
from resources.theme import get_theme, FONT_FAMILY, rgba
from resources.time_format import format_duration_minutes
from resources.task_types import (
    TASK_TYPES,
    TASK_TYPE_COLORS,
    UNCATEGORIZED_LABEL,
    normalize_task_type,
    suggest_task_type,
)
from resources.priority import (
    normalize_priority,
    priority_to_quadrant,
    quadrant_from_flags,
)
from database.db_manager import get_db_connection as _get_db_connection, init_db as _init_local_db

# --- NO WHEEL COMBOBOX ---
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

# --- STYLES HELPER ---
def get_dialog_style(theme):
    c = get_theme(theme)
    is_dark = theme == "Dark"
    dialog_bg = c["card_alt"] if is_dark else c["bg"]
    input_bg = c["input_bg"]
    focus = c["accent2"]
    text_color = c["text"]
    return f"""
        QDialog {{ background-color: {dialog_bg}; }}
        QLabel {{ color: {text_color}; font-weight: 600; font-size: 13px; padding-top: 10px; }}
        QLineEdit, QDateEdit {{
            background-color: {input_bg}; border: 1px solid {c['border']};
            border-radius: 10px; padding: 10px; font-size: 14px; color: {text_color};
        }}
        QComboBox {{
            background-color: {input_bg}; border: 1px solid {c['border']};
            border-radius: 10px; padding: 10px; font-size: 10.5pt; color: {text_color};
        }}
        QLineEdit:focus, QDateEdit:focus {{ border: 1px solid {focus}; }}
        QComboBox::drop-down {{ border: none; }}
        QComboBox QAbstractItemView {{ background-color: {input_bg}; color: {text_color}; selection-background-color: {c['accent']}; }}
    """

def get_calendar_style(theme):
    c = get_theme(theme)
    bg = c["card_alt"] if theme == "Dark" else c["card"]
    text = c["text"]
    sub = c["sub"]
    accent = c["accent"]
    border = c["border"]
    input_bg = c["input_bg"]
    return f"""
        QCalendarWidget QWidget {{ background-color: {bg}; color: {text}; }}
        QCalendarWidget QWidget#qt_calendar_navigationbar {{ background-color: {bg}; }}
        QCalendarWidget QToolButton {{ color: {text}; background: transparent; font-weight: 700; }}
        QCalendarWidget QToolButton:hover {{ color: {accent}; }}
        QCalendarWidget QMenu {{ background: {bg}; color: {text}; }}
        QCalendarWidget QHeaderView::section {{
            background: {bg};
            color: {text};
            font-weight: 700;
            padding: 4px 6px;
        }}
        QCalendarWidget QSpinBox {{
            background: {input_bg};
            color: {text};
            border: 1px solid {border};
            border-radius: 6px;
            padding: 2px 6px;
        }}
        QCalendarWidget QAbstractItemView {{
            background: {bg};
            color: {text};
            selection-background-color: {accent};
            selection-color: #ffffff;
            gridline-color: {border};
            outline: 0;
            font-size: 11px;
        }}
        QCalendarWidget QAbstractItemView::item {{ color: {text}; padding: 4px; }}
        QCalendarWidget QAbstractItemView::item:disabled {{ color: {sub}; }}
        QCalendarWidget QAbstractItemView::item:selected {{ background: {accent}; color: #ffffff; border-radius: 6px; }}
    """

PALETTE = {
    "mist": "#BAD2E0",
    "sky": "#82AFF2",
    "ocean": "#3078CD",
    "deep": "#25456B",
    "abyss": "#113356",
    "paper": "#F8F6F2"
}

STYLES = {
    "btn_primary": """
        QPushButton {
            background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #3078CD, stop:1 #82AFF2);
            color: white; border-radius: 16px; font-weight: bold; font-size: 14px; border: none;
            padding: 10px 18px;
        }
        QPushButton:hover { background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #25456B, stop:1 #3078CD); }
    """,
    "btn_secondary": """
        QPushButton {
            background-color: transparent; color: #25456B; border: 1px solid #BAD2E0;
            border-radius: 12px; padding: 8px 16px; font-weight: bold;
        }
        QPushButton:hover { background-color: #BAD2E0; color: #113356; border-color: #82AFF2; }
    """
}

PRIORITY_COLORS = {
    "high": "#ef4444",
    "medium": "#f59e0b",
    "low": "#3b82f6",
    "too low": "#94a3b8"
}

REQUIRED_IMPORTANT_TYPES = {"Deep Work", "Goal-related"}

# --- DIALOGS ---
class AddTaskDialog(QDialog):
    def __init__(
        self,
        parent=None,
        title_text="New Task",
        theme="Light",
        enable_recurrence: bool = True,
        enable_assignment: bool = False,
        assignees: list | None = None,
    ):
        super().__init__(parent)
        self.setWindowTitle(title_text)
        self.setFixedWidth(400)
        self.setStyleSheet(get_dialog_style(theme))
        self._enable_recurrence = bool(enable_recurrence)
        self._enable_assignment = bool(enable_assignment)
        self._assignees = list(assignees or [])

        layout = QVBoxLayout(self)
        layout.setContentsMargins(30, 30, 30, 30)
        layout.setSpacing(15)

        lbl_head = QLabel(title_text.upper())
        lbl_head.setStyleSheet("color: #3078CD; font-size: 12px; font-weight: 800;")
        layout.addWidget(lbl_head)

        self.title_input = QLineEdit()
        self.title_input.setPlaceholderText("What needs to be done?")
        layout.addWidget(self.title_input)

        self.title_error = QLabel("Champ requis")
        self.title_error.setStyleSheet("color: #ef4444; font-size: 11px; font-weight: 600;")
        self.title_error.setVisible(False)
        layout.addWidget(self.title_error)
        self.title_input.textChanged.connect(lambda _: self._set_title_error(False))

        self.desc_input = QLineEdit()
        self.desc_input.setPlaceholderText("Add details...")
        layout.addWidget(self.desc_input)

        lbl_type = QLabel("Task Type")
        layout.addWidget(lbl_type)

        self.type_combo = NoWheelComboBox()
        self.type_combo.addItem("Auto")
        self.type_combo.addItems(TASK_TYPES)
        self.type_combo.setMinimumHeight(40)
        self.type_combo.setCursor(Qt.CursorShape.PointingHandCursor)
        self.type_combo.currentIndexChanged.connect(self._sync_type_requirement)
        layout.addWidget(self.type_combo)

        lbl_due = QLabel("Deadline:")
        layout.addWidget(lbl_due)

        row = QHBoxLayout()
        self.date_input = QDateEdit()
        self.date_input.setCalendarPopup(True)
        today = QDate.currentDate()
        self.date_input.setDate(today)
        self.date_input.setMinimumDate(today)
        # Force English locale for clear day/month names in the calendar.
        try:
            self.date_input.setLocale(QLocale(QLocale.Language.English, QLocale.Country.UnitedStates))
            # Keep a clear numeric input format.
            self.date_input.setDisplayFormat("dd/MM/yyyy")
        except Exception:
            pass
        try:
            cal = self.date_input.calendarWidget()
            if cal:
                try:
                    cal.setLocale(QLocale(QLocale.Language.English, QLocale.Country.UnitedStates))
                except Exception:
                    pass
                try:
                    from PyQt6.QtWidgets import QCalendarWidget
                    cal.setHorizontalHeaderFormat(QCalendarWidget.HorizontalHeaderFormat.LongDayNames)
                    cal.setVerticalHeaderFormat(QCalendarWidget.VerticalHeaderFormat.NoVerticalHeader)
                except Exception:
                    pass
                try:
                    cal.setMinimumDate(today)
                except Exception:
                    pass
                try:
                    cal.setMinimumSize(320, 260)
                except Exception:
                    pass
                cal.setStyleSheet(get_calendar_style(theme))
        except Exception:
            pass

        self.important_check = QCheckBox("Important", self)
        self.important_check.setCursor(Qt.CursorShape.PointingHandCursor)
        self.important_check.stateChanged.connect(self._on_important_changed)

        row.addWidget(self.date_input)
        row.addWidget(self.important_check)
        layout.addLayout(row)

        self.repeat_combo = None
        self.repeat_interval = None
        if self._enable_recurrence:
            lbl_repeat = QLabel("Repeat for")
            layout.addWidget(lbl_repeat)

            repeat_row = QHBoxLayout()
            self.repeat_combo = NoWheelComboBox()
            self.repeat_combo.setMinimumHeight(40)
            self.repeat_combo.setCursor(Qt.CursorShape.PointingHandCursor)
            self.repeat_combo.addItem("No repeat", userData="")
            self.repeat_combo.addItem("Daily", userData="daily")
            self.repeat_combo.addItem("Weekly", userData="weekly")
            self.repeat_combo.addItem("Monthly", userData="monthly")

            self.repeat_interval = QSpinBox()
            self.repeat_interval.setRange(1, 30)
            self.repeat_interval.setValue(1)
            self.repeat_interval.setFixedHeight(40)
            self.repeat_interval.setCursor(Qt.CursorShape.PointingHandCursor)

            repeat_row.addWidget(self.repeat_combo, 2)
            repeat_row.addWidget(self.repeat_interval, 1)
            layout.addLayout(repeat_row)

            self.repeat_combo.currentIndexChanged.connect(self._sync_repeat_interval_ui)
            self._sync_repeat_interval_ui()

        self.assignee_combo = None
        if self._enable_assignment:
            lbl_assignee = QLabel("Assignee")
            layout.addWidget(lbl_assignee)

            self.assignee_combo = NoWheelComboBox()
            self.assignee_combo.setMinimumHeight(40)
            self.assignee_combo.setCursor(Qt.CursorShape.PointingHandCursor)
            self.assignee_combo.addItem("Unassigned", userData=None)
            for item in self._assignees:
                try:
                    uid, uname = item
                except Exception:
                    continue
                self.assignee_combo.addItem(str(uname), userData=int(uid))
            layout.addWidget(self.assignee_combo)

        layout.addSpacing(10)

        btn_layout = QHBoxLayout()
        self.btn_cancel = QPushButton("Cancel")
        self.btn_cancel.setStyleSheet(STYLES["btn_secondary"])
        self.btn_cancel.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_cancel.clicked.connect(self.reject)

        self.btn_save = QPushButton("Save Task")
        self.btn_save.setStyleSheet(STYLES["btn_primary"])
        self.btn_save.setFixedSize(120, 40)
        self.btn_save.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_save.clicked.connect(self.accept)
        try:
            # Allow pressing Enter to submit the dialog.
            self.btn_save.setDefault(True)
            self.btn_save.setAutoDefault(True)
        except Exception:
            pass

        btn_layout.addStretch()
        btn_layout.addWidget(self.btn_cancel)
        btn_layout.addWidget(self.btn_save)
        layout.addLayout(btn_layout)

        # Enter key convenience on common fields.
        try:
            self.title_input.returnPressed.connect(self.accept)
        except Exception:
            pass
        try:
            self.desc_input.returnPressed.connect(self.accept)
        except Exception:
            pass
        self._on_important_changed(self.important_check.isChecked())
        self._sync_type_requirement()

    def _sync_repeat_interval_ui(self):
        if not self._enable_recurrence or not self.repeat_combo or not self.repeat_interval:
            return
        kind = self.repeat_combo.currentData()
        enabled = bool(str(kind or "").strip())
        self.repeat_interval.setEnabled(enabled)
        self.repeat_interval.setVisible(enabled)
        if not enabled:
            return
        suffix = ""
        if kind == "daily":
            suffix = " day(s)"
        elif kind == "weekly":
            suffix = " week(s)"
        elif kind == "monthly":
            suffix = " month(s)"
        try:
            self.repeat_interval.setSuffix(suffix)
        except Exception:
            pass

    def _set_title_error(self, show):
        self.title_error.setVisible(bool(show))

    def accept(self):
        if not self.title_input.text().strip():
            self._set_title_error(True)
            self.title_input.setFocus()
            return
        self._set_title_error(False)
        super().accept()

    def load_data(
        self,
        title,
        desc,
        date_str,
        important,
        task_type=None,
        recurrence_kind=None,
        recurrence_interval=1,
        assigned_to=None,
    ):
        self.title_input.setText(title)
        self.desc_input.setText(desc)
        if date_str:
            d = QDate.fromString(date_str, "yyyy-MM-dd")
            try:
                if d.isValid():
                    min_d = self.date_input.minimumDate()
                    if min_d.isValid() and d < min_d:
                        d = min_d
                    self.date_input.setDate(d)
            except Exception:
                self.date_input.setDate(QDate.currentDate())
        requires_important = normalize_task_type(task_type) in REQUIRED_IMPORTANT_TYPES
        self.important_check.setChecked(bool(important) or requires_important)
        norm_type = normalize_task_type(task_type)
        if norm_type and not (norm_type == "Goal-related" and not bool(important)):
            idx = self.type_combo.findText(norm_type)
            if idx >= 0:
                self.type_combo.setCurrentIndex(idx)
        else:
            self.type_combo.setCurrentIndex(0)
        self._sync_type_requirement()
        if self._enable_recurrence and self.repeat_combo and self.repeat_interval:
            kind = str(recurrence_kind or "").strip().lower()
            idx = self.repeat_combo.findData(kind if kind in ("daily", "weekly", "monthly") else "")
            if idx >= 0:
                self.repeat_combo.setCurrentIndex(idx)
            try:
                self.repeat_interval.setValue(max(1, int(recurrence_interval or 1)))
            except Exception:
                self.repeat_interval.setValue(1)
            self._sync_repeat_interval_ui()
        if self._enable_assignment and self.assignee_combo is not None:
            uid = assigned_to
            try:
                uid = int(uid) if uid is not None else None
            except Exception:
                uid = None
            idx = self.assignee_combo.findData(uid)
            if idx >= 0:
                self.assignee_combo.setCurrentIndex(idx)

    def get_data(self):
        important = self.important_check.isChecked()
        selected = self.type_combo.currentText()
        if selected == "Auto":
            task_type = suggest_task_type(self.title_input.text(), self.desc_input.text(), important=important)
        else:
            task_type = normalize_task_type(selected)
        if task_type in REQUIRED_IMPORTANT_TYPES and not important:
            important = True
            try:
                self.important_check.setChecked(True)
            except Exception:
                pass
        recurrence_kind = ""
        recurrence_interval = 1
        if self._enable_recurrence and self.repeat_combo and self.repeat_interval:
            recurrence_kind = str(self.repeat_combo.currentData() or "").strip().lower()
            try:
                recurrence_interval = max(1, int(self.repeat_interval.value()))
            except Exception:
                recurrence_interval = 1
        assigned_to = None
        if self._enable_assignment and self.assignee_combo is not None:
            try:
                assigned_to = self.assignee_combo.currentData()
            except Exception:
                assigned_to = None
        return {
            "title": self.title_input.text(),
            "description": self.desc_input.text(),
            "date": self.date_input.date().toString("yyyy-MM-dd"),
            "important": important,
            "task_type": task_type,
            "recurrence_kind": recurrence_kind,
            "recurrence_interval": recurrence_interval,
            "assigned_to": assigned_to,
        }

    def _on_important_changed(self, state):
        if not hasattr(self, "type_combo"):
            return
        if not bool(state) and normalize_task_type(self.type_combo.currentText()) in REQUIRED_IMPORTANT_TYPES:
            # Required types must stay important.
            self.important_check.setChecked(True)
            return
        self._sync_type_requirement()

    def _sync_type_requirement(self):
        selected = self.type_combo.currentText()
        norm_type = normalize_task_type(selected)
        requires = norm_type in REQUIRED_IMPORTANT_TYPES
        if requires:
            if not self.important_check.isChecked():
                self.important_check.setChecked(True)
            self.important_check.setEnabled(False)
        else:
            self.important_check.setEnabled(True)

class ViewTaskDialog(QDialog):
    def __init__(
        self,
        title,
        desc,
        due_date,
        created_date,
        priority,
        task_type=None,
        total_focus_min=0,
        total_sessions=0,
        sessions=None,
        parent=None,
        theme="Light",
        task_id: int | None = None,
    ):
        super().__init__(parent)
        self.setWindowTitle("Task Details")
        self.setFixedWidth(400)
        if sessions is None:
            sessions = []
        self.task_id = task_id
        self._loading_subtasks = False
        self._loading_dependencies = False

        bg = "#113356" if theme == "Dark" else "#F8F6F2"
        txt = "#e6eef5" if theme == "Dark" else "#113356"
        box_bg = "#1b2f4d" if theme == "Dark" else "#ffffff"
        input_bg = box_bg
        border = "#25456B" if theme == "Dark" else "#BAD2E0"

        self.setStyleSheet(f"QDialog {{ background-color: {bg}; }} QLabel {{ color: {txt}; }}")

        self._did_constrain_to_screen = False

        root = QVBoxLayout(self)
        root.setContentsMargins(40, 40, 40, 40)
        root.setSpacing(12)

        scroll_area = QScrollArea()
        scroll_area.setWidgetResizable(True)
        scroll_area.setFrameShape(QFrame.Shape.NoFrame)
        scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll_area.setStyleSheet(
            f"QScrollArea {{ border: none; background: {bg}; }} "
            f"QScrollArea > QWidget > QWidget {{ background: {bg}; }} "
            f"QScrollBar:vertical {{ background: {bg}; width: 10px; margin: 0px; }} "
            f"QScrollBar::handle:vertical {{ background: {border}; border-radius: 5px; min-height: 24px; }} "
            f"QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0px; }} "
            f"QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: none; }}"
        )
        root.addWidget(scroll_area, 1)

        content = QWidget()
        content.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        content.setStyleSheet(f"background: {bg};")
        scroll_area.setWidget(content)

        layout = QVBoxLayout(content)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        p_colors = PRIORITY_COLORS
        c = p_colors.get(priority, "#718096")
        lbl_p = QLabel(priority.upper())
        lbl_p.setStyleSheet(f"color: {c}; font-weight: 900; font-size: 11px;")
        layout.addWidget(lbl_p)

        norm_type = normalize_task_type(task_type)
        if norm_type:
            type_color = TASK_TYPE_COLORS.get(norm_type, "#94a3b8")
            lbl_t = QLabel(f"TYPE: {norm_type}")
            lbl_t.setStyleSheet(f"color: {type_color}; font-weight: 800; font-size: 10px;")
            layout.addWidget(lbl_t)

        t = QLabel(title)
        t.setWordWrap(True)
        t.setStyleSheet(f"font-size: 22px; font-weight: 800; padding-top: 5px; color: {txt};")
        layout.addWidget(t)

        dates_row = QHBoxLayout()
        c_lbl = QLabel(f"Created: {created_date}")
        c_lbl.setStyleSheet("color: #82AFF2; font-size: 12px;")
        d_lbl = QLabel(f"Due: {due_date}")
        d_lbl.setStyleSheet("color: #3078CD; font-size: 12px; font-weight: bold;")

        dates_row.addWidget(c_lbl)
        dates_row.addSpacing(15)
        dates_row.addWidget(d_lbl)
        dates_row.addStretch()
        layout.addLayout(dates_row)

        layout.addSpacing(15)

        desc_box = QFrame()
        desc_box.setStyleSheet(f"background: {box_bg}; border-radius: 10px; padding: 15px;")
        dl = QVBoxLayout(desc_box)
        dl.setContentsMargins(0,0,0,0)
        lbl_desc = QLabel(desc if desc else "No details provided.")
        lbl_desc.setWordWrap(True)
        lbl_desc.setStyleSheet(f"color: {txt};")
        dl.addWidget(lbl_desc)
        layout.addWidget(desc_box)

        layout.addSpacing(16)

        if self.task_id:
            # Subtasks
            subt_header = QHBoxLayout()
            subt_lbl = QLabel("SUBTASKS")
            subt_lbl.setStyleSheet("font-size: 11px; font-weight: 900; letter-spacing: 1px; color: #82AFF2;")
            self.subtask_summary = QLabel("")
            self.subtask_summary.setStyleSheet("font-size: 10px; font-weight: 700; color: #82AFF2;")
            subt_header.addWidget(subt_lbl)
            subt_header.addStretch()
            subt_header.addWidget(self.subtask_summary)
            layout.addLayout(subt_header)

            subt_card = QFrame()
            subt_card.setStyleSheet(f"background: {box_bg}; border-radius: 10px; padding: 12px;")
            subt_l = QVBoxLayout(subt_card)
            subt_l.setContentsMargins(0, 0, 0, 0)
            subt_l.setSpacing(10)

            add_row = QHBoxLayout()
            add_row.setSpacing(8)
            self.subtask_input = QLineEdit()
            self.subtask_input.setPlaceholderText("Add a subtask…")
            self.subtask_input.setStyleSheet(
                f"background: {input_bg}; border: 1px solid {border}; border-radius: 10px; padding: 8px 10px;"
            )
            self.subtask_input.returnPressed.connect(self._add_subtask)
            add_row.addWidget(self.subtask_input, 1)

            btn_add = QPushButton("+")
            btn_add.setCursor(Qt.CursorShape.PointingHandCursor)
            btn_add.setFixedSize(40, 36)
            btn_add.setStyleSheet(STYLES["btn_secondary"])
            btn_add.clicked.connect(self._add_subtask)
            add_row.addWidget(btn_add)

            btn_del = QPushButton("Delete")
            btn_del.setCursor(Qt.CursorShape.PointingHandCursor)
            btn_del.setFixedHeight(36)
            btn_del.setStyleSheet(STYLES["btn_secondary"])
            btn_del.clicked.connect(self._delete_selected_subtask)
            add_row.addWidget(btn_del)
            subt_l.addLayout(add_row)

            self.subtask_list = QListWidget()
            self.subtask_list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
            self.subtask_list.setWordWrap(True)
            try:
                self.subtask_list.setTextElideMode(Qt.TextElideMode.ElideNone)
            except Exception:
                pass
            try:
                self.subtask_list.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
            except Exception:
                pass
            try:
                self.subtask_list.verticalScrollBar().setSingleStep(12)
            except Exception:
                pass
            try:
                self.subtask_list.setSpacing(8)
            except Exception:
                pass
            self.subtask_list.setStyleSheet(
                f"QListWidget {{ background: transparent; border: 0; color: {txt}; }} "
                f"QListWidget::item {{ background: {input_bg}; border: 1px solid {border}; border-radius: 14px; padding: 10px 12px; }} "
                f"QListWidget::item:selected {{ background: rgba(48, 120, 205, 40); border: 1px solid #82AFF2; }}"
            )
            self.subtask_list.setFixedHeight(170)
            self.subtask_list.itemChanged.connect(self._on_subtask_changed)
            try:
                self.subtask_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
                self.subtask_list.customContextMenuRequested.connect(self._subtask_context_menu)
            except Exception:
                pass
            try:
                self.subtask_list.installEventFilter(self)
            except Exception:
                pass
            subt_l.addWidget(self.subtask_list)
            layout.addWidget(subt_card)

            # Dependencies
            dep_header = QHBoxLayout()
            dep_lbl = QLabel("DEPENDENCIES")
            dep_lbl.setStyleSheet("font-size: 11px; font-weight: 900; letter-spacing: 1px; color: #82AFF2;")
            self.dep_summary = QLabel("")
            self.dep_summary.setStyleSheet("font-size: 10px; font-weight: 700; color: #82AFF2;")
            dep_header.addWidget(dep_lbl)
            dep_header.addStretch()
            dep_header.addWidget(self.dep_summary)
            layout.addLayout(dep_header)

            dep_card = QFrame()
            dep_card.setStyleSheet(f"background: {box_bg}; border-radius: 10px; padding: 12px;")
            dep_l = QVBoxLayout(dep_card)
            dep_l.setContentsMargins(0, 0, 0, 0)
            dep_l.setSpacing(10)

            dep_row = QHBoxLayout()
            dep_row.setSpacing(8)
            btn_add_dep = QPushButton("Add dependency")
            btn_add_dep.setCursor(Qt.CursorShape.PointingHandCursor)
            btn_add_dep.setStyleSheet(STYLES["btn_secondary"])
            btn_add_dep.clicked.connect(self._add_dependency)
            dep_row.addWidget(btn_add_dep)

            btn_del_dep = QPushButton("Remove")
            btn_del_dep.setCursor(Qt.CursorShape.PointingHandCursor)
            btn_del_dep.setStyleSheet(STYLES["btn_secondary"])
            btn_del_dep.clicked.connect(self._remove_selected_dependency)
            dep_row.addWidget(btn_del_dep)
            dep_row.addStretch()
            dep_l.addLayout(dep_row)

            self.dep_list = QListWidget()
            self.dep_list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
            self.dep_list.setWordWrap(True)
            try:
                self.dep_list.setTextElideMode(Qt.TextElideMode.ElideNone)
            except Exception:
                pass
            try:
                self.dep_list.setSpacing(8)
            except Exception:
                pass
            self.dep_list.setStyleSheet(
                f"QListWidget {{ background: transparent; border: 0; color: {txt}; }} "
                f"QListWidget::item {{ background: {input_bg}; border: 1px solid {border}; border-radius: 14px; padding: 10px 12px; }} "
                f"QListWidget::item:selected {{ background: rgba(48, 120, 205, 40); border: 1px solid #82AFF2; }}"
            )
            self.dep_list.setFixedHeight(130)
            dep_l.addWidget(self.dep_list)
            layout.addWidget(dep_card)

            self._load_subtasks()
            self._load_dependencies()

        # Pomodoro summary + sessions
        summary = QLabel(f"Focus total: {format_duration_minutes(total_focus_min)}  -  Sessions: {total_sessions}")
        summary.setStyleSheet(f"color: {txt}; font-size: 11px; font-weight: 700;")
        layout.addWidget(summary)

        sess_box = QFrame()
        sess_box.setStyleSheet(f"background: {box_bg}; border-radius: 10px; padding: 12px;")
        sl = QVBoxLayout(sess_box)
        sl.setContentsMargins(0, 0, 0, 0)
        sl.setSpacing(6)

        sessions_container = QWidget()
        sessions_layout = QVBoxLayout(sessions_container)
        sessions_layout.setContentsMargins(0, 0, 0, 0)
        sessions_layout.setSpacing(6)

        if sessions:
            for s in sessions:
                sessions_layout.addWidget(s)
        else:
            empty = QLabel("No Pomodoro sessions yet.")
            empty.setStyleSheet(f"color: {txt}; font-size: 11px;")
            sessions_layout.addWidget(empty)

        sessions_layout.addStretch()

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("QScrollArea { border: none; background: transparent; }")
        scroll.setWidget(sessions_container)
        scroll.setFixedHeight(160)

        sl.addWidget(scroll)

        layout.addWidget(sess_box)

        btn = QPushButton("Save")
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.setStyleSheet(STYLES["btn_primary"])
        btn.setFixedHeight(38)
        try:
            btn.setDefault(True)
        except Exception:
            pass
        btn.clicked.connect(self.accept)
        footer = QHBoxLayout()
        footer.addStretch()
        footer.addWidget(btn)
        root.addLayout(footer)

    def showEvent(self, event):
        if not self._did_constrain_to_screen:
            self._did_constrain_to_screen = True
            self._constrain_to_screen_height()
        super().showEvent(event)

    def _constrain_to_screen_height(self):
        screen = None
        try:
            screen = self.screen()
        except Exception:
            screen = None
        if screen is None:
            try:
                screen = QApplication.primaryScreen()
            except Exception:
                screen = None
        if screen is None:
            return

        try:
            avail = screen.availableGeometry()
            max_h = max(320, int(avail.height() * 0.92))
            self.setMaximumHeight(max_h)
            hint_h = int(self.sizeHint().height())
            if hint_h > 0:
                self.resize(self.width(), min(hint_h, max_h))
        except Exception:
            return

    def _db(self):
        return _get_db_connection()

    def _format_ts(self, raw, assume_utc: bool = True):
        if raw is None:
            return ""
        raw_text = str(raw).strip().replace("T", " ").split(".")[0]
        if not raw_text:
            return ""
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
            try:
                dt = datetime.strptime(raw_text, fmt)
                if "H" in fmt:
                    if assume_utc:
                        try:
                            dt = dt.replace(tzinfo=timezone.utc).astimezone().replace(tzinfo=None)
                        except Exception:
                            pass
                    return dt.strftime("%b %d, %H:%M")
                return dt.strftime("%b %d, %Y")
            except Exception:
                continue
        return raw_text

    def _subtask_item_text(self, title: str, created_at=None, completed_at=None) -> str:
        safe_title = str(title or "").strip() or "Untitled"
        meta = []
        created_fmt = self._format_ts(created_at, assume_utc=True)
        if created_fmt:
            meta.append(f"Added {created_fmt}")
        completed_fmt = self._format_ts(completed_at, assume_utc=False)
        if completed_fmt:
            meta.append(f"Done {completed_fmt}")
        if meta:
            # Each meta on its own line to avoid elision ("...") in narrow list items.
            return safe_title + "\n" + "\n".join(meta)
        return safe_title

    def _load_subtasks(self):
        if not self.task_id:
            return
        self._loading_subtasks = True
        try:
            self.subtask_list.clear()
            conn = self._db()
            rows = conn.execute(
                "SELECT id, title, is_completed, created_at, completed_at FROM task_subtasks WHERE task_id=? ORDER BY id",
                (int(self.task_id),),
            ).fetchall()
            conn.close()
            total = 0
            done_count = 0
            for sid, title, done, created_at, completed_at in rows:
                total += 1
                is_done = int(done or 0) != 0
                done_count += 1 if is_done else 0
                text = self._subtask_item_text(title, created_at, completed_at if is_done else None)
                item = QListWidgetItem(text)
                item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
                item.setCheckState(Qt.CheckState.Checked if is_done else Qt.CheckState.Unchecked)
                item.setData(Qt.ItemDataRole.UserRole, int(sid))
                item.setData(Qt.ItemDataRole.UserRole + 1, str(title or "").strip())
                item.setData(Qt.ItemDataRole.UserRole + 2, created_at)
                item.setData(Qt.ItemDataRole.UserRole + 3, completed_at)
                lines = max(1, str(text).count("\n") + 1)
                item.setSizeHint(QSize(0, 22 + lines * 18))
                self.subtask_list.addItem(item)
            try:
                if hasattr(self, "subtask_summary"):
                    self.subtask_summary.setText(f"{done_count}/{total} done" if total else "0/0 done")
            except Exception:
                pass
        finally:
            self._loading_subtasks = False

    def _add_subtask(self):
        if not self.task_id:
            return
        text = ""
        try:
            text = self.subtask_input.text().strip()
        except Exception:
            text = ""
        if not text:
            return
        conn = self._db()
        conn.execute(
            "INSERT INTO task_subtasks (task_id, title, is_completed) VALUES (?,?,0)",
            (int(self.task_id), text),
        )
        conn.commit()
        conn.close()
        try:
            self.subtask_input.clear()
        except Exception:
            pass
        self._load_subtasks()

    def _delete_selected_subtask(self):
        if not self.task_id:
            return
        item = self.subtask_list.currentItem() if hasattr(self, "subtask_list") else None
        if not item:
            return
        sid = item.data(Qt.ItemDataRole.UserRole)
        if not sid:
            return
        conn = self._db()
        conn.execute("DELETE FROM task_subtasks WHERE id=? AND task_id=?", (int(sid), int(self.task_id)))
        conn.commit()
        conn.close()
        self._load_subtasks()

    def _subtask_context_menu(self, pos):
        if not hasattr(self, "subtask_list"):
            return
        item = self.subtask_list.itemAt(pos)
        if not item:
            return
        menu = QMenu(self)
        act_del = menu.addAction("Delete subtask")
        chosen = menu.exec(self.subtask_list.mapToGlobal(pos))
        if chosen == act_del:
            try:
                self.subtask_list.setCurrentItem(item)
            except Exception:
                pass
            self._delete_selected_subtask()

    def eventFilter(self, obj, event):
        try:
            if obj is getattr(self, "subtask_list", None) and event.type() == QEvent.Type.KeyPress:
                if event.key() in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
                    self._delete_selected_subtask()
                    return True
        except Exception:
            pass
        return super().eventFilter(obj, event)

    def _on_subtask_changed(self, item):
        if self._loading_subtasks or not self.task_id:
            return
        sid = item.data(Qt.ItemDataRole.UserRole)
        if not sid:
            return
        try:
            # Make the toggled item the current selection so the "Delete" button works as expected.
            self.subtask_list.setCurrentItem(item)
        except Exception:
            pass
        done = item.checkState() == Qt.CheckState.Checked
        completed_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S") if done else None
        conn = self._db()
        conn.execute(
            "UPDATE task_subtasks SET is_completed=?, completed_at=? WHERE id=? AND task_id=?",
            (1 if done else 0, completed_at, int(sid), int(self.task_id)),
        )
        conn.commit()
        conn.close()
        self._load_subtasks()

    def _load_dependencies(self):
        if not self.task_id:
            return
        self._loading_dependencies = True
        try:
            self.dep_list.clear()
            conn = self._db()
            rows = conn.execute(
                """SELECT d.depends_on_task_id, t.title, t.is_completed, t.created_date, t.due_date
                   FROM task_dependencies d
                   JOIN tasks t ON t.id = d.depends_on_task_id
                   WHERE d.task_id = ?
                   ORDER BY t.is_completed, t.due_date""",
                (int(self.task_id),),
            ).fetchall()
            conn.close()
            for dep_id, title, done, created, due in rows:
                meta = []
                if created:
                    meta.append(f"Created {created}")
                if due:
                    meta.append(f"Due {due}")
                if int(done or 0):
                    meta.append("completed")
                text = str(title or "").strip() or "Untitled"
                if meta:
                    text += "\n" + "  ·  ".join(meta)
                item = QListWidgetItem(text)
                item.setData(Qt.ItemDataRole.UserRole, int(dep_id))
                lines = max(1, str(text).count("\n") + 1)
                item.setSizeHint(QSize(0, 18 + lines * 18))
                self.dep_list.addItem(item)
            try:
                if hasattr(self, "dep_summary"):
                    self.dep_summary.setText(f"{len(rows)} item(s)" if rows else "0 item")
            except Exception:
                pass
        finally:
            self._loading_dependencies = False

    def _add_dependency(self):
        if not self.task_id:
            return
        conn = self._db()
        try:
            current = conn.execute(
                "SELECT due_date FROM tasks WHERE id=?",
                (int(self.task_id),),
            ).fetchone()
            if not current:
                return
            cur_due = str(current[0] or "").strip()

            # Only allow dependencies whose due date is not after this task's due date.
            rows = conn.execute(
                "SELECT id, title, created_date, due_date, is_completed FROM tasks WHERE id != ? ORDER BY due_date",
                (int(self.task_id),),
            ).fetchall()
        finally:
            conn.close()
        if not rows:
            QMessageBox.information(self, "Dependencies", "No other tasks found.")
            return
        labels = []
        mapping = {}
        for tid, title, created, due, done in rows:
            created_s = str(created or "").strip()
            due_s = str(due or "").strip()

            if cur_due:
                # If the current task has a deadline, require the dependency to have a deadline
                # and to be due on/before the current task's deadline.
                if not due_s:
                    continue
                if due_s > cur_due:
                    continue

            meta = []
            if created_s:
                meta.append(f"Created {created_s}")
            if due_s:
                meta.append(f"Due {due_s}")
            if int(done or 0):
                meta.append("completed")
            suffix = f"  ({' · '.join(meta)})" if meta else ""
            label = f"{title}{suffix}"
            labels.append(label)
            mapping[label] = int(tid)
        if not labels:
            QMessageBox.information(
                self,
                "Dependencies",
                "No eligible dependencies found.\n\nDependencies must have a due date on/before this task's due date.",
            )
            return
        choice, ok = QInputDialog.getItem(self, "Add dependency", "This task depends on:", labels, 0, False)
        if not ok:
            return
        dep_id = mapping.get(choice)
        if not dep_id:
            return
        conn = self._db()
        try:
            conn.execute(
                "INSERT OR IGNORE INTO task_dependencies (task_id, depends_on_task_id) VALUES (?, ?)",
                (int(self.task_id), int(dep_id)),
            )
            conn.commit()
        finally:
            conn.close()
        self._load_dependencies()

    def _remove_selected_dependency(self):
        if not self.task_id:
            return
        item = self.dep_list.currentItem() if hasattr(self, "dep_list") else None
        if not item:
            return
        dep_id = item.data(Qt.ItemDataRole.UserRole)
        if not dep_id:
            return
        conn = self._db()
        conn.execute(
            "DELETE FROM task_dependencies WHERE task_id=? AND depends_on_task_id=?",
            (int(self.task_id), int(dep_id)),
        )
        conn.commit()
        conn.close()
        self._load_dependencies()

# --- TASK CARD ---
class TaskCard(QFrame):
    def __init__(
        self,
        t_id,
        title,
        desc,
        due_date_pretty,
        created_date_pretty,
        priority,
        focus_minutes,
        parent_page,
        is_completed=False,
        task_type=None,
        extra_meta: str | None = None,
    ):
        super().__init__()
        self.t_id = t_id
        self.parent_page = parent_page
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMinimumWidth(240)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setObjectName("TaskCard")

        self.priority = normalize_priority(priority) or "too low"
        self.task_type = normalize_task_type(task_type)
        self.title_text = title
        self.current_theme = "Light"
        self.accent_color = PRIORITY_COLORS.get(self.priority.lower(), "#94a3b8")
        self.focus_minutes = int(focus_minutes or 0)

        self.shadow = QGraphicsDropShadowEffect(self)
        self.shadow.setBlurRadius(24)
        self.shadow.setXOffset(0)
        self.shadow.setYOffset(10)
        self.shadow.setColor(QColor(17, 51, 86, 35))
        self.setGraphicsEffect(self.shadow)

        outer = QHBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        self.accent_bar = QFrame(self)
        self.accent_bar.setFixedWidth(6)
        self.accent_bar.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Expanding)
        self.accent_bar.setObjectName("TaskAccent")

        accent_container = QWidget(self)
        accent_container.setFixedWidth(12)
        accent_layout = QVBoxLayout(accent_container)
        accent_layout.setContentsMargins(6, 12, 0, 12)
        accent_layout.addWidget(self.accent_bar)

        content = QWidget(self)
        layout = QVBoxLayout(content)
        layout.setContentsMargins(16, 18, 18, 18)
        layout.setSpacing(10)

        outer.addWidget(accent_container)
        outer.addWidget(content)

        # Header
        header = QHBoxLayout()
        self.checkbox = QCheckBox(content)
        self.checkbox.setCursor(Qt.CursorShape.PointingHandCursor)
        self.checkbox.toggled.connect(self.on_checked)

        self.badge = QLabel(self.priority.upper(), content)
        self.badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.badge.setFixedSize(78, 22)
        self.badge.setStyleSheet("font-size: 9px; font-weight: 900;")

        self.type_badge = QLabel("", content)
        self.type_badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.type_badge.setFixedHeight(20)
        self.type_badge.setVisible(bool(self.task_type))

        header.addWidget(self.checkbox)
        header.addStretch()
        header.addWidget(self.type_badge)
        header.addWidget(self.badge)
        layout.addLayout(header)

        self.lbl_title = QLabel(title, content)
        self.lbl_title.setWordWrap(True)
        layout.addWidget(self.lbl_title)

        self.lbl_desc = QLabel(desc if desc else "", content)
        self.lbl_desc.setWordWrap(True)
        self.lbl_desc.setMaximumHeight(40)
        self.lbl_desc.setVisible(bool(desc))
        layout.addWidget(self.lbl_desc)

        self.lbl_extra = QLabel(extra_meta or "", content)
        self.lbl_extra.setWordWrap(True)
        self.lbl_extra.setVisible(bool(extra_meta))
        layout.addWidget(self.lbl_extra)

        footer = QVBoxLayout()
        footer.setSpacing(8)
        dates_layout = QVBoxLayout()
        dates_layout.setSpacing(2)

        self.lbl_created = QLabel(f"Created: {created_date_pretty}", content)
        self.lbl_created.setWordWrap(True)
        self.lbl_created.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)

        self.lbl_due = QLabel(f"Due: {due_date_pretty}", content)
        self.lbl_due.setWordWrap(True)
        self.lbl_due.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)

        dates_layout.addWidget(self.lbl_created)
        dates_layout.addWidget(self.lbl_due)

        self.lbl_focus = QLabel(f"Focus: {format_duration_minutes(self.focus_minutes)}", content)
        self.lbl_focus.setWordWrap(True)
        self.lbl_focus.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        dates_layout.addWidget(self.lbl_focus)

        actions_layout = QHBoxLayout()
        actions_layout.setSpacing(6)
        self.btn_edit = QPushButton("EDIT", content)
        self.btn_edit.setFixedHeight(20)
        self.btn_edit.setMinimumWidth(52)
        self.btn_edit.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_edit.setStyleSheet(
            "QPushButton { background-color: #3078CD; border: 1px solid #25456B; color: white; "
            "border-radius: 8px; font-weight: bold; font-size: 10px; padding: 0px 10px; }"
            "QPushButton:hover { background-color: #25456B; border-color: #25456B; }"
        )
        self.btn_edit.clicked.connect(self.on_edit)

        self.btn_del = QPushButton("DELETE", content)
        self.btn_del.setFixedHeight(20)
        self.btn_del.setMinimumWidth(60)
        self.btn_del.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_del.setStyleSheet(
            "QPushButton { background-color: #ef4444; border: 1px solid #b91c1c; color: white; "
            "border-radius: 8px; font-weight: bold; font-size: 10px; padding: 0px 10px; }"
            "QPushButton:hover { background-color: #b91c1c; border-color: #7f1d1d; }"
        )
        self.btn_del.clicked.connect(self.on_delete)

        self.btn_focus = QPushButton("FOCUS", content)
        self.btn_focus.setFixedHeight(20)
        self.btn_focus.setMinimumWidth(58)
        self.btn_focus.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_focus.clicked.connect(self.on_focus)

        actions_layout.addStretch()
        actions_layout.addWidget(self.btn_focus)
        actions_layout.addWidget(self.btn_edit)
        actions_layout.addWidget(self.btn_del)

        footer.addLayout(dates_layout)
        footer.addLayout(actions_layout)
        layout.addLayout(footer)

        self._set_checked_state(is_completed)
        self.update_theme("Light")

    def _set_checked_state(self, checked):
        checked = bool(checked)
        self.checkbox.blockSignals(True)
        self.checkbox.setChecked(checked)
        self.checkbox.blockSignals(False)
        f = self.lbl_title.font()
        f.setStrikeOut(checked)
        self.lbl_title.setFont(f)
        self._apply_text_styles(checked)

    def _apply_text_styles(self, checked=False):
        colors = get_theme(self.current_theme)
        checked_color = "#22c55e"
        if self.current_theme == "Dark":
            title_color = checked_color if checked else colors["text"]
            desc_color = colors["sub"] if not checked else checked_color
            extra_color = colors["sub"] if not checked else checked_color
        else:
            title_color = checked_color if checked else colors["text"]
            desc_color = checked_color if checked else colors["sub"]
            extra_color = checked_color if checked else colors["sub"]

        self.lbl_title.setStyleSheet(f"color: {title_color}; font-size: 15px; font-weight: 800; border: none; background: transparent;")
        self.lbl_desc.setStyleSheet(f"color: {desc_color}; font-size: 11px; border: none; background: transparent;")
        self.lbl_extra.setStyleSheet(f"color: {extra_color}; font-size: 10px; font-weight: 700; border: none; background: transparent;")

    def update_theme(self, theme):
        self.current_theme = theme
        colors = get_theme(theme)
        accent = self.accent_color
        if theme == "Dark":
            card_bg = f"qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 {colors['card_alt']}, stop:1 {colors['bg']})"
            border = colors["border"]
            hover_border = colors["accent2"]
            created_color = colors["sub"]
            due_color = colors["accent2"]
            focus_color = colors["accent"]
            checkbox_border = colors["border"]
            checkbox_bg = colors["card_alt"]
            edit_style = (
                f"QPushButton {{ background-color: {colors['accent']}; border: 1px solid {colors['deep']}; color: white; "
                f"border-radius: 8px; font-weight: bold; font-size: 10px; padding: 0px 10px; }} "
                f"QPushButton:hover {{ background-color: {colors['deep']}; border-color: {colors['deep']}; }}"
            )
            del_style = (
                "QPushButton { background-color: #ef4444; border: 1px solid #7f1d1d; color: white; "
                "border-radius: 8px; font-weight: bold; font-size: 10px; padding: 0px 10px; }"
                "QPushButton:hover { background-color: #b91c1c; border-color: #7f1d1d; }"
            )
            focus_style = (
                "QPushButton { background-color: #22c55e; border: 1px solid #15803d; color: white; "
                "border-radius: 8px; font-weight: bold; font-size: 10px; padding: 0px 10px; }"
                "QPushButton:hover { background-color: #16a34a; border-color: #166534; }"
            )
        else:
            card_bg = f"qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 {colors['card']}, stop:1 {colors['accent_soft']})"
            border = colors["border"]
            hover_border = colors["accent"]
            created_color = colors["sub"]
            due_color = colors["deep"]
            focus_color = colors["accent"]
            checkbox_border = colors["border"]
            checkbox_bg = colors["card"]
            edit_style = (
                f"QPushButton {{ background-color: {colors['accent']}; border: 1px solid {colors['deep']}; color: white; "
                f"border-radius: 8px; font-weight: bold; font-size: 10px; padding: 0px 10px; }} "
                f"QPushButton:hover {{ background-color: {colors['deep']}; border-color: {colors['deep']}; }}"
            )
            del_style = (
                "QPushButton { background-color: #ef4444; border: 1px solid #b91c1c; color: white; "
                "border-radius: 8px; font-weight: bold; font-size: 10px; padding: 0px 10px; }"
                "QPushButton:hover { background-color: #b91c1c; border-color: #7f1d1d; }"
            )
            focus_style = (
                "QPushButton { background-color: #22c55e; border: 1px solid #16a34a; color: white; "
                "border-radius: 8px; font-weight: bold; font-size: 10px; padding: 0px 10px; }"
                "QPushButton:hover { background-color: #16a34a; border-color: #166534; }"
            )

        self.setStyleSheet(
            f"QFrame#TaskCard {{ background: {card_bg}; border: 1px solid {border}; border-radius: 22px; }}"
            f"QFrame#TaskCard:hover {{ border: 1px solid {hover_border}; }}"
        )

        self.accent_bar.setStyleSheet(f"background: {accent}; border-radius: 3px;")

        self.checkbox.setStyleSheet(
            f"QCheckBox::indicator {{ width: 18px; height: 18px; border-radius: 6px; "
            f"border: 2px solid {checkbox_border}; background: {checkbox_bg}; }}"
            "QCheckBox::indicator:checked { background-color: #22c55e; border-color: #16a34a; }"
        )

        self.badge.setStyleSheet(
            f"background: {accent}; color: white; border-radius: 11px; font-size: 9px; "
            "font-weight: 900; padding: 2px 6px;"
        )
        if self.task_type:
            type_color = TASK_TYPE_COLORS.get(self.task_type, "#94a3b8")
            self.type_badge.setText(self.task_type.upper())
            self.type_badge.setVisible(True)
            self.type_badge.setStyleSheet(
                f"background: {type_color}; color: white; border-radius: 10px; font-size: 8px; "
                "font-weight: 800; padding: 2px 6px;"
            )
        else:
            self.type_badge.setVisible(False)
        self.lbl_created.setStyleSheet(f"color: {created_color}; font-size: 10px; border: none; background: transparent;")
        self.lbl_due.setStyleSheet(f"color: {due_color}; font-size: 11px; font-weight: 800; border: none; background: transparent;")
        self.lbl_focus.setStyleSheet(f"color: {focus_color}; font-size: 10px; font-weight: 700; border: none; background: transparent;")
        self.btn_edit.setStyleSheet(edit_style)
        self.btn_del.setStyleSheet(del_style)
        self.btn_focus.setStyleSheet(focus_style)
        self._apply_text_styles(self.checkbox.isChecked())

    def enterEvent(self, event):
        self.shadow.setColor(QColor(48, 120, 205, 80))
        self.shadow.setBlurRadius(34)
        self.shadow.setYOffset(14)
        super().enterEvent(event)

    def leaveEvent(self, event):
        self.shadow.setColor(QColor(17, 51, 86, 45))
        self.shadow.setBlurRadius(24)
        self.shadow.setYOffset(10)
        super().leaveEvent(event)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.parent_page.show_task_details(self.t_id)
        super().mousePressEvent(event)

    def on_checked(self, checked):
        f = self.lbl_title.font()
        f.setStrikeOut(checked)
        self.lbl_title.setFont(f)
        self._apply_text_styles(checked)
        self.parent_page.mark_task_completed(self.t_id, checked)
        self.parent_page.task_added.emit()

    def on_edit(self): self.parent_page.edit_task(self.t_id)
    def on_delete(self): self.parent_page.delete_task(self.t_id)
    def on_focus(self): self.parent_page.start_pomodoro(self.t_id, self.title_text, self.priority, self.task_type)

# --- COLUMNS ---
class DayColumn(QWidget):
    def __init__(self, title, is_today=False, theme="Light"):
        super().__init__()
        self.setMinimumWidth(320)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.cards = []
        self.title = title
        self.count = 0
        self.is_today = is_today
        self.current_theme = theme
        self._label_color = None
        self._label_base_size = 14
        self._label_min_size = 8
        self.setObjectName("DayColumn")

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        self.panel = QFrame(self)
        self.panel.setObjectName("DayPanel")
        self.panel.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.panel.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)

        layout = QVBoxLayout(self.panel)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)

        self.lbl = QLabel(title, self.panel)
        self.lbl.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        layout.addWidget(self.lbl)

        self.accent = QFrame(self.panel)
        self.accent.setFixedHeight(3)
        self.accent.setMaximumWidth(70)
        layout.addWidget(self.accent)

        self.card_layout = QVBoxLayout()
        self.card_layout.setSpacing(16)
        self.card_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.card_layout.setContentsMargins(0, 6, 0, 6)

        layout.addLayout(self.card_layout)
        layout.addStretch()
        outer.addWidget(self.panel, alignment=Qt.AlignmentFlag.AlignTop)
        outer.addStretch()
        self.update_theme(theme)

    def add_task_card(self, card):
        self.cards.append(card)
        self.count += 1
        self._update_label()
        self.card_layout.addWidget(card)

    def _update_label(self):
        self._apply_label_fit()

    def _label_text_full(self):
        suffix = "task" if self.count == 1 else "tasks"
        return f"{self.title}  -  {self.count} {suffix}"

    def _title_available_width(self):
        available = self.lbl.width()
        if available <= 10:
            available = max(10, self.panel.width() - 24)
        return available

    def _fit_size_for_text(self, base_size):
        text = self._label_text_full()
        available = self._title_available_width()
        font = self.lbl.font()
        chosen_size = self._label_min_size
        for size in range(int(base_size), self._label_min_size - 1, -1):
            font.setPointSize(size)
            metrics = QFontMetrics(font)
            if metrics.horizontalAdvance(text) <= available:
                chosen_size = size
                break
        return chosen_size

    def apply_title_font_size(self, size):
        text = self._label_text_full()
        font = self.lbl.font()
        font.setPointSize(int(size))
        self.lbl.setFont(font)
        self.lbl.setText(text)
        self.lbl.setToolTip(text)

    def _apply_label_fit(self):
        parent = self.parentWidget()
        while parent is not None and not hasattr(parent, "_sync_column_title_sizes"):
            parent = parent.parentWidget()
        if parent and hasattr(parent, "_sync_column_title_sizes"):
            parent._sync_column_title_sizes()
            return
        base_size = self._label_base_size
        available = self._title_available_width()
        if available > 0:
            base_size = int(round(max(self._label_min_size, min(self._label_base_size, available / 18))))
        chosen_size = self._fit_size_for_text(base_size)
        self.apply_title_font_size(chosen_size)

    def update_theme(self, theme):
        self.current_theme = theme
        is_today = self.is_today
        colors = get_theme(theme)
        color = colors["accent"] if is_today else colors["sub"]
        self._label_color = color
        self.lbl.setStyleSheet(f"font-weight: 900; color: {color}; margin-bottom: 8px; background: transparent; border: none;")
        accent_color = colors["accent"] if is_today else colors["border"]
        self.accent.setStyleSheet(f"background: {accent_color}; border-radius: 2px;")
        if theme == "Dark":
            self.panel.setStyleSheet(
                f"QFrame#DayPanel {{ background: {rgba(colors['card_alt'], 0.7)}; border: 1px solid {colors['border']}; border-radius: 18px; }}"
            )
        else:
            self.panel.setStyleSheet(
                f"QFrame#DayPanel {{ background: {rgba(colors['card'], 0.85)}; border: 1px solid {colors['border']}; border-radius: 18px; }}"
            )
        for card in self.cards:
            card.update_theme(theme)
        self._apply_label_fit()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._apply_label_fit()

# --- MAIN PAGE ---
class TasksPage(QWidget):
    task_added = pyqtSignal()
    pomodoro_requested = pyqtSignal(int, str, str, object)

    def __init__(self):
        super().__init__()
        self.current_theme = "Light"
        self.task_reminders_enabled = True
        self.sound_effects = True
        self.enable_notifications = True
        self._reminder_timer = QTimer(self)
        self._reminder_timer.setInterval(60 * 1000)  # check every minute
        self._reminder_timer.timeout.connect(self._check_reminders)
        self._last_remind = {}  # map task_id -> datetime of last reminder
        self._reminder_repeat_minutes = 10
        self._tray = None
        self.columns = []
        self.empty_state = None
        self.empty_title = None
        self.empty_subtitle = None
        self.setObjectName("TasksPage")

        main = QVBoxLayout(self)
        main.setContentsMargins(40, 32, 40, 0)
        main.setSpacing(18)

        # Header
        self.header_frame = QFrame()
        self.header_frame.setObjectName("TasksHeader")
        header_layout = QGridLayout(self.header_frame)
        header_layout.setContentsMargins(24, 20, 24, 20)
        header_layout.setSpacing(16)
        self.header_layout = header_layout

        txt_layout = QVBoxLayout()
        txt_layout.setSpacing(6)

        self.welcome = QLabel("MY TASKS")
        self.title = QLabel("Personal Workspace")
        self.title.setWordWrap(True)
        self.subtitle = QLabel("Your solo task list")
        self.subtitle.setWordWrap(True)

        txt_layout.addWidget(self.welcome)
        txt_layout.addWidget(self.title)
        txt_layout.addWidget(self.subtitle)
        self.header_text_layout = txt_layout

        chips_layout = QHBoxLayout()
        chips_layout.setSpacing(8)
        self.chip_total = QLabel("0 tasks")
        self.chip_due = QLabel("0 due today")
        chips_layout.addWidget(self.chip_total)
        chips_layout.addWidget(self.chip_due)
        self.header_chips_layout = chips_layout

        btn_add = QPushButton("+ New Task")
        btn_add.setMinimumSize(120, 44)
        btn_add.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        btn_add.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_add.setStyleSheet(STYLES["btn_primary"])
        shadow = QGraphicsDropShadowEffect(btn_add)
        shadow.setColor(QColor(48, 120, 205, 90))
        shadow.setBlurRadius(18)
        shadow.setYOffset(6)
        btn_add.setGraphicsEffect(shadow)
        btn_add.clicked.connect(self.prompt_new_task)

        self.header_add_btn = btn_add
        self._reflow_header()
        main.addWidget(self.header_frame)

        header_shadow = QGraphicsDropShadowEffect(self.header_frame)
        header_shadow.setColor(QColor(17, 51, 86, 30))
        header_shadow.setBlurRadius(20)
        header_shadow.setYOffset(6)
        self.header_frame.setGraphicsEffect(header_shadow)

        # Filters
        self._init_filters(main)

        # Scroll Area
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("""
            QScrollArea { border: none; background: transparent; }
            QScrollBar:horizontal { height: 10px; background: rgba(15, 23, 42, 20); border-radius: 5px; }
            QScrollBar::handle:horizontal { background: rgba(48, 120, 205, 89); border-radius: 5px; }
            QScrollBar::handle:horizontal:hover { background: rgba(48, 120, 205, 140); }
        """)
        self.scroll = scroll

        container = QWidget()
        container.setStyleSheet("background: transparent;")
        self.columns_layout = QGridLayout(container)
        self.columns_layout.setSpacing(18)
        self.columns_layout.setContentsMargins(6, 0, 6, 0)
        self.columns_layout.setAlignment(Qt.AlignmentFlag.AlignTop)

        scroll.setWidget(container)
        main.addWidget(scroll)

        self.init_db()
        self.refresh_tasks()
        # Load settings (starts/stops reminder timer)
        try:
            self.apply_settings()
        except Exception:
            pass

    def showEvent(self, event):
        self.refresh_tasks()
        self._reflow_header()
        self._reflow_filters()
        self._sync_column_title_sizes()
        super().showEvent(event)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._reflow_columns()
        self._reflow_header()
        self._reflow_filters()
        self._sync_column_title_sizes()

    def _init_filters(self, main_layout):
        self.filter_frame = QFrame()
        self.filter_frame.setObjectName("TasksFilters")
        filter_layout = QGridLayout(self.filter_frame)
        filter_layout.setContentsMargins(18, 12, 18, 12)
        filter_layout.setHorizontalSpacing(12)
        filter_layout.setVerticalSpacing(8)
        self.filter_layout = filter_layout

        self.filter_search = QLineEdit()
        self.filter_search.setObjectName("FilterSearch")
        self.filter_search.setPlaceholderText("Search title or description...")
        self.filter_search.setClearButtonEnabled(True)
        self.filter_search.setMinimumHeight(34)
        self.filter_search.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

        self.filter_type = NoWheelComboBox()
        self.filter_type.setObjectName("FilterCombo")
        self.filter_type.setMinimumHeight(34)
        self.filter_type.addItem("All Types", userData="all")
        for t in TASK_TYPES:
            self.filter_type.addItem(t, userData=normalize_task_type(t))
        self.filter_type.addItem(UNCATEGORIZED_LABEL, userData="uncategorized")

        self.filter_priority = NoWheelComboBox()
        self.filter_priority.setObjectName("FilterCombo")
        self.filter_priority.setMinimumHeight(34)
        self.filter_priority.addItem("All Priorities", userData="all")
        self.filter_priority.addItem("High", userData="high")
        self.filter_priority.addItem("Medium", userData="medium")
        self.filter_priority.addItem("Low", userData="low")
        self.filter_priority.addItem("Too low", userData="too low")

        self.filter_status = NoWheelComboBox()
        self.filter_status.setObjectName("FilterCombo")
        self.filter_status.setMinimumHeight(34)
        self.filter_status.addItem("All", userData="all")
        self.filter_status.addItem("Active", userData="active")
        self.filter_status.addItem("Completed", userData="completed")

        self.filter_due = NoWheelComboBox()
        self.filter_due.setObjectName("FilterCombo")
        self.filter_due.setMinimumHeight(34)
        self.filter_due.addItem("Any due date", userData="any")
        self.filter_due.addItem("Overdue", userData="overdue")
        self.filter_due.addItem("Due today", userData="today")
        self.filter_due.addItem("Next 7 days", userData="next7")
        self.filter_due.addItem("No deadline", userData="none")

        self.filter_clear_btn = QPushButton("Clear")
        self.filter_clear_btn.setObjectName("FilterClear")
        self.filter_clear_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.filter_clear_btn.setStyleSheet(STYLES["btn_secondary"])
        self.filter_clear_btn.setFixedHeight(34)

        self.filter_fields = {
            "search": self._build_filter_field("Search", self.filter_search),
            "type": self._build_filter_field("Type", self.filter_type),
            "priority": self._build_filter_field("Priority", self.filter_priority),
            "status": self._build_filter_field("Status", self.filter_status),
            "due": self._build_filter_field("Due", self.filter_due),
            "clear": self._build_filter_button_field(self.filter_clear_btn),
        }

        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(250)
        self._search_timer.timeout.connect(self.refresh_tasks)

        self._load_filter_defaults()

        self.filter_search.textChanged.connect(self._on_search_text_changed)
        for combo in (self.filter_type, self.filter_priority, self.filter_status, self.filter_due):
            combo.currentIndexChanged.connect(self.refresh_tasks)
        self.filter_clear_btn.clicked.connect(self._clear_filters)

        self._reflow_filters()
        main_layout.addWidget(self.filter_frame)

    def _build_filter_field(self, label_text, widget):
        field = QWidget()
        layout = QVBoxLayout(field)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        label = QLabel(label_text)
        label.setObjectName("FilterLabel")
        layout.addWidget(label)
        layout.addWidget(widget)
        return field

    def _build_filter_button_field(self, button):
        field = QWidget()
        layout = QVBoxLayout(field)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        spacer = QLabel("")
        spacer.setMinimumHeight(14)
        layout.addWidget(spacer)
        layout.addWidget(button)
        return field

    def _read_show_completed_setting(self):
        show_completed = True
        try:
            if os.path.exists("settings.json"):
                with open("settings.json", "r") as sf:
                    sdata = json.load(sf)
                    show_completed = bool(sdata.get("show_completed", True))
        except Exception:
            show_completed = True
        return show_completed

    def _load_filter_defaults(self):
        show_completed = self._read_show_completed_setting()
        self._default_status_filter = "all" if show_completed else "active"
        idx = self.filter_status.findData(self._default_status_filter)
        if idx >= 0:
            self.filter_status.setCurrentIndex(idx)
        self._default_due_filter = "any"

    def _on_search_text_changed(self, _):
        try:
            self._search_timer.start()
        except Exception:
            self.refresh_tasks()

    def _clear_filters(self):
        self.filter_search.clear()
        self.filter_type.setCurrentIndex(0)
        self.filter_priority.setCurrentIndex(0)
        idx = self.filter_status.findData(self._default_status_filter)
        if idx >= 0:
            self.filter_status.setCurrentIndex(idx)
        self.filter_due.setCurrentIndex(0)
        self.refresh_tasks()

    def _current_filters(self):
        return {
            "search": (self.filter_search.text() or "").strip(),
            "type": self.filter_type.currentData() or "all",
            "priority": self.filter_priority.currentData() or "all",
            "status": self.filter_status.currentData() or self._default_status_filter,
            "due": self.filter_due.currentData() or "any",
        }

    def _filters_active(self, filters):
        if filters.get("search"):
            return True
        if filters.get("type") != "all":
            return True
        if filters.get("priority") != "all":
            return True
        if filters.get("status") != getattr(self, "_default_status_filter", "all"):
            return True
        if filters.get("due") != getattr(self, "_default_due_filter", "any"):
            return True
        return False

    def _reflow_filters(self):
        if not hasattr(self, "filter_layout"):
            return
        layout = self.filter_layout
        while layout.count():
            layout.takeAt(0)

        width = self.filter_frame.width() if hasattr(self, "filter_frame") else self.width()

        if width < 680:
            layout.addWidget(self.filter_fields["search"], 0, 0)
            layout.addWidget(self.filter_fields["type"], 1, 0)
            layout.addWidget(self.filter_fields["priority"], 2, 0)
            layout.addWidget(self.filter_fields["status"], 3, 0)
            layout.addWidget(self.filter_fields["due"], 4, 0)
            layout.addWidget(self.filter_fields["clear"], 5, 0)
            layout.setColumnStretch(0, 1)
        elif width < 980:
            layout.addWidget(self.filter_fields["search"], 0, 0, 1, 2)
            layout.addWidget(self.filter_fields["clear"], 0, 2, alignment=Qt.AlignmentFlag.AlignBottom)
            layout.addWidget(self.filter_fields["type"], 1, 0)
            layout.addWidget(self.filter_fields["priority"], 1, 1)
            layout.addWidget(self.filter_fields["status"], 1, 2)
            layout.addWidget(self.filter_fields["due"], 2, 0)
            layout.setColumnStretch(0, 1)
            layout.setColumnStretch(1, 1)
            layout.setColumnStretch(2, 0)
        else:
            layout.addWidget(self.filter_fields["search"], 0, 0)
            layout.addWidget(self.filter_fields["type"], 0, 1)
            layout.addWidget(self.filter_fields["priority"], 0, 2)
            layout.addWidget(self.filter_fields["status"], 0, 3)
            layout.addWidget(self.filter_fields["due"], 0, 4)
            layout.addWidget(self.filter_fields["clear"], 0, 5, alignment=Qt.AlignmentFlag.AlignBottom)
            layout.setColumnStretch(0, 1)

    def _sync_column_title_sizes(self):
        if not self.columns:
            return
        min_available = None
        for col in self.columns:
            try:
                available = col._title_available_width()
            except Exception:
                continue
            if available > 0 and (min_available is None or available < min_available):
                min_available = available
        if not min_available:
            return

        sample_col = self.columns[0]
        base_size = max(sample_col._label_min_size, min(sample_col._label_base_size, int(round(min_available / 18))))
        sizes = []
        for col in self.columns:
            try:
                sizes.append(col._fit_size_for_text(base_size))
            except Exception:
                pass
        if not sizes:
            return
        target_size = min(sizes)
        for col in self.columns:
            try:
                col.apply_title_font_size(target_size)
            except Exception:
                pass

    # --- THEME MANAGER ---
    def update_theme(self, theme):
        self.current_theme = theme
        colors = get_theme(theme)
        if theme == "Dark":
            page_bg = f"qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 {colors['bg']}, stop:1 {colors['card_alt']})"
            header_bg = f"qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 {colors['card_alt']}, stop:1 {colors['bg']})"
            chip_bg = colors["card_alt"]
            chip_border = colors["border"]
            chip_total_color = colors["text"]
            chip_due_color = colors["accent"]
        else:
            page_bg = f"qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 {colors['bg']}, stop:1 {colors['accent_soft']})"
            header_bg = f"qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 {colors['card']}, stop:1 {colors['accent_soft']})"
            chip_bg = colors["card"]
            chip_border = colors["border"]
            chip_total_color = colors["text"]
            chip_due_color = colors["deep"]

        self.setStyleSheet(
            "QWidget { font-family: '%s', 'Segoe UI'; }"
            "QWidget#TasksPage { background: %s; }" % (FONT_FAMILY, page_bg)
        )
        self.header_frame.setStyleSheet(
            "QFrame#TasksHeader { background: %s; border: 1px solid %s; border-radius: 18px; }" %
            (header_bg, colors["border"])
        )
        self.welcome.setStyleSheet(f"font-size: 11px; font-weight: 800; color: {colors['accent']}; background: transparent; border: none;")
        self.title.setStyleSheet(f"font-size: 34px; font-weight: 900; color: {colors['text']}; background: transparent; border: none;")
        self.subtitle.setStyleSheet(f"font-size: 13px; font-weight: 600; color: {colors['sub']}; background: transparent; border: none;")
        self.chip_total.setStyleSheet(
            "background: %s; color: %s; border: 1px solid %s; border-radius: 999px; padding: 7px 14px; font-size: 11px; font-weight: 700;" %
            (chip_bg, chip_total_color, chip_border)
        )
        self.chip_due.setStyleSheet(
            "background: %s; color: %s; border: 1px solid %s; border-radius: 999px; padding: 7px 14px; font-size: 11px; font-weight: 700;" %
            (chip_bg, chip_due_color, chip_border)
        )

        if self.current_theme == "Dark":
            filter_bg = rgba(colors["card_alt"], 0.8)
            filter_label = colors["sub"]
        else:
            filter_bg = rgba(colors["card"], 0.9)
            filter_label = colors["deep"]

        self.filter_frame.setStyleSheet(
            "QFrame#TasksFilters { background: %s; border: 1px solid %s; border-radius: 16px; }"
            "QLabel#FilterLabel { color: %s; font-size: 10px; font-weight: 700; }"
            "QLineEdit#FilterSearch, QComboBox#FilterCombo { background: %s; border: 1px solid %s; border-radius: 10px; padding: 6px 10px; color: %s; }"
            "QLineEdit#FilterSearch:focus, QComboBox#FilterCombo:focus { border: 1px solid %s; }"
            "QComboBox::drop-down { border: 0px; }"
            "QComboBox QAbstractItemView { background: %s; color: %s; selection-background-color: %s; }"
            % (
                filter_bg,
                colors["border"],
                filter_label,
                colors["input_bg"],
                colors["border"],
                colors["text"],
                colors["accent2"],
                colors["card"],
                colors["text"],
                colors["accent"],
            )
        )

        for col in self.columns:
            col.update_theme(theme)
        if self.empty_state:
            self._style_empty_state()

    def _build_empty_state(self):
        frame = QFrame()
        frame.setMinimumWidth(520)
        frame.setFixedHeight(220)
        frame.setObjectName("EmptyState")
        frame.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)

        layout = QVBoxLayout(frame)
        layout.setContentsMargins(30, 24, 30, 24)
        layout.setSpacing(10)

        title = QLabel("No tasks yet")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        subtitle = QLabel("Create a new task to kick off your day with clarity.")
        subtitle.setWordWrap(True)
        subtitle.setAlignment(Qt.AlignmentFlag.AlignCenter)
        subtitle.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)

        layout.addStretch()
        layout.addWidget(title)
        layout.addWidget(subtitle)
        layout.addStretch()

        self.empty_state = frame
        self.empty_title = title
        self.empty_subtitle = subtitle
        self._style_empty_state()
        return frame

    def _style_empty_state(self):
        if not self.empty_state:
            return
        colors = get_theme(self.current_theme)
        if self.current_theme == "Dark":
            bg = rgba(colors["card_alt"], 0.75)
            border = colors["border"]
            title_color = colors["text"]
            sub_color = colors["sub"]
        else:
            bg = rgba(colors["card"], 0.9)
            border = colors["border"]
            title_color = colors["text"]
            sub_color = colors["sub"]

        self.empty_state.setStyleSheet(
            f"QFrame#EmptyState {{ background: {bg}; border: 1px dashed {border}; border-radius: 22px; }}"
        )
        self.empty_title.setStyleSheet(f"font-size: 20px; font-weight: 900; color: {title_color};")
        self.empty_subtitle.setStyleSheet(f"font-size: 12px; font-weight: 600; color: {sub_color};")

    # --- BDD ---
    def get_db_connection(self):
        return _get_db_connection()

    def init_db(self):
        # Use the central DB initializer so app behavior does not depend on cwd.
        _init_local_db()

    # --- Actions ---
    def prompt_new_task(self):
        dlg = AddTaskDialog(self, theme=self.current_theme)
        default_important = False
        settings_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "settings.json")
        try:
            if os.path.exists(settings_path):
                with open(settings_path, "r") as f:
                    data = json.load(f)
                    if "default_important" in data:
                        default_important = bool(data.get("default_important", False))
                    else:
                        prio = str(data.get("default_priority", "")).strip().lower()
                        default_important = prio in ("high", "medium")
        except Exception:
            default_important = False
        try:
            dlg.important_check.setChecked(default_important)
        except Exception:
            pass
        if dlg.exec():
            data = dlg.get_data()
            if data['title']:
                self.save_task_to_db(data)
                self.refresh_tasks()
                self.task_added.emit()

    def show_task_details(self, t_id):
        conn = self.get_db_connection()
        row = conn.execute("SELECT title, description, due_date, created_date, task_type, is_urgent, is_important FROM tasks WHERE id=?", (t_id,)).fetchone()

        if row:
            created = row[3] if row[3] else "Unknown"
            task_type = row[4]
            urg, imp = row[5], row[6]
            if urg and imp: prio = "high"
            elif not urg and imp: prio = "medium"
            elif urg and not imp: prio = "low"
            else: prio = "too low"

            total_focus_min = 0
            total_sessions = 0
            sessions_widgets = []
            try:
                total_row = conn.execute(
                    "SELECT COALESCE(SUM(duration_min), 0), COUNT(*) FROM pomodoro_sessions WHERE task_id=? AND status='completed'",
                    (t_id,)
                ).fetchone()
                if total_row:
                    total_focus_min = int(total_row[0] or 0)

                total_sessions_row = conn.execute(
                    "SELECT COUNT(*) FROM pomodoro_sessions WHERE task_id=?",
                    (t_id,)
                ).fetchone()
                if total_sessions_row:
                    total_sessions = int(total_sessions_row[0] or 0)

                sess_rows = conn.execute(
                    "SELECT started_at, duration_min, status FROM pomodoro_sessions WHERE task_id=? ORDER BY started_at DESC",
                    (t_id,)
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
                conn.close()

            ViewTaskDialog(
                row[0], row[1], row[2], created, prio, task_type,
                total_focus_min, total_sessions, sessions_widgets,
                self, theme=self.current_theme, task_id=t_id
            ).exec()
        else:
            conn.close()

    def edit_task(self, t_id):
        conn = self.get_db_connection()
        row = conn.execute(
            "SELECT title, description, due_date, task_type, is_urgent, is_important, recurrence_kind, recurrence_interval "
            "FROM tasks WHERE id=?",
            (t_id,),
        ).fetchone()
        conn.close()
        if row:
            dlg = AddTaskDialog(self, "Edit Task", theme=self.current_theme)
            dlg.load_data(row[0], row[1], row[2], row[5], row[3], row[6], row[7])
            if dlg.exec():
                data = dlg.get_data()
                self.update_task_in_db(t_id, data)
                self.refresh_tasks()
                self.task_added.emit()

    def delete_task(self, t_id):
        conn = self.get_db_connection()
        try:
            row = conn.execute(
                "SELECT title, due_date, recurrence_kind, recurrence_anchor_id FROM tasks WHERE id=?",
                (int(t_id),),
            ).fetchone()
        except Exception:
            row = None
        conn.close()

        title = str(row[0] or "").strip() if row else ""
        due_date = str(row[1] or "").strip() if row else ""
        recurrence_kind = str(row[2] or "").strip().lower() if row else ""
        try:
            recurrence_anchor_id = int(row[3]) if row and row[3] is not None else int(t_id)
        except Exception:
            recurrence_anchor_id = int(t_id)

        is_recurring = bool(recurrence_kind)

        if is_recurring:
            box = QMessageBox(self)
            box.setIcon(QMessageBox.Icon.Warning)
            box.setWindowTitle("Delete Recurring Task")
            pretty_title = f" ({title})" if title else ""
            box.setText(f"Delete this recurring task{pretty_title}?\n\nChoose what to remove:")

            btn_occ = box.addButton("This occurrence only", QMessageBox.ButtonRole.AcceptRole)
            btn_series = box.addButton("Entire series", QMessageBox.ButtonRole.DestructiveRole)
            box.addButton(QMessageBox.StandardButton.Cancel)
            box.setDefaultButton(btn_occ)

            try:
                box.exec()
            except Exception:
                return

            clicked = box.clickedButton()
            if clicked == btn_occ:
                # Record a skip so the app doesn't auto-recreate this due date.
                conn = self.get_db_connection()
                if due_date:
                    try:
                        conn.execute(
                            "INSERT OR IGNORE INTO recurrence_skips (recurrence_anchor_id, recurrence_kind, due_date) VALUES (?,?,?)",
                            (int(recurrence_anchor_id), str(recurrence_kind), str(due_date)),
                        )
                    except Exception:
                        pass
                try:
                    conn.execute("DELETE FROM task_subtasks WHERE task_id=?", (int(t_id),))
                except Exception:
                    pass
                try:
                    conn.execute(
                        "DELETE FROM task_dependencies WHERE task_id=? OR depends_on_task_id=?",
                        (int(t_id), int(t_id)),
                    )
                except Exception:
                    pass
                try:
                    conn.execute("DELETE FROM pomodoro_sessions WHERE task_id=?", (int(t_id),))
                except Exception:
                    pass
                try:
                    conn.execute("DELETE FROM tasks WHERE id=?", (int(t_id),))
                except Exception:
                    pass
                try:
                    conn.commit()
                except Exception:
                    pass
                try:
                    conn.close()
                except Exception:
                    pass

                try:
                    self.refresh_tasks()
                    self.task_added.emit()
                except Exception:
                    pass
                return

            if clicked == btn_series:
                conn = self.get_db_connection()
                try:
                    ids = conn.execute(
                        "SELECT id FROM tasks WHERE recurrence_anchor_id=? AND TRIM(LOWER(recurrence_kind))=?",
                        (int(recurrence_anchor_id), str(recurrence_kind)),
                    ).fetchall()
                except Exception:
                    ids = []

                for r in ids or []:
                    try:
                        occ_id = int(r[0])
                    except Exception:
                        continue
                    try:
                        conn.execute("DELETE FROM task_subtasks WHERE task_id=?", (occ_id,))
                    except Exception:
                        pass
                    try:
                        conn.execute(
                            "DELETE FROM task_dependencies WHERE task_id=? OR depends_on_task_id=?",
                            (occ_id, occ_id),
                        )
                    except Exception:
                        pass
                    try:
                        conn.execute("DELETE FROM pomodoro_sessions WHERE task_id=?", (occ_id,))
                    except Exception:
                        pass
                    try:
                        conn.execute("DELETE FROM tasks WHERE id=?", (occ_id,))
                    except Exception:
                        pass

                try:
                    conn.execute(
                        "DELETE FROM recurrence_skips WHERE recurrence_anchor_id=? AND recurrence_kind=?",
                        (int(recurrence_anchor_id), str(recurrence_kind)),
                    )
                except Exception:
                    pass

                conn.commit()
                conn.close()
                try:
                    self.refresh_tasks()
                    self.task_added.emit()
                except Exception:
                    pass
                return

            # Cancel / ESC
            return

        # Non-recurring task
        if (
            QMessageBox.question(
                self,
                "Delete",
                "Remove this Task?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            != QMessageBox.StandardButton.Yes
        ):
            return

        conn = self.get_db_connection()
        try:
            conn.execute("DELETE FROM task_subtasks WHERE task_id=?", (int(t_id),))
        except Exception:
            pass
        try:
            conn.execute(
                "DELETE FROM task_dependencies WHERE task_id=? OR depends_on_task_id=?",
                (int(t_id), int(t_id)),
            )
        except Exception:
            pass
        try:
            conn.execute("DELETE FROM pomodoro_sessions WHERE task_id=?", (int(t_id),))
        except Exception:
            pass
        try:
            conn.execute("DELETE FROM tasks WHERE id=?", (int(t_id),))
        except Exception:
            pass
        conn.commit()
        conn.close()
        self.refresh_tasks()
        self.task_added.emit()

    def mark_task_completed(self, t_id, checked):
        completed_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S") if checked else None
        if checked:
            try:
                conn = self.get_db_connection()
                blocked = conn.execute(
                    """SELECT t.title
                       FROM task_dependencies d
                       JOIN tasks t ON t.id = d.depends_on_task_id
                       WHERE d.task_id = ? AND COALESCE(t.is_completed, 0) = 0
                       ORDER BY t.due_date""",
                    (t_id,),
                ).fetchall()
                conn.close()
                if blocked:
                    titles = [str(r[0] or "").strip() for r in blocked if r and str(r[0] or "").strip()]
                    msg = "This task is blocked by unfinished dependencies."
                    if titles:
                        msg += "\n\nFinish these first:\n- " + "\n- ".join(titles[:8])
                        if len(titles) > 8:
                            msg += "\n- ..."
                    QMessageBox.information(self, "Blocked", msg)
                    try:
                        self.refresh_tasks()
                    except Exception:
                        pass
                    return
            except Exception:
                pass
        conn = self.get_db_connection()
        conn.execute(
            "UPDATE tasks SET is_completed=?, completed_at=? WHERE id=?",
            (1 if checked else 0, completed_at, t_id)
        )
        conn.commit()
        conn.close()
        if checked:
            try:
                self._maybe_spawn_next_recurrence(t_id)
            except Exception:
                pass
        # Refresh UI so task disappears immediately when 'Show completed' is disabled
        try:
            self.refresh_tasks()
        except Exception:
            pass
        # Notify other pages (matrix, etc.) about the change
        try:
            self.task_added.emit()
        except Exception:
            pass
        # If completed, clear reminder tracking so it stops repeating
        try:
            if checked and t_id in self._last_remind:
                del self._last_remind[t_id]
        except Exception:
            pass

    def _next_due_date(self, due_date_str: str, kind: str, interval: int) -> str | None:
        raw = str(due_date_str or "").strip()
        if not raw:
            return None
        try:
            base = datetime.strptime(raw, "%Y-%m-%d").date()
        except Exception:
            return None

        kind = str(kind or "").strip().lower()
        # `interval` is interpreted as "repeat for N periods" (e.g. daily for 7 days),
        # so each occurrence advances by exactly one period.

        if kind == "daily":
            nxt = base + timedelta(days=1)
        elif kind == "weekly":
            nxt = base + timedelta(days=7)
        elif kind == "monthly":
            months = 1
            year = base.year + (base.month - 1 + months) // 12
            month = (base.month - 1 + months) % 12 + 1
            day = min(base.day, calendar.monthrange(year, month)[1])
            nxt = base.replace(year=year, month=month, day=day)
        else:
            return None

        return nxt.strftime("%Y-%m-%d")

    def _maybe_spawn_next_recurrence(self, task_id: int) -> None:
        conn = self.get_db_connection()
        row = conn.execute(
            "SELECT title, description, due_date, task_type, is_important, recurrence_kind, recurrence_interval, recurrence_anchor_id "
            "FROM tasks WHERE id=?",
            (task_id,),
        ).fetchone()
        if not row:
            conn.close()
            return

        title = row[0]
        desc = row[1]
        due_date = row[2]
        task_type = row[3]
        is_imp = int(row[4] or 0)
        kind = str(row[5] or "").strip().lower()
        interval = row[6]
        anchor_id = row[7] or task_id

        if not kind:
            conn.close()
            return

        try:
            total_periods = max(1, int(interval or 1))
        except Exception:
            total_periods = 1

        # Stop spawning once the requested number of occurrences exists for this series.
        try:
            series_count_row = conn.execute(
                "SELECT COUNT(1) FROM tasks WHERE recurrence_anchor_id=? AND recurrence_kind=?",
                (int(anchor_id), kind),
            ).fetchone()
            series_count = int(series_count_row[0] or 0) if series_count_row else 0
        except Exception:
            series_count = 0
        if total_periods <= 1 or (series_count and series_count >= total_periods):
            conn.close()
            return

        next_due = self._next_due_date(str(due_date or ""), kind, int(interval or 1))
        if not next_due:
            conn.close()
            return

        # Don't create future occurrences early: only spawn dates that should already exist (<= today).
        today_str = QDate.currentDate().toString("yyyy-MM-dd")
        if str(next_due) > today_str:
            conn.close()
            return

        exists = conn.execute(
            "SELECT 1 FROM tasks WHERE recurrence_anchor_id=? AND recurrence_kind=? AND due_date=? LIMIT 1",
            (anchor_id, kind, next_due),
        ).fetchone()
        if exists:
            conn.close()
            return

        # Respect user-deleted occurrences so they don't get re-created automatically.
        try:
            skipped = conn.execute(
                "SELECT 1 FROM recurrence_skips WHERE recurrence_anchor_id=? AND recurrence_kind=? AND due_date=? LIMIT 1",
                (int(anchor_id), str(kind), str(next_due)),
            ).fetchone()
        except Exception:
            skipped = None
        if skipped:
            conn.close()
            return

        is_urg = self._deadline_is_urgent(next_due)
        prio = quadrant_from_flags(is_urg, is_imp)
        norm_type = normalize_task_type(task_type)

        conn.execute(
            "INSERT INTO tasks (title, description, due_date, created_date, priority, task_type, is_urgent, is_important, is_completed, "
            "recurrence_kind, recurrence_interval, recurrence_anchor_id) "
            "VALUES (?,?,?,?,?,?,?,?,0,?,?,?)",
            (title, desc, next_due, str(next_due), prio, norm_type, is_urg, is_imp, kind, int(interval or 1), int(anchor_id)),
        )
        conn.commit()
        conn.close()

    def _ensure_recurrence_instances(self) -> None:
        """Auto-spawn recurring tasks even if previous occurrences are not completed.

        Semantics: `recurrence_interval` is the total number of occurrences (e.g. daily for 7 days).
        This function is idempotent: it inserts only missing due dates for a series.
        """
        conn = self.get_db_connection()
        try:
            series = conn.execute(
                """
                SELECT recurrence_anchor_id, TRIM(LOWER(recurrence_kind)) AS kind, MAX(recurrence_interval) AS interval
                FROM tasks
                WHERE recurrence_anchor_id IS NOT NULL
                  AND TRIM(COALESCE(recurrence_kind, '')) != ''
                GROUP BY recurrence_anchor_id, TRIM(LOWER(recurrence_kind))
                """
            ).fetchall()

            if not series:
                return

            today_str = QDate.currentDate().toString("yyyy-MM-dd")

            for anchor_id, kind, interval in series:
                try:
                    anchor_id = int(anchor_id)
                except Exception:
                    continue
                kind = str(kind or "").strip().lower()
                if kind not in ("daily", "weekly", "monthly"):
                    continue
                try:
                    total_periods = max(1, int(interval or 1))
                except Exception:
                    total_periods = 1
                if total_periods <= 1:
                    continue

                # Older versions pre-created future occurrences; delete those so tasks only appear on/after their due date.
                try:
                    future_rows = conn.execute(
                        "SELECT id FROM tasks "
                        "WHERE recurrence_anchor_id=? AND TRIM(LOWER(recurrence_kind))=? AND id != ? AND due_date > ?",
                        (anchor_id, kind, anchor_id, today_str),
                    ).fetchall()
                    for fr in future_rows or []:
                        try:
                            occ_id = int(fr[0])
                        except Exception:
                            continue
                        try:
                            conn.execute("DELETE FROM task_subtasks WHERE task_id=?", (occ_id,))
                        except Exception:
                            pass
                        try:
                            conn.execute(
                                "DELETE FROM task_dependencies WHERE task_id=? OR depends_on_task_id=?",
                                (occ_id, occ_id),
                            )
                        except Exception:
                            pass
                        try:
                            conn.execute("DELETE FROM pomodoro_sessions WHERE task_id=?", (occ_id,))
                        except Exception:
                            pass
                        try:
                            conn.execute("DELETE FROM tasks WHERE id=?", (occ_id,))
                        except Exception:
                            pass
                except Exception:
                    pass

                base = conn.execute(
                    "SELECT title, description, due_date, task_type, is_important FROM tasks WHERE id=?",
                    (anchor_id,),
                ).fetchone()
                if not base:
                    base = conn.execute(
                        "SELECT title, description, due_date, task_type, is_important "
                        "FROM tasks WHERE recurrence_anchor_id=? AND TRIM(COALESCE(recurrence_kind, '')) != '' "
                        "ORDER BY due_date LIMIT 1",
                        (anchor_id,),
                    ).fetchone()
                if not base:
                    continue

                title, desc, base_due, task_type, is_imp = base
                base_due = str(base_due or "").strip()
                if not base_due:
                    continue

                existing = conn.execute(
                    "SELECT due_date FROM tasks WHERE recurrence_anchor_id=? AND TRIM(LOWER(recurrence_kind))=?",
                    (anchor_id, kind),
                ).fetchall()
                existing_due = {str(r[0] or "").strip() for r in existing if r and str(r[0] or "").strip()}

                # Treat skipped occurrences as "existing" so auto-spawn doesn't re-create them.
                try:
                    skipped_rows = conn.execute(
                        "SELECT due_date FROM recurrence_skips WHERE recurrence_anchor_id=? AND recurrence_kind=?",
                        (int(anchor_id), str(kind)),
                    ).fetchall()
                    skipped_due = {str(r[0] or "").strip() for r in (skipped_rows or []) if r and str(r[0] or "").strip()}
                    existing_due |= skipped_due
                except Exception:
                    pass

                due = base_due
                for _ in range(int(total_periods)):
                    if not due:
                        break
                    # Don't create future instances: only up to today (catching up if the app wasn't opened).
                    if str(due) > today_str:
                        break
                    if due and due not in existing_due:
                        is_urg = self._deadline_is_urgent(due)
                        prio = quadrant_from_flags(is_urg, int(is_imp or 0))
                        norm_type = normalize_task_type(task_type)
                        conn.execute(
                            "INSERT INTO tasks (title, description, due_date, created_date, priority, task_type, is_urgent, is_important, is_completed, "
                            "recurrence_kind, recurrence_interval, recurrence_anchor_id) "
                            "VALUES (?,?,?,?,?,?,?,?,0,?,?,?)",
                            (
                                title,
                                desc,
                                due,
                                due,
                                prio,
                                norm_type,
                                is_urg,
                                int(is_imp or 0),
                                kind,
                                int(total_periods),
                                int(anchor_id),
                            ),
                        )
                        existing_due.add(due)

                    due = self._next_due_date(due, kind, 1) if due else None
                    if not due:
                        break

            conn.commit()
        finally:
            try:
                conn.close()
            except Exception:
                pass

    # --- Helpers BDD ---
    def _deadline_is_urgent(self, due_date_str):
        if not due_date_str:
            return 0
        try:
            due = QDate.fromString(due_date_str, "yyyy-MM-dd")
        except Exception:
            return 0
        if not due.isValid():
            return 0
        today = QDate.currentDate()
        days_to = today.daysTo(due)
        return 1 if days_to <= 2 else 0

    def save_task_to_db(self, data):
        is_imp = 1 if data.get("important") else 0
        is_urg = self._deadline_is_urgent(data.get("date"))
        prio = quadrant_from_flags(is_urg, is_imp)
        task_type = normalize_task_type(data.get("task_type"))
        recurrence_kind = str(data.get("recurrence_kind") or "").strip().lower()
        recurrence_interval = data.get("recurrence_interval")
        try:
            recurrence_interval = max(1, int(recurrence_interval or 1))
        except Exception:
            recurrence_interval = 1

        today_str = QDate.currentDate().toString("yyyy-MM-dd")
        conn = self.get_db_connection()
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO tasks (title, description, due_date, created_date, priority, task_type, is_urgent, is_important, "
            "is_completed, recurrence_kind, recurrence_interval, recurrence_anchor_id) "
            "VALUES (?,?,?,?,?,?,?,?,0,?,?,NULL)",
            (data["title"], data["description"], data["date"], today_str, prio, task_type, is_urg, is_imp,
             recurrence_kind, recurrence_interval),
        )
        new_id = cur.lastrowid
        if recurrence_kind:
            cur.execute(
                "UPDATE tasks SET recurrence_anchor_id=? WHERE id=?",
                (int(new_id), int(new_id)),
            )
        conn.commit()
        conn.close()

    def update_task_in_db(self, t_id, data):
        is_imp = 1 if data.get("important") else 0
        is_urg = self._deadline_is_urgent(data.get("date"))
        prio = quadrant_from_flags(is_urg, is_imp)
        task_type = normalize_task_type(data.get("task_type"))
        recurrence_kind = str(data.get("recurrence_kind") or "").strip().lower()
        recurrence_interval = data.get("recurrence_interval")
        try:
            recurrence_interval = max(1, int(recurrence_interval or 1))
        except Exception:
            recurrence_interval = 1

        conn = self.get_db_connection()
        cur = conn.cursor()
        cur.execute(
            "UPDATE tasks SET title=?, description=?, due_date=?, priority=?, task_type=?, is_urgent=?, is_important=?, "
            "recurrence_kind=?, recurrence_interval=? WHERE id=?",
            (data["title"], data["description"], data["date"], prio, task_type, is_urg, is_imp,
             recurrence_kind, recurrence_interval, t_id),
        )
        if recurrence_kind:
            cur.execute(
                "UPDATE tasks SET recurrence_anchor_id=COALESCE(recurrence_anchor_id, id) WHERE id=?",
                (t_id,),
            )
        else:
            cur.execute(
                "UPDATE tasks SET recurrence_anchor_id=NULL WHERE id=?",
                (t_id,),
            )
        conn.commit()
        conn.close()

    def refresh_tasks(self):
        try:
            self._ensure_recurrence_instances()
        except Exception:
            pass
        while self.columns_layout.count():
            item = self.columns_layout.takeAt(0)
            if item.widget(): item.widget().deleteLater()

        self.columns = []
        self.empty_state = None
        self.empty_title = None
        self.empty_subtitle = None

        filters = self._current_filters()
        filters_active = self._filters_active(filters)

        conn = self.get_db_connection()
        try:
            status_filter = filters.get("status", "all")
            if status_filter == "completed":
                rows = conn.execute(
                    "SELECT id, title, description, due_date, created_date, task_type, is_urgent, is_important, is_completed "
                    "FROM tasks WHERE is_completed=1 ORDER BY due_date"
                ).fetchall()
            elif status_filter == "active":
                rows = conn.execute(
                    "SELECT id, title, description, due_date, created_date, task_type, is_urgent, is_important, is_completed "
                    "FROM tasks WHERE is_completed=0 ORDER BY due_date"
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT id, title, description, due_date, created_date, task_type, is_urgent, is_important, is_completed "
                    "FROM tasks ORDER BY due_date"
                ).fetchall()
            focus_rows = conn.execute(
                "SELECT task_id, COALESCE(SUM(duration_min), 0) "
                "FROM pomodoro_sessions "
                "WHERE status IN ('completed', 'stopped') "
                "GROUP BY task_id"
            ).fetchall()
        except:
            rows = []
            focus_rows = []
        conn.close()

        focus_map = {r[0]: int(r[1] or 0) for r in focus_rows}

        map_cols = {}
        total_tasks = len(rows)
        due_today = 0
        today_str = QDate.currentDate().toString("yyyy-MM-dd")
        today_date = QDate.currentDate()
        self.subtitle.setText(QDate.currentDate().toString("dddd, d MMMM yyyy"))

        if total_tasks == 0:
            empty = self._build_empty_state()
            if filters_active:
                try:
                    self.empty_title.setText("No tasks match your filters")
                    self.empty_subtitle.setText("Try clearing filters or changing your search.")
                except Exception:
                    pass
            self.columns_layout.addWidget(
                empty, 0, 0, alignment=Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignHCenter
            )
            self.columns_layout.setColumnStretch(0, 1)
            self.columns_layout.setRowStretch(0, 1)
            self.chip_total.setText("0 tasks")
            self.chip_due.setText("0 due today")
            return

        type_filters = None
        try:
            if os.path.exists("settings.json"):
                with open("settings.json", "r") as sf:
                    sdata = json.load(sf)
                    type_filters = sdata.get("task_type_filters")
        except Exception:
            type_filters = None

        allowed_types = None
        if isinstance(type_filters, list):
            allowed_types = set(str(t) for t in type_filters)

        search_text = (filters.get("search") or "").lower()
        type_filter = filters.get("type", "all")
        priority_filter = filters.get("priority", "all")
        due_filter = filters.get("due", "any")

        displayed_tasks = 0
        for row in rows:
            t_id, title, desc, due_date_str, created_date_str, task_type, urg, imp, is_completed = row
            display_type = normalize_task_type(task_type)
            if display_type is None:
                display_type = UNCATEGORIZED_LABEL
            if allowed_types is not None and display_type not in allowed_types:
                continue
            if type_filter != "all":
                if type_filter == "uncategorized":
                    if display_type != UNCATEGORIZED_LABEL:
                        continue
                elif display_type != type_filter:
                    continue
            if search_text:
                hay = f"{title or ''} {desc or ''}".lower()
                if search_text not in hay:
                    continue

            priority = quadrant_from_flags(urg, imp)
            if priority_filter != "all" and priority != priority_filter:
                continue

            due_date = None
            if due_date_str:
                try:
                    due_date = QDate.fromString(due_date_str, "yyyy-MM-dd")
                except Exception:
                    due_date = None
            due_valid = bool(due_date and due_date.isValid())
            if due_filter == "overdue":
                if not due_valid or due_date >= today_date:
                    continue
            elif due_filter == "today":
                if not due_valid or due_date != today_date:
                    continue
            elif due_filter == "next7":
                if not due_valid:
                    continue
                if due_date < today_date or due_date > today_date.addDays(7):
                    continue
            elif due_filter == "none":
                if due_valid:
                    continue

            if not due_date_str: pretty_due = "No Deadline"
            else: pretty_due = QDate.fromString(due_date_str, "yyyy-MM-dd").toString("dddd d MMMM yyyy")

            if not created_date_str: pretty_created = "Unknown"
            else: pretty_created = QDate.fromString(created_date_str, "yyyy-MM-dd").toString("d MMM yyyy")

            if due_date_str == today_str:
                due_today += 1

            if pretty_due not in map_cols:
                is_today = (due_date_str == today_str)
                col = DayColumn(pretty_due, is_today, theme=self.current_theme)
                map_cols[pretty_due] = col
                self.columns.append(col)

            focus_minutes = focus_map.get(t_id, 0)
            card = TaskCard(t_id, title, desc, pretty_due, pretty_created, priority, focus_minutes, self, is_completed, task_type)
            card.update_theme(self.current_theme)
            map_cols[pretty_due].add_task_card(card)
            displayed_tasks += 1

        if displayed_tasks == 0:
            empty = self._build_empty_state()
            if filters_active:
                try:
                    self.empty_title.setText("No tasks match your filters")
                    self.empty_subtitle.setText("Try clearing filters or changing your search.")
                except Exception:
                    pass
            self.columns_layout.addWidget(
                empty, 0, 0, alignment=Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignHCenter
            )
            self.columns_layout.setColumnStretch(0, 1)
            self.columns_layout.setRowStretch(0, 1)
            self.chip_total.setText("0 tasks")
            self.chip_due.setText("0 due today")
            return

        self._reflow_columns()
        self.chip_total.setText(f"{displayed_tasks} tasks")
        self.chip_due.setText(f"{due_today} due today")

    def _reflow_columns(self):
        if not hasattr(self, "columns_layout") or not self.columns:
            return
        layout = self.columns_layout
        while layout.count():
            item = layout.takeAt(0)
            if item.widget() and item.widget() not in self.columns:
                item.widget().deleteLater()

        try:
            available = self.scroll.viewport().width()
        except Exception:
            available = self.width()
        margins = layout.contentsMargins()
        available = max(1, available - margins.left() - margins.right())
        spacing = layout.spacing()
        min_col_width = 340
        cols_per_row = max(1, int((available + spacing) // (min_col_width + spacing)))

        for idx, col in enumerate(self.columns):
            row = idx // cols_per_row
            col_idx = idx % cols_per_row
            layout.addWidget(col, row, col_idx)

        for col_idx in range(cols_per_row):
            layout.setColumnStretch(col_idx, 1)
        self._sync_column_title_sizes()

    def _reflow_header(self):
        if not hasattr(self, "header_layout"):
            return
        layout = self.header_layout
        while layout.count():
            layout.takeAt(0)
        width = self.header_frame.width() if hasattr(self, "header_frame") else self.width()
        narrow = width < 760

        if narrow:
            layout.addLayout(self.header_text_layout, 0, 0, 1, 2)
            layout.addWidget(self.header_add_btn, 1, 0, alignment=Qt.AlignmentFlag.AlignLeft)
            layout.addLayout(self.header_chips_layout, 1, 1, alignment=Qt.AlignmentFlag.AlignRight)
            layout.setColumnStretch(0, 1)
        else:
            layout.addLayout(self.header_text_layout, 0, 0)
            layout.addLayout(self.header_chips_layout, 0, 1, alignment=Qt.AlignmentFlag.AlignRight)
            layout.addWidget(self.header_add_btn, 0, 2, alignment=Qt.AlignmentFlag.AlignRight)
            layout.setColumnStretch(0, 1)

    # --- REMINDER HELPERS ---
    def _ensure_tray(self):
        if self._tray is not None:
            return
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return
        icon_path = self._app_icon_path()
        icon = QIcon(icon_path) if icon_path else QIcon()
        self._tray = QSystemTrayIcon(icon, self)
        self._tray.setVisible(True)

    def _app_icon_path(self):
        root_dir = os.path.dirname(os.path.dirname(__file__))
        png_path = os.path.join(root_dir, "ProdSmart.png")
        ico_path = os.path.join(root_dir, "ProdSmart.ico")
        # Prefer the PNG logo if present, so notifications use the same brand logo.
        if os.path.exists(png_path):
            return png_path
        if os.path.exists(ico_path):
            return ico_path
        return None

    def _show_notification(self, title, message):
        if not getattr(self, "enable_notifications", True):
            return
        self._ensure_tray()
        if self._tray:
            self._tray.showMessage(title, message, QSystemTrayIcon.MessageIcon.Information, 3000)
        else:
            # Avoid modal popups that can flash during page switches.
            # If tray isn't available, skip the popup.
            pass

    def apply_settings(self):
        """Load settings.json and enable/disable reminders and sounds accordingly."""
        settings_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "settings.json")
        try:
            if os.path.exists(settings_path):
                with open(settings_path, "r") as f:
                    data = json.load(f)
                    self.task_reminders_enabled = data.get("task_reminders", True)
                    self.enable_notifications = data.get("enable_notifications", True)
                    self.sound_effects = data.get("sound_effects", True)
                    self._reminder_repeat_minutes = int(data.get("reminder_repeat_minutes", 10))
            else:
                # defaults
                self.task_reminders_enabled = True
                self.enable_notifications = True
                self.sound_effects = True
        except Exception:
            self.task_reminders_enabled = True
            self.enable_notifications = True
            self.sound_effects = True

        # Clamp and apply repeat interval to the timer so reminders don't fire too often.
        try:
            repeat_min = int(self._reminder_repeat_minutes or 10)
        except Exception:
            repeat_min = 10
        if repeat_min < 1:
            repeat_min = 1
        self._reminder_repeat_minutes = repeat_min
        try:
            self._reminder_timer.setInterval(repeat_min * 60 * 1000)
        except Exception:
            pass

        # Setup sound
        try:
            sound_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "resources", "alarm.wav")
            self._reminder_sound = QSoundEffect(self)
            if os.path.exists(sound_path):
                self._reminder_sound.setSource(QUrl.fromLocalFile(sound_path))
                self._reminder_sound.setVolume(0.6)
            else:
                self._reminder_sound = None
        except Exception:
            self._reminder_sound = None

        # Start/stop timer
        try:
            if self.task_reminders_enabled and self.enable_notifications:
                if not self._reminder_timer.isActive():
                    self._reminder_timer.start()
            else:
                if self._reminder_timer.isActive():
                    self._reminder_timer.stop()
        except Exception:
            pass

    def _check_reminders(self):
        """Check DB for tasks due today and not completed; show reminder once per task per session."""
        try:
            self._ensure_recurrence_instances()
        except Exception:
            pass
        if not getattr(self, "task_reminders_enabled", True):
            return
        if not getattr(self, "enable_notifications", True):
            return

        today_str = QDate.currentDate().toString("yyyy-MM-dd")
        conn = self.get_db_connection()
        try:
            rows = conn.execute(
                "SELECT id, title FROM tasks WHERE due_date=? AND is_completed=0",
                (today_str,)
            ).fetchall()
        except Exception:
            rows = []
        conn.close()

        from datetime import datetime
        for r in rows:
            try:
                t_id = r[0]
                title = r[1]
                now = datetime.now()
                last = self._last_remind.get(t_id)
                should_remind = False
                if last is None:
                    should_remind = True
                else:
                    elapsed = (now - last).total_seconds() / 60.0
                    if elapsed >= (self._reminder_repeat_minutes or 10):
                        should_remind = True

                if not should_remind:
                    continue

                # Play sound if enabled
                if self.sound_effects and getattr(self, "_reminder_sound", None):
                    try:
                        self._reminder_sound.play()
                    except Exception:
                        pass

                # Show reminder notification
                try:
                    self._show_notification("Task Reminder", f"Reminder: '{title}' is due today.")
                except Exception:
                    pass

                # Record last remind time
                self._last_remind[t_id] = now
            except Exception:
                continue

    def start_pomodoro(self, t_id, title, priority=None, task_type=None):
        try:
            conn = self.get_db_connection()
            blocked = conn.execute(
                """SELECT t.title
                   FROM task_dependencies d
                   JOIN tasks t ON t.id = d.depends_on_task_id
                   WHERE d.task_id = ? AND COALESCE(t.is_completed, 0) = 0
                   ORDER BY t.due_date""",
                (int(t_id),),
            ).fetchall()
            conn.close()
            if blocked:
                titles = [str(r[0] or "").strip() for r in blocked if r and str(r[0] or "").strip()]
                msg = "You can't start this task because it has unfinished dependencies."
                if titles:
                    msg += "\n\nFinish these first:\n- " + "\n- ".join(titles[:8])
                    if len(titles) > 8:
                        msg += "\n- ..."
                QMessageBox.information(self, "Blocked", msg)
                return
        except Exception:
            pass
        prio = normalize_priority(priority) or "too low"
        self.pomodoro_requested.emit(t_id, title, prio, normalize_task_type(task_type))
