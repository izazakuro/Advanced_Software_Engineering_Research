# Stage 1 CSPC human verification workflow

1. Read `cspc_human_verification_review.md` for the compact evidence display.
2. Edit only these columns in `cspc_human_verification_workbook.csv`:
   - `human_decision`: `ACCEPT`, `OVERRIDE_TO_0`, `OVERRIDE_TO_1`, or `UNRESOLVED`
   - `human_correction_reason`: mandatory for either override; document unresolved cases
   - `reviewer_id`
   - `review_timestamp_utc`
3. Do not change any other column. The validator compares all immutable fields with the baseline.
4. From the research project root, run:

   `python3 -B outputs/phase3d_experiment_execution/validate_stage1_cspc_human_review.py`

The validator exits nonzero until all 36 decisions are explicit, every override has a reason,
there are no unresolved items, AI data remains unchanged, and no condition/retrieval metadata is exposed.
Passing validation does not lock scores or unblind experimental arms.
