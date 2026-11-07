# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Prisma Chill
"""GUI configuration, theme palette, and desktop theme detection."""

from __future__ import annotations

import json
import os
import shutil
import subprocess

CONFIG_DIR = os.path.join(os.path.expanduser("~"), ".config", "jxlerate")
CONFIG_PATH = os.path.join(CONFIG_DIR, "config.json")
OLD_CONFIG_PATH = os.path.join(
    os.path.expanduser("~"), ".config", "strip-images", "config.json")

THEMES = {
    "dark": {
        "window": "#232629",
        "base": "#31363b",
        "alt_base": "#2b2e32",
        "text": "#eff0f1",
        "muted": "#a0a4a8",
        "disabled": "#6d7176",
        "select_bg": "#2a82da",
        "select_fg": "#ffffff",
        "border": "#4f565c",
        "button": "#3b4045",
        "button_active": "#4a5056",
        "trough": "#1b1e20",
        "accent": "#3daee9",
        "log_bg": "#1b1e20",
    },
    "light": {
        "window": "#f0f0f0",
        "base": "#ffffff",
        "alt_base": "#f7f7f7",
        "text": "#1a1a1a",
        "muted": "#555555",
        "disabled": "#9a9a9a",
        "select_bg": "#308cc6",
        "select_fg": "#ffffff",
        "border": "#c8c8c8",
        "button": "#e6e6e6",
        "button_active": "#dcdcdc",
        "trough": "#d9d9d9",
        "accent": "#308cc6",
        "log_bg": "#ffffff",
    },
}


def migrate_config():
    """Carry settings over from the old strip-images config location."""
    if os.path.exists(OLD_CONFIG_PATH) and not os.path.exists(CONFIG_PATH):
        try:
            os.makedirs(CONFIG_DIR, exist_ok=True)
            shutil.copy2(OLD_CONFIG_PATH, CONFIG_PATH)
        except Exception:
            pass


def load_config():
    migrate_config()
    try:
        with open(CONFIG_PATH, encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            if data.get("theme") not in ("auto", "light", "dark"):
                data["theme"] = "auto"
            if not isinstance(data.get("options"), dict):
                data["options"] = {}
            return data
    except Exception:
        pass
    return {"theme": "auto", "options": {}}


def save_config(config):
    try:
        os.makedirs(CONFIG_DIR, exist_ok=True)
        tmp = CONFIG_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(config, f, indent=2)
        os.replace(tmp, CONFIG_PATH)
    except Exception:
        pass


def detect_system_theme() -> str:
    """Return 'dark' or 'light' based on the desktop's preference."""
    try:
        result = subprocess.run(
            ["gsettings", "get", "org.gnome.desktop.interface", "color-scheme"],
            capture_output=True, text=True, timeout=1.5)
        value = result.stdout.strip().strip("'").lower()
        if "dark" in value:
            return "dark"
        if value in ("default", "prefer-light", "light"):
            return "light"
    except Exception:
        pass

    try:
        path = os.path.expanduser("~/.config/gtk-3.0/settings.ini")
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line.startswith("gtk-application-prefer-dark-theme"):
                    _, _, value = line.partition("=")
                    if value.strip().lower() in ("1", "true", "yes"):
                        return "dark"
    except Exception:
        pass

    try:
        if "dark" in os.environ.get("GTK_THEME", "").lower():
            return "dark"
    except Exception:
        pass

    return "light"
