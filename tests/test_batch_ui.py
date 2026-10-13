"""Offscreen checks for queue ordering, persistence safety and background jobs."""
import json
import os
import tempfile
import time
import unittest
from unittest.mock import patch
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PIL import Image
from PySide6.QtCore import QSettings, QTimer
from PySide6.QtWidgets import QApplication

from lumaview.ui.batch_window import BatchWindow


class BatchWindowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.folder = Path(self.temp.name)
        settings = QSettings(str(self.folder / "settings.ini"), QSettings.Format.IniFormat)
        with patch('lumaview.ui.batch_window.QSettings',return_value=settings):
            self.window = BatchWindow()
        self.window.show_preview.setChecked(False)
        self.window.mode_buttons["convert"].setChecked(True)
        self.window.use_advanced.setChecked(False)
        self.window.output_dir.setText(str(self.folder / "output"))
        self.window._browse_to(self.folder)

    def tearDown(self):
        if self.window._cancel_event:
            self.window._cancel_event.set()
        self._wait(lambda: not self.window.is_busy())
        self.window.pool.waitForDone(5000)
        self.window.close()
        self.app.processEvents()
        self.temp.cleanup()

    def _wait(self, predicate, timeout=5):
        deadline = time.monotonic() + timeout
        while not predicate() and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(0.005)
        self.assertTrue(predicate(), "Qt background operation timed out")

    def _image(self, name):
        path = self.folder / name
        Image.new("RGB", (128, 96), "cornflowerblue").save(path)
        return path

    def test_queue_deduplicates_and_moves_contiguous_selection(self):
        paths = [self._image(f"image{number}.png") for number in range(4)]
        self.window.add_paths(paths + [paths[0]])
        self.assertEqual(self.window._paths(), paths)
        self.window.queue.clearSelection()
        self.window.queue.item(1).setSelected(True)
        self.window.queue.item(2).setSelected(True)
        self.window._move(-1)
        self.assertEqual(self.window._paths(), [paths[1], paths[2], paths[0], paths[3]])
        self.window._move(1)
        self.assertEqual(self.window._paths(), paths)

    def test_dangerous_options_and_pdf_password_are_never_persisted(self):
        self.window.advanced.update(overwrite=True, delete_originals=True, resize=True, width=300)
        self.window.rename_options["overwrite"] = True
        self.window.move_originals.setChecked(True)
        self.window.format_options["PDF"] = {"pdf_password": "temporary-password", "pdf_dpi": 150}
        self.window._save_settings()
        data = json.loads(self.window.settings.value("batch/options"))
        self.assertFalse(data["advanced"]["overwrite"])
        self.assertFalse(data["advanced"]["delete_originals"])
        self.assertFalse(data["rename_options"]["overwrite"])
        self.assertNotIn("pdf_password", data["format_options"]["PDF"])
        self.assertTrue(data["advanced"]["resize"])
        self.assertEqual(data["advanced"]["width"], 300)

    def test_background_conversion_finishes_with_progress_and_originals_intact(self):
        paths = [self._image(f"image{number}.png") for number in range(3)]
        self.window.add_paths(paths)
        self.window.format_combo.setCurrentIndex(self.window.format_combo.findData("JPEG"))
        ticks = []
        timer = QTimer()
        timer.setInterval(1)
        timer.timeout.connect(lambda: ticks.append(1))
        timer.start()
        self.window.start_batch()
        self.assertTrue(self.window.is_busy())
        self.assertFalse(self.window.controls.isEnabled())
        self._wait(lambda: not self.window.is_busy())
        timer.stop()
        self.assertTrue(ticks)
        self.assertEqual([result["status"] for result in self.window._results], ["ok"] * 3)
        self.assertEqual(self.window.progress_bar.value(), 3)
        for path in paths:
            self.assertTrue(path.exists())
            self.assertTrue((self.folder / "output" / (path.stem + ".jpg")).exists())
        self.assertTrue(self.window.controls.isEnabled())

    def test_rename_uses_original_bytes_and_numbering(self):
        paths = [self._image(f"image{number}.png") for number in range(2)]
        self.window.add_paths(paths)
        self.window.mode_buttons["rename"].setChecked(True)
        self.window.pattern.setText("session_###")
        self.window.start_number.setValue(8)
        self.window.step_number.setValue(3)
        self.window.start_batch()
        self._wait(lambda: not self.window.is_busy())
        for path, number in zip(paths, [8, 11]):
            renamed = self.folder / "output" / f"session_{number:03d}.png"
            self.assertEqual(renamed.read_bytes(), path.read_bytes())


if __name__ == "__main__":
    unittest.main()
