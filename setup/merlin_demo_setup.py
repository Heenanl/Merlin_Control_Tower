# Databricks notebook source
# /// script
# [tool.databricks.environment]
# environment_version = "6"
# ///
# DBTITLE 1,Title
# MAGIC %md
# MAGIC # Merlin Control Tower: synthetic demo data
# MAGIC
# MAGIC Creates `merlin_demo.merlin_demo` with all Merlin config, customer, scoring, output and governance tables, fully documented (comments, TBLPROPERTIES, UC tags, CHECK constraints), and loads realistic synthetic Dutch-retail-bank data. No real customer data. **Rerunning resets the demo** (`CREATE OR REPLACE`).

# COMMAND ----------

# DBTITLE 1,Configuration
CATALOG = "merlin_demo"
SCHEMA = "merlin_demo"
FQ = f"{CATALOG}.{SCHEMA}"
DOMAIN = "hyperpersonalisation"
SOURCE_SYSTEM = "merlin_demo_synth"
SEED = 42
N_CUSTOMERS = 50_000
CURRENT_USER = spark.sql("SELECT current_user()").first()[0]

spark.sql(f"CREATE SCHEMA IF NOT EXISTS {FQ} COMMENT 'Merlin next-best-action engine: config, scoring and change-management tables for the Merlin Control Tower demo (synthetic data only)'")
print(f"Target: {FQ} | user: {CURRENT_USER}")

# COMMAND ----------

# DBTITLE 1,Table definitions and DDL
WHO = """created_by STRING COMMENT 'User who created the record',
  created_at TIMESTAMP COMMENT 'Record creation timestamp',
  updated_by STRING COMMENT 'User whose promoted change last updated the record',
  updated_at TIMESTAMP COMMENT 'Timestamp of the last promoted update',
  version INT COMMENT 'Record version, incremented on every promoted change'"""
AUDIT = """audit_timestamp TIMESTAMP COMMENT 'Job execution timestamp',
  source_system STRING COMMENT 'Upstream source system identifier'"""
BCN = "business_contact_number STRING NOT NULL COMMENT '[PII: CUSTOMER_ID - HIGH] Synthetic 10-digit business contact number'"

TABLES = {
  "proposition": dict(quality="silver", cls="internal", pii=[],
    comment="Merlin proposition master: products and services that next-best-action content promotes",
    cols=f"""proposition_id STRING NOT NULL COMMENT 'Primary key, format P### (e.g. P001)',
  name STRING COMMENT 'Proposition display name',
  product_family STRING COMMENT 'cards, savings, mortgages, investments, pensions, insurance or loans',
  proposition_weight DOUBLE COMMENT 'Ranking multiplier. Derived: weighted_score = model_score x proposition_weight',
  active_ind BOOLEAN COMMENT 'Soft-delete flag; false means the proposition is retired',
  {WHO},
  {AUDIT},
  CONSTRAINT pk_proposition PRIMARY KEY (proposition_id)""",
    checks={"chk_prop_id": "proposition_id RLIKE '^P[0-9]{3}$'", "chk_prop_weight": "proposition_weight > 0"}),

  "content": dict(quality="silver", cls="internal", pii=[],
    comment="Merlin content catalogue: tips and offers per proposition, channel, language and segment",
    cols=f"""content_id STRING NOT NULL COMMENT 'Primary key: 4 digits + 2 capital letters (e.g. 1217EE)',
  message_type STRING COMMENT 'tip or offer',
  proposition_id STRING COMMENT 'Foreign key to proposition.proposition_id',
  channel_code STRING COMMENT 'Delivery channel: email, push (app notification), in_app (message inside the mobile app) or sms',
  optin_id STRING COMMENT 'Consent (opt-in) required to send this content',
  language_code STRING COMMENT 'NL or EN',
  customer_segment_id STRING COMMENT 'Target customer segment, or ALL',
  content_frequency INT COMMENT 'Maximum sends per customer per frequency period',
  content_frequency_level STRING COMMENT 'Frequency period: weekly, biweekly, monthly or quarterly',
  active_content_ind BOOLEAN COMMENT 'Soft-delete flag; false means the content is retired',
  last_updated_date DATE COMMENT 'Last refresh of the content or its data. Derived staleness flag: older than 300 days',
  {WHO},
  {AUDIT},
  CONSTRAINT pk_content PRIMARY KEY (content_id)""",
    checks={"chk_content_id": "content_id RLIKE '^[0-9]{4}[A-Z]{2}$'",
            "chk_content_msg": "message_type IN ('tip','offer')",
            "chk_content_chan": "channel_code IN ('email','push','in_app','sms')",
            "chk_content_lang": "language_code IN ('NL','EN')",
            "chk_content_freq": "content_frequency > 0",
            "chk_content_freq_lvl": "content_frequency_level IN ('weekly','biweekly','monthly','quarterly')"}),

  "eligibility_rule": dict(quality="silver", cls="internal", pii=[],
    comment="Merlin eligibility rules: include/exclude conditions evaluated against customers before ranking",
    cols=f"""eligibility_rule_id INT NOT NULL COMMENT 'Primary key. IDs >= 10000 are per-proposition recently-contacted exclusions (10000 + proposition number)',
  eligibility_rule_level STRING COMMENT 'customer_segment, channel_code, proposition, optin, content, message_type or language',
  eligibility_rule STRING COMMENT 'Business description of the rule',
  eligibility_rule_type STRING COMMENT 'Include: customer must match sql_condition. Exclude: customers matching sql_condition are removed',
  sql_condition STRING COMMENT 'Spark SQL predicate over customer columns; may contain the placeholder {{followup_after_days}} resolved from model_metadata',
  status STRING COMMENT 'live, draft or retired (soft delete)',
  {WHO},
  {AUDIT},
  CONSTRAINT pk_eligibility_rule PRIMARY KEY (eligibility_rule_id)""",
    checks={"chk_rule_level": "eligibility_rule_level IN ('customer_segment','channel_code','proposition','optin','content','message_type','language')",
            "chk_rule_type": "eligibility_rule_type IN ('Include','Exclude')",
            "chk_rule_status": "status IN ('live','draft','retired')"}),

  "eligibility_rule_set": dict(quality="silver", cls="internal", pii=[],
    comment="Named groups of eligibility rules that are mapped to content together",
    cols=f"""eligibility_rule_set_id STRING NOT NULL COMMENT 'Rule set key; composite primary key with eligibility_rule_id',
  rule_set_name STRING COMMENT 'Business name of the rule set',
  eligibility_rule_id INT NOT NULL COMMENT 'Foreign key to eligibility_rule; composite primary key',
  active_ind BOOLEAN COMMENT 'Soft-delete flag for the membership',
  {WHO},
  {AUDIT},
  CONSTRAINT pk_eligibility_rule_set PRIMARY KEY (eligibility_rule_set_id, eligibility_rule_id)"""),

  "content_eligibility_rule": dict(quality="silver", cls="internal", pii=[],
    comment="Many-to-many mapping between content and the eligibility rules that apply to it",
    cols=f"""content_id STRING NOT NULL COMMENT 'Foreign key to content; composite primary key',
  eligibility_rule_id INT NOT NULL COMMENT 'Foreign key to eligibility_rule; composite primary key',
  eligibility_rule_set_id STRING COMMENT 'Rule set the mapping came from; null when mapped individually',
  active_ind BOOLEAN COMMENT 'Soft-delete flag for the mapping',
  {WHO},
  {AUDIT},
  CONSTRAINT pk_content_eligibility_rule PRIMARY KEY (content_id, eligibility_rule_id)"""),

  "customer": dict(quality="silver", cls="confidential", pii=["business_contact_number"],
    comment="Synthetic customer profile used for Merlin eligibility and scoring",
    cols=f"""{BCN.replace("COMMENT '", "COMMENT 'Primary key. ")},
  segment STRING COMMENT 'RETAIL, PREFERRED, PRIVATE, YOUTH or STUDENT',
  age_band STRING COMMENT 'Age band, e.g. 25-34',
  income_band STRING COMMENT 'Annual gross income band in EUR, e.g. 50-80k',
  has_email_optin BOOLEAN COMMENT 'Customer consented to email marketing (OPT_EMAIL_MARKETING)',
  has_mobile_app BOOLEAN COMMENT 'Customer actively uses the mobile banking app; required for push and in_app content',
  has_push_optin BOOLEAN COMMENT 'Customer consented to push notifications (OPT_PUSH_MARKETING); only possible when has_mobile_app',
  has_sms_optin BOOLEAN COMMENT 'Customer consented to SMS marketing (OPT_SMS_MARKETING)',
  owned_products ARRAY<STRING> COMMENT 'proposition_ids the customer already holds',
  {AUDIT},
  CONSTRAINT pk_customer PRIMARY KEY (business_contact_number)"""),

  "model_score": dict(quality="silver", cls="confidential", pii=["business_contact_number"],
    comment="Propensity model scores per customer and proposition",
    cols=f"""{BCN},
  proposition_id STRING NOT NULL COMMENT 'Foreign key to proposition',
  model_score DOUBLE COMMENT 'Propensity score between 0 and 1',
  model_id STRING NOT NULL COMMENT 'Model that produced the score (see model_metadata.active_model_id)',
  {AUDIT},
  CONSTRAINT pk_model_score PRIMARY KEY (business_contact_number, proposition_id, model_id)""",
    checks={"chk_model_score": "model_score BETWEEN 0 AND 1"}),

  "model_metadata": dict(quality="silver", cls="internal", pii=[],
    comment="Merlin engine configuration as key/value pairs (caps, exploit/explore/holdout shares, thresholds)",
    cols=f"""`key` STRING NOT NULL COMMENT 'Primary key: configuration key',
  value STRING COMMENT 'Configuration value stored as text; cast using value_type',
  value_type STRING COMMENT 'int, double or string',
  description STRING COMMENT 'Business meaning of the setting',
  {WHO},
  {AUDIT},
  CONSTRAINT pk_model_metadata PRIMARY KEY (`key`)""",
    checks={"chk_meta_type": "value_type IN ('int','double','string')"}),

  "next_best_experience": dict(quality="gold", cls="confidential", pii=["business_contact_number"],
    comment="Business aggregation: ranked next-best proposition and content per customer per simulation run",
    cols=f"""run_id STRING COMMENT 'Simulation run identifier',
  {BCN},
  proposition_id STRING COMMENT 'Foreign key to proposition',
  content_id STRING COMMENT 'Foreign key to content',
  model_score DOUBLE COMMENT 'Propensity score used',
  weighted_score DOUBLE COMMENT 'Derived: model_score x proposition_weight',
  rank INT COMMENT 'Derived: rank of weighted_score within the customer (1 = best)',
  allocation STRING COMMENT 'exploit (top-ranked), explore (random eligible alternative) or holdout (control, not contacted)',
  run_timestamp TIMESTAMP COMMENT 'Simulation run timestamp',
  snapshotdate DATE COMMENT 'Snapshot date of the input data',
  {AUDIT}""",
    checks={"chk_nbe_alloc": "allocation IN ('exploit','explore','holdout')"}),

  "contact_decision_output": dict(quality="gold", cls="confidential", pii=["business_contact_number"],
    comment="Business aggregation: final contact decisions (customer x content) per run, capped at weekly_cap",
    cols=f"""{BCN},
  content_id STRING COMMENT 'Foreign key to content',
  run_timestamp TIMESTAMP COMMENT 'Simulation run timestamp',
  snapshotdate DATE COMMENT 'Snapshot date of the input data',
  {AUDIT}"""),

  "change_request": dict(quality="silver", cls="confidential", pii=["drafted_by", "approved_by", "reverted_by", "comment"],
    comment="Merlin Control Tower change requests: every config write is drafted, approved and promoted through this table",
    cols=f"""request_id STRING NOT NULL COMMENT 'Primary key (UUID)',
  title STRING COMMENT 'Short description of the change',
  object_type STRING COMMENT 'Primary table affected, or multi for cross-table changes',
  object_id STRING COMMENT 'Primary record key affected, if a single record',
  change_json STRING COMMENT 'JSON array of operations: table, op (insert, update, delete, soft_delete), key, before, after',
  status STRING COMMENT 'draft, submitted, approved, rejected, promoted or reverted',
  drafted_by STRING COMMENT '[PII: EMAIL - HIGH] Drafter user email',
  approved_by STRING COMMENT '[PII: EMAIL - HIGH] Approver user email; must differ from drafted_by',
  comment STRING COMMENT '[PII: FREEFORM - HIGH] Drafter or approver comment',
  created_at TIMESTAMP COMMENT 'Draft creation timestamp',
  submitted_at TIMESTAMP COMMENT 'Submission timestamp',
  decided_at TIMESTAMP COMMENT 'Approval or rejection timestamp',
  promoted_at TIMESTAMP COMMENT 'Promotion to live tables timestamp',
  reverted_by STRING COMMENT '[PII: EMAIL - HIGH] User who reverted the promoted change',
  reverted_at TIMESTAMP COMMENT 'Revert timestamp',
  updated_at TIMESTAMP COMMENT 'Last status change timestamp',
  {AUDIT},
  CONSTRAINT pk_change_request PRIMARY KEY (request_id)""",
    checks={"chk_cr_status": "status IN ('draft','submitted','approved','rejected','promoted','reverted')"}),

  "change_audit_log": dict(quality="silver", cls="confidential", pii=["actor"],
    comment="Immutable audit trail of change request lifecycle events with before-images for revert",
    cols=f"""audit_id STRING NOT NULL COMMENT 'Primary key (UUID)',
  request_id STRING COMMENT 'Foreign key to change_request',
  action STRING COMMENT 'drafted, submitted, approved, rejected, promoted, promote_failed or reverted',
  actor STRING COMMENT '[PII: EMAIL - HIGH] User who performed the action',
  action_at TIMESTAMP COMMENT 'When the action happened',
  object_type STRING COMMENT 'Table affected',
  object_id STRING COMMENT 'Record key affected',
  before_json STRING COMMENT 'Before-image of the record (used for revert)',
  after_json STRING COMMENT 'After-image of the record',
  table_versions STRING COMMENT 'JSON map of Delta table versions after promotion, for RESTORE-based revert',
  {AUDIT},
  CONSTRAINT pk_change_audit_log PRIMARY KEY (audit_id)""",
    checks={"chk_audit_action": "action IN ('drafted','submitted','approved','rejected','promoted','promote_failed','reverted')"}),

  "app_roles": dict(quality="silver", cls="confidential", pii=["user_email"],
    comment="Merlin Control Tower role assignments (drafter, approver, viewer)",
    cols=f"""user_email STRING NOT NULL COMMENT '[PII: EMAIL - HIGH] User email as forwarded by Databricks Apps; composite primary key',
  role STRING NOT NULL COMMENT 'drafter, approver or viewer; composite primary key',
  {AUDIT},
  CONSTRAINT pk_app_roles PRIMARY KEY (user_email, role)""",
    checks={"chk_role": "role IN ('drafter','approver','viewer')"}),
}

# Tables the Control Tower app writes inside BEGIN ATOMIC transactions need catalog commits.
TXN_TABLES = {"proposition", "content", "eligibility_rule", "eligibility_rule_set", "content_eligibility_rule",
              "model_metadata", "next_best_experience", "contact_decision_output", "change_request", "change_audit_log"}

skipped_tags = set()
for name, t in TABLES.items():
    fq = f"{FQ}.{name}"
    has_pii = bool(t["pii"])
    comment = t["comment"] + (f" - CONTAINS PII: {', '.join(t['pii'])}" if has_pii else "")
    props = {"quality": t["quality"], "domain": DOMAIN, "delta.enableChangeDataFeed": "true",
             "contains_pii": str(has_pii).lower()}
    if has_pii:
        props["pii_columns"] = ",".join(t["pii"])
    if name in TXN_TABLES:
        props["delta.feature.catalogManaged"] = "supported"
    props_sql = ", ".join(f"'{k}' = '{v}'" for k, v in props.items())
    spark.sql(f"CREATE OR REPLACE TABLE {fq} (\n  {t['cols']}\n) COMMENT '{comment}' TBLPROPERTIES ({props_sql})")
    for cname, expr in t.get("checks", {}).items():
        spark.sql(f"ALTER TABLE {fq} DROP CONSTRAINT IF EXISTS {cname}")
        spark.sql(f"ALTER TABLE {fq} ADD CONSTRAINT {cname} CHECK ({expr})")
    # Apply tags one by one: governed tag policies may restrict allowed values per key.
    tags = {"quality": t["quality"], "domain": DOMAIN, "pii": str(has_pii).lower(), "data_classification": t["cls"]}
    for k, v in tags.items():
        try:
            spark.sql(f"ALTER TABLE {fq} SET TAGS ('{k}' = '{v}')")
        except Exception as e:
            if "TAG_POLICY_VALUE_NOT_ALLOWED" in str(e):
                skipped_tags.add(f"{k}={v}")
            else:
                raise
    print(f"created {fq}")
if skipped_tags:
    print(f"WARN: tag values blocked by governed tag policies (kept as TBLPROPERTIES instead): {sorted(skipped_tags)}")

# COMMAND ----------

# DBTITLE 1,Write helper, propositions, content, model_metadata, app_roles
import random, datetime
from pyspark.sql import functions as F

rng = random.Random(SEED)
TODAY = datetime.date.today()

def write_table(df, name):
    """Overwrite rows positionally so table comments, constraints and tags are preserved."""
    t = f"{FQ}.{name}"
    cols = spark.table(t).columns
    defaults = {"created_by": F.lit(CURRENT_USER), "created_at": F.current_timestamp(),
                "updated_by": F.lit(CURRENT_USER), "updated_at": F.current_timestamp(),
                "version": F.lit(1), "audit_timestamp": F.current_timestamp(),
                "source_system": F.lit(SOURCE_SYSTEM)}
    for c in cols:
        if c not in df.columns:
            df = df.withColumn(c, defaults[c])
    df.select(*cols).write.insertInto(t, overwrite=True)
    print(f"{name}: {spark.table(t).count():,} rows")

# --- proposition --------------------------------------------------------------
PROPOSITIONS = [
    ("P001", "Credit Card Gold",              "cards",       1.10, True),
    ("P002", "Student Credit Card",           "cards",       0.90, True),
    ("P003", "Term Deposit 1 Year",           "savings",     0.95, True),
    ("P004", "Flexible Savings Account",      "savings",     0.85, True),
    ("P005", "Kids Savings Account",          "savings",     0.80, True),
    ("P006", "Mortgage Advice Appointment",   "mortgages",   1.30, True),
    ("P007", "Mortgage Interest Rate Review", "mortgages",   1.20, False),
    ("P008", "Investment Starter",            "investments", 1.15, True),
    ("P009", "Pension Savings (Lijfrente)",   "pensions",    1.05, True),
    ("P010", "Home Insurance",                "insurance",   1.00, True),
    ("P011", "Travel Insurance",              "insurance",   0.75, True),
    ("P012", "Green Home Improvement Loan",   "loans",       1.25, True),
]
write_table(spark.createDataFrame(PROPOSITIONS,
    "proposition_id string, name string, product_family string, proposition_weight double, active_ind boolean"),
    "proposition")

# --- content (36 rows; 9 = 25% older than 300 days) ---------------------------------------------
# Channels: email, push (app notification), in_app (message inside the app), sms.
# Six propositions (P001, P002, P006, P008, P009, P012) have one item on every channel; the other six have two.
# The simulation sends the newest eligible item. DEMO_CONTENT_AGES below varies channel order
# to balance the first-run channel split without changing eligibility, scoring or consent.
# age_days = days since last_updated_date (> 300 = stale). Content of the inactive proposition P007 is inactive.
CONTENT = [  # content_id, message_type, proposition_id, channel_code, language_code, customer_segment_id, age_days
    # P001 Credit Card Gold: all channels
    ("1211SV", "offer", "P001", "push",   "NL", "ALL",        20),
    ("1200EN", "offer", "P001", "in_app", "EN", "ALL",        45),
    ("1205MA", "offer", "P001", "sms",    "NL", "ALL",        90),
    ("1206BM", "tip",   "P001", "email",  "NL", "ALL",       150),
    # P002 Student Credit Card: all channels
    ("1218MA", "tip",   "P002", "push",   "NL", "STUDENT",    25),
    ("1214EN", "offer", "P002", "in_app", "EN", "STUDENT",    60),
    ("1217SM", "offer", "P002", "sms",    "NL", "STUDENT",   110),
    ("1216OF", "offer", "P002", "email",  "NL", "STUDENT",   493),  # stale
    # P003 Term Deposit 1 Year
    ("1229MA", "offer", "P003", "sms",    "EN", "ALL",       140),
    ("1221BM", "offer", "P003", "email",  "NL", "ALL",       716),  # stale
    # P004 Flexible Savings Account
    ("1240EE", "tip",   "P004", "push",   "NL", "ALL",        40),
    ("1230MA", "offer", "P004", "in_app", "EN", "ALL",       140),
    # P005 Kids Savings Account
    ("1249EN", "tip",   "P005", "in_app", "NL", "RETAIL",     70),
    ("1244IN", "offer", "P005", "email",  "NL", "RETAIL",    382),  # stale
    # P006 Mortgage Advice Appointment: all channels
    ("1257OF", "offer", "P006", "push",   "NL", "RETAIL",     16),
    ("1262IN", "tip",   "P006", "in_app", "EN", "RETAIL",     40),
    ("1254EN", "tip",   "P006", "email",  "NL", "RETAIL",    130),
    ("1253BM", "offer", "P006", "sms",    "NL", "RETAIL",    463),  # stale
    # P007 Mortgage Interest Rate Review (inactive proposition)
    ("1264CR", "offer", "P007", "push",   "EN", "RETAIL",    245),
    ("1266CR", "tip",   "P007", "sms",    "NL", "RETAIL",    330),  # stale
    # P008 Investment Starter: all channels
    ("1280MA", "tip",   "P008", "push",   "NL", "PREFERRED",  30),
    ("1272MA", "offer", "P008", "in_app", "EN", "PREFERRED",  53),
    ("1281EE", "offer", "P008", "sms",    "NL", "PREFERRED", 120),
    ("1275CR", "offer", "P008", "email",  "NL", "PREFERRED", 410),  # stale
    # P009 Pension Savings (Lijfrente): all channels
    ("1288IN", "tip",   "P009", "push",   "NL", "ALL",        35),
    ("1289AP", "offer", "P009", "in_app", "NL", "ALL",        67),
    ("1291NL", "offer", "P009", "sms",    "NL", "ALL",       159),
    ("1284BM", "offer", "P009", "email",  "EN", "ALL",       520),  # stale
    # P010 Home Insurance
    ("1296OF", "offer", "P010", "in_app", "EN", "ALL",        86),
    ("1301TP", "tip",   "P010", "sms",    "NL", "ALL",       320),  # stale
    # P011 Travel Insurance
    ("1305EN", "offer", "P011", "push",   "NL", "ALL",       120),
    ("1304BM", "offer", "P011", "sms",    "NL", "ALL",       181),
    # P012 Green Home Improvement Loan: all channels
    ("1313MA", "offer", "P012", "push",   "NL", "PREFERRED",  22),
    ("1317NL", "tip",   "P012", "in_app", "NL", "PREFERRED",  80),
    ("1315SM", "offer", "P012", "sms",    "NL", "PREFERRED", 150),
    ("1314BM", "offer", "P012", "email",  "EN", "PREFERRED", 356),  # stale
]
# Demo-only freshness ordering calibrated on the seeded population; not production channel quotas.
# Keeps 9 of 36 items stale (>300 days). Later runs vary due to recent-contact exclusions.
DEMO_CONTENT_AGES = {
    "1200EN": 20, "1211SV": 55, "1206BM": 120, "1205MA": 410,
    "1214EN": 25, "1218MA": 60, "1217SM": 110, "1216OF": 493,
    "1221BM": 70, "1229MA": 716, "1230MA": 40, "1240EE": 140,
    "1249EN": 70, "1244IN": 260,
    "1253BM": 16, "1257OF": 45, "1254EN": 130, "1262IN": 463,
    "1264CR": 245, "1266CR": 330,
    "1272MA": 30, "1280MA": 53, "1275CR": 120, "1281EE": 410,
    "1289AP": 35, "1288IN": 67, "1291NL": 159, "1284BM": 520,
    "1296OF": 86, "1301TP": 320, "1305EN": 120, "1304BM": 181,
    "1317NL": 22, "1313MA": 80, "1314BM": 150, "1315SM": 356,
}
assert set(DEMO_CONTENT_AGES) == {row[0] for row in CONTENT}
CONTENT = [(*row[:-1], DEMO_CONTENT_AGES[row[0]]) for row in CONTENT]
ALL_CHANNEL_PROPS = ["P001", "P002", "P006", "P008", "P009", "P012"]
CHANNEL_OPTIN = {"email": "OPT_EMAIL_MARKETING", "push": "OPT_PUSH_MARKETING",
                 "in_app": "OPT_INAPP_SERVICE", "sms": "OPT_SMS_MARKETING"}
PROP_ACTIVE = {pid: active for pid, *_, active in PROPOSITIONS}
content_rows = [
    (cid, msg, pid, chan, CHANNEL_OPTIN[chan],
     lang, seg, 1, "monthly", PROP_ACTIVE[pid], TODAY - datetime.timedelta(days=age))
    for cid, msg, pid, chan, lang, seg, age in CONTENT
]
write_table(spark.createDataFrame(content_rows,
    "content_id string, message_type string, proposition_id string, channel_code string, optin_id string, "
    "language_code string, customer_segment_id string, content_frequency int, content_frequency_level string, "
    "active_content_ind boolean, last_updated_date date"), "content")

# --- model_metadata -----------------------------------------------------------
METADATA = [
    ("weekly_cap", "160000", "int", "Maximum contacts per week across all propositions (production scale)"),
    ("reference_population", "5000000", "int", "Production customer base; weekly_cap is scaled by synthetic population / reference_population"),
    ("exploit_share", "0.75", "double", "Share of contacts given the top-ranked proposition"),
    ("explore_share", "0.25", "double", "Share of contacts given a random eligible alternative"),
    ("holdout_share", "0.10", "double", "Share of eligible customers kept as control group (not contacted)"),
    ("followup_after_days", "14", "int", "Days before a customer can be contacted again for the same proposition"),
    ("active_model_id", "MERLIN_PROP_V3", "string", "Model whose scores are used by the simulation"),
    ("score_threshold", "0.30", "double", "Minimum weighted_score for a customer-proposition to be considered"),
]
write_table(spark.createDataFrame(METADATA, "key string, value string, value_type string, description string"),
            "model_metadata")

# --- app_roles (current user is drafter + approver; self-approval is blocked by the app) ---
ROLES = [(CURRENT_USER, "drafter"), (CURRENT_USER, "approver"),
         ("merlin.drafter@example.com", "drafter"), ("merlin.approver@example.com", "approver"),
         ("merlin.viewer@example.com", "viewer")]
write_table(spark.createDataFrame(ROLES, "user_email string, role string"), "app_roles")

# COMMAND ----------

# DBTITLE 1,Eligibility rules, rule sets and content mappings
# Include = customer must satisfy sql_condition; Exclude = customers satisfying sql_condition are removed.
GENERAL_RULES = [
    (1001, "customer_segment", "Retail and Preferred Banking customers", "Include", "segment IN ('RETAIL','PREFERRED')", "live"),
    (1002, "customer_segment", "Private Banking customers only", "Include", "segment = 'PRIVATE'", "live"),
    (1003, "customer_segment", "Exclude Youth customers (under 18)", "Exclude", "segment = 'YOUTH'", "live"),
    (1004, "customer_segment", "Students only", "Include", "segment = 'STUDENT'", "live"),
    (1005, "optin", "Customer has email marketing opt-in", "Include", "has_email_optin = true", "live"),
    (1006, "channel_code", "Customer actively uses the mobile app (push and in-app content)", "Include", "has_mobile_app = true", "live"),
    (1007, "language", "Dutch-language content is available to every customer", "Include", "true", "live"),
    (1008, "message_type", "No commercial offers to Youth customers", "Exclude", "segment = 'YOUTH'", "live"),
    (1009, "proposition", "Does not already own a credit card", "Exclude", "arrays_overlap(owned_products, array('P001','P002'))", "live"),
    (1010, "proposition", "Does not already have a mortgage advice appointment", "Exclude", "array_contains(owned_products, 'P006')", "live"),
    (1011, "proposition", "Does not already hold an investment account", "Exclude", "array_contains(owned_products, 'P008')", "live"),
    (1012, "proposition", "Does not already have home insurance", "Exclude", "array_contains(owned_products, 'P010')", "live"),
    (1013, "proposition", "Annual income of at least EUR 50k", "Include", "income_band IN ('50-80k','80-120k','>120k')", "live"),
    (1014, "proposition", "Age 25-64 for mortgage propositions", "Include", "age_band IN ('25-34','35-44','45-54','55-64')", "live"),
    (1015, "proposition", "Adults only (18+) for financial products", "Include", "age_band <> '12-17'", "live"),
    (1016, "content", "No pension savings tips to customers aged 65+", "Exclude", "age_band = '65+'", "live"),
    (1017, "message_type", "Offers only for annual income of EUR 30k or more", "Include", "income_band <> '<30k'", "draft"),
    (1018, "proposition", "Green loan: likely homeowner (has home insurance or mortgage advice)", "Include", "arrays_overlap(owned_products, array('P006','P010'))", "draft"),
    (1019, "optin", "Customer has push notification opt-in", "Include", "has_push_optin = true", "live"),
    (1020, "optin", "Customer has SMS marketing opt-in", "Include", "has_sms_optin = true", "live"),
]
EXCLUSION_RULES = [
    (10000 + int(pid[1:]), "proposition",
     f"Exclude customers contacted for {name} in the last followup_after_days days", "Exclude",
     f"business_contact_number IN (SELECT d.business_contact_number FROM {FQ}.contact_decision_output d "
     f"JOIN {FQ}.content c ON d.content_id = c.content_id WHERE c.proposition_id = '{pid}' "
     f"AND d.snapshotdate >= date_sub(current_date(), {{followup_after_days}}))", "live")
    for pid, name, *_ in PROPOSITIONS]
write_table(spark.createDataFrame(GENERAL_RULES + EXCLUSION_RULES,
    "eligibility_rule_id int, eligibility_rule_level string, eligibility_rule string, "
    "eligibility_rule_type string, sql_condition string, status string"), "eligibility_rule")

# --- rule sets ----------------------------------------------------------------
RULE_SETS = {
    "RS_CREDIT":    ("Credit products baseline",   [1003, 1015]),
    "RS_SAVINGS":   ("Savings baseline",           [1015]),
    "RS_MORTGAGE":  ("Mortgage baseline",          [1003, 1014, 1013]),
    "RS_INVEST":    ("Investment baseline",        [1003, 1015, 1013]),
    "RS_PENSION":   ("Pension baseline",           [1003, 1015]),
    "RS_INSURANCE": ("Insurance baseline",         [1003, 1015]),
}
write_table(spark.createDataFrame(
    [(sid, sname, rid, True) for sid, (sname, rids) in RULE_SETS.items() for rid in rids],
    "eligibility_rule_set_id string, rule_set_name string, eligibility_rule_id int, active_ind boolean"),
    "eligibility_rule_set")

# --- content <-> rule mappings ------------------------------------------------
PROP_SET = {"P001": "RS_CREDIT", "P002": "RS_CREDIT", "P003": "RS_SAVINGS", "P004": "RS_SAVINGS",
            "P005": "RS_SAVINGS", "P006": "RS_MORTGAGE", "P007": "RS_MORTGAGE", "P012": "RS_MORTGAGE",
            "P008": "RS_INVEST", "P009": "RS_PENSION", "P010": "RS_INSURANCE", "P011": "RS_INSURANCE"}
PROP_RULES = {"P001": [1009, 1013], "P002": [1009], "P006": [1010], "P008": [1011],
              "P009": [1016], "P010": [1012], "P012": [1018]}
SEGMENT_RULE = {"RETAIL": 1001, "PREFERRED": 1001, "PRIVATE": 1002, "STUDENT": 1004}
# Who can be reached on each channel: email opt-in, app user (+ push opt-in), SMS opt-in
CHANNEL_RULES = {"email": [1005], "push": [1006, 1019], "in_app": [1006], "sms": [1020]}

mappings = {}
def add_map(cid, rid, sid=None):
    mappings.setdefault((cid, rid), sid)

for (cid, msg, pid, chan, _, lang, seg, *_rest) in content_rows:
    sid = PROP_SET[pid]
    for rid in RULE_SETS[sid][1]:
        add_map(cid, rid, sid)
    for rid in PROP_RULES.get(pid, []):
        add_map(cid, rid)
    if seg in SEGMENT_RULE:
        add_map(cid, SEGMENT_RULE[seg])
    for rid in CHANNEL_RULES[chan]:
        add_map(cid, rid)
    if lang == "NL":
        add_map(cid, 1007)
    if msg == "offer":
        add_map(cid, 1008)
    add_map(cid, 10000 + int(pid[1:]))          # recently-contacted exclusion for its proposition

write_table(spark.createDataFrame([(c, r, s, True) for (c, r), s in mappings.items()],
    "content_id string, eligibility_rule_id int, eligibility_rule_set_id string, active_ind boolean"),
    "content_eligibility_rule")

# COMMAND ----------

# DBTITLE 1,Customers (50k) and model scores
# Materialise each random draw once per row; reusing F.rand() inside chained when() re-draws per branch.
seg_r, age_r, inc_r, opt_r = (F.col(c) for c in ["seg_r", "age_r", "inc_r", "opt_r"])
seg = F.col("segment")

customers = (
    spark.range(N_CUSTOMERS, numPartitions=8)   # fixed partitioning keeps seeded rand() reproducible
    .withColumns({"seg_r": F.rand(SEED + 1), "age_r": F.rand(SEED + 2), "inc_r": F.rand(SEED + 3), "opt_r": F.rand(SEED + 4)})
    .withColumn("business_contact_number", F.format_string("%010d", F.col("id") * 37 + 400000017))
    .withColumn("segment", F.when(seg_r < 0.55, "RETAIL").when(seg_r < 0.80, "PREFERRED")
                .when(seg_r < 0.85, "PRIVATE").when(seg_r < 0.93, "YOUTH").otherwise("STUDENT"))
    .withColumn("age_band",
        F.when(seg == "YOUTH", "12-17")
         .when(seg == "STUDENT", F.when(age_r < 0.85, "18-24").otherwise("25-34"))
         .when(age_r < 0.08, "18-24").when(age_r < 0.27, "25-34").when(age_r < 0.46, "35-44")
         .when(age_r < 0.64, "45-54").when(age_r < 0.82, "55-64").otherwise("65+"))
    .withColumn("income_band",
        F.when(seg.isin("YOUTH", "STUDENT"), "<30k")
         .when(seg == "PRIVATE", ">120k")
         .when(seg == "PREFERRED", F.when(inc_r < 0.30, "50-80k").when(inc_r < 0.75, "80-120k").otherwise(">120k"))
         .when(inc_r < 0.30, "<30k").when(inc_r < 0.65, "30-50k").when(inc_r < 0.90, "50-80k").otherwise("80-120k"))
    .withColumn("has_email_optin", opt_r < F.when(seg == "YOUTH", 0.40).otherwise(0.72))
    # New seeds for the channel flags, so the columns above keep their values
    .withColumns({"app_r": F.rand(SEED + 5), "push_r": F.rand(SEED + 6), "sms_r": F.rand(SEED + 7)})
    .withColumn("has_mobile_app", F.col("app_r") < F.when(seg.isin("YOUTH", "STUDENT"), 0.90)
                                                  .when(F.col("age_band") == "65+", 0.50).otherwise(0.80))
    .withColumn("has_push_optin", F.col("has_mobile_app") & (F.col("push_r") < 0.60))
    .withColumn("has_sms_optin", F.col("sms_r") < F.when(seg == "YOUTH", 0.15).otherwise(0.35))
    .drop("seg_r", "age_r", "inc_r", "opt_r", "app_r", "push_r", "sms_r")
)

adult = ~seg.isin("YOUTH", "STUDENT")
age = F.col("age_band")
OWNERSHIP = [  # (proposition_id, probability, who can own it)
    ("P001", 0.30, adult), ("P002", 0.35, seg == "STUDENT"), ("P003", 0.12, seg != "YOUTH"),
    ("P004", 0.55, F.lit(True)), ("P005", 0.15, adult), ("P006", 0.10, adult & age.isin("25-34", "35-44", "45-54", "55-64")),
    ("P007", 0.05, adult & age.isin("35-44", "45-54", "55-64")), ("P008", 0.18, seg != "YOUTH"),
    ("P009", 0.12, adult & (age != "18-24")), ("P010", 0.40, adult), ("P011", 0.25, seg != "YOUTH"),
    ("P012", 0.03, adult),
]
customers = customers.withColumn("owned_products", F.array_compact(F.array(*[
    F.when(cond & (F.rand(SEED + 10 + k) < p), F.lit(pid)) for k, (pid, p, cond) in enumerate(OWNERSHIP)])))
write_table(customers.drop("id"), "customer")

# --- model_score: one score per customer x proposition (600k rows) -------------
fam, pid_c = F.col("product_family"), F.col("proposition_id")
props_b = F.broadcast(spark.table(f"{FQ}.proposition").select("proposition_id", "product_family"))
scores = (
    spark.table(f"{FQ}.customer").select("business_contact_number", "segment", "age_band", "income_band", "owned_products")
    .crossJoin(props_b)
    .withColumn("raw", F.pow(F.rand(SEED + 100), F.lit(1.8)) +              # right-skewed base propensity
        F.when((fam == "investments") & F.col("income_band").isin("80-120k", ">120k"), 0.20)
         .when((fam == "mortgages") & age.isin("25-34", "35-44"), 0.20)
         .when((pid_c == "P002") & (seg == "STUDENT"), 0.25)
         .when((fam == "pensions") & age.isin("45-54", "55-64"), 0.15)
         .otherwise(0.0))
    .withColumn("raw", F.when(F.array_contains("owned_products", pid_c), F.col("raw") * 0.15).otherwise(F.col("raw")))
    .withColumn("raw", F.when((seg == "YOUTH") & fam.isin("cards", "mortgages", "loans", "investments"),
                              F.col("raw") * 0.10).otherwise(F.col("raw")))
    .withColumn("model_score", F.round(F.least(F.greatest(F.col("raw"), F.lit(0.0)), F.lit(1.0)), 4))
    .withColumn("model_id", F.lit("MERLIN_PROP_V3"))
    .select("business_contact_number", "proposition_id", "model_score", "model_id")
)
write_table(scores, "model_score")

# COMMAND ----------

# DBTITLE 1,Validation
T = lambda n: spark.table(f"{FQ}.{n}")

print("Row counts:")
for n in TABLES:
    print(f"  {n:28s} {T(n).count():>9,}")

checks = {}
c = T("content")
checks["content: 25% older than 300 days"] = c.filter(F.datediff(F.current_date(), "last_updated_date") > 300).count() / c.count() == 0.25
checks["content: unique content_id"] = c.select("content_id").distinct().count() == c.count()
per_prop = {r["proposition_id"]: (r["n"], r["ch"]) for r in c.groupBy("proposition_id").agg(
    F.count("*").alias("n"), F.countDistinct("channel_code").alias("ch")).collect()}
checks["content: 36 items, at most one per proposition and channel"] = c.count() == 36 and all(n == ch for n, ch in per_prop.values())
checks["content: all 4 channels for P001, P002, P006, P008, P009, P012; 2 for the others"] = all(
    per_prop[p][1] == (4 if p in ALL_CHANNEL_PROPS else 2) for p, *_ in PROPOSITIONS)
checks["content: only email, push, in_app, sms"] = {r[0] for r in c.select("channel_code").distinct().collect()} == {"email", "push", "in_app", "sms"}

r = T("eligibility_rule")
excl = r.filter("eligibility_rule_id >= 10000").withColumn("pid", F.regexp_extract("sql_condition", "proposition_id = '(P[0-9]{3})'", 1))
checks["rules: 12 exclusions, id = 10000 + proposition number"] = (
    excl.count() == 12 and excl.filter(F.col("eligibility_rule_id") != 10000 + F.substring("pid", 2, 3).cast("int")).count() == 0)
checks["rules: total 32"] = r.count() == len(GENERAL_RULES) + len(EXCLUSION_RULES) == 32

m = T("content_eligibility_rule")
checks["mappings: no orphan content"] = m.join(c, "content_id", "left_anti").count() == 0
checks["mappings: no orphan rules"] = m.join(r, "eligibility_rule_id", "left_anti").count() == 0
checks["mappings: every content mapped"] = c.join(m, "content_id", "left_anti").count() == 0
checks["rule sets: no orphan rules"] = T("eligibility_rule_set").join(r, "eligibility_rule_id", "left_anti").count() == 0

md = {row["key"]: row["value"] for row in T("model_metadata").collect()}
checks["metadata: exploit + explore = 1"] = abs(float(md["exploit_share"]) + float(md["explore_share"]) - 1) < 1e-9

cu, ms = T("customer"), T("model_score")
seg_share = {r["segment"]: r["count"] / N_CUSTOMERS for r in cu.groupBy("segment").count().collect()}
TARGET_SHARE = {"RETAIL": 0.55, "PREFERRED": 0.25, "PRIVATE": 0.05, "YOUTH": 0.08, "STUDENT": 0.07}
checks["customer: segment mix within 1pt of target"] = all(abs(seg_share.get(s, 0) - p) < 0.01 for s, p in TARGET_SHARE.items())
checks["customer: push opt-in only for app users"] = cu.filter("has_push_optin AND NOT has_mobile_app").count() == 0
checks["customer: 50,000 unique"] = cu.select("business_contact_number").distinct().count() == N_CUSTOMERS
checks["model_score: 1 per customer x proposition"] = ms.count() == N_CUSTOMERS * len(PROPOSITIONS) == ms.select("business_contact_number", "proposition_id").distinct().count()
checks["model_score: no orphan customers"] = ms.join(cu, "business_contact_number", "left_anti").count() == 0
checks["empty output/change tables"] = all(T(n).count() == 0 for n in ["next_best_experience", "contact_decision_output", "change_request", "change_audit_log"])
checks["governance: last two columns are audit_timestamp, source_system"] = all(T(n).columns[-2:] == ["audit_timestamp", "source_system"] for n in TABLES)

for k, v in checks.items():
    print(("PASS " if v else "FAIL ") + k)
assert all(checks.values()), "Validation failed"

display(spark.sql(f"""SELECT table_name, comment FROM {CATALOG}.information_schema.tables
                     WHERE table_schema = '{SCHEMA}' ORDER BY table_name"""))
display(cu.groupBy("segment").agg(F.count("*").alias("customers"), F.round(F.avg(F.size("owned_products")), 2).alias("avg_owned"),
                                  F.round(F.avg(F.col("has_email_optin").cast("int")), 2).alias("email_optin_rate")).orderBy("segment"))
display(ms.groupBy("proposition_id").agg(F.round(F.avg("model_score"), 3).alias("avg_score"),
                                         F.round(F.avg((F.col("model_score") >= 0.30).cast("int")), 3).alias("share_ge_0_30")).orderBy("proposition_id"))

# COMMAND ----------

# DBTITLE 1,Seed Lakebase (main config store)
# Lakebase Postgres is the app's main store for config, change requests and the audit log.
# The UC tables above are kept as a mirror by the app (the simulation reads them).
# This cell rebuilds the Postgres schema from the UC definitions and copies every row, so a reset
# leaves both stores identical. Database and schema are both named merlin_demo, so the app's fully
# qualified names (merlin_demo.merlin_demo.<table>) are valid in Postgres too.
import importlib.util, subprocess, sys
if importlib.util.find_spec("psycopg") is None:
    subprocess.check_call([sys.executable, "-m", "pip", "install", "-q", "psycopg[binary]>=3.1"])
import psycopg
from databricks.sdk import WorkspaceClient

LAKEBASE_ENDPOINT = "projects/merlin-control-tower/branches/production/endpoints/primary"
PG_DATABASE = CATALOG          # merlin_demo
PG_SCHEMA = SCHEMA             # merlin_demo
APP_PG_ROLE = None             # app service principal client id; set after the app has the Lakebase resource
LB_TABLES = {  # table: primary key
    "proposition": ["proposition_id"], "content": ["content_id"], "eligibility_rule": ["eligibility_rule_id"],
    "eligibility_rule_set": ["eligibility_rule_set_id", "eligibility_rule_id"],
    "content_eligibility_rule": ["content_id", "eligibility_rule_id"], "model_metadata": ["key"],
    "app_roles": ["user_email", "role"], "change_request": ["request_id"], "change_audit_log": ["audit_id"],
}
PG_TYPES = {"string": "text", "int": "integer", "bigint": "bigint", "double": "double precision",
            "boolean": "boolean", "date": "date", "timestamp": "timestamptz"}

w = WorkspaceClient()
pg_host = w.postgres.get_endpoint(name=LAKEBASE_ENDPOINT).status.hosts.host
pg_user = w.current_user.me().user_name
def pg_connect(dbname):
    return psycopg.connect(host=pg_host, dbname=dbname, user=pg_user, sslmode="require",
                           password=w.postgres.generate_database_credential(endpoint=LAKEBASE_ENDPOINT).token)

with pg_connect("databricks_postgres") as c:
    c.autocommit = True
    if not c.execute("SELECT 1 FROM pg_database WHERE datname = %s", (PG_DATABASE,)).fetchone():
        c.execute(f'CREATE DATABASE "{PG_DATABASE}"')

uc_cols = spark.sql(f"""SELECT table_name, column_name, full_data_type, comment FROM {CATALOG}.information_schema.columns
  WHERE table_schema = '{SCHEMA}' AND table_name IN ({', '.join(repr(t) for t in LB_TABLES)}) ORDER BY table_name, ordinal_position""").collect()
uc_comments = {r.table_name: r.comment for r in spark.sql(f"""SELECT table_name, comment FROM {CATALOG}.information_schema.tables
  WHERE table_schema = '{SCHEMA}' AND table_name IN ({', '.join(repr(t) for t in LB_TABLES)})""").collect()}
cols_by_table = {t: [r for r in uc_cols if r.table_name == t] for t in LB_TABLES}

with pg_connect(PG_DATABASE) as c:   # one transaction: the whole reset applies or nothing does
    c.execute("SET TIME ZONE 'UTC'")
    c.execute(f"DROP SCHEMA IF EXISTS {PG_SCHEMA} CASCADE")
    c.execute(f"CREATE SCHEMA {PG_SCHEMA}")
    c.execute(f"COMMENT ON SCHEMA {PG_SCHEMA} IS 'Merlin Control Tower main store: config, change requests and audit log (synthetic demo data)'")
    # Databricks SQL compatibility: the app sends the same SQL text to both stores
    c.execute(f"CREATE FUNCTION {PG_SCHEMA}.datediff(a date, b date) RETURNS integer LANGUAGE sql IMMUTABLE AS 'SELECT a - b'")
    c.execute(f"CREATE DOMAIN {PG_SCHEMA}.string AS text")             # CAST(x AS string)
    c.execute(f"CREATE DOMAIN {PG_SCHEMA}.double AS double precision")  # CAST(x AS double)
    # The app connects as its service principal (role created by the app's `postgres` resource)
    APP_SP = "a290334a-61d2-4d81-9e3a-a1928705aa73"  # merlin-control-tower service principal client ID
    if c.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (APP_SP,)).fetchone():
        c.execute(f'GRANT USAGE ON SCHEMA {PG_SCHEMA} TO "{APP_SP}"')
        c.execute(f'GRANT EXECUTE ON FUNCTION {PG_SCHEMA}.datediff(date, date) TO "{APP_SP}"')
        c.execute(f'ALTER DEFAULT PRIVILEGES IN SCHEMA {PG_SCHEMA} GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO "{APP_SP}"')
    else:
        print(f"WARNING: app role {APP_SP} not found; attach the Lakebase resource to the app, then rerun")
    for t, pk in LB_TABLES.items():
        col_sql = ",\n  ".join(f'"{r.column_name}" {PG_TYPES[r.full_data_type]}' + (" NOT NULL" if r.column_name in pk else "") for r in cols_by_table[t])
        c.execute(f"CREATE TABLE {PG_SCHEMA}.{t} (\n  {col_sql},\n  PRIMARY KEY ({', '.join(pk)})\n)")
        c.execute(f"COMMENT ON TABLE {PG_SCHEMA}.{t} IS %s".replace("%s", "'" + (uc_comments.get(t) or "").replace("'", "''") + "'"))
        for r in cols_by_table[t]:
            if r.comment:
                c.execute(f'COMMENT ON COLUMN {PG_SCHEMA}.{t}."{r.column_name}" IS ' + "'" + r.comment.replace("'", "''") + "'")
        names = [r.column_name for r in cols_by_table[t]]
        rows = [tuple(row[n] for n in names) for row in spark.table(f"{FQ}.{t}").select(*names).collect()]
        if rows:
            with c.cursor() as cur:
                cur.executemany(f"INSERT INTO {PG_SCHEMA}.{t} ({', '.join(chr(34) + n + chr(34) for n in names)}) VALUES ({', '.join(['%s'] * len(names))})", rows)
    # Last successful simulation run (contact history itself stays in UC)
    c.execute(f"""CREATE TABLE {PG_SCHEMA}.simulation_status (
  id integer PRIMARY KEY CHECK (id = 1), last_simulation_run timestamptz, last_snapshotdate date,
  audit_timestamp timestamptz, source_system text)""")
    c.execute(f"COMMENT ON TABLE {PG_SCHEMA}.simulation_status IS 'Single row: last successful simulation run, copied from UC contact_decision_output by the app'")
    last_run = spark.sql(f"SELECT max(run_timestamp) AS r, max(snapshotdate) AS d FROM {FQ}.contact_decision_output").first()
    if last_run.r is not None:
        c.execute(f"INSERT INTO {PG_SCHEMA}.simulation_status VALUES (1, %s, %s, now(), %s)", (last_run.r, last_run.d, SOURCE_SYSTEM))
    if APP_PG_ROLE:
        c.execute(f'GRANT USAGE ON SCHEMA {PG_SCHEMA} TO "{APP_PG_ROLE}"')
        c.execute(f'GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA {PG_SCHEMA} TO "{APP_PG_ROLE}"')
        c.execute(f'GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA {PG_SCHEMA} TO "{APP_PG_ROLE}"')

# Verify: every table identical in both stores (row for row, all columns)
import datetime as _dt
def _norm(v):
    if isinstance(v, _dt.datetime):
        return (v.astimezone(_dt.timezone.utc).replace(tzinfo=None) if v.tzinfo else v).isoformat()
    return v
lb_checks = {}
with pg_connect(PG_DATABASE) as c:
    c.execute("SET TIME ZONE 'UTC'")
    for t in LB_TABLES:
        names = [r.column_name for r in cols_by_table[t]]
        pg_rows = {tuple(_norm(v) for v in r) for r in c.execute(f"SELECT {', '.join(chr(34) + n + chr(34) for n in names)} FROM {PG_SCHEMA}.{t}").fetchall()}
        uc_rows = {tuple(_norm(row[n]) for n in names) for row in spark.table(f"{FQ}.{t}").select(*names).collect()}
        lb_checks[t] = (len(uc_rows), len(pg_rows), uc_rows == pg_rows)
for t, (n_uc, n_pg, same) in lb_checks.items():
    print(f"{'PASS' if same else 'FAIL'} lakebase {t:26s} UC {n_uc:>4}  Lakebase {n_pg:>4}")
assert all(same for *_, same in lb_checks.values()), "Lakebase and UC differ"