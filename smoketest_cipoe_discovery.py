#!/usr/bin/env python3
"""
CIPOE Discovery Smoketest

This script discovers how many CIPOE issues exist in the Customer Intelligence project
and how many have "is impacted by" relationships.
"""
import os
import sys
import requests
import json
from typing import Dict, Any, List
import re

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

def is_cipoe_prefix(title: str) -> bool:
    """Check if issue summary starts with CIPOE (case-insensitive, anchored at start)"""
    return bool(re.match(r'^\s*CIPOE(\b|[\s:\-_/])', title or '', flags=re.IGNORECASE))

def extract_impacted_by_keys(issue: dict) -> list[str]:
    """Extract 'is impacted by' link keys from issue"""
    out = []
    for link in (issue.get("fields", {}).get("issuelinks") or []):
        t = (link.get("type") or {})
        if str(t.get("inward", "")).strip().lower() == "is impacted by".lower():
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
    
    print(f"Searching with JQL: {jql}")
    print(f"Start at: {start_at}, Max results: {max_results}")
    
    resp = requests.get(url, headers=headers, params=params, timeout=30)
    resp.raise_for_status()
    return resp.json()

def main():
    print("=== CIPOE Discovery Smoketest ===")
    print()
    
    # Test 1: Search CIPOE project for ALL issues
    print("1. Searching CIPOE project for ALL issues...")
    cipoe_project_jql = 'project = "CIPOE" ORDER BY updated DESC'
    
    try:
        cipoe_project_result = search_issues(cipoe_project_jql, start_at=0, max_results=100)
        cipoe_project_issues = cipoe_project_result.get("issues", [])
        cipoe_project_total = cipoe_project_result.get("total", 0)
        
        print(f"   Found {len(cipoe_project_issues)} issues in this page, Total: {cipoe_project_total}")
        
        if cipoe_project_issues:
            print("   Sample CIPOE project issues:")
            for i, issue in enumerate(cipoe_project_issues[:10]):
                summary = issue.get("fields", {}).get("summary", "")[:60]
                print(f"     {issue['key']}: {summary}")
            print()
    
    except requests.exceptions.RequestException as e:
        print(f"   ERROR: {e}")
        print()
    
    # Test 2: Search for specific CIPOE issues that current loader found
    print("2. Searching for specific CIPOE issues found by current loader...")
    specific_keys = ["CIPOE-11194", "CIPOE-13879", "CIPOE-20314", "CIPOE-26365", "CIPOE-37208"]
    
    found_specific = []
    for key in specific_keys:
        try:
            specific_jql = f'key = "{key}"'
            specific_result = search_issues(specific_jql, start_at=0, max_results=1)
            specific_issues = specific_result.get("issues", [])
            
            if specific_issues:
                issue = specific_issues[0]
                summary = issue.get("fields", {}).get("summary", "")[:60]
                project = issue.get("fields", {}).get("project", {}).get("key", "?")
                impacted_keys = extract_impacted_by_keys(issue)
                print(f"   FOUND: {key} [{project}]: {summary} ({len(impacted_keys)} links)")
                found_specific.append(issue)
            else:
                print(f"   NOT FOUND: {key}")
        except Exception as e:
            print(f"   ERROR searching {key}: {e}")
    
    print(f"   Found {len(found_specific)} of {len(specific_keys)} expected CIPOE issues")
    print()

    # Test 3: Search Customer Intelligence project for CIPOE issues  
    print("3. Searching Customer Intelligence project for CIPOE issues...")
    ci_jql = 'project = "Customer Intelligence" AND summary ~ "CIPOE*" ORDER BY updated DESC'
    
    try:
        ci_result = search_issues(ci_jql, start_at=0, max_results=50)
        ci_issues = ci_result.get("issues", [])
        ci_total = ci_result.get("total", 0)
        
        print(f"   Found {len(ci_issues)} issues in this page, Total: {ci_total}")
        
        if ci_issues:
            print("   Sample issues:")
            for i, issue in enumerate(ci_issues[:5]):
                summary = issue.get("fields", {}).get("summary", "")[:60]
                print(f"     {issue['key']}: {summary}")
            print()
    
    except requests.exceptions.RequestException as e:
        print(f"   ERROR: {e}")
        print("   Trying alternative project name formats...")
        
        # Test alternative project names
        alternatives = [
            'project = "CUSTOMER INTELLIGENCE"',
            'project = "CustomerIntelligence"', 
            'project = "CI"',
            'project = "CIPOE"'
        ]
        
        for alt_project in alternatives:
            try:
                alt_jql = f'{alt_project} AND summary ~ "CIPOE*" ORDER BY updated DESC'
                print(f"   Trying: {alt_jql}")
                alt_result = search_issues(alt_jql, start_at=0, max_results=10)
                alt_total = alt_result.get("total", 0)
                if alt_total > 0:
                    print(f"   SUCCESS! Found {alt_total} issues with this project filter")
                    ci_issues = alt_result.get("issues", [])
                    ci_total = alt_total
                    break
                else:
                    print(f"   No issues found")
            except:
                print(f"   Failed")
        print()
    
    # Test 4: Broad search for CIPOE issues across all projects  
    print("4. Searching ALL projects for CIPOE in summary...")
    broad_jql = 'summary ~ "CIPOE*" ORDER BY updated DESC'
    
    try:
        broad_result = search_issues(broad_jql, start_at=0, max_results=50)
        broad_issues = broad_result.get("issues", [])
        broad_total = broad_result.get("total", 0)
        
        print(f"   Found {len(broad_issues)} issues in this page, Total: {broad_total}")
        
        if broad_issues:
            print("   Projects containing CIPOE issues:")
            projects = {}
            for issue in broad_issues:
                project = issue.get("fields", {}).get("project", {}).get("key", "Unknown")
                projects[project] = projects.get(project, 0) + 1
            
            for project, count in sorted(projects.items(), key=lambda x: x[1], reverse=True):
                print(f"     {project}: {count} issues")
            print()
            
            print("   Sample issues:")
            for i, issue in enumerate(broad_issues[:5]):
                project = issue.get("fields", {}).get("project", {}).get("key", "?")
                summary = issue.get("fields", {}).get("summary", "")[:50]
                print(f"     {issue['key']} [{project}]: {summary}")
        print()
    
    except requests.exceptions.RequestException as e:
        print(f"   ERROR: {e}")
        print()
    
    # Test 5: Analyze all found issues for "is impacted by" relationships
    print("5. Analyzing CIPOE issues with 'is impacted by' relationships...")
    
    # Combine all search results
    all_test_issues = []
    if 'cipoe_project_issues' in locals():
        all_test_issues.extend(cipoe_project_issues)
    if 'found_specific' in locals():
        all_test_issues.extend(found_specific)
    if 'broad_issues' in locals():
        all_test_issues.extend(broad_issues)
    
    # Remove duplicates by key
    seen_keys = set()
    unique_issues = []
    for issue in all_test_issues:
        if issue['key'] not in seen_keys:
            seen_keys.add(issue['key'])
            unique_issues.append(issue)
    
    if not unique_issues:
        print("   No CIPOE issues found to analyze")
        return
    
    cipoe_project_with_links = []
    cipoe_project_without_links = []
    cipoe_summary_with_links = []
    cipoe_summary_without_links = []
    
    for issue in unique_issues:
        summary = issue.get("fields", {}).get("summary", "")
        key = issue['key']
        project = issue.get("fields", {}).get("project", {}).get("key", "")
        impacted_keys = extract_impacted_by_keys(issue)
        
        # Check if it's from CIPOE project
        if project == 'CIPOE':
            if impacted_keys:
                cipoe_project_with_links.append((key, len(impacted_keys)))
            else:
                cipoe_project_without_links.append(key)
        # Check if summary starts with CIPOE (but not in CIPOE project)
        elif is_cipoe_prefix(summary):
            if impacted_keys:
                cipoe_summary_with_links.append((key, len(impacted_keys)))
            else:
                cipoe_summary_without_links.append(key)
    
    print(f"   CIPOE project issues WITH 'is impacted by' links: {len(cipoe_project_with_links)}")
    if cipoe_project_with_links:
        print("     Sample:")
        for key, count in cipoe_project_with_links[:10]:
            print(f"       {key}: {count} impacted-by links")
    
    print(f"   CIPOE project issues WITHOUT 'is impacted by' links: {len(cipoe_project_without_links)}")
    if cipoe_project_without_links:
        print(f"     Count: {len(cipoe_project_without_links)} (not shown)")
    
    print(f"   Other projects with CIPOE summary WITH 'is impacted by' links: {len(cipoe_summary_with_links)}")
    if cipoe_summary_with_links:
        print("     Sample:")
        for key, count in cipoe_summary_with_links[:5]:
            print(f"       {key}: {count} impacted-by links")
    
    print(f"   Other projects with CIPOE summary WITHOUT 'is impacted by' links: {len(cipoe_summary_without_links)}")
    if cipoe_summary_without_links:
        print("     Sample:")
        for key in cipoe_summary_without_links[:3]:
            print(f"       {key}: no links")
    
    print()
    print("=== SUMMARY ===")
    if 'cipoe_project_total' in locals():
        print(f"CIPOE project total issues: {cipoe_project_total}")
    if 'ci_total' in locals():
        print(f"Customer Intelligence project CIPOE issues: {ci_total}")
    if 'broad_total' in locals():
        print(f"All projects CIPOE summary issues: {broad_total}")
    
    total_with_links = len(cipoe_project_with_links) + len(cipoe_summary_with_links)
    total_without_links = len(cipoe_project_without_links) + len(cipoe_summary_without_links)
    
    print(f"Total CIPOE issues with 'is impacted by' links: {total_with_links}")
    print(f"Total CIPOE issues without 'is impacted by' links: {total_without_links}")
    
    print()
    print("The current loader found 5 CIPOE issues with links (CIPOE-11194, CIPOE-13879, CIPOE-20314, CIPOE-26365, CIPOE-37208).")
    print("This analysis shows what we can find with the CIPOE project and across all projects.")

if __name__ == "__main__":
    main()
