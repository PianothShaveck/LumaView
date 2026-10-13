# Feature coverage

This inventory compares the IrfanView-inspired workflow with LumaView's implemented behavior. It does not imply binary compatibility with IrfanView plug-ins, settings or image-processing results. The interface is English.

## Batch workflow

| Reference feature | LumaView behavior |
| --- | --- |
| `B` opens Batch conversion / rename | Implemented. |
| Conversion; rename; conversion plus rename | Implemented. Rename writes byte-verified renamed **copies** to the output folder by default. "Move originals after success" removes the source only after a verified copy. |
| Output format and Options | Implemented with the same encoder controls as Save As. Only available output encoders are listed. |
| Use advanced options / Advanced | Implemented; three-column operation dialog with scroll support. |
| Folder browser; add selected; add all | Implemented. |
| Include subfolders | Implemented. |
| Remove selected/all; move up/down; sort | Implemented. |
| Load/save input list as text | Implemented. |
| Thumbnail preview | Implemented. |
| Name pattern; start number; increment | Implemented. `#` groups create zero-padded counters; `$N` is the original stem, `$F` the original filename, `$E` the extension, `$D` the parent folder name. |
| Rename preview | Implemented. Checks collisions and shows proposed destinations before writing. |
| Output directory | Explicit directory; IrfanView's arbitrary output-directory placeholders are not implemented. |
| Multithreading | Implemented, up to 16 workers. Large images and multipage documents require more memory per worker. |
| Progress, cancellation and result log | Implemented. Cancelling keeps completed outputs; it is not a rollback. |
| Existing-file conflicts | Preflight rejects duplicate destinations, input-file overwrites and case-folding conflicts. Existing unrelated output files require the overwrite option. |
| Overwrite / delete originals | Off by default. Conversion is written to a temporary file and verified before commit. Originals are retained when output loses pages or frames, the input changes, or cancellation is observed. PDF originals are retained because raster conversion loses document objects; EPS sources are retained because verification cannot establish full fidelity. |

## Advanced operations

| Reference feature | LumaView behavior |
| --- | --- |
| Crop X/Y/width/height, anchor, current selection | Implemented with nine anchors. With the top-left anchor, negative X/Y count from the right/bottom; with other anchors they are signed offsets. |
| Resize one or both dimensions | Implemented. Zero means unspecified; at least one positive dimension is needed to change size. |
| Long side / short side / megapixels / percentage | Implemented. |
| Pixels / centimeters / inches | Implemented. Physical sizes depend on DPI. |
| Preserve aspect ratio | Implemented. |
| **Use Resample function (better quality)** | Implemented. Lanczos, Bicubic, Bilinear, Box, Hamming and Nearest filters; unchecked uses nearest-neighbor. Resizing to 100% preserves the pixels; this is not a file-compression option. Results are not claimed to match IrfanView pixel for pixel. |
| Maximum / minimum size | Maximum fits within both bounds; minimum scales until both bounds are met. Minimum size does not crop. |
| Resize using old/new DPI | Implemented; changes pixel dimensions by the DPI ratio. For density metadata only, enable Resize, use dimensions W/H = 0, set New DPI, and leave the DPI-ratio option off. |
| Do not enlarge / do not shrink | Implemented. |
| 32 / 24 / 8 / 4 / 1 BPP and custom palette | Implemented. Choosing 1/4/8/custom/24 BPP composites transparency on white; 32 BPP preserves alpha. Palette sizes are 2-256 colors. RGB 565 reduces color precision, not necessarily the stored bit depth. |
| Floyd-Steinberg dithering / best palette | Implemented. Output format and alpha can require a different stored mode. |
| Auto-adjust colors | Implemented using per-channel autocontrast. |
| Horizontal/vertical flip; left/right rotation | Implemented. |
| Grayscale / negative | Implemented. |
| Auto-crop borders | Implemented with tolerance. |
| Canvas size | Implemented, absolute or relative size, anchor and color. |
| Overlay text | Implemented, with font file, size, color, optional background, opacity, position and filename tokens. `$W`/`$H` are image dimensions; `$I` is the input index; `###` inserts a padded counter. |
| Watermark image | Implemented, with scale, opacity, anchor and offsets. |
| Replace color | Implemented with tolerance. |
| Border/frame | Implemented with separate edge widths and color. |
| RGB channel permutations | All five non-identity permutations implemented. |
| Sharpen / brightness / contrast / gamma / saturation | Implemented. Slider scales are LumaView's own; values are not interchangeable with other editors. |
| Red / green / blue balance | Implemented. |
| Blur | Implemented using Gaussian blur. |
| Fine rotation | Implemented with angle, expand canvas and background. |
| More effects | Emboss, edges, contour, smooth, detail, solarize, posterize and sepia; not IrfanView's complete effects/plugin catalog. |
| Create subfolders / original date-time | Implemented. Date-time preserves filesystem access/modified timestamps, not every platform-specific creation-date attribute. |
| Apply changes to all pages | Implemented; controls processing independently of the output format's all-pages option. |
| Custom processing order | Implemented. The default applies color depth last. Moving it before text, watermark or other operations may expand the image back to RGB/RGBA. |
| Save/load settings | JSON processing presets; IrfanView INI settings are not imported. |

## Save options

Save As keeps the format options visible beside the file browser. Profiles are stored per format. Source metadata is retained only where the destination format and encoder support it.

| Format | Implemented options and limits |
| --- | --- |
| TIFF | None, LZW, PackBits, JPEG, ZIP/Deflate, Huffman RLE, CCITT Fax 3 and Fax 4; all pages; grayscale palette; EXIF/ICC, including nested EXIF/GPS directories in compressed TIFF. Fax/RLE defaults to adaptive two-color quantization with bounded, serpentine error diffusion. It uses stored RGB values unless alpha compositing is enabled. High contrast (legacy), ordinary Floyd–Steinberg and a configurable threshold are also available. No method resizes pixels; already 1-bit images are retained exactly. Packed 4-bit TIFF retains DPI, ICC and comments, but reports that EXIF/XMP/IPTC cannot be kept. |
| JPEG | Quality; progressive; grayscale; 4:4:4/no chroma subsampling; EXIF, IPTC, comment, XMP and ICC retention; EXIF orientation reset; side-by-side encoding preview; target file size. |
| Original JPEG quality | Reuses source quantization tables and, where requested, subsampling. It still re-encodes the pixels and is **not** lossless JPEG editing. |
| JPEG target size | Searches for a quality whose encoded bytes fit the requested limit. Reports an error if it cannot fit. Cannot be combined with original-quality mode. |
| GIF | Interlace; all frames; custom transparent color or palette index. At most 256 palette colors per frame; fractional alpha becomes binary transparency. |
| PNG | Lossless compression 0-9; transparent color; alpha or color-key transparency; ICC; all animation frames/APNG. |
| OptiPNG | Optional external optimization levels 0-7. Disabled when the executable is absent. Normal PNG export does not require it. |
| PBM / PGM / PPM | Binary or ASCII encoding. |
| ICO | Transparency and 16, 24, 32, 48, 64, 128 and 256 pixel icon sizes. |
| PDF | JPEG or lossless raster embedding; quality; DPI; image-sized, A4, A3, Letter or Legal pages; orientation; fit; all pages; background; title; author; opening password. |
| PDF limitation | Input is rasterized. Output does not preserve original vector objects, searchable text, links or forms. Output DPI determines physical placement; it does not recreate lost detail. |
| WebP / HEIF / JPEG XL / JPEG 2000 | Available encoders expose quality/lossless controls; metadata and frame support vary by encoder. |
| AVIF | Quality-based export. Lossless AVIF is not implemented. |
| JPEG-LS | Lossless pixel encoding through imagecodecs when available. Export does not retain metadata. |
| RAW | Headerless pixel output; RGB, RGBA or grayscale. This is unrelated to camera RAW formats. |
| Other output formats | BMP, PCX, QOI, TGA, DDS and EPS when their encoders are available. |
| Missing reference output formats | ECW and EMF are not implemented. Windows plugin DLLs are not supported. |

Input additionally includes PDF, SVG/SVGZ, PSD composites, supported camera RAW files and the readers included by Pillow. Camera RAW is developed to 8-bit RGB. EPS/PostScript input requires separately installed Ghostscript. The runtime list in **Help > Supported formats** is authoritative for the installed codecs; individual file variants may still be unsupported.

PhotoLine PLD input reads the first embedded JPEG/PNG preview, not the full-resolution layers. PLB input reads the first thumbnail in the browser cache. The viewer and conversion log identify this limitation, and conversion never deletes these originals. ZIP archives are excluded from image scans; extract them before adding images. Batch scanning uses the same supported-extension list as the viewer.

Vector input has a rendering resolution. **Settings** controls PDF input DPI (default 96) and SVG raster size (default 1024 × 800, with optional aspect preservation), for both the viewer and batches. PDF export DPI is separate. Rasterization, fonts and antialiasing can differ between rendering engines even at identical dimensions.

## Viewer and menus

| Area | Implemented |
| --- | --- |
| File | Open/reopen/recent; external editor; headerless RAW opening; thumbnails; slideshow; batch; file search; rename/move/copy; Trash; Save/Save As; compare; save selection; print. |
| Scan | Opens macOS Image Capture. No TWAIN source selection, integrated batch-scanner driver or Copy Shop workflow. |
| Selection and editing | Undo/redo; freehand paint; coordinate and fixed-ratio selection; restore/center selection; inverted selection for applicable effects; grids; text/watermarks; cut/clear/crop; insert/remove strips; auto-crop; viewer capture; clipboard copy/paste/paste on side. |
| Image | Information; blank image; merge; rotation/flip/resize/canvas/border; rounded corners/shadow and shape masks; depth; grayscale/negative; channels; corrections; transparency; histogram; color replacement; tiling; auto colors; sharpness; red-eye reduction; effects; channel swaps; palette display. |
| Animation and multipage | Start/stop animation; frame export; page/frame navigation; append images; delete a page; export image tiles. |
| Files and desktop | EXIF date/time edit; screen capture; watched folder; file sorting; wallpaper; minimize; always on top. |
| OCR | Local text recognition through Apple Vision on macOS. The installer includes the Python-to-macOS bridge; no online OCR service is used. Recognition is ordinary text OCR, not music notation recognition. |
| View | Status/toolbar visibility; full screen; fit; next/previous/first/last/random file; refresh; zoom; zoom/scroll locks; actual size; hex view. |
| Help | Illustrated guide, keyboard shortcuts, installed format support and version information. |

## Deliberately different or deferred

- JPEG coefficient-domain lossless rotation/cropping; Adobe 8BF filters; IrfanView plugin DLLs; multimedia player; email plugin; TWAIN and Copy Shop are not included.
- Toolbar skins, custom favorite menus, dedicated menu/titlebar visibility toggles, IrfanView language DLLs and Windows file-association settings are not reproduced.
- Italian localization is not included in this version.
- LumaView is primarily an 8-bit editing workflow. Some decoders preserve higher-depth source modes, but transformations and exports may reduce precision. It is not an archival high-bit-depth or HDR editor.
- macOS integration and the installer require validation on an actual Mac. Linux tests and screenshots do not certify macOS Gatekeeper, drag-and-drop, printing, permissions or packaging behavior.
