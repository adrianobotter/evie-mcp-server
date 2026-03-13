-- EVIE Seed Data — STEP-4 Trial (Semaglutide)
-- Hand-authored Evidence Objects + Context Envelopes for development/validation.
-- Run after 001_schema.sql and 002_rls.sql.

-- ─── Sponsor ─────────────────────────────────────────────────────────────────

INSERT INTO sponsors (id, name, tier_permissions) VALUES
    ('a0000000-0000-0000-0000-000000000001', 'Novo Nordisk', '["tier1", "tier2", "tier3"]');

-- ─── Trial ───────────────────────────────────────────────────────────────────

INSERT INTO trials (id, name, drug_name, indication, phase, sponsor_id, status) VALUES
    ('b0000000-0000-0000-0000-000000000001',
     'STEP-4',
     'Semaglutide 2.4 mg',
     'Obesity / Weight Management',
     'Phase 3',
     'a0000000-0000-0000-0000-000000000001',
     'active');

-- ─── Primary Endpoint Evidence Objects ───────────────────────────────────────

-- Primary: Body weight change
INSERT INTO evidence_objects (id, trial_id, object_class, endpoint_name, result_value, unit,
    confidence_interval_low, confidence_interval_high, p_value, time_horizon, arm, tier, is_published)
VALUES
    ('c0000000-0000-0000-0000-000000000001',
     'b0000000-0000-0000-0000-000000000001',
     'primary_endpoint',
     'Percentage change in body weight from baseline',
     -12.6, '%', -13.1, -12.1, 0.0001, '68 weeks',
     'Semaglutide 2.4 mg', 'tier1', false);

-- Primary: >=5% weight loss responder
INSERT INTO evidence_objects (id, trial_id, object_class, endpoint_name, result_value, unit,
    confidence_interval_low, confidence_interval_high, p_value, time_horizon, arm, tier, is_published)
VALUES
    ('c0000000-0000-0000-0000-000000000002',
     'b0000000-0000-0000-0000-000000000001',
     'primary_endpoint',
     'Proportion achieving >=5% body weight loss',
     79.0, '%', 74.8, 83.2, 0.0001, '68 weeks',
     'Semaglutide 2.4 mg', 'tier1', false);

-- ─── Subgroup Evidence Object ────────────────────────────────────────────────

INSERT INTO evidence_objects (id, trial_id, object_class, endpoint_name, result_value, unit,
    confidence_interval_low, confidence_interval_high, p_value, time_horizon,
    subgroup_definition, arm, tier, is_published)
VALUES
    ('c0000000-0000-0000-0000-000000000003',
     'b0000000-0000-0000-0000-000000000001',
     'subgroup',
     'Percentage change in body weight — BMI >=35 subgroup',
     -14.1, '%', -15.2, -13.0, 0.0001, '68 weeks',
     'Baseline BMI >= 35 kg/m2', 'Semaglutide 2.4 mg', 'tier2', false);

-- ─── Adverse Event Evidence Objects ──────────────────────────────────────────

INSERT INTO evidence_objects (id, trial_id, object_class, endpoint_name, result_value, unit,
    confidence_interval_low, confidence_interval_high, p_value, time_horizon, arm, tier, is_published)
VALUES
    ('c0000000-0000-0000-0000-000000000004',
     'b0000000-0000-0000-0000-000000000001',
     'adverse_event',
     'Nausea', 44.2, '%', NULL, NULL, NULL, '68 weeks',
     'Semaglutide 2.4 mg', 'tier1', false),

    ('c0000000-0000-0000-0000-000000000005',
     'b0000000-0000-0000-0000-000000000001',
     'adverse_event',
     'Diarrhea', 31.5, '%', NULL, NULL, NULL, '68 weeks',
     'Semaglutide 2.4 mg', 'tier1', false),

    ('c0000000-0000-0000-0000-000000000006',
     'b0000000-0000-0000-0000-000000000001',
     'adverse_event',
     'Vomiting', 24.8, '%', NULL, NULL, NULL, '68 weeks',
     'Semaglutide 2.4 mg', 'tier1', false);

-- ─── Comparator Evidence Object ──────────────────────────────────────────────

INSERT INTO evidence_objects (id, trial_id, object_class, endpoint_name, result_value, unit,
    confidence_interval_low, confidence_interval_high, p_value, time_horizon, arm, tier, is_published)
VALUES
    ('c0000000-0000-0000-0000-000000000007',
     'b0000000-0000-0000-0000-000000000001',
     'comparator',
     'Percentage change in body weight from baseline',
     -2.4, '%', -3.2, -1.6, NULL, '68 weeks',
     'Placebo', 'tier1', false);

-- ─── Context Envelopes ───────────────────────────────────────────────────────

-- Envelope for primary endpoint: body weight change
INSERT INTO context_envelopes (evidence_object_id, source_provenance, population_constraints,
    endpoint_definition, interpretation_guardrails, safety_statement, methodology_qualifiers)
VALUES
    ('c0000000-0000-0000-0000-000000000001',
     '{"trial_name": "STEP-4", "doi": "10.1001/jama.2021.23619", "clinicaltrials_id": "NCT03548935", "publication_date": "2022-01-11"}'::jsonb,
     'Adults aged >=18 with BMI >=30 kg/m2 or >=27 kg/m2 with at least one weight-related comorbidity. Participants had achieved a >=5% body weight reduction during a 20-week run-in period on semaglutide before randomization.',
     'Percentage change in body weight from randomization (week 20) to week 68, assessed in the modified intention-to-treat population.',
     'Results apply to the mITT population who had already responded to semaglutide during the run-in. Not generalizable to semaglutide-naive patients or those who did not achieve initial weight loss.',
     'Common adverse events in the semaglutide group included gastrointestinal events: nausea (44.2%), diarrhea (31.5%), vomiting (24.8%), and constipation (17.0%). Most events were mild to moderate in severity. Treatment discontinuation due to adverse events occurred in 2.4% of the semaglutide group.',
     'Double-blind, randomized withdrawal design. Participants receiving semaglutide 2.4 mg during a 20-week run-in were randomized 2:1 to continue semaglutide or switch to placebo for 48 weeks. mITT analysis population.');

-- Envelope for >=5% responder
INSERT INTO context_envelopes (evidence_object_id, source_provenance, population_constraints,
    endpoint_definition, interpretation_guardrails, safety_statement, methodology_qualifiers)
VALUES
    ('c0000000-0000-0000-0000-000000000002',
     '{"trial_name": "STEP-4", "doi": "10.1001/jama.2021.23619", "clinicaltrials_id": "NCT03548935", "publication_date": "2022-01-11"}'::jsonb,
     'Adults aged >=18 with BMI >=30 kg/m2 or >=27 kg/m2 with at least one weight-related comorbidity. Run-in responders only.',
     'Proportion of participants achieving >=5% reduction in body weight from randomization to week 68.',
     'Responder analysis in a pre-selected population of run-in responders. Absolute responder rates may differ in unselected populations.',
     'Common adverse events in the semaglutide group included gastrointestinal events: nausea (44.2%), diarrhea (31.5%), vomiting (24.8%), and constipation (17.0%). Most events were mild to moderate in severity.',
     'Double-blind, randomized withdrawal design. mITT analysis. Multiple imputation used for missing data.');

-- Envelope for BMI >=35 subgroup
INSERT INTO context_envelopes (evidence_object_id, source_provenance, population_constraints,
    endpoint_definition, subgroup_qualifiers, interpretation_guardrails, safety_statement, methodology_qualifiers)
VALUES
    ('c0000000-0000-0000-0000-000000000003',
     '{"trial_name": "STEP-4", "doi": "10.1001/jama.2021.23619", "clinicaltrials_id": "NCT03548935", "publication_date": "2022-01-11"}'::jsonb,
     'Subgroup of participants with baseline BMI >= 35 kg/m2. Further restricted to run-in responders.',
     'Percentage change in body weight from randomization to week 68 in the BMI >=35 subgroup.',
     'Pre-specified subgroup analysis. No multiplicity adjustment applied — interpret as exploratory.',
     'Subgroup result without multiplicity adjustment. Should not be used for definitive efficacy claims in this population. Sample size for subgroup not separately powered.',
     'Common adverse events in the semaglutide group included gastrointestinal events: nausea, diarrhea, vomiting. See full safety data for complete profile.',
     'Double-blind, randomized withdrawal. Subgroup defined by baseline BMI stratification.');

-- Envelopes for adverse events
INSERT INTO context_envelopes (evidence_object_id, source_provenance, population_constraints,
    endpoint_definition, interpretation_guardrails, safety_statement, methodology_qualifiers)
VALUES
    ('c0000000-0000-0000-0000-000000000004',
     '{"trial_name": "STEP-4", "doi": "10.1001/jama.2021.23619", "clinicaltrials_id": "NCT03548935", "publication_date": "2022-01-11"}'::jsonb,
     'Safety population — all randomized participants who received at least one dose of study medication.',
     'Incidence of nausea as a treatment-emergent adverse event from randomization through week 68.',
     'Incidence reflects the randomized period only (weeks 20-68). Nausea during the initial run-in period is not captured. Rates may differ in treatment-naive populations.',
     'Nausea was the most common adverse event. Most events were mild to moderate and occurred early in treatment. Gastrointestinal events were the primary reason for treatment discontinuation (2.4% of semaglutide group).',
     'Safety population analysis. Adverse events coded using MedDRA. Severity graded by investigator assessment.'),

    ('c0000000-0000-0000-0000-000000000005',
     '{"trial_name": "STEP-4", "doi": "10.1001/jama.2021.23619", "clinicaltrials_id": "NCT03548935", "publication_date": "2022-01-11"}'::jsonb,
     'Safety population — all randomized participants who received at least one dose of study medication.',
     'Incidence of diarrhea as a treatment-emergent adverse event from randomization through week 68.',
     'Incidence reflects the randomized period only. Rates in the overall STEP program may vary by trial design and population.',
     'Diarrhea was among the most common gastrointestinal adverse events. Most events were mild to moderate in severity and transient.',
     'Safety population analysis. MedDRA coding.'),

    ('c0000000-0000-0000-0000-000000000006',
     '{"trial_name": "STEP-4", "doi": "10.1001/jama.2021.23619", "clinicaltrials_id": "NCT03548935", "publication_date": "2022-01-11"}'::jsonb,
     'Safety population — all randomized participants who received at least one dose of study medication.',
     'Incidence of vomiting as a treatment-emergent adverse event from randomization through week 68.',
     'Incidence reflects the randomized period only. Rates may differ across the STEP program trials.',
     'Vomiting was a common gastrointestinal adverse event. Most events were mild to moderate in severity.',
     'Safety population analysis. MedDRA coding.');

-- Envelope for comparator
INSERT INTO context_envelopes (evidence_object_id, source_provenance, population_constraints,
    endpoint_definition, interpretation_guardrails, safety_statement, methodology_qualifiers)
VALUES
    ('c0000000-0000-0000-0000-000000000007',
     '{"trial_name": "STEP-4", "doi": "10.1001/jama.2021.23619", "clinicaltrials_id": "NCT03548935", "publication_date": "2022-01-11"}'::jsonb,
     'Adults randomized to placebo after 20-week semaglutide run-in. These participants had already lost weight on semaglutide before switching to placebo.',
     'Percentage change in body weight from randomization (week 20) to week 68 in the placebo arm.',
     'Placebo arm participants regained weight after semaglutide withdrawal. The weight regain in placebo reflects discontinuation effect, not placebo treatment of obesity.',
     'Adverse event profile in the placebo group reflected semaglutide withdrawal. Gastrointestinal event rates were lower in placebo than active treatment.',
     'Double-blind placebo comparator arm. Randomized 2:1 (semaglutide:placebo) after 20-week run-in.');

-- ─── Publish all evidence (envelopes exist) ──────────────────────────────────

UPDATE evidence_objects SET is_published = true
WHERE trial_id = 'b0000000-0000-0000-0000-000000000001';
