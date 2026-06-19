# Requirements

This document captures the functional, structural, performance, and testing requirements for Second Brain v0.2.

## Functional

- Wiki entities are fact-checked against raw sources and referenced with proper links to other vault location [Link Text](path/to/document.md)
- Vault data export to any AI tool should be possible.
- TBD Extendability to coding application and autonomous paper-to-code implementation. If paper has repo link it should always be pulled into repos wiki.
- The objective graph should be parallel to wiki graph, focusing on question-answer, question-subquestion relations, mapping out the research area na letting me state open, non-answered questions that agent actions will try to answer whenever new facts appear.
- Automated nightly actions for web scraping and newsletter.
- Produces a newsletter.
- Improve quality of new sources retrieval with respect to NLM or bare 'research' outputs from Claude/ChatGPT by reasoning over objectives & research directions rather than only keywords, references etc
- Self-updating and learning research agents
- Agent's should be able to reason over wiki entities and knowledge entities separately.
- Knowledge entities should create a separate group in a graph from wiki entitities with structured links to entitities if needed.

## Structural

- Vault is build around single 'purpose' or research question. It's not an 'all-in' cotainer for all types of notes and documents. Cross-vault talk is TBD
- Raw files are never edited
- No duplicates.
- Notes are not lost.
- All sources ever touched are logged with status [ingested/rejected/waiting_approval/deleted]
- All sources are labelled with unique ID not dependent on link or filename.
- Existining wiki pages can be rewritten or updated as new sources and facts are ingested.
- Each repetitive item in the vault (wiki entry, report, objective uses a fixed _template)
- Two independent flows controlled by 2 independent agents: Research (web scarping + filtering + new content ranking) and Wiki (reasoning over vault contents)

## Performance

- Token cost of all actions is carefully monitored for all agentic actions. The aim is to minimise the cost of daily usage as the tool develops.
- Nightly action can consume up to 100% 5-hour token window - the goal is to maximise this window utilistion as the user is usually idle in that time.
- Post-nightly actions performed after user approval can't consume more that 30% 5-hour token window.

## Testing

- Accuracy benchmark - check ref
- Quality of responses with & without synthesis with Objectives reasoning
