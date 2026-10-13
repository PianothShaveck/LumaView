"""Compact, reusable encoder side panel for Save As and batch conversion."""
from __future__ import annotations
from copy import deepcopy
import json
from PySide6.QtCore import Qt, QSettings, Signal
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox,
    QFormLayout, QGroupBox, QHBoxLayout, QInputDialog, QLabel, QLineEdit,
    QMessageBox, QPushButton, QScrollArea, QSlider, QSpinBox, QVBoxLayout, QWidget,
)
from PySide6.QtGui import QColor
from ..model import default_format
from ..image_io import find_executable
from .advanced import ColorField


class FormatOptionsPanel(QWidget):
    """Per-format options; suitable for a panel beside a file picker."""
    changed = Signal()

    def __init__(self, fmt='PNG', options=None, parent=None):
        super().__init__(parent)
        self.setMinimumWidth(315)
        self.widgets = {}
        self._options = {}
        self._checks = {}
        self.settings = QSettings('LumaView', 'LumaView')
        self.layout = QVBoxLayout(self)
        self.layout.setContentsMargins(0, 0, 0, 0)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        self.layout.addWidget(self.scroll)
        self.set_format(fmt, options)

    def set_format(self, fmt, options=None):
        aliases = {'JPG':'JPEG', 'TIF':'TIFF', 'JPE':'JPEG', 'JP2':'JPEG2000', 'HEIC':'HEIF'}
        self.fmt = aliases.get(str(fmt).upper(), str(fmt).upper())
        self._options = default_format(self.fmt)
        self._options.update(deepcopy(options or {}))
        self.widgets = {}
        self._checks = {}
        previous = self.scroll.takeWidget()
        if previous:
            previous.deleteLater()
        self.content = QWidget()
        self.body = QVBoxLayout(self.content)
        self.body.setContentsMargins(3, 3, 6, 3)
        self.body.setSpacing(10)
        build = getattr(self, '_build_' + self.fmt.lower(), self._build_generic)
        build()
        self._build_presets()
        self.body.addStretch()
        self.scroll.setWidget(self.content)
        self.changed.emit()

    def _group(self, title):
        group = QGroupBox(title)
        layout = QVBoxLayout(group)
        layout.setSpacing(6)
        self.body.addWidget(group)
        return layout

    def _check(self, layout, key, label):
        widget = QCheckBox(label)
        widget.setChecked(bool(self._options[key]))
        widget.toggled.connect(lambda *_: self.changed.emit())
        self.widgets[key] = widget
        layout.addWidget(widget)
        return widget

    def _spin(self, key, minimum=0, maximum=100, decimals=0, suffix=''):
        widget = QDoubleSpinBox() if decimals else QSpinBox()
        if decimals:
            widget.setDecimals(decimals)
        widget.setRange(minimum, maximum)
        widget.setValue(self._options[key])
        widget.setSuffix(suffix)
        widget.setKeyboardTracking(False)
        widget.valueChanged.connect(lambda *_: self.changed.emit())
        self.widgets[key] = widget
        return widget

    def _combo(self, key, choices):
        widget = QComboBox()
        for label, value in choices:
            widget.addItem(label, value)
        index = widget.findData(self._options[key])
        widget.setCurrentIndex(max(0, index))
        widget.currentIndexChanged.connect(lambda *_: self.changed.emit())
        self.widgets[key] = widget
        return widget

    def _line(self, key, password=False):
        widget = QLineEdit(str(self._options[key]))
        if password:
            widget.setEchoMode(QLineEdit.EchoMode.Password)
        widget.textChanged.connect(lambda *_: self.changed.emit())
        self.widgets[key] = widget
        return widget

    def _color(self, key):
        widget = ColorField(self._options[key])
        widget.edit.textChanged.connect(lambda *_: self.changed.emit())
        self.widgets[key] = widget
        return widget

    def _row(self, layout, label, widget):
        row = QHBoxLayout()
        row.addWidget(QLabel(label))
        row.addStretch()
        row.addWidget(widget, 1)
        layout.addLayout(row)
        return widget

    def _quality(self, layout, key='quality'):
        container = QWidget()
        row = QHBoxLayout(container)
        row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(QLabel('Quality'))
        slider = QSlider(Qt.Orientation.Horizontal)
        slider.setRange(1, 100)
        spin = self._spin(key, 1, 100)
        slider.setValue(spin.value())
        slider.valueChanged.connect(spin.setValue)
        spin.valueChanged.connect(slider.setValue)
        row.addWidget(slider, 1)
        row.addWidget(spin)
        layout.addWidget(container)
        return container

    def _build_jpeg(self):
        layout = self._group('JPEG')
        self._quality(layout)
        self._check(layout, 'progressive', 'Save as progressive JPEG')
        self._check(layout, 'grayscale', 'Save as grayscale')
        self._check(layout, 'no_subsampling', 'Disable chroma subsampling (4:4:4)')
        self._check(layout, 'original_quality', 'Try to keep original JPEG quality')
        self._check(layout, 'reset_orientation', 'Reset EXIF orientation tag')
        self._check(layout, 'preview', 'Show preview before saving')
        row = QHBoxLayout()
        target = QCheckBox('Target file size')
        target.setChecked(self._options['target_kb'] > 0)
        target.toggled.connect(lambda *_: self.changed.emit())
        size = self._spin('target_kb', 0.01, 9999999, 2, ' KB')
        if self._options['target_kb'] <= 0:
            size.setValue(150)
        size.setEnabled(target.isChecked())
        target.toggled.connect(size.setEnabled)
        self._checks['target_kb'] = target
        row.addWidget(target)
        row.addWidget(size)
        layout.addLayout(row)
        metadata = self._group('Metadata')
        for key, label in [('keep_exif', 'Keep EXIF'), ('keep_iptc', 'Keep IPTC'), ('keep_comment', 'Keep JPEG comment'),
                           ('keep_xmp', 'Keep XMP'), ('keep_icc', 'Keep ICC color profile')]:
            self._check(metadata, key, label)
        self._row(layout, 'Alpha background', self._color('background'))

    def _build_gif(self):
        layout = self._group('GIF')
        self._check(layout, 'gif_interlace', 'Save interlaced')
        self._check(layout, 'all_pages', 'Save all frames')
        self._transparency(layout, alpha=False, indexed=True)

    def _transparency(self, layout, alpha=True, indexed=False):
        custom = self._check(layout, 'custom_transparency', 'Use a custom transparent color')
        if indexed:
            mode = self._combo('transparency_mode', [('Color', 'color'), ('Palette index', 'index')])
            self._row(layout, 'Transparency', mode)
            index = self._spin('transparency_index', 0, 255)
            self._row(layout, 'Palette index', index)
            mode.setEnabled(custom.isChecked())
            custom.toggled.connect(mode.setEnabled)
            def toggle_index():
                index.setEnabled(custom.isChecked() and mode.currentData() == 'index')
            custom.toggled.connect(toggle_index)
            mode.currentIndexChanged.connect(toggle_index)
            toggle_index()
        color = self._color('transparent_color')
        self._row(layout, 'Transparent color', color)
        def toggle_color():
            color.setEnabled(custom.isChecked() and (not indexed or mode.currentData() == 'color'))
        custom.toggled.connect(toggle_color)
        if indexed:
            mode.currentIndexChanged.connect(toggle_color)
        toggle_color()
        if alpha:
            self._check(layout, 'transparency_alpha', 'Save transparency as alpha channel')

    def _build_png(self):
        layout = self._group('PNG')
        self._row(layout, 'Compression (0–9)', self._spin('compression', 0, 9))
        self._transparency(layout)
        self._check(layout, 'all_pages', 'Save all frames (APNG)')
        self._check(layout, 'keep_icc', 'Keep ICC color profile')
        opt = self._check(layout, 'optimize_png', 'Optimize with OptiPNG')
        level = self._spin('optipng_level', 0, 7)
        self._row(layout, 'Optimization level (0–7)', level)
        available = bool(find_executable('optipng'))
        if not available:
            opt.setEnabled(False)
            opt.setChecked(False)
            hint = QLabel('OptiPNG is not installed.')
            hint.setWordWrap(True)
            hint.setStyleSheet('color: palette(mid);')
            layout.addWidget(hint)
        level.setEnabled(available and opt.isChecked())
        opt.toggled.connect(level.setEnabled)

    def _build_tiff(self):
        layout = self._group('TIFF')
        compression = self._combo('tiff_compression', [
            ('None', 'raw'), ('LZW', 'tiff_lzw'), ('PackBits', 'packbits'),
            ('JPEG', 'jpeg'), ('ZIP / Deflate', 'tiff_adobe_deflate'),
            ('Huffman RLE (1-bit)', 'tiff_ccitt'), ('CCITT Fax 3 (1-bit)', 'group3'),
            ('CCITT Fax 4 (1-bit)', 'group4')])
        self._row(layout, 'Compression', compression)
        quality = self._quality(layout)
        quality.setEnabled(compression.currentData() == 'jpeg')
        compression.currentIndexChanged.connect(lambda _: quality.setEnabled(compression.currentData() == 'jpeg'))
        method = self._combo('tiff_bilevel_method', [
            ('Adaptive (IrfanView style)', 'adaptive'),
            ('High contrast (legacy)', 'compact'),
            ('Floyd–Steinberg (photos)', 'floyd_steinberg'), ('Threshold (text / line art)', 'threshold')])
        self._row(layout, 'Black & white', method)
        alpha = self._check(layout, 'tiff_adaptive_alpha_background', 'Blend alpha with background')
        alpha.setToolTip('Adaptive mode otherwise uses the stored RGB values, matching IrfanView\'s automatic TIFF conversion.')
        contrast = self._spin('tiff_bilevel_contrast', 1, 6, decimals=1)
        self._row(layout, 'B&W contrast', contrast)
        threshold = self._spin('tiff_threshold', 0, 255)
        self._row(layout, 'Threshold', threshold)
        method.setToolTip('Used when Fax/Huffman converts an image to 1-bit. Adaptive selects two colors and diffuses a bounded error before mapping to black and white. Already 1-bit images are unchanged.')
        def update_bilevel_controls(*_):
            bilevel = compression.currentData() in {'tiff_ccitt', 'group3', 'group4'}
            method.setEnabled(bilevel)
            alpha.setEnabled(bilevel and method.currentData() == 'adaptive')
            contrast.setEnabled(bilevel and method.currentData() == 'compact')
            threshold.setEnabled(bilevel and method.currentData() == 'threshold')
        compression.currentIndexChanged.connect(update_bilevel_controls)
        method.currentIndexChanged.connect(update_bilevel_controls)
        update_bilevel_controls()
        self._check(layout, 'all_pages', 'Save all pages from original image')
        self._check(layout, 'tiff_gray_palette', 'Save palette for grayscale images')
        self._check(layout, 'keep_exif', 'Keep EXIF')
        self._check(layout, 'keep_icc', 'Keep ICC color profile')
        self._row(layout, 'Alpha background', self._color('background'))

    def _build_pdf(self):
        layout = self._group('PDF')
        compression = self._combo('pdf_compression', [('JPEG', 'jpeg'), ('Lossless', 'lossless')])
        self._row(layout, 'Compression', compression)
        quality = self._quality(layout, 'pdf_quality')
        quality.setEnabled(compression.currentData() == 'jpeg')
        compression.currentIndexChanged.connect(lambda _: quality.setEnabled(compression.currentData() == 'jpeg'))
        self._row(layout, 'Resolution', self._spin('pdf_dpi', 1, 9600, suffix=' DPI'))
        size = self._combo('pdf_page_size', [('Image size', 'image'), ('A4', 'A4'), ('A3', 'A3'), ('Letter', 'Letter'), ('Legal', 'Legal')])
        self._row(layout, 'Page size', size)
        landscape = self._check(layout, 'pdf_landscape', 'Landscape')
        fit = self._check(layout, 'pdf_fit', 'Fit image to page')
        landscape.setEnabled(size.currentData() != 'image')
        fit.setEnabled(size.currentData() != 'image')
        size.currentIndexChanged.connect(lambda _: landscape.setEnabled(size.currentData() != 'image'))
        size.currentIndexChanged.connect(lambda _: fit.setEnabled(size.currentData() != 'image'))
        self._check(layout, 'all_pages', 'Save all pages')
        self._row(layout, 'Background', self._color('background'))
        metadata = self._group('Document')
        form = QFormLayout()
        form.addRow('Title', self._line('pdf_title'))
        form.addRow('Author', self._line('pdf_author'))
        form.addRow('Password', self._line('pdf_password', True))
        metadata.addLayout(form)

    def _build_ico(self):
        layout = self._group('ICO')
        self._transparency(layout)
        self.icon_checks = []
        for value in (16, 24, 32, 48, 64, 128, 256):
            check = QCheckBox(f'{value} × {value}')
            check.setChecked(value in self._options['ico_sizes'])
            check.toggled.connect(lambda *_: self.changed.emit())
            layout.addWidget(check)
            self.icon_checks.append((value, check))

    def _build_pbm(self):
        layout = self._group(self.fmt)
        self._check(layout, 'pnm_ascii', 'ASCII encoding')
        self._row(layout, 'Background', self._color('background'))

    _build_pgm = _build_pbm
    _build_ppm = _build_pbm

    def _build_raw(self):
        layout = self._group('RAW pixel data')
        self._row(layout, 'Channels', self._combo('raw_mode', [('RGB', 'RGB'), ('RGBA', 'RGBA'), ('Grayscale', 'L')]))

    def _build_generic(self):
        layout = self._group(self.fmt)
        if self.fmt in ('WEBP', 'AVIF', 'HEIF', 'JXL', 'JPEG2000'):
            self._quality(layout)
            lossless = self._check(layout, 'lossless', 'Lossless')
            if self.fmt == 'AVIF':
                lossless.setChecked(False)
                lossless.setEnabled(False)
                lossless.setToolTip('The AVIF encoder does not support lossless output.')
            self._check(layout, 'keep_icc', 'Keep ICC color profile')
            if self.fmt in ('WEBP', 'AVIF', 'HEIF'):
                self._check(layout, 'all_pages', 'Save all frames')
        self._row(layout, 'Alpha background', self._color('background'))

    def _build_presets(self):
        layout = self._group('Profiles')
        self.preset_names = QComboBox()
        self.preset_names.setEditable(True)
        self.preset_names.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.preset_names.lineEdit().setPlaceholderText('Profile name')
        self.preset_names.addItems(sorted(self._presets()))
        self.preset_names.setCurrentIndex(-1)
        layout.addWidget(self.preset_names)
        row = QHBoxLayout()
        for label, callback in [('Load', self._load_preset), ('Save', self._save_preset), ('Delete', self._delete_preset)]:
            button = QPushButton(label)
            button.clicked.connect(callback)
            row.addWidget(button)
        layout.addLayout(row)

    def _presets(self):
        raw = self.settings.value(f'format_profiles/{self.fmt}', '{}')
        try:
            result = json.loads(raw)
            if isinstance(result, dict):
                for options in result.values():
                    if isinstance(options, dict):
                        options.pop('pdf_password', None)
                return result
            return {}
        except (ValueError, TypeError):
            return {}

    def _save_preset(self):
        name = self.preset_names.currentText().strip()
        if not name:
            name, accepted = QInputDialog.getText(self, 'Save profile', 'Profile name')
            if not accepted or not name.strip():
                return
            name = name.strip()
        presets = self._presets()
        presets[name] = {k: v for k, v in self.values().items() if k != 'pdf_password'}
        self.settings.setValue(f'format_profiles/{self.fmt}', json.dumps(presets))
        if self.preset_names.findText(name) < 0:
            self.preset_names.addItem(name)
        self.preset_names.setCurrentText(name)

    def _load_preset(self):
        name = self.preset_names.currentText().strip()
        value = self._presets().get(name)
        if value is None:
            return
        self.set_format(self.fmt, value)
        self.preset_names.setCurrentText(name)

    def _delete_preset(self):
        name = self.preset_names.currentText().strip()
        presets = self._presets()
        if name in presets:
            del presets[name]
            self.settings.setValue(f'format_profiles/{self.fmt}', json.dumps(presets))
            self.preset_names.removeItem(self.preset_names.findText(name))
            self.preset_names.setCurrentIndex(-1)

    def values(self):
        result = deepcopy(self._options)
        for key, widget in self.widgets.items():
            if isinstance(widget, QCheckBox):
                result[key] = widget.isChecked()
            elif isinstance(widget, QComboBox):
                result[key] = widget.currentData()
            elif isinstance(widget, QLineEdit):
                result[key] = widget.text()
            else:
                result[key] = widget.value()
        for key, check in self._checks.items():
            if not check.isChecked():
                result[key] = 0
        if self.fmt == 'ICO':
            result['ico_sizes'] = [value for value, check in self.icon_checks if check.isChecked()]
        return result

    def validate(self):
        values = self.values()
        for key in ('transparent_color', 'background'):
            if key in self.widgets and not QColor(values[key]).isValid():
                raise ValueError('Use a valid color name or #RRGGBB.')
        if self.fmt == 'ICO' and not values['ico_sizes']:
            raise ValueError('Select at least one icon size.')
        if self.fmt == 'JPEG' and values['original_quality'] and values['target_kb'] > 0:
            raise ValueError('Choose either original JPEG quality or a target file size.')
        return values


class FormatOptionsDialog(QDialog):
    def __init__(self, fmt='PNG', options=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f'{fmt} save options')
        self.resize(420, 710)
        self.options = deepcopy(options or default_format(fmt))
        layout = QVBoxLayout(self)
        self.panel = FormatOptionsPanel(fmt, self.options, self)
        layout.addWidget(self.panel)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def accept(self):
        try:
            self.options = self.panel.validate()
        except ValueError as error:
            QMessageBox.warning(self, 'Invalid options', str(error))
            return
        super().accept()
