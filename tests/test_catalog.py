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


class CollectionFolders(unittest.TestCase):
    """L'arborescence des collections, qui reproduit la bibliotheque de l'application."""

    def setUp(self) -> None:
        self.tmp = TemporaryDirectory()
        self.archive = Path(self.tmp.name)
        self.meta = self.archive / "metadata"
        self.meta.mkdir()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def media(self, name: str) -> str:
        relative = f"media/2026/{name}"
        (self.archive / relative).mkdir(parents=True, exist_ok=True)
        (self.archive / relative / "clip.mp4").write_bytes(b"x" * 10)
        return relative

    def test_creates_one_folder_per_collection(self) -> None:
        records = [
            {"shortcode": "A", "directory": self.media("a"), "collections": ["Cuisine"]},
            {"shortcode": "B", "directory": self.media("b"), "collections": ["Voyage"]},
        ]
        result = catalog.build_collection_links(self.archive, records)
        self.assertEqual(result["collections"], {"Cuisine": 1, "Voyage": 1})
        self.assertTrue((self.archive / "collections/Cuisine/a").is_symlink())

    def test_links_instead_of_copying(self) -> None:
        """Un reel range dans trois collections ne doit occuper la place qu'une fois."""
        records = [{"shortcode": "A", "directory": self.media("a"),
                    "collections": ["Un", "Deux", "Trois"]}]
        catalog.build_collection_links(self.archive, records)
        links = list((self.archive / "collections").rglob("a"))
        self.assertEqual(len(links), 3)
        self.assertTrue(all(link.is_symlink() for link in links))

    def test_links_are_relative_so_the_archive_stays_movable(self) -> None:
        records = [{"shortcode": "A", "directory": self.media("a"), "collections": ["Cuisine"]}]
        catalog.build_collection_links(self.archive, records)
        target = (self.archive / "collections/Cuisine/a").readlink()
        self.assertFalse(target.is_absolute())
        self.assertTrue((self.archive / "collections/Cuisine/a").resolve().is_dir())

    def test_a_content_without_a_collection_is_still_reachable(self) -> None:
        records = [{"shortcode": "A", "directory": self.media("a")}]
        result = catalog.build_collection_links(self.archive, records)
        self.assertEqual(result["collections"], {catalog.UNSORTED_NAME: 1})

    def test_a_collection_removed_in_the_app_disappears_here(self) -> None:
        first = [{"shortcode": "A", "directory": self.media("a"), "collections": ["Ancienne"]}]
        catalog.build_collection_links(self.archive, first)
        second = [{"shortcode": "A", "directory": "media/2026/a", "collections": ["Nouvelle"]}]
        catalog.build_collection_links(self.archive, second)
        self.assertFalse((self.archive / "collections/Ancienne").exists())
        self.assertTrue((self.archive / "collections/Nouvelle/a").is_symlink())

    def test_the_cleanup_never_deletes_a_real_file(self) -> None:
        """Le nettoyage ne retire que des liens. Un fichier depose la par
        l'utilisateur, ou un media, doit survivre a toute reconstruction."""
        intruder = self.archive / "collections/Cuisine"
        intruder.mkdir(parents=True)
        (intruder / "mes-notes.txt").write_text("a garder", encoding="utf-8")
        records = [{"shortcode": "A", "directory": self.media("a"), "collections": ["Cuisine"]}]

        catalog.build_collection_links(self.archive, records)

        self.assertTrue((intruder / "mes-notes.txt").exists())
        self.assertEqual((intruder / "mes-notes.txt").read_text(encoding="utf-8"), "a garder")
        self.assertTrue((self.archive / "media/2026/a/clip.mp4").exists())

    def test_a_slash_in_a_collection_name_does_not_create_a_subfolder(self) -> None:
        records = [{"shortcode": "A", "directory": self.media("a"),
                    "collections": ["Recettes/Desserts"]}]
        catalog.build_collection_links(self.archive, records)
        folders = [p.name for p in (self.archive / "collections").iterdir()]
        self.assertEqual(folders, ["Recettes-Desserts"])


class SavedDateDisplay(unittest.TestCase):
    """Ce que la page affiche selon que la date d'enregistrement est connue ou non."""

    def setUp(self) -> None:
        self.tmp = TemporaryDirectory()
        self.archive = Path(self.tmp.name)
        self.meta = self.archive / "metadata"
        self.meta.mkdir()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def page(self, *records: Record) -> str:
        for item in records:
            catalog.write_record(self.meta, item)
        catalog.write_html_catalog(self.archive, catalog.load_records(self.meta))
        return (self.archive / "index.html").read_text(encoding="utf-8")

    def test_shows_the_exact_date_once_it_is_known(self) -> None:
        """On verifie la promesse -- la date exacte apparait, l'ordre disparait --
        et non la facon dont elle est balisee, qui peut changer."""
        page = self.page(record("X", saved_at="2026-09-25T14:30:00+00:00",
                                saved_timestamp=1790000000))
        self.assertIn("2026-09-25", page)
        self.assertIn("enregistr&eacute; le", page)
        self.assertNotIn("de la liste", page)

    def test_explains_itself_when_the_date_is_missing(self) -> None:
        """« rang 3 » n'apprend rien : la page doit dire d'ou vient la date."""
        page = self.page(record("X", saved_rank=2))
        self.assertIn("3e de la liste", page)
        self.assertIn("export officiel", page)

    def test_warns_only_while_dates_are_missing(self) -> None:
        self.assertIn('class="banner"', self.page(record("X", saved_rank=0)))

    def test_no_warning_once_every_date_is_known(self) -> None:
        page = self.page(record("X", saved_at="2026-09-25T14:30:00+00:00",
                                saved_timestamp=1790000000))
        self.assertNotIn('class="banner"', page)

    def test_a_content_can_be_filtered_by_any_of_its_collections(self) -> None:
        page = self.page(record("X", collections=["Cuisine", "Voyage"]))
        self.assertIn('data-collections="|Cuisine|Voyage|"', page)
        for name in ("Cuisine", "Voyage"):
            self.assertIn(f'<option value="{name}">', page)
