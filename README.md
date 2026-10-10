# DH8 Spira report generator

First-stage API extraction and reporting, separate from the earlier dashboard.
Requires Python 3.10+. JSON/CSV and offline tests use only the standard library;
Excel export needs `pip install -r requirements.txt`.

## Connection

Copy `spira.example.ini` to `spira.ini`. Set the actual HTTPS application URL
and **DH8 project ID**. The older project's ID 832 is not assumed to be DH8.
Set credentials only in the current process environment:

```powershell
Set-Location C:\DH8\spira_report
$env:SPIRA_USERNAME = Read-Host "Spira username"
$key = Read-Host "Spira API key (including braces)" -AsSecureString
$env:SPIRA_API_KEY = [System.Net.NetworkCredential]::new("", $key).Password
python cli.py discover releases
python cli.py discover folders
python cli.py discover test-sets
```

TLS certificate verification is enabled by default. Configure `ca_bundle` with a trusted PEM
certificate bundle if the company certificate is not trusted by Python.
Relative CA paths are resolved relative to the INI file. Credentials use headers,
are not logged, and are not included in saved snapshots. Redirects are rejected
to avoid forwarding authentication headers to another host.

For temporary troubleshooting on a trusted network, set `verify_ssl = false`
under `[api]` in `spira.ini`. This disables certificate/hostname verification,
not HTTPS encryption, and prints a warning on every client initialization.
It permits server impersonation and credential interception; restore
`verify_ssl = true` once trusted certificates are configured. `ca_bundle` is
ignored while verification is disabled. An HTTP 400 already means an HTTP
response was received over TLS, so disabling verification is not expected to
fix that request error.

## Reports

These are illustrative IDs: replace them with IDs from discovery.

```powershell
python cli.py report --release 123 --test-set 456 457 --output results.xlsx --snapshot extraction.json
python cli.py report --release 123 --folder 789 --output results.json --snapshot extraction.json
python cli.py offline --snapshot extraction.json --output results.csv
python -m unittest discover -s tests -v
```

An explicit set selection and a folder selection are mutually exclusive.
A selected folder includes **all descendants**; a root/folderless selection
is made using explicit test-set IDs. There is no implicit scheduled-release
filter on test sets: the release selection filters **execution evidence**.
An empty folder produces an explicit zero-total report.

## Reporting contract

- Inventory is the selected sets' **current** memberships, obtained from
  `/test-sets/{id}/test-case-mapping`, not the case-list endpoint. Preserve
  membership ID and actual position; never deduplicate by case name or case ID.
- Include the selected release and all descendants, including inactive releases.
  Expand using the release children endpoint, not display indentation.
- Keep configurations separate by `TestConfigurationId`. Read expected entries
  from the set's associated configuration set. Every expected membership/config
  without a qualifying completion is **Not Run**. Also retain configurations
  observed in scoped history but absent from current configuration inventory,
  with explicit warnings. Unconfigured memberships without history get one row.
- Select completed runs by `EndDate` descending, then `TestRunId` descending.
  A Not Run attempt is never completion evidence, even if it has an end date.
  UTC completion time governs selection, not the order of run creation.
- Display the selected run's status, execution release/version, build ID and
  tester ID. Scheduled release is a separate ID column.
- Show latest attempt separately, ranked by `StartDate`, then run ID; a later
  unfinished/Not Run attempt is marked **Retest Pending** without hiding an
  earlier Failed or Passed completion. Attempts without a start date are not
  assumed to postdate a known completion.
- Count Passed, Failed, Blocked, Not Run, N/A and Caution from the same rows as
  details. Folderless sets remain visible. No Jira-based pass/fail reclassification.
- Missing credentials, HTTP failures, malformed responses, unknown statuses,
  missing membership IDs and repeated pagination IDs abort report generation.
  No Excel fallback or partial-success dataset is substituted.
- JSON and Excel include scope, policy and extraction time. CSV contains details;
  retain its extraction snapshot for provenance. Artifact text is escaped for
  spreadsheet formula safety.

## API operations

All calls use the v7 endpoint beneath the configured application URL.
GET operations retrieve releases, release children, folders, sets, mappings,
cases and configuration sets. The only POST is `/test-runs/search`, a **retrieval**
operation with ReleaseId and TestSetId filters; it does not change test artifacts.
Runs and set lists use explicit ascending ID sorting and pages of at most 500.
Pagination advances by the actual returned count until an empty page, so server
page caps do not cause early truncation. Repeated IDs abort instead of looping.
Transient HTTP errors retry three times; permanent errors abort immediately.

Requests remain sequential. Before sending another request, the client enforces
a cooldown equal to one-third of the preceding HTTP attempt's duration
(including reading the response), targeting **25% fewer requests per second**
than back-to-back calls with the same response times. This applies across all
endpoints, pagination and retries. Existing retry backoff or intervening work
counts toward the cooldown; longer backoff is never shortened. Page sizes and
report contents are unchanged. This is relative pacing, not a known safe server
limit or a global limiter across multiple processes.

## Remaining live verification

To diagnose an HTTP error without printing complete response bodies or credentials:

```powershell
python cli.py --diagnostic-errors discover projects
python cli.py --diagnostic-errors discover releases
```

Project discovery calls the service-level `/projects` endpoint, not
`/projects/{configured_id}/projects`. Check the returned `ProjectId` and `Name`
to confirm the configured DH8 project and API-account access. If both project
and release discovery fail, diagnose service routing/authentication before
changing release IDs. These failures do not establish the specific cause.

Place the flag before the subcommand. Diagnostic mode extracts a bounded JSON/XML
error message or HTML title, redacts configured credentials and URLs, and never
prints arbitrary response bodies or stack traces. Review the message for private
information before sharing it. Default errors omit server messages, except HTTP 400, where the redacted message is
included because it names the rejected parameter or filter. The error also names the
method, endpoint and query parameter names (not values).

The DH8 connection is intentionally not configured or contacted yet.
Confirm the instance's installed v7 API, permissions and search filters using
a small known set and release. Compare selected run IDs and status totals with
the Spira UI/export before using the report operationally.

The public configuration-set schema does not document the nested `Entries`
fields. This client requires each entry to expose `TestConfigurationId`;
if the instance uses another contract, it fails explicitly rather than inventing
configuration identity. Provide a sanitized response sample to adapt it.
Dynamic test sets are rejected pending confirmation of an effective-inventory
API for your instance. Historical runs for removed memberships are excluded
with a warning; reconstructing an inventory as it existed at a past release
is not implemented.

API responses can contain private artifact data. Keep reports/snapshots local
and control access. Remove environment credentials when finished:

```powershell
Remove-Item Env:\SPIRA_API_KEY
Remove-Item Env:\SPIRA_USERNAME
```

Streamlit is the second stage: reuse `SpiraClient.extract()` and
`build_report()` rather than adding different filtering/counting logic.

References:
- https://spiradoc.inflectra.com/Developers/API-Overview/
- https://api.inflectra.com/Spira/Services/v7_0/RestService.aspx
- https://api.inflectra.com/Spira/Services/v7_0/RestServiceOpenApi.ashx
