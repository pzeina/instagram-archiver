"""The catalogue: ordering, escaping, media wiring, CSV robustness."""

import csv
import json
import re
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from igarchive import catalog, i18n
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
        # Newlines are flattened to keep one row per item.
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
        self.assertIn(i18n.t("cat_no_media", "en"),
                      (self.archive / "index.html").read_text(encoding="utf-8"))

    def test_html_is_valid_with_an_empty_archive(self) -> None:
        catalog.write_html_catalog(self.archive, [])
        page = (self.archive / "index.html").read_text(encoding="utf-8")
        self.assertIn(i18n.t("cat_empty", "en"), page)

    def test_saved_dates_are_applied_to_records_written_earlier(self) -> None:
        """The export arrives later: records already written must benefit from it."""
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
        """A record written by a later version stays readable."""
        item = Record.from_dict({"shortcode": "X", "champ_futur": 1})
        self.assertEqual(item.shortcode, "X")


if __name__ == "__main__":
    unittest.main()


class CollectionFolders(unittest.TestCase):
    """The collection tree, mirroring the library as the app shows it."""

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
        """A reel filed under three collections must take the space of one."""
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
        """Cleanup removes links only. A file the user dropped there, or a
        media file, must survive any rebuild."""
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
    """What the page shows depending on whether the save date is known."""

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
        """The promise is checked -- the exact date appears, the rank goes --
        not the markup around it, which is free to change."""
        page = self.page(record("X", saved_at="2026-09-25T14:30:00+00:00",
                                saved_timestamp=1790000000))
        self.assertIn("2026-09-25", page)
        self.assertIn(i18n.t("cat_saved_on", "en"), page)
        self.assertNotIn("in the list", page)

    def test_explains_itself_when_the_date_is_missing(self) -> None:
        """"rank 3" teaches nothing: the page must say where the date comes from."""
        page = self.page(record("X", saved_rank=2))
        self.assertIn(i18n.t("cat_rank_value", "en", position="3rd"), page)
        self.assertIn("official export", page)

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


class ThePageFollowsTheChosenLanguage(unittest.TestCase):
    """The catalogue is a standalone file: its language must be fixed when it is
    built, not guessed by whichever browser opens it."""

    def setUp(self) -> None:
        self.tmp = TemporaryDirectory()
        self.archive = Path(self.tmp.name)
        self.meta = self.archive / "metadata"
        self.meta.mkdir()
        catalog.write_record(self.meta, record("X", saved_rank=0))

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def page(self, lang: str) -> str:
        catalog.build(self.archive, self.meta, None, lang)
        return (self.archive / "index.html").read_text(encoding="utf-8")

    def test_english_is_what_a_default_build_produces(self) -> None:
        page = self.page(i18n.DEFAULT_LANGUAGE)
        self.assertIn('<html lang="en"', page)
        self.assertIn(i18n.t("cat_title", "en"), page)

    def test_french_is_produced_on_request(self) -> None:
        page = self.page("fr")
        self.assertIn('<html lang="fr"', page)
        self.assertIn(i18n.t("cat_title", "fr"), page)
        self.assertNotIn(i18n.t("cat_search", "en"), page)

    def test_the_ordinal_reads_naturally_in_each_language(self) -> None:
        self.assertIn("1st in the list", self.page("en"))
        self.assertIn("1er de la liste", self.page("fr"))


class NoLanguageLeaksIntoThePage(unittest.TestCase):
    """A string missed during translation is invisible on review: the page stays
    correct and a single word changes language. This test finds it."""

    def setUp(self) -> None:
        self.tmp = TemporaryDirectory()
        self.archive = Path(self.tmp.name)
        self.meta = self.archive / "metadata"
        self.meta.mkdir()
        catalog.write_record(self.meta, record(
            "X", kind="reel", author="someone", saved_rank=2,
            caption="a caption", hashtags=["tag"], collections=["Pottery"],
            files=["media/2026/x/clip.mp4"]))
        catalog.write_record(self.meta, record(
            "Y", saved_at="2026-09-25T10:00:00+00:00", saved_timestamp=1790000000))

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def page(self, lang: str) -> str:
        catalog.build(self.archive, self.meta, None, lang)
        return (self.archive / "index.html").read_text(encoding="utf-8")

    @staticmethod
    def visible(page: str) -> str:
        """The markup without its CSS or its JavaScript.

        Searching a short word across the whole document gives false positives:
        "all" occurs inside "querySelectorAll". Only rendered text counts.
        """
        return re.sub(r"<(style|script)\b.*?</\1>", "", page, flags=re.S | re.I)

    def _leaks(self, rendered: str, other: str) -> list[str]:
        """Strings belonging to the other language found in the rendered text."""
        body = self.visible(self.page(rendered))
        found = []
        for key, entry in i18n.STRINGS.items():
            if not key.startswith("cat_"):
                continue
            mine, theirs = entry[rendered], entry[other]
            if mine == theirs or "{" in theirs:
                continue          # identiques, ou gabarit a trous
            if theirs in body:
                found.append(key)
        return found

    def test_the_english_page_holds_no_french(self) -> None:
        self.assertEqual(self._leaks("en", "fr"), [])

    def test_the_french_page_holds_no_english(self) -> None:
        self.assertEqual(self._leaks("fr", "en"), [])

    def test_no_stray_html_entity_from_the_french_original(self) -> None:
        """"publi&eacute;" had stayed hardcoded: no accented entity should
        survive in the English page."""
        page = self.visible(self.page("en"))
        for entity in ("&eacute;", "&egrave;", "&agrave;", "&ccedil;", "&icirc;"):
            self.assertNotIn(entity, page, entity)
