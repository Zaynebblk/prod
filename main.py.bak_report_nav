import sys
import os
import json
import ctypes
import traceback
import signal
import threading
import time
import re
from PyQt6.QtWidgets import (QApplication, QMainWindow, QWidget, QHBoxLayout, 
                             QVBoxLayout, QPushButton, QStackedWidget, QFrame, QLabel, QSizePolicy, QMessageBox)
from PyQt6.QtCore import Qt, QObject, QEvent, qInstallMessageHandler, QTimer, pyqtSignal
from PyQt6.QtGui import QIcon, QPixmap, QFontMetrics
from resources.theme import get_theme, FONT_FAMILY, rgba
from resources.api_client import clear_token
from resources.api_client import check_server_http
from resources.api_client import api_shutdown_local_server
from resources.server_manager import stop_managed_server

SERVER_CHECK_TIMEOUT = 1.0

_LAST_QSS_INFO = None

def _app_logo_path():
    root_dir = os.path.dirname(os.path.abspath(__file__))
    png_path = os.path.join(root_dir, "ProdSmart.png")
    ico_path = os.path.join(root_dir, "ProdSmart.ico")
    # Prefer PNG for in-app logo rendering.
    if os.path.exists(png_path):
        return png_path
    if os.path.exists(ico_path):
        return ico_path
    return None


def _app_icon_path():
    root_dir = os.path.dirname(os.path.abspath(__file__))
    png_path = os.path.join(root_dir, "ProdSmart.png")
    ico_path = os.path.join(root_dir, "ProdSmart.ico")
    # Prefer ICO on Windows so the taskbar icon reliably shows.
    if os.name == "nt" and os.path.exists(ico_path):
        return ico_path
    if os.path.exists(png_path):
        return png_path
    if os.path.exists(ico_path):
        return ico_path
    return None


def _load_app_icon():
    root_dir = os.path.dirname(os.path.abspath(__file__))
    ico_path = os.path.join(root_dir, "ProdSmart.ico")
    png_path = os.path.join(root_dir, "ProdSmart.png")
    icon = QIcon()
    if os.path.exists(ico_path):
        icon.addFile(ico_path)
    # Ensure a PNG-backed pixmap is available as a fallback.
    if os.path.exists(png_path):
        icon.addFile(png_path)
        if icon.isNull():
            pix = QPixmap(png_path)
            if not pix.isNull():
                icon = QIcon(pix)
    return icon


def _set_windows_app_id():
    if os.name != "nt":
        return
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("prodsmart.app.1.0")
    except Exception:
        pass


def _force_windows_app_icon(hwnd, icon_path):
    if os.name != "nt" or not icon_path:
        return None
    try:
        IMAGE_ICON = 1
        LR_LOADFROMFILE = 0x00000010
        WM_SETICON = 0x0080
        ICON_SMALL = 0
        ICON_BIG = 1
        hicon = ctypes.windll.user32.LoadImageW(
            None, icon_path, IMAGE_ICON, 0, 0, LR_LOADFROMFILE
        )
        if hicon:
            ctypes.windll.user32.SendMessageW(hwnd, WM_SETICON, ICON_SMALL, hicon)
            ctypes.windll.user32.SendMessageW(hwnd, WM_SETICON, ICON_BIG, hicon)
            try:
                GCLP_HICON = -14
                GCLP_HICONSM = -34
                set_class = getattr(ctypes.windll.user32, "SetClassLongPtrW", None)
                if set_class is None:
                    set_class = ctypes.windll.user32.SetClassLongW
                set_class(hwnd, GCLP_HICON, hicon)
                set_class(hwnd, GCLP_HICONSM, hicon)
            except Exception:
                pass
            return hicon
    except Exception:
        pass
    return None

def _install_qss_sanitizer():
    try:
        from PyQt6.QtWidgets import QWidget
    except Exception:
        return

    original_set = QWidget.setStyleSheet

    def _sanitize(style):
        if not isinstance(style, str):
            return style

        # Remove unsupported properties if any slipped in.
        s = re.sub(r"text-transform\\s*:\\s*uppercase\\s*;?", "", style)

        def _fix_block(match):
            block = match.group(0)
            block = re.sub(r"font-weight\\s*:\\s*(900|800|700)\\s*;?", "font-weight: bold;", block)
            return block

        # Fix any QPushButton-related blocks (including subselectors)
        s = re.sub(r"QPushButton[^\\{]*\\{[^\\}]*\\}", _fix_block, s, flags=re.DOTALL)
        s = re.sub(r"QMessageBox\\s+QPushButton[^\\{]*\\{[^\\}]*\\}", _fix_block, s, flags=re.DOTALL)
        return s

    def _wrapped_set(self, style):
        global _LAST_QSS_INFO
        try:
            _LAST_QSS_INFO = (self.__class__.__name__, self.objectName(), style)
        except Exception:
            _LAST_QSS_INFO = None
        try:
            return original_set(self, _sanitize(style))
        except Exception:
            return original_set(self, style)

    QWidget.setStyleSheet = _wrapped_set

def _install_qss_debug():
    if os.getenv("PRODSMART_DEBUG_QSS") != "1":
        return
    try:
        import PyQt6.sip as sip
    except Exception:
        try:
            import sip  # type: ignore
        except Exception:
            sip = None
    print("[QSS Debug] enabled")

    qss_cache = {}
    try:
        from PyQt6.QtWidgets import QWidget as _QWidget, QPushButton as _QPushButton
    except Exception:
        _QWidget = None
        _QPushButton = None

    if sip and _QWidget and _QPushButton:
        original_widget_set_style = _QWidget.setStyleSheet
        original_btn_set_style = _QPushButton.setStyleSheet

        def _cache_style(obj, style):
            try:
                ptr = sip.unwrapinstance(obj)
                qss_cache[int(ptr)] = style
            except Exception:
                pass

        def _wrapped_widget_set_style(self, style):
            if isinstance(self, _QPushButton):
                _cache_style(self, style)
            return original_widget_set_style(self, style)

        def _wrapped_btn_set_style(self, style):
            _cache_style(self, style)
            return original_btn_set_style(self, style)

        _QWidget.setStyleSheet = _wrapped_widget_set_style
        _QPushButton.setStyleSheet = _wrapped_btn_set_style

    def _handler(msg_type, context, message):
        print(message)
        if "Could not parse stylesheet of object QPushButton" in message and sip:
            match = re.search(r"QPushButton\((0x[0-9A-Fa-f]+)\)", message)
            if match:
                ptr_val = None
                try:
                    ptr_val = int(match.group(1), 16)
                except Exception:
                    ptr_val = None

                obj = None
                if ptr_val is not None:
                    try:
                        obj = sip.wrapinstance(ptr_val, QObject)
                    except Exception:
                        obj = None

                if obj is not None:
                    try:
                        name = obj.objectName()
                    except Exception:
                        name = ""
                    print(f"[QSS Debug] objectName='{name}' class={obj.__class__.__name__}")

                if ptr_val is not None and ptr_val in qss_cache:
                    print(f"[QSS Debug] styleSheet={qss_cache[ptr_val]}")
                else:
                    print("[QSS Debug] styleSheet not cached for this pointer.")

                if _LAST_QSS_INFO:
                    cls_name, obj_name, style = _LAST_QSS_INFO
                    print(f"[QSS Debug] last_set_style widget={cls_name} objectName='{obj_name}'")
                    print(f"[QSS Debug] last_set_style_sheet={style}")
        elif "Could not parse stylesheet of object QPushButton" in message and sip is None:
            print("[QSS Debug] sip module not available; cannot resolve QPushButton pointer.")

    qInstallMessageHandler(_handler)

# --- IMPORTS ---
try:
    from database.db_manager import init_db
    from pages.dashboard_page import DashboardPage
    from pages.tasks_page import TasksPage
    from pages.matrix_page import EisenhowerMatrix
    from pages.pomodoro_page import PomodoroPage
    from pages.history_page import HistoryPage
    from pages.report_page import SessionReportPage
    from pages.settings_page import SettingsPage
    from pages.quick_stats_page import QuickStatsPage
    from pages.login_page import LoginPage
    from pages.team_page import TeamPage
    from pages.user_page import UserProfilePage
    from pages.search_page import SearchPage
except ImportError as e:
    print(f"Import Error: {e}")
    sys.exit(1)

class MainApp(QMainWindow):
    server_health_checked = pyqtSignal(bool)
    def __init__(self):
        super().__init__()
        self.setWindowTitle("ProdSmart")
        self.resize(1200, 800)
        self.setMinimumSize(640, 420)
        try:
            self.setWindowFlag(Qt.WindowType.WindowSystemMenuHint, True)
            self.setWindowFlag(Qt.WindowType.WindowTitleHint, True)
            self.setWindowFlag(Qt.WindowType.WindowMinimizeButtonHint, True)
            self.setWindowFlag(Qt.WindowType.WindowMaximizeButtonHint, True)
            self.setWindowFlag(Qt.WindowType.WindowCloseButtonHint, True)
        except Exception:
            pass

        # --- SET WINDOW & TASKBAR ICON ---
        self._app_icon = _load_app_icon()
        if not self._app_icon.isNull():
            self.setWindowIcon(self._app_icon)
        self._win_hicon = None

        # ID for Windows Taskbar (also set before QApplication as a fallback)
        _set_windows_app_id()

        self.central_widget = QWidget()
        self.central_widget.setMinimumSize(0, 0)
        self.central_widget.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.main_layout = QHBoxLayout(self.central_widget)
        self.main_layout.setContentsMargins(0, 0, 0, 0)
        self.main_layout.setSpacing(0)

        # 1. SIDEBAR
        self.sidebar = QFrame()
        self.sidebar.setMinimumWidth(190)
        self.sidebar.setMaximumWidth(280)
        self.sidebar.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Expanding)
        self.sidebar.setObjectName("Sidebar")
        sidebar_l = QVBoxLayout(self.sidebar)
        sidebar_l.setContentsMargins(12, 10, 12, 10)
        sidebar_l.setSpacing(8)
        
        # --- SIDEBAR HEADER (Logo + Title) ---
        header_widget = QWidget()
        header_widget.setObjectName("SidebarHeader")
        header_layout = QVBoxLayout(header_widget)
        header_layout.setContentsMargins(14, 16, 14, 16)
        header_layout.setSpacing(10)
        header_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        header_widget.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Minimum)
        self.sidebar_header = header_widget
        self.sidebar_header_layout = header_layout

        # Logo
        logo_icon = QLabel()
        logo_icon.setObjectName("SidebarLogo")
        self._logo_pixmap = None
        logo_path = _app_logo_path()
        if logo_path:
            pixmap = QPixmap(logo_path)
            if not pixmap.isNull():
                self._logo_pixmap = pixmap
        logo_icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        logo_icon.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.logo_icon = logo_icon

        # Title
        logo_text = QLabel("ProdSmart")
        logo_text.setObjectName("SidebarTitle")
        logo_text.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.logo_text = logo_text

        header_layout.addWidget(logo_icon, alignment=Qt.AlignmentFlag.AlignCenter)
        header_layout.addWidget(logo_text, alignment=Qt.AlignmentFlag.AlignCenter)

        sidebar_l.addWidget(header_widget)

        sep_top = QFrame()
        sep_top.setObjectName("SidebarSeparator")
        sep_top.setFixedHeight(1)
        sep_top.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        sidebar_l.addWidget(sep_top)
        # -----------------------------------------
        self.btn_tasks = QPushButton("My Tasks")
        self.btn_dashboard = QPushButton("Dashboard")
        self.btn_search = QPushButton("Search")
        
        self.btn_matrix = QPushButton("Matrix")
        self.btn_pomodoro = QPushButton("Pomodoro")
        self.btn_teams = QPushButton("Teams")
        self.btn_history = QPushButton("History")
        self.btn_profile = QPushButton("My Profile")
        self.btn_settings = QPushButton("Settings")
        self.btn_sign_out = QPushButton("Sign Out")

        self.nav_buttons = [
            self.btn_tasks,
            self.btn_dashboard,
            self.btn_search,
            self.btn_matrix,
            self.btn_pomodoro,
            self.btn_teams,
            self.btn_history,
            self.btn_profile,
            self.btn_settings,
        ]
        
        for btn in self.nav_buttons:
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setProperty("nav", True)

        self.btn_sign_out.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_sign_out.setProperty("nav", True)

        for btn in [ self.btn_tasks, self.btn_dashboard, self.btn_search, self.btn_matrix, self.btn_pomodoro, self.btn_teams, self.btn_history, self.btn_profile]:
            sidebar_l.addWidget(btn)

        sep_bottom = QFrame()
        sep_bottom.setObjectName("SidebarSeparator")
        sep_bottom.setFixedHeight(1)
        sep_bottom.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        sidebar_l.addWidget(sep_bottom)

        sidebar_l.addWidget(self.btn_settings)
        sidebar_l.addWidget(self.btn_sign_out)

        sidebar_l.addStretch()
        self.main_layout.addWidget(self.sidebar)

        # Initially hide sidebar until user logs in
        self.sidebar.hide()

        # 2. CONTENT (STACK)
        self.content_stack = QStackedWidget()
        self.content_stack.setMinimumSize(0, 0)
        self.content_stack.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        
        # Initialize pages
        self.page_login = LoginPage()
        self.page_tasks = TasksPage()
        self.page_dashboard = DashboardPage()
        self.page_matrix = EisenhowerMatrix()
        self.page_pomodoro = PomodoroPage()
        self.page_history = HistoryPage()
        self.page_report = SessionReportPage()
        self.page_quick_stats = QuickStatsPage()
        self.page_settings = SettingsPage()
        self.page_team = TeamPage()
        self.page_profile = UserProfilePage()
        self.page_search = SearchPage()
        
        self.content_stack.addWidget(self.page_login)      # Index 0
        self.content_stack.addWidget(self.page_tasks)      # Index 1
        self.content_stack.addWidget(self.page_dashboard)  # Index 2
        self.content_stack.addWidget(self.page_matrix)     # Index 3
        self.content_stack.addWidget(self.page_pomodoro)   # Index 4
        self.content_stack.addWidget(self.page_history)    # Index 5
        self.content_stack.addWidget(self.page_settings)   # Index 6
        self.content_stack.addWidget(self.page_report)     # Index 7
        self.content_stack.addWidget(self.page_quick_stats) # Index 8
        self.content_stack.addWidget(self.page_team)       # Index 9
        self.content_stack.addWidget(self.page_profile)    # Index 10
        self.content_stack.addWidget(self.page_search)     # Index 11

        self.main_layout.addWidget(self.content_stack, stretch=1)
        self.setCentralWidget(self.central_widget)

        # Suppress tiny transient windows that can flash during page switches.
        self._transient_blocker = _TransientWindowBlocker(self)
        app = QApplication.instance()
        if app:
            app.installEventFilter(self._transient_blocker)

        # Server health watcher
        self._server_watch_timer = QTimer(self)
        self._server_watch_timer.setInterval(200)
        self._server_watch_timer.timeout.connect(self._check_server_health)
        self._server_down_alerted = False
        self._server_is_running = None
        self._server_check_inflight = False
        self._server_check_started_at = 0.0
        self._page_switch_guard = False
        self.server_health_checked.connect(self._handle_server_health_result)
        # Keep the global server watcher active even on the login page
        self._server_watch_timer.start()
        self._check_server_health()

        # 3. BUTTON CONNECTIONS
        self.btn_tasks.clicked.connect(lambda: self.content_stack.setCurrentIndex(1))
        self.btn_dashboard.clicked.connect(lambda: self.content_stack.setCurrentIndex(2))
        self.btn_search.clicked.connect(lambda: self.content_stack.setCurrentIndex(11))
        
        self.btn_matrix.clicked.connect(lambda: self.content_stack.setCurrentIndex(3))
        self.btn_pomodoro.clicked.connect(lambda: self.content_stack.setCurrentIndex(4))
        self.btn_teams.clicked.connect(self._open_teams_guarded)
        self.btn_history.clicked.connect(lambda: self.content_stack.setCurrentIndex(5))
        self.btn_profile.clicked.connect(lambda: self.content_stack.setCurrentIndex(10))
        self.btn_settings.clicked.connect(lambda: self.content_stack.setCurrentIndex(6))
        self.btn_sign_out.clicked.connect(self.sign_out)

        # Monitor Page Changes to Auto-Refresh Data
        self.content_stack.currentChanged.connect(self.on_page_changed)

        # Global search -> open actions
        try:
            self.page_search.open_local_task.connect(self._open_local_task_from_search)
            self.page_search.open_team_task.connect(self._open_team_task_from_search)
        except Exception:
            pass

        # --- 4. SIGNALS CONNECTIONS ---
        
        # Connection 0: Login -> Main App
        self.page_login.login_successful.connect(self.on_login_successful)
        
        # Connection 1: Settings -> Apply Theme
        self.page_settings.settings_saved.connect(self.apply_settings)

        # Connection 1b: Profile -> History (View All)
        if hasattr(self, "page_profile") and hasattr(self.page_profile, "request_history"):
            try:
                self.page_profile.request_history.connect(lambda: self.content_stack.setCurrentIndex(5))
            except Exception:
                pass

        # Connection 2: Task Added -> Matrix Refresh
        if hasattr(self.page_tasks, 'task_added'):
            self.page_tasks.task_added.connect(self.page_matrix.refresh_matrix)

        # Connection 3: Task -> Pomodoro
        if hasattr(self.page_tasks, 'pomodoro_requested'):
            self.page_tasks.pomodoro_requested.connect(self.open_pomodoro_for_task)

        # Connection 4: Pomodoro -> Tasks (Select Task shortcut)
        if hasattr(self.page_pomodoro, 'select_task_requested'):
            self.page_pomodoro.select_task_requested.connect(self.open_tasks_from_pomodoro)

        # Connection 5: History -> Dashboard (Full Analytics Breakdown)
        if hasattr(self.page_history, 'request_dashboard'):
            self.page_history.request_dashboard.connect(self.open_dashboard_from_history)

        # Connection 5b: Dashboard -> Action
        if hasattr(self.page_dashboard, 'action_requested'):
            self.page_dashboard.action_requested.connect(self.handle_dashboard_action)

        # Connection 3: History Restore -> Tasks Refresh
        # (This relies on the signal we added to history_page.py)
        if hasattr(self.page_history, 'task_restored'):
            self.page_history.task_restored.connect(self.page_tasks.refresh_tasks)

        # Connection 6: History -> Report
        if hasattr(self.page_history, 'request_report'):
            self.page_history.request_report.connect(self.open_report_from_history)

        # Connection 7: Report -> History
        if hasattr(self.page_report, 'request_history'):
            self.page_report.request_history.connect(self.open_history_from_report)

        # Connection 8: History -> Quick Stats
        if hasattr(self.page_history, 'request_quick_stats'):
            self.page_history.request_quick_stats.connect(self.open_quick_stats_from_history)

        # Connection 9: Quick Stats -> History
        if hasattr(self.page_quick_stats, 'request_history'):
            self.page_quick_stats.request_history.connect(self.open_history_from_quick_stats)

        # Connection 10: Team Tasks -> Team Pomodoro
        if hasattr(self.page_team, 'team_pomodoro_requested'):
            self.page_team.team_pomodoro_requested.connect(self.open_pomodoro_for_team)
        if hasattr(self.page_team, 'team_task_pomodoro_requested'):
            self.page_team.team_task_pomodoro_requested.connect(self.open_pomodoro_for_team_task)

        # 5. INITIAL LOAD
        self.apply_settings()
        self.content_stack.setCurrentIndex(0)  # Start with login page
        # Don't refresh tasks initially since user isn't logged in yet

    def on_login_successful(self, user_id):
        """Handle successful login - switch to main app"""
        # Store user_id for the session
        self.current_user_id = user_id
        self._server_is_running = True
        self._server_down_alerted = False
        
        # Set user info on profile page
        try:
            username = self.page_login.username_input.text().strip() or "User"
            self.page_profile.set_user_info(username, user_id)
        except Exception:
            pass
        
        # Show sidebar
        self.sidebar.show()
        # Switch to tasks page (index 1)
        self.content_stack.setCurrentIndex(1)
        self._set_active_nav(self.btn_tasks)
        self._start_server_watch()

    def sign_out(self):
        try:
            clear_token()
        except Exception:
            pass
        try:
            self.current_user_id = None
        except Exception:
            pass
        try:
            if hasattr(self, "page_team") and hasattr(self.page_team, "pause_network"):
                self.page_team.pause_network()
        except Exception:
            pass
        self.sidebar.hide()
        if hasattr(self, "page_login") and hasattr(self.page_login, "show_login_page"):
            try:
                self.page_login.show_login_page()
            except Exception:
                pass
        if hasattr(self, "page_login") and hasattr(self.page_login, "start_server_status_timer"):
            try:
                self.page_login.start_server_status_timer()
            except Exception:
                pass
        if hasattr(self, "page_login") and hasattr(self.page_login, "force_server_status_refresh"):
            try:
                self.page_login.force_server_status_refresh()
            except Exception:
                pass
        self.content_stack.setCurrentIndex(0)

    def _start_server_watch(self):
        if hasattr(self, "_server_watch_timer") and not self._server_watch_timer.isActive():
            self._server_watch_timer.start()
        self._check_server_health()

    def _check_server_health(self):
        if getattr(self, "_server_check_inflight", False):
            started_at = getattr(self, "_server_check_started_at", 0.0)
            if started_at and (time.monotonic() - started_at) < 2.0:
                return
            self._server_check_inflight = False

        server_url = ""
        try:
            if hasattr(self, "page_login") and hasattr(self.page_login, "server_input"):
                server_url = self.page_login.server_input.text().strip()
        except Exception:
            server_url = ""

        self._server_check_inflight = True
        self._server_check_started_at = time.monotonic()

        def _worker(url):
            try:
                is_up = check_server_http(url or None, timeout=SERVER_CHECK_TIMEOUT)
            except Exception:
                is_up = False
            self.server_health_checked.emit(is_up)

        threading.Thread(target=_worker, args=(server_url,), daemon=True).start()

    def _handle_server_health_result(self, is_up):
        self._server_check_inflight = False
        self._server_check_started_at = 0.0
        self._server_is_running = bool(is_up)
        # Keep login page status label in sync even while logged in.
        try:
            if hasattr(self, "page_login"):
                if hasattr(self.page_login, "set_server_state"):
                    self.page_login.set_server_state(is_up)
                elif hasattr(self.page_login, "server_status"):
                    self.page_login.server_status.setText("Server: running" if is_up else "Server: not running")
        except Exception:
            pass
        try:
            if hasattr(self, "btn_teams"):
                self.btn_teams.setEnabled(bool(is_up))
        except Exception:
            pass
        if not is_up:
            try:
                if hasattr(self, "page_team") and hasattr(self.page_team, "pause_network"):
                    self.page_team.pause_network()
            except Exception:
                pass
        if is_up:
            self._server_down_alerted = False
            return
        self._warn_server_down()
        self._redirect_from_team_if_needed()

    def _warn_server_down(self):
        if hasattr(self, "sidebar") and not self.sidebar.isVisible():
            return
        if getattr(self, "_server_down_alerted", False):
            return
        self._server_down_alerted = True
        QMessageBox.warning(self, "Server", "Server stopped or it's not running.")

    def _redirect_from_team_if_needed(self):
        if not hasattr(self, "content_stack"):
            return
        if self.content_stack.currentIndex() != 9:
            return
        if getattr(self, "_page_switch_guard", False):
            return
        self._page_switch_guard = True
        try:
            target = 1 if self.sidebar.isVisible() else 0
            self.content_stack.setCurrentIndex(target)
            if target == 1:
                self._set_active_nav(self.btn_tasks)
            else:
                self._set_active_nav(None)
        finally:
            self._page_switch_guard = False

    def _open_teams_guarded(self):
        if not bool(getattr(self, "_server_is_running", False)):
            self._check_server_health()
            self._warn_server_down()
            return
        self.content_stack.setCurrentIndex(9)

    def _open_local_task_from_search(self, task_id: int):
        try:
            task_id = int(task_id)
        except Exception:
            return
        self.content_stack.setCurrentIndex(1)
        try:
            self._set_active_nav(self.btn_tasks)
        except Exception:
            pass
        try:
            QTimer.singleShot(0, lambda: self.page_tasks.show_task_details(task_id))
        except Exception:
            pass

    def _open_team_task_from_search(self, team_id: int, task_id: int):
        if not bool(getattr(self, "_server_is_running", False)):
            self._check_server_health()
            self._warn_server_down()
            return
        try:
            team_id = int(team_id)
            task_id = int(task_id)
        except Exception:
            return
        self.content_stack.setCurrentIndex(9)
        try:
            self._set_active_nav(self.btn_teams)
        except Exception:
            pass
        try:
            if hasattr(self.page_team, "refresh_teams"):
                self.page_team.refresh_teams()
            combo = getattr(self.page_team, "team_combo", None)
            if combo is not None:
                idx = combo.findData(team_id)
                if idx >= 0:
                    combo.setCurrentIndex(idx)
            QTimer.singleShot(900, lambda: self.page_team.show_task_details(task_id))
        except Exception:
            pass

    def on_page_changed(self, index):
        """Auto-refresh data when clicking on a tab"""
        if index == 1:  # Tasks page
            if hasattr(self, "_transient_blocker"):
                self._transient_blocker.suppress_for(1200)
            self.page_tasks.refresh_tasks()
            self._set_active_nav(self.btn_tasks)
        elif index == 2:  # Dashboard
            if hasattr(self.page_dashboard, "refresh_dashboard"):
                self.page_dashboard.refresh_dashboard()
            elif hasattr(self.page_dashboard, "_load_metrics_from_db"):
                self.page_dashboard._load_metrics_from_db()
            self._set_active_nav(self.btn_dashboard)
        elif index == 3:  # Matrix
            self.page_matrix.refresh_matrix()
            self._set_active_nav(self.btn_matrix)
        elif index == 4:  # Pomodoro
            self._set_active_nav(self.btn_pomodoro)
        elif index == 5:  # History
            # --- FIX: Removed 'theme_mode' argument here ---
            self.page_history.refresh_history()
            self._set_active_nav(self.btn_history)
        elif index == 6:  # Settings
            self._set_active_nav(self.btn_settings)
        elif index == 7:  # Report
            self._set_active_nav(self.btn_history)
        elif index == 8:  # Quick Stats
            self._set_active_nav(self.btn_history)
        elif index == 9:  # Teams
            if not bool(getattr(self, "_server_is_running", False)):
                self._warn_server_down()
                self._redirect_from_team_if_needed()
                return
            if hasattr(self.page_team, "refresh_teams"):
                self.page_team.refresh_teams()
            self._set_active_nav(self.btn_teams)
        elif index == 10:  # Profile
            try:
                if hasattr(self, "page_profile") and hasattr(self.page_profile, "load_profile"):
                    self.page_profile.load_profile()
            except Exception:
                pass
            self._set_active_nav(self.btn_profile)
        elif index == 11:  # Search
            self._set_active_nav(self.btn_search)

    def open_pomodoro_for_task(self, t_id, title, priority=None, task_type=None):
        if hasattr(self, "page_pomodoro") and hasattr(self.page_pomodoro, "set_task"):
            self.page_pomodoro.set_task(t_id, title, priority, task_type)
        self.content_stack.setCurrentIndex(4)
        self._set_active_nav(self.btn_pomodoro)

    def open_tasks_from_pomodoro(self):
        box = QMessageBox(self)
        box.setWindowTitle("Select Task Source")
        box.setText("Choose which task list you want to pick from.")
        solo_btn = box.addButton("Solo Tasks", QMessageBox.ButtonRole.AcceptRole)
        team_btn = box.addButton("Team Tasks", QMessageBox.ButtonRole.AcceptRole)
        cancel_btn = box.addButton("Cancel", QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(solo_btn)
        box.exec()

        clicked = box.clickedButton()
        if clicked == team_btn:
            self._open_teams_guarded()
            return
        if clicked == solo_btn:
            self.content_stack.setCurrentIndex(1)
            self._set_active_nav(self.btn_tasks)
            return
        # Cancel: do nothing

    def open_dashboard_from_history(self):
        self.content_stack.setCurrentIndex(1)
        self._set_active_nav(self.btn_dashboard)

    def handle_dashboard_action(self, action_text):
        action = (action_text or "").strip().lower()
        if action in ("start a pomodoro", "plan recovery"):
            self.content_stack.setCurrentIndex(4)
            self._set_active_nav(self.btn_pomodoro)
            if action == "plan recovery":
                if hasattr(self.page_pomodoro, "prepare_recovery_break"):
                    try:
                        self.page_pomodoro.prepare_recovery_break(prefer_long=True)
                    except Exception:
                        pass
        elif action in ("schedule deep work", "block high priority"):
            self.content_stack.setCurrentIndex(0)
            self._set_active_nav(self.btn_tasks)
            if hasattr(self.page_tasks, "refresh_tasks"):
                try:
                    self.page_tasks.refresh_tasks()
                except Exception:
                    pass
        else:
            self.content_stack.setCurrentIndex(0)
            self._set_active_nav(self.btn_tasks)

    def open_report_from_history(self, activity_id):
        if hasattr(self, "page_report"):
            self.page_report.load_report(activity_id)
        self.content_stack.setCurrentIndex(6)
        self._set_active_nav(self.btn_history)

    def open_history_from_report(self):
        self.content_stack.setCurrentIndex(4)
        self._set_active_nav(self.btn_history)

    def open_quick_stats_from_history(self, activity_id):
        if hasattr(self, "page_quick_stats"):
            self.page_quick_stats.load_activity(activity_id)
        self.content_stack.setCurrentIndex(7)
        self._set_active_nav(self.btn_history)

    def open_history_from_quick_stats(self):
        self.content_stack.setCurrentIndex(4)
        self._set_active_nav(self.btn_history)

    def open_pomodoro_for_team(self, team_id, start_now=False):
        self.content_stack.setCurrentIndex(4)
        self._set_active_nav(self.btn_pomodoro)
        if hasattr(self, "page_pomodoro") and hasattr(self.page_pomodoro, "activate_team_mode"):
            try:
                self.page_pomodoro.activate_team_mode(team_id, start_now)
            except Exception:
                pass

    def open_pomodoro_for_team_task(self, session_key, title, priority=None, task_type=None):
        if hasattr(self, "page_pomodoro") and hasattr(self.page_pomodoro, "set_task"):
            self.page_pomodoro.set_task(session_key, title, priority, task_type)
        self.content_stack.setCurrentIndex(4)
        self._set_active_nav(self.btn_pomodoro)

    def _set_active_nav(self, active_btn):
        if not hasattr(self, "nav_buttons"):
            return
        for btn in self.nav_buttons:
            is_active = btn is active_btn
            if btn.property("active") != is_active:
                btn.setProperty("active", is_active)
                btn.style().unpolish(btn)
                btn.style().polish(btn)
                btn.update()

    def get_settings_path(self):
        root_dir = os.path.dirname(os.path.abspath(__file__))
        return os.path.join(root_dir, "settings.json")

    def apply_settings(self):
        """Reads JSON and applies theme to ALL pages."""
        theme = "Light"
        path = self.get_settings_path()
        
        if os.path.exists(path):
            try:
                with open(path, "r") as f:
                    data = json.load(f)
                    theme = data.get("theme", "Light")
            except Exception as e:
                print(f"Error reading settings: {e}")
        
        print(f"Applying Theme: {theme}")

        # 1. Apply to Main Window
        if theme == "Dark":
            self.set_dark_theme()
        else:
            self.set_light_theme()

        # 2. Apply to Specific Pages
        if hasattr(self, 'page_matrix'):
            self.page_matrix.update_theme(theme)
            
        if hasattr(self, 'page_tasks'):
            self.page_tasks.update_theme(theme)
            # Ensure task list reflects any settings changes (e.g., show completed)
            try:
                self.page_tasks.refresh_tasks()
            except Exception:
                pass

        # We keep this for compatibility, even if it doesn't change history looks
        if hasattr(self, 'page_history'):
            self.page_history.update_theme(theme)

        if hasattr(self, 'page_dashboard'):
            self.page_dashboard.update_theme(theme)

        if hasattr(self, 'page_report'):
            self.page_report.update_theme(theme)

        if hasattr(self, 'page_quick_stats'):
            self.page_quick_stats.update_theme(theme)

        if hasattr(self, "page_profile"):
            try:
                if hasattr(self.page_profile, "update_theme"):
                    self.page_profile.update_theme(theme)
            except Exception:
                pass
        if hasattr(self, 'page_team'):
            try:
                if hasattr(self.page_team, 'update_theme'):
                    self.page_team.update_theme(theme)
            except Exception:
                pass
        if hasattr(self, "page_search"):
            try:
                if hasattr(self.page_search, "update_theme"):
                    self.page_search.update_theme(theme)
            except Exception:
                pass
        if hasattr(self, 'page_settings'):
            try:
                if hasattr(self.page_settings, 'update_theme'):
                    self.page_settings.update_theme(theme)
            except Exception:
                pass
        # Apply theme to login page
        if hasattr(self, 'page_login'):
            try:
                if hasattr(self.page_login, 'update_theme'):
                    self.page_login.update_theme(theme)
            except Exception:
                pass
        # Pomodoro page has its own `apply_theme` which reloads settings like auto-start
        if hasattr(self, 'page_pomodoro'):
            try:
                if hasattr(self.page_pomodoro, 'apply_theme'):
                    self.page_pomodoro.apply_theme()
                elif hasattr(self.page_pomodoro, 'update_theme'):
                    self.page_pomodoro.update_theme(theme)
            except Exception:
                pass
        # Tasks page: let it reload settings (reminders, sounds)
        if hasattr(self, 'page_tasks'):
            try:
                if hasattr(self.page_tasks, 'apply_settings'):
                    self.page_tasks.apply_settings()
                elif hasattr(self.page_tasks, 'update_theme'):
                    self.page_tasks.update_theme(theme)
            except Exception:
                pass
        self._update_sidebar_logo()

    def _update_sidebar_logo(self):
        if not hasattr(self, "logo_icon"):
            return
        if self._logo_pixmap is None:
            try:
                logo_path = _app_logo_path()
                if logo_path:
                    pixmap = QPixmap(logo_path)
                    if not pixmap.isNull():
                        self._logo_pixmap = pixmap
            except Exception:
                pass
        sidebar_w = self.sidebar.width() if hasattr(self, "sidebar") else self.width()
        sidebar_h = self.sidebar.height() if hasattr(self, "sidebar") else self.height()
        if hasattr(self, "sidebar_header_layout"):
            try:
                self.sidebar_header_layout.setContentsMargins(12, 12, 12, 12)
                self.sidebar_header_layout.setAlignment(
                    Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignHCenter
                )
            except Exception:
                pass

        # Compute header bounds and fit logo + text vertically without overlap.
        header_max_h = max(100, int(sidebar_h * 0.30))
        if hasattr(self, "sidebar_header"):
            self.sidebar_header.setMaximumHeight(header_max_h)
            header_h = self.sidebar_header.height() or header_max_h
        else:
            header_h = header_max_h

        margins = self.sidebar_header_layout.contentsMargins() if hasattr(self, "sidebar_header_layout") else None
        if margins:
            available_w = max(1, sidebar_w - margins.left() - margins.right())
            available_h = max(1, header_h - margins.top() - margins.bottom())
        else:
            available_w = max(1, sidebar_w - 20)
            available_h = max(1, header_h - 20)

        # Size title font first, then fit logo in remaining space.
        if hasattr(self, "logo_text"):
            font = self.logo_text.font()
            font.setPointSize(max(6, min(10, int(header_h / 14))))
            font.setBold(True)
            self.logo_text.setFont(font)
            text_h = QFontMetrics(font).height()
            self.logo_text.setFixedHeight(text_h)
            self.logo_text.setVisible(True)
        else:
            text_h = 0

        spacing = max(4, int(text_h * 0.6)) if hasattr(self, "sidebar_header_layout") else 6
        if hasattr(self, "sidebar_header_layout"):
            try:
                self.sidebar_header_layout.setSpacing(spacing)
            except Exception:
                pass

        available_h = max(1, available_h - text_h - spacing)

        target_w = max(40, min(120, available_w))
        target_h = max(40, min(120, available_h))
        target = min(target_w, target_h)
        self.logo_icon.setFixedSize(target, target)
        if self._logo_pixmap:
            inner = max(28, target - 14)
            scaled = self._logo_pixmap.scaled(
                inner,
                inner,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            self.logo_icon.setPixmap(scaled)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._update_sidebar_logo()

    def showEvent(self, event):
        super().showEvent(event)
        if hasattr(self, "_app_icon") and self._app_icon and not self._app_icon.isNull():
            self.setWindowIcon(self._app_icon)
        icon_path = _app_icon_path()
        if icon_path:
            try:
                self._win_hicon = _force_windows_app_icon(int(self.winId()), icon_path)
            except Exception:
                pass

    # --- STYLE LIGHT ---
    def set_light_theme(self):
        c = get_theme("Light")
        self.setStyleSheet(f"""
            /* GLOBAL */
            QMainWindow {{ background-color: {c['bg']}; color: {c['text']}; }}
            QWidget {{ color: {c['text']}; font-family: '{FONT_FAMILY}', 'Segoe UI'; }}
            QFrame#Sidebar {{ background-color: {c['card']}; border-right: 1px solid {c['border']}; }}
            QWidget#SidebarHeader {{ background-color: {c['card_alt']}; border-radius: 14px; margin: 12px; }}
            QLabel#SidebarLogo {{ background-color: {c['card']}; border: 1px solid {c['border']}; border-radius: 12px; padding: 6px; }}
            QLabel#SidebarTitle {{ font-weight: 800; color: {c['accent']}; }}
            QFrame#SidebarSeparator {{ background-color: {c['border']}; margin: 8px 10px; }}
            
            /* SIDEBAR BUTTONS */
            QFrame#Sidebar QPushButton[nav="true"] {{ background-color: transparent; padding: 12px 14px; border: none; border-radius: 10px; color: {c['deep']}; font-weight: bold; }}
            QFrame#Sidebar QPushButton[nav="true"]:hover {{ background-color: {c['accent_soft']}; color: {c['accent']}; }}
            QFrame#Sidebar QPushButton[nav="true"][active="true"] {{ 
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                    stop:0 {c['accent']}, stop:0.03 {c['accent']}, stop:0.03 {c['accent_soft']}, stop:1 {c['accent_soft']});
                color: {c['accent']};
            }}
            QFrame#Sidebar QPushButton[nav="true"][active="true"]:hover {{ 
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                    stop:0 {c['accent2']}, stop:0.03 {c['accent2']}, stop:0.03 {c['accent_soft']}, stop:1 {c['accent_soft']});
            }}
            
            /* SETTINGS SPECIFIC */
            QFrame#SettingsCard {{ background-color: {c['card']}; border: 1px solid {c['border']}; border-radius: 12px; }}
            QLabel#SettingsLabel {{ color: {c['text']}; }}
            QComboBox {{ background-color: {c['input_bg']}; border: 1px solid {c['input_border']}; border-radius: 6px; padding: 5px; color: {c['text']}; }}
            QComboBox QAbstractItemView {{ background-color: {c['card']}; color: {c['text']}; selection-background-color: {c['accent_soft']}; selection-color: {c['accent']}; }}

            /* QMessageBox */
            QMessageBox {{ background-color: {c['card']}; }}
            QMessageBox QLabel {{ color: {c['text']}; }}
            QMessageBox QPushButton {{
                color: {c['text']};
                background-color: {c['accent_soft']};
                border: 1px solid {c['border']};
                padding: 6px 12px;
                border-radius: 6px;
            }}
            QMessageBox QPushButton:hover {{ background-color: {c['border']}; }}

            /* LOGIN PAGE */
            QLabel#AppIcon {{ color: {c['accent']}; }}
            QLabel#LoginTitle {{ color: {c['text']}; font-weight: bold; }}
            QLabel#LoginSubtitle {{ color: {c['sub']}; }}
            QFrame#LoginCard {{
                background-color: {c['card']};
                border: 1px solid {c['border']};
                border-radius: 20px;
            }}
            QFrame#RememberedCard {{
                background-color: {c['card']};
                border: 1px solid {c['border']};
                border-radius: 20px;
            }}
            QLabel#RememberedTitle {{
                color: {c['text']};
                font-weight: 800;
                font-size: 14px;
            }}
            QLabel#RememberedEmpty {{
                color: {c['sub']};
                font-size: 11px;
            }}
            QPushButton#RememberedUserButton {{
                background-color: {c['card_alt']};
                border: 1px solid {c['border']};
                border-radius: 14px;
                padding: 8px 12px;
                text-align: left;
                color: {c['text']};
                font-weight: 700;
            }}
            QPushButton#RememberedForgetButton {{
                background-color: transparent;
                border: 1px solid {c['border']};
                border-radius: 14px;
                padding: 8px 12px;
                color: {c['sub']};
                font-weight: 700;
            }}
            QPushButton#RememberedForgetButton:hover {{
                background-color: {c['accent_soft']};
                border-color: {c['accent']};
                color: {c['accent']};
            }}
            QPushButton#RememberedUserButton:hover {{
                background-color: {c['accent_soft']};
                border-color: {c['accent']};
            }}
            QLabel#FieldLabel {{ color: {c['text']}; margin-bottom: 4px; }}
            QLabel#FieldIcon {{ color: {c['sub']}; }}
            QLabel#SectionTitle {{
                color: {c['accent']};
                font-size: 11px;
                font-weight: 800;
                letter-spacing: 1px;
                text-transform: uppercase;
                padding: 0 10px;
            }}
            QFrame#SectionLine {{
                background-color: {rgba(c['border'], 0.6)};
                min-height: 1px;
                max-height: 1px;
            }}
            QLineEdit#LoginInput {{
                background-color: {c['input_bg']};
                border: 2px solid {c['border']};
                border-radius: 12px;
                padding: 6px 14px;
                color: {c['text']};
                font-size: 15px;
            }}
            QLineEdit#LoginInput:focus {{
                border-color: {c['accent']};
                background-color: {c['card']};
            }}
            QCheckBox#LoginCheckbox {{
                color: {c['sub']};
                spacing: 8px;
            }}
            QCheckBox#LoginCheckbox::indicator {{
                width: 18px;
                height: 18px;
                border: 2px solid {c['border']};
                border-radius: 4px;
                background-color: {c['input_bg']};
            }}
            QCheckBox#LoginCheckbox::indicator:checked {{
                background-color: {c['accent']};
                border-color: {c['accent']};
            }}
            QPushButton#AnimatedButton {{
                border: none;
                border-radius: 14px;
                padding: 16px 24px;
                font-size: 16px;
                font-weight: 800;
                min-height: 44px;
            }}
            QPushButton#AnimatedButton[primary="true"] {{
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                    stop:0 {c['accent']}, stop:1 {c['accent2']});
                color: white;
            }}
            QPushButton#AnimatedButton[primary="false"] {{
                background-color: transparent;
                color: {c['text']};
                border: 2px solid {c['border']};
            }}
            QPushButton#AnimatedButton[primary="true"]:hover {{
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                    stop:0 {c['accent2']}, stop:1 {c['deep']});
            }}
            QPushButton#AnimatedButton[primary="false"]:hover {{
                background-color: {c['accent_soft']};
                border-color: {c['accent']};
                color: {c['deep']};
            }}
        """)

    # --- STYLE DARK ---
    def set_dark_theme(self):
        c = get_theme("Dark")
        self.setStyleSheet(f"""
            /* GLOBAL */
            QMainWindow {{ background-color: {c['bg']}; color: {c['text']}; }}
            QWidget {{ color: {c['text']}; font-family: '{FONT_FAMILY}', 'Segoe UI'; }}
            QFrame#Sidebar {{ background-color: {c['card']}; border-right: 1px solid {c['border']}; }}
            QWidget#SidebarHeader {{ background-color: {c['card_alt']}; border-radius: 14px; margin: 12px; }}
            QLabel#SidebarLogo {{ background-color: {c['card']}; border: 1px solid {c['border']}; border-radius: 12px; padding: 6px; }}
            QLabel#SidebarTitle {{ font-weight: 800; color: {c['accent']}; }}
            QFrame#SidebarSeparator {{ background-color: {c['border']}; margin: 8px 10px; }}
            
            /* SIDEBAR BUTTONS */
            QFrame#Sidebar QPushButton[nav="true"] {{ background-color: transparent; padding: 12px 14px; border: none; border-radius: 10px; color: {c['sub']}; font-weight: bold; }}
            QFrame#Sidebar QPushButton[nav="true"]:hover {{ background-color: {c['card_alt']}; color: {c['accent2']}; }}
            QFrame#Sidebar QPushButton[nav="true"][active="true"] {{ 
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                    stop:0 {c['accent2']}, stop:0.03 {c['accent2']}, stop:0.03 {c['card_alt']}, stop:1 {c['card_alt']});
                color: {c['accent']};
            }}
            QFrame#Sidebar QPushButton[nav="true"][active="true"]:hover {{ 
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                    stop:0 {c['accent']}, stop:0.03 {c['accent']}, stop:0.03 {c['card_alt']}, stop:1 {c['card_alt']});
            }}
            
            /* SETTINGS SPECIFIC */
            QFrame#SettingsCard {{ background-color: {c['card']}; border: 1px solid {c['border']}; border-radius: 12px; }}
            QLabel#SettingsLabel {{ color: {c['text']}; }}
            QComboBox {{ background-color: {c['input_bg']}; border: 1px solid {c['input_border']}; border-radius: 6px; padding: 5px; color: {c['text']}; }}
            QComboBox QAbstractItemView {{ background-color: {c['input_bg']}; color: {c['text']}; selection-background-color: {c['accent2']}; selection-color: {c['text']}; }}

            /* QMessageBox */
            QMessageBox {{ background-color: {c['card']}; }}
            QMessageBox QLabel {{ color: {c['text']}; }}
            QMessageBox QPushButton {{
                color: {c['text']};
                background-color: {c['card_alt']};
                border: 1px solid {c['border']};
                padding: 6px 12px;
                border-radius: 6px;
            }}
            QMessageBox QPushButton:hover {{ background-color: {c['border']}; }}

            /* LOGIN PAGE */
            QLabel#AppIcon {{ color: {c['accent']}; }}
            QLabel#LoginTitle {{ color: {c['text']}; font-weight: bold; }}
            QLabel#LoginSubtitle {{ color: {c['sub']}; }}
            QFrame#LoginCard {{
                background-color: {c['card']};
                border: 1px solid {c['border']};
                border-radius: 20px;
            }}
            QFrame#RememberedCard {{
                background-color: {c['card']};
                border: 1px solid {c['border']};
                border-radius: 20px;
            }}
            QLabel#RememberedTitle {{
                color: {c['text']};
                font-weight: 800;
                font-size: 14px;
            }}
            QLabel#RememberedEmpty {{
                color: {c['sub']};
                font-size: 11px;
            }}
            QPushButton#RememberedUserButton {{
                background-color: {c['card_alt']};
                border: 1px solid {c['border']};
                border-radius: 14px;
                padding: 8px 12px;
                text-align: left;
                color: {c['text']};
                font-weight: 700;
            }}
            QPushButton#RememberedForgetButton {{
                background-color: transparent;
                border: 1px solid {c['border']};
                border-radius: 14px;
                padding: 8px 12px;
                color: {c['sub']};
                font-weight: 700;
            }}
            QPushButton#RememberedForgetButton:hover {{
                background-color: {c['card_alt']};
                border-color: {c['accent']};
                color: {c['accent']};
            }}
            QPushButton#RememberedUserButton:hover {{
                background-color: {c['card_alt']};
                border-color: {c['accent']};
            }}
            QLabel#FieldLabel {{ color: {c['text']}; margin-bottom: 4px; }}
            QLabel#FieldIcon {{ color: {c['sub']}; }}
            QLabel#SectionTitle {{
                color: {c['accent']};
                font-size: 11px;
                font-weight: 800;
                letter-spacing: 1px;
                text-transform: uppercase;
                padding: 0 10px;
            }}
            QFrame#SectionLine {{
                background-color: {rgba(c['border'], 0.6)};
                min-height: 1px;
                max-height: 1px;
            }}
            QLineEdit#LoginInput {{
                background-color: {c['input_bg']};
                border: 2px solid {c['border']};
                border-radius: 12px;
                padding: 6px 14px;
                color: {c['text']};
                font-size: 15px;
            }}
            QLineEdit#LoginInput:focus {{
                border-color: {c['accent']};
                background-color: {c['card']};
            }}
            QCheckBox#LoginCheckbox {{
                color: {c['sub']};
                spacing: 8px;
            }}
            QCheckBox#LoginCheckbox::indicator {{
                width: 18px;
                height: 18px;
                border: 2px solid {c['border']};
                border-radius: 4px;
                background-color: {c['input_bg']};
            }}
            QCheckBox#LoginCheckbox::indicator:checked {{
                background-color: {c['accent']};
                border-color: {c['accent']};
            }}
            QPushButton#AnimatedButton {{
                border: none;
                border-radius: 14px;
                padding: 16px 24px;
                font-size: 16px;
                font-weight: 800;
                min-height: 44px;
            }}
            QPushButton#AnimatedButton[primary="true"] {{
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                    stop:0 {c['accent']}, stop:1 {c['accent2']});
                color: white;
            }}
            QPushButton#AnimatedButton[primary="false"] {{
                background-color: transparent;
                color: {c['text']};
                border: 2px solid {c['border']};
            }}
            QPushButton#AnimatedButton[primary="true"]:hover {{
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                    stop:0 {c['accent2']}, stop:1 {c['deep']});
            }}
            QPushButton#AnimatedButton[primary="false"]:hover {{
                background-color: {c['accent_soft']};
                border-color: {c['accent']};
                color: {c['deep']};
            }}
        """)


class _TransientWindowBlocker(QObject):
    def __init__(self, main_window):
        super().__init__(main_window)
        self._main_window = main_window
        self._suppress_until = 0.0
        self._debug = "--debug-windows" in sys.argv

    def suppress_for(self, ms):
        self._suppress_until = time.monotonic() + (ms / 1000.0)

    def eventFilter(self, obj, event):
        if not isinstance(obj, QWidget):
            return False

        et = event.type()
        if obj is self._main_window:
            return False

        # Proactively block top-level QLabel windows before they ever show.
        if isinstance(obj, QLabel) and obj.parent() is None:
            if et in (QEvent.Type.Polish, QEvent.Type.PolishRequest, QEvent.Type.Show):
                try:
                    obj.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
                except Exception:
                    pass
                if self._debug and et == QEvent.Type.Show:
                    try:
                        text = obj.text()
                    except Exception:
                        text = ""
                    try:
                        pix = obj.pixmap()
                    except Exception:
                        pix = None
                    print(f"[TransientBlocker] Blocked top-level QLabel text='{text}' has_pixmap={pix is not None}")
                try:
                    obj.hide()
                    if et == QEvent.Type.Show:
                        return True
                except Exception:
                    pass

        if et == QEvent.Type.Show and obj.isWindow():
            now = time.monotonic()
            if self._debug:
                try:
                    title = obj.windowTitle()
                    name = obj.objectName()
                    w = obj.width()
                    h = obj.height()
                    flags = int(obj.windowFlags())
                    parent = obj.parent().__class__.__name__ if obj.parent() else "None"
                    extra = ""
                    if isinstance(obj, QLabel):
                        try:
                            lbl_text = obj.text()
                            extra = f" text='{lbl_text}'"
                        except Exception:
                            extra = ""
                    print(f"[TransientBlocker] Show {obj.__class__.__name__} title='{title}' name='{name}' size={w}x{h} flags={flags} parent={parent}{extra}")
                except Exception:
                    pass
            if now < self._suppress_until:
                w = obj.width()
                h = obj.height()
                # Hide tiny/empty transient windows that can flash during page switches.
                if w <= 420 and h <= 180:
                    if self._debug:
                        title = obj.windowTitle()
                        print(f"[TransientBlocker] Hiding window: {obj.__class__.__name__} '{title}' {w}x{h}")
                    try:
                        obj.hide()
                        return True
                    except Exception:
                        return False
        return False

if __name__ == "__main__":
    try:
        init_db()
        _set_windows_app_id()
        app = QApplication(sys.argv)
        _install_qss_sanitizer()
        _install_qss_debug()
        
        icon_path = _app_icon_path()
        if icon_path:
            icon = _load_app_icon()
            if not icon.isNull():
                app.setWindowIcon(icon)
            
        # Exit cleanly on Ctrl+C without a traceback.
        signal.signal(signal.SIGINT, lambda *_: app.quit())
        try:
            # Ensure the local server doesn't keep running after closing/crashing the app.
            def _quit_cleanup():
                try:
                    api_shutdown_local_server(timeout=0.8)
                except Exception:
                    pass
                stop_managed_server()

            app.aboutToQuit.connect(_quit_cleanup)
        except Exception:
            pass

        window = MainApp()
        window.showMaximized()
        sys.exit(app.exec())
    except KeyboardInterrupt:
        pass
    except Exception:
        traceback.print_exc()
