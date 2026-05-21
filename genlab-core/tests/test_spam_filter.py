"""Tests for genlab_core.engagement.spam_filter."""

import unittest

from genlab_core.engagement.spam_filter import is_spam


class TestSpamFilter(unittest.TestCase):
    def test_empty_is_spam(self):
        assert is_spam("") is True
        assert is_spam(" ") is True

    def test_short_is_spam(self):
        assert is_spam("a") is True

    def test_normal_comment_not_spam(self):
        assert is_spam("Great video! Loved the gameplay") is False
        assert is_spam("This is amazing 🔥") is False

    def test_url_from_unknown_domain_is_spam(self):
        assert is_spam("Check out https://scamsite.xyz/free") is True

    def test_url_from_safe_domain_not_spam(self):
        assert is_spam("I saw this on https://youtube.com/watch?v=abc") is False
        assert is_spam("Found it on https://www.instagram.com/p/123") is False

    def test_promo_language_is_spam(self):
        assert is_spam("Check my bio for free stuff") is True
        assert is_spam("DM me for collab") is True
        assert is_spam("Follow me back 4follow") is True

    def test_repeated_chars_is_spam(self):
        assert is_spam("aaaaaaaaa") is True

    def test_giveaway_is_spam(self):
        assert is_spam("Free giveaway! Enter now!") is True

    def test_earn_money_is_spam(self):
        assert is_spam("Earn $500 daily from home") is True

    def test_legitimate_question_not_spam(self):
        assert is_spam("What game is this?") is False
        assert is_spam("Can you do a tutorial on this?") is False


if __name__ == "__main__":
    unittest.main()
