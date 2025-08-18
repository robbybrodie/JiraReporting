#!/usr/bin/env python3
"""
CLI to fetch CIPOE issues and their "Impacts" issuelinks from a Jira Data Center instance.

Security notes:
- Token must be provided via the JIRA_TOKEN environment variable (never pass on command line).
- Outputs are written to the local reports/ directory (which should be git-ignored).
- This script will NOT write any secrets to disk or to source control.

Usage:
  export JIRA_BASE_URL="https://issues.redhat.com"
  export JIRA_TOKEN="..."   # Data Center PAT (Bearer)
  python3 scripts/jira_cipoe_links.py --limit 5 --output reports/cipoe-impacts.json
"""
from __future__ import annotations
import os
import sys
import json
import time
import argparse
import logging
from typing import Dict, Any, List, Optional

try:
    import requests
except Exception:
    print("Missing dependency 'requests'. Install with: pip install -r requirements.txt", file=sys.stderr)
    sys.exit(1)

# Configuration
JIRA_BASE_URL = os.getenv("JIRA_BASE_URL", "https://issues.redhat.com").rstrip("/")
JIRA_TOKEN = os.getenv("JIRA_TOKEN")  # required
DEFAULT_OUTPUT = "reports/cipoe-impacts.json"
SEARCH_FIELDS = ["key", "summary", "issuelinks", "issuetype", "status"]
SEARCH_ENDPOINT = "/rest/api/2/search"
ISSUE_ENDPOINT = "/rest/api/2/issue/{key}"

# Simple logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("cipoe-links")


def require_env():
    if not JIRA_TOKEN:
        logger.error("JIRA_TOKEN environment variable not set. Aborting.")
        sys.exit(2)


def build_headers():
    return {
        "Authorization": f"Bearer {JIRA_TOKEN}",
        "Accept": "application/json",
        "Content-Type": "application/json",
    }


def safe_request(session: requests.Session, method: str, url: str, **kwargs) -> requests.Response:
    """
    Small retry/backoff wrapper for requests.
    Retries on 429 and 5xx up to a few times.
    """
    max_attempts = 5
    backoff = 1.0
    for attempt in range(1, max_attempts + 1):
        resp = session.request(method, url, timeout=30, **kwargs)
        if resp.status_code < 400:
            return resp
        if resp.status_code in (429,) or 500 <= resp.status_code < 600:
            logger.warning("Request to %s returned %s. Attempt %d/%d. Backing off %s sec",
                           url, resp.status_code, attempt, max_attempts, backoff)
            time.sleep(backoff)
            backoff = min(backoff * 2, 8)
            continue
        # other client errors -> raise (bad auth, bad input)
        return resp
    # last attempt result
    return resp


def search_cipoe(session: requests.Session, limit: int, start_at: int = 0, include_zero_links: bool = False) -> Dict[str, Any]:
    """Search for CIPOE issues with pagination
    
    Args:
        session: requests.Session object
        limit: Maximum number of results
        start_at: Starting index for pagination
        include_zero_links: If False (default), only return CIPOE tickets with linked issues
    """
    url = f"{JIRA_BASE_URL}{SEARCH_ENDPOINT}"
    
    if include_zero_links:
        jql_query = "project = CIPOE ORDER BY key ASC"
        logger.info("Searching for all CIPOE tickets (including those with zero linked issues)")
    else:
        jql_query = "project = CIPOE AND issuelinks is not EMPTY ORDER BY key ASC"
        logger.info("Searching for CIPOE tickets with linked issues only")
    
    payload = {
        "jql": jql_query,
        "fields": SEARCH_FIELDS,
        "startAt": start_at,
        "maxResults": limit,
    }
    resp = safe_request(session, "POST", url, json=payload, headers=build_headers())
    if resp.status_code == 401 or resp.status_code == 403:
        logger.error("Authentication error (%s). Check JIRA_TOKEN and permissions.", resp.status_code)
        logger.debug("Response: %s", resp.text)
        resp.raise_for_status()
    if resp.status_code >= 400:
        logger.error("Search request failed: %s", resp.status_code)
        logger.debug("Response: %s", resp.text)
        resp.raise_for_status()
    return resp.json()


def get_issue_details(session: requests.Session, key: str) -> Dict[str, Any]:
    url = f"{JIRA_BASE_URL}{ISSUE_ENDPOINT.format(key=key)}"
    params = {"fields": ",".join(["key", "summary", "issuetype", "status"])}
    resp = safe_request(session, "GET", url, params=params, headers=build_headers())
    if resp.status_code == 404:
        logger.warning("Linked issue %s not found (404).", key)
        return {"key": key, "summary": None, "issuetype": {"name": None}, "status": {"name": None}}
    if resp.status_code >= 400:
        logger.error("Failed to fetch issue %s: %s", key, resp.status_code)
        resp.raise_for_status()
    data = resp.json()
    fields = data.get("fields", {})
    return {
        "key": data.get("key"),
        "summary": fields.get("summary"),
        "issuetype": {"name": (fields.get("issuetype") or {}).get("name")},
        "status": {"name": (fields.get("status") or {}).get("name")},
    }


def process_issues(session: requests.Session, issues: List[Dict[str, Any]], skip_zero_links: bool = True) -> List[Dict[str, Any]]:
    results: List[Dict[str, Any]] = []
    for issue in issues:
        key = issue.get("key")
        fields = issue.get("fields", {})
        summary = fields.get("summary")
        issuelinks = fields.get("issuelinks", []) or []
        
        # Validation: Skip tickets with zero links if filtering is enabled
        if skip_zero_links and len(issuelinks) == 0:
            logger.warning(f"Skipping CIPOE {key} - no linked issues found (this shouldn't happen with JQL filtering)")
            continue
        for link in issuelinks:
            # Determine whether this link represents an "impact" relationship.
            # Some Jira instances use a link type named "Impacts", others use custom types
            # whose inward/outward descriptions contain the word "impact" (e.g. "account is impacted by").
            link_type = (link.get("type") or {}).get("name", "") or ""
            relation_inward = (link.get("type") or {}).get("inward", "") or ""
            relation_outward = (link.get("type") or {}).get("outward", "") or ""
            # consider it an impact if the type name is "Impacts" or the inward/outward text contains 'impact'
            is_impact = False
            if link_type and link_type.lower() == "impacts":
                is_impact = True
            if "impact" in relation_inward.lower() or "impact" in relation_outward.lower() or "impact" in link_type.lower():
                is_impact = True
            if not is_impact:
                continue

            # The link object may contain inwardIssue or outwardIssue or both
            inward = link.get("inwardIssue")
            outward = link.get("outwardIssue")

            # If inwardIssue is present, that refers to the issue on the "inward" side
            if inward:
                linked_key = inward.get("key")
                # relation text: inward describes how inwardIssue relates to outwardIssue (e.g. "is impacted by")
                relation = relation_inward or "inward"
                linked = inward
                # Some Jira instances include only key and basic fields in the embedded issue; enrich if necessary
                linked_summary = (linked.get("fields") or {}).get("summary") or linked.get("summary")
                if not linked_summary:
                    linked_details = get_issue_details(session, linked_key)
                    linked_summary = linked_details.get("summary")
                    linked_type = (linked_details.get("issuetype") or {}).get("name")
                    linked_status = (linked_details.get("status") or {}).get("name")
                else:
                    linked_type = (linked.get("fields") or {}).get("issuetype", {}).get("name")
                    linked_status = (linked.get("fields") or {}).get("status", {}).get("name")
                results.append({
                    "cipoeKey": key,
                    "cipoeSummary": summary,
                    "relation": relation,
                    "linkedKey": linked_key,
                    "linkedSummary": linked_summary,
                    "linkedType": linked_type,
                    "linkedStatus": linked_status,
                })
            if outward:
                linked_key = outward.get("key")
                relation = relation_outward or "outward"
                linked = outward
                linked_summary = (linked.get("fields") or {}).get("summary") or linked.get("summary")
                if not linked_summary:
                    linked_details = get_issue_details(session, linked_key)
                    linked_summary = linked_details.get("summary")
                    linked_type = (linked_details.get("issuetype") or {}).get("name")
                    linked_status = (linked_details.get("status") or {}).get("name")
                else:
                    linked_type = (linked.get("fields") or {}).get("issuetype", {}).get("name")
                    linked_status = (linked.get("fields") or {}).get("status", {}).get("name")
                results.append({
                    "cipoeKey": key,
                    "cipoeSummary": summary,
                    "relation": relation,
                    "linkedKey": linked_key,
                    "linkedSummary": linked_summary,
                    "linkedType": linked_type,
                    "linkedStatus": linked_status,
                })
    return results


def get_issue_full(session: requests.Session, key: str) -> Dict[str, Any]:
    url = f"{JIRA_BASE_URL}{ISSUE_ENDPOINT.format(key=key)}"
    params = {"fields": ",".join(["key", "summary", "issuelinks", "issuetype", "status"])}
    resp = safe_request(session, "GET", url, params=params, headers=build_headers())
    if resp.status_code == 404:
        logger.error("Issue %s not found.", key)
        resp.raise_for_status()
    if resp.status_code >= 400:
        logger.error("Failed to fetch issue %s: %s", key, resp.status_code)
        resp.raise_for_status()
    return resp.json()


def main():
    parser = argparse.ArgumentParser(description="Fetch CIPOE issues and their Impacts issuelinks from Jira.")
    parser.add_argument("--limit", type=int, default=5, help="Number of CIPOE issues to fetch (for test).")
    parser.add_argument("--start-at", type=int, default=0, help="StartAt for Jira search pagination.")
    parser.add_argument("--output", type=str, default=DEFAULT_OUTPUT, help="Output JSON file (local, untracked).")
    parser.add_argument("--issue", type=str, default=None, help="Specific issue key to fetch (e.g., CIPOE-13879).")
    parser.add_argument("--stdout", action="store_true", help="Print resulting JSON to stdout instead of saving to file.")
    parser.add_argument("--include-zero-links", action="store_true", help="Include CIPOE tickets with zero linked issues (default: exclude them)")
    args = parser.parse_args()

    require_env()

    out_path = args.output
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    os.makedirs("reports", exist_ok=True)

    session = requests.Session()

    if args.issue:
        logger.info("Fetching single issue %s...", args.issue)
        try:
            issue_data = get_issue_full(session, args.issue)
        except Exception as e:
            logger.exception("Failed to fetch issue %s: %s", args.issue, e)
            sys.exit(4)
        issues = [issue_data]
        total = 1
        logger.info("Fetched issue %s", args.issue)
    else:
        logger.info("Searching CIPOE issues (limit=%d, startAt=%d)...", args.limit, args.start_at)
        try:
            data = search_cipoe(session, limit=args.limit, start_at=args.start_at, include_zero_links=args.include_zero_links)
        except Exception as e:
            logger.exception("Search failed: %s", e)
            sys.exit(3)
        issues = data.get("issues", [])
        total = data.get("total", len(issues))
        logger.info("Fetched %d issues (total reported: %s)", len(issues), total)

    results = process_issues(session, issues, skip_zero_links=not args.include_zero_links)
    logger.info("Processed issues; found %d linked Impact relationships.", len(results))

    output_json = json.dumps(results, indent=2, ensure_ascii=False)
    if args.stdout:
        print(output_json)
    else:
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(output_json)
        logger.info("Wrote results to %s (ensure this file is not committed).", out_path)


if __name__ == "__main__":
    main()
