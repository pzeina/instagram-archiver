"""Reading cookies: the shapes a user may paste in."""

import unittest
from pathlib import Path
from unittest import mock

from igarchive import session


class CookieBlobs(unittest.TestCase):
    def test_accepts_a_bare_sessionid(self) -> None:
        self.assertEqual(session.parse_cookie_blob("  12345%3AAbC%3A7  "),
                         {"sessionid": "12345%3AAbC%3A7"})

    def test_accepts_a_whole_cookie_header(self) -> None:
        parsed = session.parse_cookie_blob("csrftoken=t1; sessionid=abc%3Ad; ds_user_id=42")
        self.assertEqual(parsed["sessionid"], "abc%3Ad")
        self.assertEqual(parsed["ds_user_id"], "42")

    def test_tolerates_a_trailing_semicolon_and_spaces(self) -> None:
        self.assertEqual(session.parse_cookie_blob(" sessionid=x ; "), {"sessionid": "x"})

    def test_rejects_an_empty_paste(self) -> None:
        with self.assertRaises(session.SessionError):
            session.parse_cookie_blob("   ")

    def test_refuses_cookies_without_a_sessionid(self) -> None:
        with self.assertRaises(session.SessionError) as caught:
            session.open_from_cookies({"csrftoken": "t"}, "moi")
        self.assertIn("sessionid", str(caught.exception))

    def test_names_the_browsers_it_supports(self) -> None:
        self.assertIn("firefox", session.SUPPORTED_BROWSERS)
        with self.assertRaises(session.SessionError):
            session.browser_cookies("navigateur-inexistant")


if __name__ == "__main__":
    unittest.main()


class StatusIsCachedAndHonest(unittest.TestCase):
    """Checking a session costs a network request, and the page refreshes every
    second and a half. Without a cache, showing an indicator sent thousands of
    requests an hour -- the very rate that gets an account limited."""

    def setUp(self) -> None:
        session.invalidate()
        self.calls: list[str] = []

    def tearDown(self) -> None:
        session.invalidate()

    def probe(self, result: dict):
        def fake(username: str) -> dict:
            self.calls.append(username)
            return dict(result)
        return mock.patch.object(session, "_probe", fake)

    def test_a_second_look_within_the_window_asks_nothing(self) -> None:
        valid = {"exists": True, "valid": True, "account": "moi",
                 "reachable": True, "error": None}
        with self.probe(valid):
            session.status("moi")
            session.status("moi")
            session.status("moi")
        self.assertEqual(len(self.calls), 1)

    def test_forcing_asks_again(self) -> None:
        valid = {"exists": True, "valid": True, "account": "moi",
                 "reachable": True, "error": None}
        with self.probe(valid):
            session.status("moi")
            session.status("moi", force=True)
        self.assertEqual(len(self.calls), 2)

    def test_acting_on_a_session_makes_the_next_look_fresh(self) -> None:
        valid = {"exists": True, "valid": True, "account": "moi",
                 "reachable": True, "error": None}
        with self.probe(valid):
            session.status("moi")
            session.invalidate("moi")
            session.status("moi")
        self.assertEqual(len(self.calls), 2)

    def test_an_unreachable_check_is_not_an_expired_session(self) -> None:
        """A missing network, or Instagram limiting the account, proves nothing
        about the session. Confusing the two showed "session expired" to a
        perfectly connected user."""
        unreachable = {"exists": True, "valid": False, "account": None,
                       "reachable": False, "error": "timeout"}
        with self.probe(unreachable):
            result = session.status("moi", recover=False)
        self.assertFalse(result["reachable"])
        self.assertTrue(result["exists"])

    def test_a_session_renewed_in_the_browser_is_picked_up(self) -> None:
        """Signing in again at instagram.com must be enough: the user should not
        have to come back and press a button."""
        expired = {"exists": True, "valid": False, "account": None,
                   "reachable": True, "error": None}
        renewed = {"exists": True, "valid": True, "account": "moi",
                   "reachable": True, "error": None}
        answers = [expired, renewed]
        with mock.patch.object(session, "_probe", lambda u: answers.pop(0)), \
             mock.patch.object(session, "open_auto", lambda u="": ("moi", Path("x"))):
            result = session.status("moi")
        self.assertTrue(result["valid"])
        self.assertEqual(result.get("recovered"), "moi")

    def test_nothing_new_in_the_browser_leaves_the_session_expired(self) -> None:
        expired = {"exists": True, "valid": False, "account": None,
                   "reachable": True, "error": None}
        def refuse(username: str = "") -> None:
            raise session.SessionError("aucune session dans le navigateur")
        with self.probe(expired), mock.patch.object(session, "open_auto", refuse):
            result = session.status("moi")
        self.assertFalse(result["valid"])
        self.assertNotIn("recovered", result)


class ThrottlingIsNotLogout(unittest.TestCase):
    """test_login returns None both for a signed-out session and for a question
    it could not ask. It logs an error only in the second case, and that is the
    sole thing separating them."""

    class FakeContext:
        def __init__(self, errors):
            self.error_log = list(errors)

    class FakeLoader:
        def __init__(self, answer, adds_error):
            self.context = ThrottlingIsNotLogout.FakeContext([])
            self._answer, self._adds = answer, adds_error

        def test_login(self):
            if self._adds:
                self.context.error_log.append(
                    "Error when checking if logged in: 400 Bad Request - "
                    '"fail" status, message "feedback_required"')
            return self._answer

    def probe_with(self, loader, tmp):
        with mock.patch.object(session.paths, "sessions_dir", lambda: tmp), \
             mock.patch.object(session, "load", lambda u: loader):
            return session._probe("moi")

    def test_a_throttled_account_is_not_reported_as_logged_out(self) -> None:
        """Instagram answers "feedback_required" to an account it is limiting.
        The session stays valid -- downloads keep going through."""
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "moi.session").write_text("x", encoding="utf-8")
            result = self.probe_with(self.FakeLoader(None, adds_error=True), root)
        self.assertFalse(result["reachable"])
        self.assertTrue(result["exists"])

    def test_a_real_logout_is_reported_as_such(self) -> None:
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "moi.session").write_text("x", encoding="utf-8")
            result = self.probe_with(self.FakeLoader(None, adds_error=False), root)
        self.assertTrue(result["reachable"])
        self.assertFalse(result["valid"])

    def test_a_working_session_is_recognised(self) -> None:
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "moi.session").write_text("x", encoding="utf-8")
            result = self.probe_with(self.FakeLoader("moi", adds_error=False), root)
        self.assertTrue(result["valid"])
        self.assertEqual(result["account"], "moi")


class ThrottlingIsHandledGently(unittest.TestCase):
    def setUp(self) -> None:
        session.invalidate()

    def tearDown(self) -> None:
        session.invalidate()

    def test_a_throttled_account_is_asked_far_less_often(self) -> None:
        """Polling an account Instagram is limiting can only sustain the limit:
        the interval must be markedly longer."""
        self.assertGreater(session.UNREACHABLE_TTL, session.STATUS_TTL * 4)

    def test_instaloader_is_prevented_from_writing_to_the_terminal(self) -> None:
        """Its error() method prints regardless of "quiet": a limited account
        filled the terminal with the same message, thousands of times."""
        class FakeContext:
            def __init__(self):
                self.error_log = []
                self.printed = []
            def error(self, msg, repeat_at_end=True):
                self.printed.append(msg)

        class FakeLoader:
            def __init__(self):
                self.context = FakeContext()

        loader = session._hush(FakeLoader())
        loader.context.error("400 feedback_required")
        self.assertEqual(loader.context.printed, [])
        self.assertEqual(loader.context.error_log, ["400 feedback_required"])
