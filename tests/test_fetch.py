"""Walking the saved library, and failing in a way the user can act on."""

import unittest

from instaloader.exceptions import (
    AbortDownloadException,
    ConnectionException,
    InstaloaderException,
    TooManyRequestsException,
)

from igarchive import fetch, i18n
from igarchive.fetch import Progress


class ThrottlingIsRecognised(unittest.TestCase):
    def test_instagram_s_rate_limit_wording_is_recognised(self) -> None:
        """"feedback_required" is what Instagram answers to an account it has
        flagged; it arrives as plain text inside an exception, not as a type."""
        for message in ('400 Bad Request - "fail" status, message "feedback_required"',
                        "429 Too Many Requests",
                        "Please wait a few minutes before you try again."):
            self.assertTrue(fetch.is_throttled(AbortDownloadException(message)), message)

    def test_the_dedicated_exception_counts_too(self) -> None:
        self.assertTrue(fetch.is_throttled(TooManyRequestsException("slow down")))

    def test_an_ordinary_failure_is_not_mistaken_for_a_rate_limit(self) -> None:
        self.assertFalse(fetch.is_throttled(OSError("no space left on device")))
        self.assertFalse(fetch.is_throttled(ConnectionException("name resolution failed")))


class TheExceptionHierarchyTrap(unittest.TestCase):
    """AbortDownloadException descends from Exception, not from
    InstaloaderException, so catching the latter silently misses it. That is why
    the error reached the screen as a raw string instead of a readable message."""

    def test_it_really_does_not_descend_from_instaloader_s_base(self) -> None:
        self.assertFalse(issubclass(AbortDownloadException, InstaloaderException))

    def test_so_it_is_named_explicitly_among_the_handled_errors(self) -> None:
        self.assertIn(AbortDownloadException, fetch.WALK_ERRORS)
        self.assertIn(InstaloaderException, fetch.WALK_ERRORS)


class WalkingStopsReadably(unittest.TestCase):
    """Paginating the saved library is itself a request, so it can be refused
    halfway through. Unguarded, that escaped as instaloader's own wording."""

    @staticmethod
    def failing_after(count: int, error: BaseException):
        def generator():
            for index in range(count):
                yield f"post-{index}"
            raise error
        return generator()

    def test_a_rate_limit_mid_walk_becomes_an_actionable_message(self) -> None:
        progress = Progress()
        error = AbortDownloadException('400 - message "feedback_required"')
        seen = list(fetch.walk_saved(self.failing_after(3, error), progress))
        self.assertEqual(len(seen), 3)
        self.assertTrue(progress.rate_limited)
        self.assertEqual(progress.error, i18n.t("rate_limited", "en"))
        self.assertNotIn("AbortDownloadException", progress.error)

    def test_another_failure_is_reported_without_losing_what_was_walked(self) -> None:
        progress = Progress()
        seen = list(fetch.walk_saved(self.failing_after(2, ConnectionException("down")),
                                     progress))
        self.assertEqual(len(seen), 2)
        self.assertFalse(progress.rate_limited)
        self.assertIn("down", progress.error)

    def test_a_complete_walk_reports_nothing(self) -> None:
        progress = Progress()
        seen = list(fetch.walk_saved(iter(["a", "b"]), progress))
        self.assertEqual(seen, ["a", "b"])
        self.assertIsNone(progress.error)


if __name__ == "__main__":
    unittest.main()
