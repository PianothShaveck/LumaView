"""Generate deterministic samples and capture the real Qt interface."""
import os,sys,math
from pathlib import Path
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
import numpy as np
from PIL import Image,ImageDraw
from PySide6.QtWidgets import QApplication,QGroupBox
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtGui import QImage,QPainter
from lumaview.ui.common import apply_style
from lumaview.ui.viewer import ViewerWindow
from lumaview.ui.batch_window import BatchWindow
from lumaview.ui.advanced import AdvancedDialog
from lumaview.ui.save_dialog import SaveAsDialog
from lumaview.model import default_advanced
assets=ROOT/'assets';assets.mkdir(exist_ok=True)
from build_icon import build_icon
app=build_icon();app.setOrganizationName('LumaView-QA');app.setApplicationName('Screenshot');apply_style(app)
samples=ROOT/'samples';samples.mkdir(exist_ok=True)
w,h=1600,1060;x=np.linspace(0,1,w)[None,:,None];y=np.linspace(0,1,h)[:,None,None]
base=np.broadcast_to(np.array([186,217,226])*(1-y)+np.array([237,215,170])*y,(h,w,3)).copy();base=base*(1-.05*x)
im=Image.fromarray(base.astype('uint8'));d=ImageDraw.Draw(im);d.ellipse((1110,105,1330,325),fill='#fff4d0')
d.polygon([(0,670),(0,590),(200,510),(290,520),(510,310),(695,600),(880,395),(1050,655),(1400,460),(1600,570),(1600,1060),(0,1060)],fill='#6d9b9f')
d.polygon([(0,710),(210,550),(520,795),(810,625),(1040,715),(1310,520),(1600,790),(1600,1060),(0,1060)],fill='#356978')
d.polygon([(0,815),(205,810),(478,916),(840,800),(1080,865),(1250,760),(1600,860),(1600,1060),(0,1060)],fill='#204459')
d.polygon([(690,1060),(915,840),(930,780),(946,784),(940,857),(763,1060)],fill='#cbdbe0')
for i in range(19):d.line((0,940+i*6,1600,940+i*6),fill='#365767',width=1)
im.save(samples/'01-Blue-ridge.png',dpi=(144,144));im.crop((400,0,1460,1060)).save(samples/'02-Ridge-detail.jpg',quality=94);im.resize((800,530)).convert('L').save(samples/'03-Study.tif',compression='tiff_lzw')
out=ROOT/'docs/images';out.mkdir(parents=True,exist_ok=True)
w=ViewerWindow();w.resize(1180,780);w.open_path(samples/'01-Blue-ridge.png');w.show();app.processEvents();w.canvas.fit();app.processEvents();w.grab().save(str(out/'viewer.png'))
b=BatchWindow(paths=sorted(p for p in samples.iterdir() if p.is_file()),parent=w);b.resize(1260,960);b.output_dir.setText(str(samples/'Converted'));b.format_combo.setCurrentIndex(b.format_combo.findData('PNG'));b.use_advanced.setChecked(True);b.advanced.update(resize=True,resize_mode='percent',percent_width=50,percent_height=50);b.show()
from time import monotonic
until=monotonic()+1
while monotonic()<until:app.processEvents()
# Use neutral example paths in documentation; the real file model and images
# remain loaded from the generated samples.
b.location.setText('~/Pictures/Samples');b.output_dir.setText('~/Pictures/Converted')
for index in range(b.queue.count()):
 item=b.queue.item(index);item.setText('~/Pictures/Samples/'+Path(item.text()).name)
app.processEvents()
b.grab().save(str(out/'batch.png'))
a=default_advanced();a.update(resize=True,resize_mode='percent',percent_width=50,percent_height=50)
adv=AdvancedDialog(a,w);adv.resize(1280,1000);adv.show();app.processEvents();adv.grab().save(str(out/'advanced.png'))
for group in adv.findChildren(QGroupBox):
 if 'resize' in group.title().lower():group.grab().save(str(out/'advanced_resize.png'));break
s=SaveAsDialog(w.doc,w);s.resize(1190,800);s.show();app.processEvents();s.grab().save(str(out/'save.png'))
for dialog in [s,adv,b]:dialog.close()
w.dirty=False;w.close();app.processEvents();print('Saved UI screenshots to',out)
