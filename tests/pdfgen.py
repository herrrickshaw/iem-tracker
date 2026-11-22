"""Tiny PDF writer for parser tests: place words at exact coordinates with pymupdf (offline, deterministic)."""
import pymupdf


def make_pdf(path, pages, width=842, height=842, fontsize=7):
    """pages: list of pages; each page is a list of (x, y, text). y is the text baseline in points from the top."""
    doc = pymupdf.open()
    for items in pages:
        page = doc.new_page(width=width, height=height)
        for x, y, text in items:
            page.insert_text((x, y), text, fontsize=fontsize)
    doc.save(str(path))
    doc.close()
    return str(path)
