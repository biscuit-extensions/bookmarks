from __future__ import annotations

import sys
import tempfile
import tomllib
from pathlib import Path
from unittest.mock import MagicMock

# Fix sys.path to ensure module is importable from extension root
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.bookmarks import setup
from src.bookmarks.extension import Bookmark, Bookmarks


class TestBookmarksManifest:
    def test_manifest_schema_and_dependencies(self) -> None:
        manifest_path = Path(__file__).parent.parent / "extension.toml"
        assert manifest_path.exists(), "extension.toml must exist"
        manifest = tomllib.loads(manifest_path.read_text(encoding="utf-8"))

        ext_meta = manifest.get("extension", {})
        assert ext_meta.get("id") == "bookmarks"
        assert ext_meta.get("author") == "tomlin7"
        assert ext_meta.get("version") == "0.2.0"

        deps = manifest.get("dependencies", {})
        assert isinstance(deps.get("python"), list)
        assert isinstance(deps.get("tools"), list)

        contributes = manifest.get("contributes", {})
        commands = contributes.get("commands", [])
        assert len(commands) >= 5
        cmd_ids = [cmd["id"] for cmd in commands]
        assert "bookmarks.toggle" in cmd_ids
        assert "bookmarks.next" in cmd_ids
        assert "bookmarks.prev" in cmd_ids
        assert "bookmarks.clear_file" in cmd_ids
        assert "bookmarks.clear_all" in cmd_ids


class TestBookmarksLogic:
    def test_setup_and_registration(self, app_instance) -> None:
        ext = setup(app_instance.api)
        assert ext is not None
        assert isinstance(ext, Bookmarks)

    def test_bookmark_dataclass_serialization(self) -> None:
        bm = Bookmark(
            file_path="/workspace/main.py",
            line_number=42,
            label="Important function",
            preview="def process():",
            timestamp=123456.78,
        )
        data = bm.to_dict()
        assert data["file_path"] == "/workspace/main.py"
        assert data["line_number"] == 42
        assert data["label"] == "Important function"
        assert data["preview"] == "def process():"

        restored = Bookmark.from_dict(data)
        assert restored.file_path == bm.file_path
        assert restored.line_number == bm.line_number
        assert restored.label == bm.label
        assert restored.preview == bm.preview

    def test_toggle_bookmark(self, app_instance) -> None:
        ext = Bookmarks(app_instance.api)
        test_file = "/workspace/file1.py"

        # Initially toggle adds bookmark
        assert ext.toggle_bookmark(test_file, 10, label="Start") is True
        assert ext.has_bookmark(test_file, 10) is True
        assert ext.count(test_file) == 1

        # Toggling same line removes bookmark
        assert ext.toggle_bookmark(test_file, 10) is False
        assert ext.has_bookmark(test_file, 10) is False
        assert ext.count(test_file) == 0

        # Invalid line number
        assert ext.toggle_bookmark(test_file, 0) is False
        assert ext.toggle_bookmark("", 5) is False

    def test_add_and_remove_bookmark(self, app_instance) -> None:
        ext = Bookmarks(app_instance.api)
        test_file = "/workspace/file1.py"

        bm = ext.add_bookmark(test_file, 15, label="Bug fix", preview="x = x + 1")
        assert bm.line_number == 15
        assert ext.has_bookmark(test_file, 15) is True

        # Remove existing
        assert ext.remove_bookmark(test_file, 15) is True
        assert ext.has_bookmark(test_file, 15) is False

        # Remove nonexistent
        assert ext.remove_bookmark(test_file, 999) is False

    def test_get_and_clear_bookmarks(self, app_instance) -> None:
        ext = Bookmarks(app_instance.api)
        f1 = "/workspace/a.py"
        f2 = "/workspace/b.py"

        ext.add_bookmark(f1, 20)
        ext.add_bookmark(f1, 5)
        ext.add_bookmark(f2, 10)

        # File bookmarks sorted by line number
        f1_bms = ext.get_bookmarks(f1)
        assert len(f1_bms) == 2
        assert f1_bms[0].line_number == 5
        assert f1_bms[1].line_number == 20

        # Total workspace bookmarks
        assert ext.count() == 3

        # Clear single file
        ext.clear_bookmarks(f1)
        assert ext.count(f1) == 0
        assert ext.count(f2) == 1

        # Clear all
        ext.clear_bookmarks()
        assert ext.count() == 0

    def test_navigation_next_bookmark(self, app_instance) -> None:
        ext = Bookmarks(app_instance.api)
        f1 = "/workspace/file1.py"
        f2 = "/workspace/file2.py"

        ext.add_bookmark(f1, 10)
        ext.add_bookmark(f1, 30)
        ext.add_bookmark(f2, 5)

        # Same file advance
        target = ext.next_bookmark(f1, 10)
        assert target is not None
        assert target.line_number == 30

        # Advance beyond file line -> jumps to f2
        target = ext.next_bookmark(f1, 30)
        assert target is not None
        assert target.line_number == 5

        # Cycle wrap around back to f1 line 10
        target = ext.next_bookmark(f2, 5)
        assert target is not None
        assert target.line_number == 10

    def test_navigation_prev_bookmark(self, app_instance) -> None:
        ext = Bookmarks(app_instance.api)
        f1 = "/workspace/file1.py"
        f2 = "/workspace/file2.py"

        ext.add_bookmark(f1, 10)
        ext.add_bookmark(f1, 30)
        ext.add_bookmark(f2, 50)

        # Same file reverse
        target = ext.previous_bookmark(f1, 30)
        assert target is not None
        assert target.line_number == 10

        # Reverse from first bookmark in f1 -> wraps to f2 line 50
        target = ext.previous_bookmark(f1, 10)
        assert target is not None
        assert target.line_number == 50

        # Reverse from f2 line 50 -> jumps back to f1 line 30
        target = ext.previous_bookmark(f2, 50)
        assert target is not None
        assert target.line_number == 30

    def test_navigation_empty_registry(self, app_instance) -> None:
        ext = Bookmarks(app_instance.api)
        assert ext.next_bookmark("/workspace/none.py", 1) is None
        assert ext.previous_bookmark("/workspace/none.py", 1) is None

    def test_persistence_save_and_load(self, app_instance) -> None:
        ext = Bookmarks(app_instance.api)
        with tempfile.TemporaryDirectory() as tmp_dir:
            json_file = Path(tmp_dir) / "test_bookmarks.json"
            f_path = str(Path(tmp_dir) / "app.py")

            ext.add_bookmark(f_path, 12, label="Entry", preview="main()")
            ext.add_bookmark(f_path, 45, label="Exit", preview="return 0")
            ext.save_to_file(json_file)

            assert json_file.exists()

            # Create fresh instance and load
            ext2 = Bookmarks(app_instance.api)
            ext2.load_from_file(json_file)
            assert ext2.count() == 2
            bms = ext2.get_bookmarks(f_path)
            assert len(bms) == 2
            assert bms[0].line_number == 12
            assert bms[0].label == "Entry"

    def test_statusbar_and_editor_commands(self, app_instance) -> None:
        ext = Bookmarks(app_instance.api)
        ext.install()

        # Check statusbar button exists
        assert ext.statusbar_button is not None

        # Mock editor
        mock_editor = MagicMock()
        mock_editor.path = "/workspace/test_file.py"
        mock_text = MagicMock()
        mock_text.index.return_value = "10.0"
        mock_text.get.return_value = "print('hello')"
        mock_editor.text = mock_text
        app_instance.api.editors.active_editor = mock_editor

        # Test toggle_active
        added = ext.toggle_active()
        assert added is True
        assert ext.has_bookmark("/workspace/test_file.py", 10) is True

        # Test jump_to_next
        orig_open_editor = getattr(app_instance.api.editors, "open_editor", None)
        app_instance.api.editors.open_editor = MagicMock()
        try:
            ext.add_bookmark("/workspace/test_file.py", 25)
            target = ext.jump_to_next()
            assert target is not None
            assert target.line_number == 25
            app_instance.api.editors.open_editor.assert_called_with(target.file_path)
        finally:
            if orig_open_editor:
                app_instance.api.editors.open_editor = orig_open_editor

        # Test jump_to_prev (simulate cursor now at line 25)
        mock_text.index.return_value = "25.0"
        target_prev = ext.jump_to_prev()
        assert target_prev is not None
        assert target_prev.line_number == 10