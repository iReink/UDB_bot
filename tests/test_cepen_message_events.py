import unittest
from types import SimpleNamespace

from cepen_message_events import infection_route


class CepenMessageEventTests(unittest.TestCase):
    def test_primary_media_takes_precedence_over_reply_context(self):
        sticker = SimpleNamespace(sticker=object(), video_note=None)
        video_note = SimpleNamespace(sticker=None, video_note=object())

        self.assertEqual(("sticker", None), infection_route(sticker, 123))
        self.assertEqual(("round", None), infection_route(video_note, 123))

    def test_plain_reply_keeps_secondary_route(self):
        message = SimpleNamespace(sticker=None, video_note=None)

        self.assertEqual((None, 123), infection_route(message, 123))
        self.assertEqual((None, None), infection_route(message, None))


if __name__ == "__main__":
    unittest.main()
