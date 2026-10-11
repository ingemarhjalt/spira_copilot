import io
import unittest
import urllib.error
from unittest import mock

from client import Settings, SpiraClient, SpiraError


def make_client():
    return SpiraClient(Settings("https://x.example/spira", 1, "u", "{KEY}"))


class RequestTests(unittest.TestCase):
    def test_get_has_no_content_type_and_400_reports_detail(self):
        client = make_client()
        seen = {}

        def fake_open(req, timeout):
            seen["headers"] = dict(req.header_items())
            raise urllib.error.HTTPError(
                req.full_url, 400, "Bad Request", {},
                io.BytesIO(b'{"Message": "Invalid sort field"}'))

        client.opener = mock.Mock(open=fake_open)
        with self.assertRaises(SpiraError) as ctx:
            client.request("/test-sets", {"sort_field": "Nope"})
        self.assertNotIn("Content-type", seen["headers"])
        msg = str(ctx.exception)
        self.assertIn("RestService.svc/projects/1/test-sets (query: sort_field)", msg)
        self.assertIn("Invalid sort field", msg)


if __name__ == "__main__":
    unittest.main()
