"""Default theme tokens applied to login + lock QML."""

from __future__ import annotations

from usurface.schema import Fonts, Login, Lock


def default_fonts() -> Fonts:
    return Fonts()


def default_login() -> Login:
    return Login()


def default_lock() -> Lock:
    return Lock()
