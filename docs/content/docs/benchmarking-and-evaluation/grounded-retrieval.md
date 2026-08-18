---
title: Grounded retrieval
weight: 20
---

# Grounded retrieval

## What this tests

Grounded retrieval evaluates a single knowledge source in isolation: given a question with a known correct answer, does the agent retrieve the right document and produce an answer consistent with it? This is the baseline eval every portal copilot needs — any agent with at least one docs knowledge base should have one of these, even before adding [source routing](/docs/benchmarking-and-evaluation/source-routing/) for multi-source setups.

The CCKP Copilot's version of this is the **general-help benchmark** (`benchmark/general-help/`), which evaluates the docs KB built from help.cancercomplexity.synapse.org and the MC2 Center data model docs (mc2-center.github.io/data-models).

## How the dataset is built

Questions are generated synthetically from the live help docs, then validated by human reviewers before use:

1. **Crawl** — a Scrapy spider converts each help doc page into Markdown.
2. **Generate** — an LLM (OpenAI or Anthropic, with structured output) reads the crawled pages and produces multiple-choice questions, each with 4-5 answer choices, one correct label, a target persona (`CONTRIBUTOR`, `REUSER`, `FUNDER`, `PATIENT`, or `X`), the source page URL(s), and a `context` snippet grounding the correct answer.
3. **Validate** — human reviewers check coherence, answer specificity, and whether the question is scoped appropriately for its persona.

A dataset entry looks like:

```json
{
  "question": "What is the default license applied to non-human data submitted to the CCKP?",
  "mc1_targets": {
    "choices": ["CC BY 4.0 (Attribution)", "CC0 (No Rights Reserved)", "CC BY-SA (Attribution-ShareAlike)", "All Rights Reserved"],
    "labels": [0, 1, 0, 0]
  },
  "persona": "CONTRIBUTOR",
  "page_urls": ["https://help.cancercomplexity.synapse.org/..."],
  "context": "..."
}
```

Although the dataset is multiple-choice, evaluation runs in **free-response** format — the agent is asked the plain question, not shown the answer choices — to better reflect how a real user interacts with the copilot. An LLM judge then scores the free-text response against the known correct answer.

## Examples from a real run

No CCKP agent has been deployed yet, so there's no real eval run to draw examples from. Once `benchmark/general-help/cckpdocs_spider.py` and `mc2datamodelsdocs_spider.py` have crawled both docs sources and `generate_dataset.py` has produced a human-validated question set, run `evaluate_bedrock_agent.py` against a deployed dev agent and replace this section with real results — including at least one example of a **grounded and correct** answer and one **honest gap** (the agent saying "I don't have this" rather than guessing), which is the failure mode worth normalizing rather than penalizing away.

## Running it

```bash
cd benchmark/general-help
python evaluate_bedrock_agent.py
```

See `benchmark/general-help/README.md` for dataset generation (`generate_dataset.py`), the full scoring rubric, all CLI flags, and metrics reported (overall accuracy, per-persona accuracy, cross-page vs single-page accuracy, source attribution rate).
