# Pillow / reportlab / EPUB

- ✓ Open images with `with Image.open(p) as im:` and `.convert("RGB")` before saving JPEG or embedding in PDF.
- ✓ Resize with `Image.Resampling.LANCZOS`. Use `ImageOps.fit` for aspect-correct crops to 1920×1080 or the page size.
- ✓ Print PDFs at 300 DPI, screen/EPUB images around 150 DPI. Keep size constants in one place.
- ✓ Register TTF fonts once. Fonts are bundled in `assets/fonts/` (Young Serif, Andika; SIL OFL, licences beside them) and embedded in every PDF and EPUB (spec 023). Only use OFL-or-similar faces: a licence that forbids subsetting or extractable embedding rules a font out.
- ✓ EPUB: every image needs alt text and an ordered spine/TOC. Validate with `epubcheck`.
- ✗ Don't hold many full-resolution `Image` objects at once. Process one page at a time.
- ✗ Don't re-encode lossy images repeatedly. Keep PNG masters and export once.
