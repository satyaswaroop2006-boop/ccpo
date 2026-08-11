# Golden test battery

Each golden = one JSON scenario with hand-computed expected values, run by
`tests/test_goldens.py` (built at the start of Phase 2) through the full
engine pipeline.

Rules:
1. Every golden embeds its complete hand computation in `_hand_computation`.
   A failing golden is settled by that arithmetic — expected values are only
   edited when the hand computation itself is corrected.
2. Coverage target: every §55 master-prompt test, plus ≥1 golden per
   synthetic card (all 12), plus one golden per engine construct
   (both accrual types, both overflow modes, both tier modes, all four clock
   kinds, scoped exclusions, stacking, gated benefits, surcharges, forex,
   portfolio dedup).
3. Goldens are CI-blocking. `pytest` must be green before any commit to
   compute/.
