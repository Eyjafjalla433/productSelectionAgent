from __future__ import annotations

import unittest

from techjam_agent.agent import Agent
from techjam_agent.contracts import (
    Candidate,
    CandidateSet,
    RankedCandidate,
    RerankResult,
    Requirements,
)


def candidate(index: int) -> Candidate:
    return Candidate(
        parent_asin=f"P{index:02d}",
        candidate_rank=index + 1,
        source_ranks={"test_route": index + 1},
        product={"title": f"Product {index}"},
    )


class FakeTop50Generator:
    def __init__(self) -> None:
        self.calls: list[tuple[Requirements, str, int]] = []
        self.generated: CandidateSet | None = None

    def generate(
        self,
        requirements: Requirements,
        *,
        session_id: str,
        turn: int,
    ) -> CandidateSet:
        self.calls.append((requirements, session_id, turn))
        self.generated = CandidateSet(
            candidate_set_id=f"{session_id}:{turn}",
            session_id=session_id,
            turn=turn,
            requirements=requirements,
            candidates=tuple(candidate(index) for index in range(50)),
        )
        return self.generated


class FakeTop10Reranker:
    def __init__(self) -> None:
        self.calls: list[tuple[CandidateSet, int]] = []

    def rerank(self, candidate_set: CandidateSet, *, top_k: int) -> RerankResult:
        self.calls.append((candidate_set, top_k))
        return RerankResult(
            candidate_set_id=candidate_set.candidate_set_id,
            ranked_candidates=tuple(
                RankedCandidate(
                    parent_asin=item.parent_asin,
                    rank=rank,
                    score=float(11 - rank),
                    evidence=("fake",),
                )
                for rank, item in enumerate(candidate_set.candidates[:top_k], start=1)
            ),
        )


class TwoStageContractTests(unittest.TestCase):
    def test_candidate_set_requires_exactly_50_unique_ranked_candidates(self) -> None:
        requirements = Requirements("shirts", ("cotton",), ("blue",))

        valid = CandidateSet(
            candidate_set_id="session:3",
            session_id="session",
            turn=3,
            requirements=requirements,
            candidates=tuple(candidate(index) for index in range(50)),
        )

        self.assertEqual(len(valid.candidates), 50)
        with self.assertRaises(ValueError):
            CandidateSet(
                candidate_set_id="session:3",
                session_id="session",
                turn=3,
                requirements=requirements,
                candidates=tuple(candidate(index) for index in range(49)),
            )

    def test_agent_calls_top50_then_top10_only_after_two_other_questions(self) -> None:
        generator = FakeTop50Generator()
        reranker = FakeTop10Reranker()
        agent = Agent(candidate_generator=generator, reranker=reranker)
        agent.reset("session", {"preference_tags": ["must-not-flow"]})

        first = agent.respond(
            "session",
            "I'm looking for Women's Shirts, but I'm still exploring.",
            1,
            10,
        )
        second = agent.respond(
            "session",
            "For that, what matters is: cotton; color: blue.",
            2,
            10,
        )
        third = agent.respond(
            "session",
            "For that, what matters is: machine washable; lightweight.",
            3,
            10,
        )

        self.assertEqual(first["ask_attribute"], "other")
        self.assertEqual(second["ask_attribute"], "other")
        self.assertEqual(len(generator.calls), 1)
        self.assertEqual(len(reranker.calls), 1)
        requirements, session_id, turn = generator.calls[0]
        self.assertEqual(requirements.hard_constraints, ("cotton", "color: blue"))
        self.assertEqual(requirements.soft_preferences, ("machine washable", "lightweight"))
        self.assertEqual((session_id, turn), ("session", 3))
        self.assertIs(reranker.calls[0][0], generator.generated)
        self.assertEqual(
            third["recommendations"],
            [{"parent_asin": f"P{index:02d}"} for index in range(10)],
        )

    def test_rerank_result_must_be_a_unique_subset_of_candidate_set(self) -> None:
        requirements = Requirements("shirts", ("cotton",), ("blue",))
        candidate_set = CandidateSet(
            candidate_set_id="session:3",
            session_id="session",
            turn=3,
            requirements=requirements,
            candidates=tuple(candidate(index) for index in range(50)),
        )
        invalid = RerankResult(
            candidate_set_id="session:3",
            ranked_candidates=(RankedCandidate("OUTSIDE", 1, 1.0, ("fake",)),),
        )

        with self.assertRaises(ValueError):
            invalid.validate_against(candidate_set, top_k=1)


if __name__ == "__main__":
    unittest.main()
