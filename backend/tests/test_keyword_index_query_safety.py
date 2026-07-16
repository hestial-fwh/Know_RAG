import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import httpx

from app.services import keyword_index_service as keyword_service
from app.services import retrieval_service
from app.services.keyword_index_service import ElasticsearchQueryComplexityError


def _response(status: int, payload: dict) -> httpx.Response:
    return httpx.Response(
        status,
        json=payload,
        request=httpx.Request("POST", "http://elasticsearch:9200/index/_search"),
    )


def _multi_matches(value):
    matches = []
    if isinstance(value, dict):
        if "multi_match" in value:
            matches.append(value["multi_match"])
        for child in value.values():
            matches.extend(_multi_matches(child))
    elif isinstance(value, list):
        for child in value:
            matches.extend(_multi_matches(child))
    return matches


class KeywordQueryPreparationTests(unittest.TestCase):
    def setUp(self):
        self.settings_patch = patch.multiple(
            keyword_service.settings,
            elasticsearch_query_max_chars=256,
            elasticsearch_query_max_terms=24,
            elasticsearch_ngram_query_max_chars=64,
            elasticsearch_safe_query_max_chars=96,
        )
        self.settings_patch.start()
        self.addCleanup(self.settings_patch.stop)

    def test_short_chinese_query_keeps_ngram_fields(self):
        prepared = keyword_service.prepare_bm25_query("  人工智能\n检索  ")
        body = keyword_service.build_bm25_search_body("kb-1", prepared, 10)

        self.assertFalse(prepared.is_long)
        self.assertEqual(prepared.query_text, "人工智能 检索")
        fields = [field for match in _multi_matches(body) for field in match["fields"]]
        self.assertTrue(any(".ngram" in field for field in fields))

    def test_short_english_query_keeps_semantics_and_filters(self):
        prepared = keyword_service.prepare_bm25_query("  neural\t retrieval\r\n safety\x00 ")
        body = keyword_service.build_bm25_search_body("kb-1", prepared, 5)

        self.assertEqual(prepared.query_text, "neural retrieval safety")
        self.assertFalse(prepared.is_long)
        filters = body["query"]["bool"]["filter"]
        self.assertIn({"term": {"knowledge_base_id": "kb-1"}}, filters)
        self.assertIn({"term": {"is_enabled": True}}, filters)
        self.assertIn({"term": {"status": "ready"}}, filters)

    def test_long_chinese_query_has_no_ngram_fields(self):
        query = "人工智能知识库检索系统能够处理复杂文档并返回准确结果" * 12
        prepared = keyword_service.prepare_bm25_query(query)
        body = keyword_service.build_bm25_search_body("kb-1", prepared, 10)

        self.assertTrue(prepared.is_long)
        self.assertLessEqual(len(prepared.query_text), 256)
        self.assertLessEqual(len(prepared.terms), 24)
        fields = [field for match in _multi_matches(body) for field in match["fields"]]
        self.assertFalse(any(".ngram" in field for field in fields))

    def test_long_english_and_mixed_queries_use_whole_bounded_terms(self):
        english = " ".join(f"retrievalword{index}" for index in range(80))
        mixed = english + " 中文知识库检索结果分析" * 20

        for query in (english, mixed):
            prepared = keyword_service.prepare_bm25_query(query)
            self.assertTrue(prepared.is_long)
            self.assertLessEqual(len(prepared.query_text), 256)
            self.assertLessEqual(len(prepared.terms), 24)
            self.assertEqual(prepared.query_text.split(), list(prepared.terms))
            for term in prepared.terms:
                if term.isascii():
                    self.assertIn(term, query)

    def test_configured_character_limit_also_switches_to_compressed_query(self):
        with patch.object(keyword_service.settings, "elasticsearch_query_max_chars", 40):
            prepared = keyword_service.prepare_bm25_query("one two three four five six seven eight nine ten eleven")

        self.assertTrue(prepared.is_long)
        self.assertLessEqual(len(prepared.query_text), 40)
        self.assertTrue(all(term in prepared.normalized_query for term in prepared.terms))

    def test_ngram_prefix_does_not_cut_an_english_word(self):
        query = "中文 " + "completeword " * 7
        prepared = keyword_service.prepare_bm25_query(query)
        body = keyword_service.build_bm25_search_body("kb-1", prepared, 10)
        ngram_matches = [m for m in _multi_matches(body) if any(".ngram" in f for f in m["fields"])]

        self.assertEqual(len(ngram_matches), 1)
        ngram_query = ngram_matches[0]["query"]
        self.assertLessEqual(len(ngram_query), 64)
        self.assertFalse(ngram_query.endswith("completewor"))


class KeywordSearchRetryTests(unittest.TestCase):
    def setUp(self):
        self.ensure_patch = patch.object(keyword_service, "ensure_index")
        self.ensure_patch.start()
        self.addCleanup(self.ensure_patch.stop)

    @staticmethod
    def clause_error():
        return {
            "error": {
                "root_cause": [{"type": "too_many_nested_clauses", "reason": "too many nested clauses"}],
                "type": "search_phase_execution_exception",
                "caused_by": {"type": "too_many_nested_clauses", "reason": "maxClauseCount is set to 1310"},
            },
            "status": 500,
        }

    @staticmethod
    def success():
        return {
            "hits": {
                "max_score": 4.0,
                "hits": [
                    {
                        "_id": "chunk-1",
                        "_score": 2.0,
                        "_source": {"chunk_id": "chunk-1"},
                        "highlight": {"content": ["first fragment", "second fragment"]},
                    }
                ],
            }
        }

    def test_clause_error_retries_once_with_safe_non_ngram_query(self):
        request = Mock(side_effect=[_response(500, self.clause_error()), _response(200, self.success())])
        query = "long retrieval context " * 80

        with patch.object(keyword_service, "_request", request):
            hits = keyword_service.search_bm25("kb-1", query, 10)

        self.assertEqual(request.call_count, 2)
        safe_body = request.call_args_list[1].kwargs["json_body"]
        safe_matches = _multi_matches(safe_body)
        self.assertTrue(safe_matches)
        self.assertLessEqual(len(safe_matches[0]["query"]), 96)
        self.assertFalse(any(".ngram" in field for field in safe_matches[0]["fields"]))
        self.assertEqual(hits[0].score, 0.5)
        self.assertEqual(hits[0].highlight, "first fragment ... second fragment")

    def test_safe_retry_is_attempted_only_once(self):
        request = Mock(side_effect=[_response(500, self.clause_error()), _response(500, self.clause_error())])

        with patch.object(keyword_service, "_request", request):
            with self.assertRaisesRegex(ElasticsearchQueryComplexityError, "safe query"):
                keyword_service.search_bm25("kb-1", "复杂中文查询" * 100, 10)

        self.assertEqual(request.call_count, 2)

    def test_ordinary_500_is_not_treated_as_clause_error(self):
        payload = {"error": {"type": "security_exception", "reason": "authentication failed"}}
        request = Mock(return_value=_response(500, payload))

        with patch.object(keyword_service, "_request", request):
            with self.assertRaises(RuntimeError) as raised:
                keyword_service.search_bm25("kb-1", "short query", 10)

        self.assertNotIsInstance(raised.exception, ElasticsearchQueryComplexityError)
        self.assertEqual(request.call_count, 1)

    def test_safe_retry_other_failure_is_explicit(self):
        request = Mock(
            side_effect=[
                _response(500, self.clause_error()),
                _response(503, {"error": {"type": "unavailable_shards_exception", "reason": "unavailable"}}),
            ]
        )

        with patch.object(keyword_service, "_request", request):
            with self.assertRaisesRegex(RuntimeError, "safe retry failed"):
                keyword_service.search_bm25("kb-1", "long query " * 100, 10)

    def test_short_query_preserves_ranking_highlight_and_hit_mapping(self):
        payload = {
            "hits": {
                "max_score": 8.0,
                "hits": [
                    {
                        "_id": "fallback-id",
                        "_score": 8.0,
                        "_source": {"chunk_id": "chunk-first"},
                        "highlight": {"context_header": ["first highlight"]},
                    },
                    {
                        "_id": "chunk-second",
                        "_score": 2.0,
                        "_source": {},
                        "highlight": {},
                    },
                ],
            }
        }
        request = Mock(return_value=_response(200, payload))

        with patch.object(keyword_service, "_request", request):
            hits = keyword_service.search_bm25("kb-1", "人工智能", 10)

        self.assertEqual([hit.chunk_id for hit in hits], ["chunk-first", "chunk-second"])
        self.assertEqual([hit.rank for hit in hits], [1, 2])
        self.assertEqual([hit.score for hit in hits], [1.0, 0.25])
        self.assertEqual(hits[0].highlight, "first highlight")
        body = request.call_args.kwargs["json_body"]
        self.assertIn("highlight", body)
        self.assertTrue(any(".ngram" in field for match in _multi_matches(body) for field in match["fields"]))


class _FakeDB:
    def get(self, model, object_id):
        return SimpleNamespace(id=object_id)

    def commit(self):
        return None


class HybridIsolationTests(unittest.TestCase):
    @staticmethod
    def options(*, use_vector=True, use_keyword=True):
        return retrieval_service.SearchOptions(
            query="复杂查询" * 100,
            top_k=5,
            vector_threshold=0.0,
            keyword_threshold=0.0,
            use_vector=use_vector,
            use_keyword=use_keyword,
            include_content=False,
            include_context=False,
        )

    def test_vector_results_survive_bm25_complexity_failure(self):
        vector_hit = {"chunk_id": "vector-1", "vector_rank": 1, "vector_score": 0.9, "score": 0.9}
        with (
            patch.object(retrieval_service, "normalize_options", return_value=(self.options(), None)),
            patch.object(retrieval_service, "vector_search", AsyncMock(return_value=([vector_hit], ""))),
            patch.object(
                retrieval_service,
                "keyword_search",
                side_effect=ElasticsearchQueryComplexityError("safe query still exceeds limit"),
            ),
            patch.object(retrieval_service, "get_embedding_runtime", return_value=SimpleNamespace(provider="test", model_name="test")),
            patch.object(retrieval_service, "record_audit"),
        ):
            response = asyncio.get_event_loop().run_until_complete(
                retrieval_service.hybrid_search(_FakeDB(), None, "kb-1", {})
            )

        self.assertTrue(response["success"])
        self.assertEqual(response["data"]["results"][0]["chunk_id"], "vector-1")
        self.assertIn("降级为向量检索", response["message"])

    def test_keyword_only_complexity_failure_returns_clear_error(self):
        with (
            patch.object(
                retrieval_service,
                "normalize_options",
                return_value=(self.options(use_vector=False, use_keyword=True), None),
            ),
            patch.object(
                retrieval_service,
                "keyword_search",
                side_effect=ElasticsearchQueryComplexityError("safe query still exceeds limit"),
            ),
        ):
            response = asyncio.get_event_loop().run_until_complete(
                retrieval_service.hybrid_search(_FakeDB(), None, "kb-1", {})
            )

        self.assertFalse(response["success"])
        self.assertIn("查询过于复杂", response["message"])

    def test_missing_vectors_do_not_turn_bm25_complexity_into_empty_success(self):
        vector_message = "当前知识库尚未生成向量，请先生成向量"
        with (
            patch.object(retrieval_service, "normalize_options", return_value=(self.options(), None)),
            patch.object(retrieval_service, "vector_search", AsyncMock(return_value=([], vector_message))),
            patch.object(
                retrieval_service,
                "keyword_search",
                side_effect=ElasticsearchQueryComplexityError("safe query still exceeds limit"),
            ),
        ):
            response = asyncio.get_event_loop().run_until_complete(
                retrieval_service.hybrid_search(_FakeDB(), None, "kb-1", {})
            )

        self.assertFalse(response["success"])
        self.assertIn("查询过于复杂", response["message"])
        self.assertIn(vector_message, response["message"])


if __name__ == "__main__":
    unittest.main()
