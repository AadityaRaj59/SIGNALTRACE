# Research Methodology

## Evidence classes
- **Reported observation:** a metric/value/period/unit/basis extracted from a source and accepted only when the quoted excerpt is an exact substring of retrieved text and includes the value, metric label, and period.
- **Computed result:** deterministic arithmetic over compatible observations. Each calculation stores a formula, periods, reporting basis, source IDs, and input observation IDs.
- **Hypothesis:** a candidate interpretation generated from validated observation/calculation IDs. It is not automatically a fact or investment recommendation.
- **Counter-evidence:** an observation extracted from a fetched contradiction-search source and explicitly evaluated for relevance to the hypothesis. Search result snippets alone are not opposing evidence.

## Source provenance and independence
Records retain source URL, canonical URL, publisher/domain, retrieval timestamp, content type, content hash, publication date if found, source type, fetch status, excerpt and errors. Canonical URL and identical content hashes are deduplicated. This is only a first-pass independence check; sophisticated syndicated-content clustering is still a limitation.

## Calculation policy
Use decimal arithmetic. Do not replace missing data with zero. Reject same-period comparisons and mismatched reporting bases. The trend helper only uses clearly identifiable fiscal/calendar years and strict numeric raw values. A value that contains ambiguous unit text is skipped instead of silently reinterpreted.

## Evidence strength
The score records the number of supporting observations, distinct supporting sources, opposing observations, deterministic calculations, and whether counter-search results exist. A result URL is not corroboration until its contents have been parsed and validated. The current conservative scorer does not assign Strong while counter-evidence remains incompletely resolved.

## Abstention and limitations
When LLM or search credentials are absent, or when source data is too weak, the app states the missing capability and abstains. LLM extraction can still make semantic mistakes despite excerpt validation; analysts must inspect passages, definitions, period labels, and reporting basis. Complex bank-specific definitions and table layouts need further domain-specific validation.
