# ABOUTME: The grounding guard flags trust-claim numbers (confidence/percent/score/delta)
# ABOUTME: that appear in the model's prose but in no tool result, so the LLM can't originate numbers.

from __future__ import annotations

from openeo_ai.sdk.grounding_guard import (
    _NUM_RE,
    NumericClaim,
    TableSumViolation,
    check,
    check_class_table_sums,
    extract_numeric_claims,
)


class TestExtract:
    def test_narrows_to_trust_tokens(self):
        claims = extract_numeric_claims("Confidence is 87%. Grade B score 0.92. NDVI delta 0.15.")
        vals = {c.value for c in claims}
        assert 87.0 in vals and 0.92 in vals and 0.15 in vals

    def test_ignores_numbers_without_trust_tokens(self):
        # A year and a tile count are not trust claims — no confidence/percent/score/delta nearby.
        assert extract_numeric_claims("Analysis for January 2024 over 3 tiles") == []

    def test_bare_percent_symbol_is_a_trust_marker(self):
        claims = extract_numeric_claims("95.2% of pixels were valid")
        assert any(c.value == 95.2 for c in claims)

    def test_distant_number_is_not_claimed(self):
        # A number far from any trust token stays unclaimed.
        assert extract_numeric_claims("score summary follows after a long unrelated 512 pixel grid") == []


class TestCheck:
    def test_grounded_number_no_violation(self):
        assert check("Coverage: 95.2% valid pixels", ["scene coverage_pct: 95.2 over aoi"]) == []

    def test_ungrounded_number_flags(self):
        v = check("Confidence is 87%", ["mean: 0.42"])
        assert len(v) == 1 and v[0].value == 87.0

    def test_percent_fraction_normalization_grounds(self):
        # 87% in prose is grounded by 0.87 in a tool result.
        assert check("confidence 87%", ["quality_fraction 0.87"]) == []

    def test_empty_tool_results_flags_every_claim(self):
        assert len(check("score 0.9 and confidence 80%", [])) == 2

    def test_no_trust_claims_returns_empty(self):
        assert check("The vegetation looks healthy", ["mean: 0.42"]) == []

    def test_returns_numericclaim_instances(self):
        v = check("grade score 0.99", ["mean 0.1"])
        assert v and all(isinstance(c, NumericClaim) for c in v)


class TestAreaClaims:
    def test_hectares_flagged_when_ungrounded(self):
        v = check("Moderate severity burned 732 hectares", ["mean: 0.42"])
        assert len(v) == 1 and v[0].value == 732.0

    def test_comma_separated_ha_flagged_when_ungrounded(self):
        v = check("High severity: 1,651 ha", ["mean: 0.42"])
        assert len(v) == 1 and v[0].value == 1651.0

    def test_hectares_grounded_by_tool_result(self):
        assert check("Moderate severity burned 732 hectares", ["area_ha: 732.0 class moderate"]) == []

    def test_comma_value_grounded_by_plain_tool_number(self):
        # "1,651" in prose grounds against an uncommaed 1651 in tool output.
        assert check("High severity: 1,651 ha", ["area_ha: 1651"]) == []

    def test_plain_value_grounded_by_comma_tool_number(self):
        # Commas on the tool-result side normalize too.
        assert check("Burned area totals 1651 hectares", ["total: 1,651 ha"]) == []

    def test_bare_ha_needs_word_boundary(self):
        # "that has 40 pixels" must not read "ha" out of "that"/"has".
        assert extract_numeric_claims("that has 40 pixels") == []

    def test_km2_and_acres_are_area_tokens(self):
        claims = extract_numeric_claims("covering 12.5 km² and 45 acres")
        vals = {c.value for c in claims}
        assert 12.5 in vals and 45.0 in vals

    def test_percent_claims_still_caught(self):
        v = check("44.3% of the area was burned", ["mean 0.1"])
        assert any(c.value == 44.3 for c in v)

    def test_list_commas_do_not_merge_into_one_number(self):
        # "[732,1651,2000]" is three numbers, not 732165 — the (?!\d) guard on
        # the thousands group keeps list commas from merging (reviewer repro).
        assert _NUM_RE.findall("[732,1651,2000]") == ["732", "1651", "2000"]

    def test_glued_hectare_unit_is_flagged(self):
        # "1,651ha" (unit glued to the number, no space) must still be a claim.
        v = check("burned 1,651ha in total", ["mean: 0.42"])
        assert len(v) == 1 and v[0].value == 1651.0

    def test_incidental_area_word_is_not_a_claim(self):
        # Bare "area" was dropped from the trust tokens — incidental counts near
        # the word "area" must not be flagged (reviewer repro, annotate default).
        assert extract_numeric_claims("analyzed the area of interest for 2019 imagery") == []
        assert extract_numeric_claims("cloud-free scenes over the area in the last 30 days") == []

    def test_ha_toponym_is_not_a_hectare_claim(self):
        # "Ha Long Bay ... 0.45" — "ha" not preceded by a digit, so no unit match.
        assert extract_numeric_claims("NDVI for Ha Long Bay averaged 0.45 in June") == []


class TestBridgePayload:
    def test_payload_shape_on_violation(self):
        from openeo_ai.sdk.claude_sdk_bridge import _grounding_payload
        p = _grounding_payload("confidence 87%", ["mean 0.42"])
        assert p["type"] == "grounding_warning"
        assert p["violations"] and p["violations"][0]["value"] == 87.0

    def test_payload_none_when_clean(self):
        from openeo_ai.sdk.claude_sdk_bridge import _grounding_payload
        assert _grounding_payload("coverage 95.2%", ["coverage_pct 95.2"]) is None

    def test_payload_none_on_empty_text(self):
        from openeo_ai.sdk.claude_sdk_bridge import _grounding_payload
        assert _grounding_payload("", ["mean 0.4"]) is None


# The live 2026-07-24 Palisades answer: class shares of one whole stitched from several
# tool results, summing to 134.18%. Disjoint shares cannot exceed 100% — an impossibility
# proof, not a heuristic. Per-number grounding missed it (each number existed somewhere).
PALISADES_TABLE = """| dNBR Range | % of Area | Severity |
| < -0.1 | 67.09% | Recovery |
| -0.1 to 0.1 | 58.25% | Minimal |
| 0.1 to 0.27 | 1.53% | Light |
| 0.27 to 0.44 | 1.76% | Moderate |
| 0.44 to 0.66 | 3.57% | Significant |
| > 0.66 | 1.98% | Severe |"""


class TestClassTableSums:
    def test_palisades_table_over_sums(self):
        v = check_class_table_sums(PALISADES_TABLE)
        assert len(v) == 1
        assert isinstance(v[0], TableSumViolation)
        assert abs(v[0].total - 134.18) < 0.01
        assert v[0].rows == 6
        assert "% of Area" in v[0].column_label

    def test_correct_table_summing_to_100_is_clean(self):
        text = """| Class | % of Area |
| --- | --- |
| Water | 12.5% |
| Vegetation | 60.0% |
| Bare soil | 27.5% |"""
        assert check_class_table_sums(text) == []

    def test_partial_table_under_100_never_flags(self):
        # A table showing only a subset of classes legitimately sums low.
        text = """| Class | Share |
| --- | --- |
| Water | 12.0% |
| Vegetation | 20.0% |
| Bare soil | 13.0% |"""
        assert check_class_table_sums(text) == []

    def test_total_row_is_excluded(self):
        text = """| Class | % of Area |
| --- | --- |
| Water | 25.0% |
| Vegetation | 50.0% |
| Bare soil | 25.0% |
| Total | 100% |"""
        assert check_class_table_sums(text) == []

    def test_two_class_partition_is_checked(self):
        # burned/unburned, water/land, change/no-change are real two-class
        # partitions and the most common EO breakdown — the header gate makes
        # checking them safe, so an impossible 150% must still be caught.
        text = """| Class | % of Area |
| --- | --- |
| Burned | 100.0% |
| Unburned | 50.0% |"""
        v = check_class_table_sums(text)
        assert len(v) == 1 and v[0].rows == 2 and abs(v[0].total - 150.0) < 0.01

    def test_prose_percentages_are_not_a_table(self):
        text = "Scene quality: cloud cover 34%, confidence 85%, coverage 92% over the AOI."
        assert check_class_table_sums(text) == []

    def test_only_the_offending_column_is_flagged(self):
        text = """| Class | % Area 2023 | % Area 2024 |
| --- | --- | --- |
| Water | 50% | 60% |
| Vegetation | 30% | 60% |
| Bare soil | 20% | 40% |"""
        v = check_class_table_sums(text)
        assert len(v) == 1
        assert v[0].column_label == "% Area 2024"
        assert abs(v[0].total - 160.0) < 0.01

    def test_empty_and_malformed_input_is_safe(self):
        assert check_class_table_sums("") == []
        assert check_class_table_sums("no tables here, just prose") == []
        assert check_class_table_sums("| | |\n|||\n| 50% |") == []
        assert check_class_table_sums("|||||||") == []


class TestClassTableFalsePositives:
    """The guard feeds a user-visible notice under the annotate default, so a false
    accusation is worse than a miss. Each case below was a REAL false positive in the
    first implementation, verified before the header gate / fence / split fixes."""

    def test_per_scene_metric_column_is_not_a_partition(self):
        # Cloud cover per scene is an independent per-row metric — summing past 100%
        # is normal, not evidence of a stitched table.
        text = """| Date | Tile | Cloud cover % |
| --- | --- | --- |
| 2025-01-02 | T11SLT | 34.5% |
| 2025-01-07 | T11SLT | 88.0% |
| 2025-01-12 | T11SLT | 12.0% |"""
        assert check_class_table_sums(text) == []

    def test_percent_change_column_is_not_a_partition(self):
        text = """| Region | % Change vs 2020 |
| --- | --- |
| North | 45.0% |
| South | 55.0% |
| East | 60.0% |"""
        assert check_class_table_sums(text) == []

    def test_table_inside_a_code_fence_is_an_example_not_a_result(self):
        text = """Here is an example of the output format:
```
| Class | % of Area |
| --- | --- |
| A | 60.0% |
| B | 60.0% |
| C | 60.0% |
```"""
        assert check_class_table_sums(text) == []

    def test_two_adjacent_tables_are_not_summed_together(self):
        # The complex tier joins each specialist's prose, so two CORRECT tables can
        # end up adjacent; a separator row starts a new table, never one 200% column.
        text = """| Class | % of Area |
| --- | --- |
| Water | 25.0% |
| Veg | 50.0% |
| Bare | 25.0% |
| Class | % of Area |
| --- | --- |
| Water | 25.0% |
| Veg | 50.0% |
| Bare | 25.0% |"""
        assert check_class_table_sums(text) == []

    def test_integer_rounded_partition_tolerates_rounding(self):
        # Eight classes at whole-percent precision can legitimately land on 101%.
        text = """| Class | % of Area |
| --- | --- |
| A | 13% |
| B | 13% |
| C | 13% |
| D | 13% |
| E | 12% |
| F | 12% |
| G | 12% |
| H | 13% |"""
        assert check_class_table_sums(text) == []

    def test_all_classes_summary_row_is_excluded(self):
        text = """| Class | % of Area |
| --- | --- |
| Water | 25.0% |
| Veg | 50.0% |
| Bare | 25.0% |
| All classes | 100.0% |"""
        assert check_class_table_sums(text) == []

    def test_recount_token_matches_on_word_boundary(self):
        # "Consumption" contains "sum" but is a class label, not a summary row —
        # substring matching would silently drop it from the partition.
        text = """| Class | % of Area |
| --- | --- |
| Consumption | 60.0% |
| Storage | 50.0% |
| Transit | 40.0% |"""
        v = check_class_table_sums(text)
        assert len(v) == 1 and v[0].rows == 3

    def test_grouped_long_format_table_is_not_summed_across_groups(self):
        # One row-group per region; the shares partition WITHIN each group, so the
        # column totals 200% by design. This is the canonical multi-AOI answer shape.
        text = """| Region | Class | % of area |
| --- | --- | --- |
| North | Burned | 60.0% |
| North | Unburned | 40.0% |
| South | Burned | 30.0% |
| South | Unburned | 70.0% |"""
        assert check_class_table_sums(text) == []

    def test_grouped_by_year_is_not_summed_across_groups(self):
        text = """| Year | Class | % of area |
| --- | --- | --- |
| 2020 | Water | 12.0% |
| 2020 | Land | 88.0% |
| 2025 | Water | 18.0% |
| 2025 | Land | 82.0% |"""
        assert check_class_table_sums(text) == []

    def test_adjacent_tables_without_separator_rows_are_split(self):
        # The live agent emits tables with NO separator row (see PALISADES_TABLE),
        # so a repeated header is the only boundary signal available.
        text = """| Class | % of Area |
| Water | 25.0% |
| Veg | 50.0% |
| Bare | 25.0% |
| Class | % of Area |
| Water | 25.0% |
| Veg | 50.0% |
| Bare | 25.0% |"""
        assert check_class_table_sums(text) == []

    def test_blockquoted_tables_parse_like_unquoted_ones(self):
        text = """> | Class | % of Area |
> | --- | --- |
> | Water | 25.0% |
> | Veg | 50.0% |
> | Bare | 25.0% |
> | Class | % of Area |
> | --- | --- |
> | Water | 25.0% |
> | Veg | 50.0% |
> | Bare | 25.0% |"""
        assert check_class_table_sums(text) == []

    def test_per_row_denominator_headers_are_not_partitions(self):
        # Each row is measured against its OWN whole, so >100% is expected.
        for header in ("% of AOI covered", "Proportion of days with rain", "% of total land"):
            text = (f"| Item | {header} |\n| --- | --- |\n"
                    "| a | 82.4% |\n| b | 95.1% |\n| c | 61.7% |")
            assert check_class_table_sums(text) == [], header

    def test_bare_percent_needs_a_strong_class_column(self):
        # "Type"/"Zone"/"Range" are not class dimensions — a bare "%" beside them
        # is a per-row metric, not a partition.
        text = """| Scene | Type | % |
| --- | --- | --- |
| S2A | L2A | 82.0% |
| S2B | L2A | 91.0% |
| S2C | L2A | 74.0% |"""
        assert check_class_table_sums(text) == []

    def test_derived_rollup_row_is_excluded(self):
        text = """| Severity class | % of area |
| --- | --- |
| Unburned | 45.0% |
| Low | 20.0% |
| Moderate | 20.0% |
| High | 15.0% |
| Burned (Low+Moderate+High) | 55.0% |"""
        assert check_class_table_sums(text) == []

    def test_comma_decimal_is_not_read_as_thousands(self):
        # "12,5%" must not parse as 125% and manufacture an over-sum.
        text = """| Class | % of Area |
| --- | --- |
| A | 12,5% |
| B | 12,5% |
| C | 12,5% |"""
        assert check_class_table_sums(text) == []

    def test_tilde_fenced_example_table_is_skipped(self):
        text = """~~~
| Class | % of Area |
| --- | --- |
| A | 60.0% |
| B | 60.0% |
| C | 60.0% |
~~~"""
        assert check_class_table_sums(text) == []

    def test_emphasised_percentage_cell_still_counts(self):
        # "**60.0%**" is styling; dropping it silently under-counted the partition.
        text = """| Class | % of Area |
| --- | --- |
| A | **60.0%** |
| B | 40.0% |
| C | 40.0% |"""
        v = check_class_table_sums(text)
        assert len(v) == 1 and abs(v[0].total - 140.0) < 0.01
