---
title: Deployment
weight: 40
---

# Deployment

## Two stacks: dev and prod

We run each template as two stacks — for the recommended SQL backend, `cckp-copilot-sql-dev` and `cckp-copilot-sql-prod` (the SPARQL backend's `cckp-copilot-sparql-dev/prod` follow the same pattern once it's deployable). This gives you:

- A safe place to test instruction changes, model swaps, and Lambda updates before they reach users
- Isolated IAM roles, Lambda functions, and agent IDs per environment
- Separate Lambda packages so a bad dev deploy can't affect prod
- Dev uses the `TSTALIASID` test alias which always points to DRAFT — so you're always testing your latest changes

The workflow is: make changes on a branch → manually trigger the `deploy-copilot-sql` (or `-sparql`) workflow to push to dev → test → merge to main → prod updates automatically.

## CI/CD setup

The GitHub Actions workflows (`.github/workflows/deploy-copilot-sql.yml` and `deploy-copilot-sparql.yml`) handle deployments, one per backend. Each is smart about what changed:

- Lambda code only → uploads zip and calls `update-function-code` directly (no stack update needed)
- Template/instructions/schema → runs `cloudformation deploy`

**CI/CD is optional to get started** — you can deploy manually with `aws cloudformation deploy` first and set this up later.

To set this up for your repo, you need a repo-specific IAM role for GitHub OIDC. **Ask an admin to create this**; currently, it can't be self-service because it requires IAM permissions. The role should:

- Trust `token.actions.githubusercontent.com` scoped to your repo and branch
- Have least-privilege permissions: S3 write on your Lambda bucket prefix, Lambda update on your function names, CloudFormation update on your two stack names, IAM manage on your copilot role names, Bedrock agent operations

No such role exists yet for this repo. The NF Portal Copilot's `GitHubActionsNFPortalChatbot` role (in the `org-sagebase-synapsellm-prod` account) is a reference for the permission shape; an admin needs to provision an equivalent (e.g. `GitHubActionsCCKPChatbot`) scoped to `mc2-center/cckp-chatbot`. Once created, store the role ARN as `AWS_OIDC_ROLE_ARN` in this repo's secrets.

## Knowledge base

KBs are created and managed separately from the agent template — they have their own vector store, embedding model, data sources, and sync schedule. Create yours via the console or CLI, then pass the ID as the `KnowledgeBaseId` template parameter.

CCKP's docs KB should be built from a crawl of help.cancercomplexity.synapse.org — no such KB has been built yet. Both templates ship with a `REPLACE_ME_CCKP_KB_ID` placeholder until one exists.

## Agent registration with Synapse

Once deployed, register the prod agent with Synapse so it appears in the chat interface. You'll need the agent ID and alias ID from the CloudFormation stack outputs. See [Registering Our New Agent](https://sagebionetworks.jira.com/wiki/spaces/PLFM/pages/3711303683/Adding+Custom+Agents+to+Synapse#Registering-Our-New-Agent) for a walkthrough and the [Synapse REST API](https://rest-docs.synapse.org/rest/PUT/agent/registration.html) for the registration endpoint.

Only register prod agents — dev agents are tested internally.
