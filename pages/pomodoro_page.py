import json
import os
import shutil
import subprocess
from datetime import datetime
from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel,
                             QPushButton, QFrame, QSpinBox, QApplication, QSystemTrayIcon,
                             QToolButton, QProgressBar, QScrollArea, QMessageBox, QSizePolicy, QGridLayout, QStyle)
from PyQt6.QtWidgets import QComboBox
from PyQt6.QtCore import Qt, QTimer, QSize, QEvent, pyqtSignal
from PyQt6.QtGui import QIcon, QPainter, QPainterPath, QPen, QColor, QPixmap, QDesktopServices
from PyQt6.QtCore import QUrl
try:
    from PyQt6.QtMultimedia import QSoundEffect, QAudioOutput, QMediaPlayer
except Exception:
    QSoundEffect = None
    QAudioOutput = None
    QMediaPlayer = None
from database.db_manager import get_db_connection
from pages.settings_page import Toggle
from resources.theme import get_theme, FONT_FAMILY
from resources.time_format import format_duration_minutes
from resources.task_types import TASK_TYPE_LIMITS, normalize_task_type
from resources.api_client import (
    ApiError,
    api_list_teams,
    api_get_team_pomodoro,
    api_start_team_pomodoro,
    api_stop_team_pomodoro,
)
try:
    import PyQt6.sip as sip
except Exception:
    try:
        import sip  # type: ignore
    except Exception:
        sip = None
from resources.priority import normalize_priority, priority_session_label

class NoWheelSpinBox(QSpinBox):
    def wheelEvent(self, event):
        event.ignore()

class PomodoroPage(QWidget):
    select_task_requested = pyqtSignal()

    def __init__(self):
        super().__init__()
        
        # Listes pour le style
        self.cards = []
        self.labels_main = []
        self.labels_sub = []
        self.spinboxes = []
        self.inputs_bg = []
        
        # Variables Timer
        self.focus_time = 25
        self.time_left = self.focus_time * 60
        self.timer = QTimer()
        self.timer.timeout.connect(self.update_timer)
        self.phase = "focus"
        self.sessions_completed = 0
        self.auto_start = False
        self.sound_effects = True
        self.enable_notifications = True
        self.last_notification_phase = None
        self._is_dark = False
        self._theme_colors = {}
        self._tray = None
        self._settings_path = None
        self._suppress_focus_mode_save = False
        self._current_task_border_color = None
        self._current_task_style_base = ""
        self._pre_focus_notifications = None
        self._pre_focus_sound = None
        self._focus_assist_prompted = False
        self._force_system_focus_page = False
        self._end_sound = None
        self._suppress_next_phase_sound = False
        self._playlist_url = ""
        self._bg_audio_path = ""
        self._auto_play_bg_audio = False
        self._bg_player = None
        self._bg_audio_output = None
        self._bg_audio_active = False

        # Task-linked session state
        self.current_task_id = None
        self.current_task_title = None
        self.current_task_priority = None
        self.current_task_type = None
        self.session_started_at = None
        self.session_task_id = None
        self.session_task_title = None
        self.session_task_priority = None
        self.session_task_type = None
        self.session_duration_min = None
        self._active_session_id = None
        self.plan_enabled = True
        self.plan_index = 0
        self.plan_phases = []
        self.plan_focus_spins = []
        self.plan_break_spins = []
        self.plan_row_widgets = []
        self.plan_badges = []
        self.plan_rows = []
        self.plan_label_chips = []
        self._last_interval_value = None

        # Team session state
        self.team_timer_poll = QTimer()
        self.team_timer_poll.timeout.connect(self.refresh_team_session)
        self.team_timer_tick = QTimer()
        self.team_timer_tick.timeout.connect(self._tick_team_countdown)
        self.team_remaining_seconds = None
        self.team_status = "idle"
        self.team_phase = None
        self.team_selected_id = None
        self.team_mode_enabled = False
        
        self.setup_ui()
        self._rebuild_plan_inputs()
        self._set_phase("focus", reset_time=True, notify=False)
        self._recover_incomplete_sessions()

    def _build_team_mode_card(self):
        self.team_mode_card = QFrame()
        self.team_mode_card.setObjectName("TeamModeCard")
        self.team_mode_card.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)

        layout = QVBoxLayout(self.team_mode_card)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(8)

        mode_row = QHBoxLayout()
        self.team_mode_label = QLabel("Mode: Solo")
        self.team_mode_label.setObjectName("TeamModeLabel")
        self.team_mode_toggle = Toggle(width=44)
        self.team_mode_toggle.setObjectName("TeamModeToggle")
        self.team_mode_toggle.stateChanged.connect(self.on_team_mode_toggled)
        mode_row.addWidget(self.team_mode_label)
        mode_row.addStretch()
        mode_row.addWidget(self.team_mode_toggle)
        layout.addLayout(mode_row)

        picker_row = QHBoxLayout()
        self.team_combo = QComboBox()
        self.team_combo.setObjectName("TeamSessionCombo")
        self.team_combo.setCursor(Qt.CursorShape.PointingHandCursor)
        self.team_combo.currentIndexChanged.connect(self._on_team_changed)
        picker_row.addWidget(self.team_combo, stretch=1)

        self.btn_team_refresh = QPushButton("Refresh")
        self.btn_team_refresh.setObjectName("TeamSessionRefresh")
        self.btn_team_refresh.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_team_refresh.clicked.connect(self.refresh_team_list)
        picker_row.addWidget(self.btn_team_refresh)

        self.team_picker_widget = QWidget()
        picker_layout = QVBoxLayout(self.team_picker_widget)
        picker_layout.setContentsMargins(0, 0, 0, 0)
        picker_layout.addLayout(picker_row)
        layout.addWidget(self.team_picker_widget)

    def _build_team_session_card(self):
        self.team_session_card = QFrame()
        self.team_session_card.setObjectName("TeamSessionCard")
        self.team_session_card.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)

        layout = QVBoxLayout(self.team_session_card)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(8)

        header_row = QHBoxLayout()
        self.team_title = QLabel("TEAM SESSION")
        self.team_title.setObjectName("TeamSessionTitle")
        header_row.addWidget(self.team_title)
        header_row.addStretch()
        layout.addLayout(header_row)

        status_row = QHBoxLayout()
        self.team_status_label = QLabel("Status: Idle")
        self.team_status_label.setObjectName("TeamSessionStatus")
        self.team_phase_label = QLabel("Phase: -")
        self.team_phase_label.setObjectName("TeamSessionPhase")
        status_row.addWidget(self.team_status_label)
        status_row.addStretch()
        status_row.addWidget(self.team_phase_label)
        layout.addLayout(status_row)

        self.team_timer_label = QLabel("--:--")
        self.team_timer_label.setObjectName("TeamSessionTimer")
        self.team_timer_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.team_timer_label)

        btn_row = QHBoxLayout()
        self.btn_team_focus = QPushButton("Start Focus")
        self.btn_team_break = QPushButton("Start Break")
        self.btn_team_stop = QPushButton("Stop")
        for b in (self.btn_team_focus, self.btn_team_break, self.btn_team_stop):
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setFixedHeight(34)
            btn_row.addWidget(b)
        self.btn_team_focus.clicked.connect(self.start_team_focus)
        self.btn_team_break.clicked.connect(self.start_team_break)
        self.btn_team_stop.clicked.connect(self.stop_team_session)
        layout.addLayout(btn_row)

    def showEvent(self, event):
        """S'active quand on clique sur l'onglet"""
        self.apply_theme()
        if self.team_mode_enabled:
            self.refresh_team_list()
            if not self.team_timer_poll.isActive():
                self.team_timer_poll.start(6000)
            if not self.team_timer_tick.isActive():
                self.team_timer_tick.start(1000)
        super().showEvent(event)

    def hideEvent(self, event):
        if self.team_timer_poll.isActive():
            self.team_timer_poll.stop()
        if self.team_timer_tick.isActive():
            self.team_timer_tick.stop()
        super().hideEvent(event)

    def on_team_mode_toggled(self, state):
        self._set_team_mode(bool(state))

    def _set_team_mode(self, enabled):
        if enabled and not self._has_team_mode_ui():
            self.team_mode_enabled = False
            return
        self.team_mode_enabled = enabled
        if hasattr(self, "team_mode_label"):
            self.team_mode_label.setText("Mode: Team" if enabled else "Mode: Solo")
        if hasattr(self, "team_mode_toggle"):
            self.team_mode_toggle.blockSignals(True)
            self.team_mode_toggle.setChecked(enabled)
            self.team_mode_toggle.blockSignals(False)
        if hasattr(self, "team_picker_widget"):
            self.team_picker_widget.setVisible(enabled)

        if enabled:
            self.refresh_team_list()
            if not self.team_timer_poll.isActive():
                self.team_timer_poll.start(6000)
            if not self.team_timer_tick.isActive():
                self.team_timer_tick.start(1000)
        else:
            if self.team_timer_poll.isActive():
                self.team_timer_poll.stop()
            if self.team_timer_tick.isActive():
                self.team_timer_tick.stop()

        self._update_team_session_visibility()

    def _update_team_session_visibility(self):
        if not hasattr(self, "team_session_card"):
            return
        if not self.team_mode_enabled:
            self.team_session_card.hide()
            return
        if self.team_selected_id:
            self.team_session_card.show()
        else:
            self.team_session_card.hide()

    def activate_team_mode(self, team_id=None, start_focus=False):
        if not self._has_team_mode_ui():
            return
        self._set_team_mode(True)
        self.refresh_team_list()
        if team_id is not None and hasattr(self, "team_combo"):
            idx = self.team_combo.findData(team_id)
            if idx >= 0:
                self.team_combo.setCurrentIndex(idx)
            self.team_selected_id = team_id
        self._update_team_session_visibility()
        if start_focus:
            self.start_team_focus()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._apply_responsive_sizes()

    def _prune_dead_widgets(self):
        def _alive(widget):
            if widget is None:
                return False
            if sip:
                try:
                    if sip.isdeleted(widget):
                        return False
                except Exception:
                    pass
            try:
                widget.objectName()
            except RuntimeError:
                return False
            return True

        self.spinboxes = [w for w in self.spinboxes if _alive(w)]
        self.inputs_bg = [w for w in self.inputs_bg if _alive(w)]
        self.labels_main = [w for w in self.labels_main if _alive(w)]
        self.labels_sub = [w for w in self.labels_sub if _alive(w)]
        self.cards = [w for w in self.cards if _alive(w)]

    def setup_ui(self):
        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll_area.setStyleSheet("QScrollArea { background: transparent; }")

        self.scroll_content = QWidget()
        self.scroll_content.setObjectName("PomodoroScrollContent")
        self.scroll_content.setStyleSheet("background: transparent;")
        self.scroll_area.setWidget(self.scroll_content)
        root_layout.addWidget(self.scroll_area)

        self.main_layout = QHBoxLayout(self.scroll_content)
        self.main_layout.setContentsMargins(40, 40, 40, 40)
        self.main_layout.setSpacing(30)

        # === GAUCHE ===
        self.left_container = QFrame()
        self.cards.append(self.left_container)
        self.left_container.installEventFilter(self)
        
        self.left_vbox = QVBoxLayout(self.left_container)
        self.left_vbox.setContentsMargins(50, 40, 50, 40)
        
        self.header_title = QLabel("Pomodoro Timer")
        self.header_title.setStyleSheet("font-size: 28px; font-weight: 800;")
        self.labels_main.append(self.header_title)
        
        self.header_sub = QLabel("Deep work made simple")
        self.header_sub.setStyleSheet("font-size: 15px;")
        self.labels_sub.append(self.header_sub)

        # Current task card
        self.current_task_card = QFrame()
        self.current_task_card.setObjectName("CurrentTaskCard")
        self.current_task_card.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        current_layout = QVBoxLayout(self.current_task_card)
        current_layout.setContentsMargins(18, 16, 18, 16)
        current_layout.setSpacing(10)

        header_row = QHBoxLayout()
        self.current_task_header = QLabel("CURRENT TASK")
        self.current_task_header.setObjectName("CurrentTaskHeader")
        self.select_task_btn = QPushButton("Select Task")
        self.select_task_btn.setObjectName("SelectTaskBtn")
        self.select_task_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.select_task_btn.clicked.connect(self._request_task_selection)
        header_row.addWidget(self.current_task_header)
        header_row.addStretch()
        header_row.addWidget(self.select_task_btn)
        current_layout.addLayout(header_row)

        self.current_task_title_label = QLabel("No Task Selected")
        self.current_task_title_label.setObjectName("CurrentTaskTitle")
        self.current_task_title_label.setWordWrap(True)
        current_layout.addWidget(self.current_task_title_label)

        self.current_task_meta = QLabel("Session Type: -")
        self.current_task_meta.setObjectName("CurrentTaskMeta")
        self.current_task_meta.setWordWrap(True)
        self.labels_sub.append(self.current_task_meta)
        current_layout.addWidget(self.current_task_meta)

        progress_row = QHBoxLayout()
        self.session_progress_title = QLabel("Session Progress")
        self.session_progress_title.setObjectName("SessionProgressTitle")
        self.session_progress_value = QLabel("0%")
        self.session_progress_value.setObjectName("SessionProgressValue")
        progress_row.addWidget(self.session_progress_title)
        progress_row.addStretch()
        progress_row.addWidget(self.session_progress_value)
        current_layout.addLayout(progress_row)

        self.session_progress_bar = QProgressBar()
        self.session_progress_bar.setRange(0, 100)
        self.session_progress_bar.setValue(0)
        self.session_progress_bar.setTextVisible(False)
        self.session_progress_bar.setFixedHeight(8)
        current_layout.addWidget(self.session_progress_bar)

        self.focus_mode_card = QFrame()
        self.focus_mode_card.setObjectName("FocusModeCard")
        self.focus_mode_card.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        focus_layout = QVBoxLayout(self.focus_mode_card)
        focus_layout.setContentsMargins(12, 12, 12, 12)
        focus_layout.setSpacing(6)

        focus_title_row = QHBoxLayout()
        self.focus_mode_icon = QLabel("-")
        self.focus_mode_icon.setObjectName("FocusModeIcon")
        self.focus_mode_icon.setFixedSize(18, 18)
        self.focus_mode_icon.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.focus_mode_title = QLabel("Focus Mode Active")
        self.focus_mode_title.setObjectName("FocusModeTitle")
        focus_title_row.addWidget(self.focus_mode_icon)
        focus_title_row.addSpacing(6)
        focus_title_row.addWidget(self.focus_mode_title)
        focus_title_row.addStretch()
        focus_layout.addLayout(focus_title_row)

        self.focus_mode_desc = QLabel("Notifications are currently blocked")
        self.focus_mode_desc.setObjectName("FocusModeDesc")
        self.focus_mode_desc.setWordWrap(True)
        focus_layout.addWidget(self.focus_mode_desc)

        self.focus_mode_toggle = Toggle(width=44)
        self.focus_mode_toggle.setObjectName("FocusModeToggle")
        self.focus_mode_toggle.stateChanged.connect(self.on_focus_mode_toggled)
        focus_layout.addWidget(self.focus_mode_toggle, alignment=Qt.AlignmentFlag.AlignLeft)

        self.focus_assist_btn = QPushButton("Open Settings")
        self.focus_assist_btn.setObjectName("FocusAssistBtn")
        self.focus_assist_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.focus_assist_btn.clicked.connect(self.open_focus_assist_settings)
        focus_layout.addWidget(self.focus_assist_btn, alignment=Qt.AlignmentFlag.AlignLeft)
        if os.name != "nt":
            self.focus_assist_btn.hide()

        current_layout.addWidget(self.focus_mode_card)
        
        self.left_vbox.addWidget(self.header_title)
        self.left_vbox.addWidget(self.header_sub)
        self.left_vbox.addWidget(self.current_task_card)
        self.left_vbox.addSpacing(16)

        self.badge = QLabel("FOCUS TIME")
        self.badge.setObjectName("PomodoroBadge")
        self.badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.badge.setFixedSize(130, 32)
        self.left_vbox.addWidget(self.badge, alignment=Qt.AlignmentFlag.AlignCenter)

        self.timer_label = QLabel("25:00")
        self.timer_label.setMinimumSize(220, 220)
        self.timer_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.left_vbox.addWidget(self.timer_label, alignment=Qt.AlignmentFlag.AlignCenter)
        
        # Break tips (shown only during breaks)
        self.tips_frame = QFrame()
        self.tips_frame.setObjectName("PomodoroTips")
        tips_layout = QVBoxLayout(self.tips_frame)
        tips_layout.setContentsMargins(16, 14, 16, 14)
        tips_layout.setSpacing(10)

        self.tips_title = QLabel("REFRESHMENT TIPS")
        self.tips_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        tips_layout.addWidget(self.tips_title)

        tips_row1 = QHBoxLayout()
        tips_row1.setSpacing(8)
        self.btn_hydrate = QToolButton()
        self.btn_hydrate.setText("Hydrate")
        self.btn_stretch = QToolButton()
        self.btn_stretch.setText("Stretch")
        self.btn_step = QToolButton()
        self.btn_step.setText("Step Away")
        for b in (self.btn_hydrate, self.btn_stretch, self.btn_step):
            b.setFixedSize(100, 66)
            b.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextUnderIcon)
            b.setIconSize(QSize(22, 22))
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            tips_row1.addWidget(b)
        tips_layout.addLayout(tips_row1)

        tips_row2 = QHBoxLayout()
        tips_row2.setSpacing(10)
        self.btn_skip_break = QPushButton("Skip Break")
        self.btn_add_two = QPushButton("+2 Minutes")
        for b in (self.btn_skip_break, self.btn_add_two):
            b.setFixedHeight(34)
            b.setMinimumWidth(140)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            tips_row2.addWidget(b)
        tips_layout.addLayout(tips_row2)

        self.btn_skip_break.clicked.connect(self.skip_break)
        self.btn_add_two.clicked.connect(lambda: self.add_break_minutes(2))

        self.tips_frame.setVisible(False)

        btn_layout = QHBoxLayout()
        btn_layout.setSpacing(12)
        self.btn_start = QPushButton("Start Session")
        self.btn_start.setFixedSize(160, 50)
        self.btn_start.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_start.clicked.connect(self.toggle_timer)

        self.btn_stop = QPushButton("Stop")
        self.btn_stop.setFixedSize(110, 50)
        self.btn_stop.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_stop.clicked.connect(self.stop_timer)
        self.btn_stop.setVisible(False)
        
        self.btn_reset = QPushButton("Reset")
        self.btn_reset.setFixedSize(110, 50)
        self.btn_reset.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_reset.clicked.connect(self.reset_timer)
        self.btn_reset_ref = self.btn_reset
        
        btn_layout.addStretch()
        btn_layout.addWidget(self.btn_start)
        btn_layout.addWidget(self.btn_stop)
        btn_layout.addWidget(self.btn_reset)
        btn_layout.addStretch()
        self.left_vbox.addLayout(btn_layout)
        self._apply_control_icons()
        self.left_vbox.addWidget(self.tips_frame, alignment=Qt.AlignmentFlag.AlignCenter)

        self.left_vbox.addSpacing(30)
        self.separator = QFrame()
        self.separator.setFixedHeight(2)
        self.left_vbox.addWidget(self.separator)
        
        self.next_label = QLabel(f"Next Up: Short Break ({format_duration_minutes(5)})")
        self.next_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.labels_sub.append(self.next_label)
        self.left_vbox.addWidget(self.next_label)

        self.sessions_count = QLabel("0")
        self.sessions_count.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.sessions_count.setStyleSheet("font-weight: 900; font-size: 32px;")
        self.labels_main.append(self.sessions_count)
        self.left_vbox.addWidget(self.sessions_count)
        self.left_vbox.addStretch()

        # === DROITE ===
        self.settings_card = self.create_card("Settings")
        self.focus_input = self.add_styled_setting(self.settings_card, "Focus Duration", 25)
        self.short_input = self.add_styled_setting(self.settings_card, "Short Break", 5)
        self.long_input = self.add_styled_setting(self.settings_card, "Long Break", 15)
        self.interval_input = self.add_styled_setting(self.settings_card, "Intervals", 4)
        self.interval_input.valueChanged.connect(self._on_interval_changed)
        self._update_interval_dependent_inputs()

        self.btn_open_playlist = QPushButton("Play Playlist")
        self.btn_open_playlist.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_open_playlist.clicked.connect(self.open_playlist)
        self.settings_card.layout().addWidget(self.btn_open_playlist)

        self.btn_stop_playlist = QPushButton("Stop Playlist")
        self.btn_stop_playlist.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_stop_playlist.clicked.connect(self.stop_playlist)
        self.btn_stop_playlist.setEnabled(False)
        self.settings_card.layout().addWidget(self.btn_stop_playlist)

        self.lbl_playlist_status = QLabel("Playlist status: Stopped")
        self.lbl_playlist_status.setObjectName("PlaylistStatus")
        self.lbl_playlist_status.setStyleSheet("font-size: 12px; color: #777; margin-top: 6px;")
        self.settings_card.layout().addWidget(self.lbl_playlist_status)

        self.btn_toggle_bg_audio = QPushButton("Background Audio: Off")
        self.btn_toggle_bg_audio.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_toggle_bg_audio.clicked.connect(self.toggle_background_audio)
        self.settings_card.layout().addWidget(self.btn_toggle_bg_audio)

        self.plan_card = self.create_card("Custom Plan")
        self.plan_scroll = QScrollArea()
        self.plan_scroll.setWidgetResizable(True)
        self.plan_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.plan_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.plan_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.plan_scroll.setFixedHeight(380)
        self.plan_scroll.setStyleSheet("QScrollArea { background: transparent; }")
        self.plan_scroll.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

        self.plan_container = QWidget()
        self.plan_container.setObjectName("PlanContainer")
        self.plan_container.setStyleSheet("background: transparent;")
        self.plan_layout = QVBoxLayout(self.plan_container)
        self.plan_layout.setContentsMargins(12, 12, 12, 12)
        self.plan_layout.setSpacing(12)
        self.plan_scroll.setWidget(self.plan_container)
        self.plan_card.layout().addWidget(self.plan_scroll)

        self.cycle_card = self.create_card("Current Cycle")
        self.total_stat = self.add_stat_line(
            self.cycle_card,
            "Total Time",
            format_duration_minutes(130),
            True
        )

        self.settings_stack = QWidget()
        settings_layout = QVBoxLayout(self.settings_stack)
        settings_layout.setContentsMargins(0, 0, 0, 0)
        settings_layout.setSpacing(25)
        settings_layout.addWidget(self.settings_card)
        settings_layout.addWidget(self.plan_card)
        settings_layout.addWidget(self.cycle_card)

        insert_idx = self.left_vbox.indexOf(self.tips_frame) + 1
        self.left_vbox.insertWidget(insert_idx, self.settings_stack)

        self.main_layout.addWidget(self.left_container, stretch=1)

    # --- THEME + SETTINGS ---
    def load_settings_data(self):
        """Cherche settings.json partout où il pourrait se cacher."""
        theme = "Light"
        auto_start = False
        enable_notifications = True
        sound_effects = True
        playlist_url = ""
        bg_audio_path = ""
        auto_play_bg_audio = False
        self._settings_path = None
        
        # 1. Chemin absolu du dossier où se trouve ce fichier (pages/)
        dir_pages = os.path.dirname(os.path.abspath(__file__))
        # 2. Chemin du dossier parent (racine du projet)
        dir_root = os.path.dirname(dir_pages)
        
        # Liste des endroits où chercher
        paths_to_check = [
            "settings.json",                          # Dossier d'exécution actuel
            os.path.join(dir_root, "settings.json"),  # Racine du projet (Prodsmart/)
            os.path.join(dir_pages, "settings.json")  # Dossier pages/
        ]

        found = False
        for path in paths_to_check:
            if os.path.exists(path):
                print(f"✅ SUCCÈS : settings.json trouvé ici : {path}")
                self._settings_path = path
                try:
                    with open(path, "r") as f:
                        data = json.load(f)
                        theme = data.get("theme", "Light")
                        auto_start = data.get("auto_start_pomodoro", False)
                        enable_notifications = data.get("enable_notifications", True)
                        sound_effects = data.get("sound_effects", True)
                        playlist_url = str(data.get("pomodoro_playlist_url", "") or "").strip()
                        bg_audio_path = str(data.get("pomodoro_background_audio_path", "") or "").strip()
                        auto_play_bg_audio = bool(data.get("pomodoro_auto_play_background_audio", False))
                        found = True
                        break # On arrête de chercher
                except Exception as e:
                    print(f"❌ Erreur lecture fichier {path}: {e}")
            else:
                print(f"🔍 Pas de fichier ici : {path}")

        if not found:
            print("⚠️ AUCUN fichier settings.json trouvé. Le thème restera 'Light'.")
            print("👉 Avez-vous cliqué sur 'Save Changes' dans l'onglet Settings ?")

        return theme, auto_start, enable_notifications, sound_effects, playlist_url, bg_audio_path, auto_play_bg_audio

    def apply_theme(self):
        self._prune_dead_widgets()
        raw_theme, auto_start, enable_notifications, sound_effects, playlist_url, bg_audio_path, auto_play_bg_audio = self.load_settings_data()
        self.auto_start = bool(auto_start)
        self.enable_notifications = bool(enable_notifications)
        self.sound_effects = bool(sound_effects)
        self._playlist_url = str(playlist_url or "").strip()
        self._bg_audio_path = str(bg_audio_path or "").strip()
        self._auto_play_bg_audio = bool(auto_play_bg_audio)
        try:
            self._sync_audio_buttons()
        except Exception:
            pass
        
        is_dark = str(raw_theme).strip().lower() == "dark"
        self._is_dark = is_dark
        
        print(f"🎨 Application du thème : {'DARK' if is_dark else 'LIGHT'}")

        base = get_theme("Dark" if is_dark else "Light")
        c = {
            "bg": base["bg"],
            "card": base["card"],
            "border": base["border"],
            "text_main": base["text"],
            "text_sub": base["sub"],
            "input_bg": base["input_bg"],
            "badge_bg": base["accent_soft"],
            "badge_text": base["accent"],
            "accent": base["accent"],
            "accent2": base["accent2"],
            "good": base["good"],
            "bad": base["bad"],
            "deep": base["deep"],
            "card_alt": base["card_alt"],
        }
        self._theme_colors = c

        self.setStyleSheet(f"background-color: {c['bg']}; font-family: '{FONT_FAMILY}', 'Segoe UI';")

        for card in self.cards:
            card.setStyleSheet(f"background-color: {c['card']}; border-radius: 30px; border: 1px solid {c['border']};")

        for lbl in self.labels_main:
            lbl.setStyleSheet(lbl.styleSheet() + f" color: {c['text_main']}; border: none; background: transparent;")

        for lbl in self.labels_sub:
            lbl.setStyleSheet(lbl.styleSheet() + f" color: {c['text_sub']}; border: none; background: transparent;")

        for sb in self.spinboxes:
            try:
                is_plan = sb.property("planSpin") is True
            except RuntimeError:
                continue
            if is_plan:
                sb.setStyleSheet(
                    f"QSpinBox {{ background: transparent; color: {c['text_main']}; border: none; "
                    f"padding: 0px 0px 0px 8px; font-weight: 700; font-size: 13px; }} "
                    f"QSpinBox::up-button, QSpinBox::down-button {{ width: 0px; }}"
                )
            else:
                sb.setStyleSheet(
                    f"QSpinBox {{ background: transparent; color: {c['text_main']}; border: none; "
                    f"padding: 0px 0px 0px 10px; font-weight: bold; font-size: 15px; }} "
                    f"QSpinBox::up-button, QSpinBox::down-button {{ width: 0px; }}"
                )

        for ib in self.inputs_bg:
            if ib.property("planInput") is True:
                ib.setStyleSheet(
                    f"background-color: {c['input_bg']}; border: 1px solid {c['border']}; border-radius: 10px;"
                )
            else:
                ib.setStyleSheet(
                    f"background-color: {c['input_bg']}; border: 1px solid {c['border']}; border-radius: 10px;"
                )

        if self.plan_badges:
            for badge in self.plan_badges:
                self._style_plan_badge(badge)

        if self.plan_label_chips:
            for chip in self.plan_label_chips:
                self._style_plan_chip(chip)

        if self.plan_rows:
            if self._is_dark:
                row_bg = c["card_alt"]
                row_border = c["border"]
            else:
                row_bg = c["card"]
                row_border = c["border"]
            for row in self.plan_rows:
                row.setStyleSheet(
                    f"QFrame#PlanRow {{ background-color: {row_bg}; border: 1px solid {row_border}; border-radius: 16px; }}"
                )

        self.badge.setStyleSheet(f"background-color: {c['badge_bg']}; color: {c['badge_text']}; border-radius: 16px; font-weight: bold; font-size: 11px;")
        
        self.timer_label.setStyleSheet(f"font-size: 85px; font-weight: 900; color: {c['text_main']}; background-color: {c['bg']}; border: 12px solid {c['accent']}; border-radius: 160px;")
        
        self.btn_reset_ref.setStyleSheet(f"background-color: {c['input_bg']}; color: {c['text_main']}; border-radius: 12px; font-weight: bold; border: none;")
        if hasattr(self, "btn_stop"):
            stop = c.get("bad", "#ef4444")
            stop_hover = c.get("input_bg", "#1f2937")
            self.btn_stop.setStyleSheet(
                f"QPushButton {{ background-color: transparent; color: {stop}; border: 1px solid {stop}; "
                f"border-radius: 12px; font-weight: bold; }} "
                f"QPushButton:hover {{ background-color: {stop_hover}; }}"
            )
        
        sep_style = f"background-color: {c['border']}; border: none;"
        self.separator.setStyleSheet(sep_style)
        self._update_phase_badge()
        self._update_next_label()
        self._style_break_tips()
        self._style_current_task_card()
        self._style_team_session_card()
        self._sync_focus_mode_toggle()
        self._update_session_progress()
        self._apply_responsive_sizes()
        self._apply_control_icons()
        self._update_team_session_visibility()

    def _apply_control_icons(self):
        style = QApplication.style() if QApplication.instance() else None
        if style is None:
            return
        self._icon_play = style.standardIcon(QStyle.StandardPixmap.SP_MediaPlay)
        self._icon_pause = style.standardIcon(QStyle.StandardPixmap.SP_MediaPause)
        self._icon_stop = style.standardIcon(QStyle.StandardPixmap.SP_MediaStop)
        self._icon_reset = style.standardIcon(QStyle.StandardPixmap.SP_BrowserReload)

        if hasattr(self, "btn_start"):
            self.btn_start.setIcon(self._icon_play)
            self.btn_start.setIconSize(QSize(16, 16))
        if hasattr(self, "btn_stop"):
            self.btn_stop.setIcon(self._icon_stop)
            self.btn_stop.setIconSize(QSize(16, 16))
        if hasattr(self, "btn_reset_ref"):
            self.btn_reset_ref.setIcon(self._icon_reset)
            self.btn_reset_ref.setIconSize(QSize(16, 16))
        self._set_start_button_icon(self.timer.isActive())

    def _set_start_button_icon(self, is_running):
        if not hasattr(self, "btn_start"):
            return
        if is_running:
            icon = getattr(self, "_icon_pause", None)
        else:
            icon = getattr(self, "_icon_play", None)
        if icon:
            self.btn_start.setIcon(icon)

    def _style_current_task_card(self):
        if not hasattr(self, "current_task_card"):
            return
        colors = self._theme_colors or {}
        card_bg = colors.get("card_alt", "#0f2238" if self._is_dark else "#ffffff")
        card_border = colors.get("border", "#25456B" if self._is_dark else "#BAD2E0")
        accent = colors.get("accent2", colors.get("accent", "#82AFF2"))
        text_main = colors.get("text_main", "#e2e8f0" if self._is_dark else "#113356")
        text_sub = colors.get("text_sub", "#94a3b8" if self._is_dark else "#47617C")
        progress_bg = colors.get("border", "#25456B" if self._is_dark else "#BAD2E0")
        progress_chunk = colors.get("accent", "#82AFF2" if self._is_dark else "#3078CD")
        focus_bg = colors.get("card", "#0f2238" if self._is_dark else "#ffffff")
        focus_border = colors.get("border", "#25456B" if self._is_dark else "#BAD2E0")
        toggle_off = colors.get("border", "#25456B" if self._is_dark else "#BAD2E0")
        toggle_on = colors.get("accent", "#82AFF2" if self._is_dark else "#3078CD")
        toggle_thumb = colors.get("text_main", "#e2e8f0" if self._is_dark else "#ffffff")

        self._current_task_border_color = card_border
        self._current_task_style_base = (
            "QFrame#CurrentTaskCard { "
            f"background-color: {card_bg}; border: 1px solid {card_border}; border-radius: 16px; }} "
            "QFrame#CurrentTaskCard QLabel { background-color: transparent; border: none; } "
            "QFrame#CurrentTaskCard[error=\"true\"] { border: 1px solid #ef4444; }"
        )
        self.current_task_card.setStyleSheet(self._current_task_style_base)
        self.current_task_header.setStyleSheet(
            f"color: {accent}; font-size: 11px; font-weight: 700; background: transparent; border: none;"
        )
        self.current_task_title_label.setStyleSheet(
            f"color: {text_main}; font-size: 20px; font-weight: 800; background: transparent; border: none;"
        )
        self.session_progress_title.setStyleSheet(
            f"color: {text_sub}; font-size: 12px; font-weight: 600; background: transparent; border: none;"
        )
        self.session_progress_value.setStyleSheet(
            f"color: {accent}; font-size: 12px; font-weight: 700; background: transparent; border: none;"
        )
        self.session_progress_bar.setStyleSheet(
            f"QProgressBar {{ background-color: {progress_bg}; border: none; border-radius: 4px; }}"
            f"QProgressBar::chunk {{ background-color: {progress_chunk}; border-radius: 4px; }}"
        )

        if hasattr(self, "select_task_btn"):
            self.select_task_btn.setStyleSheet(
                f"QPushButton {{ background-color: transparent; color: {accent}; border: 1px solid {card_border}; "
                f"border-radius: 10px; padding: 4px 8px; font-size: 10px; font-weight: 700; }} "
                f"QPushButton:hover {{ border-color: {accent}; }}"
            )
        if hasattr(self, "focus_assist_btn"):
            self.focus_assist_btn.setStyleSheet(
                f"QPushButton {{ background-color: transparent; color: {accent}; border: 1px solid {focus_border}; "
                f"border-radius: 10px; padding: 4px 8px; font-size: 10px; font-weight: 700; }} "
                f"QPushButton:hover {{ border-color: {accent}; }}"
            )
            self.focus_assist_btn.setIcon(self._make_external_link_icon(QColor(accent)))
            self.focus_assist_btn.setIconSize(QSize(12, 12))

        self.focus_mode_card.setStyleSheet(
            "QFrame#FocusModeCard { "
            f"background-color: {focus_bg}; border: 1px solid {focus_border}; border-radius: 12px; }} "
            "QFrame#FocusModeCard QLabel { background-color: transparent; border: none; }"
        )
        self.focus_mode_icon.setStyleSheet(
            f"background-color: {accent}; color: {card_bg}; border-radius: 9px; font-weight: 900; font-size: 12px;"
        )
        self.focus_mode_title.setStyleSheet(
            f"color: {text_main}; font-size: 12px; font-weight: 700; background: transparent; border: none;"
        )
        self.focus_mode_desc.setStyleSheet(
            f"color: {text_sub}; font-size: 11px; background: transparent; border: none;"
        )
        if hasattr(self, "focus_mode_toggle"):
            self.focus_mode_toggle._bg_color = toggle_off
            self.focus_mode_toggle._active_color = toggle_on
            self.focus_mode_toggle._circle_color = toggle_thumb
            self.focus_mode_toggle.update()

    def _style_team_session_card(self):
        if not hasattr(self, "team_session_card"):
            return
        colors = self._theme_colors or {}
        card_bg = colors.get("card_alt", "#0f2238" if self._is_dark else "#ffffff")
        card_border = colors.get("border", "#25456B" if self._is_dark else "#BAD2E0")
        text_main = colors.get("text_main", "#e2e8f0" if self._is_dark else "#113356")
        text_sub = colors.get("text_sub", "#94a3b8" if self._is_dark else "#47617C")
        accent = colors.get("accent", "#82AFF2" if self._is_dark else "#3078CD")
        deep = colors.get("deep", "#25456B")
        input_bg = colors.get("input_bg", card_bg)
        toggle_off = colors.get("border", "#25456B" if self._is_dark else "#BAD2E0")
        toggle_on = colors.get("accent", "#82AFF2" if self._is_dark else "#3078CD")
        toggle_thumb = colors.get("text_main", "#e2e8f0" if self._is_dark else "#ffffff")

        if hasattr(self, "team_mode_card"):
            self.team_mode_card.setStyleSheet(
                f"QFrame#TeamModeCard {{ background-color: {card_bg}; border: 1px solid {card_border}; border-radius: 16px; }}"
            )
        if hasattr(self, "team_mode_label"):
            self.team_mode_label.setStyleSheet(
                f"color: {text_main}; font-size: 12px; font-weight: 700; background: transparent;"
            )
        if hasattr(self, "team_mode_toggle"):
            self.team_mode_toggle._bg_color = toggle_off
            self.team_mode_toggle._active_color = toggle_on
            self.team_mode_toggle._circle_color = toggle_thumb
            self.team_mode_toggle.update()

        self.team_session_card.setStyleSheet(
            f"QFrame#TeamSessionCard {{ background-color: {card_bg}; border: 1px solid {card_border}; border-radius: 16px; }}"
        )
        self.team_title.setStyleSheet(
            f"color: {accent}; font-size: 11px; font-weight: 800; background: transparent;"
        )
        self.team_status_label.setStyleSheet(
            f"color: {text_sub}; font-size: 12px; font-weight: 600; background: transparent;"
        )
        self.team_phase_label.setStyleSheet(
            f"color: {text_sub}; font-size: 12px; font-weight: 600; background: transparent;"
        )
        self.team_timer_label.setStyleSheet(
            f"color: {text_main}; font-size: 20px; font-weight: 900; background: transparent;"
        )
        self.team_combo.setStyleSheet(
            f"QComboBox {{ background: {input_bg}; border: 1px solid {card_border}; border-radius: 8px; "
            f"padding: 6px 10px; color: {text_main}; }}"
            f"QComboBox::drop-down {{ border: none; }}"
            f"QComboBox QAbstractItemView {{ background: {input_bg}; color: {text_main}; selection-background-color: {accent}; }}"
        )
        for btn in (self.btn_team_refresh, self.btn_team_focus, self.btn_team_break, self.btn_team_stop):
            btn.setStyleSheet(
                f"QPushButton {{ background-color: {accent}; color: white; border-radius: 10px; font-weight: 700; padding: 4px 10px; }}"
                f"QPushButton:hover {{ background-color: {deep}; }}"
            )
        if hasattr(self, "btn_team_stop"):
            stop = colors.get("bad", "#ef4444")
            self.btn_team_stop.setStyleSheet(
                f"QPushButton {{ background-color: transparent; color: {stop}; border: 1px solid {stop}; "
                f"border-radius: 10px; font-weight: 700; padding: 4px 10px; }}"
                f"QPushButton:hover {{ background-color: {input_bg}; }}"
            )

    def refresh_team_list(self):
        if not hasattr(self, "team_combo"):
            return
        if not self.team_mode_enabled:
            return
        try:
            res = api_list_teams()
        except ApiError as e:
            self.team_combo.blockSignals(True)
            self.team_combo.clear()
            self.team_combo.addItem("Team sync unavailable")
            self.team_combo.blockSignals(False)
            if self._has_team_session_ui():
                self.team_status_label.setText(f"Status: {str(e)}")
            self.team_selected_id = None
            self._update_team_session_visibility()
            return
        teams = res.get("teams", []) if isinstance(res, dict) else []
        current_id = self.team_selected_id
        self.team_combo.blockSignals(True)
        self.team_combo.clear()
        if not teams:
            self.team_combo.addItem("No teams", None)
            self.team_selected_id = None
        else:
            self.team_combo.addItem("Select a team", None)
            selected_index = 0
            for idx, team in enumerate(teams, start=1):
                team_id = team.get("id")
                name = team.get("name") or f"Team {team_id}"
                self.team_combo.addItem(name, team_id)
                if current_id and team_id == current_id:
                    selected_index = idx
            self.team_combo.setCurrentIndex(selected_index)
            self.team_selected_id = self.team_combo.currentData()
        self.team_combo.blockSignals(False)
        self._update_team_session_visibility()
        if self.team_selected_id:
            self.refresh_team_session()

    def _on_team_changed(self, index):
        self.team_selected_id = self.team_combo.currentData()
        self._update_team_session_visibility()
        if self.team_selected_id:
            self.refresh_team_session()

    def refresh_team_session(self):
        if not self._has_team_session_ui():
            if self.team_timer_poll.isActive():
                self.team_timer_poll.stop()
            if self.team_timer_tick.isActive():
                self.team_timer_tick.stop()
            return
        if not self.team_mode_enabled:
            return
        if not self.team_selected_id:
            self.team_status_label.setText("Status: Idle")
            self.team_phase_label.setText("Phase: -")
            self.team_timer_label.setText("--:--")
            self.team_remaining_seconds = None
            return
        try:
            res = api_get_team_pomodoro(self.team_selected_id)
        except ApiError as e:
            self.team_status_label.setText(f"Status: {str(e)}")
            return
        if not isinstance(res, dict):
            self.team_status_label.setText("Status: Idle")
            return
        self.team_status = res.get("status") or "idle"
        self.team_phase = res.get("phase") or "-"
        remaining = res.get("remaining_seconds")
        if remaining is None and res.get("started_at") and res.get("duration_min"):
            remaining = self._compute_remaining(res.get("started_at"), res.get("duration_min"))
        self.team_remaining_seconds = remaining
        self.team_status_label.setText(f"Status: {self.team_status.title()}")
        self.team_phase_label.setText(f"Phase: {str(self.team_phase).title()}")
        if remaining is None:
            self.team_timer_label.setText("--:--")
        else:
            self.team_timer_label.setText(self._format_seconds(remaining))

    def start_team_focus(self):
        if not self._has_team_session_ui():
            return
        if not self.team_selected_id:
            self.team_status_label.setText("Status: Select a team first")
            return
        duration = int(getattr(self, "focus_input", None).value()) if hasattr(self, "focus_input") else 25
        try:
            api_start_team_pomodoro(self.team_selected_id, "focus", duration)
        except ApiError as e:
            self.team_status_label.setText(f"Status: {str(e)}")
            return
        self.refresh_team_session()

    def start_team_break(self):
        if not self._has_team_session_ui():
            return
        if not self.team_selected_id:
            self.team_status_label.setText("Status: Select a team first")
            return
        duration = int(getattr(self, "short_input", None).value()) if hasattr(self, "short_input") else 5
        try:
            api_start_team_pomodoro(self.team_selected_id, "break", duration)
        except ApiError as e:
            self.team_status_label.setText(f"Status: {str(e)}")
            return
        self.refresh_team_session()

    def stop_team_session(self):
        if not self._has_team_session_ui():
            return
        if not self.team_selected_id:
            return
        try:
            api_stop_team_pomodoro(self.team_selected_id)
        except ApiError as e:
            self.team_status_label.setText(f"Status: {str(e)}")
            return
        self.refresh_team_session()

    def _tick_team_countdown(self):
        if not self._has_team_session_ui():
            return
        if self.team_status != "running":
            return
        if self.team_remaining_seconds is None:
            return
        self.team_remaining_seconds = max(0, self.team_remaining_seconds - 1)
        self.team_timer_label.setText(self._format_seconds(self.team_remaining_seconds))
        if self.team_remaining_seconds <= 0:
            self.team_status = "completed"
            self.team_status_label.setText("Status: Completed")

    def _has_team_mode_ui(self):
        return hasattr(self, "team_combo")

    def _has_team_session_ui(self):
        return (
            hasattr(self, "team_status_label")
            and hasattr(self, "team_phase_label")
            and hasattr(self, "team_timer_label")
        )

    def _format_seconds(self, total_seconds):
        try:
            total_seconds = int(total_seconds)
        except Exception:
            total_seconds = 0
        minutes = total_seconds // 60
        seconds = total_seconds % 60
        return f"{minutes:02d}:{seconds:02d}"

    def _compute_remaining(self, started_at, duration_min):
        try:
            try:
                duration_min = int(duration_min)
            except Exception:
                duration_min = 0
            if isinstance(started_at, str):
                if started_at.endswith("Z"):
                    started_at = started_at.replace("Z", "+00:00")
                started = datetime.fromisoformat(started_at)
            else:
                started = started_at
            now = datetime.now(tz=started.tzinfo)
            elapsed = (now - started).total_seconds()
            return int(max(0, duration_min * 60 - elapsed))
        except Exception:
            return None

    def _sync_focus_mode_toggle(self):
        if not hasattr(self, "focus_mode_toggle"):
            return
        focus_mode_on = not self.enable_notifications
        self._suppress_focus_mode_save = True
        self.focus_mode_toggle.setChecked(focus_mode_on)
        self._suppress_focus_mode_save = False
        self._update_focus_mode_text(focus_mode_on)

    def _update_focus_mode_text(self, focus_mode_on):
        if focus_mode_on:
            self.focus_mode_title.setText("Focus Mode Active")
            self.focus_mode_desc.setText("Notifications and sounds are disabled")
        else:
            self.focus_mode_title.setText("Focus Mode Off")
            self.focus_mode_desc.setText("Notifications and sounds are enabled")

    def on_focus_mode_toggled(self, state):
        if self._suppress_focus_mode_save:
            return
        focus_mode_on = bool(state)
        if focus_mode_on:
            if self._pre_focus_notifications is None:
                self._pre_focus_notifications = self.enable_notifications
            if self._pre_focus_sound is None:
                self._pre_focus_sound = self.sound_effects
            self.enable_notifications = False
            self.sound_effects = False
        else:
            self.enable_notifications = True if self._pre_focus_notifications is None else self._pre_focus_notifications
            self.sound_effects = True if self._pre_focus_sound is None else self._pre_focus_sound
            self._pre_focus_notifications = None
            self._pre_focus_sound = None
        self._update_focus_mode_text(focus_mode_on)
        self._save_settings_value("enable_notifications", self.enable_notifications)
        self._save_settings_value("sound_effects", self.sound_effects)
        if focus_mode_on:
            self._open_focus_assist_if_possible(auto=True)

    def open_focus_assist_settings(self):
        self._open_focus_assist_if_possible(auto=False)

    def _open_focus_assist_if_possible(self, auto=False):
        opened = False
        if os.name == "nt":
            opened = self._open_windows_uri("ms-settings:")
        if not opened and not auto:
            QMessageBox.information(self, "Settings", "Unable to open Windows Settings.")

    def _open_windows_uri(self, uri):
        try:
            os.startfile(uri)
            return True
        except Exception:
            pass
        try:
            subprocess.Popen(["explorer.exe", uri], shell=False)
            return True
        except Exception:
            pass
        try:
            subprocess.Popen(["cmd", "/c", "start", "", uri], shell=False)
            return True
        except Exception:
            pass
        try:
            subprocess.Popen(
                ["powershell", "-NoProfile", "-Command", f"Start-Process '{uri}'"],
                shell=False
            )
            return True
        except Exception:
            pass
        try:
            return QDesktopServices.openUrl(QUrl(uri))
        except Exception:
            return False

    def _make_external_link_icon(self, color):
        size = 12
        pix = QPixmap(size, size)
        pix.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pix)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        pen = QPen(color)
        pen.setWidth(2)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        # square
        painter.drawRect(1, 3, 8, 8)
        # arrow
        painter.drawLine(6, 1, 11, 1)
        painter.drawLine(11, 1, 11, 6)
        painter.drawLine(11, 1, 6, 6)
        painter.end()
        return QIcon(pix)

    def _save_settings_value(self, key, value):
        path = self._settings_path
        if not path or not os.path.exists(path):
            dir_pages = os.path.dirname(os.path.abspath(__file__))
            dir_root = os.path.dirname(dir_pages)
            candidates = [
                os.path.join(dir_root, "settings.json"),
                "settings.json",
                os.path.join(dir_pages, "settings.json"),
            ]
            for candidate in candidates:
                if os.path.exists(candidate):
                    path = candidate
                    self._settings_path = candidate
                    break
        if not path:
            return
        try:
            with open(path, "r") as f:
                data = json.load(f)
        except Exception:
            data = {}
        data[key] = value
        try:
            with open(path, "w") as f:
                json.dump(data, f, indent=4)
        except Exception:
            return

    # --- HELPERS ---
    def create_card(self, title):
        card = QFrame()
        card.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.cards.append(card)
        layout = QVBoxLayout(card)
        layout.setContentsMargins(25, 20, 25, 20)
        t = QLabel(title)
        t.setStyleSheet("font-size: 18px; font-weight: 800;")
        self.labels_main.append(t)
        layout.addWidget(t)
        return card

    def add_styled_setting(self, card, label_text, default_val):
        lbl = QLabel(label_text)
        lbl.setStyleSheet("font-size: 12px; font-weight: 600;")
        self.labels_sub.append(lbl)
        
        container = QFrame()
        container.setFixedHeight(45)
        self.inputs_bg.append(container)
        
        layout = QHBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0); layout.setSpacing(0)

        spin = NoWheelSpinBox()
        spin.setRange(1, 120); spin.setValue(default_val)
        self.spinboxes.append(spin)

        btn_col = QFrame()
        btn_col.setFixedWidth(40)
        v_layout = QVBoxLayout(btn_col)
        v_layout.setContentsMargins(0, 0, 0, 0); v_layout.setSpacing(0)

        btn_up = QPushButton("▲"); btn_down = QPushButton("▼")
        btn_style = "QPushButton { background-color: transparent; border: none; color: #47617C; font-weight: bold; } QPushButton:hover { color: #3078CD; }"
        btn_up.setStyleSheet(btn_style); btn_down.setStyleSheet(btn_style)
        btn_up.setCursor(Qt.CursorShape.PointingHandCursor); btn_down.setCursor(Qt.CursorShape.PointingHandCursor)

        btn_up.clicked.connect(lambda: spin.setValue(spin.value() + 1))
        btn_down.clicked.connect(lambda: spin.setValue(spin.value() - 1))

        v_layout.addWidget(btn_up); v_layout.addWidget(btn_down)
        layout.addWidget(spin); layout.addWidget(btn_col)

        spin._setting_label = lbl
        spin._setting_container = container
        spin.valueChanged.connect(self.sync_settings)
        card.layout().addWidget(lbl); card.layout().addWidget(container)
        return spin

    def _update_interval_dependent_inputs(self):
        if not hasattr(self, "interval_input") or not hasattr(self, "short_input"):
            return
        try:
            intervals = max(1, int(self.interval_input.value()))
        except Exception:
            intervals = 1
        show_short = intervals > 1
        label = getattr(self.short_input, "_setting_label", None)
        container = getattr(self.short_input, "_setting_container", None)
        if label is not None:
            label.setVisible(show_short)
        if container is not None:
            container.setVisible(show_short)
        try:
            self.short_input.setEnabled(show_short)
        except Exception:
            pass

    def _add_plan_setting(self, label_text, default_val, max_val=180):
        return None

    def _create_compact_spin(self, default_val, max_val=180):
        container = QFrame()
        container.setFixedHeight(36)
        container.setProperty("planInput", True)
        self.inputs_bg.append(container)

        layout = QHBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        spin = NoWheelSpinBox()
        spin.setRange(1, max_val)
        spin.setValue(default_val)
        spin.setFixedWidth(92)
        spin.setFixedHeight(30)
        spin.setProperty("planSpin", True)
        self.spinboxes.append(spin)

        btn_col = QFrame()
        btn_col.setFixedWidth(22)
        v_layout = QVBoxLayout(btn_col)
        v_layout.setContentsMargins(0, 0, 0, 0)
        v_layout.setSpacing(0)

        btn_up = QPushButton("▲")
        btn_down = QPushButton("▼")
        btn_style = "QPushButton { background-color: transparent; border: none; color: #47617C; font-weight: bold; } QPushButton:hover { color: #3078CD; }"
        btn_up.setStyleSheet(btn_style)
        btn_down.setStyleSheet(btn_style)
        btn_up.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_down.setCursor(Qt.CursorShape.PointingHandCursor)

        btn_up.clicked.connect(lambda: spin.setValue(spin.value() + 1))
        btn_down.clicked.connect(lambda: spin.setValue(spin.value() - 1))

        v_layout.addWidget(btn_up)
        v_layout.addWidget(btn_down)
        layout.addWidget(spin)
        layout.addWidget(btn_col)
        spin.valueChanged.connect(self._on_plan_changed)
        return container, spin

    def _style_plan_badge(self, badge):
        if badge is None:
            return
        colors = self._theme_colors or {}
        badge_bg = colors.get("accent_soft", "#e0f2fe")
        badge_border = colors.get("border", "#BAD2E0")
        badge_text = colors.get("accent", "#3078CD")
        try:
            badge.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
            badge.setStyleSheet(
                f"background-color: {badge_bg}; color: {badge_text}; border: 1px solid {badge_border}; "
                f"border-radius: 12px; padding: 8px 10px; font-weight: 800; font-size: 11px;"
            )
        except RuntimeError:
            return

    def _style_plan_chip(self, chip):
        if chip is None:
            return
        colors = self._theme_colors or {}
        focus_bg = colors.get("accent_soft", "#e0f2fe")
        focus_border = colors.get("border", "#BAD2E0")
        focus_text = colors.get("accent", "#3078CD")
        break_bg = colors.get("card_alt", "#EEF5FA")
        break_border = colors.get("border", "#BAD2E0")
        break_text = colors.get("accent2", "#82AFF2")
        long_bg = colors.get("deep", "#25456B")
        long_border = colors.get("border", "#BAD2E0")
        long_text = colors.get("text_main", "#F8F6F2")
        name = chip.objectName()
        if name == "PlanChipFocus":
            bg, border, text = focus_bg, focus_border, focus_text
        elif name == "PlanChipLong":
            bg, border, text = long_bg, long_border, long_text
        else:
            bg, border, text = break_bg, break_border, break_text
        try:
            chip.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
            chip.setStyleSheet(
                f"background-color: {bg}; color: {text}; border: 1px solid {border}; "
                f"border-radius: 10px; padding: 4px 10px; font-weight: 800; font-size: 10px;"
            )
        except RuntimeError:
            return

    def _add_plan_row(self, idx, focus_default, break_default, break_label):
        row = QFrame()
        row.setMinimumHeight(140)
        row.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        row.setObjectName("PlanRow")
        row.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.plan_rows.append(row)

        layout = QVBoxLayout(row)
        layout.setContentsMargins(10, 6, 10, 14)
        layout.setSpacing(8)
        layout.setAlignment(Qt.AlignmentFlag.AlignTop)

        badge = QLabel(f"Interval {idx}")
        badge.setObjectName("PlanBadge")
        badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        badge.setFixedWidth(102)
        badge.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.plan_badges.append(badge)
        self._style_plan_badge(badge)

        header = QHBoxLayout()
        header.addStretch()
        header.addWidget(badge)
        header.addStretch()
        layout.addLayout(header)

        grid = QGridLayout()
        grid.setHorizontalSpacing(16)
        grid.setVerticalSpacing(18)
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 0)

        focus_chip = QLabel("Focus")
        focus_chip.setObjectName("PlanChipFocus")
        focus_chip.setAlignment(Qt.AlignmentFlag.AlignCenter)
        focus_chip.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.plan_label_chips.append(focus_chip)
        self._style_plan_chip(focus_chip)
        focus_container, focus_spin = self._create_compact_spin(focus_default, max_val=180)
        grid.addWidget(focus_chip, 0, 0, alignment=Qt.AlignmentFlag.AlignLeft)
        grid.addWidget(focus_container, 0, 1, alignment=Qt.AlignmentFlag.AlignRight)

        break_chip = QLabel(break_label)
        chip_name = "PlanChipLong" if break_label == "Long Break" else "PlanChipBreak"
        break_chip.setObjectName(chip_name)
        break_chip.setAlignment(Qt.AlignmentFlag.AlignCenter)
        break_chip.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.plan_label_chips.append(break_chip)
        self._style_plan_chip(break_chip)
        break_container, break_spin = self._create_compact_spin(break_default, max_val=90)
        grid.addWidget(break_chip, 1, 0, alignment=Qt.AlignmentFlag.AlignLeft)
        grid.addWidget(break_container, 1, 1, alignment=Qt.AlignmentFlag.AlignRight)

        layout.addLayout(grid)

        layout.addStretch()

        self.plan_layout.addWidget(row)
        self.plan_row_widgets.append(row)
        return focus_spin, break_spin

    def add_stat_line(self, card, label_text, val_text, is_bold=False):
        row = QHBoxLayout()
        l = QLabel(label_text); v = QLabel(val_text)
        if is_bold:
            self.labels_main.append(l); self.labels_main.append(v)
            l.setStyleSheet("font-weight: 800;"); v.setStyleSheet("font-weight: 800;")
        else:
            self.labels_sub.append(l); self.labels_sub.append(v)
        row.addWidget(l); row.addStretch(); row.addWidget(v)
        card.layout().addLayout(row)
        return v

    def _on_interval_changed(self, *_):
        self._rebuild_plan_inputs()
        self._on_plan_changed()

    def _rebuild_plan_inputs(self):
        intervals = max(1, int(self.interval_input.value()))
        if self._last_interval_value == intervals and self.plan_focus_spins and self.plan_break_spins:
            return
        self._last_interval_value = intervals

        while self.plan_layout.count():
            item = self.plan_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self.plan_row_widgets = []
        self.plan_focus_spins = []
        self.plan_break_spins = []
        self.plan_badges = []
        self.plan_rows = []
        self.plan_label_chips = []
        alive_spinboxes = []
        for sb in self.spinboxes:
            try:
                if sb is not None and sb.parent() is not None:
                    alive_spinboxes.append(sb)
            except RuntimeError:
                continue
        self.spinboxes = alive_spinboxes
        alive_inputs = []
        for ib in self.inputs_bg:
            try:
                if ib is not None and ib.parent() is not None:
                    alive_inputs.append(ib)
            except RuntimeError:
                continue
        self.inputs_bg = alive_inputs

        focus_default = int(self.focus_input.value())
        short_default = int(self.short_input.value())
        long_default = int(self.long_input.value())

        for idx in range(1, intervals + 1):
            break_label = "Long Break" if idx == intervals else f"Break {idx}"
            break_default = long_default if idx == intervals else short_default
            focus_spin, break_spin = self._add_plan_row(idx, focus_default, break_default, break_label)
            self.plan_focus_spins.append(focus_spin)
            self.plan_break_spins.append(break_spin)

        self._rebuild_plan_phases()
        self._sync_plan_stats()
        if hasattr(self, "plan_scroll"):
            try:
                self._update_plan_scroll_height(intervals)
                self.plan_scroll.verticalScrollBar().setValue(0)
            except Exception:
                pass
        self._apply_plan_styles()
        if hasattr(self, "plan_container"):
            try:
                self.plan_container.adjustSize()
                self.plan_container.updateGeometry()
            except Exception:
                pass
        self._apply_task_type_constraints(self.current_task_type)
        self._update_interval_dependent_inputs()

    def _apply_plan_styles(self):
        c = self._theme_colors or {}
        if c:
            for sb in self.spinboxes:
                if sb is None:
                    continue
                if sb.parent() is None:
                    continue
                try:
                    if sb.property("planSpin") is True:
                        sb.setStyleSheet(
                            f"QSpinBox {{ background: transparent; color: {c['text_main']}; border: none; "
                            f"padding: 0px 0px 0px 8px; font-weight: 700; font-size: 13px; }} "
                            f"QSpinBox::up-button, QSpinBox::down-button {{ width: 0px; }}"
                        )
                    else:
                        sb.setStyleSheet(
                            f"QSpinBox {{ background: transparent; color: {c['text_main']}; border: none; padding: 0px 0px 0px 10px; "
                            f"font-weight: bold; font-size: 15px; }} QSpinBox::up-button, QSpinBox::down-button {{ width: 0px; }}"
                        )
                except RuntimeError:
                    continue
            for ib in self.inputs_bg:
                if ib is None:
                    continue
                if ib.parent() is None:
                    continue
                try:
                    ib.setStyleSheet(f"background-color: {c['input_bg']}; border: 1px solid {c['border']}; border-radius: 10px;")
                except RuntimeError:
                    continue

        if self.plan_badges:
            for badge in self.plan_badges:
                self._style_plan_badge(badge)

        if self.plan_label_chips:
            for chip in self.plan_label_chips:
                self._style_plan_chip(chip)

        if self.plan_rows:
            if self._is_dark:
                row_bg = "#161d24"
                row_border = "#2b3440"
            else:
                row_bg = "#f8fafc"
                row_border = "#e2e8f0"
            for row in self.plan_rows:
                try:
                    row.setStyleSheet(
                        f"QFrame#PlanRow {{ background-color: {row_bg}; border: 1px solid {row_border}; border-radius: 16px; }}"
                    )
                except RuntimeError:
                    continue

    def _update_plan_scroll_height(self, intervals):
        if not hasattr(self, "plan_scroll") or not hasattr(self, "plan_layout"):
            return
        row_height = 140
        spacing = self.plan_layout.spacing()
        margins = self.plan_layout.contentsMargins()
        content_height = (intervals * row_height) + max(0, intervals - 1) * spacing + margins.top() + margins.bottom()
        min_height = 180
        max_height = 380
        target_height = max(min_height, min(max_height, content_height))
        self.plan_scroll.setFixedHeight(int(target_height))

    def _rebuild_plan_phases(self):
        self.plan_phases = []
        intervals = max(1, int(self.interval_input.value()))
        for idx in range(intervals):
            focus_minutes = int(self.plan_focus_spins[idx].value()) if idx < len(self.plan_focus_spins) else int(self.focus_input.value())
            self.plan_phases.append({
                "phase": "focus",
                "minutes": focus_minutes
            })
            break_minutes = int(self.plan_break_spins[idx].value()) if idx < len(self.plan_break_spins) else int(self.short_input.value())
            phase_type = "long_break" if idx == intervals - 1 else "short_break"
            self.plan_phases.append({
                "phase": phase_type,
                "minutes": break_minutes
            })

    def _current_plan_phase(self):
        if not self.plan_phases:
            return None
        idx = max(0, min(self.plan_index, len(self.plan_phases) - 1))
        return self.plan_phases[idx]

    def _next_plan_phase(self):
        if not self.plan_phases:
            return None
        next_idx = (self.plan_index + 1) % len(self.plan_phases)
        return self.plan_phases[next_idx]

    def _on_plan_changed(self, *_):
        self._rebuild_plan_phases()
        self._sync_plan_stats()
        if not self.timer.isActive():
            if not self.plan_phases:
                return
            self.plan_index = max(0, min(self.plan_index, len(self.plan_phases) - 1))
            self.phase = self.plan_phases[self.plan_index]["phase"]
            minutes = int(self.plan_phases[self.plan_index]["minutes"])
            self.time_left = minutes * 60
            self.timer_label.setText(f"{minutes:02d}:00")
            self._update_next_label()
            self._update_session_progress()

    def _sync_plan_stats(self):
        if not self.plan_phases:
            return
        total_minutes = sum(int(item["minutes"]) for item in self.plan_phases)
        self.total_stat.setText(format_duration_minutes(total_minutes))

    def sync_settings(self):
        sender = self.sender()
        f, s, l, i = self.focus_input.value(), self.short_input.value(), self.long_input.value(), self.interval_input.value()

        if sender is self.focus_input and self.plan_focus_spins:
            for spin in self.plan_focus_spins:
                spin.blockSignals(True)
                spin.setValue(int(f))
                spin.blockSignals(False)
        elif sender is self.short_input and self.plan_break_spins:
            for spin in self.plan_break_spins[:-1]:
                spin.blockSignals(True)
                spin.setValue(int(s))
                spin.blockSignals(False)
        elif sender is self.long_input and self.plan_break_spins:
            self.plan_break_spins[-1].blockSignals(True)
            self.plan_break_spins[-1].setValue(int(l))
            self.plan_break_spins[-1].blockSignals(False)

        if self.plan_phases:
            self._on_plan_changed()
        else:
            self.total_stat.setText(format_duration_minutes((f * i) + (s * (i - 1)) + l))
            if not self.timer.isActive():
                minutes = self._phase_minutes(self.phase)
                self.time_left = minutes * 60
                self.timer_label.setText(f"{minutes:02d}:00")
                self._update_next_label()
                self._update_session_progress()

    def _update_session_progress(self):
        if not hasattr(self, "session_progress_bar"):
            return
        if self.plan_phases and self.phase == "focus":
            total_minutes = max(1, int(self._phase_minutes(self.phase)))
        else:
            total_minutes = max(1, int(self.focus_input.value()))
        total_seconds = total_minutes * 60
        if self.phase == "focus":
            elapsed = max(0, total_seconds - int(self.time_left))
            percent = int(round((elapsed / total_seconds) * 100))
        else:
            percent = 0
        percent = max(0, min(100, percent))
        self.session_progress_bar.setValue(percent)
        if hasattr(self, "session_progress_value"):
            self.session_progress_value.setText(f"{percent}%")

    def toggle_timer(self):
        if self.timer.isActive():
            self.timer.stop()
            self._stop_background_audio()
            self._update_start_button(resume=True)
            if not self._should_disable_start():
                good = (self._theme_colors or {}).get("good", "#10b981")
                self.btn_start.setStyleSheet(f"background-color: {good}; color: white; border-radius: 12px; font-weight: bold; border: none;")
        else:
            if self._should_disable_start():
                self._show_task_required()
                return
            self._start_timer()

    def stop_timer(self):
        if self.phase != "focus":
            return
        if self.session_started_at is None:
            return
        self.timer.stop()
        self._stop_background_audio()
        self._log_session(status="stopped")
        self._set_phase("focus", reset_time=True, notify=False)

    def update_timer(self):
        if self.time_left > 0:
            self.time_left -= 1
            mins, secs = divmod(self.time_left, 60)
            self.timer_label.setText(f"{mins:02d}:{secs:02d}")
            self._update_session_progress()
        else:
            self.timer.stop()
            if self.plan_phases:
                current_minutes = int(self._phase_minutes(self.phase))
                if self.phase == "focus":
                    self._log_session(status="completed", duration_override=current_minutes)
                    self.sessions_completed += 1
                    self.sessions_count.setText(str(self.sessions_completed))
                    if self._play_session_end_sound():
                        self._suppress_next_phase_sound = True

                self.plan_index += 1
                if self.plan_index >= len(self.plan_phases):
                    self.plan_index = 0
                    self.sessions_completed = 0
                    self.sessions_count.setText("0")

                next_phase = self.plan_phases[self.plan_index]["phase"]
                self._set_phase(next_phase, reset_time=True, notify=True)
                self._update_session_progress()
            else:
                if self.phase == "focus":
                    self._log_session(status="completed", duration_override=self.focus_input.value())
                    self.sessions_completed += 1
                    self.sessions_count.setText(str(self.sessions_completed))
                    if self._play_session_end_sound():
                        self._suppress_next_phase_sound = True
                    next_phase = self._next_break_phase(pending_focus=False)
                else:
                    if self.phase == "long_break":
                        self.sessions_completed = 0
                        self.sessions_count.setText("0")
                    next_phase = "focus"

                self._set_phase(next_phase, reset_time=True, notify=True)
                self._update_session_progress()

            if self.auto_start and self.phase == "focus":
                self._start_timer()
            else:
                self._update_start_button(resume=False)
                if not self._should_disable_start():
                    accent = (self._theme_colors or {}).get("accent", "#3078CD")
                    self.btn_start.setStyleSheet(f"background-color: {accent}; color: white; border-radius: 12px; font-weight: bold; border: none;")

    def reset_timer(self):
        if self.session_started_at is not None and self.phase == "focus":
            self._log_session(status="stopped")
        self.timer.stop()
        self._stop_background_audio()
        self.sessions_completed = 0
        self.sessions_count.setText("0")
        self.plan_index = 0
        # Reset settings to defaults
        for spin in (self.focus_input, self.short_input, self.long_input, self.interval_input):
            try:
                spin.blockSignals(True)
            except Exception:
                pass
        self.focus_input.setValue(25)
        self.short_input.setValue(5)
        self.long_input.setValue(15)
        self.interval_input.setValue(4)
        for spin in (self.focus_input, self.short_input, self.long_input, self.interval_input):
            try:
                spin.blockSignals(False)
            except Exception:
                pass
        self._rebuild_plan_inputs()
        self.current_task_id = None
        self.current_task_title = None
        self.current_task_priority = None
        self.current_task_type = None
        if hasattr(self, "current_task_title_label"):
            self.current_task_title_label.setText("No Task Selected")
        if hasattr(self, "current_task_meta"):
            self.current_task_meta.setText("Session Type: -")
        self._set_phase("focus", reset_time=True, notify=False)
        self._apply_task_type_constraints(None)
        self._update_start_button(resume=False)
        if not self._should_disable_start():
            accent = (self._theme_colors or {}).get("accent", "#3078CD")
            self.btn_start.setStyleSheet(f"background-color: {accent}; color: white; border-radius: 12px; font-weight: bold; border: none;")

    def set_task(self, task_id, title, priority=None, task_type=None):
        self.current_task_id = task_id
        self.current_task_title = title
        self.current_task_priority = normalize_priority(priority)
        self.current_task_type = normalize_task_type(task_type)
        safe_title = title.strip() if title else "No Task Selected"
        if hasattr(self, "current_task_title_label"):
            self.current_task_title_label.setText(safe_title)
        if hasattr(self, "current_task_meta"):
            parts = []
            meta_lines = []
            limits = TASK_TYPE_LIMITS.get(self.current_task_type)
            if limits:
                min_val, max_val = limits
            else:
                min_val, max_val = 1, 120
            if self.current_task_type or self.current_task_priority:
                meta_lines.append(
                    f"You can choose only between {min_val} and {max_val} min session duration."
                )
            if self.current_task_priority:
                session_label = priority_session_label(self.current_task_priority)
                parts.append(f"Session Type: {session_label}")
            else:
                parts.append("Session Type: -")
            if self.current_task_type:
                parts.append(f"Task Type: {self.current_task_type}")
            meta_lines.append("  ·  ".join(parts))
            self.current_task_meta.setText("\n".join(meta_lines))
        if not self.timer.isActive():
            self._update_start_button(resume=False)
        self._apply_task_type_constraints(self.current_task_type)

    def prepare_recovery_break(self, prefer_long=True):
        if self.timer.isActive():
            if self.phase == "focus" and self.session_started_at is not None:
                self._log_session(status="stopped")
            self.timer.stop()
        target_phase = "long_break" if prefer_long else "short_break"
        if self.plan_phases:
            target_idx = None
            for idx, item in enumerate(self.plan_phases):
                if item.get("phase") == target_phase:
                    target_idx = idx
                    break
            if target_idx is not None:
                self.plan_index = target_idx
        self._set_phase(target_phase, reset_time=True, notify=True)
        self._update_start_button(resume=False)

    def _has_task_selected(self):
        return self.current_task_id is not None and bool(str(self.current_task_title or "").strip())

    def _should_disable_start(self):
        return self.phase == "focus" and not self._has_task_selected()

    def _show_task_required(self):
        self._flash_task_card()

    def _flash_task_card(self):
        if not hasattr(self, "current_task_card"):
            return
        self.current_task_card.setProperty("error", True)
        self.current_task_card.style().unpolish(self.current_task_card)
        self.current_task_card.style().polish(self.current_task_card)
        self.current_task_card.update()
        QTimer.singleShot(900, self._clear_task_card_error)

    def _clear_task_card_error(self):
        if not hasattr(self, "current_task_card"):
            return
        self.current_task_card.setProperty("error", False)
        self.current_task_card.style().unpolish(self.current_task_card)
        self.current_task_card.style().polish(self.current_task_card)
        self.current_task_card.update()

    def _request_task_selection(self):
        self.select_task_requested.emit()

    def eventFilter(self, obj, event):
        if obj is self.left_container and event.type() == QEvent.Type.MouseButtonPress:
            if self._should_disable_start():
                try:
                    pos = event.position().toPoint()
                except Exception:
                    pos = event.pos()
                if self.btn_start.geometry().contains(pos):
                    self._flash_task_card()
                    return True
        return super().eventFilter(obj, event)

    def _phase_minutes(self, phase):
        if self.plan_phases:
            current = self._current_plan_phase()
            if current:
                return int(current["minutes"])
        if phase == "short_break":
            return self.short_input.value()
        if phase == "long_break":
            return self.long_input.value()
        return self.focus_input.value()

    def _next_break_phase(self, pending_focus=False):
        intervals = max(1, self.interval_input.value())
        count = self.sessions_completed + (1 if pending_focus else 0)
        if count % intervals == 0:
            return "long_break"
        return "short_break"

    def _phase_label(self, phase):
        if phase == "short_break":
            return "Short Break"
        if phase == "long_break":
            return "Long Break"
        return "Focus"

    def _update_next_label(self):
        if self.plan_phases:
            next_phase = self._next_plan_phase()
            if next_phase:
                mins = int(next_phase["minutes"])
                self.next_label.setText(
                    f"Next Up: {self._phase_label(next_phase['phase'])} ({format_duration_minutes(mins)})"
                )
                return
        if self.phase == "focus":
            next_phase = self._next_break_phase(pending_focus=True)
        else:
            next_phase = "focus"
        mins = self._phase_minutes(next_phase)
        self.next_label.setText(
            f"Next Up: {self._phase_label(next_phase)} ({format_duration_minutes(mins)})"
        )

    def _update_phase_badge(self):
        colors = self._theme_colors or {}
        if self.phase == "focus":
            self.badge.setText("FOCUS TIME")
            bg = colors.get("badge_bg", colors.get("accent_soft", "#dbeafe"))
            fg = colors.get("badge_text", colors.get("accent", "#3078CD"))
        elif self.phase == "short_break":
            self.badge.setText("SHORT BREAK")
            bg = colors.get("card_alt", "#1b2f4d")
            fg = colors.get("accent2", "#82AFF2")
        else:
            self.badge.setText("LONG BREAK")
            bg = colors.get("deep", "#25456B")
            fg = colors.get("text_main", "#F8F6F2")
        self.badge.setStyleSheet(f"background-color: {bg}; color: {fg}; border-radius: 16px; font-weight: bold; font-size: 11px;")

    def _update_start_button(self, resume=False):
        colors = self._theme_colors or {}
        if self._should_disable_start():
            self.btn_start.setEnabled(False)
            self.btn_start.setText("Select Task")
            disabled_bg = colors.get("border", "#334155")
            disabled_text = colors.get("text_sub", "#94a3b8")
            self.btn_start.setStyleSheet(f"background-color: {disabled_bg}; color: {disabled_text}; border-radius: 12px; font-weight: bold; border: none;")
            self._set_start_button_icon(False)
            self._update_stop_button_visibility()
            return
        self.btn_start.setEnabled(True)
        phase_label = self._phase_label(self.phase)
        if resume:
            self.btn_start.setText(f"Resume {phase_label}")
        else:
            self.btn_start.setText(f"Start {phase_label}")
            accent = colors.get("accent", "#3078CD")
            self.btn_start.setStyleSheet(f"background-color: {accent}; color: white; border-radius: 12px; font-weight: bold; border: none;")
        self._set_start_button_icon(False)
        self._update_stop_button_visibility()

    def _update_stop_button_visibility(self):
        if not hasattr(self, "btn_stop"):
            return
        show = self.phase == "focus" and self.session_started_at is not None
        self.btn_stop.setVisible(bool(show))

    def _apply_task_type_constraints(self, task_type):
        if not hasattr(self, "focus_input"):
            return
        limits = TASK_TYPE_LIMITS.get(task_type)
        if limits:
            min_val, max_val = limits
        else:
            min_val, max_val = 1, 120
        try:
            self.focus_input.setRange(int(min_val), int(max_val))
        except Exception:
            pass
        try:
            current = int(self.focus_input.value())
            if current < min_val:
                self.focus_input.setValue(int(min_val))
            elif current > max_val:
                self.focus_input.setValue(int(max_val))
        except Exception:
            pass
        for spin in getattr(self, "plan_focus_spins", []):
            if spin is None:
                continue
            try:
                spin.setRange(int(min_val), int(max_val))
                val = int(spin.value())
                if val < min_val:
                    spin.setValue(int(min_val))
                elif val > max_val:
                    spin.setValue(int(max_val))
            except Exception:
                continue

    def _set_phase(self, phase, reset_time=True, notify=False):
        self.phase = phase
        if phase != "focus":
            self._stop_background_audio()
        if reset_time:
            minutes = self._phase_minutes(phase)
            self.time_left = minutes * 60
            self.timer_label.setText(f"{minutes:02d}:00")
        if phase != "focus":
            self.session_started_at = None
            self.session_task_id = None
            self.session_task_title = None
            self.session_task_priority = None
            self.session_task_type = None
            self.session_duration_min = None
        self._update_phase_badge()
        self._update_next_label()
        self._update_start_button(resume=False)
        self._update_break_tips_visibility()
        self._update_session_progress()
        if notify:
            self._announce_phase_change(phase)

    def _start_timer(self):
        if self._should_disable_start():
            self._show_task_required()
            return
        self.timer.start(1000)
        self.btn_start.setText("Pause")
        self._set_start_button_icon(True)
        bad = (self._theme_colors or {}).get("bad", "#ef4444")
        self.btn_start.setStyleSheet(f"background-color: {bad}; color: white; border-radius: 12px; font-weight: bold; border: none;")
        if self.phase == "focus" and self.session_started_at is None:
            self.session_started_at = datetime.now()
            self.session_task_id = self.current_task_id
            self.session_task_title = self.current_task_title
            self.session_task_priority = self.current_task_priority
            self.session_task_type = self.current_task_type
            self.session_duration_min = self._phase_minutes(self.phase)
            self._start_session_record()
        if self.phase == "focus" and self._auto_play_bg_audio:
            try:
                self._start_background_audio()
            except Exception:
                pass
        self._update_stop_button_visibility()

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
        if not self.enable_notifications:
            return
        self._ensure_tray()
        if self._tray:
            self._tray.showMessage(title, message, QSystemTrayIcon.MessageIcon.Information, 3000)
        else:
            print(f"[Pomodoro] {title}: {message}")

    def _ensure_background_player(self):
        if QMediaPlayer is None or QAudioOutput is None:
            return False
        if self._bg_player is not None and self._bg_audio_output is not None:
            return True
        try:
            self._bg_audio_output = QAudioOutput(self)
            self._bg_audio_output.setVolume(0.35)
            self._bg_player = QMediaPlayer(self)
            self._bg_player.setAudioOutput(self._bg_audio_output)
            try:
                self._bg_player.setLoops(QMediaPlayer.Loops.Infinite)
            except Exception:
                pass
            return True
        except Exception:
            self._bg_player = None
            self._bg_audio_output = None
            return False

    def _start_background_audio(self):
        path = str(self._bg_audio_path or "").strip()
        if not path or not os.path.exists(path):
            return False
        if not self._ensure_background_player():
            return False
        try:
            self._bg_player.setSource(QUrl.fromLocalFile(path))
            self._bg_player.play()
            self._bg_audio_active = True
            self._sync_audio_buttons()
            return True
        except Exception:
            return False

    def _stop_background_audio(self):
        try:
            if self._bg_player is not None:
                self._bg_player.stop()
        except Exception:
            pass
        self._bg_audio_active = False
        try:
            self._sync_audio_buttons()
        except Exception:
            pass

    def stop_playlist(self):
        self._stop_background_audio()
        if hasattr(self, "btn_open_playlist"):
            self.btn_open_playlist.setText("Play Playlist")

    def _sync_audio_buttons(self):
        if hasattr(self, "btn_open_playlist"):
            try:
                enabled = bool(str(self._playlist_url or "").strip())
                self.btn_open_playlist.setEnabled(enabled)
                if self._bg_audio_active:
                    self.btn_open_playlist.setText("Playing Playlist")
                else:
                    self.btn_open_playlist.setText("Play Playlist")
            except Exception:
                pass
        if hasattr(self, "btn_stop_playlist"):
            try:
                self.btn_stop_playlist.setEnabled(bool(self._bg_audio_active))
            except Exception:
                pass
        if hasattr(self, "lbl_playlist_status"):
            try:
                status_text = "Playing" if self._bg_audio_active else "Stopped"
                self.lbl_playlist_status.setText(f"Playlist status: {status_text}")
            except Exception:
                pass
        if hasattr(self, "btn_toggle_bg_audio"):
            has_file = bool(str(self._bg_audio_path or "").strip()) and os.path.exists(str(self._bg_audio_path))
            ok_media = QMediaPlayer is not None and QAudioOutput is not None
            try:
                self.btn_toggle_bg_audio.setEnabled(bool(has_file and ok_media))
            except Exception:
                pass
            status = "On" if bool(self._bg_audio_active) else "Off"
            try:
                self.btn_toggle_bg_audio.setText(f"Background Audio: {status}")
            except Exception:
                pass

    def open_playlist(self):
        url = str(self._playlist_url or "").strip()
        if not url and self._bg_audio_path:
            url = str(self._bg_audio_path or "").strip()

        if not url:
            QMessageBox.information(self, "Playlist", "Set a Pomodoro playlist URL or select a background audio file in Settings.")
            return

        if not self._ensure_background_player():
            QMessageBox.information(self, "Playlist", "Background audio is not available on this system.")
            return

        source = self._resolve_playlist_source(url)
        if source is None:
            QMessageBox.information(
                self,
                "Playlist",
                "This playlist cannot be played directly. Use a local audio file, a direct audio stream, or install yt-dlp for YouTube support.",
            )
            return

        try:
            self._bg_player.setSource(source)
            self._bg_player.play()
            self._bg_audio_active = True
            self._sync_audio_buttons()
        except Exception:
            QMessageBox.information(
                self,
                "Playlist",
                "Could not start playback for this playlist URL.",
            )

    def _resolve_playlist_source(self, url: str) -> QUrl | None:
        candidate = str(url or "").strip()
        if not candidate:
            return None

        if os.path.exists(candidate):
            return QUrl.fromLocalFile(candidate)

        if not candidate.lower().startswith(("http://", "https://")):
            return None

        if "youtube.com" in candidate.lower() or "youtu.be" in candidate.lower():
            resolved = self._resolve_youtube_audio_url(candidate)
            if resolved:
                return QUrl(resolved)
            return None

        return QUrl(candidate)

    def _resolve_youtube_audio_url(self, url: str) -> str | None:
        try:
            import yt_dlp
        except Exception:
            yt_dlp = None

        def _choose_best_audio(info_dict: dict) -> str | None:
            formats = info_dict.get("formats") or []
            best = None
            for fmt in formats:
                if not fmt or fmt.get("acodec") == "none":
                    continue
                if fmt.get("url"):
                    best = fmt
            if best is not None:
                return str(best.get("url"))
            if info_dict.get("url"):
                return str(info_dict.get("url"))
            return None

        if yt_dlp is not None:
            try:
                ydl_opts = {
                    "format": "bestaudio/best",
                    "quiet": True,
                    "no_warnings": True,
                    "skip_download": True,
                }
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    info = ydl.extract_info(url, download=False)
                    if isinstance(info, dict):
                        if info.get("entries") and isinstance(info["entries"], list) and info["entries"]:
                            info = info["entries"][0] or info
                        resolved = _choose_best_audio(info)
                        if resolved:
                            return resolved
            except Exception:
                pass

        try:
            if shutil.which("yt-dlp"):
                result = subprocess.run(
                    ["yt-dlp", "-g", "-f", "bestaudio/best", url],
                    capture_output=True,
                    text=True,
                    timeout=30,
                )
                if result.returncode == 0:
                    output = result.stdout.strip().splitlines()
                    if output:
                        return output[0].strip()
        except Exception:
            pass

        return None

    def toggle_background_audio(self):
        if self._bg_audio_active:
            self._stop_background_audio()
            return
        started = self._start_background_audio()
        if not started:
            QMessageBox.information(self, "Background Audio", "Select a valid audio file in Settings first.")

    def _pomodoro_end_sound_path(self):
        root_dir = os.path.dirname(os.path.dirname(__file__))
        return os.path.join(root_dir, "resources", "mixkit-happy-bells-notification-937.wav")

    def _play_session_end_sound(self):
        if not self.sound_effects:
            return False
        sound_path = self._pomodoro_end_sound_path()
        if QSoundEffect is None:
            QApplication.beep()
            return True
        if not os.path.exists(sound_path):
            QApplication.beep()
            return True
        if self._end_sound is None:
            self._end_sound = QSoundEffect(self)
            self._end_sound.setLoopCount(1)
            self._end_sound.setVolume(0.85)
        self._end_sound.setSource(QUrl.fromLocalFile(sound_path))
        self._end_sound.play()
        return True

    def _play_sound(self):
        if not self.sound_effects:
            return
        QApplication.beep()

    def _announce_phase_change(self, phase):
        if self.last_notification_phase == phase:
            return
        self.last_notification_phase = phase
        label = self._phase_label(phase)
        mins = self._phase_minutes(phase)
        self._show_notification("Pomodoro", f"{label} started ({format_duration_minutes(mins)})")
        if self._suppress_next_phase_sound:
            self._suppress_next_phase_sound = False
            return
        self._play_sound()

    def _update_break_tips_visibility(self):
        if hasattr(self, "tips_frame"):
            self.tips_frame.setVisible(self.phase in ("short_break", "long_break"))

    def _style_break_tips(self):
        if not hasattr(self, "tips_frame"):
            return
        colors = self._theme_colors or {}
        if self._is_dark:
            bg = colors.get("card_alt", "#112844")
            border = colors.get("border", "#25456B")
            title = colors.get("text_sub", "#BAD2E0")
            btn_bg = colors.get("card", "#0F2238")
            btn_border = colors.get("border", "#25456B")
            btn_text = colors.get("text_main", "#E6EEF5")
            btn_hover = colors.get("accent_soft", "#1B2F4D")
            action_bg = colors.get("accent_soft", "#1B2F4D")
            action_border = colors.get("accent2", "#3078CD")
            action_text = colors.get("accent", "#82AFF2")
        else:
            bg = colors.get("accent_soft", "#E6F0FA")
            border = colors.get("border", "#BAD2E0")
            title = colors.get("text_sub", "#47617C")
            btn_bg = colors.get("card", "#FFFFFF")
            btn_border = colors.get("border", "#BAD2E0")
            btn_text = colors.get("deep", "#25456B")
            btn_hover = colors.get("border", "#BAD2E0")
            action_bg = colors.get("accent_soft", "#E6F0FA")
            action_border = colors.get("accent", "#3078CD")
            action_text = colors.get("accent", "#3078CD")

        self.tips_frame.setStyleSheet(
            f"QFrame#PomodoroTips {{ background-color: {bg}; border: 1px solid {border}; border-radius: 16px; }}"
        )
        self.tips_title.setStyleSheet(f"color: {title}; font-size: 10px; font-weight: bold;")

        tip_style = (
            f"QToolButton {{ background-color: {btn_bg}; color: {btn_text}; border: 1px solid {btn_border}; "
            f"border-radius: 10px; padding: 6px 8px; font-size: 11px; font-weight: bold; }} "
            f"QToolButton:hover {{ background-color: {btn_hover}; }}"
        )
        for b in (self.btn_hydrate, self.btn_stretch, self.btn_step):
            b.setStyleSheet(tip_style)
        icon_color = colors.get("accent", "#3078CD")
        self._set_tip_icons(icon_color)

        action_style = (
            f"QPushButton {{ background-color: {action_bg}; color: {action_text}; border: 1px solid {action_border}; "
            f"border-radius: 10px; padding: 6px 10px; font-size: 11px; font-weight: bold; }} "
            f"QPushButton:hover {{ background-color: {btn_hover}; }}"
        )
        self.btn_skip_break.setStyleSheet(action_style)
        self.btn_add_two.setStyleSheet(action_style)

    def _apply_responsive_sizes(self):
        if not hasattr(self, "timer_label") or not hasattr(self, "left_container"):
            return
        width = self.left_container.width()
        if width <= 0:
            return
        try:
            margins = self.left_vbox.contentsMargins()
            width -= (margins.left() + margins.right())
        except Exception:
            pass

        size = int(width * 0.6)
        size = max(220, min(420, size))
        if self.timer_label.width() != size:
            self.timer_label.setFixedSize(size, size)

        if hasattr(self, "badge"):
            badge_w = max(110, min(200, int(size * 0.45)))
            self.badge.setFixedSize(badge_w, 32)

        btn_h = max(42, min(58, int(size * 0.16)))
        start_w = max(140, min(220, int(size * 0.5)))
        other_w = max(90, min(150, int(size * 0.35)))
        if hasattr(self, "btn_start"):
            self.btn_start.setFixedSize(start_w, btn_h)
        if hasattr(self, "btn_stop"):
            self.btn_stop.setFixedSize(other_w, btn_h)
        if hasattr(self, "btn_reset"):
            self.btn_reset.setFixedSize(other_w, btn_h)

        colors = self._theme_colors or {}
        accent = colors.get("accent", "#3078CD")
        text_main = colors.get("text_main", "#113356")
        bg = colors.get("bg", "#F8F6F2")
        border = max(8, int(round(size * 0.0375)))
        radius = size // 2
        font_size = max(56, min(110, int(size * 0.26)))
        self.timer_label.setStyleSheet(
            f"font-size: {font_size}px; font-weight: 900; color: {text_main}; "
            f"background-color: {bg}; border: {border}px solid {accent}; border-radius: {radius}px;"
        )

    def _set_tip_icons(self, color):
        icon_color = QColor(color)
        self.btn_hydrate.setIcon(self._make_tip_icon("hydrate", icon_color))
        self.btn_stretch.setIcon(self._make_tip_icon("stretch", icon_color))
        self.btn_step.setIcon(self._make_tip_icon("step", icon_color))

    def _make_tip_icon(self, kind, color):
        size = 24
        pix = QPixmap(size, size)
        pix.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pix)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        pen = QPen(color)
        pen.setWidth(2)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)

        if kind == "hydrate":
            path = QPainterPath()
            path.moveTo(12, 2)
            path.cubicTo(17, 7, 20, 11, 20, 15)
            path.cubicTo(20, 19, 16.5, 22, 12, 22)
            path.cubicTo(7.5, 22, 4, 19, 4, 15)
            path.cubicTo(4, 11, 7, 7, 12, 2)
            painter.drawPath(path)
        elif kind == "stretch":
            painter.drawEllipse(9, 2, 6, 6)  # head
            painter.drawLine(12, 8, 12, 16)  # body
            painter.drawLine(6, 9, 12, 12)   # left arm
            painter.drawLine(18, 9, 12, 12)  # right arm
            painter.drawLine(12, 16, 8, 22)  # left leg
            painter.drawLine(12, 16, 16, 22) # right leg
        else:  # "step"
            painter.drawRect(3, 4, 8, 16)    # door
            painter.drawLine(11, 12, 20, 12) # arrow
            painter.drawLine(17, 9, 20, 12)
            painter.drawLine(17, 15, 20, 12)

        painter.end()
        return QIcon(pix)

    def skip_break(self):
        if self.phase not in ("short_break", "long_break"):
            return
        self.timer.stop()
        if self.plan_phases:
            next_idx = (self.plan_index + 1) % len(self.plan_phases)
            self.plan_index = next_idx
            while self.plan_phases[self.plan_index]["phase"] != "focus":
                self.plan_index = (self.plan_index + 1) % len(self.plan_phases)
                if self.plan_index == 0:
                    self.sessions_completed = 0
                    self.sessions_count.setText("0")
            self._set_phase(self.plan_phases[self.plan_index]["phase"], reset_time=True, notify=True)
        else:
            self._set_phase("focus", reset_time=True, notify=True)
        if self.auto_start:
            self._start_timer()

    def add_break_minutes(self, minutes=2):
        if self.phase not in ("short_break", "long_break"):
            return
        self.time_left += int(minutes) * 60
        mins, secs = divmod(self.time_left, 60)
        self.timer_label.setText(f"{mins:02d}:{secs:02d}")

    def _elapsed_minutes(self):
        total_minutes = self.session_duration_min or self.focus_input.value()
        total_seconds = (total_minutes * 60) - self.time_left
        if total_seconds < 0:
            total_seconds = 0
        return max(1, int(round(total_seconds / 60.0))) if total_seconds > 0 else 0

    def _recover_incomplete_sessions(self):
        """Finalize any in-progress sessions left by a forced app stop."""
        try:
            conn = get_db_connection()
        except Exception:
            return
        try:
            rows = conn.execute(
                "SELECT id, started_at FROM pomodoro_sessions "
                "WHERE status='in_progress' OR ended_at IS NULL OR ended_at=''"
            ).fetchall()
            if not rows:
                conn.close()
                return
            now = datetime.now()
            now_str = now.strftime("%Y-%m-%d %H:%M:%S")
            for row in rows:
                try:
                    sid = row[0]
                    started_at = row[1]
                    started_dt = None
                    if started_at:
                        try:
                            started_dt = datetime.strptime(str(started_at), "%Y-%m-%d %H:%M:%S")
                        except Exception:
                            started_dt = None
                    if started_dt is None:
                        started_dt = now
                    delta_sec = max(0, (now - started_dt).total_seconds())
                    duration_min = 0 if delta_sec <= 0 else max(1, int(round(delta_sec / 60.0)))
                    conn.execute(
                        "UPDATE pomodoro_sessions SET ended_at=?, duration_min=?, status=? WHERE id=?",
                        (now_str, int(duration_min), "stopped", sid),
                    )
                except Exception:
                    continue
            conn.commit()
        except Exception as exc:
            print("DB Error (Pomodoro recovery):", exc)
        finally:
            try:
                conn.close()
            except Exception:
                pass

    def _start_session_record(self):
        if self._active_session_id is not None:
            return
        if self.session_started_at is None:
            self.session_started_at = datetime.now()
        try:
            conn = get_db_connection()
            cur = conn.execute(
                "INSERT INTO pomodoro_sessions (task_id, task_title, task_priority, task_type, started_at, ended_at, duration_min, status) "
                "VALUES (?,?,?,?,?,?,?,?)",
                (
                    self.session_task_id,
                    self.session_task_title,
                    self.session_task_priority or self.current_task_priority,
                    self.session_task_type or self.current_task_type,
                    self.session_started_at.strftime("%Y-%m-%d %H:%M:%S"),
                    None,
                    0,
                    "in_progress",
                ),
            )
            try:
                self._active_session_id = cur.lastrowid
            except Exception:
                self._active_session_id = None
            conn.commit()
            conn.close()
        except Exception as exc:
            print("DB Error (Pomodoro start):", exc)
            self._active_session_id = None

    def _log_session(self, status="completed", duration_override=None):
        if self.session_started_at is None:
            self.session_started_at = datetime.now()
        ended_at = datetime.now()
        if duration_override is not None:
            duration_min = int(duration_override)
        else:
            duration_min = self._elapsed_minutes()
        try:
            conn = get_db_connection()
            if self._active_session_id is not None:
                conn.execute(
                    "UPDATE pomodoro_sessions SET ended_at=?, duration_min=?, status=? WHERE id=?",
                    (
                        ended_at.strftime("%Y-%m-%d %H:%M:%S"),
                        int(duration_min),
                        status,
                        self._active_session_id,
                    ),
                )
            else:
                conn.execute(
                    "INSERT INTO pomodoro_sessions (task_id, task_title, task_priority, task_type, started_at, ended_at, duration_min, status) VALUES (?,?,?,?,?,?,?,?)",
                    (
                        self.session_task_id,
                        self.session_task_title,
                        self.session_task_priority or self.current_task_priority,
                        self.session_task_type or self.current_task_type,
                        self.session_started_at.strftime("%Y-%m-%d %H:%M:%S"),
                        ended_at.strftime("%Y-%m-%d %H:%M:%S"),
                        int(duration_min),
                        status,
                    ),
                )
            conn.commit()
            conn.close()
        except Exception as exc:
            print("DB Error (Pomodoro):", exc)
        finally:
            self.session_started_at = None
            self.session_task_id = None
            self.session_task_title = None
            self.session_task_priority = None
            self.session_duration_min = None
            self._active_session_id = None
        
