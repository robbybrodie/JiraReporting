# Quick Start Guide

## 🚀 Deploy in 5 Minutes

### Prerequisites
- ROSA cluster with ArgoCD installed
- JIRA Personal Access Token
- `oc` CLI configured

### Step 1: Create Secrets
```bash
oc new-project jira-cipoe-analytics

oc create secret generic jira-credentials \
  --from-literal=JIRA_BASE_URL="https://issues.redhat.com" \
  --from-literal=JIRA_TOKEN="YOUR_JIRA_TOKEN" \
  -n jira-cipoe-analytics

oc create secret generic neo4j-auth \
  --from-literal=NEO4J_AUTH="neo4j/YourSecurePassword123" \
  -n jira-cipoe-analytics
```

### Step 2: Deploy Application
```bash
oc apply -f argocd/application.yaml
```

### Step 3: Monitor Deployment
```bash
# Watch ArgoCD sync
oc get application jira-cipoe-analytics -n openshift-gitops -w

# Monitor pods
oc get pods -n jira-cipoe-analytics -w
```

### Step 4: Access Neo4j Browser
```bash
# Get browser URL
echo "https://$(oc get route neo4j-browser -n jira-cipoe-analytics -o jsonpath='{.spec.host}')"

# Login with: neo4j / YourSecurePassword123
```

### Step 5: Load Data
```bash
# Trigger initial data load
oc create job --from=job/initial-cipoe-data-load \
  initial-load-$(date +%Y%m%d-%H%M%S) \
  -n jira-cipoe-analytics

# Watch progress
oc logs -f job/initial-load-$(date +%Y%m%d-%H%M%S) -n jira-cipoe-analytics
```

## 🎯 Quick Analysis

Once data is loaded, try these queries in Neo4j Browser:

```cypher
// Show total counts
MATCH (c:CIPOE) WITH count(c) as cipoe_count
MATCH (i:Issue) WITH cipoe_count, count(i) as issue_count  
MATCH ()-[r:IMPACTS]->() 
RETURN cipoe_count, issue_count, count(r) as relationships;

// Top impacted CIPOE tickets
MATCH (c:CIPOE)<-[r:IMPACTS]-()
RETURN c.key, c.summary, count(r) as impact_count
ORDER BY impact_count DESC LIMIT 10;
```

## 🔧 Common Commands

```bash
# Check application status
oc get application jira-cipoe-analytics -n openshift-gitops

# Restart Neo4j
oc rollout restart deployment/neo4j -n jira-cipoe-analytics

# Manual data sync
oc create job --from=cronjob/incremental-cipoe-data-load manual-sync-$(date +%s) -n jira-cipoe-analytics

# View logs
oc logs -f deployment/neo4j -n jira-cipoe-analytics
```

## 📊 Dashboard

Neo4j Browser provides powerful visualization:
1. **Graph View**: Visual relationship mapping
2. **Table View**: Structured query results  
3. **Code View**: Raw JSON/CSV export

## 🛠 Troubleshooting

| Issue | Command | Solution |
|-------|---------|----------|
| Neo4j not starting | `oc describe pod neo4j-xxx` | Check PVC/resources |
| Job failing | `oc logs job/xxx` | Verify secrets |
| No data | Check JIRA token | Verify permissions |

## 📈 Scaling

For production workloads:
- Increase Neo4j resources (2Gi+ memory)
- Adjust page sizes based on JIRA limits
- Use multiple parallel jobs for initial load

## 🔒 Security

- Secrets are cluster-local only
- No credentials in git  
- HTTPS enforced on routes
- Non-root containers
