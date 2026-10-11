"""Read-only Spira v7 JSON extraction. No dashboard-specific rules."""

import configparser
import html
import json
import os
import re
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path


class SpiraError(Exception):
    """An explicit connection, response, or report-contract error."""


@dataclass(frozen=True)
class Settings:
    base_url: str
    project_id: int
    username: str = field(repr=False)
    api_key: str = field(repr=False)
    ca_bundle: str | None = None
    page_size: int = 250
    verify_ssl: bool = True
    api_version: str = "v7_0"

    @classmethod
    def load(cls, path: str):
        cfg = configparser.ConfigParser(interpolation=None)
        if not cfg.read(path, encoding="utf-8"):
            raise SpiraError(f"Configuration not found: {path}")
        if not cfg.has_section("api"):
            raise SpiraError("Configuration needs an uncommented [api] section.")
        try:
            project_id = cfg.getint("api", "project_id")
            page_size = cfg.getint("api", "page_size", fallback=250)
            verify_ssl = cfg.getboolean("api", "verify_ssl", fallback=True)
            base_url = cfg.get("api", "base_url").strip().rstrip("/")
            api_version = cfg.get("api", "api_version", fallback="v7_0").strip()
        except (ValueError, configparser.Error) as exc:
            raise SpiraError(
                "Set a numeric DH8 project_id, valid page_size and boolean verify_ssl in [api]."
            ) from exc
        parsed = urllib.parse.urlsplit(base_url)
        if (parsed.scheme != "https" or not parsed.hostname or parsed.username
                or parsed.password or parsed.query or parsed.fragment):
            raise SpiraError("base_url must be an HTTPS application URL without credentials or query.")
        if project_id <= 0 or not 1 <= page_size <= 500:
            raise SpiraError("project_id must be positive; page_size must be between 1 and 500.")
        if not re.fullmatch(r"v\d+_\d+", api_version):
            raise SpiraError("api_version must look like v7_0.")
        username = os.environ.get("SPIRA_USERNAME", "").strip()
        api_key = os.environ.get("SPIRA_API_KEY", "").strip()
        if not username or not api_key:
            raise SpiraError("Set SPIRA_USERNAME and SPIRA_API_KEY environment variables.")
        ca = cfg.get("api", "ca_bundle", fallback="").strip()
        if ca:
            ca = str((Path(path).resolve().parent / ca).resolve())
        return cls(base_url, project_id, username, api_key, ca or None, page_size, verify_ssl,
                   api_version)


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise SpiraError("API redirected. Configure the final HTTPS application URL.")


class SpiraClient:
    def __init__(self, settings: Settings, diagnostic_errors: bool = False):
        self.settings = settings
        self.diagnostic_errors = diagnostic_errors
        self._next_request_at = 0.0
        try:
            context = ssl.create_default_context(
                cafile=settings.ca_bundle if settings.verify_ssl else None
            )
        except (OSError, ssl.SSLError) as exc:
            raise SpiraError("Unable to load trusted CA certificates.") from exc
        if not settings.verify_ssl:
            context.check_hostname = False
            context.verify_mode = ssl.CERT_NONE
            print(
                "WARNING: TLS certificate verification is disabled. HTTPS remains encrypted, "
                "but server identity is not verified; credentials may be intercepted. "
                "Use only temporarily on a trusted network.",
                file=sys.stderr,
            )
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPSHandler(context=context), NoRedirect()
        )
        self.service_base = f"{settings.base_url}/Services/{settings.api_version}/RestService.svc"
        self.base = f"{self.service_base}/projects/{settings.project_id}"

    def request(self, path, params=None, filters=None, *, project_scoped=True):
        if not project_scoped and (path != "/projects" or filters is not None):
            raise SpiraError("Unscoped access is restricted to project discovery.")
        url = (self.base if project_scoped else self.service_base) + path
        if params:
            url += "?" + urllib.parse.urlencode(params)
        # Only the search endpoints use POST; these perform retrieval, not writes.
        if filters is not None and path != "/test-runs/search":
            raise SpiraError("POST is restricted to the read-only test-run search endpoint.")
        body = json.dumps(filters).encode("utf-8") if filters is not None else None
        headers = {
            "Accept": "application/json",
            "username": self.settings.username,
            "api-key": self.settings.api_key,
        }
        if body is not None:
            # A Content-Type on body-less GETs is unnecessary and some WCF/proxy setups reject it.
            headers["Content-Type"] = "application/json"
        for attempt in range(3):
            req = urllib.request.Request(url, data=body, headers=headers)
            remaining = self._next_request_at - time.monotonic()
            if remaining > 0:
                time.sleep(remaining)
            started = time.monotonic()
            try:
                try:
                    with self.opener.open(req, timeout=60) as response:
                        raw = response.read()
                finally:
                    finished = time.monotonic()
                    # A duration/3 cooldown gives 75% of the unpaced request rate.
                    self._next_request_at = finished + (finished - started) / 3
                return json.loads(raw.decode("utf-8-sig"))
            except urllib.error.HTTPError as exc:
                if exc.code not in (429, 500, 502, 503, 504) or attempt == 2:
                    code = exc.code
                    try:
                        # A 400 is a request-shape problem; its message is what identifies the cause.
                        detail = (self._error_detail(exc)
                                  if self.diagnostic_errors or code in (400, 404) else "")
                    finally:
                        exc.close()
                    query = f" (query: {', '.join(params)})" if params else ""
                    method = "POST" if body is not None else "GET"
                    raise SpiraError(
                        f"HTTP {code} on {method} {url.split('?')[0]}{query}; report aborted.{detail}"
                    ) from None
                retry = exc.headers.get("Retry-After", "")
                wait = min(int(retry), 60) if retry.isdigit() else 2 ** attempt
                exc.close()
                time.sleep(wait)
            except (urllib.error.URLError, TimeoutError, OSError):
                if attempt == 2:
                    raise SpiraError(
                        f"Connection/TLS failure retrieving {path}; check VPN and trusted CA."
                    ) from None
                time.sleep(2 ** attempt)
            except (UnicodeError, json.JSONDecodeError):
                raise SpiraError(f"Invalid JSON retrieving {path}; report aborted.") from None
        raise SpiraError(f"Retrieval failed: {path}")

    def _error_detail(self, response):
        try:
            text = response.read(16384).decode("utf-8-sig", errors="replace")
        except (OSError, TimeoutError):
            return " Server diagnostic could not be read."
        message = None
        try:
            data = json.loads(text)
            if isinstance(data, str):
                message = data
            elif isinstance(data, dict):
                message = next((data[key] for key in ("Message", "message", "ExceptionMessage")
                                if isinstance(data.get(key), str)), None)
        except json.JSONDecodeError:
            pass
        if message is None:
            try:
                root = ET.fromstring(text)
                message = next((node.text for node in root.iter()
                                if node.tag.split("}")[-1] in ("Message", "faultstring", "Text", "string")
                                and node.text), None)
            except ET.ParseError:
                pass
        if message is None:
            # HTML titles can identify proxy/IIS errors without displaying request dumps.
            title = re.search(r"<title\b[^>]*>(.*?)</title>", text, re.I | re.S)
            message = title.group(1) if title else None
        if message is None:
            return " Server diagnostic format unrecognized; response body withheld."
        message = html.unescape(urllib.parse.unquote(message))
        for secret in (self.settings.api_key, self.settings.api_key.strip("{}"),
                       self.settings.username):
            if secret:
                message = re.sub(re.escape(secret), "[REDACTED]", message, flags=re.I)
        message = re.sub(r"https?://[^\s<>\"']+", "[URL REDACTED]", message, flags=re.I)
        message = re.sub(
            r"\b(username|api[-_]key|authorization|password)\s*[:=]\s*[^\s,;<>]+",
            r"\1=[REDACTED]", message, flags=re.I,
        )
        message = re.sub(r"<[^>]*>", " ", message)
        message = " ".join(message.split())
        return " Server diagnostic: " + message[:1000]

    def array(self, path, params=None, filters=None, *, project_scoped=True):
        data = self.request(path, params, filters, project_scoped=project_scoped)
        if not isinstance(data, list) or any(not isinstance(row, dict) for row in data):
            raise SpiraError(f"Expected an array of objects from {path}.")
        return data

    def object(self, path):
        data = self.request(path)
        if not isinstance(data, dict):
            raise SpiraError(f"Expected an object from {path}.")
        return data

    def pages(self, path, id_field, params=None, filters=None):
        rows, seen = [], set()
        start = 1
        while True:
            query = {
                "starting_row": start, "number_of_rows": self.settings.page_size,
                "sort_field": id_field, "sort_direction": "ASC",
                **(params or {}),
            }
            page = self.array(path, query, filters)
            if not page:
                return rows
            for row in page:
                key = positive_id(row, id_field)
                if key in seen:
                    raise SpiraError(
                        f"Repeated {id_field} during pagination; data changed or paging was ignored."
                    )
                seen.add(key)
            rows.extend(page)
            # Advance by actual count, including server-capped pages. Fetch until empty.
            start += len(page)

    def projects(self):
        return self.array("/projects", project_scoped=False)

    def releases(self):
        return self.array("/releases", {"active_only": "false"})

    def folders(self):
        return self.array("/test-set-folders")

    def test_sets(self):
        return self.pages("/test-sets", "TestSetId")

    def release_scope(self, release_id):
        found, queue = {}, [release_id]
        while queue:
            current = queue.pop()
            if current in found:
                continue
            found[current] = self.object(f"/releases/{current}")
            if positive_id(found[current], "ReleaseId") != current:
                raise SpiraError("Release response ID does not match the requested release.")
            for child in self.array(f"/releases/{current}/children"):
                child_id = positive_id(child, "ReleaseId")
                if child_id != current and child_id not in found:
                    queue.append(child_id)
        return list(found.values())

    def extract(self, release_id, set_ids=(), folder_id=None):
        releases = self.release_scope(release_id)
        folders = self.folders()
        if folder_id is not None:
            ids = folder_scope(folders, folder_id)
            test_sets = []
            for fid in sorted(ids):
                test_sets.extend(self.pages(
                    f"/test-set-folders/{fid}/test-sets", "TestSetId"
                ))
            set_ids = sorted({positive_id(row, "TestSetId") for row in test_sets})
        else:
            set_ids = sorted(set(set_ids))
        sets, memberships, cases, configurations = [], {}, {}, {}
        for tx in set_ids:
            test_set = self.object(f"/test-sets/{tx}")
            if positive_id(test_set, "TestSetId") != tx:
                raise SpiraError("Test set response ID does not match the requested test set.")
            sets.append(test_set)
            if test_set.get("IsDynamic"):
                raise SpiraError(
                    f"Test set {tx} is dynamic. Verify its effective membership API before reporting it."
                )
            # Unlike /test-cases, this endpoint preserves instance IDs and positions.
            memberships[str(tx)] = self.array(f"/test-sets/{tx}/test-case-mapping")
            for tc in {positive_id(m, "TestCaseId") for m in memberships[str(tx)]}:
                if str(tc) not in cases:
                    cases[str(tc)] = self.object(f"/test-cases/{tc}")
            config_set_id = test_set.get("TestConfigurationSetId")
            configurations[str(tx)] = []
            if config_set_id:
                config_set_id = positive_id(test_set, "TestConfigurationSetId")
                config_set = self.object(f"/test-configuration-sets/{config_set_id}")
                entries = config_set.get("Entries")
                if not isinstance(entries, list):
                    raise SpiraError(
                        f"Configuration set {config_set_id} has no Entries array. "
                        "Provide an instance-specific JSON sample before reporting configurations."
                    )
                configurations[str(tx)] = entries
        runs = []
        if sets:
            filters = [
                {"PropertyName": "ReleaseId",
                 "MultiValue": {"Values": sorted(positive_id(r, "ReleaseId") for r in releases),
                                "IsNone": False}},
                {"PropertyName": "TestSetId",
                 "MultiValue": {"Values": set_ids, "IsNone": False}},
            ]
            runs = self.pages("/test-runs/search", "TestRunId", filters=filters)
        return {
            "schema_version": 1,
            "project_id": self.settings.project_id,
            "selected_release_id": release_id,
            "selected_folder_id": folder_id,
            "releases": releases, "folders": folders, "test_sets": sets,
            "memberships": memberships, "test_cases": cases,
            "configurations": configurations, "runs": runs,
        }


def positive_id(row, field):
    value = row.get(field)
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise SpiraError(f"Missing or invalid {field}; cannot preserve artifact identity.")
    return value


def folder_scope(folders, root):
    by_id = {positive_id(f, "TestSetFolderId"): f for f in folders}
    if root not in by_id:
        raise SpiraError(f"Test-set folder {root} is not present in this project.")
    selected, queue = set(), [root]
    while queue:
        current = queue.pop()
        if current in selected:
            continue
        selected.add(current)
        queue.extend(fid for fid, row in by_id.items()
                     if row.get("ParentTestSetFolderId") == current and fid not in selected)
    return selected
