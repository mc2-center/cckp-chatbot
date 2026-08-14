# Agent Registrations

This directory contains CCKP (Cancer Complexity Knowledge Portal) agent configurations.

## Copilot Stacks

The Copilot has two backend variants, deployed from separate CloudFormation templates under `cckp-copilot/`:

- **`cloudformation.sql.yaml`** — queries the CCKP's curated Synapse View tables (Dataset, Publication, Tool, Grant, EducationalResource) directly via SQL. No new infrastructure required; **recommended today**.
- **`cloudformation.sparql.yaml`** — queries a hosted SPARQL endpoint over the CCKP knowledge graph produced by `mc2-center/data-models/kg-pipeline`. **Not deployable yet** — no such endpoint has been stood up (kg-pipeline only produces a local `.ttl` file). Kept for when that infrastructure exists.

Each template deploys two stacks from the same file:

- **`cckp-copilot-{sql,sparql}-prod`** — Production. Stable version that portal users interact with.
- **`cckp-copilot-{sql,sparql}-dev`** — Development/staging. Used to test instruction changes, model swaps, and Lambda updates before promoting to prod.

No agent has been deployed yet, so there are no real Agent IDs to record here — fill in the table below once a stack is first deployed.

## Synapse Registrations

| Agent | Registration | Registered by | Notes |
|---|---|---|---|
| _(none yet)_ | | | Register the prod agent with Synapse once deployed, following the [Synapse Custom Agent framework](https://sagebionetworks.jira.com/wiki/spaces/PLFM/pages/3711303683/Adding+Custom+Agents+to+Synapse) (internal Confluence). Dev agents are typically tested internally and not registered. |

## Copilot Capabilities

- **Help Docs QA**: Answers process, policy, and how-to questions from the CCKP documentation (help.cancercomplexity.synapse.org)
- **Resource Search** (SQL variant): SQL queries against the CCKP's Dataset, Publication, Tool, Grant, and EducationalResource View tables
- **Knowledge Graph Integration** (SPARQL variant, not yet deployable): SPARQL queries against a hosted CCKP knowledge graph
- **Portal Navigation**: Redirects users to filtered Explore pages (datasets, publications, tools, grants, educational resources)
- **Guided Prompts**: Interactive follow-up suggestions

See [CHANGELOG](CHANGELOG.md) for release history.

## CI/CD

Changes under `agents/cckp-copilot/` trigger deploy workflows in `.github/workflows/`:

- `deploy-copilot-sql.yml` — triggered by changes to `cloudformation.sql.yaml` or `lambda/cckpSqlRag/**`
- `deploy-copilot-sparql.yml` — triggered by changes to `cloudformation.sparql.yaml` or `lambda/cckpGraphRag/**`

Each supports:

- **Manual dispatch** (`workflow_dispatch`) — deploys to the dev stack for testing. Trigger from any branch via the Actions UI or `gh workflow run deploy-copilot-sql.yml --ref my-branch`.
- **Merge to main** — automatically deploys to the prod stack.

The workflow detects what changed and only runs the needed steps:

- Lambda code only → uploads zip to S3 and updates the function in-place (no stack update)
- Template/instructions/schema → runs `cloudformation deploy` on the stack

AWS credentials use GitHub OIDC via an IAM role, stored as the `AWS_OIDC_ROLE_ARN` repo secret. **No such role has been provisioned yet** — an AWS admin needs to create one scoped to this repo (e.g. named `GitHubActionsCCKPChatbot`, mirroring the NF Portal Copilot's role) before these workflows will succeed.

## Setup

To learn more about the Synapse Custom Agent framework, refer to [this internal Confluence doc](https://sagebionetworks.jira.com/wiki/spaces/PLFM/pages/3711303683/Adding+Custom+Agents+to+Synapse).

## Open items before first deploy

- Provision the `GitHubActionsCCKPChatbot` IAM OIDC role and `AWS_OIDC_ROLE_ARN` repo secret.
- Provision an S3 bucket for Lambda deployment packages (placeholder name: `cckp-chatbot`).
- Build a Bedrock Knowledge Base from `help.cancercomplexity.synapse.org` and set its ID as `KnowledgeBaseId` (currently `REPLACE_ME_CCKP_KB_ID` in both templates).
- If deploying the SPARQL variant: stand up a hosted SPARQL endpoint serving `mc2-center/data-models/kg-pipeline`'s `data/rdf/cckp_kg.ttl` output, kept in sync with the pipeline's extract stage, and store its URL as the `CCKP_SPARQL_ENDPOINT` repo secret used by `deploy-copilot-sparql.yml`.
- A Synapse Personal Access Token for the SQL variant's `SynapseAuthToken` parameter, stored as the `SYNAPSE_AUTH_TOKEN` repo secret used by `deploy-copilot-sql.yml`.
