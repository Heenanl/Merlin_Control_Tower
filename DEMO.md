# Merlin Control Tower: demo script

About 15 minutes. All data is synthetic (`merlin_demo.merlin_demo`); no real customer data.

## What the app is

A governance layer over the Merlin next-best-action configuration. Business users never edit live config tables directly: every change is a change request that is drafted, approved by a second person, promoted to the live Unity Catalog tables in one transaction, and can be reverted. Every step is audited.

* **Config the bank owns:** 12 propositions (with a ranking `proposition_weight`), 36 content items on email, push, in-app message and SMS (six propositions on all 4 channels, the other six on 2), 32 eligibility rules (Include / Exclude SQL conditions, including the consent rules per channel), 262 rule-to-content mappings, and model settings (weekly cap, exploit / explore / holdout shares, score threshold).
* **Inputs:** 50k synthetic customers and 600k model scores (one per customer per proposition).
* **Simulation:** keeps only content each customer is eligible for, ranks propositions by `model_score x proposition_weight`, drops scores under the threshold, then allocates customers to exploit (best offer), explore (a random eligible alternative) and holdout (control group, not contacted) within the weekly cap. Results land in `next_best_experience` and `contact_decision_output`.
* **Roles:** drafter, approver, viewer. Approvers cannot approve their own requests, except in demo mode (`MERLIN_ALLOW_SELF_APPROVAL=true`), shown by the yellow banner.

## Before the demo

* Open the app a minute or two early: the SQL warehouse `geniecode-demo` stops after 5 idle minutes, and the first load after that is slow while it starts.
* Run the simulation once so the Overview and Run simulation tabs show results (about 1 to 2 minutes).
* Optional: run `setup/merlin_demo_setup` first for a clean slate. It resets all tables, including change requests and audit history.

## Script

1. **Overview (1 min).** "This is Merlin's live state: live rules, active content, 25% of content is stale (older than 300 days), open change requests and the last simulation run." Point out the demo-mode banner.
2. **Rules & Propositions (2 min).** Show the propositions and eligibility rules tables. "Rules are plain conditions on the customer, for example Exclude age 65+ or Include email opt-in." Optionally mention the new-proposition wizard, which drafts a proposition, its content and its exclusion rule as one request.
3. **Rule x Content matrix (3 min).** "Which rule applies to which content." Click one mapping, for example rule 1016 (Exclude age 65+) on content `1284BM`. "Nothing changes in the live config. It only creates a draft change request."
4. **Change requests, Preview (3 min).** Open the draft and show the planned operations, eligible customers before and after per proposition, and the effect on the weekly cap. "You see the impact before anything goes live." For the example above, P009 eligibility rises from 37,138 to 42,525. The preview takes a few seconds.
5. **Submit, Approve, Promote (2 min).** "In production a second person approves. Promote applies the change to all config tables and the audit log in one atomic transaction: all or nothing." Show the audit history.
6. **Run simulation (2 min).** Show contacts by proposition and channel and explain exploit / explore / holdout. A live rerun takes about 1 to 2 minutes; the button shows Running... until it finishes, or show the results from the pre-demo run.
7. **Revert (1 min).** Revert the promoted request and show the config is back where it was, with the revert also in the audit trail.
8. **Close.** "Business users own the changes, controls are built in, everything is traceable and reversible, and the data never leaves Unity Catalog."

## Likely questions

* **Why does P006 get most contacts?** It took 1,157 of 1,600 contacts in the first run after a reset. P006 (Mortgage Advice Appointment) has the highest `proposition_weight` (1.30), but P012 is close (1.25) and got only 93, so eligibility rules and model scores matter too. Not fully analysed yet. On a second run P006 should drop, because customers contacted in the first run are excluded for `followup_after_days` (14 days); on the previous data set it fell from 1,168 to 620. A good live demo of the recently-contacted rules.
* **Why is the channel split fairly even?** The setup varies content freshness by proposition rather than making push newest everywhere. The first run after reset produced email 367 (22.9%), push 399 (24.9%), in-app 368 (23.0%) and SMS 466 (29.1%). All 1,600 contacts passed channel eligibility and consent checks. This is a synthetic demo calibration, not a production quota; subsequent runs can differ because recent-contact exclusions apply. Proposition scoring and eligibility are unchanged.
* **How big is the holdout?** `holdout_share` (10%) of eligible customers, drawn once per customer with a deterministic hash, so a customer stays in or out of the holdout across runs.
* **Where is the history?** `change_request` holds each request and its operations; `change_audit_log` holds before-images and Delta versions used for revert.
