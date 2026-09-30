"""Portabilite : la resolution des chemins doit suivre le systeme.

Les tests forcent la plateforme, de sorte que le comportement Linux est verifie
depuis macOS et reciproquement. Ils ne remplacent pas un essai reel sur chaque
systeme, mais ils figent la logique de choix.
"""

import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from igarchive import paths


class ConfigLocation(unittest.TestCase):
    def test_honours_xdg_config_home_when_set(self) -> None:
        with mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": "/tmp/xdg-essai"}):
            self.assertEqual(paths.config_dir(), Path("/tmp/xdg-essai/igarchive"))

    def test_falls_back_to_dot_config_in_the_home_directory(self) -> None:
        env = {k: v for k, v in os.environ.items() if k != "XDG_CONFIG_HOME"}
        with mock.patch.dict(os.environ, env, clear=True):
            self.assertEqual(paths.config_dir(), Path.home() / ".config/igarchive")

    def test_sessions_live_beside_the_configuration_not_in_the_archive(self) -> None:
        """Le jeton vaut un mot de passe : il ne doit pas suivre l'archive si
        elle est copiee sur un disque externe ou dans un nuage."""
        self.assertEqual(paths.sessions_dir().parent, paths.config_dir())
        self.assertNotIn(str(paths.default_archive_dir()), str(paths.sessions_dir()))


class FirefoxProfiles(unittest.TestCase):
    def test_looks_in_the_macos_location_on_macos(self) -> None:
        with TemporaryDirectory() as tmp:
            home = Path(tmp)
            (home / "Library/Application Support/Firefox/Profiles").mkdir(parents=True)
            with mock.patch.object(paths.sys, "platform", "darwin"), \
                 mock.patch.object(Path, "home", staticmethod(lambda: home)):
                roots = paths.firefox_profile_roots()
        self.assertEqual(len(roots), 1)
        self.assertIn("Library/Application Support", str(roots[0]))

    def test_covers_plain_snap_and_flatpak_installs_on_linux(self) -> None:
        """Sous Linux, Firefox est installe de trois facons incompatibles ;
        chacune range ses profils ailleurs."""
        with TemporaryDirectory() as tmp:
            home = Path(tmp)
            for relative in (".mozilla/firefox",
                             "snap/firefox/common/.mozilla/firefox",
                             ".var/app/org.mozilla.firefox/.mozilla/firefox"):
                (home / relative).mkdir(parents=True)
            with mock.patch.object(paths.sys, "platform", "linux"), \
                 mock.patch.object(Path, "home", staticmethod(lambda: home)):
                roots = paths.firefox_profile_roots()
        self.assertEqual(len(roots), 3)

    def test_returns_nothing_when_firefox_is_absent(self) -> None:
        with TemporaryDirectory() as tmp:
            home = Path(tmp)
            with mock.patch.object(paths.sys, "platform", "linux"), \
                 mock.patch.object(Path, "home", staticmethod(lambda: home)):
                self.assertEqual(paths.firefox_profile_roots(), [])


class Opening(unittest.TestCase):
    def test_uses_open_on_macos(self) -> None:
        with mock.patch.object(paths.sys, "platform", "darwin"), \
             mock.patch.object(paths.subprocess, "Popen") as popen:
            self.assertTrue(paths.open_in_browser("http://x"))
        self.assertEqual(popen.call_args[0][0][0], "open")

    def test_uses_xdg_open_on_linux(self) -> None:
        with mock.patch.object(paths.sys, "platform", "linux"), \
             mock.patch.object(paths.shutil, "which", lambda name: "/usr/bin/xdg-open"), \
             mock.patch.object(paths.subprocess, "Popen") as popen:
            self.assertTrue(paths.open_in_browser("http://x"))
        self.assertEqual(popen.call_args[0][0][0], "/usr/bin/xdg-open")

    def test_reports_failure_rather_than_raising_when_no_opener_exists(self) -> None:
        """Un serveur Linux sans environnement graphique n'a pas xdg-open :
        l'outil doit afficher l'adresse, pas planter."""
        with mock.patch.object(paths.sys, "platform", "linux"), \
             mock.patch.object(paths.shutil, "which", lambda name: None):
            self.assertFalse(paths.open_in_browser("http://x"))


class HumanSizes(unittest.TestCase):
    def test_reads_the_way_a_person_expects(self) -> None:
        for value, expected in [(0, "0 o"), (999, "999 o"), (15_600_000, "14.9 Mo"),
                                (2.1e10, "19.6 Go")]:
            self.assertEqual(paths.human_bytes(value), expected)


if __name__ == "__main__":
    unittest.main()
