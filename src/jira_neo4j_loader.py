#!/usr/bin/env python3
"""
JIRA CIPOE to Neo4j Graph Database Loader

This script queries JIRA for CIPOE issues and their linked issues,
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
from typing import Dict, Any, List, Optional
from urllib.parse import urlparse

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

# JIRA API configuration
SEARCH_FIELDS = ["key", "summary", "issuelinks", "issuetype", "status", "created", "updated"]
SEARCH_ENDPOINT = "/rest/api/2/search"
ISSUE_ENDPOINT = "/rest/api/2/issue/{key}"

# Pagination settings
DEFAULT_PAGE_SIZE = 100
DEFAULT_MAX_RESULTS = 1000

# Logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s"
)
logger = logging.getLogger("cipoe-neo4j-loader")


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
    
    def create_indexes(self):
        """Create necessary indexes for performance"""
        queries = [
            "CREATE INDEX cipoe_key_idx IF NOT EXISTS FOR (c:CIPOE) ON (c.key)",
            "CREATE INDEX issue_key_idx IF NOT EXISTS FOR (i:Issue) ON (i.key)",
            "CREATE INDEX cipoe_status_idx IF NOT EXISTS FOR (c:CIPOE) ON (c.status)",
            "CREATE INDEX issue_status_idx IF NOT EXISTS FOR (i:Issue) ON (i.status)",
        ]
        for query in queries:
            try:
                self.execute_query(query)
                logger.info(f"Index created: {query}")
            except Exception as e:
                logger.warning(f"Index creation failed: {e}")


class JiraClient:
    """JIRA API client with retry logic"""
    
    def __init__(self, base_url: str, token: str):
        self.base_url = base_url
        self.headers = {
            "Authorization": f"Bearer {token}",
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
    
    def search_cipoe(self, start_at: int = 0, max_results: int = 100, include_zero_links: bool = False) -> Dict[str, Any]:
        """Search for CIPOE issues with pagination
        
        Args:
            start_at: Starting index for pagination
            max_results: Maximum number of results per page
            include_zero_links: If False (default), only return CIPOE tickets with linked issues
        """
        url = f"{self.base_url}{SEARCH_ENDPOINT}"
        
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
            "maxResults": max_results,
        }
        
        resp = self.safe_request("POST", url, json=payload)
        resp.raise_for_status()
        return resp.json()
    
    def get_issue(self, key: str) -> Dict[str, Any]:
        """Get detailed issue information"""
        url = f"{self.base_url}{ISSUE_ENDPOINT.format(key=key)}"
        params = {"fields": ",".join(SEARCH_FIELDS)}
        
        resp = self.safe_request("GET", url, params=params)
        if resp.status_code == 404:
            logger.warning(f"Issue {key} not found")
            return None
        
        resp.raise_for_status()
        return resp.json()


class GraphBuilder:
    """Build and populate the Neo4j graph with JIRA data"""
    
    def __init__(self, neo4j_conn: Neo4jConnection):
        self.neo4j = neo4j_conn
    
    def create_cipoe_node(self, issue_data: Dict[str, Any]):
        """Create or update a CIPOE node"""
        fields = issue_data.get("fields", {})
        
        query = """
        MERGE (c:CIPOE {key: $key})
        SET c.summary = $summary,
            c.status = $status,
            c.issueType = $issueType,
            c.created = $created,
            c.updated = $updated,
            c.lastSync = datetime()
        RETURN c
        """
        
        params = {
            "key": issue_data.get("key"),
            "summary": fields.get("summary"),
            "status": (fields.get("status") or {}).get("name"),
            "issueType": (fields.get("issuetype") or {}).get("name"),
            "created": fields.get("created"),
            "updated": fields.get("updated"),
        }
        
        self.neo4j.execute_query(query, params)
        logger.debug(f"Created/updated CIPOE node: {params['key']}")
    
    def create_linked_issue_node(self, issue_key: str, issue_data: Dict[str, Any] = None):
        """Create or update a linked issue node"""
        if issue_data is None:
            # Minimal node with just the key
            query = """
            MERGE (i:Issue {key: $key})
            SET i.lastSync = datetime()
            RETURN i
            """
            params = {"key": issue_key}
        else:
            fields = issue_data.get("fields", {})
            query = """
            MERGE (i:Issue {key: $key})
            SET i.summary = $summary,
                i.status = $status,
                i.issueType = $issueType,
                i.created = $created,
                i.updated = $updated,
                i.lastSync = datetime()
            RETURN i
            """
            params = {
                "key": issue_data.get("key"),
                "summary": fields.get("summary"),
                "status": (fields.get("status") or {}).get("name"),
                "issueType": (fields.get("issuetype") or {}).get("name"),
                "created": fields.get("created"),
                "updated": fields.get("updated"),
            }
        
        self.neo4j.execute_query(query, params)
        logger.debug(f"Created/updated Issue node: {issue_key}")
    
    def create_relationship(self, cipoe_key: str, linked_key: str, relation_type: str, direction: str):
        """Create relationship between CIPOE and linked issue"""
        # Normalize relation type for consistent relationships
        normalized_relation = relation_type.upper().replace(" ", "_")
        
        if direction == "inward":
            # Linked issue affects CIPOE
            query = f"""
            MATCH (c:CIPOE {{key: $cipoe_key}})
            MATCH (i:Issue {{key: $linked_key}})
            MERGE (i)-[r:IMPACTS]->(c)
            SET r.relation = $relation_type,
                r.direction = $direction,
                r.created = datetime()
            RETURN r
            """
        else:
            # CIPOE affects linked issue
            query = f"""
            MATCH (c:CIPOE {{key: $cipoe_key}})
            MATCH (i:Issue {{key: $linked_key}})
            MERGE (c)-[r:IMPACTS]->(i)
            SET r.relation = $relation_type,
                r.direction = $direction,
                r.created = datetime()
            RETURN r
            """
        
        params = {
            "cipoe_key": cipoe_key,
            "linked_key": linked_key,
            "relation_type": relation_type,
            "direction": direction,
        }
        
        self.neo4j.execute_query(query, params)
        logger.debug(f"Created relationship: {cipoe_key} -> {linked_key} ({relation_type})")
    
    def process_issue_links(self, jira_client: JiraClient, issue_data: Dict[str, Any], skip_zero_links: bool = True):
        """Process all links for a CIPOE issue"""
        cipoe_key = issue_data.get("key")
        fields = issue_data.get("fields", {})
        issue_links = fields.get("issuelinks", []) or []
        
        # Validation: Skip tickets with zero links if filtering is enabled
        if skip_zero_links and len(issue_links) == 0:
            logger.warning(f"Skipping CIPOE {cipoe_key} - no linked issues found (this shouldn't happen with JQL filtering)")
            return
        
        logger.info(f"Processing {len(issue_links)} links for {cipoe_key}")
        
        for link in issue_links:
            link_type = (link.get("type") or {}).get("name", "") or ""
            relation_inward = (link.get("type") or {}).get("inward", "") or ""
            relation_outward = (link.get("type") or {}).get("outward", "") or ""
            
            # Check if this is an impact relationship
            is_impact = (
                link_type.lower() == "impacts" or
                "impact" in relation_inward.lower() or
                "impact" in relation_outward.lower() or
                "impact" in link_type.lower()
            )
            
            if not is_impact:
                logger.debug(f"Skipping non-impact link: {link_type}")
                continue
            
            # Process inward issue (affects this CIPOE)
            if "inwardIssue" in link:
                inward_issue = link["inwardIssue"]
                linked_key = inward_issue.get("key")
                
                # Create the linked issue node
                self.create_linked_issue_node(linked_key)
                
                # Try to get more details if not already present
                linked_summary = (inward_issue.get("fields") or {}).get("summary")
                if not linked_summary:
                    detailed_issue = jira_client.get_issue(linked_key)
                    if detailed_issue:
                        self.create_linked_issue_node(linked_key, detailed_issue)
                
                # Create relationship
                self.create_relationship(cipoe_key, linked_key, relation_inward or "impacts", "inward")
            
            # Process outward issue (this CIPOE affects)
            if "outwardIssue" in link:
                outward_issue = link["outwardIssue"]
                linked_key = outward_issue.get("key")
                
                # Create the linked issue node
                self.create_linked_issue_node(linked_key)
                
                # Try to get more details if not already present
                linked_summary = (outward_issue.get("fields") or {}).get("summary")
                if not linked_summary:
                    detailed_issue = jira_client.get_issue(linked_key)
                    if detailed_issue:
                        self.create_linked_issue_node(linked_key, detailed_issue)
                
                # Create relationship
                self.create_relationship(cipoe_key, linked_key, relation_outward or "impacts", "outward")


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
    parser.add_argument("--include-zero-links", action="store_true",
                       help="Include CIPOE tickets with zero linked issues (default: exclude them)")
    
    args = parser.parse_args()
    
    # Validate environment
    validate_environment()
    
    # Initialize connections
    logger.info("Connecting to Neo4j...")
    neo4j_conn = None
    jira_client = None
    
    try:
        neo4j_conn = Neo4jConnection(NEO4J_URI, NEO4J_USERNAME, NEO4J_PASSWORD)
        jira_client = JiraClient(JIRA_BASE_URL, JIRA_TOKEN)
        graph_builder = GraphBuilder(neo4j_conn)
        
        # Test connections
        logger.info("Testing Neo4j connection...")
        neo4j_conn.execute_query("RETURN 1 as test")
        
        logger.info("Testing JIRA connection...")
        test_search = jira_client.search_cipoe(start_at=0, max_results=1, include_zero_links=args.include_zero_links)
        logger.info(f"JIRA connection successful. Total CIPOE issues: {test_search.get('total', 'unknown')}")
        
        if args.dry_run:
            logger.info("Dry run completed successfully")
            return
        
        # Create indexes
        logger.info("Creating Neo4j indexes...")
        neo4j_conn.create_indexes()
        
        # Start data loading
        total_processed = 0
        start_at = args.start_at
        
        while total_processed < args.max_results:
            page_size = min(args.page_size, args.max_results - total_processed)
            
            logger.info(f"Fetching CIPOE issues: offset={start_at}, limit={page_size}")
            
            try:
                search_result = jira_client.search_cipoe(start_at=start_at, max_results=page_size, include_zero_links=args.include_zero_links)
                issues = search_result.get("issues", [])
                
                if not issues:
                    logger.info("No more issues found")
                    break
                
                # Process each issue
                for issue in issues:
                    cipoe_key = issue.get("key")
                    logger.info(f"Processing CIPOE: {cipoe_key}")
                    
                    # Create CIPOE node
                    graph_builder.create_cipoe_node(issue)
                    
                    # Process linked issues (skip zero links unless explicitly included)
                    graph_builder.process_issue_links(jira_client, issue, skip_zero_links=not args.include_zero_links)
                    
                    total_processed += 1
                
                # Update pagination
                start_at += len(issues)
                
                # Rate limiting
                time.sleep(1)
                
            except Exception as e:
                logger.error(f"Error processing page starting at {start_at}: {e}")
                break
        
        logger.info(f"Data loading completed. Processed {total_processed} CIPOE issues.")
        
        # Log some statistics
        stats_query = """
        MATCH (c:CIPOE) 
        WITH count(c) as cipoe_count
        MATCH (i:Issue)
        WITH cipoe_count, count(i) as issue_count
        MATCH ()-[r:IMPACTS]->()
        RETURN cipoe_count, issue_count, count(r) as relationship_count
        """
        
        result = list(neo4j_conn.execute_query(stats_query))
        if result:
            stats = result[0]
            logger.info(f"Graph statistics - CIPOE nodes: {stats['cipoe_count']}, "
                       f"Issue nodes: {stats['issue_count']}, "
                       f"Relationships: {stats['relationship_count']}")
    
    except Exception as e:
        logger.error(f"Data loading failed: {e}")
        sys.exit(1)
    
    finally:
        if neo4j_conn:
            neo4j_conn.close()


if __name__ == "__main__":
    main()
