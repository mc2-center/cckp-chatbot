---
title: Reference workflow
weight: 20
---

# Reference workflow

The CCKP Copilot combines documentation and a resource-query backend (SQL over Synapse View tables, or SPARQL over a knowledge graph) behind a Bedrock agent, then iterates through evaluation before promoting changes from dev to prod.

![Diagram showing the CCKP Copilot workflow: knowledge sources (Docs KB and a resource backend) feed into a CloudFormation-managed agent stack (instructions, Lambda adapter, agent, alias); the agent alias is evaluated, results decide whether to revise or promote; ready changes flow through CI/CD dev and prod stacks, ending in an update to the Synapse agent registration.](./diagrams/cckp-copilot-workflow.svg)
