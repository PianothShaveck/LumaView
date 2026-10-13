"""Batch conversion and renaming, with a familiar browser / queue layout."""
from __future__ import annotations

import json
import os
import threading
from copy import deepcopy
from pathlib import Path

from PySide6.QtCore import (
    QDir, QObject, QRunnable, QSettings, QSize, Qt, QThread, QThreadPool,
    QTimer, Signal, Slot,
)
from PySide6.QtGui import QCloseEvent, QImage, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView, QButtonGroup, QCheckBox, QComboBox,
    QDialog, QDialogButtonBox, QFileDialog, QFileSystemModel,
    QGridLayout, QGroupBox, QHBoxLayout, QHeaderView, QLabel, QLineEdit,
    QListWidget, QListWidgetItem, QMessageBox, QPlainTextEdit, QProgressBar,
    QPushButton, QRadioButton, QScrollArea, QSizePolicy, QSpinBox, QSplitter,
    QTableWidget, QTableWidgetItem, QTreeView, QVBoxLayout, QWidget,
)

from ..model import FORMAT_LABELS, default_advanced, default_format
from ..image_io import supported_input_extensions


class _TaskSignals(QObject):
    done = Signal(object)
    failed = Signal(str)


class _Task(QRunnable):
    """Small cancellable-by-discarding-result background tasks."""
    def __init__(self, function):
        super().__init__()
        self.function = function
        self.signals = _TaskSignals()

    @Slot()
    def run(self):
        try:
            self.signals.done.emit(self.function())
        except Exception as exc:
            self.signals.failed.emit(str(exc))


class _BatchWorker(QObject):
    progress = Signal(object)
    finished = Signal(object)
    failed = Signal(str)

    def __init__(self, kwargs, cancel):
        super().__init__()
        self.kwargs = kwargs
        self.cancel = cancel

    def _progress(self, *args, **kwargs):
        self.progress.emit((args, kwargs))

    @Slot()
    def run(self):
        from ..batch import run_batch
        try:
            results = run_batch(**self.kwargs, callback=self._progress,
                                cancel=self.cancel)
            self.finished.emit(results)
        except Exception as exc:
            self.failed.emit(str(exc))


class _Queue(QListWidget):
    pathsDropped = Signal(list)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.setAcceptDrops(True)
        self.setAlternatingRowColors(True)

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            super().dragEnterEvent(event)

    def dragMoveEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            super().dragMoveEvent(event)

    def dropEvent(self, event):
        if event.mimeData().hasUrls():
            paths = [url.toLocalFile() for url in event.mimeData().urls()
                     if url.isLocalFile()]
            self.pathsDropped.emit(paths)
            event.acceptProposedAction()
        else:
            super().dropEvent(event)


class BatchWindow(QDialog):
    """Persistent batch window; processing never blocks the GUI thread."""

    def __init__(self, paths=None, parent=None, selection=None):
        super().__init__(parent)
        self.setWindowTitle("Batch conversion")
        self.setWindowFlag(Qt.WindowType.WindowMinMaxButtonsHint, True)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)
        self.setAcceptDrops(True)
        self.resize(1180, 840)
        self.setMinimumSize(940, 660)
        self.selection = selection
        self.settings = QSettings("LumaView", "LumaView")
        self.advanced = default_advanced()
        self.format_options = {}
        self.rename_options = {"overwrite": False, "keep_datetime": True, "create_subfolders": False}
        self._thread = None
        self._worker = None
        self._cancel_event = None
        self._results = []
        self._processed = set()
        self._preview_generation = 0
        self._preview_pixmap = None
        self._scanning = 0
        self._tasks = []
        self._closing = False
        self._preparing = False
        self._prepare_cancelled = False
        self.pool = QThreadPool(self)
        self.pool.setMaxThreadCount(2)
        self._build_ui()
        self._restore_settings()
        if paths:
            initial = [Path(p).expanduser() for p in paths]
            existing = next((p for p in initial if p.exists()), None)
            if existing:
                self._browse_to(existing if existing.is_dir() else existing.parent)
            self.add_paths(initial)
        self._update_mode()
        self._update_count()

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 14, 16, 12)
        root.setSpacing(10)
        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        root.addWidget(self.splitter, 1)

        left = QWidget()
        left.setMinimumWidth(340)
        controls = QVBoxLayout(left)
        controls.setContentsMargins(0, 0, 10, 0)
        controls.setSpacing(10)
        self.controls = left
        self.work_group = QGroupBox("Work as")
        work = QVBoxLayout(self.work_group)
        self.mode_group = QButtonGroup(self)
        self.mode_buttons = {}
        for number, (mode, label) in enumerate([
            ("convert", "Batch conversion"),
            ("rename", "Batch rename"),
            ("convert_rename", "Batch conversion — rename result files"),
        ]):
            button = QRadioButton(label)
            self.mode_group.addButton(button, number)
            self.mode_buttons[mode] = button
            work.addWidget(button)
            button.toggled.connect(self._update_mode)
        self.mode_buttons["convert"].setChecked(True)
        controls.addWidget(self.work_group)

        self.conversion_group = QGroupBox("Batch conversion settings")
        conversion = QGridLayout(self.conversion_group)
        conversion.addWidget(QLabel("Output format"), 0, 0, 1, 2)
        self.format_combo = QComboBox()
        from ..image_io import available_formats
        for fmt in available_formats():
            self.format_combo.addItem(FORMAT_LABELS.get(fmt, fmt), fmt)
        conversion.addWidget(self.format_combo, 1, 0)
        self.format_button = QPushButton("Options…")
        self.format_button.clicked.connect(self._format_options)
        conversion.addWidget(self.format_button, 1, 1)
        self.use_advanced = QCheckBox("Use advanced options")
        self.use_advanced.toggled.connect(self._update_mode)
        conversion.addWidget(self.use_advanced, 2, 0)
        self.advanced_button = QPushButton("Advanced…")
        self.advanced_button.clicked.connect(self._advanced_options)
        conversion.addWidget(self.advanced_button, 2, 1)
        controls.addWidget(self.conversion_group)

        self.rename_group = QGroupBox("Batch rename settings")
        rename = QGridLayout(self.rename_group)
        rename.addWidget(QLabel("Name pattern"), 0, 0, 1, 4)
        self.pattern = QLineEdit("$N_###")
        self.pattern.setToolTip("$N: original name · ###: numbered sequence")
        rename.addWidget(self.pattern, 1, 0, 1, 3)
        self.rename_options_button = QPushButton("Options…")
        self.rename_options_button.clicked.connect(self._rename_options_dialog)
        rename.addWidget(self.rename_options_button, 1, 3)
        rename.addWidget(QLabel("Start"), 2, 0)
        self.start_number = QSpinBox()
        self.start_number.setRange(0, 999999999)
        self.start_number.setValue(1)
        rename.addWidget(self.start_number, 2, 1)
        rename.addWidget(QLabel("Step"), 2, 2)
        self.step_number = QSpinBox()
        self.step_number.setRange(1, 999999)
        self.step_number.setValue(1)
        rename.addWidget(self.step_number, 2, 3)
        self.move_originals = QCheckBox("Move originals after success")
        self.move_originals.setToolTip("Remove each source only after its output was saved successfully.")
        rename.addWidget(self.move_originals, 3, 0, 1, 4)
        controls.addWidget(self.rename_group)

        output_group = QGroupBox("Output folder")
        output = QGridLayout(output_group)
        self.output_dir = QLineEdit(str(Path.home() / "Pictures" / "LumaView"))
        output.addWidget(self.output_dir, 0, 0, 1, 2)
        current = QPushButton("Use current folder")
        current.clicked.connect(lambda: self.output_dir.setText(self.location.text()))
        output.addWidget(current, 1, 0)
        browse = QPushButton("Browse…")
        browse.clicked.connect(self._choose_output)
        output.addWidget(browse, 1, 1)
        controls.addWidget(output_group)

        options = QGridLayout()
        self.include_subfolders = QCheckBox("Include subfolders")
        self.include_subfolders.setToolTip("Include nested folders when adding a folder or using Add all.")
        options.addWidget(self.include_subfolders, 0, 0, 1, 2)
        self.show_preview = QCheckBox("Show preview")
        self.show_preview.setChecked(True)
        self.show_preview.toggled.connect(self._toggle_preview)
        options.addWidget(self.show_preview, 1, 0, 1, 2)
        self.multithread = QCheckBox("Use multiple threads")
        options.addWidget(self.multithread, 2, 0)
        self.workers = QSpinBox()
        self.workers.setRange(1, 16)
        self.workers.setValue(min(os.cpu_count() or 2, 8))
        self.workers.setEnabled(False)
        self.multithread.toggled.connect(self.workers.setEnabled)
        options.addWidget(self.workers, 2, 1)
        controls.addLayout(options)

        self.preview_label = QLabel("Preview")
        self.preview_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview_label.setMinimumHeight(120)
        self.preview_label.setMaximumHeight(250)
        self.preview_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Expanding)
        self.preview_label.setStyleSheet("QLabel { background: palette(base); border: 1px solid palette(mid); border-radius: 6px; padding: 4px; }")
        controls.addWidget(self.preview_label, 1)
        self.preview_info = QLabel()
        self.preview_info.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview_info.setWordWrap(True)
        controls.addWidget(self.preview_info)

        self.start_button = QPushButton("Start batch")
        self.start_button.setObjectName("primaryButton")
        self.start_button.setProperty("primary",True)
        self.start_button.setMinimumWidth(150)
        self.start_button.setMinimumHeight(36)
        self.start_button.clicked.connect(self.start_batch)
        self.test_button = QPushButton("Test names…")
        self.test_button.clicked.connect(self.test_names)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll.setWidget(left)
        self.splitter.addWidget(scroll)

        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(4, 0, 0, 0)
        right_layout.setSpacing(8)
        self.browser_panel = QWidget()
        browser_layout = QVBoxLayout(self.browser_panel)
        browser_layout.setContentsMargins(0, 0, 0, 0)
        location_row = QHBoxLayout()
        location_row.addWidget(QLabel("Look in"))
        self.location = QLineEdit(str(Path.home()))
        self.location.returnPressed.connect(lambda: self._browse_to(self.location.text()))
        location_row.addWidget(self.location, 1)
        up = QPushButton("↑")
        up.setFixedWidth(32)
        up.setToolTip("Parent folder")
        up.clicked.connect(lambda: self._browse_to(Path(self.location.text()).parent))
        location_row.addWidget(up)
        folder_button = QPushButton("Browse…")
        folder_button.clicked.connect(self._choose_folder)
        location_row.addWidget(folder_button)
        browser_layout.addLayout(location_row)

        self.fs_model = QFileSystemModel(self)
        self.fs_model.setFilter(QDir.Filter.AllDirs | QDir.Filter.Files | QDir.Filter.NoDotAndDotDot)
        self.fs_model.setNameFilterDisables(False)
        self.fs_model.setRootPath(str(Path.home()))
        self.browser = QTreeView()
        self.browser.setModel(self.fs_model)
        self.browser.setRootIndex(self.fs_model.index(str(Path.home())))
        self.browser.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.browser.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.browser.setRootIsDecorated(False)
        self.browser.setItemsExpandable(False)
        self.browser.setSortingEnabled(True)
        self.browser.sortByColumn(0, Qt.SortOrder.AscendingOrder)
        self.browser.setAlternatingRowColors(True)
        self.browser.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.browser.setColumnWidth(1, 82)
        self.browser.setColumnWidth(2, 100)
        self.browser.setColumnWidth(3, 135)
        self.browser.doubleClicked.connect(self._browser_open)
        self.browser.selectionModel().currentChanged.connect(self._browser_preview)
        browser_layout.addWidget(self.browser, 1)

        filter_row = QHBoxLayout()
        filter_row.addWidget(QLabel("File type"))
        self.file_filter = QComboBox()
        self.file_filter.addItems(["Supported images", "All files", "JPEG (*.jpg *.jpeg)",
                                   "PNG (*.png)", "TIFF (*.tif *.tiff)", "PDF (*.pdf)",
                                   "GIF (*.gif)"])
        self.file_filter.currentIndexChanged.connect(self._apply_filter)
        filter_row.addWidget(self.file_filter, 1)
        browser_layout.addLayout(filter_row)

        self.right_splitter = QSplitter(Qt.Orientation.Vertical)
        self.right_splitter.addWidget(self.browser_panel)
        queue_panel = QWidget()
        queue_layout = QVBoxLayout(queue_panel)
        queue_layout.setContentsMargins(0, 0, 0, 0)
        queue_buttons = QGridLayout()
        self.queue_controls = []
        for row, col, label, function in [
            (0, 0, "Add", self._add_selected), (1, 0, "Add all", self._add_all),
            (0, 1, "Remove", self._remove_selected), (1, 1, "Remove all", self._remove_all),
            (0, 2, "Move up", lambda: self._move(-1)), (1, 2, "Move down", lambda: self._move(1)),
            (0, 3, "Sort files…", self._sort_files), (1, 3, "Load TXT…", self._load_list),
        ]:
            button = QPushButton(label)
            button.clicked.connect(function)
            queue_buttons.addWidget(button, row, col)
            self.queue_controls.append(button)
        queue_layout.addLayout(queue_buttons)
        count_row = QHBoxLayout()
        self.count_label = QLabel("Input files (0)")
        count_row.addWidget(self.count_label, 1)
        self.save_list_button = QPushButton("Save TXT…")
        self.save_list_button.clicked.connect(self._save_list)
        count_row.addWidget(self.save_list_button)
        queue_layout.addLayout(count_row)
        self.queue = _Queue()
        self.queue.pathsDropped.connect(self.add_paths)
        self.queue.currentItemChanged.connect(self._queue_preview)
        self.queue.model().rowsMoved.connect(lambda *_: self._update_count())
        queue_layout.addWidget(self.queue, 1)
        self.right_splitter.addWidget(queue_panel)
        self.right_splitter.setSizes([360, 300])
        right_layout.addWidget(self.right_splitter, 1)
        self.splitter.addWidget(right)
        self.splitter.setSizes([390, 760])

        self.progress_bar = QProgressBar()
        self.progress_bar.setVisible(False)
        root.addWidget(self.progress_bar)
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumHeight(155)
        self.log.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.log.setVisible(False)
        root.addWidget(self.log)
        footer = QHBoxLayout()
        footer.addWidget(self.start_button)
        footer.addWidget(self.test_button)
        self.status_label = QLabel()
        self.status_label.setWordWrap(True)
        footer.addWidget(self.status_label, 1)
        self.log_toggle = QPushButton("Show log")
        self.log_toggle.setVisible(False)
        self.log_toggle.clicked.connect(self._toggle_log)
        footer.addWidget(self.log_toggle)
        self.close_button = QPushButton("Close")
        self.close_button.clicked.connect(self._cancel_or_close)
        footer.addWidget(self.close_button)
        root.addLayout(footer)
        for button in self.findChildren(QPushButton):
            button.setAutoDefault(False)
            button.setDefault(False)
        self._apply_filter()

    def _mode(self):
        return next((mode for mode, button in self.mode_buttons.items()
                     if button.isChecked()), "convert")

    def _update_mode(self, *_):
        if not hasattr(self, "rename_group"):
            return
        busy = self.is_busy()
        mode = self._mode()
        self.conversion_group.setEnabled(not busy and mode != "rename")
        self.rename_group.setEnabled(not busy and mode != "convert")
        self.rename_options_button.setEnabled(mode == "rename")
        self.advanced_button.setEnabled(self.use_advanced.isChecked())
        if hasattr(self, "file_filter"):
            self._apply_filter()
        self._update_count()

    def _format(self):
        return self.format_combo.currentData() or "PNG"

    def is_busy(self):
        return self._thread is not None or self._preparing

    def _format_options(self):
        from .format_options import FormatOptionsDialog
        fmt = self._format()
        dialog = FormatOptionsDialog(fmt, deepcopy(self.format_options.get(fmt, default_format(fmt))), self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.format_options[fmt] = deepcopy(dialog.options)

    def _advanced_options(self):
        from .advanced import AdvancedDialog
        dialog = AdvancedDialog(deepcopy(self.advanced), self, selection=self.selection)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.advanced = deepcopy(dialog.options)

    def _rename_options_dialog(self):
        dialog = QDialog(self)
        dialog.setWindowTitle("Rename options")
        layout = QVBoxLayout(dialog)
        checks = {}
        for key, label in [("keep_datetime", "Keep original date and time"),
                           ("create_subfolders", "Create original subfolders"),
                           ("overwrite", "Overwrite existing files")]:
            check = QCheckBox(label)
            check.setChecked(bool(self.rename_options.get(key)))
            checks[key] = check
            layout.addWidget(check)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.rename_options.update({key: check.isChecked() for key, check in checks.items()})

    def _restore_settings(self):
        try:
            data = json.loads(str(self.settings.value("batch/options", "{}")))
        except (ValueError, TypeError):
            data = {}
        if not isinstance(data, dict):
            data = {}
        self.advanced.update(data.get("advanced", {}))
        self.advanced.update(overwrite=False, delete_originals=False)
        self.rename_options.update(data.get("rename_options", {}))
        self.rename_options["overwrite"] = False
        self.format_options = data.get("format_options", {})
        for fmt_options in self.format_options.values():
            if isinstance(fmt_options, dict):
                fmt_options.pop("pdf_password", None)
        self.mode_buttons.get(data.get("mode"), self.mode_buttons["convert"]).setChecked(True)
        self.use_advanced.setChecked(bool(data.get("use_advanced", False)))
        index = self.format_combo.findData(data.get("format", "PNG"))
        if index >= 0:
            self.format_combo.setCurrentIndex(index)
        self.pattern.setText(str(data.get("pattern", "$N_###")))
        self.start_number.setValue(int(data.get("start", 1)))
        self.step_number.setValue(int(data.get("step", 1)))
        self.output_dir.setText(str(data.get("output_dir", self.output_dir.text())))
        self.multithread.setChecked(bool(data.get("multithread", False)))
        self.workers.setValue(int(data.get("workers", self.workers.value())))
        self.show_preview.setChecked(bool(data.get("preview", True)))
        location = Path(str(data.get("location", Path.home()))).expanduser()
        self._browse_to(location if location.is_dir() else Path.home())

    def _save_settings(self):
        advanced = deepcopy(self.advanced)
        advanced.update(overwrite=False, delete_originals=False)
        formats = deepcopy(self.format_options)
        for options in formats.values():
            options.pop("pdf_password", None)
        data = dict(advanced=advanced, format_options=formats, mode=self._mode(),
                    use_advanced=self.use_advanced.isChecked(), format=self._format(),
                    pattern=self.pattern.text(), start=self.start_number.value(),
                    step=self.step_number.value(), output_dir=self.output_dir.text(),
                    multithread=self.multithread.isChecked(), workers=self.workers.value(),
                    preview=self.show_preview.isChecked(), location=self.location.text())
        data["rename_options"] = {**self.rename_options, "overwrite": False}
        self.settings.setValue("batch/options", json.dumps(data))

    def _browse_to(self, path):
        directory = Path(path).expanduser()
        if not directory.is_dir():
            self.status_label.setText("Folder not found.")
            return
        directory = directory.resolve()
        self.location.setText(str(directory))
        self.browser.setRootIndex(self.fs_model.index(str(directory)))
        self.status_label.clear()

    def _choose_folder(self):
        path = QFileDialog.getExistingDirectory(self, "Choose input folder", self.location.text())
        if path:
            self._browse_to(path)

    def _choose_output(self):
        path = QFileDialog.getExistingDirectory(self, "Choose output folder", self.output_dir.text())
        if path:
            self.output_dir.setText(path)

    def _filter_extensions(self):
        selection = self.file_filter.currentIndex()
        if selection == 1:
            return None
        return [supported_input_extensions(), None, {".jpg", ".jpeg"}, {".png"},
                {".tif", ".tiff"}, {".pdf"}, {".gif"}][selection]

    def _apply_filter(self, *_):
        extensions = self._filter_extensions()
        self.fs_model.setNameFilters(["*" + ext for ext in sorted(extensions)] if extensions else ["*"])

    def _browser_open(self, index):
        path = Path(self.fs_model.filePath(index))
        if path.is_dir():
            self._browse_to(path)
        else:
            self.add_paths([path])

    def _browser_preview(self, current, previous):
        path = Path(self.fs_model.filePath(current))
        if path.is_file():
            self._request_preview(path)

    def _queue_preview(self, current, previous):
        if current:
            self._request_preview(Path(current.data(Qt.ItemDataRole.UserRole)))

    def _add_selected(self):
        paths = [self.fs_model.filePath(index)
                 for index in self.browser.selectionModel().selectedRows(0)]
        self.add_paths(paths)

    def _add_all(self):
        self.add_paths([self.location.text()])

    def add_paths(self, paths):
        if self.is_busy():
            return
        paths = [Path(path).expanduser() for path in paths]
        files = [path for path in paths if path.is_file()]
        folders = [path for path in paths if path.is_dir()]
        self._append_paths(files)
        if not folders:
            return
        recursive = self.include_subfolders.isChecked()
        extensions = self._filter_extensions()
        self._scanning += 1
        self._update_count()
        self.status_label.setText("Reading folder…")

        def scan():
            found = []
            for folder in folders:
                if recursive:
                    for base, dirs, names in os.walk(folder, followlinks=False):
                        dirs[:] = sorted(d for d in dirs if not d.startswith("."))
                        found.extend(Path(base) / name for name in sorted(names)
                                     if not name.startswith(".") and
                                     (extensions is None or Path(name).suffix.lower() in extensions))
                else:
                    found.extend(path for path in sorted(folder.iterdir()) if path.is_file()
                                 and not path.name.startswith(".") and
                                 (extensions is None or path.suffix.lower() in extensions))
            return found

        task = _Task(scan)
        self._tasks.append(task)
        task.signals.done.connect(lambda result, task=task: self._scan_complete(task, result))
        task.signals.failed.connect(lambda message, task=task: self._scan_failed(task, message))
        self.pool.start(task)

    def _scan_complete(self, task, paths):
        self._scanning -= 1
        self._release_task(task)
        self._append_paths(paths)
        self.status_label.setText(f"{len(paths):,} files found.")
        self._update_count()

    def _scan_failed(self, task, message):
        self._scanning -= 1
        self._release_task(task)
        self.status_label.setText(message)
        self._update_count()

    def _append_paths(self, paths):
        existing = {self.queue.item(row).data(Qt.ItemDataRole.UserRole)
                    for row in range(self.queue.count())}
        for path in paths:
            path = str(Path(path).resolve())
            if path not in existing:
                item = QListWidgetItem(path)
                item.setData(Qt.ItemDataRole.UserRole, path)
                item.setToolTip(path)
                self.queue.addItem(item)
                existing.add(path)
        self._update_count()
        if self.queue.count() and self.queue.currentRow() < 0:
            self.queue.setCurrentRow(0)

    def _paths(self):
        return [Path(self.queue.item(row).data(Qt.ItemDataRole.UserRole))
                for row in range(self.queue.count())]

    def _remove_selected(self):
        for item in list(self.queue.selectedItems()):
            self.queue.takeItem(self.queue.row(item))
        self._update_count()

    def _remove_all(self):
        self.queue.clear()
        self._update_count()
        self._preview_generation += 1
        self._preview_pixmap = None
        self.preview_label.setText("Preview")
        self.preview_info.clear()

    def _move(self, direction):
        rows = sorted([self.queue.row(item) for item in self.queue.selectedItems()], reverse=direction > 0)
        selected = set(rows)
        if not rows:
            return
        for row in rows:
            target = row + direction
            if target < 0 or target >= self.queue.count() or target in selected:
                continue
            item = self.queue.takeItem(row)
            self.queue.insertItem(target, item)
            item.setSelected(True)
            selected.remove(row)
            selected.add(target)

    def _sort_files(self):
        dialog = QDialog(self)
        dialog.setWindowTitle("Sort files")
        layout = QVBoxLayout(dialog)
        sort_by = QComboBox()
        sort_by.addItems(["Name", "Full path", "File size", "Date modified"])
        layout.addWidget(sort_by)
        descending = QCheckBox("Descending")
        layout.addWidget(descending)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        paths = self._paths()
        def sort_key(path):
            mode = sort_by.currentIndex()
            if mode == 0:
                import re
                return [int(part) if part.isdigit() else part.casefold()
                        for part in re.split(r"(\d+)", path.name)]
            if mode == 1:
                return str(path).casefold()
            try:
                return path.stat().st_size if mode == 2 else path.stat().st_mtime
            except OSError:
                return -1
        paths.sort(key=sort_key, reverse=descending.isChecked())
        self.queue.clear()
        self._append_paths(paths)

    def _load_list(self):
        filename, _ = QFileDialog.getOpenFileName(self, "Load input list", self.location.text(), "Text files (*.txt);;All files (*)")
        if not filename:
            return
        try:
            source = Path(filename)
            paths = []
            missing = 0
            for line in source.read_text(encoding="utf-8-sig").splitlines():
                line = line.strip()
                if not line:
                    continue
                path = Path(line).expanduser()
                if not path.is_absolute():
                    path = source.parent / path
                if path.exists():
                    paths.append(path)
                else:
                    missing += 1
            self.add_paths(paths)
            if missing:
                self.status_label.setText(f"{missing:,} missing paths were skipped.")
        except (OSError, UnicodeError) as exc:
            QMessageBox.warning(self, "Cannot load list", str(exc))

    def _save_list(self):
        filename, _ = QFileDialog.getSaveFileName(self, "Save input list", str(Path(self.location.text()) / "batch-files.txt"), "Text files (*.txt)")
        if filename:
            try:
                Path(filename).write_text("\n".join(str(path) for path in self._paths()) + "\n", encoding="utf-8")
            except OSError as exc:
                QMessageBox.warning(self, "Cannot save list", str(exc))

    def _update_count(self):
        if not hasattr(self, "queue"):
            return
        count = self.queue.count()
        self.count_label.setText(f"Input files ({count:,})")
        available = count > 0 and not self.is_busy() and self._scanning == 0
        self.start_button.setEnabled(available)
        self.test_button.setEnabled(available)
        self.save_list_button.setEnabled(count > 0)

    def _toggle_preview(self, enabled):
        self.preview_label.setVisible(enabled)
        self.preview_info.setVisible(enabled)
        self._preview_generation += 1
        if enabled and self.queue.currentItem():
            self._queue_preview(self.queue.currentItem(), None)

    def _request_preview(self, path):
        if not self.show_preview.isChecked():
            return
        self._preview_generation += 1
        generation = self._preview_generation
        self.preview_info.setText(path.name)
        self.preview_label.setText("Loading…")
        self.preview_label.setToolTip("")
        self._preview_pixmap = None

        def load():
            if generation != self._preview_generation:
                return generation, None, ""
            from ..image_io import load_document
            document = load_document(path, all_pages=False, pdf_dpi=72)
            im = document.frames[0].copy()
            width, height = im.size
            im.thumbnail((800, 600))
            im = im.convert("RGBA")
            raw = im.tobytes("raw", "RGBA")
            qimage = QImage(raw, im.width, im.height, im.width * 4,
                            QImage.Format.Format_RGBA8888).copy()
            return generation, qimage, f"{width:,} × {height:,} · {path.name}"

        task = _Task(load)
        self._tasks.append(task)
        task.signals.done.connect(lambda value, task=task: self._preview_ready(task, value))
        task.signals.failed.connect(lambda error, task=task, gen=generation: self._preview_failed(task, gen, error))
        self.pool.start(task)

    def _preview_ready(self, task, value):
        self._release_task(task)
        generation, qimage, info = value
        if generation == self._preview_generation and self.show_preview.isChecked():
            self._preview_pixmap = QPixmap.fromImage(qimage)
            self._scale_preview()
            self.preview_info.setText(info)

    def _preview_failed(self, task, generation, error):
        self._release_task(task)
        if generation == self._preview_generation:
            self.preview_label.setText("No preview")
            self.preview_label.setToolTip(error)

    def _release_task(self, task):
        if task in self._tasks:
            self._tasks.remove(task)
        # Signal closures hold the task itself; disconnect to release completed
        # previews immediately rather than retaining their pixel buffers.
        task.signals.done.disconnect()
        task.signals.failed.disconnect()

    def _scale_preview(self):
        if self._preview_pixmap:
            self.preview_label.setPixmap(self._preview_pixmap.scaled(
                self.preview_label.size() - QSize(14, 14),
                Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, "preview_label"):
            self._scale_preview()

    def _effective_advanced(self):
        mode = self._mode()
        options = deepcopy(self.advanced) if self.use_advanced.isChecked() and mode != "rename" else default_advanced()
        if mode == "rename":
            options.update(self.rename_options)
        if mode != "convert" and self.move_originals.isChecked():
            options["delete_originals"] = True
        return options

    def _arguments(self):
        paths = self._paths()
        if not paths:
            raise ValueError("Add at least one input file.")
        output = self.output_dir.text().strip()
        if not output:
            raise ValueError("Choose an output folder.")
        if self._mode() != "convert" and not self.pattern.text().strip():
            raise ValueError("Enter a name pattern.")
        source_root = Path(os.path.commonpath([str(path.parent) for path in paths]))
        return dict(paths=paths, output_dir=Path(output).expanduser(), fmt=self._format(),
                    mode=self._mode(), pattern=self.pattern.text(),
                    start=self.start_number.value(), step=self.step_number.value(),
                    source_root=source_root, advanced=self._effective_advanced())

    def test_names(self):
        self._prepare_batch(test_only=True)

    def _prepare_batch(self, test_only=False):
        if self.is_busy() or self._scanning:
            return
        from ..batch import preview_batch
        try:
            kwargs = self._arguments()
        except Exception as exc:
            QMessageBox.warning(self, "Cannot prepare batch", str(exc))
            return
        self._preparing = True
        self._prepare_cancelled = False
        self._set_busy(True)
        self.status_label.setText("Checking output files…")

        def prepare():
            mappings = preview_batch(**kwargs)
            existing = sum(Path(mapping["output"]).exists() for mapping in mappings)
            return mappings, existing

        task = _Task(prepare)
        self._tasks.append(task)
        task.signals.done.connect(lambda value, task=task: self._prepared(task, kwargs, value, test_only))
        task.signals.failed.connect(lambda message, task=task: self._prepare_failed(task, message))
        self.pool.start(task)

    def _end_prepare(self, task):
        self._release_task(task)
        self._preparing = False
        self._set_busy(False)
        self._update_mode()
        if self._closing:
            self.close()
        return self._closing or self._prepare_cancelled

    def _prepare_failed(self, task, message):
        if self._end_prepare(task):
            return
        self.status_label.setText("Batch not started.")
        QMessageBox.warning(self, "Cannot prepare batch", message)

    def _prepared(self, task, kwargs, value, test_only):
        if self._end_prepare(task):
            self.status_label.setText("Stopped.")
            return
        mappings, existing = value
        if test_only:
            self.status_label.setText(f"{len(mappings):,} output names checked.")
            self._show_names(mappings)
        else:
            self._start_prepared(kwargs, existing)

    def _show_names(self, mappings):
        dialog = QDialog(self)
        dialog.setWindowTitle("Output names")
        dialog.resize(960, 480)
        layout = QVBoxLayout(dialog)
        table = QTableWidget(len(mappings), 3)
        table.setHorizontalHeaderLabels(["Source", "Output", "Status"])
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.setAlternatingRowColors(True)
        table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        seen = set()
        for row, mapping in enumerate(mappings):
            source = Path(mapping["source"])
            output = Path(mapping["output"])
            key = str(output.resolve()).casefold()
            status = "Duplicate output" if key in seen else "Source file" if output.resolve() == source.resolve() else "Exists" if output.exists() else "New file"
            seen.add(key)
            for column, value in enumerate([str(source), str(output), status]):
                item = QTableWidgetItem(value)
                item.setToolTip(value)
                table.setItem(row, column, item)
        layout.addWidget(table)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        dialog.exec()

    def start_batch(self):
        self._prepare_batch()

    def _start_prepared(self, kwargs, existing):
        try:
            advanced = kwargs["advanced"]
            if existing:
                response = QMessageBox.question(self, "Replace output files?",
                    f"Replace {existing:,} existing output files?",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.No)
                if response != QMessageBox.StandardButton.Yes:
                    return
            if advanced.get("delete_originals"):
                response = QMessageBox.question(self, "Remove originals after saving?",
                    "Each source file will be removed after its output is saved successfully.\n\nContinue?",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.No)
                if response != QMessageBox.StandardButton.Yes:
                    return
            kwargs["format_options"] = deepcopy(self.format_options.get(self._format(), default_format(self._format())))
            kwargs["input_options"] = {
                'pdf_dpi': self.settings.value('pdf/dpi', 96, type=int),
                'svg_width': self.settings.value('svg/width', 1024, type=int),
                'svg_height': self.settings.value('svg/height', 800, type=int),
                'svg_keep_aspect': self.settings.value('svg/keep_aspect', False, type=bool),
            }
            kwargs["workers"] = self.workers.value() if self.multithread.isChecked() else 1
        except Exception as exc:
            QMessageBox.warning(self, "Cannot start batch", str(exc))
            return
        self._save_settings()
        self._results = []
        self._processed.clear()
        self.log.clear()
        self.log_toggle.setVisible(True)
        self.log.setVisible(False)
        self.log_toggle.setText("Show log")
        self.progress_bar.setRange(0, len(kwargs["paths"]))
        self.progress_bar.setValue(0)
        self.progress_bar.setVisible(True)
        self.status_label.setText("Processing…")
        self._cancel_event = threading.Event()
        self._thread = QThread(self)
        self._worker = _BatchWorker(kwargs, self._cancel_event)
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.progress.connect(self._batch_progress)
        self._worker.finished.connect(self._batch_finished)
        self._worker.failed.connect(self._batch_failed)
        self._worker.finished.connect(self._thread.quit)
        self._worker.failed.connect(self._thread.quit)
        self._worker.finished.connect(self._worker.deleteLater)
        self._worker.failed.connect(self._worker.deleteLater)
        self._thread.finished.connect(self._thread_finished)
        self._set_busy(True)
        self._thread.start()

    def _set_busy(self, busy):
        self.controls.setEnabled(not busy)
        self.browser_panel.setEnabled(not busy)
        self.queue.setEnabled(not busy)
        for button in self.queue_controls:
            button.setEnabled(not busy)
        self.close_button.setText("Stop" if busy else "Close")
        self.close_button.setEnabled(True)
        self._update_count()

    def _batch_progress(self, value):
        args, kwargs = value
        result = next((arg for arg in reversed(args) if isinstance(arg, dict)), kwargs)
        if not isinstance(result, dict):
            return
        source = result.get("source", result.get("path", ""))
        key = str(source)
        if not key or key in self._processed:
            return
        status = result.get("status", "ok")
        if status not in {"ok", "error", "cancelled", "skipped"}:
            return
        self._processed.add(key)
        self.progress_bar.setValue(len(self._processed))
        message = result.get("error") or result.get("message") or result.get("output", "")
        line = f"{str(status).upper()}  {source}"
        if message:
            line += f"  →  {message}"
        if status == 'ok' and result.get('bytes') is not None:
            details = [f"{result['bytes']:,} bytes"]
            if result.get('pixel_size'):
                width, height = result['pixel_size']
                details.append(f'{width} × {height}')
            mode = result.get('color_mode')
            if mode:
                details.append('1-bit' if mode == '1' else mode)
            if result.get('compression'):
                names = {'group4': 'CCITT Fax 4', 'group3': 'CCITT Fax 3',
                         'tiff_ccitt': 'Huffman RLE', 'tiff_lzw': 'LZW',
                         'tiff_adobe_deflate': 'ZIP', 'raw': 'Uncompressed'}
                details.append(names.get(result['compression'], result['compression']))
            line += '  [' + ' · '.join(details) + ']'
        self.log.appendPlainText(line)
        for warning in result.get("warnings", []):
            self.log.appendPlainText(f"WARNING  {source}  →  {warning}")
        self.status_label.setText(f"{len(self._processed):,} / {self.queue.count():,} · {Path(source).name}")

    def _batch_finished(self, results):
        self._results = results or []
        for result in self._results:
            self._batch_progress(((result,), {}))
        counts = {status: sum(1 for result in self._results if result.get("status") == status)
                  for status in ("ok", "error", "cancelled", "skipped")}
        summary = f"{counts['ok']:,} completed"
        if counts["error"]:
            summary += f" · {counts['error']:,} failed"
            self.log.setVisible(True)
            self.log_toggle.setText("Hide log")
        if counts["cancelled"] or (self._cancel_event and self._cancel_event.is_set()):
            summary += f" · stopped"
        if counts["skipped"]:
            summary += f" · {counts['skipped']:,} skipped"
        warnings = sum(len(result.get("warnings", [])) for result in self._results)
        if warnings:
            summary += f" · {warnings:,} warnings"
            self.log.setVisible(True)
            self.log_toggle.setText("Hide log")
        self.status_label.setText(summary)

    def _batch_failed(self, message):
        self.log.appendPlainText("ERROR  " + message)
        self.log.setVisible(True)
        self.log_toggle.setText("Hide log")
        self.status_label.setText("Batch failed. See log.")

    def _thread_finished(self):
        old_thread = self._thread
        self._thread = None
        self._worker = None
        self._cancel_event = None
        if old_thread:
            old_thread.deleteLater()
        self._set_busy(False)
        self._update_mode()
        if self._closing:
            self.close()

    def _toggle_log(self):
        visible = not self.log.isVisible()
        self.log.setVisible(visible)
        self.log_toggle.setText("Hide log" if visible else "Show log")

    def _cancel_or_close(self):
        if self._preparing:
            self._prepare_cancelled = True
            self.status_label.setText("Stopping preparation…")
            self.close_button.setEnabled(False)
        elif self._thread is not None:
            self._cancel_event.set()
            self.status_label.setText("Stopping after active files finish…")
            self.close_button.setEnabled(False)
        else:
            self.close()

    def reject(self):
        if self.is_busy():
            self._cancel_or_close()
        else:
            self._save_settings()
            self._preview_generation += 1
            super().reject()

    def showEvent(self, event):
        if not self.is_busy():
            self._closing = False
        super().showEvent(event)

    def closeEvent(self, event: QCloseEvent):
        if self.is_busy():
            self._closing = True
            self._cancel_or_close()
            event.ignore()
            return
        self._save_settings()
        self._preview_generation += 1
        super().closeEvent(event)

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls() and not self.is_busy():
            event.acceptProposedAction()

    def dropEvent(self, event):
        if not self.is_busy() and event.mimeData().hasUrls():
            self.add_paths([url.toLocalFile() for url in event.mimeData().urls() if url.isLocalFile()])
            event.acceptProposedAction()
