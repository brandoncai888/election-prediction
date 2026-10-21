# Historical demographic vote data

The importer uses **actual CES respondents**, rather than deriving demographic votes from precinct totals or multiplying demographic marginals. Individual rows stay in `data/cache`; only aggregate cells are written to SQLite or served to the dashboard.

## Source and reproduction

Shiro Kuriwaki. *Cumulative CES Common Content*, version 10, 2006–2024. [Original dataset DOI](https://doi.org/10.7910/DVN/II2DB6). Data are downloaded from the public [UC Berkeley SDA mirror](https://sda.berkeley.edu/sdaweb/analysis/?dataset=ces-cumulative-2024-v10), listed in the [Berkeley SDA archive](https://sda.berkeley.edu/archive.htm). The export uses SDA's **Download Custom Subset** form, restricted to years 2022 and 2024. Each year contains 60,000 original respondents. The raw cache contains 15 analysis columns, with no case identifiers, addresses, ZIP codes, candidate names, or other direct identifiers. Its adjacent metadata JSON records retrieval time, source URL, selection, columns and SHA256 checksum. The checksum is verified before any cached download is reused.

The original yearly data are [CES Common Content 2022](https://doi.org/10.7910/DVN/PR4L8P) and [CES Common Content 2024](https://doi.org/10.7910/DVN/X11EP6). Harvard Dataverse currently presents a robot-verification challenge from this environment; the public Berkeley mirror supplies a reproducible subset without that dependency. The 2024 yearly dataset has a newer vote-validated release than the cumulative version used here.

Interpretation and recoding were checked against the [CES cumulative guide](https://sda.berkeley.edu/sdaweb/docs/ces-cumulative-2024-v10/DOC/guide_cumulative_2006-2024.pdf), [SDA variable list](https://sda.berkeley.edu/sdaweb/docs/ces-cumulative-2024-v10/DOC/hcbkx01.htm), [demographic codebook](https://sda.berkeley.edu/sdaweb/docs/ces-cumulative-2024-v10/DOC/hcbk0002.htm), and [office-vote codebook](https://sda.berkeley.edu/sdaweb/docs/ces-cumulative-2024-v10/DOC/hcbk0008.htm). The [CES investigators' FAQ](https://tischcollege.tufts.edu/research-faculty/research-centers/cooperative-election-study/cooperative-election-study-faqs) explains population coverage, weights, Hispanic coding, and district representativeness.

## What is estimated

The importer filters to respondents age 25 or older with usable demographics, geography and positive finite weights. For each office it selects reported Democratic or Republican votes from the **chosen candidate's party** fields `voted_rep_party`, `voted_sen_party`, and `voted_gov_party`; it never treats a candidate-position code as a party code. It calculates `sum(weight * Democratic) / sum(weight)` within each race/ethnicity × standardized sex × education cell. Third-party votes, unknown choices, nonvoters, and missing values are excluded from this two-party denominator.

Education is collapsed into four groups: below high school; high school; some college or two-year degree; four-year degree or postgraduate study. Standardized CES sex is retained as male/female; it does not capture the full gender-identity question. Missing standardized-sex responses are excluded.

**2022 uses `weight_post`. 2024 uses `weight` because version 10 contains no 2024 post-election weights.** The cumulative guide allows normal weights as a second-best choice when post-wave weights are unavailable. That alternative leaves a risk of post-wave attrition bias and is recorded as `survey_weighted_preweight`; it must not be described as the final vote-validated 2024 estimate. This importer does not estimate turnout or classify a respondent as a validated voter. The cumulative choice fields can include an early-vote report collected before election day.

Raw sample size counts valid D/R survey responses. `weighted_total` is a sum of survey weights, **not a population total**. Kish effective sample size is `(sum(w))² / sum(w²)` and describes weight concentration; it does not account for clustering or the complete survey design. Cells with either raw or effective sample size below 30 have `dem_share=NULL` and `estimate_kind=survey_suppressed`. This threshold is an initial display rule, not a guarantee of precision. The importer does not manufacture missing cells or substitute state observations for district observations.

## National comparisons and boundaries

House comparisons are available nationally (`us`), by state (`state:FF`) and historical congressional district (`cd:FFDD`). Senate and governor comparisons are available nationally and by state. Their national averages include only respondents reporting a D/R vote in that office, so they describe the subset of states holding the relevant elections, rather than an all-state hypothetical contest. House comparisons exclude nonvoting territories and DC.

Historical district IDs use the district for the **upcoming Congress elected in that year**, preferring post-election geography when present. The importer records `118th_congress` for 2022 and `119th_congress` for 2024. A repeated district number does not establish that its boundaries match 2026. The investigators also state that the weighted CES sample is not designed to represent every congressional district. District results therefore remain descriptive survey estimates, even when their sample sizes pass the display threshold.

## Race definitions prevent automatic forecasting

The CES `race_h` field gives anyone identifying as Hispanic in either the race or ethnicity question a mutually exclusive Hispanic category. Other categories therefore describe non-Hispanic respondents. Its eight groups are white, black, Hispanic, Asian, Native American, mixed race, other race and Middle Eastern. This is stored as `race_scheme=ces_hispanic_exclusive`.

These categories cannot be merged directly with ACS C15002A–G populations, which classify race regardless of Hispanic ethnicity and use different race concepts. A white CES cell is **non-Hispanic white**; a white ACS cell includes white Hispanic residents. The forecasting layer must reject this mismatch. A production poststratification model needs a compatible population joint distribution and survey recoding, plus the missing 18–24 electorate and eligibility/turnout estimates. The historical visualizations remain useful without pretending that the incompatible cells form a valid electoral forecast.

## First verified import

On October 21, 2026, the cached original subset yielded:

| Year | Weight | Usable demographic respondents 25+ | House D/R responses | Total cells | Published cells | Suppressed cells |
|---|---|---:|---:|---:|---:|---:|
| 2022 | Post-wave | 48,036 | 36,138 | 11,770 | 612 | 11,158 |
| 2024 | Pre-wave fallback | 54,724 | 38,986 | 10,996 | 424 | 10,572 |

Counts include every supported office and geography level. The much smaller number of publishable district intersections is an empirical limitation of direct cross-tabs, not an import failure.
