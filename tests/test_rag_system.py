"""Tests for `RAGSystem.query` — how the RAG layer orchestrates a
content-related question. The vector store is faked and the Anthropic client is
patched; `ToolManager`, the tools, and `SessionManager` run for real."""

import types

import pytest

from rag_system import RAGSystem


@pytest.fixture
def rag_config():
    return types.SimpleNamespace(
        CHUNK_SIZE=800,
        CHUNK_OVERLAP=100,
        CHROMA_PATH="unused-in-tests",
        EMBEDDING_MODEL="all-MiniLM-L6-v2",
        MAX_RESULTS=5,
        ANTHROPIC_API_KEY="test-key",
        ANTHROPIC_MODEL="claude-sonnet-5",
        MAX_HISTORY=2,
    )


@pytest.fixture
def rag(mocker, rag_config, fake_vector_store):
    """RAGSystem with a fully mocked AIGenerator (generate_response -> 'AI ANSWER')."""
    mocker.patch("rag_system.DocumentProcessor")
    mocker.patch("rag_system.VectorStore", return_value=fake_vector_store)
    fake_ai = mocker.patch("rag_system.AIGenerator").return_value
    fake_ai.generate_response.return_value = "AI ANSWER"

    system = RAGSystem(rag_config)
    system._fake_ai = fake_ai  # test convenience handle
    return system


@pytest.fixture
def rag_real_ai(mocker, rag_config, fake_vector_store, anthropic_create):
    """RAGSystem with a REAL AIGenerator/ToolManager/tools; only the Anthropic
    client (`anthropic_create`) and the vector store are faked."""
    mocker.patch("rag_system.DocumentProcessor")
    mocker.patch("rag_system.VectorStore", return_value=fake_vector_store)
    return RAGSystem(rag_config)


# --------------------------------------------------------------------------- #
# query() contract
# --------------------------------------------------------------------------- #
class TestQueryContract:
    def test_returns_answer_and_sources_tuple(self, rag):
        result = rag.query("What is MCP?")

        assert isinstance(result, tuple) and len(result) == 2
        answer, sources = result
        assert answer == "AI ANSWER"
        assert sources == []

    def test_hands_generator_both_tools_and_the_manager(self, rag):
        rag.query("q")

        kwargs = rag._fake_ai.generate_response.call_args.kwargs
        tool_names = {d["name"] for d in kwargs["tools"]}
        assert tool_names == {"search_course_content", "get_course_outline"}
        assert kwargs["tool_manager"] is rag.tool_manager

    def test_prompt_is_wrapped_before_generation(self, rag):
        rag.query("What is MCP?")

        kwargs = rag._fake_ai.generate_response.call_args.kwargs
        assert (
            kwargs["query"]
            == "Answer this question about course materials: What is MCP?"
        )


# --------------------------------------------------------------------------- #
# Sources plumbing
# --------------------------------------------------------------------------- #
class TestSourcesPlumbing:
    def test_returns_tool_sources_then_resets_them(self, rag):
        rag.search_tool.last_sources = ["MCP Course - Lesson 1"]

        _answer, sources = rag.query("q")

        assert sources == ["MCP Course - Lesson 1"]
        assert rag.tool_manager.get_last_sources() == []
        assert rag.search_tool.last_sources == []

    def test_sources_empty_when_no_tool_ran(self, rag):
        _answer, sources = rag.query("q")
        assert sources == []


# --------------------------------------------------------------------------- #
# Session handling
# --------------------------------------------------------------------------- #
class TestSessionFlow:
    def test_history_read_and_raw_query_saved(self, rag, mocker):
        spy_hist = mocker.spy(rag.session_manager, "get_conversation_history")
        spy_add = mocker.spy(rag.session_manager, "add_exchange")
        sid = rag.session_manager.create_session()

        rag.query("raw question", session_id=sid)

        spy_hist.assert_called_once_with(sid)
        # the UNWRAPPED query is what gets stored
        spy_add.assert_called_once_with(sid, "raw question", "AI ANSWER")

    def test_prior_history_reaches_the_generator(self, rag):
        sid = rag.session_manager.create_session()
        rag.session_manager.add_exchange(sid, "earlier q", "earlier a")

        rag.query("next q", session_id=sid)

        kwargs = rag._fake_ai.generate_response.call_args.kwargs
        assert "earlier q" in kwargs["conversation_history"]

    def test_no_session_id_skips_history_and_exchange(self, rag, mocker):
        spy_hist = mocker.spy(rag.session_manager, "get_conversation_history")
        spy_add = mocker.spy(rag.session_manager, "add_exchange")

        rag.query("q")

        spy_hist.assert_not_called()
        spy_add.assert_not_called()
        assert rag._fake_ai.generate_response.call_args.kwargs[
            "conversation_history"
        ] is None


# --------------------------------------------------------------------------- #
# Full chain with only the network mocked
# --------------------------------------------------------------------------- #
class TestFullChainNetworkMocked:
    def test_search_tool_runs_and_answer_with_sources_returned(
        self,
        rag_real_ai,
        fake_vector_store,
        anthropic_create,
        messages,
        make_search_results,
    ):
        fake_vector_store.search_result = make_search_results(
            [("MCP is a protocol for tools.", "MCP Course", 1)]
        )
        fake_vector_store.lesson_links[("MCP Course", 1)] = "https://example.com/l1"
        anthropic_create.side_effect = [
            messages.tool_use(
                "search_course_content", {"query": "what is mcp"}, tool_id="tu_1"
            ),
            messages.text("MCP is the Model Context Protocol."),
        ]

        answer, sources = rag_real_ai.query("what is mcp")

        assert answer == "MCP is the Model Context Protocol."
        assert fake_vector_store.search_calls[0]["query"] == "what is mcp"
        assert sources == [
            '<a href="https://example.com/l1" target="_blank" '
            'rel="noopener noreferrer" class="source-link">MCP Course - Lesson 1</a>'
        ]
        assert anthropic_create.call_count == 2

    def test_store_error_is_handled_without_raising(
        self,
        rag_real_ai,
        fake_vector_store,
        anthropic_create,
        messages,
        make_search_results,
    ):
        fake_vector_store.search_result = make_search_results(
            error="Search error: boom"
        )
        anthropic_create.side_effect = [
            messages.tool_use("search_course_content", {"query": "x"}, tool_id="tu_1"),
            messages.text("I could not find anything on that."),
        ]

        answer, sources = rag_real_ai.query("x")

        assert answer == "I could not find anything on that."
        assert sources == []

    def test_two_search_rounds_make_three_calls(
        self,
        rag_real_ai,
        fake_vector_store,
        anthropic_create,
        messages,
        make_search_results,
    ):
        # A comparison-style question: Claude searches twice in separate rounds,
        # then a tools-less synthesis call produces the answer.
        fake_vector_store.search_result = make_search_results(
            [("Body text.", "Course A", 1)]
        )
        anthropic_create.side_effect = [
            messages.tool_use("search_course_content", {"query": "a"}, tool_id="t1"),
            messages.tool_use("search_course_content", {"query": "b"}, tool_id="t2"),
            messages.text("synthesized"),
        ]

        answer, sources = rag_real_ai.query("compare a and b")

        assert answer == "synthesized"
        assert anthropic_create.call_count == 3
        assert [c["query"] for c in fake_vector_store.search_calls] == ["a", "b"]
        assert sources  # populated from a search round


# --------------------------------------------------------------------------- #
# Analytics + config sanity
# --------------------------------------------------------------------------- #
class TestAnalyticsAndConfig:
    def test_get_course_analytics_reads_from_store(self, rag, fake_vector_store):
        fake_vector_store.course_count = 3
        fake_vector_store.course_titles = ["A", "B", "C"]

        assert rag.get_course_analytics() == {
            "total_courses": 3,
            "course_titles": ["A", "B", "C"],
        }

    def test_real_config_values_are_sane(self):
        from config import config

        assert config.MAX_RESULTS > 0, (
            "MAX_RESULTS must be > 0 or every content search returns nothing"
        )
        assert config.MAX_HISTORY >= 0
        assert isinstance(config.ANTHROPIC_MODEL, str) and config.ANTHROPIC_MODEL
