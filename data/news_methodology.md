# News content and tone methodology

The previous implementation scored only a short list of headline words, and `INSERT OR IGNORE` left existing records unchanged on refresh. Its first snapshot contained 49 articles, only four with nonzero scores. The replacement scores the RSS title **and summary**, upserts existing article scores without changing IDs, and saves an audit record in `news_analysis`. The original ten-column `news_items` table and CSV interfaces remain compatible.

## Affect scores and coverage

The package uses [VADER's original Python implementation and documentation](https://github.com/cjhutto/vaderSentiment), version 3.3.2. VADER is a rule-based sentiment analyzer with a human-rated lexicon and rules for negation, emphasis and contrast. Its compound output is a continuous number from -1 to +1. Here, it describes the emotional tone of the title and RSS summary. It is not party favorability, approval polling, or a measurement of voter opinion.

Every nonempty article gets a numeric score, including zero. The implementation separately stores the matching sentiment terms and a coverage status: `positive` (at least +0.05), `negative` (at most -0.05), `neutral_or_mixed`, or `no_lexicon_matches`. A zero with no matched terms is **not evidence of measured neutral public opinion**. HTML is stripped from RSS summaries before analysis; complete article pages are not fetched.

## Party outcome attribution

An independent, transparent rule layer identifies named Democratic and Republican entities and nearby political outcome cues, such as gaining support, endorsements, legal setbacks, declining approval, and electoral losses. It splits sentences and contrast clauses, attributes each cue to the nearest entity within ten tokens, reverses cues for preceding negation, and normalizes cue sums to -1 through +1. Repeated copies of the same cue are counted once. Mixed clauses such as “Democrats and Republicans gain” abstain; missing target scores remain null. Generic negative reporting on crime, inflation or war is not automatically attributed to the party mentioned in the article.

Rules explicitly exclude a “won” match inside “won't”, “ahead of” calendar references, and common retrospective election victories. Longer phrases such as “losing support” consume their component tokens before single-word matches. Endorsement proximity and verb context reduce simple attribution errors. Party and politician dictionaries are finite; sarcasm, unnamed candidates, quotations, causal claims, policy preferences, and complex grammar remain sources of error. The outcome rules and their strength values are analyst assumptions, not a trained target-sentiment model.

For an article with at least one attributed party, its signed partisan feature is `clip(D_tone - R_tone, -1, 1)`, with a missing counterpart contributing zero to that expression. A positive value favors Democrats; a negative value favors Republicans. Both missing means the article provides no partisan direction. This feature does not assume that the opposite party was observed when it was not.

## Content and aggregation

Keyword dictionaries identify nine issue areas: `economy`, `immigration`, `abortion`, `healthcare`, `democracy`, `crime`, `foreign_policy`, `education`, and `climate`. Article salience is the number of distinct matched terms divided by three, capped at one. An issue's signed article feature multiplies this salience by the article's partisan feature. This makes an explicit article-level association; it does not establish which sentence caused a voter's response. Terms and target evidence are retained in the audit JSON.

`news_signals()` selects articles by UTC **publication date**, with an inclusive 30-day window ending on the requested cutoff. Future articles, older articles, and records assigned exclusively to other races are excluded. Tracking query parameters are removed for URL deduplication. Articles naming states other than the race's state, without naming that state, receive 0.25 geographic relevance; unlocalized national items and explicit local records receive full relevance. This is a geographic heuristic, not a measured media exposure model.

Within each publisher, articles have a 14-day recency half-life multiplied by geographic relevance. Publisher means then receive equal weight. Overall direction averages the attributed article features; diagnostics report separately sampled party means, so their subtraction is not necessarily the overall feature. Issue direction and salience include all relevant articles, with unattributed articles contributing salience and zero direction. A prolific publisher therefore cannot dominate solely by publishing more items. Syndicated stories at different URLs are not fully deduplicated, and equal weighting does not remove publisher selection bias.

## Refresh and reproducibility

`python main.py fetch-news` retrieves BBC US & Canada, NPR Politics, and Guardian US Politics RSS feeds and updates stored articles and analyses. Each fetch saves the raw XML, feed URL, retrieval time and SHA-256 checksum. `python main.py rescore-news` can recover summaries from those snapshots and rescore without network access. Explicit scores supplied through the CSV import are retained when refreshing their content features. A model, dictionary, or summary change can change automatic scores; new predictive runs must be generated to use them.

On October 21, 2026, the actual 49 saved articles all recovered RSS summaries: 46 had nonzero VADER scores and three had no lexicon matches; 11 had an attributable party outcome after local-rule auditing. A live refresh then updated all 49 existing articles across the three feeds without inserting duplicates or errors. The regression tests cover this update path, cached-summary recovery, negation, ambiguous targets, false win matches, manual-score preservation, publication cutoffs, geographic relevance, publisher balancing and URL deduplication.

## Electoral interpretation

These are observable news features for a forecasting model to consume. An issue-response coefficient, demographic response multiplier, or sentiment-to-vote mapping is a **separate modeling assumption** until fitted to appropriately dated historical news and held-out election outcomes. The available current news snapshot cannot train or validate the effect of news on 2026 voting. Publication-date cutoffs also do not reconstruct the exact information available at historical forecast times when a later RSS snapshot was collected. The forecast methodology must report those limits and distinguish fitted 2024 voting baselines from assumed 2026 news responses.

Reference: Hutto, C.J. and Gilbert, E. (2014), *VADER: A Parsimonious Rule-based Model for Sentiment Analysis of Social Media Text*, ICWSM. The VADER package is distributed under the MIT license.
