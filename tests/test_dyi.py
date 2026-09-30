"""L'export officiel : format localise, encodage casse, collections."""

import json
import unittest
import zipfile
from pathlib import Path
from tempfile import TemporaryDirectory

from igarchive import dyi


def mojibake(text: str) -> str:
    """Reproduit le defaut d'encodage des exports Instagram."""
    return text.encode("utf-8").decode("latin-1")


SAVED = {"saved_saved_media": [
    {"title": mojibake("créateur.périgord"),
     "string_map_data": {"Enregistré le": {
         "href": "https://www.instagram.com/reel/AAA1/", "timestamp": 1710000000}}},
    {"title": "studio_nordic",
     "string_map_data": {"Saved on": {
         "href": "https://www.instagram.com/p/BBB2/", "timestamp": 1720500000}}},
    {"title": "sans_lien", "string_map_data": {"Autre": {"value": "rien"}}},
]}

COLLECTIONS = {"saved_saved_collections": [
    {"title": mojibake("Recettes été"),
     "string_map_data": {"Name": {
         "href": "https://www.instagram.com/reel/AAA1/", "timestamp": 1710000500}}},
]}


class ExportParsing(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = TemporaryDirectory()
        root = Path(self.tmp.name) / "export/your_instagram_activity/saved"
        root.mkdir(parents=True)
        (root / "saved_posts.json").write_text(json.dumps(SAVED, ensure_ascii=False),
                                               encoding="utf-8")
        (root / "saved_collections.json").write_text(json.dumps(COLLECTIONS, ensure_ascii=False),
                                                     encoding="utf-8")
        self.folder = Path(self.tmp.name) / "export"
        self.zip = Path(self.tmp.name) / "export.zip"
        with zipfile.ZipFile(self.zip, "w") as archive:
            for path in self.folder.rglob("*.json"):
                archive.write(path, str(path.relative_to(self.tmp.name)))

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_reads_a_zip_and_a_folder_identically(self) -> None:
        """Seul « source_file » differe legitimement : il est relatif a la racine
        de la source, qui n'est pas la meme pour une archive et pour un dossier."""
        def without_source(parsed: dict) -> dict:
            return {code: {k: v for k, v in record.items() if k != "source_file"}
                    for code, record in parsed.items()}

        self.assertEqual(without_source(dyi.parse(self.zip)),
                         without_source(dyi.parse(self.folder)))

    def test_finds_entries_whatever_the_field_language(self) -> None:
        parsed = dyi.parse(self.zip)
        self.assertEqual(set(parsed), {"AAA1", "BBB2"})

    def test_repairs_the_broken_encoding(self) -> None:
        parsed = dyi.parse(self.zip)
        self.assertEqual(parsed["AAA1"]["collection"], "Recettes été")

    def test_ignores_entries_without_a_link(self) -> None:
        self.assertNotIn("sans_lien", {r.get("author_hint") for r in dyi.parse(self.zip).values()}
                         - {"créateur.périgord", "studio_nordic", None})

    def test_keeps_the_collection_and_the_earliest_date(self) -> None:
        """Un post figure dans saved_posts ET dans sa collection : ni l'un ni
        l'autre ne doit ecraser le reste."""
        record = dyi.parse(self.zip)["AAA1"]
        self.assertEqual(record["saved_timestamp"], 1710000000)   # la plus ancienne
        self.assertEqual(record["collections"], ["Recettes été"])

    def test_merging_twice_adds_nothing(self) -> None:
        first = dyi.parse(self.zip)
        merged, added = dyi.merge_into(dict(first), dyi.parse(self.zip))
        self.assertEqual(added, 0)
        self.assertEqual(set(merged), set(first))

    def test_rejects_a_source_that_is_neither_zip_nor_folder(self) -> None:
        with self.assertRaises(dyi.ExportError):
            dyi.parse(Path(self.tmp.name) / "absent.zip")

    def test_reports_an_export_without_the_expected_files(self) -> None:
        empty = Path(self.tmp.name) / "vide"
        empty.mkdir()
        with self.assertRaises(dyi.ExportError):
            dyi.parse(empty)

    def test_recognises_every_url_shape(self) -> None:
        for url, expected in [
            ("https://www.instagram.com/p/ABC-1_x/", "ABC-1_x"),
            ("https://instagram.com/reel/XYZ9/?igsh=1", "XYZ9"),
            ("https://www.instagram.com/tv/TV123/", "TV123"),
            ("https://www.instagram.com/someone/", None),
        ]:
            self.assertEqual(dyi.shortcode_from_url(url), expected, url)


if __name__ == "__main__":
    unittest.main()
