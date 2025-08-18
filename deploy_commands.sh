#!/bin/bash

# ROSA Deployment Commands - Copy and paste these into your terminal

echo "🚀 ROSA Deployment Commands for JIRA CIPOE Analytics"
echo "======================================================"
echo ""

echo "1. Login to your ROSA cluster:"
echo "oc login --username=cluster-admin --password=your-cluster-password https://api.rosa-x92n2.vbeb.p3.openshiftapps.com:443"
echo ""

echo "2. Set environment variables:"
echo 'export JIRA_BASE_URL="https://issues.redhat.com"'
echo 'export JIRA_TOKEN="your-jira-token-here"'
echo 'export NEO4J_PASSWORD="your-secure-password-here"'
echo ""

echo "3. Make scripts executable:"
echo "chmod +x scripts/deploy.sh scripts/create-secrets.sh"
echo ""

echo "4. Run the complete deployment:"
echo "./scripts/deploy.sh"
echo ""

echo "5. Monitor the ArgoCD application:"
echo "oc get application jira-cipoe-analytics -n openshift-gitops -w"
echo ""

echo "6. Check deployment status:"
echo "oc get all -n jira-cipoe-analytics"
echo ""

echo "7. Test the new filtering functionality:"
echo 'oc create job --from=job/initial-cipoe-data-load test-filtered-load-$(date +%Y%m%d-%H%M%S) -n jira-cipoe-analytics'
echo ""

echo "8. Monitor the job logs to see filtering in action:"
echo "oc logs -f job/test-filtered-load-TIMESTAMP -n jira-cipoe-analytics"
echo ""

echo "✅ Your JIRA CIPOE Analytics platform with zero-link filtering is ready to deploy!"
