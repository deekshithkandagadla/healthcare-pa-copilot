---
{
  "policy_id": "POL-MRI-LUMBAR-V3",
  "policy_family": "POL-MRI-LUMBAR",
  "version": 3,
  "title": "Advanced Imaging of the Lumbar Spine (Version 3)",
  "service_codes": ["72148"],
  "plans": ["PPO-GOLD", "HMO-STD", "HMO-RURAL"],
  "effective_start": "2026-03-01",
  "effective_end": null,
  "status": "active",
  "supersedes": "POL-MRI-LUMBAR-V2",
  "red_flag_section": "S2",
  "criteria": [
    {"criterion_id": "C-01", "section": "S3.1", "description": "Symptoms present for at least 6 weeks",
     "rule": {"fact": "pain_duration_weeks", "op": ">=", "value": 6}},
    {"criterion_id": "C-02", "section": "S3.2", "description": "At least 6 weeks of physical therapy completed within the 6 months before the request",
     "rule": {"fact": "pt_weeks_recent", "op": ">=", "value": 6}},
    {"criterion_id": "C-03", "section": "S3.3", "description": "Radicular symptoms or objective neurological findings documented",
     "rule": {"fact": "radiculopathy", "op": "==", "value": true}},
    {"criterion_id": "C-04", "section": "S3.4", "description": "No lumbar spine MRI in the prior 12 months unless there is a documented change in condition",
     "rule": {"fact": "prior_lumbar_mri_within_12m", "op": "==", "value": false}}
  ],
  "exclusions": [
    {"exclusion_id": "X-01", "section": "S5.1", "description": "Implanted device that is not MRI-conditional, without documented device clearance",
     "rule": {"fact": "mri_unsafe_device", "op": "==", "value": true}},
    {"exclusion_id": "X-02", "section": "S5.2", "description": "Imaging requested solely for legal, litigation or workers' compensation documentation",
     "rule": {"fact": "medicolegal_only", "op": "==", "value": true}}
  ]
}
---
# Advanced Imaging of the Lumbar Spine — Version 3

## S1 Scope and change summary
Applies to CPT 72148 for adults. Version 3 replaces Version 2 for dates of service on or after
2026-03-01. Changes: the therapy requirement now specifies physical therapy of at least 6 weeks within
the prior 6 months, and radicular or neurological findings must be documented.

## S2 Red flags (criteria bypass)
Imaging is appropriate without meeting S3 when the record documents suspected cauda equina syndrome
(saddle anesthesia, new urinary retention or incontinence), a progressive neurological deficit,
suspected malignancy, suspected infection, or significant trauma. Red-flag requests should be expedited.

## S3 Criteria
### S3.1 Symptom duration
Symptoms present for at least 6 weeks.

### S3.2 Physical therapy
At least 6 weeks of physical therapy completed within the 6 months before the request. Medication-only
management or an unsupervised home program does not satisfy this criterion. Documentation must state
the duration or show dated therapy encounters; a statement that therapy was "tried" is insufficient.

### S3.3 Neurological findings
Radicular symptoms (for example, pain radiating below the knee) or objective findings (for example, a
positive straight-leg raise, dermatomal sensory loss, or reflex changes) are documented.

### S3.4 Repeat imaging
No lumbar spine MRI in the prior 12 months unless there is a documented change in condition.

## S4 Exceptions
Date-limited plan exceptions published in the exception register may modify S3 thresholds.
Exceptions apply only within their effective window and only to the listed plans.

## S5 Exclusions
### S5.1 MRI safety
Lumbar MRI is excluded when the member has an implanted device that is not MRI-conditional
(for example a non-MRI-conditional pacemaker, defibrillator or neurostimulator) and no device
clearance is documented. These requests require physician review.

### S5.2 Non-clinical purpose
Imaging requested solely for legal, litigation or workers' compensation documentation, without a
clinical indication, is excluded.
