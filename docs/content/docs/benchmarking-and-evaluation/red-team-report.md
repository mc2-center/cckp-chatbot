---
title: Red team report
weight: 50
---

# CCKP Copilot Red Team — Latest Report

## Status

No red team evaluation has been run against a CCKP Copilot agent yet — no such agent has been deployed. This page is a placeholder; see [Red teaming](/docs/benchmarking-and-evaluation/red-teaming/) for the methodology this report will follow once real runs exist.

This report previously described the NF Portal Copilot (this repo's upstream fork source), including a recurring finding in its `nf-medical-misinformation` vulnerability. That content described NF Portal Copilot runs specifically and has been removed rather than carried over as if it applied to CCKP — see `benchmark/redteam/redteam_config.json` for the vulnerability taxonomy, which still needs a CCKP-appropriate replacement for `nf-medical-misinformation` before a first CCKP run.

## Next steps

1. Deploy a CCKP Copilot dev agent (see [Deployment](/docs/start-here/deployment/)).
2. Update `benchmark/redteam/redteam_config.json` with CCKP-appropriate vulnerabilities (see the open item in `benchmark/redteam/README.md`).
3. Run `benchmark/redteam/evaluate_redteam.py` against the dev agent, aggregate with `aggregate_redteam.py`, and replace this page with the real methodology, results tables, and discussion — following the structure this page previously had for the NF Portal Copilot as a template for depth and rigor, not for content.
