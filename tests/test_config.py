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
