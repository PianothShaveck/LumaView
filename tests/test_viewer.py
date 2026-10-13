"""Viewer integration tests with real images, Qt widgets and encoded files."""
import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch
from types import SimpleNamespace

import numpy as np
from PIL import Image
from PySide6.QtCore import QSettings, Qt, QPointF
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QMessageBox

from lumaview.image_io import load_document, save_document
from lumaview.model import ImageDocument, default_format, default_advanced
from lumaview.ui.viewer import ViewerWindow


class ViewerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app=QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        # Compare against the same canonical paths used by the viewer on macOS.
        self.folder=Path(self.temp.name).resolve()
        self.window=ViewerWindow()
        self.window.settings=QSettings(str(self.folder/'settings.ini'),QSettings.Format.IniFormat)
        self.errors=[]
        self.error_patch=patch('lumaview.ui.viewer.error',side_effect=lambda parent,exc,*args:self.errors.append(str(exc)))
        self.error_patch.start()
        self.info_patch=patch('lumaview.ui.viewer.QMessageBox.information',return_value=QMessageBox.StandardButton.Ok)
        self.info_patch.start()

    def tearDown(self):
        self.window.dirty=False
        self.window._cancel_thumbnails()
        self.window._thumbnail_pool.waitForDone(5000)
        self.app.processEvents()
        self.window.close()
        self.app.processEvents()
        self.info_patch.stop();self.error_patch.stop();self.temp.cleanup()

    def image(self,name='image.png',color='red',size=(32,24)):
        path=self.folder/name;Image.new('RGB',size,color).save(path);return path

    def wait(self,predicate,timeout=5):
        deadline=time.monotonic()+timeout
        while not predicate() and time.monotonic()<deadline:
            self.app.processEvents();time.sleep(.005)
        self.assertTrue(predicate())

    def test_crop_undo_redo_and_saved_revision_tracking(self):
        path=self.image();self.assertTrue(self.window.open_path(path))
        self.window.canvas.set_selection((3,4,9,8));self.window.crop()
        self.assertEqual(self.window.current_image().size,(9,8));self.assertTrue(self.window.dirty)
        self.window.undo();self.assertEqual(self.window.current_image().size,(32,24));self.assertFalse(self.window.dirty)
        self.window.redo();self.assertEqual(self.window.current_image().size,(9,8));self.assertTrue(self.window.dirty)
        destination=self.folder/'saved.png'
        dialog=SimpleNamespace(exec=lambda:True,options=default_format('PNG'),format='PNG',path=destination,keep_time=SimpleNamespace(isChecked=lambda:False))
        with patch('lumaview.ui.viewer.SaveAsDialog',return_value=dialog):self.assertTrue(self.window.save_as())
        self.assertFalse(self.window.dirty)
        self.window.undo();self.assertTrue(self.window.dirty);self.assertEqual(self.window.doc.source,destination)
        self.window.redo();self.assertFalse(self.window.dirty);self.assertEqual(load_document(destination).frames[0].size,(9,8))

    def test_navigation_is_natural_and_cancel_protects_unsaved_changes(self):
        ten=self.image('image10.png');two=self.image('image2.png',color='blue')
        self.window.open_path(two)
        self.assertEqual(self.window.files,[two,ten]);self.window.navigate(1)
        self.assertEqual(self.window.doc.source,ten)
        self.window.operation(negative=True)
        with patch('lumaview.ui.viewer.QMessageBox.question',return_value=QMessageBox.StandardButton.Cancel):self.window.navigate(-1)
        self.assertEqual(self.window.doc.source,ten);self.assertTrue(self.window.dirty)

    def test_navigation_deduplicates_paths_through_directory_symlinks(self):
        first=self.image('image1.png');second=self.image('image2.png',color='blue')
        alias=self.folder/'alias'
        alias.symlink_to(self.folder,target_is_directory=True)
        self.assertTrue(self.window.open_path(alias/first.name,[first,alias/first.name,alias/second.name]))
        self.assertEqual(self.window.files,[first,second])
        self.assertEqual(self.window.doc.source,first)
        self.window.navigate(1)
        self.assertEqual(self.window.doc.source,second)

    def test_paint_stroke_stops_animation_and_undo_restores_pixels(self):
        path=self.image(size=(80,60));self.window.open_path(path);self.window.show();self.app.processEvents()
        self.window.canvas.actual_size();self.window.canvas.paint_mode=True;self.window.canvas.paint_color='#0000ff'
        self.window.doc.frames.append(Image.new('RGB',(80,60),'green'));self.window.doc.durations=[100,100]
        self.window.toggle_animation();self.assertTrue(self.window.animation.isActive())
        point=self.window.canvas.mapFromScene(QPointF(40,30))
        QTest.mousePress(self.window.canvas.viewport(),Qt.MouseButton.LeftButton,pos=point)
        self.assertFalse(self.window.animation.isActive())
        QTest.mouseRelease(self.window.canvas.viewport(),Qt.MouseButton.LeftButton,pos=point)
        self.assertEqual(self.window.current_image().convert('RGB').getpixel((40,30)),(0,0,255))
        self.window.undo();self.assertEqual(self.window.current_image().getpixel((40,30)),(255,0,0))

    def test_append_delete_and_undo_keep_frame_durations_aligned(self):
        self.window._set_document(ImageDocument([Image.new('RGB',(10,10),'red')],durations=[40]))
        self.window.refresh_image()
        gif=self.folder/'timing.gif'
        Image.new('RGB',(10,10),'green').save(gif,save_all=True,append_images=[Image.new('RGB',(10,10),'blue')],duration=[60,90],loop=0)
        with patch('lumaview.ui.viewer.QFileDialog.getOpenFileNames',return_value=([str(gif)],'')):self.window.append_images()
        self.assertEqual(self.window.doc.durations,[40,60,90]);self.assertEqual(len(self.window.doc.frames),3)
        self.window.page=1;self.window.delete_page();self.assertEqual(self.window.doc.durations,[40,90])
        self.window.undo();self.assertEqual(self.window.doc.durations,[40,60,90]);self.assertEqual(self.window.page,1)
        self.window.toggle_animation();self.assertEqual(self.window.animation.interval(),60)
        self.window.animate();self.assertEqual(self.window.page,2);self.assertEqual(self.window.animation.interval(),90)

    def test_single_frame_save_exports_displayed_frame_and_keeps_document_dirty(self):
        source=self.folder/'pages.tif'
        doc=ImageDocument([Image.new('RGB',(16,16),'red'),Image.new('RGB',(16,16),'blue')],source=source)
        save_document(doc,source,'TIFF');self.window.open_path(source)
        self.window.change_page(1);self.window.operation(flip_horizontal=True)
        target=self.folder/'blue.png';opts=default_format('PNG');opts['all_pages']=False
        dialog=SimpleNamespace(exec=lambda:True,options=opts,format='PNG',path=target,keep_time=SimpleNamespace(isChecked=lambda:False))
        with patch('lumaview.ui.viewer.SaveAsDialog',return_value=dialog):self.window.save_as()
        self.assertEqual(load_document(target).frames[0].getpixel((0,0)),(0,0,255))
        self.assertEqual(len(self.window.doc.frames),2);self.assertEqual(self.window.doc.source,source);self.assertTrue(self.window.dirty)
        with patch('lumaview.ui.viewer.SaveAsDialog',return_value=dialog),patch('lumaview.ui.viewer.QMessageBox.question',return_value=QMessageBox.StandardButton.Save):
            self.assertFalse(self.window.guard())

    def test_clipboard_crop_copy_paste_and_document_reset(self):
        self.window.open_path(self.image());self.window.canvas.set_selection((2,3,8,7));self.window.copy()
        self.assertEqual(QApplication.clipboard().image().size().width(),8)
        self.window.paste_side('right');self.assertEqual(self.window.current_image().size,(40,24));self.window.undo()
        self.window.doc.frames.append(Image.new('RGB',(32,24),'green'));self.window.doc.durations=[100,100]
        self.window.toggle_animation();self.window.canvas.clear_selection()
        with patch('lumaview.ui.viewer.QMessageBox.question',return_value=QMessageBox.StandardButton.Discard):self.window.paste()
        self.assertEqual(self.window.current_image().size,(8,7));self.assertEqual(self.window.undo_stack,[])
        self.assertFalse(self.window.animation.isActive());self.assertEqual(self.window.files,[])

    def test_clear_display_stops_timers_and_clears_history(self):
        self.window.open_path(self.image());self.window.operation(negative=True)
        self.window.doc.frames.append(Image.new('RGB',(32,24),'green'));self.window.toggle_animation()
        with patch('lumaview.ui.viewer.QMessageBox.question',return_value=QMessageBox.StandardButton.Discard):self.window.clear_display()
        self.assertIsNone(self.window.doc);self.assertFalse(self.window.animation.isActive());self.assertFalse(self.window.undo_stack)
        self.window.animate();self.window.undo();self.assertIsNone(self.window.doc)

    def test_rename_then_undo_keeps_current_source_path(self):
        source=self.image();self.window.open_path(source);self.window.operation(negative=True)
        with patch('lumaview.ui.viewer.QInputDialog.getText',return_value=('renamed.png',True)):self.window.rename_file()
        target=self.folder/'renamed.png';self.assertTrue(target.exists());self.assertFalse(source.exists())
        self.window.undo();self.assertEqual(self.window.doc.source,target);self.assertEqual(self.window.current_image().getpixel((0,0)),(255,0,0))

    def test_move_is_verified_and_preserves_source_on_copy_failure(self):
        source=self.image();self.window.open_path(source);original=source.read_bytes()
        destination=self.folder/'destination';destination.mkdir()
        with patch('lumaview.ui.viewer.QFileDialog.getExistingDirectory',return_value=str(destination)),patch('lumaview.ui.viewer.shutil.copyfileobj',side_effect=OSError('copy interrupted')):
            self.window.move_copy(True)
        self.assertEqual(source.read_bytes(),original);self.assertFalse((destination/source.name).exists())
        with patch('lumaview.ui.viewer.QFileDialog.getExistingDirectory',return_value=str(destination)):self.window.move_copy(True)
        self.assertFalse(source.exists());self.assertEqual((destination/source.name).read_bytes(),original)
        self.assertEqual(self.window.doc.source,destination/source.name)

    def test_encrypted_pdf_prompts_and_cancellation_keeps_current_document(self):
        source=self.image();self.window.open_path(source)
        pdf=self.folder/'encrypted.pdf';opts=default_format('PDF');opts['pdf_password']='secret'
        save_document(ImageDocument([Image.new('RGB',(20,20),'purple')]),pdf,'PDF',opts)
        with patch('lumaview.ui.viewer.QInputDialog.getText',return_value=('',False)):
            self.assertFalse(self.window.open_path(pdf));self.assertEqual(self.window.doc.source,source)
        with patch('lumaview.ui.viewer.QInputDialog.getText',side_effect=[('wrong',True),('secret',True)]) as prompt:
            self.assertTrue(self.window.open_path(pdf));self.assertEqual(prompt.call_count,2)
        self.assertEqual(self.window.doc.source,pdf);self.assertFalse(self.errors)

    def test_thumbnails_decode_in_background_and_match_rows(self):
        files=[self.image(f'image{i}.png',color=color) for i,color in enumerate(['red','green','blue'])]
        self.window.open_path(files[0]);self.window.populate_thumbnails()
        self.assertEqual(self.window.thumbs.count(),3)
        self.wait(lambda:not self.window._thumbnail_jobs)
        for row,path in enumerate(files):
            self.assertEqual(self.window.thumbs.item(row).data(Qt.ItemDataRole.UserRole),str(path))
            self.assertFalse(self.window.thumbs.item(row).icon().isNull())

    def test_advanced_dialog_options_reach_processing_and_undo(self):
        self.window.open_path(self.image())
        options=default_advanced();options.update(resize=True,width=16,height=12,resample=True,new_dpi=150)
        dialog=SimpleNamespace(exec=lambda:True,options=options)
        with patch('lumaview.ui.advanced.AdvancedDialog',return_value=dialog):self.window.advanced_tool('resize')
        self.assertEqual(self.window.current_image().size,(16,12));self.assertEqual(self.window.doc.dpi,(150,150))
        self.window.undo();self.assertEqual(self.window.current_image().size,(32,24))

    def test_viewer_close_waits_for_batch_preparation_to_stop(self):
        path=self.image();self.window.open_path(path);self.window.show()
        settings=QSettings(str(self.folder/'batch.ini'),QSettings.Format.IniFormat)
        with patch('lumaview.ui.batch_window.QSettings',return_value=settings):self.window.open_batch()
        batch=self.window.batch_windows[0]
        batch.output_dir.setText(str(self.folder/'output'));batch.use_advanced.setChecked(False)
        batch.mode_buttons['convert'].setChecked(True);batch.format_combo.setCurrentIndex(batch.format_combo.findData('JPEG'))
        batch.start_batch();self.assertTrue(batch.is_busy())
        self.assertFalse(self.window.close())
        self.wait(lambda:not self.window.isVisible() and not self.window.batch_windows)
        self.assertTrue(path.exists())


if __name__=='__main__':unittest.main()
