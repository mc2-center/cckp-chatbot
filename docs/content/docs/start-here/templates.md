---
title: Templates
weight: 30
---

# Templates

CCKP Copilot ships **two** CloudFormation templates for two different backends — pick the one that fits (see [Choosing a backend](#choosing-a-backend) below), or use them as a reference for your own portal.

### CloudFormation templates

Both deploy the full stack: IAM roles, Lambda function, Bedrock Agent with action group, knowledge base attachment, and agent alias. See the usage comments at the top of each template for what to replace.

- **`agents/cckp-copilot/cloudformation.sql.yaml`** — SQL action group over Synapse View tables. Deployable today, no new infrastructure.
- **`agents/cckp-copilot/cloudformation.sparql.yaml`** — SPARQL action group over a knowledge graph. Not deployable yet — requires a hosted SPARQL endpoint serving `mc2-center/data-models/kg-pipeline`'s output, which doesn't exist.

Key things to change for your portal:
- **`Instruction`** — the system prompt, embedded inline. Replace with your agent's instructions.
- **`KnowledgeBaseId`** — a placeholder (`REPLACE_ME_CCKP_KB_ID`) until a real CCKP docs KB is provisioned. Replace with your own or remove the block entirely.
- **`SynapseAuthToken`** (SQL template) / **`SparqlEndpoint`** (SPARQL template) — the resource backend to query.
- **`FoundationModelId`** — defaults to Claude Haiku 4.5; change as needed.

Deploy with:

```bash
aws cloudformation deploy \
  --template-file agents/cckp-copilot/cloudformation.sql.yaml \
  --stack-name my-portal-copilot-dev \
  --parameter-overrides \
      AgentName=my-portal-copilot-dev \
      SynapseAuthToken=my-token \
      LambdaS3Bucket=my-bucket \
      LambdaS3Key=lambda/my-function.zip \
  --capabilities CAPABILITY_NAMED_IAM
```

### Choosing a backend

| | SQL (`cckpSqlRag`) | SPARQL (`cckpGraphRag`) |
|---|---|---|
| Infrastructure needed | None — queries live Synapse tables directly | A hosted triple store serving kg-pipeline's RDF output |
| Matches deployed schema | Exactly (queries the real tables) | Only as current as the last kg-pipeline sync |
| Cross-entity joins | One `sqlQuery` call per table, joined client-side | Native graph traversal in one query |
| Publication full-text search | Not available | Not available (CCKP has no indexed full-text; unlike some other portal copilots) |
| Status | Recommended, deployable now | Kept for when a hosted endpoint exists |

### Lambda functions

`agents/cckp-copilot/lambda/cckpSqlRag/lambda_function.py` exposes three operations for the SQL backend:

| Function | Purpose |
|---|---|
| `sqlQuery` | Run SQL against one named table (datasets, publications, tools, grants, education, or a raw synId) |
| `getColumns` | List a table's exact deployed column names |
| `countByType` | Row counts across all 5 tables |

`agents/cckp-copilot/lambda/cckpGraphRag/lambda_function.py` exposes four operations for the SPARQL backend (only usable once a hosted endpoint exists):

| Function | Purpose |
|---|---|
| `sparqlQuery` | Run arbitrary SPARQL |
| `getSchema` | List ontology classes and properties |
| `getShape` | SHACL constraints for a class |
| `countByType` | Quick RDF type inventory |

To reuse either for your own portal:
- SQL: point `SYNAPSE_AUTH_TOKEN` at a valid Synapse Personal Access Token and update the `TABLES` alias map to your own View table synIds.
- SPARQL: point `SPARQL_ENDPOINT` at your own endpoint; if it requires auth, set `SPARQL_AUTH_TOKEN`.
- Each Lambda's `openapi.yaml` defines its action group interface and can be used as-is.
