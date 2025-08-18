# Neo4j Data Browsing Guide

## Overview
Your JIRA CIPOE Analytics platform has successfully loaded 500 CIPOE issues into Neo4j. This guide explains how to access and explore the data using various methods.

## 1. Neo4j Browser Access

### Port Forward to Neo4j Service
```bash
# Forward Neo4j HTTP port (7474) to your local machine
kubectl port-forward -n jira-cipoe-analytics service/neo4j 7474:7474

# In a separate terminal, also forward the Bolt port (7687) for direct connections
kubectl port-forward -n jira-cipoe-analytics service/neo4j 7687:7687
```

### Access Neo4j Browser
1. Open your web browser and navigate to: `http://localhost:7474`
2. Login credentials:
   - **Connect URL**: `neo4j://localhost:7687`
   - **Username**: `neo4j`
   - **Password**: `password` (as configured in the deployment)

## 2. Sample Cypher Queries

### Basic Data Exploration

#### Count all nodes and relationships
```cypher
// Count all nodes
MATCH (n) RETURN count(n) as total_nodes

// Count all relationships
MATCH ()-[r]->() RETURN count(r) as total_relationships

// Count by node type
MATCH (n) RETURN labels(n) as node_type, count(n) as count
```

#### View CIPOE issues
```cypher
// Show first 10 CIPOE issues
MATCH (issue:Issue) 
WHERE issue.key CONTAINS 'CIPOE'
RETURN issue.key, issue.summary, issue.status, issue.priority 
LIMIT 10

// Show CIPOE issues with their relationships
MATCH (cipoe:Issue)-[r]-(related:Issue)
WHERE cipoe.key CONTAINS 'CIPOE'
RETURN cipoe.key, cipoe.summary, type(r), related.key, related.summary
LIMIT 20
```

#### Analyze issue priorities and statuses
```cypher
// Count issues by priority
MATCH (issue:Issue)
WHERE issue.key CONTAINS 'CIPOE'
RETURN issue.priority, count(issue) as count
ORDER BY count DESC

// Count issues by status
MATCH (issue:Issue)
WHERE issue.key CONTAINS 'CIPOE'
RETURN issue.status, count(issue) as count
ORDER BY count DESC
```

#### Find highly connected issues
```cypher
// Find CIPOE issues with most relationships
MATCH (cipoe:Issue)-[r]-()
WHERE cipoe.key CONTAINS 'CIPOE'
RETURN cipoe.key, cipoe.summary, count(r) as relationship_count
ORDER BY relationship_count DESC
LIMIT 10
```

### Advanced Analysis Queries

#### Find blocked issues and their blockers
```cypher
// Find blocked CIPOE issues
MATCH (blocked:Issue)-[:BLOCKS]-(blocker:Issue)
WHERE blocked.key CONTAINS 'CIPOE'
RETURN blocked.key, blocked.summary, blocker.key, blocker.summary
```

#### Identify duplicate or related issues
```cypher
// Find duplicates and relates relationships
MATCH (cipoe:Issue)-[r:DUPLICATES|RELATES]->(other:Issue)
WHERE cipoe.key CONTAINS 'CIPOE'
RETURN cipoe.key, type(r), other.key, other.summary
```

#### Component analysis
```cypher
// Show issues grouped by components
MATCH (issue:Issue)
WHERE issue.key CONTAINS 'CIPOE' AND issue.components IS NOT NULL
RETURN issue.components, count(issue) as issue_count
ORDER BY issue_count DESC
```

## 3. Visual Graph Exploration

### View issue relationships as a graph
```cypher
// Visualize a subset of CIPOE issues and their connections
MATCH path = (cipoe:Issue)-[r]-(connected:Issue)
WHERE cipoe.key CONTAINS 'CIPOE'
RETURN path
LIMIT 50
```

### Focus on specific issue types or priorities
```cypher
// Show high-priority CIPOE issues and their connections
MATCH path = (cipoe:Issue)-[r]-(connected:Issue)
WHERE cipoe.key CONTAINS 'CIPOE' 
  AND cipoe.priority IN ['Highest', 'High']
RETURN path
LIMIT 30
```

## 4. Alternative Access Methods

### Using Neo4j Desktop (if installed locally)
1. Download and install Neo4j Desktop
2. Create a new database connection:
   - Connect URL: `neo4j://localhost:7687`
   - Username: `neo4j`
   - Password: `password`

### Using Python for Programmatic Access
```python
from neo4j import GraphDatabase

# Connect to Neo4j
driver = GraphDatabase.driver("neo4j://localhost:7687", auth=("neo4j", "password"))

def run_query(query):
    with driver.session() as session:
        result = session.run(query)
        return [record for record in result]

# Example: Get CIPOE issue count
result = run_query("MATCH (n:Issue) WHERE n.key CONTAINS 'CIPOE' RETURN count(n) as count")
print(f"Total CIPOE issues: {result[0]['count']}")

driver.close()
```

## 5. Troubleshooting

### If Neo4j Browser won't load:
```bash
# Check if Neo4j pod is running
kubectl get pods -n jira-cipoe-analytics

# Check Neo4j service
kubectl get svc -n jira-cipoe-analytics

# Check logs for issues
kubectl logs -n jira-cipoe-analytics deployment/neo4j
```

### If data appears empty:
```bash
# Check the initial data load job status
kubectl get jobs -n jira-cipoe-analytics

# Check job logs
kubectl logs -n jira-cipoe-analytics job/initial-data-load

# Verify the cronjob is working
kubectl get cronjobs -n jira-cipoe-analytics
```

## 6. Regular Maintenance

### Data Updates
- The system runs incremental updates every 6 hours via CronJob
- Manual updates can be triggered by running a new Job based on the CronJob spec

### Monitoring Data Growth
```cypher
// Check when data was last updated (if timestamp fields exist)
MATCH (n:Issue) 
WHERE n.updated IS NOT NULL 
RETURN max(n.updated) as latest_update

// Monitor database size
CALL apoc.monitor.store()
```

## Next Steps
1. Start with basic exploration queries to understand your data structure
2. Use the visual graph interface to identify interesting patterns
3. Create custom queries based on your specific analysis needs
4. Consider setting up dashboards using tools like Neo4j Bloom or Grafana for ongoing monitoring
