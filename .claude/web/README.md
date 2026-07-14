# Web Agent Configuration

This directory houses the web crawl agent's configuration and registry sources.

## web-config.json

The `web-config.json` file controls crawl parameters and source selection:

- **new_sources_total**: Hard cap on new sources discovered per crawl cycle (default: 5). Increase to expand crawl breadth; decrease to focus on high-signal sources.
- **lanes**: Allocation quotas for source lanes (mutually exclusive categories):
  - **gap**: Papers/sources filling citation gaps or direct wiki references (default quota: 3)
  - **research**: Sources tied to an active research direction not yet reducible to a gap (default quota: 1, minimum 1)
  - **news**: Registry blog/newsletter RSS items and conference announcements (default quota: 1)
- **spillover_order**: Priority order for unused quota handoff. If the gap lane fills with fewer than 3 sources, spillover goes to research, then news.
- **registry_boost**: Relevance multiplier (0–1 scale) applied to any source already listed in the registry. Default 0.25 = 25% boost over open-web discovery. Higher value favors known sources; lower value encourages new exploration.
- **paid_scrape**: Phase 3B feature (currently disabled). When enabled: governs premium API calls (e.g., academic paper APIs, premium web search engines) and max concurrent crawl sessions.

## Registry Sources

Three registry JSON files drive source discovery. Each contains pre-scored entries with `relevance` / `priority` metadata:

- **paper-publisher.json**: Academic paper feeds (arXiv, bioRxiv, etc.) and paper APIs (Semantic Scholar, PapersWithCode). Updated by research agent.
- **github-repos.json**: GitHub repositories to monitor (core Second Brain deps, reference implementations, open-source tools). Updated by research agent.
- **blog-newsfeed.json**: Blogs, RSS feeds, and conference announcements relevant to research themes. Updated by research agent.

## Tuning for Your Vault

To refocus crawl behavior, edit `web-config.json`:

1. **Increase gap quota** → prioritize closing citation gaps over exploratory research.
2. **Increase news quota** → follow more conference announcements and blog updates.
3. **Raise registry_boost** → trust pre-listed sources more; lower to diversify beyond the registry.
4. **Decrease new_sources_total** → tighter, more selective crawls (cheaper).

To add new sources: add entries to the appropriate registry JSON (via UI, manual edit, or ask Claude).

## Cache Directory

The `cache/` subdirectory stores ephemeral crawl artifacts (intermediate fetch results, HTTP etags, etc.). It is ignored by git and cleared regularly by the web agent.
