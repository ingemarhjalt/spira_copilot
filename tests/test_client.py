import io
import unittest
import urllib.error
from unittest import mock

from client import Settings, SpiraClient, SpiraError


def make_client():
    return SpiraClient(Settings("https://x.example/spira", 1, "u", "{KEY}"))


class RequestTests(unittest.TestCase):
    def test_get_sends_content_type_and_400_reports_detail(self):
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
        self.assertEqual(seen["headers"]["Content-type"], "application/json")
        msg = str(ctx.exception)
        self.assertIn("/spira/Services/v7_0/RestService.svc/projects/1/test-sets (query: sort_field)", msg)
        self.assertIn("Invalid sort field", msg)


    def test_use_proxy_false_ignores_environment_proxy(self):
        def proxied(use_proxy):
            s = Settings("https://x.example/spira", 1, "u", "{KEY}", use_proxy=use_proxy)
            with mock.patch.dict("os.environ", {"HTTPS_PROXY": "http://proxy.example:8080"}):
                client = SpiraClient(s)
            return any(h.__class__.__name__ == "ProxyHandler" for h in client.opener.handlers)

        self.assertTrue(proxied(True))
        self.assertFalse(proxied(False))


if __name__ == "__main__":
    unittest.main()
