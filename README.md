# Merlin Control Tower

Databricks App that lets the hyperpersonalisation team safely change Merlin (next-best-action engine) configuration themselves: every change is drafted as a change request, approved by a second person, promoted to the live Unity Catalog tables in one transaction, and can be reverted. All data in this repo is synthetic; no real customer data.

## Status

* Step 1, synthetic data: done (`setup/merlin_demo_setup`)
* Step 2, the app: not started

## Step 1: synthetic data

Run `setup/merlin_demo_setup` on serverless. It creates `merlin_demo.merlin_demo` and runs validation checks at the end. **Rerunning resets the demo** (`CREATE OR REPLACE`), including change requests and audit history.

| Table | Rows | Purpose |
| --- | --- | --- |
| `proposition` | 12 | Products (cards, savings, mortgages, investments, pensions, insurance, loans) with `proposition_weight` |
| `content` | 40 | Tips and offers per proposition and channel; 25% older than 300 days |
| `eligibility_rule` | 30 | Include/exclude rules with `sql_condition`; IDs >= 10000 are per-proposition recently-contacted exclusions |
| `eligibility_rule_set` | 13 | Rule memberships of 6 named rule sets |
| `content_eligibility_rule` | 280 | Content to rule mappings (many-to-many) |
| `customer` | 50,000 | Segment, age band, income band, email opt-in, owned products |
| `model_score` | 600,000 | Propensity score per customer and proposition (`MERLIN_PROP_V3`) |
| `model_metadata` | 8 | weekly_cap, reference_population, exploit/explore/holdout shares, followup_after_days, active_model_id, score_threshold |
| `next_best_experience`, `contact_decision_output` | 0 | Filled by the simulation |
| `change_request`, `change_audit_log` | 0 | Change workflow and audit trail (before-images, Delta versions for revert) |
| `app_roles` | 5 | drafter / approver / viewer |

### Conventions

* Include rules: the customer must match `sql_condition`. Exclude rules: customers matching `sql_condition` are removed. The `{followup_after_days}` placeholder is resolved from `model_metadata`.
* Soft delete: `active_ind` / `active_content_ind` = false, or rule `status` = `retired`.
* Config tables carry `created_by`, `created_at`, `updated_by`, `updated_at`, `version`; every table ends with `audit_timestamp`, `source_system`.
* Allowed values are enforced with Delta CHECK constraints; keys are declared as primary keys.
* Governance: table comments, column comments, TBLPROPERTIES (`quality`, `domain`, `contains_pii`, `pii_columns`) and UC tags `quality`, `data_classification`. The `domain` and `pii` tags are blocked by workspace tag policies and are kept as TBLPROPERTIES.

