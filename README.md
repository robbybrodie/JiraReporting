# JIRA CIPOE GraphDB Analytics

A comprehensive GraphDB-based analytics platform for analyzing relationships between CIPOE (Critical Issue - Partial Outage Event) tickets and their linked issues in JIRA, deployed on OpenShift via GitOps.

## 🚀 Quick Start

Get up and running in 5 minutes: **[Quick Start Guide](docs/QUICKSTART.md)**

For detailed deployment instructions: **[Deployment Guide](docs/DEPLOYMENT.md)**

## 🏗️ Architecture

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

### Components

- **Neo4j Graph Database**: Stores CIPOE tickets and their relationships
- **Neo4j Browser**: Web-based visualization and query interface
- **Data Loader Service**: Containerized Python service for JIRA data ingestion
- **ArgoCD GitOps**: Automated deployment and configuration management
- **OpenShift Integration**: ROSA-optimized with proper RBAC and networking

## 📊 What You Can Analyze

- **Impact Analysis**: Which issues affect multiple CIPOE tickets?
- **Relationship Mapping**: Visual graph of issue dependencies  
- **Trend Analysis**: Status distributions and patterns over time
- **Critical Path**: Find chains of impact across your infrastructure

### Sample Insights

```cypher
// Find issues impacting multiple CIPOE tickets
MATCH (i:Issue)-[:IMPACTS]->(c:CIPOE)
WITH i, count(c) as impact_count
WHERE impact_count > 1
RETURN i.key, i.summary, impact_count
ORDER BY impact_count DESC;
```

## 🔧 Features

### ✅ Production Ready
- **GitOps Deployment**: Automated via ArgoCD sync waves
- **Security First**: Secrets management, non-root containers, HTTPS
- **Scalable**: Paginated data loading with rate limiting
- **Resilient**: Health checks, backoff strategies, job retry logic

### ✅ Operational Excellence  
- **Monitoring**: Comprehensive logging and metrics
- **Maintenance**: Automated incremental updates via CronJob
- **Backup**: Built-in Neo4j export capabilities
- **Documentation**: Complete deployment and troubleshooting guides

### ✅ Developer Friendly
- **Container Ready**: Dockerfile and image build pipeline
- **Configuration**: Environment-based config with sensible defaults
- **Extensible**: Modular Python architecture
- **Observable**: Structured logging and error handling

## 🗂️ Repository Structure

```
├── argocd/                    # ArgoCD application definitions
├── docs/                      # Documentation and guides
├── manifests/                 # OpenShift/K8s manifests
│   ├── 00-namespace.yaml     # Namespace and RBAC
│   ├── 01-secrets-template.yaml # Secret templates (manual creation)
│   ├── 02-neo4j-pvc.yaml    # Persistent storage
│   ├── 03-neo4j-deployment.yaml # Neo4j database
│   ├── 04-neo4j-service.yaml # Services and routes
│   ├── 05-data-loader-configmap.yaml # Configuration
│   ├── 06-initial-data-load-job.yaml # One-time data load
│   ├── 07-incremental-data-load-cronjob.yaml # Scheduled updates
│   └── 08-deployment-waves.yaml # ArgoCD sync ordering
├── scripts/                   # Legacy scripts (reference)
├── src/                      # Python source code
│   └── jira_neo4j_loader.py # Main data loader service
├── Dockerfile                # Container image build
└── requirements.txt          # Python dependencies
```

## 🚦 Deployment Waves

ArgoCD deploys components in the following order:

1. **Wave 0**: Namespace and basic resources
2. **Wave 1**: Secrets (manual) and persistent volumes  
3. **Wave 2**: Neo4j database and configuration
4. **Wave 3**: Services and routes
5. **Wave 4**: Data loading jobs (manual trigger recommended)

## 🔒 Security & Compliance

- **Secret Management**: All credentials stored in OpenShift secrets
- **Network Security**: Internal cluster communication only
- **HTTPS Enforcement**: TLS termination on all routes
- **Non-Root Containers**: Security contexts and user namespaces
- **RBAC Integration**: OpenShift role-based access controls

## 📈 Scaling Considerations

### Small Deployment (< 1000 CIPOE tickets)
- Neo4j: 1Gi memory, 500m CPU
- Page size: 50 issues per batch
- Sync frequency: Every 6 hours

### Large Deployment (> 10k CIPOE tickets)  
- Neo4j: 4Gi memory, 2 CPU cores
- Page size: 100 issues per batch
- Parallel job processing with offset ranges

## 🛠️ Development

### Local Development
```bash
# Set up environment
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# Run locally (requires Neo4j)
export NEO4J_URI="bolt://localhost:7687"
export JIRA_TOKEN="your-token"
python src/jira_neo4j_loader.py --dry-run
```

### Container Build
```bash
# Build image
podman build -t jira-cipoe-loader .

# Test run
podman run --rm -e JIRA_TOKEN=xxx jira-cipoe-loader --help
```

## 📋 Prerequisites

- **ROSA Cluster**: Red Hat OpenShift Service on AWS
- **ArgoCD**: OpenShift GitOps operator installed  
- **JIRA Access**: Data Center Personal Access Token
- **Storage**: Persistent volume support (gp3-csi recommended)

## 🎯 Getting Started

1. **[Quick Start](docs/QUICKSTART.md)** - Deploy in 5 minutes
2. **[Full Deployment Guide](docs/DEPLOYMENT.md)** - Production deployment  
3. **[Troubleshooting](docs/DEPLOYMENT.md#troubleshooting)** - Common issues and solutions

## 📞 Support

- **Issues**: Use GitHub Issues for bug reports
- **Documentation**: Check the `docs/` directory
- **Logs**: `oc logs -f deployment/neo4j -n jira-cipoe-analytics`

## 🤝 Contributing

1. Fork the repository
2. Create a feature branch
3. Test your changes
4. Submit a pull request

## 📄 License

This project is licensed under the MIT License - see the LICENSE file for details.
