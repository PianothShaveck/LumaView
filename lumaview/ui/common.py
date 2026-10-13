from pathlib import Path
from PIL import Image
from PySide6.QtCore import Qt
from PySide6.QtGui import QImage, QPixmap, QPalette, QColor
from PySide6.QtWidgets import QApplication, QMessageBox

STYLE = '''
QWidget { font-family: "Inter", "Helvetica Neue", "Segoe UI", sans-serif; font-size: 12px; color: #243044; }
QMainWindow, QDialog { background: #f4f6f8; }
QMenuBar, QMenu, QToolBar { background: #ffffff; }
QMenuBar { padding: 3px; border-bottom: 1px solid #dce2ea; }
QMenu::item { padding: 6px 28px 6px 16px; }
QMenu::item:selected { background: #e6efff; color: #194fba; }
QToolBar { spacing: 5px; padding: 6px; border: none; border-bottom: 1px solid #dce2ea; }
QToolButton { padding: 6px 10px; border-radius: 5px; }
QToolButton:hover { background: #e9eff8; }
QPushButton { background: #fff; border: 1px solid #cbd3df; border-radius: 5px; padding: 6px 12px; min-height: 18px; }
QPushButton:hover { background: #eaf0fa; border-color: #a2b7d8; }
QPushButton:pressed { background: #dbe7fa; }
QPushButton:default, QPushButton[primary="true"] { background: #2563eb; color: white; border-color: #2563eb; }
QPushButton:disabled { color: #97a0ae; background: #f1f3f6; border-color: #dce2ea; }
QGroupBox { background: #ffffff; border: 1px solid #dce2ea; border-radius: 7px; margin-top: 15px; padding: 12px 9px 9px; font-weight: 600; }
QGroupBox::title { subcontrol-origin: margin; left: 9px; padding: 0 4px; }
QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox, QTextEdit, QPlainTextEdit { background: #fff; border: 1px solid #cbd3df; border-radius: 4px; padding: 4px 6px; min-height: 18px; selection-background-color: #2563eb; }
QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus { border-color: #2563eb; }
QLineEdit:disabled, QSpinBox:disabled, QDoubleSpinBox:disabled, QComboBox:disabled { color: #98a0ad; background: #f3f5f8; }
QCheckBox, QRadioButton { spacing: 5px; padding: 2px 0; }
QCheckBox:disabled, QRadioButton:disabled, QLabel:disabled { color: #9ba3b0; }
QTreeView, QListWidget, QListView, QTableWidget { background: #fff; border: 1px solid #dce2ea; border-radius: 4px; alternate-background-color: #f7f9fc; }
QTreeView::item, QListWidget::item { padding: 4px; }
QTreeView::item:selected, QListWidget::item:selected { background: #e1ebff; color: #1d4d9d; }
QHeaderView::section { background: #f1f4f9; border: none; padding: 6px; color: #627087; }
QStatusBar { background: #fff; border-top: 1px solid #dce2ea; color: #69768b; }
QSplitter::handle { background: #e5eaf1; }
QScrollBar:vertical { background: #f2f4f7; width: 10px; }
QScrollBar::handle:vertical { background: #bfc8d5; border-radius: 4px; min-height: 24px; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QTabWidget::pane { border: 1px solid #dce2ea; }
QTabBar::tab { background: #edf1f6; padding: 8px 16px; }
QTabBar::tab:selected { background: #fff; color: #2563eb; }
QProgressBar { border: 1px solid #dce2ea; border-radius: 4px; text-align: center; background: #edf1f6; }
QProgressBar::chunk { background: #5788ed; border-radius: 3px; }
'''

def apply_style(app):
    app.setStyle('Fusion')
    palette = QPalette()
    palette.setColor(QPalette.ColorRole.Window, QColor('#f4f6f8'))
    palette.setColor(QPalette.ColorRole.WindowText, QColor('#243044'))
    palette.setColor(QPalette.ColorRole.Base, QColor('#ffffff'))
    palette.setColor(QPalette.ColorRole.Text, QColor('#243044'))
    palette.setColor(QPalette.ColorRole.Button, QColor('#ffffff'))
    palette.setColor(QPalette.ColorRole.ButtonText, QColor('#243044'))
    palette.setColor(QPalette.ColorRole.Highlight, QColor('#2563eb'))
    palette.setColor(QPalette.ColorRole.HighlightedText, QColor('#ffffff'))
    app.setPalette(palette)
    app.setStyleSheet(STYLE)

def pil_to_pixmap(im):
    rgba = im.convert('RGBA')
    data = rgba.tobytes('raw', 'RGBA')
    qi = QImage(data, rgba.width, rgba.height, rgba.width * 4, QImage.Format.Format_RGBA8888).copy()
    return QPixmap.fromImage(qi)

def qimage_to_pil(qi):
    qi = qi.convertToFormat(QImage.Format.Format_RGBA8888)
    data = bytes(qi.constBits())
    return Image.frombytes('RGBA', (qi.width(),qi.height()),data,'raw','RGBA',qi.bytesPerLine())

def error(parent, exc, title='LumaView'):
    QMessageBox.critical(parent,title,str(exc))

def resource_path(relative):
    import sys
    base = Path(getattr(sys,'_MEIPASS', Path(__file__).resolve().parents[2]))
    return base / relative
