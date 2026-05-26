from __future__ import annotations

from typing import Optional

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QPainter, QPainterPath, QPixmap
from PyQt6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QTextEdit,
    QVBoxLayout,
)

from resources.api_client import api_get_user_avatar
from resources.theme import get_theme, rgba


class UserProfileDialog(QDialog):
    def __init__(self, parent, *, theme: str, profile: dict, avatar_bytes: bytes | None = None):
        super().__init__(parent)
        self.setWindowTitle("User Profile")
        self.setMinimumWidth(540)
        self.current_theme = theme or "Light"

        c = get_theme(self.current_theme)
        self.setStyleSheet(f"QDialog {{ background: {c['bg']}; }}")

        root = QVBoxLayout(self)
        root.setContentsMargins(18, 18, 18, 18)
        root.setSpacing(12)

        user_id = None
        try:
            user_id = int(profile.get("user_id")) if isinstance(profile, dict) else None
        except Exception:
            user_id = None

        username = str(profile.get("username") or "").strip() if isinstance(profile, dict) else ""
        if not username:
            username = "User"

        header = QFrame()
        header.setStyleSheet(
            f"QFrame {{ background: {rgba(c['card_alt'], 0.85)}; border: 1px solid {rgba(c['border'], 0.7)}; border-radius: 16px; }}"
        )
        hl = QHBoxLayout(header)
        hl.setContentsMargins(14, 12, 14, 12)
        hl.setSpacing(12)

        avatar = QLabel()
        avatar.setFixedSize(56, 56)
        avatar.setAlignment(Qt.AlignmentFlag.AlignCenter)
        avatar.setStyleSheet(f"border-radius: 28px; background: {rgba(c['accent_soft'], 0.9)};")
        pix = self._load_avatar_pixmap(user_id, avatar_bytes)
        if pix is not None and not pix.isNull():
            avatar.setPixmap(self._circle_pixmap(pix, 56))
            avatar.setStyleSheet("background: transparent; border-radius: 28px;")
        else:
            avatar.setText((username[:1] or "U").upper())
            avatar.setStyleSheet(
                f"border-radius: 28px; background: {rgba(c['accent'], 0.25)}; color: {c['text']}; font-weight: 900; font-size: 18px;"
            )

        hl.addWidget(avatar)

        title_col = QVBoxLayout()
        title_col.setSpacing(2)
        title = QLabel(username)
        title.setStyleSheet(f"color: {c['text']}; font-size: 18px; font-weight: 900;")
        title_col.addWidget(title)

        role = str(profile.get("role") or "").strip() if isinstance(profile, dict) else ""
        dept = str(profile.get("department") or "").strip() if isinstance(profile, dict) else ""
        join_date = str(profile.get("join_date") or "").strip() if isinstance(profile, dict) else ""
        skills = profile.get("skills") if isinstance(profile, dict) else None
        skills_txt = ", ".join([str(s).strip() for s in (skills or []) if str(s).strip()]) if isinstance(skills, list) else ""

        meta_parts = []
        if role:
            meta_parts.append(f"Role: {role}")
        if dept:
            meta_parts.append(f"Department: {dept}")
        meta = QLabel("  •  ".join(meta_parts) if meta_parts else " ")
        meta.setWordWrap(True)
        meta.setStyleSheet(f"color: {c['sub']}; font-size: 11px; font-weight: 700;")
        title_col.addWidget(meta)

        extra_parts = []
        if join_date:
            extra_parts.append(f"Join date: {join_date}")
        extra_parts.append(f"Skills: {skills_txt or '-'}")
        extra = QLabel("  •  ".join(extra_parts))
        extra.setWordWrap(True)
        extra.setStyleSheet(f"color: {c['sub']}; font-size: 10px; font-weight: 650;")
        title_col.addWidget(extra)

        hl.addLayout(title_col, 1)
        root.addWidget(header)

        def add_block(label: str, text: str):
            lbl = QLabel(label)
            lbl.setStyleSheet(f"color: {c['text']}; font-size: 11px; font-weight: 900; margin-top: 2px;")
            root.addWidget(lbl)
            box = QTextEdit()
            box.setReadOnly(True)
            box.setPlainText(text or "")
            box.setMinimumHeight(92)
            box.setStyleSheet(
                f"background: {c['input_bg']}; border: 1px solid {rgba(c['border'], 0.7)}; border-radius: 12px; "
                f"padding: 8px 10px; color: {c['text']}; font-size: 11px;"
            )
            root.addWidget(box)

        add_block("Bio", str(profile.get("bio") or "").strip() if isinstance(profile, dict) else "-")
        add_block("Projects / Work", str(profile.get("projects") or "").strip() if isinstance(profile, dict) else "-")

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.button(QDialogButtonBox.StandardButton.Close).clicked.connect(self.accept)
        root.addWidget(buttons)

    def _load_avatar_pixmap(self, user_id: Optional[int], avatar_bytes: bytes | None) -> QPixmap | None:
        data = avatar_bytes
        if not data and user_id:
            try:
                data = api_get_user_avatar(int(user_id))
            except Exception:
                data = None
        if not data:
            return None
        pixmap = QPixmap()
        try:
            ok = pixmap.loadFromData(data)
        except Exception:
            ok = False
        if not ok or pixmap.isNull():
            return None
        return pixmap

    def _circle_pixmap(self, source: QPixmap, size: int) -> QPixmap:
        try:
            size_i = int(size)
        except Exception:
            size_i = 56
        size_i = max(8, size_i)
        scaled = source.scaled(
            size_i,
            size_i,
            Qt.AspectRatioMode.KeepAspectRatioByExpanding,
            Qt.TransformationMode.SmoothTransformation,
        )
        result = QPixmap(size_i, size_i)
        result.fill(Qt.GlobalColor.transparent)
        painter = QPainter(result)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        path = QPainterPath()
        path.addEllipse(0, 0, size_i, size_i)
        painter.setClipPath(path)
        dx = int((scaled.width() - size_i) / 2)
        dy = int((scaled.height() - size_i) / 2)
        painter.drawPixmap(-dx, -dy, scaled)
        painter.end()
        return result

