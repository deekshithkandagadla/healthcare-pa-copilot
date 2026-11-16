---
{
  "policy_id": "POL-MRI-LUMBAR-V2",
  "policy_family": "POL-MRI-LUMBAR",
  "version": 2,
  "title": "Lumbar Spine MRI for Low Back Pain (Version 2)",
  "service_codes": ["72148"],
  "plans": ["PPO-GOLD", "HMO-STD", "HMO-RURAL"],
  "effective_start": "2024-07-01",
  "effective_end": "2026-02-28",
  "status": "superseded",
  "superseded_by": "POL-MRI-LUMBAR-V3",
  "red_flag_section": "S2",
  "criteria": [
    {"criterion_id": "C-01", "section": "S3.1", "description": "Low back pain present for at least 6 weeks",
     "rule": {"fact": "pain_duration_weeks", "op": ">=", "value": 6}},
    {"criterion_id": "C-02", "section": "S3.2", "description": "At least 4 weeks of conservative treatment (physical therapy, NSAIDs, or a supervised home exercise program)",
     "rule": {"fact": "conservative_therapy_weeks", "op": ">=", "value": 4}},
    {"criterion_id": "C-03", "section": "S3.3", "description": "No lumbar spine MRI in the prior 12 months unless symptoms are new or worsening",
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
# Lumbar Spine MRI for Low Back Pain — Version 2

## S1 Scope
This policy covers magnetic resonance imaging (MRI) of the lumbar spine (CPT 72148) for adult members with
low back pain. A lumbar spine MRI for low back pain is considered medically necessary when the member has had
low back pain that has not improved with conservative treatment, as described below.

## S2 Red flags (criteria bypass)
Lumbar spine MRI is appropriate without a conservative treatment trial when any red flag is documented:
suspected cauda equina syndrome (saddle anesthesia, new urinary retention or incontinence), progressive or
severe neurological deficit, suspected malignancy, suspected spinal infection, or significant trauma.

## S3 Criteria
### S3.1 Duration of symptoms
Low back pain has been present for at least 6 weeks.

### S3.2 Conservative treatment
The member has completed at least 4 weeks of conservative treatment for low back pain. Conservative
treatment may include physical therapy, NSAIDs, or a supervised home exercise program. Any one
documented conservative treatment modality is sufficient.

### S3.3 Repeat imaging
No lumbar spine MRI in the prior 12 months, unless symptoms are new or worsening.

## S4 Documentation
Clinical notes should describe the low back pain, its duration, and the conservative treatment tried.

## S5 Exclusions
### S5.1 MRI safety
Lumbar MRI is excluded when the member has an implanted device that is not MRI-conditional
(for example a non-MRI-conditional pacemaker, defibrillator or neurostimulator) and no device
clearance is documented. These requests require physician review.

### S5.2 Non-clinical purpose
Imaging requested solely for legal, litigation or workers' compensation documentation, without a
clinical indication, is excluded.
