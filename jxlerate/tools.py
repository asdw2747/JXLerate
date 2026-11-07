# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Prisma Chill
"""Helpers for invoking optional external image tools."""

from __future__ import annotations

import subprocess


def run_tool(cmd, timeout):
    """Run a tool quietly; return True on success, False on any failure."""
    try:
        subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL, timeout=timeout)
        return True
    except Exception:
        return False
