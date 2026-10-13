"""Application entry point, including Finder open events and CI smoke test."""
import os,sys
from pathlib import Path
from PySide6.QtCore import QEvent,QTimer,QSettings
from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QIcon
from lumaview import __version__
from lumaview.ui.common import apply_style,resource_path

class Application(QApplication):
    def __init__(self,args):
        super().__init__(args);self.window=None;self.pending=[]
    def event(self,event):
        if event.type()==QEvent.Type.FileOpen:
            if self.window:self.window.open_path(Path(event.file()))
            else:self.pending.append(event.file())
            return True
        return super().event(event)

def main():
    smoke='--smoke-test' in sys.argv
    if smoke and sys.platform!='darwin':os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
    if sys.platform=='darwin':
        os.environ['PATH'] = os.environ.get('PATH','') + ':/opt/homebrew/bin:/usr/local/bin:/usr/bin'
    # Cocoa builds its application menu during QApplication construction.
    # Set the identity first so it never caches Python's __main__.py name.
    QApplication.setApplicationName('LumaView')
    QApplication.setApplicationDisplayName('LumaView')
    QApplication.setOrganizationName('LumaView')
    QApplication.setApplicationVersion(__version__)
    app=Application(['LumaView']+[x for x in sys.argv[1:] if not x.startswith('--smoke')])
    from lumaview.ui.settings import upgrade_settings
    upgrade_settings(QSettings('LumaView', 'LumaView'))
    apply_style(app)
    icon=resource_path('assets/lumaview.png')
    if icon.exists():app.setWindowIcon(QIcon(str(icon)))
    from lumaview.ui.viewer import ViewerWindow
    window=ViewerWindow();app.window=window;window.show()
    paths=app.pending+[p for p in sys.argv[1:] if not p.startswith('-')]
    if smoke:
        if sys.platform=='darwin':
            import Vision, Quartz, Foundation
        from PIL import Image
        from lumaview.model import ImageDocument,default_advanced,default_format
        from lumaview.ui.advanced import AdvancedDialog
        from lumaview.ui.format_options import FormatOptionsPanel
        from lumaview.ui.batch_window import BatchWindow
        from lumaview.bilevel import warm_up
        warm_up()
        from lumaview.runtime_check import verify_codecs
        verify_codecs()
        window.doc=ImageDocument([Image.new('RGB',(80,60),'#4375cf')]);window.refresh_image()
        advanced=AdvancedDialog(default_advanced(),window)
        batch=BatchWindow(parent=window)
        for fmt in ['JPEG','PNG','TIFF','PDF','GIF']:
            panel=FormatOptionsPanel(fmt,default_format(fmt),window);panel.values();panel.deleteLater()
        advanced.close();batch.close();QTimer.singleShot(150,app.quit)
    elif paths:QTimer.singleShot(0,lambda:window.open_path(Path(paths[0])))
    return app.exec()

if __name__=='__main__':sys.exit(main())
