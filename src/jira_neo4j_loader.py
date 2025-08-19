#!/usr/bin/env python3
"""
JIRA CIPOE to Neo4j Graph Database Loader

This script queries JIRA for issues whose summary starts with "CIPOE" and that have 
at least one "is impacted by" link, then stores the relationships in a Neo4j graph 
database for analysis.

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
from datetime import datetime, timedelta

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

# JQL Query from environment or default - now targets CIPOE project specifically
JQL_QUERY = os.getenv("JQL_QUERY", 'project = "CIPOE" ORDER BY updated DESC')
UPDATED_SINCE = os.getenv("UPDATED_SINCE")  # e.g., "2025-06-01" or "-30d"
PAGE_DELAY_MS = int(os.getenv("PAGE_DELAY_MS", "500"))
MAX_ISSUES = os.getenv("MAX_ISSUES")  # optional cap for pilots
PRUNE = os.getenv("PRUNE", "true").lower() == "true"
CHUNK = int(os.getenv("CHUNK", "500"))

# JIRA API configuration - specific fields only
SEARCH_FIELDS = ["summary", "status", "updated", "issuetype", "priority", "project", "issuelinks"]
SEARCH_ENDPOINT = "/rest/api/2/search"

# Pagination settings
MAX_RESULTS_PER_PAGE = 100

# Logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s"
)
logger = logging.getLogger("cipoe-neo4j-loader")


def is_cipoe_prefix(title: str) -> bool:
    """Check if issue summary starts with CIPOE (case-insensitive, anchored at start)"""
    return bool(re.match(r'^\s*CIPOE(\b|[\s:\-_/])', title or '', flags=re.IGNORECASE))


def extract_impacted_by_keys(issue: dict) -> list[str]:
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


def parse_updated_since(value: str) -> str:
    """Parse UPDATED_SINCE value and return formatted date"""
    if not value:
        return ""
    
    value = value.strip()
    
    # Handle relative dates like "-30d"
    if value.startswith('-') and value.endswith('d'):
        try:
            days = int(value[1:-1])
            target_date = datetime.now() - timedelta(days=days)
            return target_date.strftime('%Y-%m-%d')
        except ValueError:
            logger.warning(f"Invalid UPDATED_SINCE format: {value}")
            return ""
    
    # Handle absolute dates (assume already in correct format)
    return value


def build_jql_query() -> str:
    """Build final JQL query with optional UPDATED_SINCE clause"""
    query = JQL_QUERY
    
    if UPDATED_SINCE:
        updated_date = parse_updated_since(UPDATED_SINCE)
        if updated_date:
            query += f' AND updated >= "{updated_date}"'
    
    return query


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
            result = session.run(query, parameters or {})
            # Consume the result within the session context to avoid consumption errors
            return list(result)
    
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
    """JIRA API client with enhanced retry logic and rate limiting"""

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
        """Request with enhanced retry/backoff for rate limiting"""
        max_attempts = 5
        backoff = 0.5  # Start with 0.5s
        
        for attempt in range(1, max_attempts + 1):
            resp = self.session.request(method, url, timeout=30, headers=self.headers, **kwargs)
            
            if resp.status_code < 400:
                return resp
            
            if resp.status_code in (429, 502, 503) or 500 <= resp.status_code < 600:
                # Check for Retry-After header
                retry_after = resp.headers.get('Retry-After')
                if retry_after:
                    try:
                        delay = float(retry_after)
                    except ValueError:
                        delay = backoff
                else:
                    delay = backoff
                
                logger.warning(
                    f"Request to {url} returned {resp.status_code}. "
                    f"Attempt {attempt}/{max_attempts}. Backing off {delay}s"
                )
                time.sleep(delay)
                backoff = min(backoff * 2, 8)  # Double up to ~8s
                continue
            
            # Client errors - don't retry
            resp.raise_for_status()
        
        return resp
    
    def search_issues(self, jql: str, start_at: int = 0, max_results: int = MAX_RESULTS_PER_PAGE) -> Dict[str, Any]:
        """Search for issues using JQL with pagination"""
        url = f"{self.base_url}{SEARCH_ENDPOINT}"
        
        params = {
            "jql": jql,
            "fields": ",".join(SEARCH_FIELDS),
            "startAt": start_at,
            "maxResults": min(max_results, MAX_RESULTS_PER_PAGE),
        }
        
        resp = self.safe_request("GET", url, params=params)
        resp.raise_for_status()
        return resp.json()


def map_issue_to_row(issue: Dict[str, Any]) -> Dict[str, Any]:
    """Map JIRA issue to our row format"""
    f = issue.get("fields", {})
    impacted_keys = extract_impacted_by_keys(issue)
    
    return {
        "key": issue["key"],
        "props": {
            "summary": f.get("summary"),
            "status": (f.get("status") or {}).get("name"),
            "issuetype": (f.get("issuetype") or {}).get("name"),
            "updated": f.get("updated"),
            "priority": (f.get("priority") or {}).get("name"),
            "project": (f.get("project") or {}).get("key"),
            "key_url": f"{JIRA_BASE_URL}/browse/{issue['key']}",
        },
        "impactedKeys": impacted_keys
    }


def fetch_linked_issues_details(jira_client: JiraClient, linked_keys: set) -> Dict[str, Dict]:
    """Fetch full details for linked issues in batches"""
    if not linked_keys:
        return {}
    
    linked_details = {}
    keys_list = list(linked_keys)
    
    # Process in batches to avoid URL length limits
    batch_size = 50
    for i in range(0, len(keys_list), batch_size):
        batch = keys_list[i:i + batch_size]
        keys_query = " OR ".join([f'key = "{key}"' for key in batch])
        
        try:
            logger.info(f"Fetching details for {len(batch)} linked issues (batch {i//batch_size + 1})")
            result = jira_client.search_issues(keys_query, start_at=0, max_results=batch_size)
            
            for issue in result.get("issues", []):
                issue_key = issue["key"]
                f = issue.get("fields", {})
                
                linked_details[issue_key] = {
                    "key": issue_key,
                    "props": {
                        "summary": f.get("summary"),
                        "status": (f.get("status") or {}).get("name"),
                        "issuetype": (f.get("issuetype") or {}).get("name"),
                        "updated": f.get("updated"),
                        "priority": (f.get("priority") or {}).get("name"),
                        "project": (f.get("project") or {}).get("key"),
                        "key_url": f"{JIRA_BASE_URL}/browse/{issue_key}",
                    }
                }
            
            # Rate limiting
            if PAGE_DELAY_MS > 0:
                time.sleep(PAGE_DELAY_MS / 1000.0)
                
        except Exception as e:
            logger.warning(f"Error fetching batch {i//batch_size + 1}: {e}")
            continue
    
    logger.info(f"Successfully fetched details for {len(linked_details)} linked issues")
    return linked_details


def upsert_issues_and_relationships_chunked(neo4j_conn: Neo4jConnection, rows: List[Dict[str, Any]], linked_issues: Dict[str, Dict], prune: bool = True):
    """Upsert Issue nodes and IMPACTED_BY relationships in chunks"""
    if not rows:
        return
    
    # Process in chunks
    for i in range(0, len(rows), CHUNK):
        chunk = rows[i:i + CHUNK]
        
        # Prepare linked issues data for this chunk
        chunk_linked = []
        for row in chunk:
            for linked_key in row.get("impactedKeys", []):
                if linked_key in linked_issues:
                    chunk_linked.append(linked_issues[linked_key])
        
        # Upsert main issues and relationships
        main_query = """
        UNWIND $rows AS row
        MERGE (i:Issue {key: row.key})
        ON CREATE SET i.firstSeen = datetime(), i += row.props
        ON MATCH  SET i.lastSeen  = datetime(), i += row.props
        WITH i, coalesce(row.impactedKeys, []) AS impactList
        UNWIND impactList AS k
        MERGE (j:Issue {key: k})
        ON CREATE SET j.firstSeen = datetime()
        MERGE (i)-[r:IMPACTED_BY]->(j)
        ON CREATE SET r.firstSeen = datetime()
        SET r.lastSeen = datetime()
        """
        
        neo4j_conn.execute_query(main_query, {"rows": chunk})
        
        # Upsert detailed linked issues
        if chunk_linked:
            linked_query = """
            UNWIND $linked_issues AS linked
            MERGE (j:Issue {key: linked.key})
            ON CREATE SET j.firstSeen = datetime(), j += linked.props
            ON MATCH  SET j.lastSeen  = datetime(), j += linked.props
            """
            
            neo4j_conn.execute_query(linked_query, {"linked_issues": chunk_linked})
        
        logger.info(f"Upserted chunk {i//CHUNK + 1}: {len(chunk)} main issues, {len(chunk_linked)} linked issues")
    
    logger.info(f"Upserted {len(rows)} main issues total with linked issue details")
    
    # Optional pruning
    if prune:
        prune_query = """
        UNWIND $rows AS row
        MATCH (i:Issue {key: row.key})
        OPTIONAL MATCH (i)-[r:IMPACTED_BY]->(x:Issue)
        WHERE NOT x.key IN coalesce(row.impactedKeys, [])
        DELETE r
        """
        
        for i in range(0, len(rows), CHUNK):
            chunk = rows[i:i + CHUNK]
            neo4j_conn.execute_query(prune_query, {"rows": chunk})
        
        logger.info(f"Pruned stale edges for {len(rows)} rows")


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
    parser.add_argument("--dry-run", action="store_true",
                       help="Test connections without modifying data")
    
    args = parser.parse_args()
    
    # Validate environment
    validate_environment()
    
    # Build final JQL query
    final_jql = build_jql_query()
    
    # Show configuration
    logger.info(f"JQL_QUERY: {final_jql}")
    logger.info(f"UPDATED_SINCE: {UPDATED_SINCE or 'not set'}")
    logger.info(f"PAGE_DELAY_MS: {PAGE_DELAY_MS}")
    if MAX_ISSUES:
        logger.info(f"MAX_ISSUES: {MAX_ISSUES}")
    logger.info(f"PRUNE: {PRUNE}")
    logger.info(f"CHUNK: {CHUNK}")
    
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
        logger.info("Neo4j connection successful")
        
        logger.info("Testing JIRA connection...")
        test_search = jira_client.search_issues(final_jql, start_at=0, max_results=1)
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
        total_kept_after_filter = 0
        start_at = 0
        max_issues = int(MAX_ISSUES) if MAX_ISSUES else float('inf')
        
        while total_fetched < max_issues:
            if max_issues == float('inf'):
                page_size = MAX_RESULTS_PER_PAGE
            else:
                page_size = min(MAX_RESULTS_PER_PAGE, int(max_issues) - total_fetched)
            
            try:
                search_result = jira_client.search_issues(final_jql, start_at=start_at, max_results=page_size)
                issues = search_result.get("issues", [])
                
                if not issues:
                    logger.info("No more issues found")
                    break
                
                total_fetched += len(issues)
                
                # Apply filtering: CIPOE project issues that have "account is impacted by" links
                kept_issues = []
                for issue in issues:
                    # Since we're already filtering by project="CIPOE", all issues are CIPOE issues
                    impacted_keys = extract_impacted_by_keys(issue)
                    if len(impacted_keys) > 0:  # Must have at least one "account is impacted by" link
                        kept_issues.append(issue)
                
                # Map to rows
                for issue in kept_issues:
                    row = map_issue_to_row(issue)
                    all_rows.append(row)
                
                kept_count = len(kept_issues)
                total_kept_after_filter += kept_count
                
                logger.info(f"Page: fetched {len(issues)}, kept {kept_count} (has-account-links), total so far: {total_kept_after_filter}")
                
                # Update pagination
                start_at += len(issues)
                
                # Page delay
                if PAGE_DELAY_MS > 0:
                    time.sleep(PAGE_DELAY_MS / 1000.0)
                
            except Exception as e:
                logger.error(f"Error processing page starting at {start_at}: {e}")
                break
        
        logger.info(f"Final totals - Fetched: {total_fetched}, Kept with account links: {total_kept_after_filter}")
        
        # Preview first 3 kept rows
        if all_rows:
            logger.info("Preview of kept rows:")
            for i, row in enumerate(all_rows[:3]):
                summary = (row['props']['summary'] or '')[:60]
                logger.info(f"  {row['key']}: {summary} (#{len(row['impactedKeys'])} account-impacted-by)")
        
        # Collect all unique linked issue keys and fetch their details
        if all_rows:
            logger.info(f"Before write: {len(all_rows)} main rows to upsert")
            
            # Collect all unique linked issue keys
            all_linked_keys = set()
            for row in all_rows:
                all_linked_keys.update(row.get("impactedKeys", []))
            
            logger.info(f"Found {len(all_linked_keys)} unique linked issues to fetch details for")
            
            # Fetch details for all linked issues
            linked_issues_details = fetch_linked_issues_details(jira_client, all_linked_keys)
            
            # Upsert everything with linked issue details
            upsert_issues_and_relationships_chunked(neo4j_conn, all_rows, linked_issues_details, prune=PRUNE)
            
            # Log final statistics
            try:
                stats_query = """
                MATCH (i:Issue) 
                WITH count(i) as issue_count
                MATCH ()-[r:IMPACTED_BY]->()
                RETURN issue_count, count(r) as relationship_count
                """
                
                result = neo4j_conn.execute_query(stats_query)
                if result:
                    stats = result[0]
                    logger.info(f"Final graph - Issue nodes: {stats['issue_count']}, "
                               f"IMPACTED_BY relationships: {stats['relationship_count']}")
            except Exception as e:
                logger.warning(f"Could not retrieve final statistics (data was loaded successfully): {e}")
        else:
            logger.info("No issues matched the filtering criteria")
        
        logger.info("Data loading completed successfully")
    
    except Exception as e:
        logger.error(f"Data loading failed: {e}")
        sys.exit(1)
    
    finally:
        if neo4j_conn:
            neo4j_conn.close()


if __name__ == "__main__":
    main()
