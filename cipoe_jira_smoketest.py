#!/usr/bin/env python3
"""
CIPOE JIRA Smoke Test - Updated Logic

Test the updated CIPOE loader logic to see how many issues we'll find
with the correct "account is impacted by" relationships from the CIPOE project.
"""
import os
import sys
import requests
import json
from typing import Dict, Any, List

# Load from secrets.env if available
if os.path.exists('secrets.env'):
    with open('secrets.env', 'r') as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith('#') and '=' in line:
                key, value = line.split('=', 1)
                # Remove quotes if present
                value = value.strip('"\'')
                os.environ[key] = value

# Configuration
JIRA_BASE_URL = os.getenv("JIRA_BASE_URL", "https://issues.redhat.com").rstrip("/")
JIRA_TOKEN = os.getenv("JIRA_TOKEN")

if not JIRA_TOKEN:
    print("ERROR: JIRA_TOKEN not found in environment or secrets.env")
    sys.exit(1)

# JIRA API configuration
SEARCH_FIELDS = ["summary", "status", "updated", "issuetype", "priority", "project", "issuelinks"]
SEARCH_ENDPOINT = "/rest/api/2/search"
MAX_RESULTS_PER_PAGE = 100

def extract_account_impacted_by_keys(issue: dict) -> list[str]:
    """Extract 'account is impacted by' link keys from issue"""
    out = []
    for link in (issue.get("fields", {}).get("issuelinks") or []):
        t = (link.get("type") or {})
        # Look for "account is impacted by" relationship type
        if str(t.get("inward", "")).strip().lower() == "account is impacted by".lower():
            other = link.get("inwardIssue")
            if other and other.get("key"): 
                out.append(other["key"])
    
    # de-dupe preserve order
    seen = set()
    res = []
    for k in out:
        if k not in seen: 
            seen.add(k)
            res.append(k)
    return res

def search_issues(jql: str, start_at: int = 0, max_results: int = MAX_RESULTS_PER_PAGE) -> Dict[str, Any]:
    """Search for issues using JQL with pagination"""
    url = f"{JIRA_BASE_URL}{SEARCH_ENDPOINT}"
    
    headers = {
        "Authorization": f"Bearer {JIRA_TOKEN.strip()}",
        "Accept": "application/json",
        "Content-Type": "application/json",
    }
    
    params = {
        "jql": jql,
        "fields": ",".join(SEARCH_FIELDS),
        "startAt": start_at,
        "maxResults": min(max_results, MAX_RESULTS_PER_PAGE),
    }
    
    resp = requests.get(url, headers=headers, params=params, timeout=30)
    resp.raise_for_status()
    return resp.json()

def main():
    print("=== CIPOE JIRA Smoke Test - Updated Logic ===")
    print()
    
    # Test the updated query: CIPOE project specifically
    jql = 'project = "CIPOE" ORDER BY updated DESC'
    
    print(f"JQL Query: {jql}")
    print(f"Looking for 'account is impacted by' relationships...")
    print()
    
    total_fetched = 0
    total_with_links = 0
    total_without_links = 0
    sample_issues_with_links = []
    max_sample = 10
    max_issues_to_check = 1000  # Check first 1000 issues
    
    start_at = 0
    
    while total_fetched < max_issues_to_check:
        try:
            page_size = min(MAX_RESULTS_PER_PAGE, max_issues_to_check - total_fetched)
            
            print(f"Fetching page starting at {start_at} (page size: {page_size})...")
            search_result = search_issues(jql, start_at=start_at, max_results=page_size)
            issues = search_result.get("issues", [])
            total_issues_available = search_result.get("total", 0)
            
            if not issues:
                print("No more issues found")
                break
                
            print(f"  Found {len(issues)} issues in this page (Total available: {total_issues_available})")
            
            total_fetched += len(issues)
            
            # Process each issue to check for account links
            for issue in issues:
                summary = issue.get("fields", {}).get("summary", "")
                account_links = extract_account_impacted_by_keys(issue)
                
                if account_links:
                    total_with_links += 1
                    if len(sample_issues_with_links) < max_sample:
                        sample_issues_with_links.append({
                            "key": issue["key"],
                            "summary": summary[:60],
                            "links": len(account_links),
                            "sample_links": account_links[:3]  # Show first 3 links
                        })
                else:
                    total_without_links += 1
            
            print(f"  Page summary: {total_with_links} with links, {total_without_links} without links")
            
            # Update pagination
            start_at += len(issues)
            
        except Exception as e:
            print(f"ERROR processing page starting at {start_at}: {e}")
            break
    
    print()
    print("=== FINAL RESULTS ===")
    print(f"Total CIPOE issues checked: {total_fetched}")
    print(f"Total available in CIPOE project: {total_issues_available}")
    print(f"Issues WITH 'account is impacted by' links: {total_with_links}")
    print(f"Issues WITHOUT 'account is impacted by' links: {total_without_links}")
    
    if total_with_links > 0:
        print(f"\nThis means the updated loader would process {total_with_links} issues from the first {total_fetched} checked.")
        print(f"If we extrapolate across all {total_issues_available} issues, we might expect roughly:")
        estimated_total = int((total_with_links / total_fetched) * total_issues_available) if total_fetched > 0 else 0
        print(f"  ~{estimated_total} issues with account links across the entire CIPOE project")
    
    if sample_issues_with_links:
        print(f"\nSample issues with 'account is impacted by' links:")
        for sample in sample_issues_with_links:
            print(f"  {sample['key']}: {sample['summary']} ({sample['links']} links)")
            for link in sample['sample_links']:
                print(f"    -> {link}")
            print()
    
    print("\nComparison:")
    print("- Current loader found: 5 CIPOE issues with links")
    print(f"- Updated loader would find: {total_with_links} issues with links (from first {total_fetched} checked)")
    print("- Improvement: Targeting CIPOE project specifically and correct relationship type")

if __name__ == "__main__":
    main()
