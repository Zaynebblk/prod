def _hex_to_rgb(value):
    value = value.lstrip("#")
    return tuple(int(value[i:i+2], 16) for i in (0, 2, 4))


def rgba(hex_color, alpha):
    r, g, b = _hex_to_rgb(hex_color)
    try:
        a_val = float(alpha)
    except Exception:
        a_val = alpha
    if isinstance(a_val, float) and 0 <= a_val <= 1:
        a_val = int(round(a_val * 255))
    return f"rgba({r}, {g}, {b}, {int(a_val)})"


FONT_FAMILY = "Poppins"


PALETTE_LIGHT = {
    "bg": "#F8F6F2",
    "surface": "#FFFFFF",
    "surface_alt": "#EEF5FA",
    "border": "#BAD2E0",
    "text": "#113356",
    "sub": "#47617C",
    "accent": "#3078CD",
    "accent2": "#82AFF2",
    "accent_soft": "#E6F0FA",
    "chip": "#E6F0FA",
    "chip_text": "#25456B",
    "deep": "#25456B",
    "abyss": "#113356",
    "shadow": "#DCE7FF",
    "heat0": "#BAD2E0",
    "heat1": "#82AFF2",
    "heat2": "#3078CD",
    "heat3": "#25456B",
    "input_bg": "#FFFFFF",
}


PALETTE_DARK = {
    "bg": "#0B1A2B",
    "surface": "#0F2238",
    "surface_alt": "#112844",
    "border": "#25456B",
    "text": "#E6EEF5",
    "sub": "#BAD2E0",
    "accent": "#82AFF2",
    "accent2": "#3078CD",
    "accent_soft": "#1B2F4D",
    "chip": "#1B2F4D",
    "chip_text": "#BAD2E0",
    "deep": "#25456B",
    "abyss": "#0B1A2B",
    "shadow": "#071324",
    "heat0": "#1B2F4D",
    "heat1": "#25456B",
    "heat2": "#3078CD",
    "heat3": "#82AFF2",
    "input_bg": "#1B2F4D",
}


def get_theme(theme_name):
    base = PALETTE_DARK if theme_name == "Dark" else PALETTE_LIGHT
    base = dict(base)
    overrides = _load_theme_overrides()
    if overrides.get("accent"):
        base["accent"] = overrides["accent"]
    if overrides.get("accent2"):
        base["accent2"] = overrides["accent2"]
    is_dark = theme_name == "Dark"
    primary_grad = (
        f"qlineargradient(x1:0, y1:0, x2:1, y2:1, "
        f"stop:0 {base['accent2']}, stop:1 {base['deep']})"
    )
    colors = {
        "bg": base["bg"],
        "card": base["surface"],
        "card_alt": base["surface_alt"],
        "border": base["border"],
        "text": base["text"],
        "sub": base["sub"],
        "accent": base["accent"],
        "accent2": base["accent2"],
        "accent_soft": base["accent_soft"],
        "chip": base["chip"],
        "chip_text": base["chip_text"],
        "deep": base["deep"],
        "abyss": base["abyss"],
        "primary": base["accent"],
        "primary_gradient": primary_grad,
        "secondary": base["surface_alt"],
        "shadow": base["shadow"],
        "heat0": base["heat0"],
        "heat1": base["heat1"],
        "heat2": base["heat2"],
        "heat3": base["heat3"],
        "velocity": base["accent"],
        "capacity": base["deep"],
        "chart_grid": base["border"],
        "good": "#34D399" if is_dark else "#22C55E",
        "bad": "#F87171" if is_dark else "#EF4444",
        "card_selected": base["accent_soft"],
        "card_selected_border": base["accent2"],
        "input_bg": base["input_bg"],
        "input_border": base["border"],
        "primary_soft": rgba(base["accent"], 0.18 if is_dark else 0.12),
        "primary_soft_border": rgba(base["accent"], 0.35 if is_dark else 0.25),
    }
    return colors
import json
import os


_THEME_OVERRIDE_CACHE = None
_THEME_OVERRIDE_MTIME = None


def _settings_path():
    root_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(root_dir, "settings.json")


def _load_theme_overrides():
    global _THEME_OVERRIDE_CACHE, _THEME_OVERRIDE_MTIME
    path = _settings_path()
    try:
        mtime = os.path.getmtime(path)
    except Exception:
        _THEME_OVERRIDE_CACHE = {}
        _THEME_OVERRIDE_MTIME = None
        return {}

    if _THEME_OVERRIDE_CACHE is not None and _THEME_OVERRIDE_MTIME == mtime:
        return _THEME_OVERRIDE_CACHE

    data = {}
    try:
        with open(path, "r") as f:
            data = json.load(f)
    except Exception:
        data = {}

    overrides = {}
    accent = str(data.get("accent_color") or "").strip()
    accent2 = str(data.get("accent2_color") or "").strip()
    if accent.startswith("#") and len(accent) == 7:
        overrides["accent"] = accent
    if accent2.startswith("#") and len(accent2) == 7:
        overrides["accent2"] = accent2

    _THEME_OVERRIDE_CACHE = overrides
    _THEME_OVERRIDE_MTIME = mtime
    return overrides
