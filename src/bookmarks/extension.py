from __future__ import annotations

import json
import os
import time
import typing
from bisect import bisect_left, bisect_right
from dataclasses import dataclass, asdict
from pathlib import Path

from biscuit.extensions import Extension

if typing.TYPE_CHECKING:
    from biscuit.api import ExtensionsAPI


@dataclass
class Bookmark:
    file_path: str
    line_number: int
    label: str | None = None
    preview: str = ""
    timestamp: float = 0.0

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> Bookmark:
        return cls(
            file_path=data.get("file_path", ""),
            line_number=int(data.get("line_number", 1)),
            label=data.get("label"),
            preview=data.get("preview", ""),
            timestamp=float(data.get("timestamp", 0.0)),
        )


class Bookmarks(Extension):
    """Set, clear, and jump between line bookmarks with file registry and persistence."""

    def __init__(self, api: ExtensionsAPI) -> None:
        super().__init__(api)
        self.api = api
        # Storage: canonical file path -> dict of line_number -> Bookmark
        self.registry: dict[str, dict[int, Bookmark]] = {}
        self.statusbar_button = None
        self.storage_file: Path | None = None

        ws_dir = getattr(getattr(self.api, "base", None), "active_directory", None)
        if isinstance(ws_dir, (str, Path)):
            try:
                p = Path(ws_dir)
                if p.is_dir():
                    self.storage_file = p / ".biscuit" / "bookmarks.json"
                    self.load_from_file()
            except Exception:
                pass

        if hasattr(self.api, "logger") and self.api.logger:
            self.api.logger.info("Bookmarks extension initialised.")

    def _normalize_path(self, file_path: str) -> str:
        return str(Path(file_path).resolve()) if file_path else ""

    def toggle_bookmark(
        self,
        file_path: str,
        line_number: int,
        label: str | None = None,
        preview: str = "",
    ) -> bool:
        """Toggle bookmark at line_number in file. Returns True if added, False if removed."""
        norm_path = self._normalize_path(file_path)
        if not norm_path or line_number < 1:
            return False

        file_bookmarks = self.registry.setdefault(norm_path, {})
        if line_number in file_bookmarks:
            del file_bookmarks[line_number]
            if not file_bookmarks:
                del self.registry[norm_path]
            added = False
        else:
            file_bookmarks[line_number] = Bookmark(
                file_path=norm_path,
                line_number=line_number,
                label=label,
                preview=preview.strip(),
                timestamp=time.time(),
            )
            added = True

        self.update_statusbar()
        self.save_to_file()
        return added

    def add_bookmark(
        self,
        file_path: str,
        line_number: int,
        label: str | None = None,
        preview: str = "",
    ) -> Bookmark:
        """Explicitly add or update a bookmark."""
        norm_path = self._normalize_path(file_path)
        file_bookmarks = self.registry.setdefault(norm_path, {})
        bm = Bookmark(
            file_path=norm_path,
            line_number=line_number,
            label=label,
            preview=preview.strip(),
            timestamp=time.time(),
        )
        file_bookmarks[line_number] = bm
        self.update_statusbar()
        self.save_to_file()
        return bm

    def remove_bookmark(self, file_path: str, line_number: int) -> bool:
        """Remove a bookmark if present."""
        norm_path = self._normalize_path(file_path)
        file_bookmarks = self.registry.get(norm_path)
        if file_bookmarks and line_number in file_bookmarks:
            del file_bookmarks[line_number]
            if not file_bookmarks:
                del self.registry[norm_path]
            self.update_statusbar()
            self.save_to_file()
            return True
        return False

    def has_bookmark(self, file_path: str, line_number: int) -> bool:
        """Check if a bookmark exists at line_number in file."""
        norm_path = self._normalize_path(file_path)
        return line_number in self.registry.get(norm_path, {})

    def get_bookmarks(self, file_path: str | None = None) -> list[Bookmark]:
        """Return sorted list of bookmarks for a file or entire workspace."""
        if file_path is not None:
            norm_path = self._normalize_path(file_path)
            file_bms = self.registry.get(norm_path, {})
            return [file_bms[line] for line in sorted(file_bms.keys())]

        all_bms: list[Bookmark] = []
        for norm_path in sorted(self.registry.keys()):
            file_bms = self.registry[norm_path]
            for line in sorted(file_bms.keys()):
                all_bms.append(file_bms[line])
        return all_bms

    def clear_bookmarks(self, file_path: str | None = None) -> None:
        """Clear bookmarks for a specific file, or all bookmarks if file_path is None."""
        if file_path is not None:
            norm_path = self._normalize_path(file_path)
            self.registry.pop(norm_path, None)
        else:
            self.registry.clear()
        self.update_statusbar()
        self.save_to_file()

    def count(self, file_path: str | None = None) -> int:
        """Return total count of bookmarks in file or workspace."""
        return len(self.get_bookmarks(file_path))

    def next_bookmark(self, current_file: str, current_line: int) -> Bookmark | None:
        """Compute the next sequential bookmark with cycle wrap-around."""
        all_files = sorted(self.registry.keys())
        if not all_files:
            return None

        norm_file = self._normalize_path(current_file)

        if norm_file in self.registry:
            file_lines = sorted(self.registry[norm_file].keys())
            idx = bisect_right(file_lines, current_line)
            if idx < len(file_lines):
                return self.registry[norm_file][file_lines[idx]]

            # Wrap to next file
            cur_file_idx = all_files.index(norm_file)
            for i in range(1, len(all_files) + 1):
                next_f = all_files[(cur_file_idx + i) % len(all_files)]
                lines = sorted(self.registry[next_f].keys())
                if lines:
                    return self.registry[next_f][lines[0]]
        else:
            first_f = all_files[0]
            lines = sorted(self.registry[first_f].keys())
            if lines:
                return self.registry[first_f][lines[0]]

        return None

    def previous_bookmark(self, current_file: str, current_line: int) -> Bookmark | None:
        """Compute the previous bookmark with reverse cycle wrap-around."""
        all_files = sorted(self.registry.keys())
        if not all_files:
            return None

        norm_file = self._normalize_path(current_file)

        if norm_file in self.registry:
            file_lines = sorted(self.registry[norm_file].keys())
            idx = bisect_left(file_lines, current_line)
            if idx > 0:
                return self.registry[norm_file][file_lines[idx - 1]]

            # Wrap to previous file
            cur_file_idx = all_files.index(norm_file)
            for i in range(1, len(all_files) + 1):
                prev_f = all_files[(cur_file_idx - i) % len(all_files)]
                lines = sorted(self.registry[prev_f].keys())
                if lines:
                    return self.registry[prev_f][lines[-1]]
        else:
            last_f = all_files[-1]
            lines = sorted(self.registry[last_f].keys())
            if lines:
                return self.registry[last_f][lines[-1]]

        return None

    def save_to_file(self, target_path: str | Path | None = None) -> None:
        """Persist bookmarks registry to a JSON file."""
        target = Path(target_path) if target_path else self.storage_file
        if not target:
            return
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            data = [bm.to_dict() for bm in self.get_bookmarks()]
            with open(target, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
        except Exception:
            pass

    def load_from_file(self, target_path: str | Path | None = None) -> None:
        """Restore bookmarks registry from a JSON file."""
        target = Path(target_path) if target_path else self.storage_file
        if not target or not target.is_file():
            return
        try:
            with open(target, "r", encoding="utf-8") as f:
                data = json.load(f)
            self.registry.clear()
            for item in data:
                bm = Bookmark.from_dict(item)
                if bm.file_path and bm.line_number >= 1:
                    norm = self._normalize_path(bm.file_path)
                    self.registry.setdefault(norm, {})[bm.line_number] = bm
            self.update_statusbar()
        except Exception:
            pass

    def update_statusbar(self) -> None:
        """Update active bookmark counter on statusbar."""
        if not self.statusbar_button:
            return
        total = self.count()
        text = f"Bookmarks: {total}"
        if hasattr(self.statusbar_button, "change_text"):
            self.statusbar_button.change_text(text)
        elif hasattr(self.statusbar_button, "set_text"):
            self.statusbar_button.set_text(text)

    def _get_active_editor_info(self) -> tuple[str, int, str]:
        editor = getattr(getattr(self.api, "editors", None), "active_editor", None)
        path = str(getattr(editor, "path", ""))
        cur_line = 1
        preview = ""

        text_widget = getattr(editor, "text", None) or getattr(getattr(editor, "content", None), "text", None)
        if text_widget:
            if hasattr(text_widget, "index"):
                try:
                    cur_line = int(str(text_widget.index("insert")).split(".")[0])
                except Exception:
                    cur_line = 1
            if hasattr(text_widget, "get"):
                try:
                    preview = text_widget.get(f"{cur_line}.0", f"{cur_line}.end").strip()
                except Exception:
                    preview = ""

        return path, cur_line, preview

    def toggle_active(self) -> bool:
        """Command: Toggle bookmark on current line in active editor."""
        path, line, preview = self._get_active_editor_info()
        if not path:
            return False
        added = self.toggle_bookmark(path, line, preview=preview)
        msg = f"Bookmark added at line {line}" if added else f"Bookmark removed from line {line}"
        if hasattr(self.api, "notifications") and self.api.notifications:
            self.api.notifications.info(msg)
        return added

    def jump_to_next(self) -> Bookmark | None:
        """Command: Jump to next bookmark."""
        path, line, _ = self._get_active_editor_info()
        target = self.next_bookmark(path, line)
        if target:
            if hasattr(self.api, "editors") and hasattr(self.api.editors, "open_editor"):
                self.api.editors.open_editor(target.file_path)
            if hasattr(self.api, "notifications") and self.api.notifications:
                self.api.notifications.info(f"Jumped to bookmark: {Path(target.file_path).name}:{target.line_number}")
        return target

    def jump_to_prev(self) -> Bookmark | None:
        """Command: Jump to previous bookmark."""
        path, line, _ = self._get_active_editor_info()
        target = self.previous_bookmark(path, line)
        if target:
            if hasattr(self.api, "editors") and hasattr(self.api.editors, "open_editor"):
                self.api.editors.open_editor(target.file_path)
            if hasattr(self.api, "notifications") and self.api.notifications:
                self.api.notifications.info(f"Jumped to bookmark: {Path(target.file_path).name}:{target.line_number}")
        return target

    def install(self) -> None:
        """Register commands and statusbar for Bookmarks."""
        if hasattr(self.api, "commands") and hasattr(self.api.commands, "register_command"):
            self.api.commands.register_command("bookmarks.toggle", self.toggle_active)
            self.api.commands.register_command("bookmarks.next", self.jump_to_next)
            self.api.commands.register_command("bookmarks.prev", self.jump_to_prev)
            self.api.commands.register_command(
                "bookmarks.clear_file",
                lambda: self.clear_bookmarks(self._get_active_editor_info()[0])
            )
            self.api.commands.register_command("bookmarks.clear_all", lambda: self.clear_bookmarks())

        if hasattr(self.api, "statusbar") and hasattr(self.api.statusbar, "add_button"):
            try:
                self.statusbar_button = self.api.statusbar.add_button(
                    text="Bookmarks: 0",
                    callback=self.jump_to_next,
                    description="Bookmarks: Jump to next bookmark",
                )
                if hasattr(self.statusbar_button, "show"):
                    self.statusbar_button.show()
            except Exception:
                pass

        if hasattr(self.api, "logger") and self.api.logger:
            self.api.logger.info("Bookmarks extension installed.")
