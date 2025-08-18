#!/bin/bash

# Complete deployment script for JIRA CIPOE Analytics
set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

echo -e "${BLUE}🚀 JIRA CIPOE Analytics - Complete Deployment${NC}"
echo "=================================================="

# Step 1: Create secrets
echo -e "${YELLOW}Step 1: Creating OpenShift secrets...${NC}"
if ./scripts/create-secrets.sh; then
    echo -e "${GREEN}✅ Secrets created successfully${NC}"
else
    echo -e "${RED}❌ Failed to create secrets${NC}"
    exit 1
fi

echo

# Step 2: Deploy application
echo -e "${YELLOW}Step 2: Deploying ArgoCD application...${NC}"
if oc apply -f argocd/application.yaml; then
    echo -e "${GREEN}✅ ArgoCD application deployed${NC}"
else
    echo -e "${RED}❌ Failed to deploy ArgoCD application${NC}"
    exit 1
fi

echo

# Step 3: Monitor deployment
echo -e "${YELLOW}Step 3: Monitoring deployment progress...${NC}"
echo "Waiting for ArgoCD to sync..."

# Wait for application to exist
sleep 5

# Check application status
echo "Checking ArgoCD application status..."
oc get application jira-cipoe-analytics -n openshift-gitops || echo "Application not found yet..."

echo

# Step 4: Wait for Neo4j to be ready
echo -e "${YELLOW}Step 4: Waiting for Neo4j to be ready...${NC}"
echo "This may take a few minutes..."

# Wait for deployment to be available
oc wait --for=condition=available deployment/neo4j -n jira-cipoe-analytics --timeout=300s || true

echo

# Step 5: Get Neo4j Browser URL
echo -e "${YELLOW}Step 5: Getting Neo4j Browser access...${NC}"
NEO4J_URL=$(oc get route neo4j-browser -n jira-cipoe-analytics -o jsonpath='{.spec.host}' 2>/dev/null || echo "Route not ready yet")

if [[ "$NEO4J_URL" != "Route not ready yet" ]]; then
    echo -e "${GREEN}✅ Neo4j Browser available at: https://${NEO4J_URL}${NC}"
else
    echo -e "${YELLOW}⏳ Neo4j Browser route not ready yet. Check again in a few minutes.${NC}"
fi

echo

# Step 6: Instructions for data loading
echo -e "${YELLOW}Step 6: Next steps for data loading...${NC}"
echo
echo -e "${GREEN}🎯 Ready to load data!${NC}"
echo
echo "1. Trigger stomping data load:"
echo "   oc create job --from=job/cipoe-data-load \\"
echo "     cipoe-load-\$(date +%Y%m%d-%H%M%S) -n jira-cipoe-analytics"
echo
echo "2. Monitor data load progress:"
echo "   oc logs -f job/cipoe-load-TIMESTAMP -n jira-cipoe-analytics"
echo
echo "3. Access Neo4j Browser:"
if [[ "$NEO4J_URL" != "Route not ready yet" ]]; then
    echo "   https://${NEO4J_URL}"
else
    echo "   oc get route neo4j-browser -n jira-cipoe-analytics -o jsonpath='{.spec.host}'"
fi
echo
echo "4. Login to Neo4j with:"
echo "   Username: neo4j"
echo "   Password: [the password you created]"
echo
echo -e "${GREEN}🎉 Deployment complete!${NC}"
