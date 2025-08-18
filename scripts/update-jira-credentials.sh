#!/bin/bash
"""
Update JIRA Credentials for CIPOE Analytics Platform

This script updates the JIRA authentication token in the Kubernetes secret.
Run this script after deploying the platform to set up JIRA access.

Usage:
  ./scripts/update-jira-credentials.sh <YOUR_JIRA_TOKEN>

Example:
  ./scripts/update-jira-credentials.sh "your-actual-jira-token"
"""

set -e

NAMESPACE="jira-cipoe-analytics"

if [ $# -ne 1 ]; then
    echo "Usage: $0 <JIRA_TOKEN>"
    echo ""
    echo "Example: $0 'your-actual-jira-token'"
    echo ""
    echo "Get your JIRA token from: https://id.atlassian.com/manage-profile/security/api-tokens"
    exit 1
fi

JIRA_TOKEN="$1"

echo "Updating JIRA credentials in namespace: $NAMESPACE"

# Check if namespace exists
if ! kubectl get namespace "$NAMESPACE" > /dev/null 2>&1; then
    echo "Error: Namespace '$NAMESPACE' does not exist."
    echo "Please deploy the platform first using ArgoCD."
    exit 1
fi

# Check if secret exists
if ! kubectl get secret jira-credentials -n "$NAMESPACE" > /dev/null 2>&1; then
    echo "Error: Secret 'jira-credentials' does not exist in namespace '$NAMESPACE'."
    echo "Please deploy the platform first using ArgoCD."
    exit 1
fi

# Update the JIRA token
echo "Updating JIRA_TOKEN in secret..."
kubectl patch secret jira-credentials -n "$NAMESPACE" \
    --type merge \
    -p="{\"data\":{\"JIRA_TOKEN\":\"$(echo -n "$JIRA_TOKEN" | base64)\"}}"

echo "✅ JIRA credentials updated successfully!"
echo ""
echo "You can now test the data loading with:"
echo "kubectl create job --from=cronjob/incremental-cipoe-data-load test-jira-connection -n $NAMESPACE"
echo ""
echo "Monitor the job logs with:"
echo "kubectl logs job/test-jira-connection -n $NAMESPACE"
