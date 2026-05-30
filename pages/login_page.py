import os
import sys
import subprocess
import time
import threading
from urllib.parse import urlparse
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QLineEdit, QFrame, QMessageBox, QCheckBox, QGraphicsDropShadowEffect,
    QStackedLayout, QSizePolicy
)
from PyQt6.QtCore import Qt, pyqtSignal, QRectF, QTimer
from PyQt6.QtGui import QFont, QPainter, QColor, QLinearGradient, QPixmap, QIcon
from resources.api_client import (
    ApiError,
    api_login,
    api_me,
    api_register,
    api_register_app_instance,
    check_server_http,
    get_base_url,
    get_project_root,
    normalize_base_url,
    set_base_url,
    set_token,
    load_settings,
    save_settings,
)
from resources.server_manager import start_managed_server, get_current_process_create_time_filetime
from resources.theme import get_theme, FONT_FAMILY, rgba

SERVER_CHECK_TIMEOUT = 1.0

class AnimatedButton(QPushButton):
    """A custom button with hover animations and modern styling"""
    def __init__(self, text, primary=False):
        super().__init__(text)
        self.primary = primary
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setObjectName("AnimatedButton")
        self.setProperty("primary", primary)
        
        # Simple hover state
        self.hover_state = False
        
        # Shadow effect
        shadow = QGraphicsDropShadowEffect()
        if self.primary:
            shadow.setBlurRadius(18)
            shadow.setColor(QColor(0, 0, 0, 70))
            shadow.setOffset(0, 4)
        else:
            shadow.setBlurRadius(12)
            shadow.setColor(QColor(0, 0, 0, 40))
            shadow.setOffset(0, 2)
        self.setGraphicsEffect(shadow)

    def enterEvent(self, event):
        self.hover_state = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self.hover_state = False
        self.update()
        super().leaveEvent(event)

    def paintEvent(self, event):
        super().paintEvent(event)
        
        if self.hover_state:
            painter = QPainter(self)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            
            # Draw hover overlay
            overlay_color = QColor(255, 255, 255, 30)
            painter.fillRect(self.rect(), overlay_color)
            painter.end()

class CreateAccountPage(QWidget):
    account_created = pyqtSignal()
    cancel_requested = pyqtSignal()

    def __init__(self):
        super().__init__()
        self.current_theme = "Dark"
        self.init_ui()

    def init_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        main_layout.setSpacing(16)
        main_layout.setContentsMargins(24, 24, 24, 24)

        card = QFrame()
        card.setObjectName("LoginCard")
        card.setFixedWidth(580)
        card.setMinimumHeight(620)
        card.setMaximumHeight(900)
        self.card = card

        shadow = QGraphicsDropShadowEffect()
        shadow.setBlurRadius(20)
        shadow.setColor(QColor(0, 0, 0, 60))
        shadow.setOffset(0, 6)
        card.setGraphicsEffect(shadow)

        self.update_theme(self.current_theme)

        card_layout = QVBoxLayout(card)
        card_layout.setSpacing(16)
        card_layout.setContentsMargins(30, 30, 30, 28)

        header_layout = QVBoxLayout()
        header_layout.setSpacing(10)
        header_layout.setContentsMargins(0, 8, 0, 12)

        title = QLabel("Create your account")
        title.setObjectName("CreateTitle")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title_font = QFont(FONT_FAMILY, 26, QFont.Weight.Bold)
        title.setFont(title_font)
        title.setWordWrap(True)
        header_layout.addWidget(title)

        card_layout.addLayout(header_layout)

        def field_block(label_text, placeholder=""):
            block = QVBoxLayout()
            block.setSpacing(8)
            block.setContentsMargins(0, 0, 0, 12)

            separator_layout = QHBoxLayout()
            separator_layout.setContentsMargins(0, 0, 0, 0)
            separator_layout.setSpacing(10)

            left_line = QFrame()
            left_line.setObjectName("SectionLine")
            left_line.setFrameShape(QFrame.Shape.HLine)
            left_line.setFrameShadow(QFrame.Shadow.Plain)
            left_line.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            separator_layout.addWidget(left_line)

            section_label = QLabel(label_text)
            section_label.setObjectName("SectionTitle")
            section_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            separator_layout.addWidget(section_label)

            right_line = QFrame()
            right_line.setObjectName("SectionLine")
            right_line.setFrameShape(QFrame.Shape.HLine)
            right_line.setFrameShadow(QFrame.Shadow.Plain)
            right_line.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            separator_layout.addWidget(right_line)

            block.addLayout(separator_layout)

            field = QLineEdit()
            field.setObjectName("LoginInput")
            field.setPlaceholderText(placeholder)
            field.setFixedHeight(50)
            block.addWidget(field)
            return block, field

        username_block, self.username_input = field_block("Username", "Choose a username")
        card_layout.addLayout(username_block)

        password_block, self.password_input = field_block("Password", "Create a password")
        self.password_input.setEchoMode(QLineEdit.EchoMode.Password)
        card_layout.addLayout(password_block)

        confirm_block, self.confirm_input = field_block("Confirm Password", "Confirm your password")
        self.confirm_input.setEchoMode(QLineEdit.EchoMode.Password)
        card_layout.addLayout(confirm_block)

        server_block, self.server_input = field_block("Server URL", "http://127.0.0.1:8000")
        try:
            self.server_input.setText(get_base_url())
        except Exception:
            pass
        card_layout.addLayout(server_block)

        # Enter key submits the form (avoid forcing mouse clicks).
        try:
            self.username_input.returnPressed.connect(self.handle_create_account)
            self.password_input.returnPressed.connect(self.handle_create_account)
            self.confirm_input.returnPressed.connect(self.handle_create_account)
            self.server_input.returnPressed.connect(self.handle_create_account)
        except Exception:
            pass

        action_layout = QHBoxLayout()
        action_layout.setSpacing(12)

        self.create_button = AnimatedButton("Create account", primary=True)
        self.create_button.clicked.connect(self.handle_create_account)
        action_layout.addWidget(self.create_button)

        self.back_button = AnimatedButton("Back to login", primary=False)
        self.back_button.clicked.connect(lambda: self.cancel_requested.emit())
        action_layout.addWidget(self.back_button)

        card_layout.addLayout(action_layout)
        main_layout.addWidget(card)
        main_layout.addStretch()

    def update_theme(self, theme_name):
        self.current_theme = theme_name
        theme = get_theme(theme_name)
        self.card.setStyleSheet(f"""
            QLabel#CreateTitle {{
                color: {theme['text']};
                font-size: 32px;
                font-weight: 900;
            }}
            QLabel#SectionTitle {{
                color: {theme['accent']};
                font-size: 12px;
                font-weight: 800;
                letter-spacing: 1px;
                text-transform: uppercase;
                padding: 0 10px;
            }}
            QFrame#SectionLine {{
                background-color: {rgba(theme['border'], 0.5)};
                min-height: 1px;
                max-height: 1px;
            }}
            QLabel#BlockSubtitle {{
                color: {theme['sub']};
                font-size: 11px;
            }}
        """)

    def handle_create_account(self):
        username = self.username_input.text().strip()
        password = self.password_input.text()
        confirm = self.confirm_input.text()
        server_url = self.server_input.text().strip() or get_base_url()

        if not username or not password or not confirm:
            QMessageBox.warning(self, "Registration Error", "Please complete all fields before continuing.")
            return

        if password != confirm:
            QMessageBox.warning(self, "Registration Error", "Passwords do not match.")
            return

        if len(password) < 6:
            QMessageBox.warning(self, "Registration Error", "Password must be at least 6 characters long.")
            return

        try:
            set_base_url(server_url)
            api_register(username, password)
            self.account_created.emit()
        except ApiError as e:
            QMessageBox.warning(self, "Registration Error", str(e))

class LoginPage(QWidget):
    login_successful = pyqtSignal(int)  # Signal emitted with user_id when login succeeds
    server_status_checked = pyqtSignal(bool)
    server_status_scheduled = pyqtSignal(bool, int)

    def __init__(self):
        super().__init__()
        self.user_id = None
        self.init_ui()

    def init_ui(self):
        # Main layout with gradient background
        main_layout = QVBoxLayout(self)
        main_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        main_layout.setSpacing(22)
        main_layout.setContentsMargins(40, 36, 40, 36)

        # Welcome section with icon
        welcome_layout = QVBoxLayout()
        welcome_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        welcome_layout.setSpacing(10)

        # Title with gradient text effect
        title = QLabel("Welcome to ProdSmart")
        title.setObjectName("LoginTitle")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title_font = QFont(FONT_FAMILY, 22, QFont.Weight.Bold)
        title.setFont(title_font)
        welcome_layout.addWidget(title)

        # Subtitle
        subtitle = QLabel("Your productivity companion")
        subtitle.setObjectName("LoginSubtitle")
        subtitle.setAlignment(Qt.AlignmentFlag.AlignCenter)
        subtitle_font = QFont(FONT_FAMILY, 11)
        subtitle.setFont(subtitle_font)
        welcome_layout.addWidget(subtitle)

        main_layout.addLayout(welcome_layout)

        self.stack = QStackedLayout()
        self.login_view = QWidget()
        login_wrapper = QVBoxLayout(self.login_view)
        login_wrapper.setContentsMargins(0, 0, 0, 0)
        login_wrapper.setSpacing(0)

        # Login form container with modern card design
        form_card = QFrame()
        form_card.setObjectName("LoginCard")
        form_card.setFixedWidth(580)
        
        # Add shadow effect to the card
        shadow = QGraphicsDropShadowEffect()
        shadow.setBlurRadius(20)
        shadow.setColor(QColor(0, 0, 0, 60))
        shadow.setOffset(0, 4)
        form_card.setGraphicsEffect(shadow)
        
        form_layout = QVBoxLayout(form_card)
        form_layout.setSpacing(14)
        form_layout.setContentsMargins(44, 38, 44, 36)

        card_title = QLabel("LOGIN TO YOUR ACCOUNT")
        card_title.setObjectName("LoginCardTitle")
        card_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        card_title_font = QFont(FONT_FAMILY, 18, QFont.Weight.Bold)
        card_title.setFont(card_title_font)
        form_layout.addWidget(card_title)

        # Username field
        input_height = 54

        def section_header(label_text):
            header = QHBoxLayout()
            header.setContentsMargins(0, 0, 0, 0)
            header.setSpacing(10)

            left_line = QFrame()
            left_line.setObjectName("SectionLine")
            left_line.setFrameShape(QFrame.Shape.HLine)
            left_line.setFrameShadow(QFrame.Shadow.Plain)
            left_line.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            header.addWidget(left_line)

            section_label = QLabel(label_text)
            section_label.setObjectName("SectionTitle")
            section_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            header.addWidget(section_label)

            right_line = QFrame()
            right_line.setObjectName("SectionLine")
            right_line.setFrameShape(QFrame.Shape.HLine)
            right_line.setFrameShadow(QFrame.Shadow.Plain)
            right_line.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            header.addWidget(right_line)

            return header

        def field_block(label_text, placeholder, is_password=False):
            block = QVBoxLayout()
            block.setSpacing(8)
            block.addLayout(section_header(label_text))

            field = QLineEdit()
            field.setObjectName("LoginInput")
            field.setPlaceholderText(placeholder)
            field.setFixedHeight(input_height)
            if is_password:
                field.setEchoMode(QLineEdit.EchoMode.Password)
            block.addWidget(field)
            return block, field

        username_block, self.username_input = field_block("Username", "Enter your username")
        form_layout.addLayout(username_block)

        password_block, self.password_input = field_block("Password", "Enter your password", is_password=True)
        form_layout.addLayout(password_block)

        self.show_password_checkbox = QCheckBox("Show password")
        self.show_password_checkbox.setObjectName("LoginCheckbox")
        self.show_password_checkbox.toggled.connect(self._toggle_password_visibility)
        form_layout.addWidget(self.show_password_checkbox)

        server_block = QVBoxLayout()
        server_block.setSpacing(8)
        server_block.addLayout(section_header("Server URL"))

        server_row = QHBoxLayout()
        server_row.setSpacing(12)

        self.server_input = QLineEdit()
        self.server_input.setObjectName("LoginInput")
        self.server_input.setPlaceholderText("http://127.0.0.1:8000")
        self.server_input.setFixedHeight(input_height)
        try:
            self.server_input.setText(get_base_url())
        except Exception:
            pass
        server_row.addWidget(self.server_input, 1)

        self.start_server_button = AnimatedButton("Start Server", primary=False)
        self.start_server_button.setFixedHeight(input_height)
        self.start_server_button.setMinimumWidth(140)
        self.start_server_button.clicked.connect(self.start_cloud_server)
        self.start_server_button.setGraphicsEffect(None)
        server_row.addWidget(self.start_server_button)

        server_block.addLayout(server_row)
        form_layout.addLayout(server_block)

        self.server_status = QLabel("Server: not running")
        self.server_status.setObjectName("LoginSubtitle")
        self.server_status.setContentsMargins(2, 0, 0, 0)
        form_layout.addWidget(self.server_status)

        # Remember me checkbox with better styling
        self.remember_checkbox = QCheckBox("Remember me")
        self.remember_checkbox.setObjectName("LoginCheckbox")
        self.remember_checkbox.stateChanged.connect(self._on_remember_toggled)
        form_layout.addWidget(self.remember_checkbox)

        # Buttons layout with modern buttons
        buttons_layout = QHBoxLayout()
        buttons_layout.setSpacing(16)

        self.login_button = AnimatedButton("Login", primary=True)
        self.login_button.clicked.connect(self.handle_login)
        self.login_button.setFixedHeight(44)
        self.login_button.setMinimumWidth(170)
        buttons_layout.addWidget(self.login_button)

        self.register_button = AnimatedButton("Create Account", primary=False)
        self.register_button.clicked.connect(self.handle_register)
        self.register_button.setFixedHeight(44)
        self.register_button.setMinimumWidth(170)
        buttons_layout.addWidget(self.register_button)

        form_layout.addLayout(buttons_layout)

        # Remembered accounts card (separate block)
        self.remembered_card = QFrame()
        self.remembered_card.setObjectName("RememberedCard")
        self.remembered_card.setFixedWidth(320)
        remembered_card_layout = QVBoxLayout(self.remembered_card)
        remembered_card_layout.setContentsMargins(24, 24, 24, 24)
        remembered_card_layout.setSpacing(12)

        remembered_title = QLabel("Remembered Accounts")
        remembered_title.setObjectName("RememberedTitle")
        remembered_card_layout.addWidget(remembered_title)

        self.remembered_container = QWidget()
        self.remembered_container.setObjectName("RememberedContainer")
        self.remembered_layout = QVBoxLayout(self.remembered_container)
        self.remembered_layout.setContentsMargins(0, 0, 0, 0)
        self.remembered_layout.setSpacing(8)
        remembered_card_layout.addWidget(self.remembered_container)
        remembered_card_layout.addStretch()

        cards_row = QHBoxLayout()
        cards_row.setSpacing(22)
        cards_row.addWidget(form_card)
        cards_row.addWidget(self.remembered_card)

        login_wrapper.addLayout(cards_row)
        login_wrapper.addStretch()

        self.register_view = CreateAccountPage()
        self.register_view.account_created.connect(self.on_registration_success)
        self.register_view.cancel_requested.connect(self.show_login_page)

        self.stack.addWidget(self.login_view)
        self.stack.addWidget(self.register_view)
        main_layout.addLayout(self.stack)
        main_layout.addStretch()

        # Connect enter key to login
        self.username_input.returnPressed.connect(self.handle_login)
        self.password_input.returnPressed.connect(self.handle_login)
        self.server_input.returnPressed.connect(self.handle_login)

        # Initialize status + remembered user
        self._last_server_url = None
        self._server_status_inflight = False
        self._server_status_started_at = 0.0
        self._server_is_running = None
        self.server_status_checked.connect(self._handle_server_status_result)
        self.server_status_scheduled.connect(self._handle_scheduled_check_result)
        self._load_remembered_user()
        self._refresh_server_status()
        self._server_status_timer = QTimer(self)
        self._server_status_timer.setInterval(1000)
        self._server_status_timer.timeout.connect(self._refresh_server_status)
        self._server_status_timer.start()

    def _toggle_password_visibility(self, show):
        if not hasattr(self, "password_input"):
            return
        mode = QLineEdit.EchoMode.Normal if show else QLineEdit.EchoMode.Password
        self.password_input.setEchoMode(mode)

    def start_server_status_timer(self):
        if hasattr(self, "_server_status_timer"):
            self._server_status_timer.start()

    def stop_server_status_timer(self):
        if hasattr(self, "_server_status_timer") and self._server_status_timer.isActive():
            self._server_status_timer.stop()

    def update_theme(self, theme_name):
        """Update the theme of the login page"""
        self.current_theme = theme_name
        if hasattr(self, "register_view"):
            self.register_view.update_theme(theme_name)
        try:
            self._render_remembered_users(getattr(self, "_remembered_accounts_cache", []))
        except Exception:
            pass
        self.update()  # Trigger repaint for gradient background

    def _apply_server_state(self, is_up):
        prev = getattr(self, "_server_is_running", None)
        self._server_is_running = bool(is_up)
        if hasattr(self, "server_status"):
            self.server_status.setText("Server: running" if is_up else "Server: not running")
        if hasattr(self, "login_button"):
            self.login_button.setEnabled(bool(is_up))

        if bool(is_up):
            try:
                url = self.server_input.text().strip() if hasattr(self, "server_input") else ""
            except Exception:
                url = ""
            if not url:
                try:
                    url = get_base_url()
                except Exception:
                    url = ""

            key = str(normalize_base_url(url or "")).strip().lower()
            if getattr(self, "_app_registered_key", None) != key or prev is False or prev is None:
                self._app_registered_key = key

                pid = os.getpid()
                create_time = None
                try:
                    create_time = get_current_process_create_time_filetime()
                except Exception:
                    create_time = None

                def _reg_worker():
                    try:
                        api_register_app_instance(pid, create_time=create_time, timeout=0.8)
                    except Exception:
                        pass

                threading.Thread(target=_reg_worker, daemon=True).start()

    def set_server_state(self, is_up):
        self._apply_server_state(bool(is_up))

    def is_server_running(self):
        return bool(self._server_is_running)

    def _warn_server_down(self):
        QMessageBox.warning(self, "Server", "Server stopped or it's not running.")

    def _refresh_server_status(self):
        self._sync_server_url()
        if getattr(self, "_server_status_inflight", False):
            started_at = getattr(self, "_server_status_started_at", 0.0)
            if started_at and (time.monotonic() - started_at) < 2.0:
                return
            # Reset stuck inflight checks
            self._server_status_inflight = False
        server_url = ""
        if hasattr(self, "server_input"):
            server_url = self.server_input.text().strip()
        if not server_url:
            server_url = get_base_url()
        self._server_status_inflight = True
        self._server_status_started_at = time.monotonic()

        def _worker(url):
            try:
                is_up = check_server_http(url, timeout=SERVER_CHECK_TIMEOUT)
            except Exception:
                is_up = False
            self.server_status_checked.emit(is_up)

        threading.Thread(target=_worker, args=(server_url,), daemon=True).start()

    def _handle_server_status_result(self, is_up):
        self._server_status_inflight = False
        self._server_status_started_at = 0.0
        self._apply_server_state(is_up)

    def _schedule_server_status_check(self, attempts=10):
        if attempts <= 0:
            return
        def _try():
            self._sync_server_url()
            server_url = ""
            if hasattr(self, "server_input"):
                server_url = self.server_input.text().strip()
            if not server_url:
                server_url = get_base_url()

            def _worker(url, remaining):
                try:
                    is_up = check_server_http(url, timeout=SERVER_CHECK_TIMEOUT)
                except Exception:
                    is_up = False
                self.server_status_scheduled.emit(is_up, remaining)

            threading.Thread(target=_worker, args=(server_url, attempts), daemon=True).start()

        QTimer.singleShot(800, _try)

    def _handle_scheduled_check_result(self, is_up, remaining):
        self._apply_server_state(is_up)
        if is_up:
            return
        if remaining <= 1:
            return
        self._schedule_server_status_check(remaining - 1)

    def _sync_server_url(self):
        if not hasattr(self, "server_input"):
            return
        target = (self.server_input.text().strip() or get_base_url()).strip()
        if not target:
            return
        try:
            current = get_base_url()
        except Exception:
            current = ""
        if target == self._last_server_url and current == target:
            return
        self._last_server_url = target
        if current != target:
            try:
                set_base_url(target)
            except Exception:
                pass

    def _remembered_accounts(self):
        try:
            data = load_settings()
        except Exception:
            return []
        accounts = data.get("remembered_accounts")
        result = []
        if isinstance(accounts, list):
            for item in accounts:
                if not isinstance(item, dict):
                    continue
                username = str(item.get("username") or "").strip()
                if not username:
                    continue
                token = str(item.get("token") or "").strip() or None
                user_id = item.get("user_id")
                result.append({"username": username, "token": token, "user_id": user_id})
        usernames = data.get("remembered_usernames")
        if isinstance(usernames, list):
            for item in usernames:
                name = str(item).strip()
                if not name:
                    continue
                if not any(acc["username"] == name for acc in result):
                    result.append({"username": name, "token": None, "user_id": None})
        legacy = (data.get("remembered_username") or "").strip()
        if legacy and not any(acc["username"] == legacy for acc in result):
            result.append({"username": legacy, "token": None, "user_id": None})
        return result

    def _save_remembered_accounts(self, accounts):
        try:
            data = load_settings()
        except Exception:
            data = {}
        clean = []
        seen = set()
        for item in accounts:
            username = str(item.get("username") or "").strip()
            if not username or username in seen:
                continue
            seen.add(username)
            clean.append({
                "username": username,
                "token": item.get("token"),
                "user_id": item.get("user_id"),
            })
        data["remembered_accounts"] = clean
        data.pop("remembered_username", None)
        data.pop("remembered_usernames", None)
        try:
            save_settings(data)
        except Exception:
            pass

    def _load_remembered_user(self):
        self._remembered_accounts_cache = self._remembered_accounts()
        self._render_remembered_users(self._remembered_accounts_cache)
        # Always start unchecked per requirement
        self.remember_checkbox.setChecked(False)

    def _on_remember_toggled(self, state):
        try:
            data = load_settings()
        except Exception:
            data = {}
        data["remember_me"] = bool(state)
        try:
            save_settings(data)
        except Exception:
            pass

    def _render_remembered_users(self, accounts):
        if not hasattr(self, "remembered_layout"):
            return
        while self.remembered_layout.count():
            item = self.remembered_layout.takeAt(0)
            widget = item.widget()
            if widget:
                widget.deleteLater()
        if not accounts:
            empty = QLabel("No remembered accounts yet.")
            empty.setObjectName("RememberedEmpty")
            self.remembered_layout.addWidget(empty)
            return
        self._remembered_accounts_cache = list(accounts)
        for account in accounts:
            username = account.get("username")
            row = QWidget()
            row_layout = QHBoxLayout(row)
            row_layout.setContentsMargins(0, 0, 0, 0)
            row_layout.setSpacing(8)

            user_btn = QPushButton()
            user_btn.setObjectName("RememberedUserButton")
            user_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            user_btn.setFixedHeight(44)
            user_btn.setText(f"  {username}")
            user_btn.clicked.connect(lambda _, u=username: self._use_remembered_user(u))
            self._set_user_button_icon(user_btn, username)
            row_layout.addWidget(user_btn, 1)

            forget_btn = QPushButton("Forget")
            forget_btn.setObjectName("RememberedForgetButton")
            forget_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            forget_btn.setFixedHeight(44)
            forget_btn.clicked.connect(lambda _, u=username: self._forget_remembered_user(u))
            row_layout.addWidget(forget_btn)

            self.remembered_layout.addWidget(row)

    def _set_user_button_icon(self, button, username):
        theme = get_theme(self.current_theme if hasattr(self, "current_theme") else "Dark")
        initials = (str(username)[:1] or "U").upper()
        size = 26
        pix = QPixmap(size, size)
        pix.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pix)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        bg = QColor(theme["accent"])
        fg = QColor(theme["bg"])
        painter.setBrush(bg)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawEllipse(0, 0, size, size)
        painter.setPen(fg)
        font = QFont(FONT_FAMILY, 10, QFont.Weight.Bold)
        painter.setFont(font)
        painter.drawText(pix.rect(), Qt.AlignmentFlag.AlignCenter, initials)
        painter.end()
        button.setIcon(QIcon(pix))
        button.setIconSize(pix.size())

    def _forget_remembered_user(self, username):
        current = [acc for acc in self._remembered_accounts() if acc.get("username") != username]
        self._save_remembered_accounts(current)
        self._render_remembered_users(current)

    def _use_remembered_user(self, username):
        if not username:
            return
        token = None
        user_id = None
        for acc in self._remembered_accounts():
            if acc.get("username") == username:
                token = acc.get("token")
                user_id = acc.get("user_id")
                break
        server_url = self.server_input.text().strip() or get_base_url()
        try:
            set_base_url(server_url)
        except Exception:
            pass
        if not check_server_http(server_url, timeout=SERVER_CHECK_TIMEOUT):
            self._warn_server_down()
            return
        self.username_input.setText(username)
        if token:
            try:
                set_token(token, user_id=user_id, username=username)
            except Exception:
                pass
            try:
                res = api_me()
                srv_id = res.get("user_id") if isinstance(res, dict) else None
                server_username = (res.get("username") or "").strip() if isinstance(res, dict) else ""
                if srv_id and server_username.lower() == str(username).strip().lower():
                    self.user_id = srv_id
                    try:
                        self._maybe_offer_legacy_local_db_import(srv_id)
                    except Exception:
                        pass
                    self.login_successful.emit(srv_id)
                    return
            except ApiError:
                pass
            except Exception:
                pass
        self.password_input.setFocus()

    def _maybe_offer_legacy_local_db_import(self, user_id: int) -> None:
        """Offer to import the legacy shared local DB into this account (one-time prompt)."""
        try:
            uid = int(user_id)
        except Exception:
            return
        if uid <= 0:
            return

        try:
            from database.db_manager import (
                get_legacy_import_decision,
                import_legacy_db_to_user,
                legacy_db_has_data,
                merge_legacy_db_to_user,
                set_legacy_import_decision,
                user_db_has_any_data,
            )
        except Exception:
            return

        try:
            decision = get_legacy_import_decision(uid)
        except Exception:
            decision = None
        if decision in ("imported", "skipped"):
            return

        try:
            if not legacy_db_has_data():
                return
        except Exception:
            return

        has_user_data = False
        try:
            has_user_data = bool(user_db_has_any_data(uid))
        except Exception:
            has_user_data = False

        prompt = (
            "We found local tasks/history on this device from an older version.\n\n"
            "Import them into this account?"
        )
        if has_user_data:
            prompt = (
                "We found local tasks/history on this device from an older version.\n\n"
                "This account already has local data.\n\n"
                "Merge the old local data into this account? (This may create duplicates.)"
            )

        reply = QMessageBox.question(
            self,
            "Local Data",
            prompt,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )

        if reply == QMessageBox.StandardButton.Yes:
            if has_user_data:
                ok, msg = merge_legacy_db_to_user(uid, backup_existing=True)
            else:
                ok, msg = import_legacy_db_to_user(uid, overwrite_if_empty=True, backup_existing=True)
            if ok:
                try:
                    QMessageBox.information(self, "Local Data", msg)
                except Exception:
                    pass
                try:
                    set_legacy_import_decision(uid, "imported")
                except Exception:
                    pass
            else:
                try:
                    QMessageBox.warning(self, "Local Data", msg)
                except Exception:
                    pass
        else:
            try:
                set_legacy_import_decision(uid, "skipped")
            except Exception:
                pass

    def _is_local_url(self, url):
        try:
            url = normalize_base_url(url or "")
            parsed = urlparse(url)
            host = parsed.hostname or ""
            return host in ("127.0.0.1", "localhost", "0.0.0.0")
        except Exception:
            return False

    def start_cloud_server(self):
        server_url = self.server_input.text().strip() if hasattr(self, "server_input") else ""
        if not server_url:
            server_url = get_base_url()
        server_url = normalize_base_url(server_url or "http://127.0.0.1:8000")

        if not self._is_local_url(server_url):
            QMessageBox.information(
                self,
                "Server URL",
                "This URL points to another machine. Start the server on that machine, or use 127.0.0.1 here."
            )
            return

        try:
            set_base_url(server_url)
        except Exception:
            pass

        if check_server_http(server_url, timeout=SERVER_CHECK_TIMEOUT):
            self._apply_server_state(True)
            QMessageBox.information(self, "Server", "Server is already running.")
            return
        else:
            self._apply_server_state(False)

        project_root = get_project_root()
        server_path = os.path.join(project_root, "server", "main.py")
        if not os.path.exists(server_path):
            QMessageBox.warning(self, "Server", "Server entrypoint not found (server/main.py).")
            return

        creationflags = 0
        if os.name == "nt":
            try:
                creationflags = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS
            except Exception:
                creationflags = 0

        try:
            parsed = urlparse(server_url)
            host = parsed.hostname or "127.0.0.1"
            port = parsed.port or 8000
            if host in ("localhost", "0.0.0.0"):
                host = "127.0.0.1"

            log_path = os.path.join(project_root, "server", "server.log")
            start_managed_server(
                server_path=server_path,
                cwd=project_root,
                host=host,
                port=int(port),
                log_path=log_path,
                python_exe=sys.executable,
                creationflags=creationflags,
            )
            if hasattr(self, "server_status"):
                self.server_status.setText("Server: starting...")
            msg = "Server started. Try login again in a few seconds."
            if os.path.exists(log_path):
                msg += "\n\nLogs: server/server.log"
            QMessageBox.information(self, "Server", msg)
            self._schedule_server_status_check()
        except Exception as e:
            QMessageBox.warning(self, "Server", f"Could not start server: {e}")

    def paintEvent(self, event):
        """Custom paint event to draw gradient background"""
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        
        # Create gradient background
        gradient = QLinearGradient(0, 0, self.width(), self.height())
        theme = get_theme(self.current_theme if hasattr(self, 'current_theme') else "Dark")
        
        gradient.setColorAt(0.0, QColor(theme["bg"]))
        gradient.setColorAt(0.5, QColor(theme["card_alt"]))
        gradient.setColorAt(1.0, QColor(theme["deep"]))
        
        painter.fillRect(self.rect(), gradient)
        painter.end()
        
        super().paintEvent(event)

    def handle_login(self):
        username = self.username_input.text().strip()
        password = self.password_input.text()
        server_url = self.server_input.text().strip() or get_base_url()

        if not username or not password:
            QMessageBox.warning(self, "Login Error", "Please enter both username and password.")
            return
        if self._server_is_running is False:
            self._warn_server_down()
            return

        try:
            set_base_url(server_url)
            if not check_server_http(server_url, timeout=SERVER_CHECK_TIMEOUT):
                self._warn_server_down()
                return
            res = api_login(username, password)
            user_id = res.get("user_id") if isinstance(res, dict) else None
            if user_id:
                if self.remember_checkbox.isChecked():
                    accounts = self._remembered_accounts()
                    token = res.get("token") if isinstance(res, dict) else None
                    updated = False
                    for acc in accounts:
                        if acc.get("username") == username:
                            acc["token"] = token
                            acc["user_id"] = user_id
                            updated = True
                            break
                    if not updated:
                        accounts.append({"username": username, "token": token, "user_id": user_id})
                    self._save_remembered_accounts(accounts)
                    self._render_remembered_users(accounts)
                self.user_id = user_id
                try:
                    self._maybe_offer_legacy_local_db_import(user_id)
                except Exception:
                    pass
                self.login_successful.emit(user_id)
                return
            QMessageBox.warning(self, "Login Failed", "Invalid username or password.")
        except ApiError as e:
            message = str(e)
            if "Network error" in message or "timed out" in message:
                self._warn_server_down()
            else:
                QMessageBox.warning(self, "Login Failed", message)

    def handle_register(self):
        self.show_registration_page()

    def show_registration_page(self):
        self.stack.setCurrentWidget(self.register_view)

    def show_login_page(self):
        self.stack.setCurrentWidget(self.login_view)
        self.username_input.clear()
        self.password_input.clear()
        try:
            self.force_server_status_refresh()
        except Exception:
            pass

    def force_server_status_refresh(self):
        try:
            self._server_status_inflight = False
        except Exception:
            pass
        try:
            self._server_status_started_at = 0.0
        except Exception:
            pass
        try:
            self._refresh_server_status()
        except Exception:
            pass

    def on_registration_success(self):
        QMessageBox.information(self, "Success", "Account created successfully! You can now log in.")
        self.show_login_page()
