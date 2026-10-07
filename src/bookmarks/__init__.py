from __future__ import annotations

import typing

from .extension import Bookmarks

if typing.TYPE_CHECKING:
    from biscuit.api import ExtensionsAPI


def setup(api: "ExtensionsAPI") -> Bookmarks:
    """Entrypoint for Biscuit to load the extension."""
    ext = Bookmarks(api)
    api.register("bookmarks", ext)
    return ext
