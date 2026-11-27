import tempfile
import unittest
from pathlib import Path

import pymupdf as fitz

from scripts.booklet_images import find_product_photo, prune_images, save_product_photo


def _page_with_photo(doc, photo_rect, price_points):
    page = doc.new_page(width=467, height=779)
    pix = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 60, 80))
    pix.clear_with(120)
    page.insert_image(photo_rect, pixmap=pix)
    for x, y in price_points:
        page.insert_text(fitz.Point(x, y), "0,69", fontsize=40)
    return page


class BookletImageTests(unittest.TestCase):
    def test_photo_above_price_is_matched_and_saved(self):
        doc = fitz.open()
        page = _page_with_photo(doc, fitz.Rect(20, 60, 80, 140), [(20, 150), (250, 150)])
        with tempfile.TemporaryDirectory() as tmp:
            url = save_product_photo(page, 0, 135, 107, 162, 0, Path(tmp), "lidl-test-p0-1")
            self.assertIsNotNone(url)
            self.assertTrue((Path(tmp) / Path(url).name).exists())
            self.assertTrue(url.startswith(tmp.replace("\\", "/")) or "/" in url)
            self.assertIn("lidl-test-p0-1", url)
        doc.close()

    def test_page_wide_banner_is_rejected(self):
        doc = fitz.open()
        page = _page_with_photo(doc, fitz.Rect(5, 60, 460, 140), [(20, 150), (250, 150)])
        found = find_product_photo(page, 0, 135, 107, 162, 0)
        self.assertIsNone(found)
        doc.close()

    def test_photo_in_other_column_is_not_matched(self):
        doc = fitz.open()
        page = _page_with_photo(doc, fitz.Rect(250, 60, 310, 140), [(20, 150), (250, 150)])
        found = find_product_photo(page, 0, 135, 107, 162, 0)
        self.assertIsNone(found)
        doc.close()

    def test_prune_removes_stale_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            (out / "keep.jpg").write_bytes(b"x")
            (out / "stale.jpg").write_bytes(b"x")
            prune_images(out, {"data/x-img/keep.jpg"})
            self.assertTrue((out / "keep.jpg").exists())
            self.assertFalse((out / "stale.jpg").exists())


if __name__ == "__main__":
    unittest.main()