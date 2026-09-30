"""Configuration : persistance, tolerance aux fichiers d'une autre version."""

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from igarchive import config as config_module


class ConfigRoundTrip(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = TemporaryDirectory()
        self.path = Path(self.tmp.name) / "config.json"

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_survives_a_save_and_reload(self) -> None:
        original = config_module.Config(username="moi", sleep_min=4.5, download_comments=True)
        original.save(self.path)
        self.assertEqual(config_module.load(self.path), original)

    def test_ignores_keys_it_does_not_know(self) -> None:
        """Un fichier ecrit par une version ulterieure ne doit pas bloquer le demarrage."""
        self.path.write_text(json.dumps({"username": "moi", "reglage_futur": 42}),
                             encoding="utf-8")
        self.assertEqual(config_module.load(self.path).username, "moi")

    def test_falls_back_to_defaults_on_a_corrupt_file(self) -> None:
        self.path.write_text("{ ceci n'est pas du JSON", encoding="utf-8")
        self.assertEqual(config_module.load(self.path), config_module.Config())

    def test_missing_file_gives_defaults(self) -> None:
        self.assertEqual(config_module.load(self.path), config_module.Config())


class ConfigValidation(unittest.TestCase):
    def test_reports_a_missing_account(self) -> None:
        self.assertTrue(any("compte" in p.lower() for p in config_module.Config().problems()))

    def test_refuses_a_dangerously_short_pause(self) -> None:
        problems = config_module.Config(username="x", sleep_min=0.1).problems()
        self.assertTrue(any("pause minimale" in p for p in problems))

    def test_refuses_an_inverted_pause_range(self) -> None:
        problems = config_module.Config(username="x", sleep_min=9, sleep_max=2).problems()
        self.assertTrue(any("inferieure" in p for p in problems))

    def test_derived_paths_sit_under_the_archive(self) -> None:
        cfg = config_module.Config(archive_dir="/tmp/ig-test")
        for path in (cfg.media_dir, cfg.metadata_dir, cfg.state_dir, cfg.ledger_file):
            self.assertTrue(str(path).startswith("/tmp/ig-test"), path)


if __name__ == "__main__":
    unittest.main()


class ProblemsPointAtTheRightStep(unittest.TestCase):
    """L'interface signale chaque probleme a l'endroit ou il se corrige."""

    def test_a_missing_account_belongs_to_the_account_step(self) -> None:
        detail = config_module.Config().problems_detail()
        self.assertTrue(all(issue["step"] == 1 for issue in detail), detail)

    def test_a_bad_pause_belongs_to_the_settings_step(self) -> None:
        detail = config_module.Config(username="x", sleep_min=0.1).problems_detail()
        pauses = [i for i in detail if "pause" in i["text"]]
        self.assertTrue(pauses and all(i["step"] == 2 for i in pauses))

    def test_settings_are_clean_when_only_the_account_is_missing(self) -> None:
        """Rien dans les reglages n'est fautif parce qu'aucun compte n'est saisi."""
        detail = config_module.Config().problems_detail()
        self.assertEqual([i for i in detail if i["step"] == 2], [])

    def test_the_plain_list_still_reads_the_same(self) -> None:
        cfg = config_module.Config(username="x", sleep_min=9, sleep_max=2)
        self.assertEqual(cfg.problems(), [i["text"] for i in cfg.problems_detail()])


class LanguageDefaultsToEnglish(unittest.TestCase):
    def test_a_fresh_install_speaks_english(self) -> None:
        from igarchive import i18n
        self.assertEqual(config_module.Config().language, i18n.DEFAULT_LANGUAGE)
        self.assertEqual(i18n.DEFAULT_LANGUAGE, "en")

    def test_every_string_exists_in_both_languages(self) -> None:
        """Une traduction manquante doit tomber au test, pas devant l'utilisateur."""
        from igarchive import i18n
        incomplete = {key for key, entry in i18n.STRINGS.items()
                      if set(entry) != set(i18n.LANGUAGES)}
        self.assertEqual(incomplete, set())

    def test_an_unsupported_language_falls_back_instead_of_breaking(self) -> None:
        from igarchive import i18n
        self.assertEqual(i18n.normalise("de"), "en")
        self.assertEqual(i18n.normalise(None), "en")
        self.assertEqual(i18n.normalise("FR"), "fr")

    def test_an_unknown_key_is_visible_rather_than_silent(self) -> None:
        from igarchive import i18n
        self.assertEqual(i18n.t("pas_une_cle"), "[pas_une_cle]")
