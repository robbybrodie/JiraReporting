#!/usr/bin/env python3
"""
JIRA CIPOE to Neo4j Graph Database Loader

This script queries JIRA for issues with CIPOE in their summary and their linked issues,
then stores the relationships in a Neo4j graph database for analysis.

Security notes:
- Credentials are provided via environment variables from Kubernetes secrets
- No secrets are written to disk or source control
"""
from __future__ import annotations
import os
import sys
import json
import time
import logging
import re
from typing import Dict, Any, List, Optional

try:
    import requests
    from neo4j import GraphDatabase
except ImportError as e:
    print(f"Missing dependency: {e}. Install with: pip install -r requirements.txt", file=sys.stderr)
    sys.exit(1)

# Configuration from environment
JIRA_BASE_URL = os.getenv("JIRA_BASE_URL", "https://issues.redhat.com").rstrip("/")
JIRA_TOKEN = os.getenv("JIRA_TOKEN")
NEO4J_URI = os.getenv("NEO4J_URI", "bolt://neo4j:7687")
NEO4J_USERNAME = os.getenv("NEO4J_USERNAME", "neo4j")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD")

# JQL Query from environment or default
JQL_QUERY = os.getenv("JQL_QUERY", 'text ~ "CIPOE" ORDER BY updated DESC')
# Prune setting from environment
PRUNE = os.getenv("PRUNE", "true").lower() == "true"

# JIRA API configuration - updated fields as specified
SEARCH_FIELDS = ["summary", "status", "updated", "issuetype", "priority", "project", "issuelinks"]
SEARCH_ENDPOINT = "/rest/api/2/search"

# Pagination settings
DEFAULT_PAGE_SIZE = 100
DEFAULT_MAX_RESULTS = 500

# Logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s"
)
logger = logging.getLogger("cipoe-neo4j-loader")


def is_cipoe_title_prefix(issue: dict) -> bool:
    """Check if issue summary starts with CIPOE (case-insensitive)"""
    s = ((issue.get("fields") or {}).get("summary") or "")
    return bool(re.match(r'^\s*CIPOE(\b|[\s:\-_/])', s, flags=re.IGNORECASE))


class Neo4jConnection:
    """Neo4j database connection manager"""
    
    def __init__(self, uri: str, username: str, password: str):
        self.driver = GraphDatabase.driver(uri, auth=(username, password))
    
    def close(self):
        if self.driver:
            self.driver.close()
    
    def execute_query(self, query: str, parameters: Dict = None):
        """Execute a Cypher query"""
        with self.driver.session() as session:
            return session.run(query, parameters or {})
    
    def create_constraint(self):
        """Create unique constraint for Issue nodes"""
        query = """
        CREATE CONSTRAINT issue_key IF NOT EXISTS
        FOR (i:Issue) REQUIRE i.key IS UNIQUE
        """
        try:
            self.execute_query(query)
            logger.info("Created unique constraint for Issue.key")
        except Exception as e:
            logger.warning(f"Constraint creation failed (may already exist): {e}")


class JiraClient:
    """JIRA API client with retry logic"""

    def __init__(self, base_url: str, token: str):
        self.base_url = base_url.rstrip("/")
        # Clean token of any whitespace/newlines 
        clean_token = token.strip()
        self.headers = {
            "Authorization": f"Bearer {clean_token}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        }
        self.session = requests.Session()
    
    def safe_request(self, method: str, url: str, **kwargs) -> requests.Response:
        """Request with retry/backoff for rate limiting"""
        max_attempts = 5
        backoff = 1.0
        
        for attempt in range(1, max_attempts + 1):
            resp = self.session.request(method, url, timeout=30, headers=self.headers, **kwargs)
            
            if resp.status_code < 400:
                return resp
            
            if resp.status_code in (429,) or 500 <= resp.status_code < 600:
                logger.warning(
                    f"Request to {url} returned {resp.status_code}. "
                    f"Attempt {attempt}/{max_attempts}. Backing off {backoff}s"
                )
                time.sleep(backoff)
                backoff = min(backoff * 2, 8)
                continue
            
            # Client errors - don't retry
            resp.raise_for_status()
        
        return resp
    
    def search_issues(self, jql: str, start_at: int = 0, max_results: int = 100) -> Dict[str, Any]:
        """Search for issues using JQL with pagination"""
        url = f"{self.base_url}{SEARCH_ENDPOINT}"
        
        params = {
            "jql": jql,
            "fields": ",".join(SEARCH_FIELDS),
            "startAt": start_at,
            "maxResults": max_results,
        }
        
        resp = self.safe_request("GET", url, params=params)
        resp.raise_for_status()
        return resp.json()


def extract_impacted_by_links(issue: Dict[str, Any]) -> List[str]:
    """Extract 'is impacted by' link keys from issue"""
    fields = issue.get("fields", {})
    issue_links = fields.get("issuelinks", []) or []
    
    impacted_keys = []
    
    for link in issue_links:
        link_type = link.get("type", {})
        inward_desc = (link_type.get("inward", "") or "").lower()
        
        # Check if this is an "is impacted by" relationship (case-insensitive)
        if "is impacted by" in inward_desc and "inwardIssue" in link:
            inward_key = link["inwardIssue"].get("key")
            if inward_key:
                impacted_keys.append(inward_key)
    
    return impacted_keys


def map_issue_to_row(issue: Dict[str, Any]) -> Dict[str, Any]:
    """Map JIRA issue to our row format"""
    f = issue.get("fields", {})
    
    return {
        "key": issue["key"],
        "props": {
            "summary": f.get("summary"),
            "status": (f.get("status") or {}).get("name"),
            "issuetype": (f.get("issuetype") or {}).get("name"),
            "updated": f.get("updated"),
            "priority": (f.get("priority") or {}).get("name"),
            "project": ((f.get("project") or {}).get("key")),
            "key_url": f"{JIRA_BASE_URL}/browse/{issue['key']}",
        },
        "impactedKeys": extract_impacted_by_links(issue)
    }


def upsert_issues_and_relationships(neo4j_conn: Neo4jConnection, rows: List[Dict[str, Any]], prune: bool = True):
    """Upsert Issue nodes and IMPACTED_BY relationships"""
    if not rows:
        return
    
    # Upsert all Issue nodes
    for row in rows:
        query = """
        MERGE (i:Issue {key: $key})
        SET i += $props
        """
        params = {
            "key": row["key"],
            "props": row["props"]
        }
        neo4j_conn.execute_query(query, params)
    
    # Create IMPACTED_BY relationships
    all_relationships = []
    for row in rows:
        for impacted_key in row["impactedKeys"]:
            all_relationships.append((impacted_key, row["key"]))
    
    # Upsert relationships
    for impacter_key, impacted_key in all_relationships:
        query = """
        MATCH (impacter:Issue {key: $impacter_key})
        MATCH (impacted:Issue {key: $impacted_key})
        MERGE (impacter)-[r:IMPACTED_BY]->(impacted)
        """
        params = {
            "impacter_key": impacter_key,
            "impacted_key": impacted_key
        }
        neo4j_conn.execute_query(query, params)
    
    # Optional pruning: remove IMPACTED_BY relationships not in current payload
    if prune:
        current_issue_keys = [row["key"] for row in rows]
        if current_issue_keys:
            query = """
            MATCH (i:Issue)-[r:IMPACTED_BY]->()
            WHERE i.key IN $current_keys
            AND NOT EXISTS {
                MATCH (impacter:Issue)-[r2:IMPACTED_BY]->(i)
                WHERE (impacter.key, i.key) IN $valid_relationships
            }
            DELETE r
            """
            params = {
                "current_keys": current_issue_keys,
                "valid_relationships": all_relationships
            }
            result = neo4j_conn.execute_query(query, params)
            logger.info(f"Pruning completed for {len(current_issue_keys)} issues")


def validate_environment():
    """Validate required environment variables"""
    missing = []
    
    if not JIRA_TOKEN:
        missing.append("JIRA_TOKEN")
    if not NEO4J_PASSWORD:
        missing.append("NEO4J_PASSWORD")
    
    if missing:
        logger.error(f"Missing required environment variables: {', '.join(missing)}")
        sys.exit(1)


def main():
    """Main data loading process"""
    import argparse
    
    parser = argparse.ArgumentParser(description="Load JIRA CIPOE data into Neo4j")
    parser.add_argument("--page-size", type=int, default=DEFAULT_PAGE_SIZE,
                       help="Number of issues to process per page")
    parser.add_argument("--max-results", type=int, default=DEFAULT_MAX_RESULTS,
                       help="Maximum number of issues to process")
    parser.add_argument("--start-at", type=int, default=0,
                       help="Starting offset for pagination")
    parser.add_argument("--dry-run", action="store_true",
                       help="Test connections without modifying data")
    
    args = parser.parse_args()
    
    # Validate environment
    validate_environment()
    
    # Initialize connections
    logger.info("Connecting to Neo4j...")
    neo4j_conn = None
    jira_client = None
    
    try:
        # Handle Neo4j password format (extract after / if present)
        neo4j_password = NEO4J_PASSWORD
        if '/' in neo4j_password:
            neo4j_password = neo4j_password.split('/')[-1]
            
        neo4j_conn = Neo4jConnection(NEO4J_URI, NEO4J_USERNAME, neo4j_password)
        jira_client = JiraClient(JIRA_BASE_URL, JIRA_TOKEN)
        
        # Test connections
        logger.info("Testing Neo4j connection...")
        neo4j_conn.execute_query("RETURN 1 as test")
        
        logger.info("Testing JIRA connection...")
        test_search = jira_client.search_issues(JQL_QUERY, start_at=0, max_results=1)
        logger.info(f"JIRA connection successful. Total issues found: {test_search.get('total', 'unknown')}")
        
        if args.dry_run:
            logger.info("Dry run completed successfully")
            return
        
        # Create constraint
        logger.info("Creating Neo4j constraints...")
        neo4j_conn.create_constraint()
        
        # Start data loading
        all_rows = []
        total_fetched = 0
        total_after_filter = 0
        start_at = args.start_at
        
        logger.info(f"Using JQL query: {JQL_QUERY}")
        logger.info(f"Prune mode: {'enabled' if PRUNE else 'disabled'}")
        
        while total_fetched < args.max_results:
            page_size = min(args.page_size, args.max_results - total_fetched)
            
            logger.info(f"Fetching issues: offset={start_at}, limit={page_size}")
            
            try:
                search_result = jira_client.search_issues(JQL_QUERY, start_at=start_at, max_results=page_size)
                issues = search_result.get("issues", [])
                
                if not issues:
                    logger.info("No more issues found")
                    break
                
                total_fetched += len(issues)
                
                # Apply CIPOE title prefix filter
                filtered_issues = [issue for issue in issues if is_cipoe_title_prefix(issue)]
                
                # Map to rows
                for issue in filtered_issues:
                    row = map_issue_to_row(issue)
                    all_rows.append(row)
                    total_after_filter += 1
                
                logger.info(f"Batch: fetched {len(issues)}, kept {len(filtered_issues)} after CIPOE filter")
                
                # Update pagination
                start_at += len(issues)
                
                # Rate limiting
                time.sleep(1)
                
            except Exception as e:
                logger.error(f"Error processing page starting at {start_at}: {e}")
                break
        
        logger.info(f"Total fetched from JIRA: {total_fetched}")
        logger.info(f"Total after prefix filter: {total_after_filter}")
        
        # Preview first 3 kept issues
        if all_rows:
            logger.info("Preview of first 3 kept issues:")
            for i, row in enumerate(all_rows[:3]):
                logger.info(f"  {i+1}. {row['key']}: {row['props']['summary']} (impacted by {len(row['impactedKeys'])} issues)")
        
        # Upsert to Neo4j
        if all_rows:
            logger.info(f"Upserting {len(all_rows)} issues to Neo4j...")
            upsert_issues_and_relationships(neo4j_conn, all_rows, prune=PRUNE)
            
            # Log statistics
            stats_query = """
            MATCH (i:Issue) 
            WITH count(i) as issue_count
            MATCH ()-[r:IMPACTED_BY]->()
            RETURN issue_count, count(r) as relationship_count
            """
            
            result = list(neo4j_conn.execute_query(stats_query))
            if result:
                stats = result[0]
                logger.info(f"Graph statistics - Issue nodes: {stats['issue_count']}, "
                           f"IMPACTED_BY relationships: {stats['relationship_count']}")
        else:
            logger.info("No issues matched the CIPOE title prefix filter")
        
        logger.info("Data loading completed successfully")
    
    except Exception as e:
        logger.error(f"Data loading failed: {e}")
        sys.exit(1)
    
    finally:
        if neo4j_conn:
            neo4j_conn.close()


if __name__ == "__main__":
    main()
