"""Desktop viewer, image tools and familiar keyboard navigation."""
from pathlib import Path
from copy import deepcopy
import json, os, random, shutil, sys, tempfile, re, math, threading, hashlib, errno
from PIL import Image, ImageOps, ImageDraw, ImageFont, ImageFilter
import numpy as np
from PySide6.QtCore import Qt, QTimer, Signal, QRectF, QPointF, QSettings, QUrl, QFile, QEvent, QObject, QRunnable, QThreadPool, Slot
from PySide6.QtGui import (QAction,QKeySequence,QPainter,QPen,QColor,QBrush,QPixmap,QDesktopServices,QIcon,QFont,QImage)
from PySide6.QtWidgets import (QMainWindow,QGraphicsView,QGraphicsScene,QGraphicsPixmapItem,QGraphicsRectItem,
    QWidget,QLabel,QFileDialog,QMessageBox,QInputDialog,QDialog,QDialogButtonBox,QVBoxLayout,QHBoxLayout,
    QFormLayout,QSpinBox,QDoubleSpinBox,QLineEdit,QComboBox,QCheckBox,QColorDialog,QDockWidget,QListWidget,
    QListWidgetItem,QToolBar,QTextEdit,QPushButton,QScrollArea,QSplitter,QAbstractItemView,QApplication,QGroupBox)
from lumaview import __version__,APP_NAME
from lumaview.model import ImageDocument,default_advanced,default_format,FORMAT_LABELS,EXTENSIONS
from lumaview.image_io import load_document,save_document,available_formats,supported_input_extensions
from lumaview.processing import apply_operations,process_document
from lumaview.ui.common import pil_to_pixmap,qimage_to_pil,error,resource_path
from lumaview.ui.save_dialog import SaveAsDialog,EncodingPreview

class _ThumbnailSignals(QObject):
    ready=Signal(int,int,str,object)
    finished=Signal(int)

class _ThumbnailJob(QRunnable):
    def __init__(self,generation,paths,cancel):
        super().__init__();self.generation=generation;self.paths=paths;self.cancel=cancel;self.signals=_ThumbnailSignals()
    @Slot()
    def run(self):
        try:
            for row,path in enumerate(self.paths):
                if self.cancel.is_set():break
                try:
                    im=load_document(path,pdf_dpi=40,all_pages=False).frames[0]
                    im.thumbnail((104,82));im=im.convert('RGBA');raw=im.tobytes('raw','RGBA')
                    qi=QImage(raw,im.width,im.height,im.width*4,QImage.Format.Format_RGBA8888).copy()
                    if not self.cancel.is_set():self.signals.ready.emit(self.generation,row,str(path),qi)
                except Exception:pass
        finally:self.signals.finished.emit(self.generation)

class ImageCanvas(QGraphicsView):
    selectionChanged=Signal(object)
    painted=Signal(object)
    zoomChanged=Signal(float)
    interactionStarted=Signal()
    def __init__(self,parent=None):
        super().__init__(parent)
        self.setScene(QGraphicsScene(self));self.item=QGraphicsPixmapItem();self.scene().addItem(self.item)
        self.selection_item=QGraphicsRectItem();selection_pen=QPen(QColor('#4e8dff'),1,Qt.PenStyle.DashLine);selection_pen.setCosmetic(True);self.selection_item.setPen(selection_pen)
        self.selection_item.setBrush(QBrush(QColor(60,130,255,25)));self.selection_item.setZValue(2);self.scene().addItem(self.selection_item)
        self.image=None;self.selection=None;self.last_selection=None;self.origin=None;self.fit_mode=True;self.grid=0
        self.paint_mode=False;self.paint_color='#e43b3b';self.paint_width=5;self.last_point=None;self.paint_before=None
        self._pan_origin=None
        self.setBackgroundBrush(QColor('#202735'));self.setFrameShape(QGraphicsView.Shape.NoFrame)
        self.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setResizeAnchor(QGraphicsView.ViewportAnchor.AnchorViewCenter)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
    def set_image(self,im,reset=True):
        self.image=im.copy();self.item.setPixmap(pil_to_pixmap(im));self.scene().setSceneRect(0,0,im.width,im.height)
        self.clear_selection()
        if reset and self.fit_mode:self.fit()
        self.viewport().update()
    def fit(self):
        self.fit_mode=True
        if self.image:self.fitInView(self.item,Qt.AspectRatioMode.KeepAspectRatio)
        self.zoomChanged.emit(self.transform().m11())
    def actual_size(self):
        self.fit_mode=False;self.resetTransform();self.zoomChanged.emit(1.0)
    def zoom(self,factor):
        self.fit_mode=False
        if .005 < self.transform().m11()*factor < 100:self.scale(factor,factor)
        self.zoomChanged.emit(self.transform().m11())
    def resizeEvent(self,e):
        super().resizeEvent(e)
        if self.fit_mode:self.fit()
    def wheelEvent(self,e):
        self.zoom(1.2 if e.angleDelta().y()>0 else 1/1.2);e.accept()
    def rect_bounds(self):return QRectF(0,0,self.image.width,self.image.height) if self.image else QRectF()
    def set_selection(self,box):
        if not self.image:return
        x,y,w,h=map(int,box)
        rect=QRectF(x,y,w,h).intersected(self.rect_bounds())
        if rect.width()<1 or rect.height()<1:return
        self.selection=(int(rect.x()),int(rect.y()),int(rect.width()),int(rect.height()))
        self.last_selection=self.selection;self.selection_item.setRect(rect);self.selection_item.show();self.selectionChanged.emit(self.selection)
    def clear_selection(self):
        self.selection=None;self.selection_item.hide();self.selectionChanged.emit(None)
    def mousePressEvent(self,e):
        if e.button()==Qt.MouseButton.MiddleButton:
            self._pan_origin=e.position().toPoint();self.setCursor(Qt.CursorShape.ClosedHandCursor);e.accept();return
        if e.button()==Qt.MouseButton.LeftButton and self.image:
            p=self.mapToScene(e.position().toPoint())
            if not self.rect_bounds().contains(p):return
            self.interactionStarted.emit()
            if self.paint_mode:
                self.paint_before=self.image.copy();self.last_point=(p.x(),p.y());self._stroke(self.last_point);return
            self.origin=p;self.clear_selection();e.accept();return
        super().mousePressEvent(e)
    def mouseMoveEvent(self,e):
        if self._pan_origin is not None:
            point=e.position().toPoint();delta=point-self._pan_origin;self._pan_origin=point
            self.horizontalScrollBar().setValue(self.horizontalScrollBar().value()-delta.x())
            self.verticalScrollBar().setValue(self.verticalScrollBar().value()-delta.y());e.accept();return
        if self.paint_mode and self.last_point and e.buttons()&Qt.MouseButton.LeftButton:
            p=self.mapToScene(e.position().toPoint());self._stroke((p.x(),p.y()));return
        if self.origin is not None and e.buttons()&Qt.MouseButton.LeftButton:
            p=self.mapToScene(e.position().toPoint());r=QRectF(self.origin,p).normalized().intersected(self.rect_bounds())
            self.set_selection((r.x(),r.y(),r.width(),r.height()));return
        super().mouseMoveEvent(e)
    def mouseReleaseEvent(self,e):
        if e.button()==Qt.MouseButton.MiddleButton:
            self._pan_origin=None;self.setCursor(Qt.CursorShape.CrossCursor if self.paint_mode else Qt.CursorShape.ArrowCursor);e.accept();return
        if self.paint_mode and self.last_point:
            self.last_point=None;self.painted.emit(self.image.copy());return
        self.origin=None
        super().mouseReleaseEvent(e)
    def _stroke(self,p):
        if self.image.mode!='RGBA':self.image=self.image.convert('RGBA')
        draw=ImageDraw.Draw(self.image);draw.line([self.last_point,p],fill=self.paint_color,width=self.paint_width)
        r=self.paint_width/2;draw.ellipse((p[0]-r,p[1]-r,p[0]+r,p[1]+r),fill=self.paint_color)
        self.last_point=p;self.item.setPixmap(pil_to_pixmap(self.image))
    def drawForeground(self,painter,rect):
        super().drawForeground(painter,rect)
        if self.grid and self.image:
            pen=QPen(QColor(255,255,255,100));pen.setCosmetic(True);painter.setPen(pen)
            target=self.selection_item.rect() if self.selection else self.rect_bounds()
            for i in range(1,self.grid):
                x=target.x()+target.width()*i/self.grid;y=target.y()+target.height()*i/self.grid
                painter.drawLine(QPointF(x,target.top()),QPointF(x,target.bottom()))
                painter.drawLine(QPointF(target.left(),y),QPointF(target.right(),y))

class ViewerWindow(QMainWindow):
    def __init__(self):
        super().__init__();self.settings=QSettings();self.doc=None;self.page=0;self.files=[];self.file_index=0
        self.dirty=False;self.undo_stack=[];self.redo_stack=[];self.zoom_locked=False;self.scroll_locked=False;self.invert_selection=False
        self._revision=0;self._next_revision=1;self._saved_revision=0;self._close_approved=False
        self._thumbnail_generation=0;self._thumbnail_cancel=threading.Event();self._thumbnail_jobs={}
        self._thumbnail_pool=QThreadPool(self);self._thumbnail_pool.setMaxThreadCount(1)
        self.last_options=default_advanced();self.batch_windows=[];self.capture_hidden=False
        self.setWindowTitle(APP_NAME);self.resize(1180,800);self.setAcceptDrops(True)
        self.canvas=ImageCanvas(self);self.setCentralWidget(self.canvas)
        self.canvas.selectionChanged.connect(self.selection_status);self.canvas.zoomChanged.connect(self.update_status);self.canvas.painted.connect(self.replace_image)
        self.status=QLabel('');self.statusBar().addPermanentWidget(self.status)
        self.slideshow=QTimer(self);self.slideshow.timeout.connect(lambda:self.navigate(1,slideshow=True))
        self.animation=QTimer(self);self.animation.timeout.connect(self.animate)
        self.canvas.interactionStarted.connect(self.animation.stop)
        self.thumb_dock=QDockWidget('Thumbnails',self);self.thumbs=QListWidget();self.thumbs.setViewMode(QListWidget.ViewMode.IconMode)
        self.thumbs.setIconSize(__import__('PySide6.QtCore',fromlist=['QSize']).QSize(104,82));self.thumbs.setResizeMode(QListWidget.ResizeMode.Adjust)
        self.thumb_dock.setWidget(self.thumbs);self.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea,self.thumb_dock);self.thumb_dock.hide()
        self.thumbs.itemDoubleClicked.connect(lambda item:self.open_path(Path(item.data(Qt.ItemDataRole.UserRole))))
        self._menus();self._toolbar()
        self.empty=QLabel('Open an image',self.canvas);self.empty.setStyleSheet('font-size:20px;color:#91a0b6;background:transparent;');self.empty.move(45,45)
        self.restoreGeometry(self.settings.value('viewer/geometry',b''))
    def action(self,menu,text,callback,shortcut=None,checkable=False):
        a=QAction(text,self);a.setCheckable(checkable)
        if shortcut:a.setShortcut(QKeySequence(shortcut))
        a.triggered.connect(callback);menu.addAction(a);return a
    def _menus(self):
        m=self.menuBar().addMenu('&File')
        self.action(m,'Open…',self.open_dialog,'O');self.action(m,'Reopen',self.reopen,'Shift+R')
        self.recent_menu=m.addMenu('Open recent');self.refresh_recent()
        self.action(m,'Open with external editor…',self.external_editor)
        self.action(m,'Open raw pixels…',self.open_raw)
        m.addSeparator();self.action(m,'Thumbnails',self.toggle_thumbnails,'T');self.action(m,'Slideshow…',self.slideshow_dialog,'W')
        self.action(m,'Start / stop slideshow',self.toggle_slideshow,'Ctrl+W')
        self.action(m,'Batch conversion / rename…',self.open_batch,'B');self.action(m,'Search files…',self.search_files,'Ctrl+F')
        m.addSeparator();self.action(m,'Rename file…',self.rename_file,'F2');self.action(m,'Move file…',lambda:self.move_copy(True),'F7');self.action(m,'Copy file…',lambda:self.move_copy(False),'F8');self.action(m,'Move to Trash',self.trash,'Delete')
        m.addSeparator();self.action(m,'Save',self.save_current,'Ctrl+S');self.action(m,'Save as…',self.save_as,'S')
        self.action(m,'Compare images…',self.compare);self.action(m,'Save selection as…',self.save_selection,'Ctrl+Shift+B')
        self.action(m,'Print…',self.print_image,'Ctrl+P');self.action(m,'Scan…',self.scan);m.addSeparator();self.action(m,'Close',self.close,'Ctrl+Q')
        m=self.menuBar().addMenu('&Edit')
        self.action(m,'Undo',self.undo,'Ctrl+Z');self.action(m,'Redo',self.redo,'Ctrl+Shift+Z');m.addSeparator()
        self.action(m,'Paint…',self.paint_dialog,'F12');self.action(m,'Create selection…',self.selection_dialog,'Shift+C')
        ratios=m.addMenu('Maximized selection')
        for label,ratio in [('Square',1),('4:3',4/3),('3:2',3/2),('16:9',16/9)]:self.action(ratios,label,lambda checked=False,r=ratio:self.max_selection(r))
        self.action(m,'Restore last selection',lambda:self.canvas.set_selection(self.canvas.last_selection) if self.canvas.last_selection else None)
        self.action(m,'Center selection',self.center_selection)
        self.action(m,'Invert selection for effects',lambda checked:setattr(self,'invert_selection',checked),checkable=True)
        grid=m.addMenu('Selection grid')
        for n in [0,3,4,8]:self.action(grid,'None' if n==0 else f'{n} × {n}',lambda checked=False,v=n:self.set_grid(v))
        m.addSeparator();self.action(m,'Insert text…',lambda:self.advanced_tool('text'),'Ctrl+T');self.action(m,'Insert watermark…',lambda:self.advanced_tool('watermark'))
        self.action(m,'Cut selection',self.cut_selection,'Ctrl+X');self.action(m,'Clear outside selection',self.clear_outside)
        strips=m.addMenu('Remove / insert strip')
        self.action(strips,'Remove horizontal strip',lambda:self.strip('remove-horizontal'));self.action(strips,'Remove vertical strip',lambda:self.strip('remove-vertical'))
        self.action(strips,'Insert horizontal strip…',lambda:self.strip('insert-horizontal'));self.action(strips,'Insert vertical strip…',lambda:self.strip('insert-vertical'))
        self.action(m,'Crop selection',self.crop,'Ctrl+Y');self.action(m,'Auto-crop borders',lambda:self.operation(auto_crop=True),'Ctrl+Shift+Y')
        self.action(m,'Capture viewer area',self.capture_view)
        m.addSeparator();self.action(m,'Copy',self.copy,'Ctrl+C');self.action(m,'Paste',self.paste,'Ctrl+V')
        paste=m.addMenu('Paste on side')
        for side in ['left','right','top','bottom']:self.action(paste,side.title(),lambda checked=False,s=side:self.paste_side(s))
        self.action(m,'Clear display',self.clear_display,'D');self.action(m,'Clear clipboard',lambda:QApplication.clipboard().clear())
        m=self.menuBar().addMenu('&Image')
        self.action(m,'Information…',self.information,'I');self.action(m,'New image…',self.new_image,'Shift+N');self.action(m,'Merge images…',self.merge_images)
        m.addSeparator();self.action(m,'Rotate left',lambda:self.operation(rotate_left=True),'L');self.action(m,'Rotate right',lambda:self.operation(rotate_right=True),'R')
        self.action(m,'Fine rotation…',lambda:self.advanced_tool('fine_rotation'),'Ctrl+U');self.action(m,'Vertical flip',lambda:self.operation(flip_vertical=True),'V');self.action(m,'Horizontal flip',lambda:self.operation(flip_horizontal=True),'H')
        m.addSeparator();self.action(m,'Resize / resample…',lambda:self.advanced_tool('resize'),'Ctrl+R');self.action(m,'Canvas size…',lambda:self.advanced_tool('canvas'),'Shift+V')
        self.action(m,'Border / frame…',lambda:self.advanced_tool('border'),'Ctrl+D');self.action(m,'Rounded corners / shadow…',self.rounded_shadow)
        self.action(m,'Shape mask…',self.shape_mask);self.action(m,'Change color depth…',lambda:self.advanced_tool('depth'))
        m.addSeparator();self.action(m,'Grayscale',lambda:self.operation(grayscale=True),'Ctrl+G');self.action(m,'Negative',lambda:self.operation(negative=True))
        channels=m.addMenu('Show channel')
        for ch in ['Red','Green','Blue','Alpha']:self.action(channels,ch,lambda checked=False,c=ch:self.show_channel(c))
        self.action(m,'Color corrections…',lambda:self.advanced_tool('brightness'),'Shift+G');self.action(m,'Alpha / transparency…',self.set_transparency)
        self.action(m,'Histogram…',self.histogram,'Shift+H');self.action(m,'Replace color…',lambda:self.advanced_tool('replace_color'));self.action(m,'Tile image…',self.tile_image)
        m.addSeparator();self.action(m,'Auto-adjust colors',lambda:self.operation(auto_colors=True),'Shift+U');self.action(m,'Sharpen…',lambda:self.advanced_tool('sharpen'),'Shift+S')
        self.action(m,'Red-eye reduction (selection)',self.red_eye,'Shift+Y');self.action(m,'Effects…',lambda:self.advanced_tool('effects'))
        swap=m.addMenu('Swap colors')
        for order in ['RBG','BGR','BRG','GRB','GBR']:self.action(swap,order,lambda checked=False,o=order:self.operation(swap_channels=o))
        self.action(m,'Palette…',self.palette)
        m=self.menuBar().addMenu('&Options')
        self.action(m,'Settings…',self.preferences,'P');self.action(m,'Stop / resume animation',self.toggle_animation,'G');self.action(m,'Extract all frames…',self.extract_frames)
        self.action(m,'Export tiles…',self.export_tiles)
        pages=m.addMenu('Multipage images');self.action(pages,'Append images…',self.append_images);self.action(pages,'Delete current page',self.delete_page)
        self.action(m,'Edit EXIF date/time…',self.edit_exif_date);self.action(m,'Capture screenshot…',self.capture_screen,'C')
        self.action(m,'Watch folder…',self.watch_folder);self.action(m,'OCR…',self.ocr,'F9')
        sort=m.addMenu('Sort file list')
        for key in ['name','date','size']:self.action(sort,key.title(),lambda checked=False,k=key:self.sort_files(k))
        self.action(m,'Set as wallpaper',self.wallpaper);self.action(m,'Minimize',self.showMinimized,'M');self.action(m,'Always on top',self.always_on_top,checkable=True)
        m=self.menuBar().addMenu('&View')
        self.action(m,'Show status bar',lambda b:self.statusBar().setVisible(b),checkable=True).setChecked(True)
        self.toolbar_action=self.action(m,'Show toolbar',lambda b:self.toolbar.setVisible(b),checkable=True);self.toolbar_action.setChecked(True)
        self.action(m,'Full screen',self.fullscreen,'Return');self.action(m,'Fit image to window',self.canvas.fit,'F')
        self.action(m,'Next file',lambda:self.navigate(1),'Space');self.action(m,'Previous file',lambda:self.navigate(-1),'Backspace')
        self.action(m,'First file',lambda:self.go_file(0),'Ctrl+Home');self.action(m,'Last file',lambda:self.go_file(len(self.files)-1),'Ctrl+End');self.action(m,'Random file',self.random_file,'Ctrl+M')
        self.action(m,'Refresh',self.reopen,'F5');m.addSeparator();self.action(m,'Zoom in',lambda:self.canvas.zoom(1.25),'+');self.action(m,'Zoom out',lambda:self.canvas.zoom(.8),'-')
        self.action(m,'Lock zoom',lambda b:setattr(self,'zoom_locked',b),'Shift+L',True)
        self.action(m,'Lock scroll position',lambda b:setattr(self,'scroll_locked',b),'Ctrl+Shift+K',True)
        self.action(m,'Original size',self.canvas.actual_size,'Ctrl+H');self.action(m,'Hex view…',self.hex_view,'F3')
        self.action(m,'Next page / frame',lambda:self.change_page(1),'PageDown');self.action(m,'Previous page / frame',lambda:self.change_page(-1),'PageUp')
        m=self.menuBar().addMenu('&Help');self.action(m,'User guide',self.open_guide,'F1');self.action(m,'Keyboard shortcuts',self.shortcuts)
        self.action(m,'Supported formats',self.formats_dialog);self.action(m,'About LumaView',self.about)
    def _toolbar(self):
        self.toolbar=QToolBar('Tools',self);self.toolbar.setMovable(False);self.addToolBar(self.toolbar)
        for label,cb in [('Open',self.open_dialog),('Save as',self.save_as),('Batch',self.open_batch)]:
            a=QAction(label,self);a.triggered.connect(cb);self.toolbar.addAction(a)
        self.toolbar.addSeparator()
        for label,cb in [('‹',lambda:self.navigate(-1)),('›',lambda:self.navigate(1)),('Fit',self.canvas.fit),('1:1',self.canvas.actual_size),('−',lambda:self.canvas.zoom(.8)),('+',lambda:self.canvas.zoom(1.25)),('Rotate ↶',lambda:self.operation(rotate_left=True)),('Crop',self.crop)]:
            a=QAction(label,self);a.triggered.connect(cb);self.toolbar.addAction(a)
        self.toolbar.addSeparator();self.page_label=QLabel('');self.toolbar.addWidget(self.page_label)
    def current_image(self):return self.doc.frames[self.page] if self.doc and self.doc.frames else None
    def guard(self):
        if not self.dirty:return True
        result=QMessageBox.question(self,'Unsaved changes','Save changes before continuing?',QMessageBox.StandardButton.Save|QMessageBox.StandardButton.Discard|QMessageBox.StandardButton.Cancel)
        if result==QMessageBox.StandardButton.Cancel:return False
        if result==QMessageBox.StandardButton.Save:return bool(self.save_as()) and not self.dirty
        return True
    def open_dialog(self):
        files,_=QFileDialog.getOpenFileNames(self,'Open images',self.settings.value('viewer/directory',str(Path.home()/'Pictures')),'Images ('+' '.join('*'+e for e in sorted(supported_input_extensions()))+');;All files (*)')
        if files:self.open_path(Path(files[0]),[Path(f) for f in files] if len(files)>1 else None)
    def open_path(self,path,file_list=None):
        path=Path(path).expanduser().resolve()
        if not self.guard():return False
        password=None
        while True:
            failure=None
            try:
                QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
                doc=load_document(path,pdf_dpi=int(self.settings.value('pdf/dpi',96)),password=password,
                                  svg_size=(self.settings.value('svg/width',1024,type=int),self.settings.value('svg/height',800,type=int)),
                                  svg_keep_aspect=self.settings.value('svg/keep_aspect',False,type=bool))
            except Exception as exc:failure=exc
            finally:QApplication.restoreOverrideCursor()
            if failure is None:break
            if path.suffix.lower()=='.pdf' and (getattr(failure,'err_code',None)==4 or 'password' in str(failure).lower()):
                password,ok=QInputDialog.getText(self,'PDF password','Password' if password is None else 'Incorrect password. Try again',QLineEdit.EchoMode.Password)
                if not ok:return False
            else:error(self,failure,'Cannot open image');return False
        self._set_document(doc,reset_files=False)
        if file_list is not None:self.files=list(dict.fromkeys(Path(p).expanduser().resolve() for p in file_list))
        elif path not in self.files or not self.files or path.parent!=self.files[0].parent:
            try:self.files=sorted([p for p in path.parent.iterdir() if p.is_file() and p.suffix.lower() in supported_input_extensions()],key=lambda p:natural_key(p.name))
            except OSError:self.files=[path]
        if path not in self.files:self.files.append(path)
        self.file_index=self.files.index(path);self.settings.setValue('viewer/directory',str(path.parent))
        self.refresh_image();self.add_recent(path)
        if self.thumb_dock.isVisible():self.populate_thumbnails()
        return True
    def _set_document(self,document,dirty=False,reset_files=True):
        """Replace a document without retaining animation or history from another file."""
        self.animation.stop();self.doc=document;self.page=0;self.dirty=dirty
        self.undo_stack=[];self.redo_stack=[];self._revision=0;self._next_revision=1;self._saved_revision=None if dirty else 0
        self.canvas.paint_before=None;self.canvas.last_point=None;self.canvas.origin=None
        if reset_files:
            self.files=[];self.file_index=0;self.thumbs.clear();self._cancel_thumbnails()
    def _frame_delay(self,index=None):
        if not self.doc:return 100
        index=self.page if index is None else index
        return max(20,int(self.doc.durations[index])) if index<len(self.doc.durations) else 100
    def _sync_animation(self):
        if not self.doc or len(self.doc.frames)<2:self.animation.stop()
        elif self.animation.isActive():self.animation.setInterval(self._frame_delay())
    def refresh_image(self):
        im=self.current_image()
        if im is None:return
        old_scroll=(self.canvas.horizontalScrollBar().value(),self.canvas.verticalScrollBar().value())
        self.empty.hide();self.canvas.set_image(im,not self.zoom_locked)
        if self.scroll_locked:
            self.canvas.horizontalScrollBar().setValue(old_scroll[0]);self.canvas.verticalScrollBar().setValue(old_scroll[1])
        name=self.doc.source.name if self.doc.source else 'Untitled'
        self.setWindowTitle(f'{name}{" *" if self.dirty else ""} — {APP_NAME}')
        self.page_label.setText(f'Page {self.page+1} / {len(self.doc.frames)}' if len(self.doc.frames)>1 else '')
        self.update_status()
    def update_status(self,*_):
        im=self.current_image()
        if im:
            preview='   ·   Embedded preview only' if self.doc.metadata.get('preview_only') else ''
            self.status.setText(f'{im.width} × {im.height}   ·   {im.mode}   ·   {self.canvas.transform().m11()*100:.0f}%   ·   {self.file_index+1 if self.files else 0} / {len(self.files)}{preview}')
    def selection_status(self,box):
        if box:self.statusBar().showMessage(f'Selection  {box[0]}, {box[1]}   {box[2]} × {box[3]}')
        else:self.statusBar().clearMessage()
    def checkpoint(self):
        if self.doc:
            self.animation.stop()
            self.undo_stack.append((self.doc.clone(),self.page,self._revision));self.undo_stack=self.undo_stack[-10:];self.redo_stack=[]
            self._revision=self._next_revision;self._next_revision+=1
    def replace_image(self,im):
        if self.doc:
            self.checkpoint();self.doc.frames[self.page]=im;self.dirty=True;self.refresh_image()
    def operation(self,**kwargs):
        im=self.current_image()
        if im is None:return
        opt=default_advanced();opt.update(kwargs)
        try:
            if self.canvas.selection and not any(k in kwargs for k in ['rotate_left','rotate_right','flip_horizontal','flip_vertical','auto_crop']):
                x,y,w,h=self.canvas.selection;region=im.crop((x,y,x+w,y+h));changed=apply_operations(region,opt,{'source':self.doc.source})
                if changed.size==region.size:
                    out=im.convert('RGBA');layer=im.convert('RGBA');layer.paste(changed,(x,y))
                    if self.invert_selection:
                        out=apply_operations(im,opt,{'source':self.doc.source}).convert('RGBA');out.paste(region,(x,y))
                    else:out=layer
                else:out=changed
            else:out=apply_operations(im,opt,{'source':self.doc.source})
            self.replace_image(out)
        except Exception as exc:error(self,exc)
    def advanced_tool(self,key):
        if not self.doc:return
        self.animation.stop()
        from lumaview.ui.advanced import AdvancedDialog
        options=default_advanced();options[key]=True
        if key=='resize':options['width']=self.current_image().width;options['height']=self.current_image().height
        dialog=AdvancedDialog(options,self,selection=self.canvas.selection)
        if dialog.exec():
            try:
                document=self.doc.clone();document.frames=[self.current_image().copy()]
                result=process_document(document,dialog.options);self.checkpoint();self.doc.frames[self.page]=result.frames[0];self.doc.dpi=result.dpi;self.dirty=True;self.refresh_image()
            except Exception as exc:error(self,exc)
    def undo(self):
        if self.undo_stack:
            self.animation.stop();source=self.doc.source
            self.redo_stack.append((self.doc.clone(),self.page,self._revision));self.doc,self.page,self._revision=self.undo_stack.pop()
            self.doc.source=source;self.dirty=self._revision!=self._saved_revision;self.refresh_image()
    def redo(self):
        if self.redo_stack:
            self.animation.stop();source=self.doc.source
            self.undo_stack.append((self.doc.clone(),self.page,self._revision));self.doc,self.page,self._revision=self.redo_stack.pop()
            self.doc.source=source;self.dirty=self._revision!=self._saved_revision;self.refresh_image()
    def save_as(self,document=None):
        doc=document if isinstance(document,ImageDocument) else self.doc
        if not doc:return False
        self.animation.stop()
        dialog=SaveAsDialog(doc,self)
        if not dialog.exec():return False
        options=dialog.options
        if dialog.format=='JPEG' and options.get('preview'):
            preview=EncodingPreview(doc,dialog.format,options,self)
            if not preview.exec():return False
            options=preview.options
        try:
            times=doc.source.stat() if doc.source and doc.source.exists() else None
            export=self._export_document(doc,dialog.format,options)
            result=save_document(export,dialog.path,dialog.format,options)
            if dialog.keep_time.isChecked() and times:os.utime(dialog.path,(times.st_atime,times.st_mtime))
            if doc is self.doc:
                if len(export.frames)==len(doc.frames):
                    self.doc.source=Path(dialog.path);self.dirty=False;self._saved_revision=self._revision
                    if self.doc.source not in self.files:self.files.append(self.doc.source)
                    self.file_index=self.files.index(self.doc.source)
                self.refresh_image();self.add_recent(dialog.path)
            warnings=result.get('warnings',[]) if isinstance(result,dict) else []
            if len(export.frames)<len(doc.frames):warnings=list(warnings)+['Only the displayed page / frame was exported. The complete document remains open.']
            if warnings:QMessageBox.information(self,'Saved','\n'.join(warnings))
            self.statusBar().showMessage(f'Saved {dialog.path.name}',5000);return True
        except Exception as exc:error(self,exc,'Cannot save image');return False
    def _export_document(self,doc,fmt,options):
        multipage={'GIF','TIFF','PDF','PNG','WEBP','AVIF','HEIF'}
        if len(doc.frames)>1 and (not options.get('all_pages',True) or fmt not in multipage):
            result=doc.clone();page=self.page if doc is self.doc else 0;result.frames=[doc.frames[page].copy()]
            result.durations=[doc.durations[page]] if page<len(doc.durations) else []
            return result
        return doc
    def save_current(self):
        if not self.doc:return
        self.animation.stop()
        if not self.doc.source or self.doc.source.suffix.lower() not in {'.jpg','.jpeg','.png','.tif','.tiff','.gif','.webp','.bmp'}:return self.save_as()
        if QMessageBox.question(self,'Save','Replace the current file?',QMessageBox.StandardButton.Save|QMessageBox.StandardButton.Cancel)!=QMessageBox.StandardButton.Save:return False
        fmt={'.jpeg':'JPEG','.tiff':'TIFF'}.get(self.doc.source.suffix.lower()) or next((k for k,v in EXTENSIONS.items() if v==self.doc.source.suffix.lower()),'PNG')
        try:
            opts={**default_format(fmt),**json.loads(self.settings.value('format/'+fmt,'{}'))}
            export=self._export_document(self.doc,fmt,opts)
            if len(export.frames)<len(self.doc.frames):
                if QMessageBox.question(self,'Save one frame','Replace this file with only the displayed page / frame?',QMessageBox.StandardButton.Save|QMessageBox.StandardButton.Cancel,QMessageBox.StandardButton.Cancel)!=QMessageBox.StandardButton.Save:return False
            result=save_document(export,self.doc.source,fmt,opts)
            if len(export.frames)==len(self.doc.frames):self.dirty=False;self._saved_revision=self._revision
            self.refresh_image()
            if result.get('warnings'):QMessageBox.information(self,'Saved','\n'.join(result['warnings']))
            return True
        except Exception as exc:error(self,exc);return False
    def save_selection(self):
        if self.canvas.selection and self.doc:
            x,y,w,h=self.canvas.selection;doc=ImageDocument([self.current_image().crop((x,y,x+w,y+h))],dpi=self.doc.dpi);self.save_as(doc)
    def reopen(self):
        if self.doc and self.doc.source:self.open_path(self.doc.source)
    def navigate(self,step,slideshow=False):
        if not self.files:return
        if self.dirty and slideshow:self.slideshow.stop();return
        self.go_file((self.file_index+step)%len(self.files))
    def go_file(self,index):
        if self.files and 0<=index<len(self.files):self.open_path(self.files[index],self.files)
    def random_file(self):
        if self.files:self.go_file(random.randrange(len(self.files)))
    def change_page(self,step):
        if self.doc and self.doc.frames:self.page=(self.page+step)%len(self.doc.frames);self.refresh_image();self._sync_animation()
    def toggle_animation(self):
        if self.animation.isActive():self.animation.stop()
        elif self.doc and len(self.doc.frames)>1:self.animation.start(self._frame_delay())
    def animate(self):
        if not self.doc or len(self.doc.frames)<2:self.animation.stop();return
        self.change_page(1)
    def toggle_slideshow(self):
        if self.slideshow.isActive():self.slideshow.stop()
        else:self.slideshow.start(int(float(self.settings.value('slideshow/seconds',3))*1000))
    def slideshow_dialog(self):
        seconds,ok=QInputDialog.getDouble(self,'Slideshow','Seconds per image',float(self.settings.value('slideshow/seconds',3)),.1,3600,1)
        if ok:self.settings.setValue('slideshow/seconds',seconds);self.slideshow.start(int(seconds*1000));self.showFullScreen()
    def open_batch(self):
        from lumaview.ui.batch_window import BatchWindow
        paths=[self.doc.source] if self.doc and self.doc.source and self.doc.source.exists() else []
        window=BatchWindow(paths=paths,parent=self,selection=self.canvas.selection);self.batch_windows.append(window)
        window.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose);window.destroyed.connect(lambda:self.batch_windows.remove(window) if window in self.batch_windows else None);window.show()
    def toggle_thumbnails(self):
        self.thumb_dock.setVisible(not self.thumb_dock.isVisible())
        if self.thumb_dock.isVisible():self.populate_thumbnails()
        else:self._cancel_thumbnails()
    def populate_thumbnails(self):
        self._cancel_thumbnails()
        self.thumbs.clear()
        for p in self.files:
            item=QListWidgetItem(p.name);item.setData(Qt.ItemDataRole.UserRole,str(p));self.thumbs.addItem(item)
        if self.files:
            generation=self._thumbnail_generation;self._thumbnail_cancel=threading.Event()
            job=_ThumbnailJob(generation,list(self.files),self._thumbnail_cancel);self._thumbnail_jobs[generation]=job
            job.signals.ready.connect(self._thumbnail_ready);job.signals.finished.connect(self._thumbnail_finished)
            self._thumbnail_pool.start(job)
    def _cancel_thumbnails(self):
        self._thumbnail_cancel.set();self._thumbnail_generation+=1
    @Slot(int,int,str,object)
    def _thumbnail_ready(self,generation,row,path,image):
        if generation!=self._thumbnail_generation:return
        item=self.thumbs.item(row)
        if item and item.data(Qt.ItemDataRole.UserRole)==path:item.setIcon(QIcon(QPixmap.fromImage(image)))
    @Slot(int)
    def _thumbnail_finished(self,generation):
        job=self._thumbnail_jobs.pop(generation,None)
        if job:job.signals.ready.disconnect();job.signals.finished.disconnect()
    def refresh_recent(self):
        self.recent_menu.clear()
        for p in self.settings.value('viewer/recent',[],type=list)[:12]:self.action(self.recent_menu,Path(p).name,lambda checked=False,v=p:self.open_path(Path(v)))
    def add_recent(self,path):
        old=self.settings.value('viewer/recent',[],type=list);self.settings.setValue('viewer/recent',[str(path)]+[p for p in old if p!=str(path)][:11]);self.refresh_recent()
    def dragEnterEvent(self,event):
        if event.mimeData().hasUrls():event.acceptProposedAction()
    def dropEvent(self,event):
        paths=[Path(u.toLocalFile()) for u in event.mimeData().urls() if u.isLocalFile()]
        if paths:
            if paths[0].is_dir():paths=sorted(p for p in paths[0].iterdir() if p.suffix.lower() in supported_input_extensions())
            if paths:self.open_path(paths[0],paths if len(paths)>1 else None)
    def closeEvent(self,event):
        if not self._close_approved and not self.guard():event.ignore();return
        self._close_approved=True
        self.animation.stop();self.slideshow.stop();self._cancel_thumbnails()
        if getattr(self,'watch_timer',None):self.watch_timer.stop()
        busy=False
        for batch in list(self.batch_windows):
            if batch.is_busy():busy=True
            batch.close()
        if busy or self._thumbnail_pool.activeThreadCount():
            self.setEnabled(False);self.statusBar().showMessage('Finishing active operations…')
            QTimer.singleShot(50,self.close);event.ignore();return
        self.settings.setValue('viewer/geometry',self.saveGeometry());event.accept()
    def showEvent(self,event):
        self._close_approved=False;self.setEnabled(True);super().showEvent(event)
    def keyPressEvent(self,event):
        if event.key()==Qt.Key.Key_Escape:
            if self.isFullScreen():self.showNormal();self.slideshow.stop()
            elif self.canvas.selection:self.canvas.clear_selection()
            else:self.close()
        else:super().keyPressEvent(event)
    def require_selection(self):
        if not self.canvas.selection:
            self.statusBar().showMessage('Drag on the image to select an area.',5000);return None
        return self.canvas.selection
    def crop(self):
        box=self.require_selection()
        if box:
            x,y,w,h=box;self.replace_image(self.current_image().crop((x,y,x+w,y+h)))
    def selection_dialog(self):
        if not self.doc:return
        im=self.current_image();box=self.canvas.selection or (0,0,im.width,im.height)
        result=fields_dialog(self,'Selection',[('X',box[0],-im.width,im.width),('Y',box[1],-im.height,im.height),('Width',box[2],1,im.width),('Height',box[3],1,im.height)])
        if result:self.canvas.set_selection(result)
    def max_selection(self,ratio):
        im=self.current_image()
        if im:
            w=min(im.width,im.height*ratio);h=w/ratio;self.canvas.set_selection(((im.width-w)/2,(im.height-h)/2,w,h))
    def center_selection(self):
        if self.canvas.selection:
            _,_,w,h=self.canvas.selection;im=self.current_image();self.canvas.set_selection(((im.width-w)//2,(im.height-h)//2,w,h))
    def set_grid(self,n):self.canvas.grid=n;self.canvas.viewport().update()
    def copy(self):
        im=self.current_image()
        if im:
            if self.canvas.selection:
                x,y,w,h=self.canvas.selection;im=im.crop((x,y,x+w,y+h))
            QApplication.clipboard().setPixmap(pil_to_pixmap(im))
    def paste(self):
        qi=QApplication.clipboard().image()
        if qi.isNull():return
        im=qimage_to_pil(qi)
        if self.doc and self.canvas.selection:
            x,y,w,h=self.canvas.selection;out=self.current_image().convert('RGBA');piece=im.copy();piece.thumbnail((w,h),Image.Resampling.LANCZOS);out.alpha_composite(piece,(x,y));self.replace_image(out)
        elif self.guard():
            self._set_document(ImageDocument([im]),dirty=True);self.refresh_image()
    def cut_selection(self):
        box=self.require_selection()
        if not box:return
        self.copy();x,y,w,h=box;im=self.current_image().convert('RGBA');ImageDraw.Draw(im).rectangle((x,y,x+w-1,y+h-1),fill=(0,0,0,0));self.replace_image(im)
    def clear_outside(self):
        box=self.require_selection()
        if not box:return
        x,y,w,h=box;im=self.current_image().convert('RGBA');out=Image.new('RGBA',im.size);out.paste(im.crop((x,y,x+w,y+h)),(x,y));self.replace_image(out)
    def paste_side(self,side):
        qi=QApplication.clipboard().image();im=self.current_image()
        if qi.isNull() or im is None:return
        clip=qimage_to_pil(qi);horizontal=side in ['left','right']
        out=Image.new('RGBA',(im.width+clip.width,max(im.height,clip.height)) if horizontal else (max(im.width,clip.width),im.height+clip.height),'white')
        if side=='left':out.paste(clip,(0,0));out.paste(im,(clip.width,0))
        elif side=='right':out.paste(im,(0,0));out.paste(clip,(im.width,0))
        elif side=='top':out.paste(clip,(0,0));out.paste(im,(0,clip.height))
        else:out.paste(im,(0,0));out.paste(clip,(0,im.height))
        self.replace_image(out)
    def strip(self,mode):
        im=self.current_image();box=self.require_selection()
        if im is None or not box:return
        x,y,w,h=box;horizontal=mode.endswith('horizontal')
        if mode.startswith('remove'):
            if horizontal:
                if h>=im.height:return
                out=Image.new(im.mode,(im.width,im.height-h));out.paste(im.crop((0,0,im.width,y)),(0,0));out.paste(im.crop((0,y+h,im.width,im.height)),(0,y))
            else:
                if w>=im.width:return
                out=Image.new(im.mode,(im.width-w,im.height));out.paste(im.crop((0,0,x,im.height)),(0,0));out.paste(im.crop((x+w,0,im.width,im.height)),(x,0))
        else:
            size,ok=QInputDialog.getInt(self,'Insert strip','Pixels',h if horizontal else w,1,50000)
            if not ok:return
            out=Image.new('RGBA',(im.width,im.height+size) if horizontal else (im.width+size,im.height),'white')
            if horizontal:out.paste(im.crop((0,0,im.width,y)),(0,0));out.paste(im.crop((0,y,im.width,im.height)),(0,y+size))
            else:out.paste(im.crop((0,0,x,im.height)),(0,0));out.paste(im.crop((x,0,im.width,im.height)),(x+size,0))
        self.replace_image(out)
    def new_image(self):
        result=fields_dialog(self,'New image',[('Width',1920,1,30000),('Height',1080,1,30000)])
        if not result or not self.guard():return
        color=QColorDialog.getColor(QColor('white'),self,'Background',QColorDialog.ColorDialogOption.ShowAlphaChannel)
        if color.isValid():
            self._set_document(ImageDocument([Image.new('RGBA',tuple(result),color.getRgb())]),dirty=True);self.refresh_image()
    def paint_dialog(self):
        if not self.doc:return
        dialog=QDialog(self);dialog.setWindowTitle('Paint');layout=QFormLayout(dialog)
        enabled=QCheckBox('Paint on image');enabled.setChecked(self.canvas.paint_mode);layout.addRow(enabled)
        size=QSpinBox();size.setRange(1,200);size.setValue(self.canvas.paint_width);layout.addRow('Brush size',size)
        color=QPushButton(self.canvas.paint_color)
        def choose():
            c=QColorDialog.getColor(QColor(color.text()),dialog)
            if c.isValid():color.setText(c.name())
        color.clicked.connect(choose);layout.addRow('Color',color)
        buttons=QDialogButtonBox(QDialogButtonBox.StandardButton.Ok|QDialogButtonBox.StandardButton.Cancel);layout.addRow(buttons);buttons.accepted.connect(dialog.accept);buttons.rejected.connect(dialog.reject)
        if dialog.exec():self.canvas.paint_mode=enabled.isChecked();self.canvas.paint_width=size.value();self.canvas.paint_color=color.text();self.canvas.setCursor(Qt.CursorShape.CrossCursor if enabled.isChecked() else Qt.CursorShape.ArrowCursor)
    def rounded_shadow(self):
        im=self.current_image()
        if im is None:return
        result=fields_dialog(self,'Rounded corners / shadow',[('Corner radius',30,0,min(im.size)//2),('Shadow blur',12,0,100),('Shadow offset',10,0,200)])
        if not result:return
        radius,blur,offset=result;rgba=im.convert('RGBA');mask=Image.new('L',im.size);ImageDraw.Draw(mask).rounded_rectangle((0,0,im.width-1,im.height-1),radius,fill=255)
        alpha=np.minimum(np.array(mask),np.array(rgba.getchannel('A')));rgba.putalpha(Image.fromarray(alpha));pad=blur*3+offset
        shadow=Image.new('RGBA',(im.width+pad*2,im.height+pad*2));sil=Image.new('RGBA',im.size,(0,0,0,120));sil.putalpha(Image.fromarray((alpha*.4).astype('uint8')))
        shadow.paste(sil,(pad+offset,pad+offset));shadow=shadow.filter(ImageFilter.GaussianBlur(blur));shadow.alpha_composite(rgba,(pad,pad));self.replace_image(shadow)
    def shape_mask(self):
        im=self.current_image()
        if im is None:return
        shape,ok=QInputDialog.getItem(self,'Shape mask','Shape',['Ellipse','Hexagon','Star','Heart'],0,False)
        if not ok:return
        mask=Image.new('L',im.size);d=ImageDraw.Draw(mask);w,h=im.size
        if shape=='Ellipse':d.ellipse((0,0,w-1,h-1),fill=255)
        elif shape=='Heart':
            points=[]
            for t in np.linspace(0,2*math.pi,300):
                x=16*math.sin(t)**3;y=13*math.cos(t)-5*math.cos(2*t)-2*math.cos(3*t)-math.cos(4*t);points.append((w*(x+17)/34,h*(18-y)/36))
            d.polygon(points,fill=255)
        else:
            n=6 if shape=='Hexagon' else 10;points=[]
            for i in range(n):
                angle=2*math.pi*i/n-math.pi/2;r=.48 if shape=='Hexagon' or i%2==0 else .22;points.append((w/2+w*r*math.cos(angle),h/2+h*r*math.sin(angle)))
            d.polygon(points,fill=255)
        out=im.convert('RGBA');out.putalpha(Image.fromarray(np.minimum(np.array(mask),np.array(out.getchannel('A')))));self.replace_image(out)
    def show_channel(self,ch):
        im=self.current_image()
        if im:self.replace_image(im.convert('RGBA').getchannel({'Red':'R','Green':'G','Blue':'B','Alpha':'A'}[ch]))
    def set_transparency(self):
        if not self.doc:return
        color=QColorDialog.getColor(QColor('white'),self,'Transparent color')
        if not color.isValid():return
        tolerance,ok=QInputDialog.getInt(self,'Transparency','Color tolerance',0,0,255)
        if not ok:return
        a=np.array(self.current_image().convert('RGBA'));match=np.max(np.abs(a[:,:,:3].astype(int)-np.array(color.getRgb()[:3])),axis=2)<=tolerance;a[match,3]=0;self.replace_image(Image.fromarray(a))
    def red_eye(self):
        box=self.require_selection()
        if not box:return
        x,y,w,h=box;out=self.current_image().convert('RGBA');arr=np.array(out.crop((x,y,x+w,y+h)));rgb=arr[:,:,:3].astype(float);mask=(rgb[:,:,0]>1.4*rgb[:,:,1])&(rgb[:,:,0]>1.4*rgb[:,:,2])&(rgb[:,:,0]>60)
        arr[mask,0]=((rgb[:,:,1]+rgb[:,:,2])/2)[mask].astype('uint8');out.paste(Image.fromarray(arr),(x,y));self.replace_image(out)
    def tile_image(self):
        im=self.current_image()
        if im is None:return
        result=fields_dialog(self,'Tile image',[('Columns',2,1,50),('Rows',2,1,50)])
        if result:
            cols,rows=result
            if im.width*im.height*cols*rows>200_000_000:error(self,'The tiled image exceeds 200 megapixels.');return
            out=Image.new(im.mode,(im.width*cols,im.height*rows))
            for y in range(rows):
                for x in range(cols):out.paste(im,(x*im.width,y*im.height))
            self.replace_image(out)
    def merge_images(self):
        paths,_=QFileDialog.getOpenFileNames(self,'Merge images','','Images (*)')
        if not paths:return
        direction,ok=QInputDialog.getItem(self,'Merge images','Direction',['Horizontal','Vertical'],0,False)
        if not ok:return
        try:
            images=[load_document(p,all_pages=False).frames[0].convert('RGBA') for p in paths];horizontal=direction=='Horizontal'
            size=(sum(i.width for i in images),max(i.height for i in images)) if horizontal else (max(i.width for i in images),sum(i.height for i in images))
            if size[0]*size[1]>200_000_000:raise ValueError('The merged image exceeds 200 megapixels.')
            out=Image.new('RGBA',size,'white');x=y=0
            for im in images:out.alpha_composite(im,(x,y));x+=im.width if horizontal else 0;y+=im.height if not horizontal else 0
            if self.guard():self._set_document(ImageDocument([out]),dirty=True);self.refresh_image()
        except Exception as exc:error(self,exc)
    def information(self):
        if not self.doc:return
        im=self.current_image();source=str(self.doc.source) if self.doc.source else 'Untitled'
        text=f'{source}\n\nDimensions: {im.width} × {im.height}\nColor mode: {im.mode}\nResolution: {self.doc.dpi[0]:g} × {self.doc.dpi[1]:g} DPI\nPages / frames: {len(self.doc.frames)}'
        if self.doc.source and self.doc.source.exists():text+=f'\nFile size: {self.doc.source.stat().st_size:,} bytes'
        if self.doc.metadata.get('icc_profile'):text+='\nEmbedded ICC profile: yes'
        for warning in self.doc.metadata.get('warnings',[]):text+='\n'+warning
        show_text(self,'Image information',text)
    def histogram(self):
        im=self.current_image()
        if im is None:return
        hist=im.convert('RGB').histogram();chart=Image.new('RGB',(768,320),'#f4f6f8');d=ImageDraw.Draw(chart)
        for channel,color in enumerate(['#e65f66','#38a87a','#4b83e8']):
            vals=hist[channel*256:(channel+1)*256];mx=max(vals) or 1
            points=[(i*3,305-v/mx*285) for i,v in enumerate(vals)];d.line(points,fill=color,width=2)
        show_picture(self,'Histogram',chart)
    def palette(self):
        im=self.current_image()
        if im is None:return
        if im.mode!='P':im=im.convert('RGB').quantize(256)
        palette=im.getpalette() or [];chart=Image.new('RGB',(512,512),'white');draw=ImageDraw.Draw(chart)
        for i in range(min(256,len(palette)//3)):
            color=tuple(palette[i*3:i*3+3]);x=(i%16)*32;y=(i//16)*32;draw.rectangle((x,y,x+31,y+31),fill=color)
        show_picture(self,'Palette',chart)
    def clear_display(self):
        if not self.guard():return
        self._set_document(None);self.slideshow.stop();self.canvas.item.setPixmap(QPixmap());self.canvas.image=None;self.canvas.clear_selection();self.canvas.last_selection=None;self.empty.show();self.status.clear();self.page_label.clear();self.setWindowTitle(APP_NAME)
    def rename_file(self):
        if not self.doc or not self.doc.source:return
        name,ok=QInputDialog.getText(self,'Rename file','Name',text=self.doc.source.name)
        if ok and name:
            if Path(name).name!=name or name in ['.','..']:error(self,'Enter a filename, without a folder.');return
            target=self.doc.source.with_name(name)
            if target==self.doc.source:return
            if target.exists():error(self,'A file with this name already exists.');return
            try:
                old=self.doc.source
                # Both names are in the same folder: an exclusive hard link
                # prevents a racing file from being silently replaced.
                try:os.link(old,target)
                except OSError as exc:
                    if exc.errno not in {errno.EPERM,errno.EXDEV,errno.ENOTSUP,errno.EOPNOTSUPP}:raise
                    copy_file_exclusive(old,target,move=True)
                else:
                    try:old.unlink()
                    except Exception:target.unlink(missing_ok=True);raise
                self.doc.source=target;self.files=[target if p==old else p for p in self.files];self.refresh_image();self.add_recent(target)
            except Exception as exc:error(self,exc)
    def move_copy(self,move):
        if not self.doc or not self.doc.source:return
        if self.dirty and not self.guard():return
        folder=QFileDialog.getExistingDirectory(self,'Move to' if move else 'Copy to')
        if not folder:return
        target=Path(folder)/self.doc.source.name
        if target.exists():error(self,'The destination already exists.');return
        try:
            source=self.doc.source;copy_file_exclusive(source,target,move)
            if move:
                self.doc.source=target;self.files=[target if p==source else p for p in self.files];self.refresh_image();self.add_recent(target)
            self.statusBar().showMessage(('Moved ' if move else 'Copied ')+target.name,5000)
        except Exception as exc:error(self,exc)
    def trash(self):
        if not self.doc or not self.doc.source or not self.guard():return
        source=self.doc.source
        if QMessageBox.question(self,'Move to Trash',f'Move {source.name} to the Trash?')!=QMessageBox.StandardButton.Yes:return
        result=QFile.moveToTrash(str(source));ok=result[0] if isinstance(result,tuple) else result
        if not ok:error(self,'Could not move the file to the Trash.');return
        self.dirty=False;self.files=[p for p in self.files if p!=source]
        if self.files:self.go_file(min(self.file_index,len(self.files)-1))
        else:self.clear_display()
    def external_editor(self):
        if not self.doc or not self.doc.source:return
        editor=QFileDialog.getOpenFileName(self,'Choose editor', '/Applications' if sys.platform=='darwin' else '')[0]
        if not editor and sys.platform=='darwin':editor=QFileDialog.getExistingDirectory(self,'Choose editor application','/Applications')
        if editor:
            import subprocess
            try:subprocess.Popen(['open','-a',editor,str(self.doc.source)] if sys.platform=='darwin' else [editor,str(self.doc.source)])
            except Exception as exc:error(self,exc)
    def open_raw(self):
        path,_=QFileDialog.getOpenFileName(self,'Open headerless pixels')
        if not path:return
        values=fields_dialog(self,'Raw dimensions',[('Width',1920,1,50000),('Height',1080,1,50000),('Header bytes',0,0,10000000)])
        if not values:return
        mode,ok=QInputDialog.getItem(self,'Pixel layout','Mode',['RGB','RGBA','L'],0,False)
        if not ok:return
        try:
            w,h,skip=values;data=Path(path).read_bytes()[skip:];im=Image.frombytes(mode,(w,h),data)
            if self.guard():self._set_document(ImageDocument([im],Path(path)));self.files=[Path(path)];self.refresh_image()
        except Exception as exc:error(self,exc)
    def search_files(self):
        folder=QFileDialog.getExistingDirectory(self,'Search folder',self.settings.value('viewer/directory',str(Path.home())))
        if not folder:return
        pattern,ok=QInputDialog.getText(self,'Search files','Filename pattern',text='*')
        if not ok:return
        paths=sorted((p for p in Path(folder).rglob(pattern) if p.is_file() and p.suffix.lower() in supported_input_extensions()),key=lambda p:natural_key(str(p)))
        if paths:self.open_path(paths[0],paths);self.thumb_dock.show();self.populate_thumbnails()
        else:QMessageBox.information(self,'Search','No matching images.')
    def compare(self):
        im=self.current_image()
        if im is None:return
        path,_=QFileDialog.getOpenFileName(self,'Compare with')
        if not path:return
        try:other=load_document(path,all_pages=False).frames[0]
        except Exception as exc:error(self,exc);return
        dialog=QDialog(self);dialog.setWindowTitle('Compare images');dialog.resize(1100,680);layout=QHBoxLayout(dialog)
        for image in [im,other]:
            canvas=ImageCanvas();canvas.set_image(image);layout.addWidget(canvas)
        dialog.exec()
    def print_image(self):
        if not self.doc:return
        from PySide6.QtPrintSupport import QPrinter,QPrintDialog
        printer=QPrinter(QPrinter.PrinterMode.HighResolution);dialog=QPrintDialog(printer,self)
        if dialog.exec():
            painter=QPainter(printer);pix=pil_to_pixmap(self.current_image());rect=printer.pageRect(QPrinter.Unit.DevicePixel).toRect();size=pix.size();size.scale(rect.size(),Qt.AspectRatioMode.KeepAspectRatio);painter.drawPixmap((rect.width()-size.width())//2,(rect.height()-size.height())//2,size.width(),size.height(),pix);painter.end()
    def scan(self):
        if sys.platform=='darwin':QDesktopServices.openUrl(QUrl.fromLocalFile('/System/Applications/Image Capture.app'))
        else:QMessageBox.information(self,'Scan','Scanning uses Image Capture on macOS.')
    def append_images(self):
        if not self.doc:return
        paths,_=QFileDialog.getOpenFileNames(self,'Append pages')
        if not paths:return
        try:
            pages=[];durations=[]
            for p in paths:
                document=load_document(p);pages.extend(document.frames)
                durations.extend(document.durations[i] if i<len(document.durations) else 100 for i in range(len(document.frames)))
            self.checkpoint()
            self.doc.durations=[self.doc.durations[i] if i<len(self.doc.durations) else 100 for i in range(len(self.doc.frames))]
            self.doc.frames.extend(pages);self.doc.durations.extend(durations);self.dirty=True;self.refresh_image();self._sync_animation()
        except Exception as exc:error(self,exc)
    def delete_page(self):
        if self.doc and len(self.doc.frames)>1:
            self.checkpoint();self.doc.frames.pop(self.page)
            if self.page<len(self.doc.durations):self.doc.durations.pop(self.page)
            self.page=min(self.page,len(self.doc.frames)-1);self.dirty=True;self.refresh_image();self._sync_animation()
    def extract_frames(self):
        if not self.doc:return
        folder=QFileDialog.getExistingDirectory(self,'Extract frames to')
        if not folder:return
        stem=self.doc.source.stem if self.doc.source else 'image'
        paths=[Path(folder)/f'{stem}_{i+1:04}.png' for i in range(len(self.doc.frames))]
        if any(p.exists() for p in paths):error(self,'One or more frame files already exist. Choose another folder.');return
        try:
            for p,im in zip(paths,self.doc.frames):save_document(ImageDocument([im],dpi=self.doc.dpi),p,'PNG',default_format('PNG'))
            self.statusBar().showMessage(f'Extracted {len(paths)} frames',5000)
        except Exception as exc:error(self,exc)
    def export_tiles(self):
        im=self.current_image()
        if im is None:return
        values=fields_dialog(self,'Export tiles',[('Columns',2,1,100),('Rows',2,1,100)])
        if not values:return
        folder=QFileDialog.getExistingDirectory(self,'Export tiles to')
        if not folder:return
        cols,rows=values
        if cols>im.width or rows>im.height:error(self,'Each tile must contain at least one pixel.');return
        targets=[(Path(folder)/f'tile_{y+1:03}_{x+1:03}.png',(round(x*im.width/cols),round(y*im.height/rows),round((x+1)*im.width/cols),round((y+1)*im.height/rows))) for y in range(rows) for x in range(cols)]
        if any(p.exists() for p,b in targets):error(self,'Tile files already exist. Choose another folder.');return
        try:
            for p,box in targets:save_document(ImageDocument([im.crop(box)],dpi=self.doc.dpi),p,'PNG',default_format('PNG'))
        except Exception as exc:error(self,exc)
    def edit_exif_date(self):
        if not self.doc:return
        value,ok=QInputDialog.getText(self,'EXIF date/time','YYYY:MM:DD HH:MM:SS',text=__import__('datetime').datetime.now().strftime('%Y:%m:%d %H:%M:%S'))
        if not ok:return
        try:
            __import__('datetime').datetime.strptime(value,'%Y:%m:%d %H:%M:%S')
            exif=Image.Exif()
            if self.doc.metadata.get('exif'):exif.load(self.doc.metadata['exif'])
            exif[306]=value
            sub=exif.get_ifd(34665) if 34665 in exif else {};sub[36867]=value;sub[36868]=value;exif[34665]=sub
            self.checkpoint();self.doc.metadata['exif']=exif.tobytes();self.dirty=True;self.refresh_image()
        except Exception as exc:error(self,exc)
    def capture_view(self):
        pix=self.canvas.viewport().grab();im=qimage_to_pil(pix.toImage())
        if self.guard():self._set_document(ImageDocument([im]),dirty=True);self.refresh_image()
    def capture_screen(self):
        delay,ok=QInputDialog.getInt(self,'Screenshot','Delay in seconds',3,0,60)
        if not ok:return
        self.hide()
        def capture():
            try:
                screen=QApplication.primaryScreen();im=qimage_to_pil(screen.grabWindow(0).toImage())
                self.show()
                if self.guard():self._set_document(ImageDocument([im]),dirty=True);self.refresh_image()
            except Exception as exc:self.show();error(self,exc)
        QTimer.singleShot(delay*1000,capture)
    def ocr(self):
        if not self.doc:return
        try:
            from lumaview.native_macos import recognize_text
            QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
            text=recognize_text(self.current_image())
        except Exception as exc:error(self,exc);return
        finally:QApplication.restoreOverrideCursor()
        show_text(self,'Recognized text',text or 'No text found.')
    def sort_files(self,key):
        try:
            source=self.doc.source if self.doc else None
            self.files.sort(key=(lambda p:natural_key(p.name)) if key=='name' else (lambda p:p.stat().st_mtime) if key=='date' else (lambda p:p.stat().st_size))
            if source in self.files:self.file_index=self.files.index(source)
            self.update_status()
            if self.thumb_dock.isVisible():self.populate_thumbnails()
        except Exception as exc:error(self,exc)
    def wallpaper(self):
        if not self.doc or not self.doc.source:return
        if sys.platform!='darwin':QMessageBox.information(self,'Wallpaper','Wallpaper integration requires macOS.');return
        import subprocess
        script='on run argv\ntell application "System Events" to tell every desktop to set picture to item 1 of argv\nend run'
        result=subprocess.run(['/usr/bin/osascript','-e',script,str(self.doc.source)],capture_output=True,text=True)
        if result.returncode:error(self,result.stderr)
    def always_on_top(self,checked):self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint,checked);self.show()
    def fullscreen(self):self.showNormal() if self.isFullScreen() else self.showFullScreen()
    def hex_view(self):
        if self.doc and self.doc.source:
            try:
                with self.doc.source.open('rb') as f:data=f.read(65536)
                lines=[]
                for i in range(0,len(data),16):
                    part=data[i:i+16];lines.append(f'{i:08X}  '+part.hex(' ').upper().ljust(47)+'  '+''.join(chr(c) if 32<=c<127 else '.' for c in part))
                show_text(self,'Hex view · first 64 KiB','\n'.join(lines),monospace=True)
            except Exception as exc:error(self,exc)
    def preferences(self):
        dialog=QDialog(self);dialog.setWindowTitle('Settings');form=QFormLayout(dialog)
        dpi=QSpinBox();dpi.setRange(36,1200);dpi.setValue(int(self.settings.value('pdf/dpi',96)));form.addRow('PDF input resolution (DPI)',dpi)
        svg_w=QSpinBox();svg_w.setRange(1,8192);svg_w.setValue(self.settings.value('svg/width',1024,type=int));form.addRow('SVG input width (px)',svg_w)
        svg_h=QSpinBox();svg_h.setRange(1,8192);svg_h.setValue(self.settings.value('svg/height',800,type=int));form.addRow('SVG input height (px)',svg_h)
        svg_aspect=QCheckBox('Preserve SVG aspect ratio');svg_aspect.setChecked(self.settings.value('svg/keep_aspect',False,type=bool));form.addRow(svg_aspect)
        fit=QCheckBox('Fit images to window');fit.setChecked(self.canvas.fit_mode);form.addRow(fit)
        sec=QDoubleSpinBox();sec.setRange(.1,3600);sec.setValue(float(self.settings.value('slideshow/seconds',3)));form.addRow('Slideshow seconds',sec)
        buttons=QDialogButtonBox(QDialogButtonBox.StandardButton.Ok|QDialogButtonBox.StandardButton.Cancel);form.addRow(buttons);buttons.accepted.connect(dialog.accept);buttons.rejected.connect(dialog.reject)
        if dialog.exec():
            self.settings.setValue('pdf/dpi',dpi.value());self.settings.setValue('svg/width',svg_w.value());self.settings.setValue('svg/height',svg_h.value());self.settings.setValue('svg/keep_aspect',svg_aspect.isChecked())
            self.settings.setValue('slideshow/seconds',sec.value());self.canvas.fit_mode=fit.isChecked();self.canvas.fit() if fit.isChecked() else None
    def watch_folder(self):
        if getattr(self,'watch_timer',None) and self.watch_timer.isActive():
            self.watch_timer.stop();self.statusBar().showMessage('Folder watch stopped',5000);return
        folder=QFileDialog.getExistingDirectory(self,'Watch folder')
        if not folder:return
        # A stable file must be unchanged on two consecutive polls before opening.
        self.watch_path=Path(folder);self.watch_seen={p:(p.stat().st_size,p.stat().st_mtime_ns) for p in self.watch_path.iterdir() if p.is_file()};self.watch_pending={}
        self.watch_timer=QTimer(self);self.watch_timer.setInterval(1200);self.watch_timer.timeout.connect(self.poll_watch);self.watch_timer.start();self.statusBar().showMessage(f'Watching {self.watch_path.name}',5000)
    def poll_watch(self):
        if self.dirty or not self.watch_path.exists():return
        try:
            for p in self.watch_path.iterdir():
                if not p.is_file() or p.suffix.lower() not in supported_input_extensions():continue
                sig=(p.stat().st_size,p.stat().st_mtime_ns)
                if self.watch_seen.get(p)==sig:continue
                if self.watch_pending.get(p)==sig:
                    self.watch_seen[p]=sig;self.watch_pending.pop(p,None);self.open_path(p);return
                self.watch_pending[p]=sig
        except OSError:pass
    def open_guide(self):
        path=resource_path('docs/LumaView-Guide.pdf')
        if path.exists():QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))
        else:show_text(self,'Help','B  Batch conversion / rename\nS  Save as\nO  Open\nSpace / Backspace  Next / previous file\nL / R  Rotate\nCtrl+R (Command+R on Mac)  Resize / resample\n\nThe PDF guide is included in the release package.')
    def shortcuts(self):
        lines=[]
        for a in self.findChildren(QAction):
            if not a.shortcut().isEmpty():lines.append(f'{a.shortcut().toString(QKeySequence.SequenceFormat.NativeText):20} {a.text().replace("&","")}')
        show_text(self,'Keyboard shortcuts','\n'.join(lines),monospace=True)
    def formats_dialog(self):
        show_text(self,'Supported formats','WRITE\n'+'\n'.join(FORMAT_LABELS[f] for f in available_formats())+'\n\nREAD\n'+'  '.join(sorted(supported_input_extensions())))
    def about(self):
        QMessageBox.about(self,'About LumaView',f'LumaView {__version__}\nImage viewer and batch processor.\n\nIndependent software inspired by familiar image-viewer workflows.\nNot affiliated with IrfanView.\n\nSource code: MIT license.\nThird-party notices are included with the application.')

def copy_file_exclusive(source,target,move=False):
    """Verify a new file before deleting its source; never replace a destination."""
    source=Path(source);target=Path(target);created=False;committed=False
    def signature(path):
        stat=path.stat();return stat.st_dev,stat.st_ino,stat.st_size,stat.st_mtime_ns
    def digest(path):
        value=hashlib.sha256()
        with path.open('rb') as handle:
            for chunk in iter(lambda:handle.read(1024*1024),b''):value.update(chunk)
        return value.digest()
    before=signature(source)
    try:
        with source.open('rb') as src,target.open('xb') as dst:
            created=True;shutil.copyfileobj(src,dst);dst.flush();os.fsync(dst.fileno())
        if digest(source)!=digest(target):raise OSError('Copy verification failed; the original was retained.')
        shutil.copystat(source,target)
        if signature(source)!=before:raise OSError('The original changed during copying; it was retained.')
        if move:source.unlink()
        committed=True
    finally:
        if created and not committed:target.unlink(missing_ok=True)

def natural_key(text):return [int(p) if p.isdigit() else p.lower() for p in re.split(r'(\d+)',text)]

def fields_dialog(parent,title,fields):
    dialog=QDialog(parent);dialog.setWindowTitle(title);form=QFormLayout(dialog);widgets=[]
    for label,value,minimum,maximum in fields:
        widget=QSpinBox();widget.setRange(minimum,maximum);widget.setValue(value);form.addRow(label,widget);widgets.append(widget)
    buttons=QDialogButtonBox(QDialogButtonBox.StandardButton.Ok|QDialogButtonBox.StandardButton.Cancel);form.addRow(buttons);buttons.accepted.connect(dialog.accept);buttons.rejected.connect(dialog.reject)
    return [w.value() for w in widgets] if dialog.exec() else None

def show_text(parent,title,text,monospace=False):
    dialog=QDialog(parent);dialog.setWindowTitle(title);dialog.resize(760,540);layout=QVBoxLayout(dialog);edit=QTextEdit();edit.setPlainText(text);edit.setReadOnly(True)
    if monospace:edit.setFont(QFont('Menlo' if sys.platform=='darwin' else 'monospace',11))
    layout.addWidget(edit);buttons=QDialogButtonBox(QDialogButtonBox.StandardButton.Close);buttons.rejected.connect(dialog.reject);layout.addWidget(buttons);dialog.exec()

def show_picture(parent,title,image):
    dialog=QDialog(parent);dialog.setWindowTitle(title);layout=QVBoxLayout(dialog);label=QLabel();label.setPixmap(pil_to_pixmap(image));layout.addWidget(label);dialog.exec()
