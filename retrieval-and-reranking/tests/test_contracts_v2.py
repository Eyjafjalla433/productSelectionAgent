from dataclasses import replace
import json
import unittest

from techjam_agent.contracts import Candidate, CandidateSet, RankedCandidate, Requirements
from techjam_agent.contracts_v2 import RankingResultV2, RetrievalResultV2, RetrievalStats
from techjam_agent.ranking import LockedWeightedRrfTop10Reranker


def candidate(index):
    return Candidate(f"P{index}", index + 1, {"keyword": index + 1}, {"title": "Blue cotton shirt", "features": ["cotton"]})


def pool(count=3, **kwargs):
    return RetrievalResultV2("batch", "s", 3, 7, 50, tuple(candidate(i) for i in range(count)), **kwargs)


def ranking(retrieval, count):
    return RankingResultV2(
        retrieval.candidate_set_id, retrieval.session_id, retrieval.turn, retrieval.state_version,
        tuple(RankedCandidate(item.parent_asin, i + 1, 1 / (i + 1), ()) for i, item in enumerate(retrieval.candidates[:count])),
        "test",
    )


class RetrievalContractTests(unittest.TestCase):
    def test_empty_and_short_pools_and_rankings_are_valid_without_padding(self):
        for count in (0, 3, 50):
            with self.subTest(count=count):
                result = pool(count)
                ranked = ranking(result, min(10, count))
                ranked.validate_against(result, top_k=10)
                self.assertEqual(len(ranked.ranked_candidates), min(10, count))
                self.assertEqual(result.returned_count, count)

    def test_unknown_statistics_are_not_fabricated_from_top50(self):
        result = pool(50)
        self.assertIsNone(result.stats.matched_count)
        self.assertIsNone(result.stats.filtered_count)
        wire = json.loads(json.dumps(result.to_dict(), allow_nan=False))
        self.assertEqual(wire["returned_count"], 50)
        with self.assertRaises(ValueError):
            pool(3, stats=RetrievalStats(matched_count=100, filtered_count=2))
        with self.assertRaises(ValueError):
            RetrievalStats(matched_count=3, filtered_count=4)

    def test_duplicates_gaps_and_excess_candidates_are_rejected(self):
        for candidates in [(candidate(0), candidate(0)), (candidate(1),), tuple(candidate(i) for i in range(51))]:
            with self.subTest(count=len(candidates)), self.assertRaises(ValueError):
                replace(pool(), candidates=candidates)

    def test_ranking_rejects_stale_version_cross_session_and_unknown_ids(self):
        result = pool()
        valid = ranking(result, 3)
        invalid = [
            replace(valid, state_version=8), replace(valid, session_id="other"),
            replace(valid, turn=4), replace(valid, candidate_set_id="other"),
            replace(valid, ranked_candidates=(RankedCandidate("UNKNOWN", 1, 1.0, ()),)),
        ]
        for item in invalid:
            with self.subTest(item=item), self.assertRaises(ValueError):
                item.validate_against(result, top_k=10)
        with self.assertRaises(ValueError):
            valid.validate_against(result, top_k=2)
        with self.assertRaises(ValueError):
            valid.validate_against(result, top_k=True)

    def test_invalid_scores_and_ranking_order_are_rejected(self):
        for items in [(RankedCandidate("P0", 1, float("nan"), ()),), (RankedCandidate("P0", 2, 1.0, ()),)]:
            with self.subTest(items=items), self.assertRaises(ValueError):
                replace(ranking(pool(), 1), ranked_candidates=items)

    def test_state_payload_preserves_constraints_and_rejects_mismatched_identity(self):
        state = {"schema_version": "2.0", "session_id": "s", "turn": 3, "state_version": 7,
                 "hard_constraints": {"price_max": 50.0}, "exclusions": {"color": ["black"]}}
        result = pool(state_snapshot=state)
        state["exclusions"]["color"].append("red")
        self.assertEqual(result.state_snapshot["exclusions"], {"color": ["black"]})
        self.assertEqual(result.state_snapshot["hard_constraints"]["price_max"], 50.0)
        with self.assertRaises(ValueError):
            pool(state_snapshot={**state, "state_version": 8})
        with self.assertRaises(ValueError):
            result.validate_context(session_id="s", turn=3, state_version=8)

    def test_legacy_generator_contract_and_real_locked_reranker_stay_usable(self):
        legacy = CandidateSet("batch", "s", 3, Requirements("shirts", ("cotton",), ()), tuple(candidate(i) for i in range(50)))
        converted = RetrievalResultV2.from_legacy(legacy, state_version=7)
        old_ranking = LockedWeightedRrfTop10Reranker().rerank(legacy, top_k=10)
        old_ranking.validate_against(legacy, top_k=10)
        new_ranking = RankingResultV2.from_legacy(old_ranking, retrieval=converted, top_k=10, ranking_method="locked")
        self.assertEqual(new_ranking.ranked_candidates, old_ranking.ranked_candidates)
        self.assertEqual(converted.candidates, legacy.candidates)
        self.assertTrue(converted.warnings)
        self.assertEqual(new_ranking.score_semantics, "uncalibrated")
        converted.candidates[0].product["features"].append("changed")
        self.assertEqual(legacy.candidates[0].product["features"], ["cotton"])
        with self.assertRaises(ValueError):
            replace(legacy, candidates=legacy.candidates[:3])


if __name__ == "__main__":
    unittest.main()
