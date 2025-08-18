# JIRA CIPOE Analytics Deployment Guide

## Overview

This guide walks through deploying the JIRA CIPOE Analytics platform on a ROSA (Red Hat OpenShift Service on AWS) cluster using ArgoCD for GitOps deployment.

## Prerequisites

1. **ROSA Cluster**: Active ROSA cluster with admin access
2. **ArgoCD**: OpenShift GitOps operator installed and configured
3. **JIRA Access**: Data Center Personal Access Token with read permissions
4. **Git Repository**: Fork/clone of this repository

## Architecture

```
┌─────────────────┐    ┌─────────────────┐    ┌─────────────────┐
│   JIRA Server   │    │   Neo4j Graph   │    │  Neo4j Browser  │
│                 │────┤     Database     │────┤      GUI        │
└─────────────────┘    └─────────────────┘    └─────────────────┘
         │                       │                       │
         └───────────────────────┼───────────────────────┘
                                 │
                    ┌─────────────────┐
                    │  Data Loader    │
                    │     Jobs        │
                    └─────────────────┘
```

## Deployment Steps

### 1. Prepare Secrets

**IMPORTANT**: Create secrets manually before deploying with ArgoCD.

```bash
# Create the namespace first
oc new-project jira-cipoe-analytics

# Create JIRA credentials secret
oc create secret generic jira-credentials \
  --from-literal=JIRA_BASE_URL="https://issues.redhat.com" \
  --from-literal=JIRA_TOKEN="YOUR_ACTUAL_JIRA_TOKEN" \
  -n jira-cipoe-analytics

# Create Neo4j auth secret
oc create secret generic neo4j-auth \
  --from-literal=NEO4J_AUTH="neo4j/YOUR_SECURE_PASSWORD" \
  -n jira-cipoe-analytics
```

### 2. Deploy via ArgoCD

```bash
# Apply the ArgoCD application
oc apply -f argocd/application.yaml
```

### 3. Monitor Deployment

ArgoCD will deploy components in the following order (sync waves):

- **Wave 0**: Namespace and basic resources
- **Wave 1**: Secrets (manual) and PVCs
- **Wave 2**: Neo4j database and ConfigMaps
- **Wave 3**: Services and Routes
- **Wave 4**: Data loading Jobs (manual trigger recommended)

Monitor the deployment:
```bash
# Check ArgoCD application status
oc get application jira-cipoe-analytics -n openshift-gitops

# Monitor pod deployment
oc get pods -n jira-cipoe-analytics -w

# Check Neo4j startup logs
oc logs -f deployment/neo4j -n jira-cipoe-analytics
```

### 4. Access Neo4j Browser

Once deployed, access the Neo4j browser:

```bash
# Get the route URL
oc get route neo4j-browser -n jira-cipoe-analytics -o jsonpath='{.spec.host}'

# Open in browser and login with your Neo4j credentials
# Username: neo4j
# Password: YOUR_SECURE_PASSWORD (from the secret)
```

### 5. Run Initial Data Load

**Manual trigger recommended for initial load:**

```bash
# Trigger the initial data load job
oc create job --from=job/initial-cipoe-data-load initial-load-$(date +%Y%m%d-%H%M%S) -n jira-cipoe-analytics

# Monitor the job progress
oc logs -f job/initial-load-$(date +%Y%m%d-%H%M%S) -n jira-cipoe-analytics
```

### 6. Verify Data Loading

Check that data is being loaded into Neo4j:

```bash
# Port forward to Neo4j for direct access (optional)
oc port-forward svc/neo4j 7474:7474 7687:7687 -n jira-cipoe-analytics
```

Run sample queries in Neo4j Browser:
```cypher
// Count total CIPOE issues
MATCH (c:CIPOE) RETURN count(c) as total_cipoe;

// Count relationships
MATCH ()-[r:IMPACTS]->() RETURN count(r) as total_relationships;

// Find CIPOE with most linked issues
MATCH (c:CIPOE)-[r:IMPACTS]-()
WITH c, count(r) as link_count
RETURN c.key, c.summary, link_count
ORDER BY link_count DESC
LIMIT 5;
```

## Configuration

### Environment Variables

The data loader supports the following configuration:

| Variable | Description | Default |
|----------|-------------|---------|
| `JIRA_BASE_URL` | JIRA server URL | `https://issues.redhat.com` |
| `JIRA_TOKEN` | Personal Access Token | Required |
| `NEO4J_URI` | Neo4j connection URI | `bolt://neo4j:7687` |
| `NEO4J_USERNAME` | Neo4j username | `neo4j` |
| `NEO4J_PASSWORD` | Neo4j password | Required |

### Data Loader Parameters

| Parameter | Description | Default |
|-----------|-------------|---------|
| `--page-size` | Issues per page | 50 |
| `--max-results` | Maximum issues to load | 1000 |
| `--start-at` | Starting offset | 0 |
| `--dry-run` | Test without loading data | false |

### Scheduled Updates

The incremental data loader runs every 6 hours via CronJob:
- Smaller page size (25) for efficiency
- Processes up to 500 issues per run
- Automatically handles rate limiting

## Troubleshooting

### Neo4j Won't Start

```bash
# Check PVC status
oc get pvc neo4j-data -n jira-cipoe-analytics

# Check events
oc get events -n jira-cipoe-analytics --sort-by='.lastTimestamp'

# Check Neo4j logs
oc logs deployment/neo4j -n jira-cipoe-analytics
```

### Data Loader Failures

```bash
# Check job logs
oc logs job/initial-cipoe-data-load -n jira-cipoe-analytics

# Check secret values (be careful not to expose in logs)
oc get secret jira-credentials -o yaml -n jira-cipoe-analytics

# Test JIRA connectivity
oc run curl-test --image=curlimages/curl --rm -it --restart=Never -- \
  curl -H "Authorization: Bearer YOUR_TOKEN" \
  https://issues.redhat.com/rest/api/2/search?jql=project=CIPOE&maxResults=1
```

### Performance Tuning

For large datasets, consider:

1. **Increase resources**:
   ```yaml
   resources:
     requests:
       memory: "2Gi"
       cpu: "1000m"
     limits:
       memory: "4Gi"
       cpu: "2000m"
   ```

2. **Adjust page sizes**:
   - Smaller pages for rate limiting: `--page-size 25`
   - Larger pages for efficiency: `--page-size 100`

3. **Parallel processing**:
   - Create multiple jobs with different `--start-at` offsets
   - Ensure no overlap in processed ranges

## Data Analysis Queries

### Sample Cypher Queries

```cypher
-- Find issues impacting multiple CIPOE tickets
MATCH (i:Issue)-[:IMPACTS]->(c:CIPOE)
WITH i, collect(c.key) as cipoe_keys, count(c) as cipoe_count
WHERE cipoe_count > 1
RETURN i.key, i.summary, cipoe_keys, cipoe_count
ORDER BY cipoe_count DESC;

-- Find CIPOE impact chains
MATCH (source:Issue)-[:IMPACTS]->(cipoe:CIPOE)-[:IMPACTS]->(target:Issue)
RETURN source.key as source_issue, 
       cipoe.key as cipoe_ticket,
       target.key as target_issue;

-- Status distribution
MATCH (c:CIPOE)
RETURN c.status, count(*) as count
ORDER BY count DESC;
```

## Security Notes

1. **Secrets**: Never commit actual secrets to git
2. **RBAC**: Ensure proper OpenShift RBAC is configured
3. **Network**: Neo4j is only accessible within the cluster
4. **Routes**: HTTPS termination is enforced
5. **Images**: Uses official Red Hat UBI base images

## Maintenance

### Backup

```bash
# Export Neo4j data
oc exec deployment/neo4j -n jira-cipoe-analytics -- \
  neo4j-admin database dump --to-path=/tmp neo4j

# Copy backup locally
oc cp jira-cipoe-analytics/neo4j-pod:/tmp/neo4j.dump ./neo4j-backup.dump
```

### Updates

ArgoCD will automatically sync changes from git. To update:

1. Modify manifests in git
2. Commit and push changes  
3. ArgoCD will detect and apply updates

### Monitoring

Monitor key metrics:
- Neo4j memory usage
- Job success/failure rates
- JIRA API rate limit status
- Data freshness timestamps

## Support

For issues:
1. Check ArgoCD application status
2. Review pod logs
3. Verify secret configurations
4. Test JIRA connectivity
5. Check Neo4j browser accessibility
