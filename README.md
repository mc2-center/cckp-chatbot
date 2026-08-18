# CCKP Chatbot

Development and configuration for CCKP (Cancer Complexity Knowledge Portal)-flavored Synapse chatbots.

Forked from [nf-osi/portal-chatbot](https://github.com/nf-osi/portal-chatbot) (the NF Portal Copilot) and re-targeted at the CCKP, maintained by Sage Bionetworks' MC2 Center.

## Description

This repository contains configuration, test datasets, and other resources for CCKP-tailored Synapse chatbots.

## Documentation

Guides and architecture notes live in [`docs/`](docs/). The docs site is built with [Hugo](https://gohugo.io) and the [hugo-book](https://themes.gohugo.io/themes/hugo-book/) theme; see [`docs/README.md`](docs/README.md) for local dev setup. Deploys to GitHub Pages automatically via [`.github/workflows/deploy-docs.yml`](.github/workflows/deploy-docs.yml) on changes to `docs/`.

## Agent Registrations

For details on all registered agents (including the two backend variants — SQL over Synapse View tables, and SPARQL over a knowledge graph), see [agents/README.md](agents/README.md).
(Note: Creating agents for Synapse is not generally available to all Synapse users.
Nevertheless, if you came across this project and have interest and funding for Synapse agents and portals, feel free to reach out to us.)

No CCKP Copilot has been deployed yet — see [agents/README.md](agents/README.md)'s "Open items before first deploy" for what's needed.

## PHD Benchmarking and Evaluation

The Portal Help & Discovery (PHD) suite is a set of benchmarking and evaluation datasets to ensure quality standards and quantify improvements in our chatbot agents.
Within our framework, these resources also help identify documentation gaps and inconsistencies.
Not all datasets are stored here in this repo; references to relevant datasets will be kept up to date.

### General Help Component

The General Help component tests the agent's ability to answer questions about CCKP navigation, features, history, and general usage.
The agent answers questions based on the CCKP help docs and the MC2 Center data model docs.

- [Test questions about the CCKP](benchmark/general-help/) — reset pending a fresh crawl of help.cancercomplexity.synapse.org and mc2-center.github.io/data-models; see the benchmark's README

### Discovery Component

There will eventually be multiple implementations falling under the Discovery component.
Some of the below may arguably focus more on search; the distinction sometimes depend on user phrasing.
(Note: Search is more goal-oriented with targeted results. Discovery is for browsing, recommendations, and understanding what's available. Discovery is harder to evaluate.)

- Finding answers and connections with the CCKP knowledge graph — [mc2-center/data-models/kg-pipeline](https://github.com/mc2-center/data-models/tree/main/kg-pipeline) builds the underlying graph; a discovery-QA benchmark analogous to [nf-osi/kg-pipeline's evaluation](https://github.com/nf-osi/kg-pipeline/tree/develop/evaluation/main) has not yet been built from it — a candidate follow-up.

## Contributing

We welcome contributions to improve the chatbot. To contribute:

1. Fork the repository.
2. Create a new branch for your feature or bugfix.
    ```sh
    git checkout -b feature-name
    ```
3. Commit your changes.
    ```sh
    git commit -m 'Describe your feature or fix'
    ```
4. Push to the branch.
    ```sh
    git push origin feature-name
    ```
5. Create a pull request.

## Acknowledgements

Adapted from the NF Portal Copilot, built by NF-OSI with funding from the [Gilbert Family Foundation](https://gilbertfamilyfoundation.org/).

## See Also

- https://rest-docs.synapse.org/rest/index.html#org.sagebionetworks.repo.web.controller.AgentController
- [Synapse Custom Agents framework](https://sagebionetworks.jira.com/wiki/spaces/PLFM/pages/3711303683/Adding+Custom+Agents+to+Synapse) (Internal Confluence page)
- [mc2-center/data-models](https://github.com/mc2-center/data-models) — the CCKP/MC2 Center data model, including the `kg-pipeline` knowledge-graph pipeline referenced above

## License

This project is licensed under the MIT License. See the [LICENSE](LICENSE) file for details.
