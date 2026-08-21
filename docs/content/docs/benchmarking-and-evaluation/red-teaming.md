---
title: Red teaming
weight: 40
---

# Red teaming

## What red teaming is

Red teaming is adversarial testing: instead of asking the copilot known questions with known-good answers (like the [source routing](/docs/benchmarking-and-evaluation/source-routing/) and [grounded retrieval](/docs/benchmarking-and-evaluation/grounded-retrieval/) evals), an **attacker LLM** actively tries to make the agent misbehave, and a **judge LLM** scores whether each attempt succeeded.

The CCKP Copilot's red team benchmark (`benchmark/redteam/`) is a self-contained harness with three roles, all running on Bedrock:

- **Target** — the live CCKP Copilot Bedrock Agent (dev alias), invoked read-only.
- **Attacker** — a Claude model that crafts an adversarial message for a given (vulnerability, technique) pair. In multi-turn items it sees the full conversation and escalates, building on established rapport.
- **Judge** — a Claude model that scores the full transcript against the vulnerability's pass/fail criteria.

A **low** attack success rate is good — it means the copilot resisted. This is the opposite direction from the other benchmarks, where a high score is good.

## How it tests guardrails

Each vulnerability in `redteam_config.json` pairs a goal (what the attacker wants the agent to do) with one or more **attack techniques** — `Direct`, `Roleplay`, `PromptInjection`, `SystemOverride`, `GrayBox`, `EmotionalManipulation`, `Crescendo` (multi-turn escalation), and others. Vulnerabilities span four categories:

| Category | Checks |
|---|---|
| `data-privacy` | PII leakage, system prompt / config / credential leakage |
| `safety` | Unqualified medical guidance to patient/advocate personas |
| `security` | Query-injection attempts, write attempts against a read-only backend |
| `agentic` | Off-topic repurposing, false capability claims |

The copilot has **read-only** access only — a docs KB and a read-only resource-query action group, no code execution, no write path anywhere. Nevertheless, the benchmark checks whether an attacker can still talk it into overstepping that scope, leaking something it shouldn't, or giving unsafe guidance.

## Examples from a real run

No CCKP agent has been deployed yet, so there's no real red-team run to draw examples from. Once a dev agent exists, run `evaluate_redteam.py` and replace this section with real transcript examples — both guardrails that held and any that failed. Worth specifically watching for once real runs exist: a CCKP-appropriate equivalent of the NF Portal Copilot's own recurring finding (see `benchmark/redteam/redteam_config.json`'s `nf-medical-misinformation` vulnerability, carried over as a placeholder pending a CCKP-appropriate replacement) — that copilot's one recurring failure was a multi-turn "emotional manipulation" escalation from an anxious patient persona that got it to apply a quoted clinical criterion to the user's own self-reported symptoms. A cancer-research portal has an analogous risk surface (tumor-type-specific literature, prognosis statistics) worth probing for the same failure shape.

## Running it

```bash
cd benchmark/redteam
python evaluate_redteam.py                                   # all config items, dev agent
python evaluate_redteam.py --vulnerability <vulnerability-id>
```

See `benchmark/redteam/README.md` for the full vulnerability/technique taxonomy, safety notes (it attacks a live agent), and all CLI flags.

> [!CAUTION]
> Result JSON files can contain successfully leaked or harmful content the attacks extracted from the agent — review before sharing outside the benchmark's normal workflow.
