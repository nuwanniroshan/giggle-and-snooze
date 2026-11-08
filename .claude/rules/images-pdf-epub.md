# Pillow / reportlab / EPUB

- ✓ Open images with `with Image.open(p) as im:` and `.convert("RGB")` before saving JPEG or embedding in PDF.
- ✓ Resize with `Image.Resampling.LANCZOS`. Use `ImageOps.fit` for aspect-correct crops to 1920×1080 or the page size.
- ✓ Print PDFs at 300 DPI, screen/EPUB images around 150 DPI. Keep size constants in one place.
- ✓ Register TTF fonts once. [observed] Fonts currently come from macOS Georgia with a Times fallback. Note: web research recommends bundling fonts in `assets/fonts/` — confirm this matches your intent.
- ✓ EPUB: every image needs alt text and an ordered spine/TOC. Validate with `epubcheck`.
- ✗ Don't hold many full-resolution `Image` objects at once. Process one page at a time.
- ✗ Don't re-encode lossy images repeatedly. Keep PNG masters and export once.
