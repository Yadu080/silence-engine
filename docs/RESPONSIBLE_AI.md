# Responsible use

SILENCE ENGINE is decision support. It proposes questions for people to check; it does not decide anything.

| Principle | How the prototype applies it |
|---|---|
| No personal data | Only aggregate counts per location and day. The schema has no person-level fields; reviewers record a role, not a name; the note field warns against personal information. Imported data must be aggregated first (see DATA_SOURCES.md). |
| Hypotheses, not findings | Categories are worded as "potential", "likely" or "uncertain". Every case page states that a gap in recorded activity is not evidence that anyone was denied a service. |
| Uncertainty is visible | An explicit "Uncertain — investigate" category; evidence sufficiency shown with its formula; limitations listed per case; confidence intervals on every evaluation metric. |
| Competing explanations | Each case shows evidence for and against access-gap, reporting-failure and normal-variation hypotheses. |
| Data problems are not service problems | Feed health is checked independently; missing data is never read as zero; data-quality issues have their own page. |
| Context is not causation | Weather, transport and outage signals are described as coinciding; no causal claims. |
| Machine learning is optional and subordinate | An optional scikit-learn model is benchmarked against the rules on synthetic data, never sees the rules' verdict or any ground-truth label that postdates its training period, and cannot change what is flagged or which cases are shown. Its category is not displayed on cases. It found no detectable advantage, so the explainable rules stay the default; a learned model would need real outcome data and the same human review before being trusted (LIMITATIONS items 19–23). |
| Human in the loop | Recommended verification steps per case; a review panel records the human outcome (under review, verified access gap, verified reporting issue, verified expected, dismissed). No automatic or punitive action exists in the system. |
| Auditability | Every generation, import, analysis, evaluation and review is appended to a SHA-256 hash chain that the Platform page verifies. Case ids are deterministic so reviews survive re-analysis. |
| Fairness across sites | Priority is volume-neutral; evaluation reports performance separately for high-, medium- and low-volume streams and shows that low-volume services are harder to monitor. |
| Honest evaluation | Synthetic data clearly labelled everywhere; labels physically separated from detector inputs; tuning restricted to the training period and tested for leakage; covert failures that no method can catch are included and reported. |

## Before any real deployment

- Agree with service owners what an alert means and who verifies it; never route alerts to performance management.
- Re-tune thresholds on the organisation's own historical data with reviewed outcomes, and re-evaluate.
- Check reporting coverage across sites so that unequal data quality does not translate into unequal scrutiny.
- Add proper authentication and role-based access, and a retention policy for reviews and audit records.
