"""Le serveur de l'interface, demarre pour de vrai sur un port ephemere.

Ces tests ne touchent jamais Instagram : ils verifient l'aiguillage, le controle
du jeton et le traitement des erreurs, c'est-a-dire tout ce qui se passe avant
qu'une requete parte sur le reseau.
"""

import json
import os
import threading
import unittest
import urllib.error
import urllib.request
from functools import partial
from http.server import ThreadingHTTPServer
from pathlib import Path
from tempfile import TemporaryDirectory

from igarchive import i18n, webui


class ServerCase(unittest.TestCase):
    def setUp(self) -> None:
        # Toute la configuration part dans un dossier jetable : aucun reglage
        # reel de la machine n'est lu ni ecrit par les tests.
        self.tmp = TemporaryDirectory()
        self._old_xdg = os.environ.get("XDG_CONFIG_HOME")
        os.environ["XDG_CONFIG_HOME"] = self.tmp.name

        self.state = webui.AppState()
        self.state.config.archive_dir = str(Path(self.tmp.name) / "archive")
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0),
                                         partial(webui.Handler, state=self.state))
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=5)
        if self._old_xdg is None:
            os.environ.pop("XDG_CONFIG_HOME", None)
        else:
            os.environ["XDG_CONFIG_HOME"] = self._old_xdg
        self.tmp.cleanup()

    def call(self, path: str, body=None, token: str | None = "valid"):
        url = f"http://127.0.0.1:{self.port}{path}"
        data = None if body is None else json.dumps(body).encode()
        request = urllib.request.Request(url, data=data,
                                         method="GET" if body is None else "POST")
        if token == "valid":
            request.add_header("X-IG-Token", self.state.token)
        elif token is not None:
            request.add_header("X-IG-Token", token)
        if data is not None:
            request.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                return response.status, json.loads(response.read())
        except urllib.error.HTTPError as error:
            payload = error.read()
            try:
                return error.code, json.loads(payload)
            except json.JSONDecodeError:
                return error.code, {"raw": payload[:200].decode("utf-8", "replace")}


class TokenProtection(ServerCase):
    def test_the_api_refuses_a_request_without_a_token(self) -> None:
        status, _ = self.call("/api/state", token=None)
        self.assertEqual(status, 403)

    def test_the_api_refuses_a_wrong_token(self) -> None:
        status, _ = self.call("/api/state", token="pas-le-bon")
        self.assertEqual(status, 403)

    def test_a_post_also_requires_the_token(self) -> None:
        status, _ = self.call("/api/config", {"username": "intrus"}, token=None)
        self.assertEqual(status, 403)

    def test_the_page_itself_is_served_without_a_token(self) -> None:
        """La page porte le jeton : elle doit donc etre lisible sans lui."""
        with urllib.request.urlopen(f"http://127.0.0.1:{self.port}/", timeout=10) as response:
            page = response.read().decode("utf-8")
        self.assertEqual(response.status, 200)
        self.assertIn(self.state.token, page)
        self.assertNotIn("__TOKEN__", page)


class StateAndConfig(ServerCase):
    def test_state_describes_everything_the_interface_needs(self) -> None:
        status, payload = self.call("/api/state")
        self.assertEqual(status, 200)
        for key in ("version", "platform", "config", "browsers", "session",
                    "summary", "problems", "job"):
            self.assertIn(key, payload)

    def test_saving_a_setting_keeps_it(self) -> None:
        status, payload = self.call("/api/config", {"username": "moi", "sleep_min": "4.5"})
        self.assertEqual(status, 200)
        self.assertEqual(payload["config"]["username"], "moi")
        self.assertEqual(payload["config"]["sleep_min"], 4.5)   # converti depuis le texte
        _, state = self.call("/api/state")
        self.assertEqual(state["config"]["username"], "moi")

    def test_a_blank_number_does_not_wipe_an_existing_setting(self) -> None:
        self.call("/api/config", {"sleep_max": "9"})
        self.call("/api/config", {"sleep_max": ""})
        _, state = self.call("/api/state")
        self.assertEqual(state["config"]["sleep_max"], 9.0)

    def test_unknown_settings_are_ignored(self) -> None:
        status, payload = self.call("/api/config", {"reglage_invente": 1})
        self.assertEqual(status, 200)
        self.assertNotIn("reglage_invente", payload["config"])

    def test_a_tilde_in_the_destination_is_expanded(self) -> None:
        _, payload = self.call("/api/config", {"archive_dir": "~/ArchiveTest"})
        self.assertFalse(payload["config"]["archive_dir"].startswith("~"))


class ErrorHandling(ServerCase):
    def test_an_unknown_route_answers_404(self) -> None:
        self.assertEqual(self.call("/api/inexistant", {})[0], 404)
        self.assertEqual(self.call("/inexistant")[0], 404)

    def test_an_unreadable_body_answers_400(self) -> None:
        url = f"http://127.0.0.1:{self.port}/api/config"
        request = urllib.request.Request(url, data=b"{ pas du json", method="POST")
        request.add_header("X-IG-Token", self.state.token)
        try:
            urllib.request.urlopen(request, timeout=10)
            self.fail("une requete illisible devrait etre refusee")
        except urllib.error.HTTPError as error:
            self.assertEqual(error.code, 400)

    def test_starting_without_a_session_explains_why(self) -> None:
        """On verifie le message employe, pas sa formulation : elle change avec
        la langue et il n'y a rien a apprendre d'une chaine figee."""
        status, payload = self.call("/api/fetch/start", {})
        self.assertEqual(status, 400)
        self.assertEqual(payload["error"], i18n.t("connect_first", "en"))

    def test_reading_an_absent_export_explains_why(self) -> None:
        status, payload = self.call("/api/dyi", {"path": "/introuvable/export.zip"})
        self.assertEqual(status, 400)
        self.assertTrue(payload["error"])

    def test_opening_something_that_does_not_exist_explains_why(self) -> None:
        status, payload = self.call("/api/open", {"what": "html"})
        self.assertEqual(status, 400)
        expected = i18n.t("nothing_to_open", "en",
                          path=Path(self.state.config.archive_dir) / "index.html")
        self.assertEqual(payload["error"], expected)

    def test_cancelling_with_no_job_running_is_not_an_error(self) -> None:
        status, payload = self.call("/api/fetch/cancel", {})
        self.assertEqual(status, 200)
        self.assertEqual(payload["message"], i18n.t("nothing_running", "en"))


class Catalogue(ServerCase):
    def test_building_an_empty_catalogue_succeeds(self) -> None:
        status, payload = self.call("/api/catalog", {})
        self.assertEqual(status, 200)
        self.assertEqual(payload["count"], 0)
        self.assertTrue(Path(payload["html"]).exists())


if __name__ == "__main__":
    unittest.main()


class DownloadTouchesNothingOutsideTheArchive(ServerCase):
    """Un telechargement ne doit lire aucun dossier du systeme.

    Parcourir « Telechargements », « Bureau » ou le dossier personnel fait
    surgir une demande d'autorisation macOS au milieu d'un telechargement,
    attribuee a l'application qui a lance le programme. Rien dans un archivage
    ne justifie d'aller regarder la.
    """

    def test_the_download_path_never_searches_the_home_directory(self) -> None:
        from unittest import mock

        from igarchive import dyi

        seen: list[dict] = []
        real = dyi.find_exports

        def spy(extra_dirs=None, *, search_home=True):
            seen.append({"dirs": [str(d) for d in (extra_dirs or [])],
                         "home": search_home})
            return real(extra_dirs, search_home=search_home)

        with mock.patch.object(dyi, "find_exports", spy):
            self.call("/api/fetch/start", {})      # refuse : aucune session
        self.assertTrue(all(not call["home"] for call in seen), seen)

    def test_an_explicit_command_may_still_search_the_usual_folders(self) -> None:
        """L'utilisateur qui tape « igarchive dyi » demande justement cela."""
        from igarchive import dyi
        import inspect

        signature = inspect.signature(dyi.find_exports)
        self.assertIs(signature.parameters["search_home"].default, True)


class LaunchingTwiceShowsTheOpenWindow(ServerCase):
    """Relancer le programme alors qu'il tourne deja est le cas le plus frequent
    d'un port occupe. Refuser de demarrer laisse l'utilisateur devant une erreur
    au lieu de la fenetre qu'il cherchait."""

    def test_an_igarchive_already_listening_is_recognised(self) -> None:
        self.assertTrue(webui.already_running(self.port))

    def test_a_free_port_is_not_mistaken_for_one(self) -> None:
        import socket
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            free = probe.getsockname()[1]
        self.assertFalse(webui.already_running(free))

    def test_another_program_on_the_port_is_not_mistaken_for_igarchive(self) -> None:
        import socket
        import threading

        server = socket.socket()
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server.bind(("127.0.0.1", 0))
        server.listen(1)
        port = server.getsockname()[1]

        def answer() -> None:
            try:
                client, _ = server.accept()
                client.recv(1024)
                client.sendall(b"HTTP/1.1 200 OK\r\nServer: autre-chose/1\r\n"
                               b"Content-Length: 0\r\n\r\n")
                client.close()
            except OSError:
                pass

        thread = threading.Thread(target=answer, daemon=True)
        thread.start()
        try:
            self.assertFalse(webui.already_running(port))
        finally:
            server.close()
            thread.join(timeout=3)
