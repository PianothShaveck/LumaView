"""Render the canonical SVG to PNG and a multi-resolution macOS icon."""
import os
from pathlib import Path
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PIL import Image
from PySide6.QtGui import QImage, QPainter
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import QApplication

def build_icon():
    assets = Path(__file__).resolve().parents[1] / 'assets'
    app = QApplication.instance() or QApplication([])
    renderer = QSvgRenderer(str(assets / 'lumaview.svg'))
    if not renderer.isValid():
        raise ValueError('Invalid icon SVG')
    image = QImage(1024, 1024, QImage.Format.Format_ARGB32)
    image.fill(0)
    painter = QPainter(image)
    renderer.render(painter)
    painter.end()
    if not image.save(str(assets / 'lumaview.png')):
        raise RuntimeError('Could not write icon PNG')
    with Image.open(assets / 'lumaview.png') as source:
        source.save(assets / 'lumaview.icns', format='ICNS')
    return app

if __name__ == '__main__':
    build_icon()
