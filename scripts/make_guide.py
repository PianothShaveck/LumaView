#!/usr/bin/env python3
"""Build the illustrated user guide from real application screenshots."""
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.styles import ParagraphStyle
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas
from reportlab.platypus import Paragraph
from PIL import Image


ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs" / "LumaView-Guide.pdf"
IMAGES = ROOT / "docs" / "images"
PAGE = (595.276, 841.89)
W, H = PAGE
M = 40
INK = colors.HexColor("#17283B")
MUTED = colors.HexColor("#516275")
BLUE = colors.HexColor("#245FAD")
PALE = colors.HexColor("#F0F5FA")
LINE = colors.HexColor("#DCE4ED")


def font_setup():
    pairs = [
        ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
        ("/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf", "/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf"),
        ("/System/Library/Fonts/Supplemental/Arial.ttf", "/System/Library/Fonts/Supplemental/Arial Bold.ttf"),
    ]
    for regular, heavy in pairs:
        normal, bold = Path(regular), Path(heavy)
        if normal.exists() and bold.exists():
            pdfmetrics.registerFont(TTFont("Guide", str(normal)))
            pdfmetrics.registerFont(TTFont("Guide-Bold", str(bold)))
            pdfmetrics.registerFontFamily("Guide", normal="Guide", bold="Guide-Bold", italic="Guide", boldItalic="Guide-Bold")
            return
    pdfmetrics.registerFont(pdfmetrics.Font("Guide", "Helvetica", "WinAnsiEncoding"))
    pdfmetrics.registerFont(pdfmetrics.Font("Guide-Bold", "Helvetica-Bold", "WinAnsiEncoding"))
    pdfmetrics.registerFontFamily("Guide", normal="Guide", bold="Guide-Bold", italic="Guide", boldItalic="Guide-Bold")


def para(c, text, x, top, width=W - 2*M, size=10.4, color=INK, leading=None, bold=False):
    p = Paragraph(text, ParagraphStyle("guide", fontName="Guide-Bold" if bold else "Guide", fontSize=size,
                   leading=leading or size*1.45, textColor=color, spaceAfter=0))
    _, height = p.wrap(width, H)
    p.drawOn(c, x, top-height)
    return top-height


def header(c, number, title, subtitle=""):
    c.setFillColor(BLUE)
    c.rect(0, H-8, W, 8, fill=1, stroke=0)
    para(c, "LUMAVIEW  /  USER GUIDE", M, H-34, size=8.2, color=MUTED, bold=True)
    para(c, title, M, H-61, size=25, leading=30, bold=True)
    if subtitle:
        para(c, subtitle, M, H-99, size=10.2, color=MUTED)
    c.setStrokeColor(LINE)
    c.line(M, 39, W-M, 39)
    para(c, "LumaView  |  English", M, 29, size=7.4, color=MUTED)
    c.setFont("Guide", 7.4)
    c.setFillColor(MUTED)
    c.drawRightString(W-M, 20, str(number))


def picture(c, name, top, max_height, caption="", x=None, max_width=None):
    path = IMAGES / name
    if not path.exists():
        raise FileNotFoundError(f"Missing actual application screenshot: {path}")
    with Image.open(path) as im:
        iw, ih = im.size
    scale = min((max_width or (W-2*M))/iw, max_height/ih)
    width, height = iw*scale, ih*scale
    x = (W-width)/2 if x is None else x
    c.setFillColor(colors.white)
    c.setStrokeColor(LINE)
    c.roundRect(x-0.5, top-height-0.5, width+1, height+1, 4, fill=1, stroke=1)
    c.drawImage(str(path), x, top-height, width=width, height=height, mask="auto")
    bottom = top-height
    if caption:
        bottom = para(c, caption, M, bottom-8, size=8, color=MUTED)
    return bottom


def row(c, title, body, top):
    y = para(c, title, M, top, size=10.3, bold=True)
    return para(c, body, M, y-4, size=10.1)-14


def note(c, title, body, top):
    style = ParagraphStyle("note", fontName="Guide", fontSize=9.5, leading=13.5, textColor=INK)
    p = Paragraph(f"<b>{title}</b><br/>{body}", style)
    _, height = p.wrap(W-2*M-24, H)
    c.setFillColor(PALE)
    c.roundRect(M, top-height-20, W-2*M, height+20, 6, fill=1, stroke=0)
    p.drawOn(c, M+12, top-height-10)
    return top-height-32


def table(c, rows, top, first_width=130):
    y = top
    for left, right in rows:
        style = ParagraphStyle("cell", fontName="Guide", fontSize=9.1, leading=12.8, textColor=INK)
        lp = Paragraph(f"<b>{left}</b>", style)
        rp = Paragraph(right, style)
        _, lh = lp.wrap(first_width-14, H)
        _, rh = rp.wrap(W-2*M-first_width-10, H)
        height = max(lh, rh)+15
        c.setStrokeColor(LINE)
        c.line(M, y-height, W-M, y-height)
        lp.drawOn(c, M, y-lh-5)
        rp.drawOn(c, M+first_width, y-rh-5)
        y -= height
    return y


def build():
    font_setup()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    c = canvas.Canvas(str(OUT), pagesize=PAGE)
    c.setTitle("LumaView - User Guide")
    c.setAuthor("LumaView contributors")
    c.setSubject("Installation, viewing, batch conversion, resampling and export")

    header(c, 1, "Install and open", "Apple Silicon  |  macOS 14 or later")
    y = H-143
    y = row(c, "1  Download the macOS app", "From GitHub Releases, download <b>LumaView-&lt;version&gt;-macos-arm64.dmg</b>. Open it and drag <b>LumaView</b> to <b>Applications</b>. The app includes Python and the required libraries.", y)
    y = row(c, "2  Open LumaView", "Open the app from Applications. The <b>macos-arm64.zip</b> contains the same app if you prefer a ZIP download.", y)
    y = row(c, "3  Allow opening if blocked", "Builds are ad-hoc signed, without Developer ID signing or notarization. After trying to open the app, go to <b>System Settings &gt; Privacy &amp; Security</b>. Choose <b>Open Anyway</b>, authenticate if asked, then choose <b>Open</b>.", y)
    y = note(c, "A damaged-app alert is different", "If macOS reports that the app is damaged or will damage your computer, do not treat it as the standard unidentified-developer prompt. Download a fresh release and report the exact alert if it persists.", y)
    y = row(c, "Alternative: source installer", "Extract <b>LumaView-&lt;version&gt;-installer.zip</b> and double-click <b>Install.command</b>. It downloads native Python and the required libraries, then opens <b>~/Applications/LumaView.app</b>. Internet access is required; Homebrew, developer tools and administrator access are not.", y)
    y = row(c, "Updating a source installation", "Run <b>Install.command</b> again. You can delete the extracted installer folder afterward; keep <b>~/Library/Application Support/LumaView</b>, which contains the installed code and runtime.", y)
    para(c, 'Apple opening instructions: <link href="https://support.apple.com/en-us/102445" color="#245FAD">support.apple.com/en-us/102445</link>', M, y-2, size=8.7, color=MUTED)
    c.showPage()

    header(c, 2, "View and save", "Format options remain beside the save controls.")
    y = picture(c, "viewer.png", H-136, 180)
    y = row(c, "Open and browse", "Open a file, or drag it into the window. Use <b>Space</b> for the next file and <b>Backspace</b> for the previous one. Use <b>+</b> and <b>-</b> to zoom.", y-19)
    y = picture(c, "save.png", y-1, 320)
    y = row(c, "Save a copy", "Press <b>S</b>, choose the output format, review the options on the right, then save. Options belong to the selected format; changing the filename extension alone does not select a different encoder.", y-16)
    c.showPage()

    header(c, 3, "Batch conversion and rename", "Press B from the viewer.")
    y = picture(c, "batch.png", H-136, 385)
    y = row(c, "1  Choose the job", "Select conversion, rename, or conversion plus rename. Rename creates renamed copies by default. Add files to the input list and arrange them in the required order.", y-18)
    y = row(c, "2  Choose output", "Select the format and its <b>Options</b>, then choose an output folder. Enable <b>Use advanced options</b> to apply the operations selected in <b>Advanced</b>.", y)
    y = row(c, "3  Start", "Use <b>Test names</b> to review filenames. Click <b>Start batch</b> and review the result log for errors and warnings. Cancelling keeps completed outputs.", y)
    y = note(c, "Original files", "Overwrite, delete-original and move-original options are off by default. Leave them off when creating converted copies.", y)
    c.showPage()

    header(c, 4, "Resize and resample", "In Batch, enable Use advanced options, then choose Advanced.")
    image_bottom = picture(c, "advanced_resize.png", H-142, 500, x=M, max_width=246)
    x, width, y = M+266, W-2*M-266, H-146
    y = para(c, "Choose the size", x, y, width=width, size=10.3, bold=True)
    y = para(c, "Enable <b>Resize</b>. Set width/height, a long or short side, megapixels, or percentages. Keep <b>Preserve aspect ratio</b> enabled to avoid distortion. A zero dimension leaves that dimension unspecified.", x, y-7, width=width, size=10)-20
    y = para(c, "Resample on", x, y, width=width, size=10.3, bold=True)
    y = para(c, "Uses the selected interpolation filter. <b>Lanczos</b> is the default for smooth photographic resizing.", x, y-7, width=width, size=10)-20
    y = para(c, "Resample off", x, y, width=width, size=10.3, bold=True)
    y = para(c, "Uses <b>nearest-neighbor</b>, keeping hard pixel edges. Neither method recreates missing source detail.", x, y-7, width=width, size=10)-20
    y = row(c, "Control the result", "Maximum size fits inside the target box; minimum size meets both bounds without cropping. The no-enlarge and no-shrink options limit which images change size.", min(y,image_bottom)-23)
    y = row(c, "DPI", "The DPI-ratio option changes pixel dimensions to preserve physical size. To change only density metadata, enable <b>Resize</b>, choose <b>Set one or both sides</b> with W/H at 0, set <b>New DPI</b>, and leave the DPI-ratio option off.", y)
    c.showPage()

    header(c, 5, "Operations and format choices", "Review the output format before running a large batch.")
    y = table(c, [
        ("Advanced", "Crop; resize; color depth; flips and rotations; grayscale and negative; auto colors and border crop; canvas; text and image watermark; replacement color; border; RGB channel order; sharpness, brightness, contrast, gamma, saturation, color balance and blur."),
        ("Order and presets", "Use custom processing order when one operation depends on another. Save and load settings to reuse a batch recipe."),
        ("JPEG", "Quality, progressive encoding, grayscale, chroma subsampling and metadata controls. Target size searches for a quality that fits; saving fails if no tested quality can meet the limit."),
        ("PNG", "Compression level 0-9 is lossless. Higher levels change file size and speed, not image quality. Alpha transparency is preserved unless deliberately replaced."),
        ("TIFF", "LZW, PackBits, JPEG, ZIP and fax/RLE compression. <b>Adaptive (IrfanView style)</b> converts to 1-bit for fax/RLE. Enable <b>Blend alpha with background</b> to flatten transparency; leave it off to use stored RGB. JPEG compression is lossy."),
        ("GIF", "Palette-based output with at most 256 colors per frame and a single transparent palette entry. Partial alpha cannot be preserved."),
        ("PDF", "Raster pages with JPEG or lossless image compression. Choose page size, DPI and fit. Opening and saving a PDF here does not preserve vector objects, searchable text, forms or links."),
        ("Other formats", "Available encoders appear in the output list. RAW output is headerless pixel bytes and requires external knowledge of dimensions and pixel layout to reopen."),
    ], H-139)
    y = note(c, "Multipage files", "<b>Apply changes to all pages</b> controls processing. The format's <b>Save all pages/frames</b> option controls output. Check both when converting a whole document or animation.", y-20)
    c.showPage()

    header(c, 6, "Shortcuts and troubleshooting", "Single-letter shortcuts apply in the viewer.")
    y = table(c, [
        ("B", "Batch conversion / rename"),
        ("S", "Save as, with format options"),
        ("Space / Backspace", "Next / previous file"),
        ("+ / -", "Zoom in / out"),
        ("L / R", "Rotate left / right"),
        ("H / V", "Flip horizontally / vertically"),
    ], H-142)
    y = row(c, "Format cannot open or save", "Check <b>Help &gt; Supported formats</b>. Run <b>Install.command</b> again to repair the source installation. Optional OptiPNG and Ghostscript are separate: normal PNG saving does not require OptiPNG.", y-27)
    y = row(c, "Colors or transparency changed", "JPEG has no alpha; GIF has a restricted palette. In Advanced, 1/4/8/custom/24-bit depth composites transparency on white; 32-bit preserves it. Use PNG or lossless TIFF to retain alpha.", y)
    y = row(c, "The PDF looks soft", "In <b>Options &gt; Settings</b>, increase <b>PDF opening resolution (DPI)</b>, then reopen the PDF. Increasing output DPI alone cannot recover detail lost during opening.", y)
    y = row(c, "Report a problem", "Include the app version, macOS version, codec status, selected output options and the error text. Include a small sample file when it can be shared.", y)
    para(c, "Feature coverage and deferred IrfanView commands are listed in docs/FEATURES.md.", M, y-5, size=8.7, color=MUTED)
    c.showPage()
    c.save()
    print(OUT)


if __name__ == "__main__":
    build()
