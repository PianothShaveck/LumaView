# Adaptive monochrome TIFF

Fax 3, Fax 4 and Huffman RLE require one-bit pixels. LumaView's default **Adaptive (IrfanView style)** method performs this conversion before compression:

1. Build a 7-bit-per-channel BGR histogram. Each color-cell count saturates at 65,535.
2. Split the occupied color range along its largest weighted extent (B:G:R = 2:3:1).
3. Compute two representative colors from the histogram.
4. Scan from the bottom row, alternating left-to-right and right-to-left. Use Floyd–Steinberg weights with integer error rounding and a bounded correction: magnitudes below 16 are unchanged, 16–47 use `8 + magnitude // 2`, and larger values use 32.
5. Choose between the two colors using squared B:G:R weights 4:9:1 at color-cell centers; map the result to black and white.

These details affect both the dot pattern and the TIFF byte count. The method never resizes an image or reads reference outputs. Existing one-bit pixels are preserved. Raster inputs use stored RGB values unless **Blend alpha with background** is enabled. SVG canvases are rasterized against the chosen background for monochrome TIFF, since they have no stored RGB behind transparent areas.

The Numba kernels use integer arithmetic and release the Python GIL. The source installer verifies and compiles them before replacing the installed app; it does not require Xcode or a C compiler. Compiled releases build the kernels in memory on first use.

Fax output uses one strip per page with min-is-white photometric interpretation. PDF/SVG rasterization happens before monochrome conversion. Matching their dimensions does not guarantee matching font outlines, geometry or antialiasing across renderers. TIFF headers and retained metadata can change file size without changing the compressed pixel data.
