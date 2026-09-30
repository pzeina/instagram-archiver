"""Lecture des cookies : les formats que l'utilisateur peut coller."""

import unittest

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
