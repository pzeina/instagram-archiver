"""Catalogue : tri, echappement, cablage des medias, robustesse du CSV."""

import csv
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from igarchive import catalog
from igarchive.catalog import Record


def record(code: str, **kwargs) -> Record:
    base = dict(shortcode=code, url=f"https://www.instagram.com/p/{code}/",
                kind="image", author="quelquun", posted_at_utc="2026-01-01T00:00:00+00:00",
                caption="", files=[], bytes_total=0)
    base.update(kwargs)
    return Record(**base)


class CatalogOutputs(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = TemporaryDirectory()
        self.archive = Path(self.tmp.name)
        self.meta = self.archive / "metadata"
        self.meta.mkdir()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def write(self, *records: Record) -> None:
        for item in records:
            catalog.write_record(self.meta, item)

    def test_sorts_most_recently_saved_first_and_undated_last(self) -> None:
        self.write(
            record("OLD", saved_timestamp=1000, saved_at="2026-01-01T00:00:00+00:00"),
            record("NEW", saved_timestamp=2000, saved_at="2026-02-01T00:00:00+00:00"),
            record("NODATE", saved_rank=7),
        )
        order = [r["shortcode"] for r in catalog.load_records(self.meta)]
        self.assertEqual(order, ["NEW", "OLD", "NODATE"])

    def test_csv_quotes_a_caption_containing_commas_and_quotes(self) -> None:
        self.write(record("X", caption='Un titre, avec "guillemets"\net un retour'))
        catalog.write_csv_catalog(self.archive, catalog.load_records(self.meta))
        with (self.archive / "catalog.csv").open(encoding="utf-8-sig") as handle:
            rows = list(csv.DictReader(handle))
        self.assertEqual(len(rows), 1)
        # Le retour a la ligne est aplati pour garder une ligne par contenu.
        self.assertEqual(rows[0]["caption"], 'Un titre, avec "guillemets" et un retour')

    def test_csv_starts_with_a_bom_so_excel_reads_the_accents(self) -> None:
        self.write(record("X", caption="éàü"))
        catalog.write_csv_catalog(self.archive, catalog.load_records(self.meta))
        self.assertTrue((self.archive / "catalog.csv").read_bytes().startswith(b"\xef\xbb\xbf"))

    def test_html_escapes_markup_found_in_a_caption(self) -> None:
        self.write(record("X", caption='<script>alert("xss")</script>'))
        catalog.write_html_catalog(self.archive, catalog.load_records(self.meta))
        page = (self.archive / "index.html").read_text(encoding="utf-8")
        self.assertNotIn("<script>alert", page)
        self.assertIn("&lt;script&gt;", page)

    def test_html_wires_a_video_with_its_poster(self) -> None:
        self.write(record("V", kind="reel", is_video=True,
                          files=["media/2026/v/clip.mp4", "media/2026/v/vignette.jpg"]))
        catalog.write_html_catalog(self.archive, catalog.load_records(self.meta))
        page = (self.archive / "index.html").read_text(encoding="utf-8")
        self.assertIn('src="media/2026/v/clip.mp4"', page)
        self.assertIn('poster="media/2026/v/vignette.jpg"', page)

    def test_html_survives_a_record_with_no_media_at_all(self) -> None:
        self.write(record("EMPTY", files=[]))
        catalog.write_html_catalog(self.archive, catalog.load_records(self.meta))
        self.assertIn("aucun media",
                      (self.archive / "index.html").read_text(encoding="utf-8"))

    def test_html_is_valid_with_an_empty_archive(self) -> None:
        catalog.write_html_catalog(self.archive, [])
        page = (self.archive / "index.html").read_text(encoding="utf-8")
        self.assertIn("Aucun contenu archive", page)

    def test_saved_dates_are_applied_to_records_written_earlier(self) -> None:
        """L'export arrive apres coup : les fiches deja ecrites doivent en profiter."""
        self.write(record("X"))
        patched = catalog.apply_saved_dates(self.meta, {
            "X": {"saved_at": "2026-03-01T10:00:00+00:00", "saved_timestamp": 1772359200,
                  "collection": "Cuisine", "collections": ["Cuisine"]},
        })
        self.assertEqual(patched, 1)
        stored = json.loads((self.meta / "X.json").read_text(encoding="utf-8"))
        self.assertEqual(stored["saved_at"], "2026-03-01T10:00:00+00:00")
        self.assertEqual(stored["collection"], "Cuisine")

    def test_a_corrupt_record_does_not_break_the_catalogue(self) -> None:
        self.write(record("GOOD"))
        (self.meta / "BROKEN.json").write_text("{ pas du JSON", encoding="utf-8")
        result = catalog.build(self.archive, self.meta)
        self.assertEqual(result["count"], 1)

    def test_unknown_fields_in_a_record_are_ignored(self) -> None:
        """Une fiche ecrite par une version ulterieure reste lisible."""
        item = Record.from_dict({"shortcode": "X", "champ_futur": 1})
        self.assertEqual(item.shortcode, "X")


if __name__ == "__main__":
    unittest.main()
