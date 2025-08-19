#!/usr/bin/env python3
"""
Inspect specific CIPOE issues for link details

Check why our smoketest shows 0 links but the loader found links.
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

def get_issue_details(issue_key: str) -> Dict[str, Any]:
    """Get full issue details including all fields"""
    url = f"{JIRA_BASE_URL}/rest/api/2/issue/{issue_key}"
    
    headers = {
        "Authorization": f"Bearer {JIRA_TOKEN.strip()}",
        "Accept": "application/json",
        "Content-Type": "application/json",
    }
    
    resp = requests.get(url, headers=headers, timeout=30)
    resp.raise_for_status()
    return resp.json()

def main():
    print("=== CIPOE Link Inspection ===")
    print()
    
    # Check the specific issues that current loader found with links
    specific_keys = ["CIPOE-11194", "CIPOE-13879", "CIPOE-20314", "CIPOE-26365", "CIPOE-37208"]
    
    for key in specific_keys:
        try:
            print(f"Inspecting {key}...")
            issue = get_issue_details(key)
            
            # Basic info
            fields = issue.get("fields", {})
            summary = fields.get("summary", "")
            project = fields.get("project", {}).get("key", "?")
            print(f"  Summary: {summary}")
            print(f"  Project: {project}")
            
            # Check issue links in detail
            issuelinks = fields.get("issuelinks", [])
            print(f"  Total issuelinks: {len(issuelinks)}")
            
            if issuelinks:
                print("  Issue links details:")
                for i, link in enumerate(issuelinks):
                    link_type = link.get("type", {})
                    inward = link_type.get("inward", "")
                    outward = link_type.get("outward", "")
                    
                    print(f"    Link {i+1}:")
                    print(f"      Type: {link_type.get('name', 'Unknown')}")
                    print(f"      Inward: '{inward}'")
                    print(f"      Outward: '{outward}'")
                    
                    # Check which direction this link goes
                    if "inwardIssue" in link:
                        other_issue = link["inwardIssue"]
                        print(f"      Inward Issue: {other_issue.get('key', 'Unknown')} ('{inward}' from {key})")
                    
                    if "outwardIssue" in link:
                        other_issue = link["outwardIssue"]
                        print(f"      Outward Issue: {other_issue.get('key', 'Unknown')} ('{outward}' to {key})")
                    
                    print()
            
            # Look for "is impacted by" specifically
            impacted_by_links = []
            for link in issuelinks:
                link_type = link.get("type", {})
                inward = str(link_type.get("inward", "")).strip().lower()
                
                print(f"  Checking inward: '{inward}' == 'is impacted by'? {inward == 'is impacted by'}")
                
                if inward == "is impacted by":
                    inward_issue = link.get("inwardIssue")
                    if inward_issue and inward_issue.get("key"):
                        impacted_by_links.append(inward_issue["key"])
            
            print(f"  Found 'is impacted by' links: {len(impacted_by_links)}")
            if impacted_by_links:
                for linked_key in impacted_by_links:
                    print(f"    -> {linked_key}")
            
            print("-" * 50)
            
        except Exception as e:
            print(f"  ERROR: {e}")
            print("-" * 50)

if __name__ == "__main__":
    main()
