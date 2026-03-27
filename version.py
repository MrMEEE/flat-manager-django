#!/usr/bin/env python3
"""
flat-manager-django version information.

This file is the single source of truth for the application version.
It is updated automatically by tools/release.py.
"""

# Version format: MAJOR.MINOR.PATCH
VERSION = "0.1.100"
BUILD_DATE = "2026-03-27"


def get_version() -> str:
    return VERSION


def get_version_tuple() -> tuple[int, int, int]:
    major, minor, patch = VERSION.split(".")
    return int(major), int(minor), int(patch)
