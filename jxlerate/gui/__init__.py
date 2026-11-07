# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Prisma Chill
"""GUI entry point.

Tkinter is imported lazily so the worker bridge and the core package remain
testable on headless systems.
"""

from __future__ import annotations


def main():
    from .app import App

    app = App()
    app.mainloop()
