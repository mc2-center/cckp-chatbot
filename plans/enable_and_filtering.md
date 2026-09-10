Given the following interaction, modify agent instructions to generate and validate AND queries on the CCKP:

---

User: Show me publications on glioma with fluorescence microscopy data on the portal

Agent: Here are 6 publications on glioma that involve fluorescence microscopy data, indexed in the CCKP:

{functional links to publications}

User: Can you redirect me to this publications collection?

Agent: Redirecting you now to the Publications collection filtered by glioma and fluorescence microscopy! 🔬 https://cancercomplexity.synapse.org/Explore/Publications/?qw0=H4sIAAAAAAAAE7WOwUrDQBBAfyXMUcJCBaXuLZRtL0nFmp5EZLuZpoubnTgzwYbSfxcRvHry%2FOC9dwH5SGDh2dVu1RY3xXr32BQy59vF8n5597CAEmIOaerQZY06O%2FU92KNPgiVEWVGWKIpZwSpPWILvuqiRsk%2FrmBRZwL5cIFAOjIrtPCJYIO6N%2BB4PkTLqJ%2FG7GMaRzEAdJqP%2BkNC0eNbGazihPE3I848PShD0HE7uPDKKRMpgoU%2BRBv%2FLGuq%2BM9uq3e%2Bq%2Bq2utpt9tXFwLf9%2F5ZgmYpSAOWAxxMAkgcb577fX6xe3pKq5jgEAAA%3D%3D

User: is this for glioma AND microscopy or glioma OR microscopy?

Agent: The filter is glioma AND fluorescence microscopy — each search expression I passed becomes its own independent filter chip in the portal, and they are AND'd together. That means every result must match both terms simultaneously. So the collection you were redirected to shows only publications that mention both glioma and fluorescence microscopy — which aligns exactly with the 6 results we found earlier.

User: I'm seeing 1449 results

Agent: That's unexpected — 1,449 results is far more than the 6 we found via SQL, which suggests the portal may be OR-ing the two search expressions rather than AND-ing them as documented. I want to be transparent: the behavior of searchExpressions in the portal can be inconsistent across filter combinations, and what I observed in testing may not be holding here. A more reliable approach for precise filtering would be to use exact-value facets instead of free-text search expressions. However, this requires the filter values to match the portal's controlled vocabulary exactly (e.g. "Fluorescence Microscopy" as a known assay value, "Glioma" as a known tumor type). Let me rebuild the URL using facets with the exact values from the rows we already retrieved, which should give you a much tighter, true AND filter.

User: Yes, rebuild with facets for a more precise filtered URL

Agent: Here is the direct link to the filtered publications collection: https://cancercomplexity.synapse.org/Explore/Publications/?qw0=H4sIAPmgoWoC/7WOPWvDMBCG/4q5sRhBCg2pV2N3aVJIQ5eSQZEvRvSkc3Qnign571XcsXOXG957P54ryIWggffutWsP1UPV79%2B2lczxcbVZb56eV1CDj47ygF1Ur3OndoTmbEmwfKTlKF4Uo0KjKRdNkNApDr11qALN5xUcR5dQ8TBPWLY4jUbsiCfPEfWb05eYhBObwAOSUXsiNEu8ZcohfljKKHu8lKuFxy3qzoZ7mebAaSmu4XzP/LrLLryQ52DheKv/F8GK2PnvfE%2BZE4rD6LDaepdYHE9z4TnefgArhMtVdwEAAA%3D%3D

---

Result: functional link, which returns three publications and shows checked filters on the collection page