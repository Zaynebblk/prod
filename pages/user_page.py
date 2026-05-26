import os
import sqlite3
from datetime import datetime
from typing import List, Optional, Tuple

from PyQt6.QtCore import QByteArray, QBuffer, QIODevice, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QPainter, QPainterPath, QPixmap
from PyQt6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QTextEdit,
    QVBoxLayout,
    QWidget,
    QApplication,
)

from pages.settings_page import Toggle
from resources.api_client import (
    ApiError,
    api_get_user_avatar,
    api_get_user_profile,
    api_set_my_avatar,
    api_set_my_profile,
    get_base_url,
    load_settings,
    save_settings,
)
from resources.theme import FONT_FAMILY, get_theme, rgba


class _ChangePasswordDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Change Password")

        root = QVBoxLayout(self)
        root.setContentsMargins(14, 14, 14, 14)
        root.setSpacing(10)

        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft)
        form.setFormAlignment(Qt.AlignmentFlag.AlignTop)

        self.input_current = QLineEdit()
        self.input_current.setEchoMode(QLineEdit.EchoMode.Password)
        self.input_new = QLineEdit()
        self.input_new.setEchoMode(QLineEdit.EchoMode.Password)
        self.input_confirm = QLineEdit()
        self.input_confirm.setEchoMode(QLineEdit.EchoMode.Password)

        form.addRow("Current password", self.input_current)
        form.addRow("New password", self.input_new)
        form.addRow("Confirm new password", self.input_confirm)
        root.addLayout(form)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel | QDialogButtonBox.StandardButton.Ok)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    def values(self):
        return (
            self.input_current.text(),
            self.input_new.text(),
            self.input_confirm.text(),
        )


class UserProfilePage(QWidget):
    """Profile page inspired by a modern dashboard layout."""

    profile_updated = pyqtSignal()
    request_history = pyqtSignal()

    def __init__(self):
        super().__init__()
        self.setObjectName("UserProfilePage")
        self.username: Optional[str] = None
        self.user_id: Optional[int] = None
        self.profile_picture_path: Optional[str] = None
        self._profile_picture_dirty: bool = False
        self._profile_picture_source_pixmap: Optional[QPixmap] = None
        self.current_theme = "Dark"
        self._skills: List[str] = []
        self._projects_dirty: bool = False

        self._build_ui()
        self.load_profile()
        self.update_theme(self.current_theme)

    # ---------- Settings (per-account) ----------
    def _user_profile_key(self) -> str | None:
        try:
            uid = int(self.user_id)
        except Exception:
            return None
        if uid <= 0:
            return None
        return str(uid)

    def _get_or_create_profile_settings(self, settings: dict) -> dict:
        """Return the profile dict for the current user and ensure it exists in settings."""
        key = self._user_profile_key()
        if not key:
            return settings

        profiles = settings.get("user_profiles")
        if not isinstance(profiles, dict):
            profiles = {}
            settings["user_profiles"] = profiles

        profile = profiles.get(key)
        if not isinstance(profile, dict):
            profile = {}
            profiles[key] = profile
        return profile

    def _migrate_legacy_profile_if_needed(self, settings: dict) -> None:
        """Move legacy global profile keys into the current user's profile dict (one-time)."""
        key = self._user_profile_key()
        if not key:
            return

        profiles = settings.get("user_profiles")
        if isinstance(profiles, dict) and isinstance(profiles.get(key), dict):
            return

        legacy_keys = (
            "user_bio",
            "user_role",
            "user_department",
            "user_join_date",
            "user_two_factor",
            "user_skills",
            "user_profile_picture",
        )
        if not any(k in settings for k in legacy_keys):
            return

        profile = self._get_or_create_profile_settings(settings)
        profile["bio"] = str(settings.pop("user_bio", "") or "")
        profile["role"] = str(settings.pop("user_role", "") or "").strip()
        profile["department"] = str(settings.pop("user_department", "") or "").strip()
        profile["join_date"] = str(settings.pop("user_join_date", "") or "").strip()
        profile["two_factor"] = bool(settings.pop("user_two_factor", False))
        profile["skills"] = settings.pop("user_skills", None)
        profile["local_picture_path"] = settings.pop("user_profile_picture", None)

        save_settings(settings)

    # ---------- UI ----------
    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setStyleSheet(
            "QScrollArea { border: none; background: transparent; }"
            "QScrollArea QWidget { background: transparent; }"
            "QScrollArea::viewport { background: transparent; }"
        )

        self.container = QWidget()
        self.container.setObjectName("ProfileContainer")
        self.container.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        content = QVBoxLayout(self.container)
        content.setContentsMargins(32, 26, 32, 18)
        content.setSpacing(16)

        # Page header
        header = QFrame()
        header.setObjectName("ProfileHeader")
        header.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        header_l = QVBoxLayout(header)
        header_l.setContentsMargins(6, 6, 6, 6)
        header_l.setSpacing(4)

        self.kicker = QLabel("USER PROFILE")
        self.kicker.setObjectName("ProfileKicker")
        self.page_title = QLabel("User Profile")
        self.page_title.setObjectName("ProfileTitle")
        self.page_subtitle = QLabel("Manage your identity, preferences, and security")
        self.page_subtitle.setObjectName("ProfileSub")

        header_l.addWidget(self.kicker)
        header_l.addWidget(self.page_title)
        header_l.addWidget(self.page_subtitle)
        content.addWidget(header)

        grid_wrap = QWidget()
        grid_wrap.setObjectName("ProfileGridWrap")
        grid_wrap.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        grid = QGridLayout(grid_wrap)
        grid.setSpacing(14)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setColumnStretch(0, 3)
        grid.setColumnStretch(1, 2)

        # --- Hero / overview card ---
        hero = self._new_card(variant="hero")
        hero_l = QHBoxLayout(hero)
        hero_l.setContentsMargins(18, 18, 18, 18)
        hero_l.setSpacing(18)

        avatar_col = QVBoxLayout()
        avatar_col.setSpacing(10)
        avatar_col.setAlignment(Qt.AlignmentFlag.AlignTop)

        self.profile_picture = QLabel()
        self.profile_picture.setObjectName("ProfileAvatar")
        self.profile_picture.setFixedSize(104, 104)
        self.profile_picture.setScaledContents(True)
        self._set_default_avatar()
        avatar_col.addWidget(self.profile_picture, alignment=Qt.AlignmentFlag.AlignLeft)

        self.btn_upload_picture = QPushButton("Change photo")
        self.btn_upload_picture.setObjectName("ProfileSecondaryBtn")
        self.btn_upload_picture.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_upload_picture.clicked.connect(self.on_upload_picture)
        avatar_col.addWidget(self.btn_upload_picture, alignment=Qt.AlignmentFlag.AlignLeft)

        self.btn_share_profile = QPushButton("Share profile")
        self.btn_share_profile.setObjectName("ProfileSecondaryBtn")
        self.btn_share_profile.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_share_profile.clicked.connect(self._on_share_profile)
        avatar_col.addWidget(self.btn_share_profile, alignment=Qt.AlignmentFlag.AlignLeft)

        avatar_col.addStretch()

        hero_l.addLayout(avatar_col)

        info_col = QVBoxLayout()
        info_col.setSpacing(10)

        self.label_username = QLabel("Not logged in")
        self.label_username.setObjectName("ProfileName")
        self.label_username.setWordWrap(True)
        info_col.addWidget(self.label_username)

        role_lbl = QLabel("ROLE")
        role_lbl.setObjectName("ProfileFieldLabel")
        self.input_role = QLineEdit()
        self.input_role.setObjectName("ProfileInput")
        self.input_role.setPlaceholderText("e.g., Product Manager")
        info_col.addWidget(role_lbl)
        info_col.addWidget(self.input_role)

        bio_lbl = QLabel("BIO")
        bio_lbl.setObjectName("ProfileFieldLabel")
        info_col.addWidget(bio_lbl)

        self.text_bio = QTextEdit()
        self.text_bio.setObjectName("ProfileText")
        self.text_bio.setPlaceholderText("Write a short bio...")
        self.text_bio.setMinimumHeight(88)
        self.text_bio.setMaximumHeight(120)
        info_col.addWidget(self.text_bio)

        projects_lbl = QLabel("PROJECTS / WORK")
        projects_lbl.setObjectName("ProfileFieldLabel")
        info_col.addWidget(projects_lbl)

        self.text_projects = QTextEdit()
        self.text_projects.setObjectName("ProfileText")
        self.text_projects.setPlaceholderText("Projects you're working on, responsibilities, focus areas…")
        self.text_projects.setMinimumHeight(88)
        self.text_projects.setMaximumHeight(140)
        info_col.addWidget(self.text_projects)

        chips_row = QHBoxLayout()
        chips_row.setSpacing(8)
        self.chip_user_id = QLabel("ID: -")
        self.chip_user_id.setObjectName("ProfileChip")
        self.chip_server = QLabel("Server: -")
        self.chip_server.setObjectName("ProfileChip")
        chips_row.addWidget(self.chip_user_id)
        chips_row.addWidget(self.chip_server)
        chips_row.addStretch()
        info_col.addLayout(chips_row)

        hero_l.addLayout(info_col, stretch=1)

        # --- Department card ---
        dept = self._new_card(variant="small")
        dept_l = QVBoxLayout(dept)
        dept_l.setContentsMargins(16, 16, 16, 16)
        dept_l.setSpacing(10)
        dept_lbl = QLabel("DEPARTMENT")
        dept_lbl.setObjectName("ProfileFieldLabel")
        self.input_department = QLineEdit()
        self.input_department.setObjectName("ProfileInput")
        self.input_department.setPlaceholderText("e.g., Product Strategy")
        dept_l.addWidget(dept_lbl)
        dept_l.addWidget(self.input_department)
        dept_l.addStretch()

        # --- Join date card ---
        join = self._new_card(variant="small")
        join_l = QVBoxLayout(join)
        join_l.setContentsMargins(16, 16, 16, 16)
        join_l.setSpacing(10)
        join_lbl = QLabel("JOIN DATE")
        join_lbl.setObjectName("ProfileFieldLabel")
        self.label_join_date = QLabel(datetime.now().strftime("%b %d, %Y"))
        self.label_join_date.setObjectName("ProfileBigValue")
        join_l.addWidget(join_lbl)
        join_l.addWidget(self.label_join_date)
        join_l.addStretch()

        # --- Activity log ---
        activity = self._new_card()
        activity_l = QVBoxLayout(activity)
        activity_l.setContentsMargins(16, 16, 16, 16)
        activity_l.setSpacing(10)
        act_header = QHBoxLayout()
        act_title = QLabel("Recent Activity Log")
        act_title.setObjectName("ProfileCardTitle")
        self.btn_view_activity = QPushButton("View All")
        self.btn_view_activity.setObjectName("ProfileLinkBtn")
        self.btn_view_activity.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_view_activity.clicked.connect(self._on_view_all_clicked)
        act_header.addWidget(act_title)
        act_header.addStretch()
        act_header.addWidget(self.btn_view_activity)
        activity_l.addLayout(act_header)

        self.activity_list = QVBoxLayout()
        self.activity_list.setSpacing(8)
        activity_l.addLayout(self.activity_list)
        activity_l.addStretch()

        # --- Security settings ---
        security = self._new_card()
        security_l = QVBoxLayout(security)
        security_l.setContentsMargins(16, 16, 16, 16)
        security_l.setSpacing(12)
        sec_title = QLabel("Security Settings")
        sec_title.setObjectName("ProfileCardTitle")
        security_l.addWidget(sec_title)

        pwd_row = QHBoxLayout()
        pwd_left = QVBoxLayout()
        pwd_left.setSpacing(2)
        pwd_name = QLabel("Password Reset")
        pwd_name.setObjectName("ProfileRowTitle")
        self.pwd_meta = QLabel("Last updated: never")
        self.pwd_meta.setObjectName("ProfileRowMeta")
        pwd_left.addWidget(pwd_name)
        pwd_left.addWidget(self.pwd_meta)
        self.btn_change_password = QPushButton("Change")
        self.btn_change_password.setObjectName("ProfileSecondaryBtn")
        self.btn_change_password.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_change_password.clicked.connect(self._on_change_password_clicked)
        pwd_row.addLayout(pwd_left)
        pwd_row.addStretch()
        pwd_row.addWidget(self.btn_change_password)
        security_l.addLayout(pwd_row)

        twofa_row = QHBoxLayout()
        twofa_left = QVBoxLayout()
        twofa_left.setSpacing(2)
        twofa_name = QLabel("Two-factor Authentication")
        twofa_name.setObjectName("ProfileRowTitle")
        twofa_meta = QLabel("Protect your account with a second step")
        twofa_meta.setObjectName("ProfileRowMeta")
        twofa_left.addWidget(twofa_name)
        twofa_left.addWidget(twofa_meta)
        self.toggle_twofa = Toggle()
        self.toggle_twofa.setObjectName("ProfileToggle")
        twofa_row.addLayout(twofa_left)
        twofa_row.addStretch()
        twofa_row.addWidget(self.toggle_twofa, alignment=Qt.AlignmentFlag.AlignRight)
        security_l.addLayout(twofa_row)
        security_l.addStretch()

        # --- Skills card ---
        skills = self._new_card()
        skills_l = QVBoxLayout(skills)
        skills_l.setContentsMargins(16, 16, 16, 16)
        skills_l.setSpacing(10)
        skills_title_row = QHBoxLayout()
        skills_title = QLabel("Skills & Expertise")
        skills_title.setObjectName("ProfileCardTitle")
        self.btn_add_skill = QPushButton("+ Add New")
        self.btn_add_skill.setObjectName("ProfileSecondaryBtn")
        self.btn_add_skill.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_add_skill.clicked.connect(self._add_skill_from_input)
        skills_title_row.addWidget(skills_title)
        skills_title_row.addStretch()
        skills_title_row.addWidget(self.btn_add_skill)
        skills_l.addLayout(skills_title_row)

        self.input_skill = QLineEdit()
        self.input_skill.setObjectName("ProfileInput")
        self.input_skill.setPlaceholderText("Type a skill and press Enter...")
        self.input_skill.returnPressed.connect(self._add_skill_from_input)
        skills_l.addWidget(self.input_skill)

        self.skills_grid = QGridLayout()
        self.skills_grid.setSpacing(8)
        skills_l.addLayout(self.skills_grid)
        skills_l.addStretch()

        # Layout in grid
        grid.addWidget(hero, 0, 0, 1, 1)
        grid.addWidget(dept, 0, 1, 1, 1)
        grid.addWidget(join, 1, 1, 1, 1)
        grid.addWidget(activity, 1, 0, 3, 1)
        grid.addWidget(security, 2, 1, 1, 1)
        grid.addWidget(skills, 3, 1, 1, 1)

        content.addWidget(grid_wrap)
        content.addStretch()

        self.scroll.setWidget(self.container)
        root.addWidget(self.scroll, stretch=1)

        # Sticky footer action bar
        footer = QFrame()
        footer.setObjectName("ProfileFooter")
        footer.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        footer_l = QHBoxLayout(footer)
        footer_l.setContentsMargins(26, 10, 26, 10)
        footer_l.setSpacing(12)

        self.footer_hint = QLabel("Changes are saved locally and reflected across the app.")
        self.footer_hint.setObjectName("ProfileFooterHint")
        footer_l.addWidget(self.footer_hint)
        footer_l.addStretch()

        self.btn_cancel = QPushButton("Cancel")
        self.btn_cancel.setObjectName("ProfileGhostBtn")
        self.btn_cancel.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_cancel.clicked.connect(self.load_profile)

        self.btn_save = QPushButton("Update Profile")
        self.btn_save.setObjectName("ProfilePrimaryBtn")
        self.btn_save.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_save.clicked.connect(self.on_save_profile)

        footer_l.addWidget(self.btn_cancel)
        footer_l.addWidget(self.btn_save)
        root.addWidget(footer, stretch=0)

    def _new_card(self, variant: str = "base") -> QFrame:
        card = QFrame()
        card.setObjectName("ProfileCard")
        card.setProperty("variant", variant)
        card.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        card.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
        return card

    # ---------- Avatar ----------
    def _set_default_avatar(self):
        size = self.profile_picture.size()
        pixmap = QPixmap(max(1, size.width()), max(1, size.height()))
        pixmap.fill(QColor("#3078CD"))

        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        font_size = max(28, int(min(size.width(), size.height()) * 0.55))
        painter.setFont(QFont(FONT_FAMILY, font_size, QFont.Weight.Bold))
        painter.setPen(QColor("#ffffff"))
        initials = (self.username or "U")[:1].upper()
        painter.drawText(pixmap.rect(), Qt.AlignmentFlag.AlignCenter, initials)
        painter.end()

        self._set_profile_picture_pixmap(pixmap)

    def _create_rounded_pixmap(self, source: QPixmap, radius: int = 18) -> QPixmap:
        size = self.profile_picture.size()
        scaled = source.scaled(
            size,
            Qt.AspectRatioMode.KeepAspectRatioByExpanding,
            Qt.TransformationMode.SmoothTransformation,
        )
        x_offset = max(0, (scaled.width() - size.width()) // 2)
        y_offset = max(0, (scaled.height() - size.height()) // 2)
        square = scaled.copy(x_offset, y_offset, size.width(), size.height())

        rounded = QPixmap(size)
        rounded.fill(Qt.GlobalColor.transparent)
        painter = QPainter(rounded)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        path = QPainterPath()
        path.addRoundedRect(0, 0, size.width(), size.height(), radius, radius)
        painter.setClipPath(path)
        painter.drawPixmap(0, 0, square)
        painter.end()
        return rounded

    def _set_profile_picture_pixmap(self, pixmap: QPixmap):
        self.profile_picture.setPixmap(self._create_rounded_pixmap(pixmap))

    # ---------- Data ----------
    def set_user_info(self, username, user_id):
        self.username = username
        self.user_id = user_id
        self.label_username.setText(username)
        self.chip_user_id.setText(f"ID: {user_id}")
        self._set_default_avatar()
        self.load_profile()

    def load_profile(self):
        self._profile_picture_dirty = False
        self._profile_picture_source_pixmap = None
        try:
            settings = load_settings()

            key = self._user_profile_key()
            if key:
                self._migrate_legacy_profile_if_needed(settings)
                profile = self._get_or_create_profile_settings(settings)
            else:
                profile = {
                    "bio": settings.get("user_bio", ""),
                    "role": settings.get("user_role", ""),
                    "department": settings.get("user_department", ""),
                    "join_date": settings.get("user_join_date", ""),
                    "two_factor": settings.get("user_two_factor", False),
                    "skills": settings.get("user_skills"),
                    "projects": settings.get("user_projects", ""),
                    "local_picture_path": settings.get("user_profile_picture"),
                }

            self.text_bio.setPlainText(str(profile.get("bio", "") or ""))
            try:
                self.text_projects.setPlainText(str(profile.get("projects", "") or ""))
            except Exception:
                pass
            self.input_role.setText(str(profile.get("role", "") or "").strip())
            self.input_department.setText(str(profile.get("department", "") or "").strip())

            join_raw = str(profile.get("join_date", "") or "").strip()
            join_dt = self._parse_datetime(join_raw)
            self.label_join_date.setText(join_dt.strftime("%b %d, %Y") if join_dt else datetime.now().strftime("%b %d, %Y"))
            if not join_raw:
                # Persist a stable join date even if the user never hits "Update Profile".
                try:
                    if key and isinstance(profile, dict):
                        profile["join_date"] = datetime.now().strftime("%Y-%m-%d")
                    else:
                        settings["user_join_date"] = datetime.now().strftime("%Y-%m-%d")
                    save_settings(settings)
                except Exception:
                    pass

            self.toggle_twofa.setChecked(bool(profile.get("two_factor", False)))

            self._skills = self._normalize_skills(profile.get("skills"))
            self._render_skill_chips()

            try:
                base_url = get_base_url()
            except Exception:
                base_url = str(settings.get("cloud_base_url", "") or "").strip()
            self.chip_server.setText(f"Server: {base_url or '-'}")

            # Prefer the cloud avatar (per account). Fallback to a per-user local picture path if present.
            avatar_loaded = False
            try:
                if self.user_id is not None:
                    data = api_get_user_avatar(int(self.user_id))
                    if data:
                        pixmap = QPixmap()
                        ok = pixmap.loadFromData(data)
                        if ok and not pixmap.isNull():
                            self._set_profile_picture_pixmap(pixmap)
                            self.profile_picture_path = None
                            avatar_loaded = True
            except Exception:
                avatar_loaded = False

            if not avatar_loaded:
                pic_path = profile.get("local_picture_path") if isinstance(profile, dict) else None
                if pic_path and os.path.exists(str(pic_path)):
                    pixmap = QPixmap(str(pic_path))
                    if not pixmap.isNull():
                        self._set_profile_picture_pixmap(pixmap)
                        self.profile_picture_path = str(pic_path)
        except Exception:
            pass

        # Best-effort: refresh from cloud profile (so other devices + user search share the same data).
        try:
            if self.user_id is not None:
                cloud = api_get_user_profile(int(self.user_id))
            else:
                cloud = None
        except Exception:
            cloud = None

        if isinstance(cloud, dict):
            try:
                cloud_bio = str(cloud.get("bio") or "")
                cloud_role = str(cloud.get("role") or "").strip()
                cloud_dept = str(cloud.get("department") or "").strip()
                cloud_projects = str(cloud.get("projects") or "")
                cloud_skills = self._normalize_skills(cloud.get("skills"))
                cloud_join = str(cloud.get("join_date") or "").strip()

                # Only overwrite local UI if the server has *some* profile content.
                has_cloud_content = any(
                    [
                        cloud_bio.strip(),
                        cloud_role,
                        cloud_dept,
                        cloud_projects.strip(),
                        bool(cloud_skills),
                    ]
                )

                if has_cloud_content:
                    self.text_bio.setPlainText(cloud_bio)
                    self.input_role.setText(cloud_role)
                    self.input_department.setText(cloud_dept)
                    self.text_projects.setPlainText(cloud_projects)

                    join_dt = self._parse_datetime(cloud_join)
                    if join_dt:
                        self.label_join_date.setText(join_dt.strftime("%b %d, %Y"))

                    self._skills = cloud_skills
                    self._render_skill_chips()

                # If server profile is empty but local has data, migrate local -> server once.
                if not has_cloud_content:
                    try:
                        local_bio = self.text_bio.toPlainText()
                        local_role = self.input_role.text().strip()
                        local_dept = self.input_department.text().strip()
                        local_projects = self.text_projects.toPlainText()
                        local_skills = list(self._skills)
                        if any([local_bio.strip(), local_role, local_dept, local_projects.strip(), bool(local_skills)]):
                            api_set_my_profile(
                                {
                                    "bio": local_bio,
                                    "role": local_role,
                                    "department": local_dept,
                                    "join_date": cloud_join,
                                    "skills": local_skills,
                                    "projects": local_projects,
                                }
                            )
                    except Exception:
                        pass

                # Cache server profile locally for offline use (or after migration).
                try:
                    settings = load_settings()
                    key = self._user_profile_key()
                    if key:
                        self._migrate_legacy_profile_if_needed(settings)
                        profile = self._get_or_create_profile_settings(settings)
                        profile["bio"] = cloud_bio
                        profile["role"] = cloud_role
                        profile["department"] = cloud_dept
                        profile["join_date"] = cloud_join
                        profile["skills"] = list(cloud_skills)
                        profile["projects"] = cloud_projects
                        save_settings(settings)
                except Exception:
                    pass
            except Exception:
                pass

        self._refresh_activity()

    def on_upload_picture(self):
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "Select Profile Picture",
            "",
            "Image Files (*.png *.jpg *.jpeg *.bmp);;All Files (*)",
        )

        if not file_path:
            return

        pixmap = QPixmap(file_path)
        if pixmap.isNull():
            QMessageBox.warning(self, "Error", "Failed to load image. Please try another file.")
            return

        self._set_profile_picture_pixmap(pixmap)
        self.profile_picture_path = file_path
        self._profile_picture_dirty = True
        self._profile_picture_source_pixmap = pixmap
        QMessageBox.information(self, "Success", "Picture updated. Click 'Update Profile' to persist.")

    def _on_share_profile(self):
        if self.user_id is None:
            QMessageBox.warning(self, "Share profile", "No user profile is available to share.")
            return

        try:
            uid = int(self.user_id)
            if uid <= 0:
                raise ValueError("Invalid user id")
        except Exception:
            QMessageBox.warning(self, "Share profile", "Unable to determine your user ID for sharing.")
            return

        try:
            base_url = get_base_url().rstrip("/")
            share_url = f"{base_url}/users/{uid}/profile"
        except Exception:
            QMessageBox.warning(self, "Share profile", "Could not build the share link.")
            return

        try:
            QApplication.clipboard().setText(share_url)
            QMessageBox.information(
                self,
                "Share profile",
                "Profile link copied to clipboard. Share it with others so they can view your profile.",
            )
        except Exception:
            QMessageBox.information(self, "Share profile", f"Copy this link:\n{share_url}")

    def on_save_profile(self):
        try:
            settings = load_settings()

            key = self._user_profile_key()
            if key:
                self._migrate_legacy_profile_if_needed(settings)
                profile = self._get_or_create_profile_settings(settings)
                profile["bio"] = self.text_bio.toPlainText()
                profile["role"] = self.input_role.text().strip()
                profile["department"] = self.input_department.text().strip()
                profile["skills"] = list(self._skills)
                profile["projects"] = self.text_projects.toPlainText()
                profile["two_factor"] = bool(self.toggle_twofa.isChecked())
                if not str(profile.get("join_date", "") or "").strip():
                    profile["join_date"] = datetime.now().strftime("%Y-%m-%d")
            else:
                settings["user_bio"] = self.text_bio.toPlainText()
                settings["user_role"] = self.input_role.text().strip()
                settings["user_department"] = self.input_department.text().strip()
                settings["user_skills"] = list(self._skills)
                settings["user_two_factor"] = bool(self.toggle_twofa.isChecked())
                settings["user_projects"] = self.text_projects.toPlainText()
                if not str(settings.get("user_join_date", "") or "").strip():
                    settings["user_join_date"] = datetime.now().strftime("%Y-%m-%d")

            if self.profile_picture_path and self._profile_picture_dirty:
                app_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
                pic_dir = os.path.join(app_dir, "resources", "profiles")
                os.makedirs(pic_dir, exist_ok=True)

                ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
                pic_filename = f"profile_{self.user_id or 'local'}_{ts}.png"
                pic_dest = os.path.join(pic_dir, pic_filename)

                pixmap = self._profile_picture_source_pixmap
                if pixmap is not None and not pixmap.isNull():
                    ok = pixmap.save(pic_dest, "PNG")
                    if not ok:
                        raise OSError("Failed to write profile picture.")
                else:
                    import shutil

                    shutil.copy2(self.profile_picture_path, pic_dest)

                if key:
                    profile["local_picture_path"] = pic_dest
                else:
                    settings["user_profile_picture"] = pic_dest
                self.profile_picture_path = pic_dest

                # Upload to cloud so the Team page can show the avatar for all members.
                try:
                    image_bytes = None
                    ba = QByteArray()
                    buffer = QBuffer(ba)
                    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
                    ok = False
                    if pixmap is not None and not pixmap.isNull():
                        ok = pixmap.save(buffer, "PNG")
                    buffer.close()
                    if ok and ba.size():
                        image_bytes = bytes(ba)
                    if not image_bytes and os.path.exists(pic_dest):
                        with open(pic_dest, "rb") as f:
                            image_bytes = f.read()
                    api_set_my_avatar(image_bytes)
                except ApiError as e:
                    QMessageBox.warning(self, "Avatar", f"Profile saved, but avatar upload failed: {str(e)}")
                self._profile_picture_dirty = False
                self._profile_picture_source_pixmap = None

            # Migration helper: if we have a local picture but no cloud avatar yet, upload it once.
            if key and not self._profile_picture_dirty:
                try:
                    existing = api_get_user_avatar(int(self.user_id)) if self.user_id is not None else None
                except Exception:
                    existing = None
                if not existing:
                    try:
                        local_path = str(profile.get("local_picture_path") or "").strip()
                        if local_path and os.path.exists(local_path):
                            with open(local_path, "rb") as f:
                                api_set_my_avatar(f.read())
                    except Exception:
                        pass

            save_settings(settings)

            # Best-effort: persist the profile to the server so it can be viewed in user search.
            try:
                api_set_my_profile(
                    {
                        "bio": self.text_bio.toPlainText(),
                        "role": self.input_role.text().strip(),
                        "department": self.input_department.text().strip(),
                        "join_date": (profile.get("join_date") if key else settings.get("user_join_date")),
                        "skills": list(self._skills),
                        "projects": self.text_projects.toPlainText(),
                    }
                )
            except Exception:
                pass
            QMessageBox.information(self, "Success", "Profile updated successfully!")
            self.profile_updated.emit()
        except Exception as e:
            QMessageBox.warning(self, "Error", f"Failed to save profile: {str(e)}")

    # ---------- Theme ----------
    def update_theme(self, theme_name):
        self.current_theme = theme_name
        c = get_theme(theme_name)

        card_bg = rgba(c["card"], 0.96)
        card_alt = rgba(c["card_alt"], 0.94)
        border = rgba(c["border"], 0.85)
        border_soft = rgba(c["border"], 0.6)

        self.setStyleSheet(
            f"""
            QWidget#UserProfilePage {{
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                    stop:0 {c['bg']}, stop:0.6 {c['card_alt']}, stop:1 {c['deep']});
                font-family: '{FONT_FAMILY}', 'Segoe UI';
            }}
            QWidget#ProfileContainer {{ background: transparent; }}
            QFrame#ProfileHeader {{ background: transparent; }}

            QLabel#ProfileKicker {{ color: {c['accent']}; font-size: 11px; font-weight: 900; letter-spacing: 1px; }}
            QLabel#ProfileTitle {{ color: {c['text']}; font-size: 30px; font-weight: 900; }}
            QLabel#ProfileSub {{ color: {c['sub']}; font-size: 12px; font-weight: 600; }}
            QLabel#ProfileName {{ color: {c['text']}; font-size: 22px; font-weight: 900; }}

            QFrame#ProfileCard {{
                background: {card_bg};
                border: 1px solid {border};
                border-radius: 22px;
            }}
            QFrame#ProfileCard[variant="hero"] {{
                background: {card_alt};
                border: 1px solid {border};
                border-radius: 24px;
            }}
            QFrame#ProfileCard[variant="small"] {{
                background: {rgba(c['card'], 0.92)};
                border: 1px solid {border_soft};
                border-radius: 18px;
            }}

            QLabel#ProfileFieldLabel {{ color: {c['sub']}; font-size: 10px; font-weight: 900; letter-spacing: 1px; }}
            QLabel#ProfileBigValue {{ color: {c['text']}; font-size: 18px; font-weight: 900; }}
            QLabel#ProfileCardTitle {{ color: {c['text']}; font-size: 14px; font-weight: 900; }}
            QLabel#ProfileRowTitle {{ color: {c['text']}; font-size: 12px; font-weight: 800; }}
            QLabel#ProfileRowMeta {{ color: {c['sub']}; font-size: 10px; font-weight: 600; }}

            QLabel#ProfileChip {{
                background: {rgba(c['chip'], 0.9)};
                color: {c['chip_text']};
                border: 1px solid {rgba(c['border'], 0.65)};
                border-radius: 10px;
                padding: 3px 8px;
                font-size: 10px;
                font-weight: 800;
            }}

            QLabel#ProfileAvatar {{
                border-radius: 18px;
                border: 1px solid {rgba(c['border'], 0.8)};
                background: {c['accent_soft']};
            }}

            QLineEdit#ProfileInput {{
                background: {c['input_bg']};
                border: 1px solid {rgba(c['input_border'], 0.9)};
                border-radius: 12px;
                padding: 8px 10px;
                color: {c['text']};
                font-size: 12px;
                font-weight: 600;
            }}
            QLineEdit#ProfileInput:focus {{
                border-color: {c['accent']};
                background: {rgba(c['card'], 0.98)};
            }}

            QTextEdit#ProfileText {{
                background: {c['input_bg']};
                border: 1px solid {rgba(c['input_border'], 0.9)};
                border-radius: 12px;
                padding: 8px 10px;
                color: {c['text']};
                font-size: 12px;
            }}

            QPushButton#ProfilePrimaryBtn {{
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                    stop:0 {c['accent2']}, stop:1 {c['accent']});
                color: white;
                border: none;
                border-radius: 12px;
                padding: 10px 16px;
                font-weight: 900;
                min-width: 140px;
            }}
            QPushButton#ProfilePrimaryBtn:hover {{
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                    stop:0 {c['accent']}, stop:1 {c['accent2']});
            }}

            QPushButton#ProfileSecondaryBtn {{
                background: {rgba(c['card'], 0.35)};
                color: {c['text']};
                border: 1px solid {rgba(c['border'], 0.75)};
                border-radius: 12px;
                padding: 8px 12px;
                font-weight: 800;
            }}
            QPushButton#ProfileSecondaryBtn:hover {{
                background: {rgba(c['card_alt'], 0.9)};
                border-color: {c['accent']};
            }}

            QPushButton#ProfileGhostBtn {{
                background: transparent;
                color: {c['sub']};
                border: 1px solid {rgba(c['border'], 0.65)};
                border-radius: 12px;
                padding: 10px 14px;
                font-weight: 800;
            }}
            QPushButton#ProfileGhostBtn:hover {{
                background: {rgba(c['card_alt'], 0.7)};
                color: {c['text']};
            }}

            QPushButton#ProfileLinkBtn {{
                background: transparent;
                color: {c['accent']};
                border: none;
                font-weight: 900;
                padding: 0;
            }}
            QPushButton#ProfileLinkBtn:hover {{ color: {c['accent2']}; }}

            QFrame#ProfileFooter {{
                background: {rgba(c['card'], 0.96)};
                border-top: 1px solid {rgba(c['border'], 0.6)};
            }}
            QLabel#ProfileFooterHint {{ color: {c['sub']}; font-size: 11px; font-weight: 700; }}
            """
        )

        # Keep the Toggle colors coherent with the theme.
        try:
            self.toggle_twofa._bg_color = rgba(c["border"], 0.55)
            self.toggle_twofa._active_color = c["accent"]
            self.toggle_twofa._circle_color = c["text"]
            self.toggle_twofa.update()
        except Exception:
            pass

        self._render_skill_chips()
        self._refresh_activity()

    # ---------- Skills ----------
    def _normalize_skills(self, raw) -> List[str]:
        if raw is None:
            return []
        if isinstance(raw, list):
            items = [str(x).strip() for x in raw]
        else:
            items = [s.strip() for s in str(raw).split(",")]
        out: List[str] = []
        for item in items:
            if not item:
                continue
            if item.lower() in {s.lower() for s in out}:
                continue
            out.append(item)
        return out

    def _clear_layout(self, layout):
        while layout.count():
            item = layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.setParent(None)

    def _render_skill_chips(self):
        if not hasattr(self, "skills_grid") or self.skills_grid is None:
            return
        self._clear_layout(self.skills_grid)
        cols = 3
        for idx, skill in enumerate(self._skills):
            chip = QLabel(skill)
            chip.setObjectName("ProfileChip")
            chip.setToolTip("Double-click to remove")
            chip.mouseDoubleClickEvent = lambda _e, s=skill: self._remove_skill(s)
            self.skills_grid.addWidget(chip, idx // cols, idx % cols)

    def _add_skill_from_input(self):
        text = self.input_skill.text().strip()
        if not text:
            return
        self.input_skill.setText("")
        if text.lower() not in {s.lower() for s in self._skills}:
            self._skills.append(text)
            self._render_skill_chips()

    def _remove_skill(self, skill: str):
        self._skills = [s for s in self._skills if s.lower() != skill.lower()]
        self._render_skill_chips()

    # ---------- Activity ----------
    def _db_path(self) -> str:
        """Return the active local DB path.

        Must match `HistoryPage` / `get_db_connection()` behavior, i.e. per-account DB when logged in.
        """
        try:
            from database.db_manager import get_db_path

            return str(get_db_path() or "")
        except Exception:
            app_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            return os.path.join(app_dir, "prodsmart.db")

    def _parse_datetime(self, raw: str) -> Optional[datetime]:
        value = (raw or "").strip()
        if not value:
            return None
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%Y/%m/%d %H:%M:%S", "%Y/%m/%d"):
            try:
                return datetime.strptime(value, fmt)
            except Exception:
                continue
        try:
            return datetime.fromisoformat(value)
        except Exception:
            return None

    def _relative_time(self, dt: Optional[datetime]) -> str:
        if dt is None:
            return ""
        now = datetime.now()
        try:
            delta = now - dt
        except Exception:
            return dt.strftime("%b %d, %Y")
        seconds = int(delta.total_seconds())
        if seconds < 60:
            return "just now"
        minutes = seconds // 60
        if minutes < 60:
            return f"{minutes} min ago"
        hours = minutes // 60
        if hours < 24:
            return f"{hours}h ago"
        days = hours // 24
        if days == 1:
            return "Yesterday"
        if days < 7:
            return f"{days} days ago"
        return dt.strftime("%b %d, %Y")

    def _fetch_recent_activity(self, limit: int = 4) -> List[Tuple[str, str]]:
        db_path = self._db_path()
        if not os.path.exists(db_path):
            return []

        items: List[Tuple[str, str]] = []
        conn = None
        try:
            conn = sqlite3.connect(db_path)
            try:
                rows = conn.execute(
                    "SELECT task_title, ended_at, duration_min FROM pomodoro_sessions "
                    "WHERE status='completed' AND ended_at IS NOT NULL "
                    "ORDER BY ended_at DESC LIMIT ?",
                    (int(limit),),
                ).fetchall()
                for title, ended_at, duration_min in rows:
                    dt = self._parse_datetime(str(ended_at or ""))
                    when = self._relative_time(dt) if dt else ""
                    minutes = int(duration_min or 0)
                    label = str(title or "Focus session").strip() or "Focus session"
                    detail = f"{minutes} min focus" if minutes else "Focus session"
                    if when:
                        detail = f"{detail} - {when}"
                    items.append((label, detail))
            except Exception:
                pass

            if not items:
                rows = conn.execute(
                    "SELECT title, completed_at FROM tasks "
                    "WHERE is_completed=1 AND completed_at IS NOT NULL "
                    "ORDER BY completed_at DESC LIMIT ?",
                    (int(limit),),
                ).fetchall()
                for title, completed_at in rows:
                    dt = self._parse_datetime(str(completed_at or ""))
                    when = self._relative_time(dt) if dt else ""
                    label = str(title or "Completed a task").strip() or "Completed a task"
                    detail = f"Completed - {when}" if when else "Completed"
                    items.append((label, detail))
        except Exception:
            items = []
        finally:
            try:
                if conn is not None:
                    conn.close()
            except Exception:
                pass

        return items[:limit]

    def _refresh_activity(self):
        if not hasattr(self, "activity_list") or self.activity_list is None:
            return
        self._clear_layout(self.activity_list)

        items = self._fetch_recent_activity(limit=4)
        if not items:
            items = [("No activity yet", "Complete tasks or run Pomodoro sessions to see updates here.")]

        for title, meta in items:
            row = QFrame()
            row.setObjectName("ProfileActivityRow")
            row.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
            row_l = QHBoxLayout(row)
            row_l.setContentsMargins(12, 10, 12, 10)
            row_l.setSpacing(10)

            dot = QLabel("*")
            dot.setObjectName("ProfileActivityDot")
            dot.setFixedSize(18, 18)
            dot.setAlignment(Qt.AlignmentFlag.AlignCenter)

            text_col = QVBoxLayout()
            text_col.setSpacing(2)
            t = QLabel(title)
            t.setObjectName("ProfileRowTitle")
            t.setWordWrap(True)
            m = QLabel(meta)
            m.setObjectName("ProfileRowMeta")
            m.setWordWrap(True)
            text_col.addWidget(t)
            text_col.addWidget(m)

            row_l.addWidget(dot, alignment=Qt.AlignmentFlag.AlignTop)
            row_l.addLayout(text_col)
            self.activity_list.addWidget(row)

        try:
            c = get_theme(self.current_theme)
            for row in self.findChildren(QFrame, "ProfileActivityRow"):
                row.setStyleSheet(
                    f"QFrame#ProfileActivityRow {{ background: {rgba(c['card'], 0.35)}; "
                    f"border: 1px solid {rgba(c['border'], 0.55)}; border-radius: 16px; }}"
                )
            for dot in self.findChildren(QLabel, "ProfileActivityDot"):
                dot.setStyleSheet(
                    f"QLabel#ProfileActivityDot {{ color: {c['accent']}; "
                    f"background: {rgba(c['accent_soft'], 0.85)}; border-radius: 9px; }}"
                )
        except Exception:
            pass

    # ---------- Actions ----------
    def _show_activity_not_implemented(self):
        QMessageBox.information(self, "Activity", "Full activity history view is not implemented yet.")

    def _on_view_all_clicked(self, _checked=False):
        try:
            self.request_history.emit()
        except Exception:
            pass

    def _show_password_not_implemented(self):
        QMessageBox.information(self, "Security", "Password management is not implemented yet.")

    def _on_change_password_clicked(self):
        try:
            from resources.api_client import ApiError, api_change_password
        except Exception:
            QMessageBox.warning(self, "Security", "API client is not available.")
            return

        dlg = _ChangePasswordDialog(self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return

        current_pw, new_pw, confirm_pw = dlg.values()
        if not current_pw or not new_pw:
            QMessageBox.warning(self, "Security", "Please fill in all fields.")
            return
        if new_pw != confirm_pw:
            QMessageBox.warning(self, "Security", "New passwords do not match.")
            return
        policy_err = self._password_policy_error(new_pw)
        if policy_err:
            QMessageBox.warning(self, "Security", policy_err)
            return

        try:
            api_change_password(current_pw, new_pw)
            try:
                self.pwd_meta.setText("Last updated: just now")
            except Exception:
                pass
            QMessageBox.information(self, "Security", "Password updated successfully.")
        except ApiError as e:
            status = getattr(e, "status", None)
            msg = str(e).strip()
            if (status == 404 and msg.lower() == "not found") or (status == 401 and "incorrect" in msg.lower()):
                msg = "Password incorrect."
            QMessageBox.warning(self, "Security", msg)
        except Exception as e:
            QMessageBox.warning(self, "Security", f"Could not update password: {e}")

    def _password_policy_error(self, password: str) -> Optional[str]:
        pw = str(password or "")
        if len(pw) < 8:
            return "Password must be at least 8 characters."
        if any(ch.isspace() for ch in pw):
            return "Password must not contain spaces."
        has_upper = any(ch.isupper() for ch in pw)
        has_lower = any(ch.islower() for ch in pw)
        has_digit = any(ch.isdigit() for ch in pw)
        has_special = any((not ch.isalnum()) and (not ch.isspace()) for ch in pw)
        if not (has_upper and has_lower and has_digit and has_special):
            return "Password must include: 1 uppercase, 1 lowercase, 1 number, and 1 special character."
        return None

    # ---------- Compatibility ----------
    def update_statistics(self, stats):
        # Kept for API compatibility; the redesigned profile page does not show stat cards.
        return
