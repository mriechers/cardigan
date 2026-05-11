"""
Cardigan API

FastAPI-based control plane for the Cardigan system.

Version is sourced from git tags via setuptools_scm at build time.
The build writes api/_version.py, which we import below. If the file
is missing (e.g., editable checkout without a build run), fall back
to a sentinel so imports never fail.

See docs/VERSIONING.md for the release/bump policy.
"""

try:
    from api._version import __version__  # type: ignore[import-not-found]
except ImportError:
    __version__ = "0.0.0+unknown"
