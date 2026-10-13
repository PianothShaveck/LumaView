"""Batch transformation controls and portable processing presets."""
from __future__ import annotations
from copy import deepcopy
import json
from pathlib import Path
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QColorDialog, QComboBox, QDialog,
    QDialogButtonBox, QDoubleSpinBox, QFileDialog, QFormLayout, QGridLayout,
    QGroupBox, QHBoxLayout, QLabel, QLineEdit, QListWidget, QMessageBox,
    QPushButton, QRadioButton, QScrollArea, QSpinBox, QVBoxLayout, QWidget,
)
from PySide6.QtGui import QColor
from ..model import default_advanced, OPERATIONS

ANCHORS = ['top-left', 'top-center', 'top-right', 'center-left', 'center',
           'center-right', 'bottom-left', 'bottom-center', 'bottom-right']


class ColorField(QWidget):
    """Editable color plus a system color picker."""
    def __init__(self, value='#ffffff', parent=None, allow_empty=False):
        super().__init__(parent)
        self.edit = QLineEdit(str(value))
        self.edit.setMinimumWidth(75)
        self.button = QPushButton('…')
        self.button.setFixedWidth(30)
        self.button.setStyleSheet('padding: 0;')
        self.button.setToolTip('Choose color')
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        layout.addWidget(self.edit)
        layout.addWidget(self.button)
        self.button.clicked.connect(self.choose)
        if allow_empty:
            self.edit.setPlaceholderText('None')

    def choose(self):
        color = QColorDialog.getColor(QColor(self.edit.text() or '#ffffff'), self)
        if color.isValid():
            self.edit.setText(color.name())

    def value(self):
        return self.edit.text().strip()

    def setValue(self, value):
        self.edit.setText(str(value))


class _Fields:
    """Small option binder shared by the dialog and its subdialogs."""
    def __init__(self, data):
        self.data = data
        self.widgets = {}

    def check(self, key, label):
        widget = QCheckBox(label)
        widget.setChecked(bool(self.data[key]))
        self.widgets[key] = widget
        return widget

    def spin(self, key, minimum=0, maximum=99999, decimals=0, suffix=''):
        if decimals:
            widget = QDoubleSpinBox()
            widget.setDecimals(decimals)
        else:
            widget = QSpinBox()
        widget.setRange(minimum, maximum)
        widget.setValue(self.data[key])
        widget.setSuffix(suffix)
        widget.setMinimumWidth(78)
        widget.setKeyboardTracking(False)
        self.widgets[key] = widget
        return widget

    def combo(self, key, values):
        widget = QComboBox()
        for item in values:
            text, value = item if isinstance(item, tuple) else (item.replace('-', ' ').title(), item)
            widget.addItem(text, value)
        index = widget.findData(self.data[key])
        widget.setCurrentIndex(max(index, 0))
        self.widgets[key] = widget
        return widget

    def line(self, key):
        widget = QLineEdit(str(self.data[key]))
        self.widgets[key] = widget
        return widget

    def color(self, key, allow_empty=False):
        widget = ColorField(self.data[key], allow_empty=allow_empty)
        self.widgets[key] = widget
        return widget

    def values(self):
        result = deepcopy(self.data)
        for key, widget in self.widgets.items():
            if isinstance(widget, (QCheckBox, QGroupBox, QRadioButton)):
                result[key] = widget.isChecked()
            elif isinstance(widget, QComboBox):
                result[key] = widget.currentData()
            elif isinstance(widget, QLineEdit):
                result[key] = widget.text()
            else:
                result[key] = widget.value()
        return result

    def apply(self, values):
        self.data.update(values)
        for key, widget in self.widgets.items():
            if key not in values:
                continue
            value = values[key]
            if isinstance(widget, (QCheckBox, QGroupBox, QRadioButton)):
                widget.setChecked(bool(value))
            elif isinstance(widget, QComboBox):
                index = widget.findData(value)
                if index >= 0:
                    widget.setCurrentIndex(index)
            elif isinstance(widget, QLineEdit):
                widget.setText(str(value))
            else:
                widget.setValue(value)


def _path_row(edit, parent, title, filter_text, directory=False):
    row = QWidget()
    layout = QHBoxLayout(row)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(4)
    layout.addWidget(edit)
    button = QPushButton('Browse…')
    layout.addWidget(button)
    def choose():
        if directory:
            path = QFileDialog.getExistingDirectory(parent, title, edit.text())
        else:
            path, _ = QFileDialog.getOpenFileName(parent, title, edit.text(), filter_text)
        if path:
            edit.setText(path)
    button.clicked.connect(choose)
    return row


class AdvancedDialog(QDialog):
    """All batch processing settings; ``options`` is replaced on acceptance."""
    def __init__(self, options=None, parent=None, selection=None):
        super().__init__(parent)
        self.setWindowTitle('Advanced batch options')
        self.options = default_advanced()
        self.options.update(deepcopy(options or {}))
        self.fields = _Fields(self.options)
        self.selection = selection
        self.mode_buttons = {}
        self.resize(1160, 900)
        self.setMinimumSize(920, 640)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(12, 12, 12, 12)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        content = QWidget()
        columns = QHBoxLayout(content)
        columns.setContentsMargins(0, 0, 0, 0)
        columns.setSpacing(12)
        left, middle, right = QVBoxLayout(), QVBoxLayout(), QVBoxLayout()
        for column in (left, middle, right):
            column.setSpacing(10)
            columns.addLayout(column, 1)
        left.addWidget(self._crop_group())
        left.addWidget(self._resize_group())
        left.addStretch()
        middle.addWidget(self._depth_group())
        middle.addWidget(self._transform_group())
        middle.addStretch()
        right.addWidget(self._adjustments_group())
        right.addWidget(self._misc_group())
        right.addStretch()
        scroll.setWidget(content)
        outer.addWidget(scroll)
        footer = QHBoxLayout()
        load = QPushButton('Load settings…')
        save = QPushButton('Save settings…')
        reset = QPushButton('Reset')
        load.clicked.connect(self._load)
        save.clicked.connect(self._save)
        reset.clicked.connect(lambda: self._apply(default_advanced()))
        footer.addWidget(load)
        footer.addWidget(save)
        footer.addWidget(reset)
        footer.addStretch()
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        footer.addWidget(buttons)
        outer.addLayout(footer)

    def _group(self, title, key=None):
        group = QGroupBox(title)
        if key:
            group.setCheckable(True)
            group.setChecked(self.options[key])
            self.fields.widgets[key] = group
        return group

    def _crop_group(self):
        group = self._group('Crop', 'crop')
        layout = QGridLayout(group)
        for row, (a, b) in enumerate([(('X', 'crop_x'), ('Y', 'crop_y')),
                                     (('Width', 'crop_width'), ('Height', 'crop_height'))]):
            for col, (label, key) in enumerate((a, b)):
                layout.addWidget(QLabel(label), row * 2, col)
                layout.addWidget(self.fields.spin(key, -999999 if key.endswith(('_x', '_y')) else 0, 999999), row * 2 + 1, col)
        layout.addWidget(QLabel('Start corner'), 4, 0)
        layout.addWidget(self.fields.combo('crop_anchor', ANCHORS), 4, 1)
        button = QPushButton('Get current selection')
        button.setEnabled(self.selection is not None)
        button.clicked.connect(self._get_selection)
        layout.addWidget(button, 5, 0, 1, 2)
        return group

    def _get_selection(self):
        value = self.selection() if callable(self.selection) else self.selection
        if value is None:
            return
        if hasattr(value, 'getRect'):
            x, y, width, height = value.getRect()
        elif isinstance(value, dict):
            x, y, width, height = [value.get(k, 0) for k in ('x', 'y', 'width', 'height')]
        else:
            x, y, width, height = value
        self.fields.apply({'crop_x': int(x), 'crop_y': int(y), 'crop_width': int(width),
                           'crop_height': int(height), 'crop_anchor': 'top-left'})

    def _resize_group(self):
        group = self._group('Resize', 'resize')
        layout = QVBoxLayout(group)
        layout.setSpacing(6)
        size_grid = QGridLayout()
        size_grid.setSpacing(5)
        modes = [('Set one or both sides', 'dimensions'), ('Set long side', 'long_side'),
                 ('Set short side', 'short_side'), ('Set megapixels', 'megapixels'),
                 ('Percentage of original', 'percent')]
        for i, (label, mode) in enumerate(modes):
            button = QRadioButton(label)
            button.setChecked(self.options['resize_mode'] == mode)
            button.toggled.connect(lambda checked, m=mode: self._set_resize_mode(m) if checked else None)
            self.mode_buttons[mode] = button
            size_grid.addWidget(button, i * 2, 0, 1, 2)
            if mode in ('dimensions', 'percent'):
                row = QHBoxLayout()
                keys = ('width', 'height') if mode == 'dimensions' else ('percent_width', 'percent_height')
                for label2, key in zip(('W', 'H'), keys):
                    row.addWidget(QLabel(label2))
                    spin = self.fields.spin(key, 0 if mode == 'dimensions' else 0.01, 999999, 2, '%' if mode == 'percent' else '')
                    row.addWidget(spin)
                    button.toggled.connect(spin.setEnabled)
                    spin.setEnabled(button.isChecked())
                size_grid.addLayout(row, i * 2 + 1, 0, 1, 2)
            else:
                spin = self.fields.spin(mode, 0.01, 999999, 2)
                size_grid.addWidget(spin, i * 2 + 1, 0, 1, 2)
                button.toggled.connect(spin.setEnabled)
                spin.setEnabled(button.isChecked())
        layout.addLayout(size_grid)
        unitrow = QHBoxLayout()
        unitrow.addWidget(QLabel('Units'))
        unitrow.addWidget(self.fields.combo('units', [('Pixels', 'pixels'), ('Centimeters', 'cm'), ('Inches', 'inches')]), 1)
        layout.addLayout(unitrow)
        layout.addWidget(self.fields.check('preserve_aspect', 'Preserve aspect ratio'))
        resample = self.fields.check('resample', 'Use Resample function (better quality)')
        font = resample.font()
        font.setBold(True)
        resample.setFont(font)
        layout.addWidget(resample)
        filters = self.fields.combo('resample_filter', [(v, v) for v in ['Lanczos', 'Bicubic', 'Bilinear', 'Box', 'Hamming', 'Nearest']])
        filters.setEnabled(resample.isChecked())
        resample.toggled.connect(filters.setEnabled)
        layout.addWidget(filters)
        layout.addWidget(self.fields.combo('size_bound', [('Maximum size (fit inside)', 'fit'), ('Minimum size (fill)', 'fill')]))
        layout.addWidget(self.fields.check('dpi_only', 'Resize using new / old DPI only'))
        layout.addWidget(self.fields.check('no_enlarge', 'Do not enlarge smaller images'))
        layout.addWidget(self.fields.check('no_shrink', 'Do not shrink larger images'))
        dpirow = QHBoxLayout()
        dpirow.addWidget(QLabel('New DPI'))
        dpi = self.fields.spin('new_dpi', 0, 9600, 1)
        dpi.setSpecialValueText('Unchanged')
        dpirow.addWidget(dpi)
        layout.addLayout(dpirow)
        return group

    def _set_resize_mode(self, mode):
        self.fields.data['resize_mode'] = mode

    def _depth_group(self):
        group = self._group('Change color depth', 'depth')
        layout = QVBoxLayout(group)
        mode = self.fields.combo('depth_mode', [('32 BPP (alpha)', '32'), ('24 BPP (16.7 million colors)', '24'),
            ('RGB565 colors', '16'), ('8 BPP (256 colors)', '8'), ('4 BPP (16 colors)', '4'),
            ('1 BPP (black / white)', '1'), ('Custom colors', 'custom')])
        layout.addWidget(mode)
        row = QHBoxLayout()
        row.addWidget(QLabel('Custom colors'))
        colors = self.fields.spin('custom_colors', 2, 256)
        colors.setEnabled(mode.currentData() == 'custom')
        mode.currentIndexChanged.connect(lambda _: colors.setEnabled(mode.currentData() == 'custom'))
        row.addWidget(colors)
        layout.addLayout(row)
        layout.addWidget(self.fields.check('dither', 'Use Floyd–Steinberg dithering'))
        layout.addWidget(self.fields.check('best_palette', 'Use best color quality'))
        return group

    def _transform_group(self):
        group = self._group('Transform')
        layout = QGridLayout(group)
        row = 0
        for key, label in [('auto_colors', 'Auto-adjust colors'), ('flip_horizontal', 'Horizontal flip'),
            ('flip_vertical', 'Vertical flip'), ('rotate_left', 'Rotate left'), ('rotate_right', 'Rotate right'),
            ('grayscale', 'Convert to grayscale'), ('negative', 'Negative')]:
            layout.addWidget(self.fields.check(key, label), row, 0, 1, 2)
            row += 1
        autocrop = self.fields.check('auto_crop', 'Auto-crop borders')
        layout.addWidget(autocrop, row, 0)
        tolerance = self.fields.spin('auto_crop_tolerance', 0, 255)
        tolerance.setToolTip('Color tolerance')
        tolerance.setEnabled(autocrop.isChecked())
        autocrop.toggled.connect(tolerance.setEnabled)
        layout.addWidget(tolerance, row, 1)
        row += 1
        for key, label in [('canvas', 'Canvas size'), ('text', 'Add overlay text'),
                           ('watermark', 'Add watermark image'), ('replace_color', 'Replace color'),
                           ('border', 'Add border / frame')]:
            layout.addWidget(self.fields.check(key, label), row, 0)
            button = QPushButton('Settings…')
            button.clicked.connect(lambda _=False, k=key: self._settings(k))
            layout.addWidget(button, row, 1)
            row += 1
        layout.addWidget(QLabel('RGB channels'), row, 0)
        layout.addWidget(self.fields.combo('swap_channels', [(x, x) for x in ['RGB', 'RBG', 'BGR', 'BRG', 'GRB', 'GBR']]), row, 1)
        return group

    def _adjustments_group(self):
        group = self._group('Adjustments')
        layout = QGridLayout(group)
        adjustments = [('sharpen', 'Sharpen', 1, 99, 0), ('brightness', 'Brightness', -255, 255, 0),
            ('contrast', 'Contrast', -127, 127, 0), ('gamma', 'Gamma correction', .01, 6.99, 2),
            ('saturation', 'Saturation', -255, 255, 0), ('balance_r', 'Color balance: R', -255, 255, 0),
            ('balance_g', 'Color balance: G', -255, 255, 0), ('balance_b', 'Color balance: B', -255, 255, 0),
            ('blur', 'Blur filter', 1, 99, 0)]
        for row, (key, title, low, high, decimals) in enumerate(adjustments):
            check = self.fields.check(key, title)
            spin = self.fields.spin(key + '_value', low, high, decimals)
            spin.setEnabled(check.isChecked())
            check.toggled.connect(spin.setEnabled)
            layout.addWidget(check, row, 0)
            layout.addWidget(spin, row, 1)
        row = len(adjustments)
        layout.addWidget(self.fields.check('fine_rotation', 'Fine rotation'), row, 0)
        button = QPushButton('Settings…')
        button.clicked.connect(lambda: self._settings('fine_rotation'))
        layout.addWidget(button, row, 1)
        layout.addWidget(self.fields.check('effects', 'More effects'), row + 1, 0)
        button = QPushButton('Settings…')
        button.clicked.connect(lambda: self._settings('effects'))
        layout.addWidget(button, row + 1, 1)
        return group

    def _misc_group(self):
        group = self._group('Miscellaneous')
        layout = QVBoxLayout(group)
        for key, label in [('overwrite', 'Overwrite existing files'), ('delete_originals', 'Delete originals after conversion'),
            ('create_subfolders', 'Create subfolders in destination'), ('keep_datetime', 'Keep original date / time'),
            ('all_pages', 'Apply changes to all pages')]:
            layout.addWidget(self.fields.check(key, label))
        row = QHBoxLayout()
        row.addWidget(self.fields.check('custom_order', 'Custom processing order'))
        button = QPushButton('Edit…')
        button.clicked.connect(self._order)
        row.addWidget(button)
        layout.addLayout(row)
        return group

    def _settings(self, section):
        self.fields.data.update(self.fields.values())
        fields = _Fields(deepcopy(self.fields.data))
        dialog = QDialog(self)
        titles = {'canvas': 'Canvas size', 'text': 'Overlay text', 'watermark': 'Watermark image',
                  'replace_color': 'Replace color', 'border': 'Border / frame',
                  'fine_rotation': 'Fine rotation', 'effects': 'Effects'}
        dialog.setWindowTitle(titles[section])
        dialog.setMinimumWidth(440)
        outer = QVBoxLayout(dialog)
        form = QFormLayout()
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        outer.addLayout(form)
        def spin(label, key, low=0, high=99999, decimals=0, suffix=''):
            form.addRow(label, fields.spin(key, low, high, decimals, suffix))
        def anchor(key):
            form.addRow('Anchor', fields.combo(key, ANCHORS))
        if section == 'canvas':
            spin('Width', 'canvas_width', -99999)
            spin('Height', 'canvas_height', -99999)
            form.addRow(fields.check('canvas_relative', 'Relative to current size'))
            anchor('canvas_anchor')
            form.addRow('Color', fields.color('canvas_color'))
        elif section == 'text':
            form.addRow('Text', fields.line('text_value'))
            tokens = QLabel('$N name · $F filename · $E extension · $D folder · ### counter')
            tokens.setWordWrap(True)
            form.addRow(tokens)
            form.addRow('Font file', _path_row(fields.line('text_font'), dialog, 'Choose font', 'Fonts (*.ttf *.otf *.ttc);;All files (*)'))
            spin('Font size', 'text_size', 1, 2000)
            form.addRow('Text color', fields.color('text_color'))
            form.addRow('Background', fields.color('text_background', True))
            spin('Opacity', 'text_opacity', 0, 100, suffix='%')
            anchor('text_anchor')
            spin('X offset', 'text_x', -99999)
            spin('Y offset', 'text_y', -99999)
        elif section == 'watermark':
            form.addRow('Image', _path_row(fields.line('watermark_path'), dialog, 'Choose watermark', 'Images (*.png *.jpg *.jpeg *.tif *.tiff *.webp *.gif);;All files (*)'))
            spin('Opacity', 'watermark_opacity', 0, 100, suffix='%')
            spin('Scale', 'watermark_scale', 1, 10000, suffix='%')
            anchor('watermark_anchor')
            spin('X offset', 'watermark_x', -99999)
            spin('Y offset', 'watermark_y', -99999)
        elif section == 'replace_color':
            form.addRow('Replace', fields.color('replace_from'))
            form.addRow('With', fields.color('replace_to'))
            spin('Tolerance', 'replace_tolerance', 0, 255)
        elif section == 'border':
            for side in ('left', 'top', 'right', 'bottom'):
                spin(side.title(), 'border_' + side, 0)
            form.addRow('Color', fields.color('border_color'))
        elif section == 'fine_rotation':
            spin('Angle', 'rotation_angle', -360, 360, 2, '°')
            form.addRow(fields.check('rotation_expand', 'Expand canvas to contain image'))
            form.addRow('Background', fields.color('rotation_color'))
        elif section == 'effects':
            form.addRow('Effect', fields.combo('effect', ['emboss', 'edge', 'contour', 'smooth', 'solarize', 'posterize', 'sepia']))
            spin('Amount / threshold', 'effect_value', 0, 255, 2)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        outer.addWidget(buttons)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            updated = {key: fields.values()[key] for key in fields.widgets}
            self.fields.apply(updated)

    def _order(self):
        dialog = QDialog(self)
        dialog.setWindowTitle('Processing order')
        dialog.resize(370, 510)
        layout = QVBoxLayout(dialog)
        listing = QListWidget()
        listing.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        listing.setDefaultDropAction(Qt.DropAction.MoveAction)
        order = self.fields.data.get('order', OPERATIONS)
        order = [key for key in order if key in OPERATIONS]
        order += [key for key in OPERATIONS if key not in order]
        for key in order:
            listing.addItem(key.replace('_', ' ').title())
            listing.item(listing.count() - 1).setData(Qt.ItemDataRole.UserRole, key)
        layout.addWidget(listing)
        actions = QHBoxLayout()
        for label, direction in [('Move up', -1), ('Move down', 1)]:
            button = QPushButton(label)
            def move(_=False, delta=direction):
                row = listing.currentRow()
                if row >= 0 and 0 <= row + delta < listing.count():
                    item = listing.takeItem(row)
                    listing.insertItem(row + delta, item)
                    listing.setCurrentRow(row + delta)
            button.clicked.connect(move)
            actions.addWidget(button)
        layout.addLayout(actions)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.fields.data['order'] = [listing.item(i).data(Qt.ItemDataRole.UserRole) for i in range(listing.count())]

    def _values(self):
        values = self.fields.values()
        values['resize_mode'] = next((key for key, button in self.mode_buttons.items() if button.isChecked()), 'dimensions')
        return values

    def _apply(self, values):
        self.fields.apply(values)
        mode = values.get('resize_mode', 'dimensions')
        if mode in self.mode_buttons:
            self.mode_buttons[mode].setChecked(True)

    def _load(self):
        path, _ = QFileDialog.getOpenFileName(self, 'Load processing settings', '', 'JSON settings (*.json)')
        if not path:
            return
        try:
            values = json.loads(Path(path).read_text('utf-8'))
            if 'options' in values:
                values = values['options']
            if not isinstance(values, dict):
                raise ValueError('Settings must be a JSON object.')
            defaults = default_advanced()
            for key, value in values.items():
                if key in defaults:
                    expected = defaults[key]
                    if isinstance(expected, bool) and not isinstance(value, bool):
                        raise ValueError(f'Invalid value for {key}.')
                    if isinstance(expected, (int, float)) and not isinstance(expected, bool) and not isinstance(value, (int, float)):
                        raise ValueError(f'Invalid value for {key}.')
                    if isinstance(expected, str) and not isinstance(value, str):
                        raise ValueError(f'Invalid value for {key}.')
                    if key == 'order' and (not isinstance(value, list) or any(v not in OPERATIONS for v in value)):
                        raise ValueError('Invalid processing order.')
                    defaults[key] = value
            self._apply(defaults)
        except (OSError, ValueError, TypeError) as error:
            QMessageBox.warning(self, 'Cannot load settings', str(error))

    def _save(self):
        path, _ = QFileDialog.getSaveFileName(self, 'Save processing settings', 'batch-settings.json', 'JSON settings (*.json)')
        if path:
            try:
                Path(path).write_text(json.dumps({'version': 1, 'options': self._values()}, indent=2), 'utf-8')
            except OSError as error:
                QMessageBox.warning(self, 'Cannot save settings', str(error))

    def accept(self):
        values = self._values()
        for key in ('canvas_color', 'text_color', 'text_background', 'replace_from', 'replace_to', 'border_color', 'rotation_color'):
            value = values[key]
            if value and not QColor(value).isValid():
                QMessageBox.warning(self, 'Invalid color', f'{key.replace("_", " ").title()}: use a color name or #RRGGBB.')
                return
        self.options = values
        super().accept()
