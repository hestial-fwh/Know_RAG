import unittest

from app.services.retrieval_service import SearchOptions, merge_results_rrf


def options(vector_weight=1.0, keyword_weight=1.0):
    return SearchOptions(
        query="test",
        top_k=10,
        vector_threshold=0.0,
        keyword_threshold=0.0,
        use_vector=True,
        use_keyword=True,
        include_content=False,
        include_context=True,
        fusion_strategy="rrf",
        rrf_k=60,
        vector_weight=vector_weight,
        keyword_weight=keyword_weight,
    )


class RRFFusionTests(unittest.TestCase):
    def test_vector_only_score(self):
        result = merge_results_rrf([{"chunk_id": "a", "vector_rank": 1, "vector_score": 0.9}], [], options())[0]
        self.assertAlmostEqual(result["rrf_score"], 1 / 61)
        self.assertEqual(result["match_type"], "vector")

    def test_keyword_only_score(self):
        result = merge_results_rrf([], [{"chunk_id": "a", "keyword_rank": 2, "keyword_score": 0.8}], options())[0]
        self.assertAlmostEqual(result["rrf_score"], 1 / 62)
        self.assertEqual(result["match_type"], "keyword")

    def test_hybrid_score_adds_both_paths(self):
        result = merge_results_rrf(
            [{"chunk_id": "a", "vector_rank": 1, "vector_score": 0.9}],
            [{"chunk_id": "a", "keyword_rank": 3, "keyword_score": 0.7}],
            options(),
        )[0]
        self.assertAlmostEqual(result["rrf_score"], 1 / 61 + 1 / 63)
        self.assertEqual(result["match_type"], "hybrid")

    def test_better_rank_scores_higher(self):
        results = merge_results_rrf(
            [
                {"chunk_id": "a", "vector_rank": 3, "vector_score": 0.8},
                {"chunk_id": "b", "vector_rank": 1, "vector_score": 0.7},
            ],
            [],
            options(),
        )
        self.assertEqual(results[0]["chunk_id"], "b")

    def test_weights_affect_score(self):
        result = merge_results_rrf([{"chunk_id": "a", "vector_rank": 1, "vector_score": 0.9}], [], options(vector_weight=2))[0]
        self.assertAlmostEqual(result["rrf_score"], 2 / 61)

    def test_duplicate_chunk_is_merged(self):
        results = merge_results_rrf(
            [{"chunk_id": "a", "vector_rank": 1, "vector_score": 0.9}],
            [{"chunk_id": "a", "keyword_rank": 1, "keyword_score": 0.9}],
            options(),
        )
        self.assertEqual(len(results), 1)


if __name__ == "__main__":
    unittest.main()
