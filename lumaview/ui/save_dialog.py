"""Save browser with format options beside the file chooser."""
from copy import deepcopy
from pathlib import Path
import json
import tempfile

from PySide6.QtCore import Qt, QSettings, QTimer
from PySide6.QtWidgets import (
    QDialog, QHBoxLayout, QVBoxLayout, QFileDialog, QLabel,
    QDialogButtonBox, QSpinBox, QFormLayout, QCheckBox, QMessageBox, QLineEdit,
)
from lumaview.model import FORMAT_LABELS, EXTENSIONS, default_format
from lumaview.image_io import available_formats, save_document, load_document
from lumaview.ui.format_options import FormatOptionsPanel
from lumaview.ui.common import pil_to_pixmap

_FORMAT_SUFFIXES = {key: {value} for key, value in EXTENSIONS.items()}
_FORMAT_SUFFIXES.update({
    'JPEG': {'.jpg', '.jpeg', '.jpe'}, 'TIFF': {'.tif', '.tiff'},
    'PNG': {'.png', '.apng'}, 'JPEG2000': {'.jp2', '.j2k', '.jpf'},
    'HEIF': {'.heic', '.heif'}, 'BMP': {'.bmp', '.dib'},
})


class _SaveBrowser(QFileDialog):
    """Validate before QFileDialog hides itself or confirms replacement."""
    def __init__(self, owner, directory):
        super().__init__(owner, '', directory)
        self.owner = owner

    def set_filename(self, path):
        path = Path(path)
        # Qt's selectFile() does not update a visible non-native dialog.
        edit = self.findChild(QLineEdit, 'fileNameEdit')
        if edit is not None and self.isVisible():
            current = Path(self.directory().absolutePath())
            edit.setText(path.name if path.parent == current else str(path))
        else:
            self.selectFile(str(path))

    def accept(self):
        if self.owner.prepare_selection():
            super().accept()


class SaveAsDialog(QDialog):
    def __init__(self, document, parent=None):
        super().__init__(parent)
        self.document = document
        self.setWindowTitle('Save picture as')
        self.resize(1160, 750)
        self.settings = QSettings()
        self.formats = available_formats()
        if not self.formats:
            raise RuntimeError('No image encoders are available.')
        suffix = document.source.suffix.lower() if document.source else '.png'
        self.format = next((fmt for fmt in self.formats if suffix in _FORMAT_SUFFIXES.get(fmt, set())), 'PNG')
        if self.format not in self.formats:
            self.format = self.formats[0]
        self.options = self.read_options(self.format)
        self.path = None
        layout = QHBoxLayout(self)
        directory = str(document.source.parent) if document.source else self.settings.value('save/directory', str(Path.home() / 'Pictures'))
        self.browser = _SaveBrowser(self, directory)
        self.browser.setOption(QFileDialog.Option.DontUseNativeDialog, True)
        self.browser.setWindowFlags(Qt.WindowType.Widget)
        self.browser.setAcceptMode(QFileDialog.AcceptMode.AcceptSave)
        self.browser.setFileMode(QFileDialog.FileMode.AnyFile)
        self.filters = {f'{FORMAT_LABELS[fmt]} (*{EXTENSIONS[fmt]})': fmt for fmt in self.formats}
        self.browser.setNameFilters(list(self.filters))
        self.browser.selectNameFilter(next(key for key, value in self.filters.items() if value == self.format))
        self.browser.setDefaultSuffix(EXTENSIONS[self.format].lstrip('.'))
        name = document.source.stem if document.source else 'Untitled'
        self.browser.selectFile(name + EXTENSIONS[self.format])
        layout.addWidget(self.browser, 1)
        right = QVBoxLayout()
        self.title = QLabel(self.format + ' options')
        self.title.setStyleSheet('font-size:16px;font-weight:600;padding:8px 0;')
        right.addWidget(self.title)
        self.panel = FormatOptionsPanel(self.format, self.options, self)
        self.panel.setMinimumWidth(350)
        self.panel.setMaximumWidth(410)
        right.addWidget(self.panel, 1)
        self.keep_time = QCheckBox('Keep original date/time')
        self.keep_time.setEnabled(document.source is not None)
        right.addWidget(self.keep_time)
        layout.addLayout(right)
        self.browser.filterSelected.connect(self.change_format)
        self.browser.accepted.connect(self.accept_selection)
        self.browser.rejected.connect(self.reject)

    def read_options(self, fmt):
        try:
            saved = json.loads(self.settings.value('format/' + fmt, '{}'))
            if isinstance(saved, dict):
                saved.pop('pdf_password', None)
                return {**default_format(fmt), **saved}
            return default_format(fmt)
        except (ValueError, TypeError):
            return default_format(fmt)

    def change_format(self, name):
        if name not in self.filters:
            return
        self.settings.setValue('format/' + self.format, json.dumps({k: v for k, v in self.panel.values().items() if k != 'pdf_password'}))
        chosen = self.browser.selectedFiles()
        self.format = self.filters[name]
        self.panel.set_format(self.format, self.read_options(self.format))
        self.title.setText(self.format + ' options')
        self.browser.setDefaultSuffix(EXTENSIONS[self.format].lstrip('.'))
        if chosen:
            path = Path(chosen[0])
            if not path.is_dir():
                self.browser.set_filename(path.with_suffix(EXTENSIONS[self.format]))

    def prepare_selection(self):
        paths = self.browser.selectedFiles()
        if not paths:
            return False
        path = Path(paths[0])
        if path.is_dir():
            # Let QFileDialog navigate into a directory typed in the name field.
            return True
        try:
            options = self.panel.validate()
        except ValueError as exc:
            QMessageBox.warning(self, 'Invalid save options', str(exc))
            return False
        # Encoder and extension must agree. Normalization occurs BEFORE Qt's
        # overwrite question, so that question always names the actual target.
        if path.suffix.lower() not in _FORMAT_SUFFIXES.get(self.format, set()):
            path = path.with_suffix(EXTENSIONS[self.format])
            self.browser.set_filename(path)
        self.path = path
        self.options = options
        return True

    def accept_selection(self):
        # Called only after QFileDialog has checked the path and overwrite choice.
        paths = self.browser.selectedFiles()
        if not paths:
            self.browser.show()
            return
        self.path = Path(paths[0])
        self.settings.setValue('format/' + self.format, json.dumps({k: v for k, v in self.options.items() if k != 'pdf_password'}))
        self.settings.setValue('save/directory', str(self.path.parent))
        super().accept()

    def accept(self):
        self.browser.accept()


class EncodingPreview(QDialog):
    def __init__(self, document, fmt, options, parent=None):
        super().__init__(parent)
        self.document = document
        self.format = fmt
        self.options = deepcopy(options)
        self.preview_valid = False
        self.setWindowTitle('Encoding preview')
        self.resize(940, 600)
        layout = QVBoxLayout(self)
        images = QHBoxLayout()
        self.original = QLabel()
        self.encoded = QLabel()
        for title, label in [('Original', self.original), ('Encoded', self.encoded)]:
            column = QVBoxLayout()
            caption = QLabel(title)
            caption.setAlignment(Qt.AlignmentFlag.AlignCenter)
            column.addWidget(caption)
            label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            label.setMinimumSize(400, 350)
            column.addWidget(label, 1)
            images.addLayout(column, 1)
        layout.addLayout(images, 1)
        self.original.setPixmap(pil_to_pixmap(document.frames[0]).scaled(
            420, 400, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
        controls = QFormLayout()
        self.quality = QSpinBox()
        self.quality.setRange(1, 100)
        self.quality.setValue(int(options.get('quality', 90)))
        controls.addRow('JPEG quality', self.quality)
        self.original_quality = QCheckBox('Use original JPEG quality')
        self.original_quality.setChecked(bool(options.get('original_quality')))
        self.original_quality.setVisible(bool(document.metadata.get('jpeg_qtables')) or self.original_quality.isChecked())
        controls.addRow(self.original_quality)
        target = float(options.get('target_kb', 0))
        if target > 0:
            controls.addRow('Target file size', QLabel(f'{target:g} KiB'))
            self.original_quality.setEnabled(False)
        self.size_label = QLabel()
        self.size_label.setWordWrap(True)
        controls.addRow('Encoded size', self.size_label)
        layout.addLayout(controls)
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        layout.addWidget(self.buttons)
        self.save_button = self.buttons.button(QDialogButtonBox.StandardButton.Save)
        self.save_button.setEnabled(False)
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        self.preview_timer = QTimer(self)
        self.preview_timer.setSingleShot(True)
        self.preview_timer.setInterval(120)
        self.preview_timer.timeout.connect(self.update_preview)
        self.quality.valueChanged.connect(self.queue_preview)
        self.original_quality.toggled.connect(self.queue_preview)
        self.update_preview()

    def queue_preview(self, *_):
        self.preview_valid = False
        self.save_button.setEnabled(False)
        self.quality.setEnabled(not self.original_quality.isChecked() and float(self.options.get('target_kb', 0)) <= 0)
        self.size_label.setText('Updating…')
        self.preview_timer.start()

    def update_preview(self):
        self.preview_timer.stop()
        self.preview_valid = False
        self.save_button.setEnabled(False)
        self.options['quality'] = self.quality.value()
        self.options['original_quality'] = self.original_quality.isChecked()
        self.quality.setEnabled(not self.options['original_quality'] and float(self.options.get('target_kb', 0)) <= 0)
        try:
            with tempfile.TemporaryDirectory() as folder:
                path = Path(folder) / ('preview' + EXTENSIONS[self.format])
                result = save_document(self.document, path, self.format, self.options)
                preview = load_document(path, all_pages=False)
                pixmap = pil_to_pixmap(preview.frames[0]).scaled(
                    420, 400, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
                self.encoded.setPixmap(pixmap)
                text = f'{path.stat().st_size / 1024:.1f} KiB'
                if isinstance(result, dict) and 'quality' in result:
                    text += f' · quality {result["quality"]}'
                self.size_label.setText(text)
                self.preview_valid = True
                self.save_button.setEnabled(True)
        except Exception as exc:
            self.encoded.clear()
            self.size_label.setText(str(exc))

    def accept(self):
        if self.preview_timer.isActive():
            self.update_preview()
        if self.preview_valid:
            super().accept()
