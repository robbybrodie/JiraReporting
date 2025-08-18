#!/bin/bash

# JIRA CIPOE Analytics - Secret Creation Script
# This script creates the required OpenShift secrets for the application

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

NAMESPACE="jira-cipoe-analytics"

echo -e "${GREEN}🔐 JIRA CIPOE Analytics - Secret Creation${NC}"
echo "================================================"

# Check if oc is installed
if ! command -v oc &> /dev/null; then
    echo -e "${RED}❌ OpenShift CLI (oc) is not installed or not in PATH${NC}"
    echo "Please install the OpenShift CLI and try again."
    exit 1
fi

# Check if logged into OpenShift
if ! oc whoami &> /dev/null; then
    echo -e "${RED}❌ Not logged into OpenShift cluster${NC}"
    echo "Please login using: oc login <your-cluster-url>"
    exit 1
fi

echo -e "${GREEN}✅ OpenShift CLI detected and logged in as: $(oc whoami)${NC}"
echo

# Create namespace if it doesn't exist
echo -e "${YELLOW}📦 Creating namespace: ${NAMESPACE}${NC}"
oc new-project ${NAMESPACE} 2>/dev/null || oc project ${NAMESPACE}
echo

# Check for secrets.env file
echo -e "${YELLOW}🔍 Checking for secrets.env file...${NC}"
if [[ ! -f "secrets.env" ]]; then
    echo -e "${RED}❌ secrets.env file not found!${NC}"
    echo
    echo "Please create a secrets.env file in the project root with the following format:"
    echo
    echo "JIRA_BASE_URL=https://issues.redhat.com"
    echo "JIRA_TOKEN=your_personal_access_token_here"
    echo "NEO4J_PASSWORD=your_secure_neo4j_password_here"
    echo
    echo "You can copy secrets.env.example to secrets.env and populate it with your values."
    exit 1
fi

# Source the secrets file
echo -e "${GREEN}✅ Found secrets.env file${NC}"
source secrets.env

# Validate that secrets are populated (not placeholder values)
echo -e "${YELLOW}🔍 Validating secrets...${NC}"

if [[ -z "$JIRA_BASE_URL" ]] || echo "$JIRA_BASE_URL" | grep -q "YOUR_.*_HERE"; then
    echo -e "${RED}❌ JIRA_BASE_URL is not properly configured in secrets.env${NC}"
    echo "Please set a valid JIRA base URL"
    exit 1
fi

if [[ -z "$JIRA_TOKEN" ]] || echo "$JIRA_TOKEN" | grep -q "YOUR_.*_HERE"; then
    echo -e "${RED}❌ JIRA_TOKEN is not properly configured in secrets.env${NC}"
    echo "Please set your JIRA Personal Access Token"
    exit 1
fi

if [[ -z "$NEO4J_PASSWORD" ]] || echo "$NEO4J_PASSWORD" | grep -q "YOUR_.*_HERE"; then
    echo -e "${RED}❌ NEO4J_PASSWORD is not properly configured in secrets.env${NC}"
    echo "Please set a secure Neo4j password"
    exit 1
fi

echo -e "${GREEN}✅ All secrets validated successfully${NC}"

echo -e "${YELLOW}🔧 Creating secrets...${NC}"

# Create JIRA credentials secret
echo "Creating JIRA credentials secret..."
oc create secret generic jira-credentials \
  --from-literal=JIRA_BASE_URL="${JIRA_BASE_URL}" \
  --from-literal=JIRA_TOKEN="${JIRA_TOKEN}" \
  --namespace=${NAMESPACE} \
  --dry-run=client -o yaml | oc apply -f -

if [[ $? -eq 0 ]]; then
    echo -e "${GREEN}✅ JIRA credentials secret created${NC}"
else
    echo -e "${RED}❌ Failed to create JIRA credentials secret${NC}"
    exit 1
fi

# Create Neo4j auth secret
echo "Creating Neo4j auth secret..."
oc create secret generic neo4j-auth \
  --from-literal=NEO4J_AUTH="neo4j/${NEO4J_PASSWORD}" \
  --namespace=${NAMESPACE} \
  --dry-run=client -o yaml | oc apply -f -

if [[ $? -eq 0 ]]; then
    echo -e "${GREEN}✅ Neo4j auth secret created${NC}"
else
    echo -e "${RED}❌ Failed to create Neo4j auth secret${NC}"
    exit 1
fi

echo
echo -e "${GREEN}🎉 All secrets created successfully!${NC}"
echo
echo -e "${YELLOW}📋 Next Steps:${NC}"
echo "1. Deploy the application:"
echo "   oc apply -f argocd/application.yaml"
echo
echo "2. Monitor the deployment:"
echo "   oc get application jira-cipoe-analytics -n openshift-gitops -w"
echo
echo "3. Once deployed, access Neo4j Browser:"
echo "   oc get route neo4j-browser -n ${NAMESPACE} -o jsonpath='{.spec.host}'"
echo
echo "4. Login to Neo4j Browser with:"
echo "   Username: neo4j"
echo "   Password: [the password you just created]"
echo
echo -e "${GREEN}🚀 Happy analyzing!${NC}"
