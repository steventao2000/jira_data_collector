"""Collect Jira issues for the configured product and creation date range.

Requirements:
	Python 3.7 or newer; only Python standard-library modules are used.

Usage:
	Configure JIRA_USERNAME and JIRA_TOKEN below, then run:
		python jira_data_collector.py

Create the API token from your Jira profile's API tokens section. The collector
fetches matching issues and their comments, then writes one JSON file per issue
under the PRODUCT-named folder. Do not commit or share
real credentials embedded in this file.
"""

import base64
import json
import os
import re
import ssl
import sys
from datetime import date, timedelta
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen


JIRA_BASE_URL = os.getenv("JIRA_BASE_URL", "https://jira-pro.company.com:portnumber").rstrip("/")
# Create an API token on your Jira profile page and put the new token below.
JIRA_USERNAME = "firstname.lastname@company.com"
JIRA_PASSWORD = ""
JIRA_TOKEN = "Get A API toke from your jira website under account profile"
PRODUCT = "COMMON_PRODUCT"
CREATED_FROM = "2026-01-01"
CREATED_UNTIL = date.today().isoformat()
PAGE_SIZE = 100
COMMENT_PAGE_SIZE = 1000
PROBLEM_STATEMENT_FIELD = "customfield_15055"
SIE_ID_FIELD = "customfield_15084"


def _authorization_header():
	if JIRA_USERNAME and JIRA_TOKEN:
		credentials = base64.b64encode(
			f"{JIRA_USERNAME}:{JIRA_TOKEN}".encode("utf-8")
		).decode("ascii")
		return f"Basic {credentials}"

	if JIRA_USERNAME and JIRA_PASSWORD:
		credentials = base64.b64encode(
			f"{JIRA_USERNAME}:{JIRA_PASSWORD}".encode("utf-8")
		).decode("ascii")
		return f"Basic {credentials}"

	raise RuntimeError(
		"Set JIRA_USERNAME and JIRA_TOKEN (API token), or JIRA_USERNAME and JIRA_PASSWORD."
	)


def _ssl_context():
	verify_tls = os.getenv("JIRA_VERIFY_TLS", "true").lower() not in {"0", "false", "no"}
	context = ssl.create_default_context()
	if not verify_tls:
		context.check_hostname = False
		context.verify_mode = ssl.CERT_NONE
	return context


def _fetch_page(jql, start_at):
	query = urlencode(
		{
			"jql": jql,
			"startAt": start_at,
			"maxResults": PAGE_SIZE,
			"fields": ",".join(
				(
					"summary",
					"created",
					"status",
					"assignee",
					"comment",
					PROBLEM_STATEMENT_FIELD,
					SIE_ID_FIELD,
				)
			),
		}
	)
	url = f"{JIRA_BASE_URL}/rest/api/2/search?{query}"
	request = Request(
		url,
		headers={
			"Accept": "application/json",
			"Authorization": _authorization_header(),
		},
	)

	try:
		with urlopen(request, timeout=30, context=_ssl_context()) as response:
			return json.load(response)
	except HTTPError as error:
		detail = error.read().decode("utf-8", errors="replace")
		raise RuntimeError(f"Jira returned HTTP {error.code}: {detail}") from error
	except URLError as error:
		raise RuntimeError(f"Could not connect to Jira: {error.reason}") from error


def _fetch_all_comments(issue_key, initial_page):
	comments = list(initial_page.get("comments", []))
	total = initial_page.get("total", len(comments))
	start_at = len(comments)

	while start_at < total:
		query = urlencode({"startAt": start_at, "maxResults": COMMENT_PAGE_SIZE})
		url = f"{JIRA_BASE_URL}/rest/api/2/issue/{quote(issue_key, safe='-')}/comment?{query}"
		request = Request(
			url,
			headers={
				"Accept": "application/json",
				"Authorization": _authorization_header(),
			},
		)
		try:
			with urlopen(request, timeout=30, context=_ssl_context()) as response:
				page = json.load(response)
		except HTTPError as error:
			detail = error.read().decode("utf-8", errors="replace")
			raise RuntimeError(f"Jira returned HTTP {error.code}: {detail}") from error
		except URLError as error:
			raise RuntimeError(f"Could not connect to Jira: {error.reason}") from error

		page_comments = page.get("comments", [])
		if not page_comments:
			break
		comments.extend(page_comments)
		start_at += len(page_comments)
		total = page.get("total", total)

	return comments


def fetch_issues():
	created_before = (date.fromisoformat(CREATED_UNTIL) + timedelta(days=1)).isoformat()
	jql = (
		f'"Product" = "{PRODUCT}" AND created >= "{CREATED_FROM}" '
		f'AND created < "{created_before}" ORDER BY created ASC'
	)
	issues = []
	start_at = 0

	while True:
		page = _fetch_page(jql, start_at)
		page_issues = page.get("issues", [])
		issues.extend(page_issues)
		start_at += len(page_issues)

		if not page_issues or start_at >= page.get("total", 0):
			return issues


def main():
	try:
		issues = fetch_issues()
	except RuntimeError as error:
		print(error, file=sys.stderr)
		return 1

	output_directory = Path(PRODUCT)
	output_directory.mkdir(parents=True, exist_ok=True)
	for issue in issues:
		issue_key = issue.get("key")
		if not issue_key:
			continue
		fields = issue.get("fields") or {}
		comment_page = fields.pop("comment", {}) or {}
		issue["Problem statemen"] = fields.get(PROBLEM_STATEMENT_FIELD)
		issue["SIE ID"] = fields.get(SIE_ID_FIELD)
		issue["comments"] = _fetch_all_comments(issue_key, comment_page)
		filename = re.sub(r"[^A-Za-z0-9_-]", "_", issue_key) + ".json"
		output_path = output_directory / filename
		with output_path.open("w", encoding="utf-8") as output:
			json.dump(issue, output, indent=2, ensure_ascii=False)

	print(f"Collected {len(issues)} Jira tickets as individual JSON files in {output_directory}")
	return 0


if __name__ == "__main__":
	raise SystemExit(main())
