# Changelog

## cckp-copilot

### Unreleased

- Forked from `nf-osi/portal-chatbot`'s NF Portal Copilot and re-targeted at the Cancer Complexity Knowledge Portal (CCKP).
- Two backend variants: `cloudformation.sql.yaml` (SQL over Synapse View tables, deployable today) and `cloudformation.sparql.yaml` (SPARQL over a CCKP knowledge graph, pending a hosted endpoint).
- Instructions, redirect targets, and example queries rewritten for CCKP's Dataset/Publication/Tool/Grant/EducationalResource entities.
- Not yet deployed — no real agent IDs, KB ID, or AWS resources exist.
