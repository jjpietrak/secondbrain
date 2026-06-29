---
type: gap
id: GAP-XXXX
title: "TBD -- replace with gap title"
status: open
topics: [T-XXXX]
fillable_by: [web]
priority: medium
shows_up_in: ["[[wiki/TBD]]"]
created: YYYY-MM-DD
updated: YYYY-MM-DD
written_by: wiki
---
## Missing
Describe what knowledge is missing and why it matters.

## Why
Justification for the priority level (high/medium/low).

## Shows up in
- [[wiki/<page>]] -- why it surfaces here

## Open questions
- List any harvested open-question lines that reference this gap's topic or pages.
- Omit this section if there are no matching open questions.

## For future Claude
Gap files are machine-parsed by web_decision.parse_gaps (reads wiki/gap/*.md).
Field rules:
- topics/fillable_by: YAML lists (e.g. [T-0001, T-0002] or [arxiv, web])
- fillable_by values: bare engine tags -- arxiv | web | github | forum | x
- priority: one word only -- high | medium | low
- shows_up_in: YAML list of wikilinks (e.g. ["[[wiki/sources/paper-a]]"])
- status: open (default) or filled (set by the wiki agent once the gap is addressed)
Do NOT add extra YAML fields; the parser reads only the listed keys.
