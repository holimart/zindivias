## What the submitted score is

Final-two: `V2_model_cgs` (`hvLtPVmr`; SHA-256 `5d0607d5531793008d8d5c86b1625f26e5bb8e9504a2b4a4a84f0224a0f5cef7`) and `B_from_A_cgs` (`eAaWXWxM`; SHA-256 `38e17cf1ec99f4f3fc5d358d13ccd0edfabe947d81fd6b6e5ef61e6cea006607`). Each is a 9,794-row `GEOID,coverage_gap_score` file copied byte-for-byte from its frozen source. 9,379 GEOIDs are official; the other 415 eastern-WA template rows use registered zero fill, not target estimates.

V2 components:

- **Transport:** `T_i=clip(1−named_road_km_5070_i/route_km_i,0,1)`, defined iff `route_km_i>0`; uses provided Overture named-road and eligible route lengths.
- **Buildings:** `G_i=0.01` iff the provided Overture building count is positive; otherwise undefined (placeholder zero excluded).
- **POI:** For fire/EMS/schools, `p_t=sigmoid(a_t+b_t·log1p((pop_total/max(ALAND_km²,.01))/100)+c_t·u)`, with fixed `(a,b,c)` respectively `(-2.8,.30,.70)`, `(-3.2,.25,.60)`, `(-1,.20,.35)`. `u=pct_urban` if finite, else `pop_urban/pop_total` if population positive, else `.5`. `n_t` is provided Overture category count plus its registered unclassified-place share. Type gap=`p_t×clip(1−n_t/(n_t+1),0,1)`, defined iff `p_t>0`; facility half averages defined types. Establishments half=`clip(1−all_overture_poi_count/(.024×pop_total),0,1)`, defined iff population or all-place count is positive (positive count at zero population gives zero). POI is the equal mean of defined halves, or undefined if neither is. Inputs: provided Overture category/all-place counts and `pop_total`, `pop_urban`, `pct_urban`, `ALAND`; priors and `.024` are fixed, not fitted to scores/labels.

V2 is the arithmetic mean of defined `T_i`, `G_i`, and `POI_i`; undefined terms are omitted. B changes only the composite: `B_i=.5×V_i+.5×mean(V_j)` over official tracts *j* in the same region. Its component columns are unchanged; its regional mean uses V2 predictions, not targets.

**Fresh-clone rebuild:** committed V2 source/components plus a workspace containing `samples/<region>-sample-submission.csv` (four official files) and `SampleSubmission.csv` (GEOID/region metadata) suffice; no private feature cache is needed. From the repository root:

```sh
uv run python scripts/build_candidate_b_from_a.py --workspace "$COMPETITION_WORKSPACE" && \
uv run python scripts/build_bias_column_order_cgs.py --workspace "$COMPETITION_WORKSPACE"
```

Verified in a scratch clone; both hashes matched.
