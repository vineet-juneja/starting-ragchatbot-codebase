"""Tests for `CourseSearchTool.execute` (+ `_format_results`, `CourseOutlineTool`,
and `ToolManager`, since the sources plumbing lives in the same module)."""

from search_tools import CourseOutlineTool, CourseSearchTool, ToolManager
from vector_store import SearchResults


# --------------------------------------------------------------------------- #
# CourseSearchTool.execute — result handling
# --------------------------------------------------------------------------- #
class TestCourseSearchToolExecute:
    def test_returns_formatted_results_on_success(
        self, fake_vector_store, make_search_results
    ):
        fake_vector_store.search_result = make_search_results(
            [
                ("MCP lets you expose tools to a model.", "MCP Course", 1),
                ("Servers speak JSON-RPC.", "MCP Course", 2),
            ]
        )
        tool = CourseSearchTool(fake_vector_store)

        out = tool.execute("what is mcp")

        assert "[MCP Course - Lesson 1]" in out
        assert "MCP lets you expose tools to a model." in out
        assert "[MCP Course - Lesson 2]" in out
        assert "Servers speak JSON-RPC." in out

    def test_forwards_query_and_filters_to_store(
        self, fake_vector_store, make_search_results
    ):
        fake_vector_store.search_result = make_search_results([("t", "Course A", 3)])
        tool = CourseSearchTool(fake_vector_store)

        tool.execute("q", course_name="MCP", lesson_number=3)

        assert fake_vector_store.search_calls == [
            {"query": "q", "course_name": "MCP", "lesson_number": 3, "limit": None}
        ]

    def test_error_from_store_is_returned_verbatim(
        self, fake_vector_store, make_search_results
    ):
        fake_vector_store.search_result = make_search_results(
            error="No course found matching 'zzz'"
        )
        tool = CourseSearchTool(fake_vector_store)

        assert tool.execute("q", course_name="zzz") == "No course found matching 'zzz'"

    def test_empty_results_give_plain_message(
        self, fake_vector_store, make_search_results
    ):
        fake_vector_store.search_result = make_search_results([])
        tool = CourseSearchTool(fake_vector_store)

        assert tool.execute("q") == "No relevant content found."

    def test_empty_results_include_filter_context(
        self, fake_vector_store, make_search_results
    ):
        fake_vector_store.search_result = make_search_results([])
        tool = CourseSearchTool(fake_vector_store)

        msg = tool.execute("q", course_name="MCP", lesson_number=2)

        assert msg == "No relevant content found in course 'MCP' in lesson 2."

    def test_execute_does_not_raise_on_error_or_empty(
        self, fake_vector_store, make_search_results
    ):
        tool = CourseSearchTool(fake_vector_store)

        fake_vector_store.search_result = make_search_results(error="boom")
        assert isinstance(tool.execute("q"), str)

        fake_vector_store.search_result = make_search_results([])
        assert isinstance(tool.execute("q"), str)


# --------------------------------------------------------------------------- #
# CourseSearchTool — source citations
# --------------------------------------------------------------------------- #
class TestCourseSearchToolSources:
    def test_source_is_anchor_when_lesson_link_present(
        self, fake_vector_store, make_search_results
    ):
        fake_vector_store.search_result = make_search_results([("t", "Course A", 1)])
        fake_vector_store.lesson_links[("Course A", 1)] = "https://example.com/l1"
        tool = CourseSearchTool(fake_vector_store)

        tool.execute("q")

        assert tool.last_sources == [
            '<a href="https://example.com/l1" target="_blank" '
            'rel="noopener noreferrer" class="source-link">Course A - Lesson 1</a>'
        ]

    def test_source_is_plain_label_when_no_link(
        self, fake_vector_store, make_search_results
    ):
        fake_vector_store.search_result = make_search_results([("t", "Course A", 1)])
        tool = CourseSearchTool(fake_vector_store)

        tool.execute("q")

        assert tool.last_sources == ["Course A - Lesson 1"]

    def test_non_http_link_falls_back_to_plain_label(
        self, fake_vector_store, make_search_results
    ):
        fake_vector_store.search_result = make_search_results([("t", "Course A", 1)])
        fake_vector_store.lesson_links[("Course A", 1)] = "ftp://nope/l1"
        tool = CourseSearchTool(fake_vector_store)

        tool.execute("q")

        assert tool.last_sources == ["Course A - Lesson 1"]

    def test_sources_deduplicated_preserving_order(
        self, fake_vector_store, make_search_results
    ):
        fake_vector_store.search_result = make_search_results(
            [
                ("chunk one", "Course A", 1),
                ("chunk two", "Course A", 1),
                ("chunk three", "Course A", 2),
            ]
        )
        tool = CourseSearchTool(fake_vector_store)

        tool.execute("q")

        assert tool.last_sources == ["Course A - Lesson 1", "Course A - Lesson 2"]

    def test_metadata_without_lesson_number(self, fake_vector_store):
        fake_vector_store.search_result = SearchResults(
            documents=["doc body"],
            metadata=[{"course_title": "Course A"}],
            distances=[0.1],
        )
        tool = CourseSearchTool(fake_vector_store)

        out = tool.execute("q")

        assert out == "[Course A]\ndoc body"
        assert tool.last_sources == ["Course A"]

    def test_missing_course_title_uses_unknown(self, fake_vector_store):
        fake_vector_store.search_result = SearchResults(
            documents=["doc body"], metadata=[{}], distances=[0.1]
        )
        tool = CourseSearchTool(fake_vector_store)

        out = tool.execute("q")

        assert out.startswith("[unknown]")
        assert tool.last_sources == ["unknown"]


# --------------------------------------------------------------------------- #
# Tool definition
# --------------------------------------------------------------------------- #
def test_search_tool_definition_shape(fake_vector_store):
    d = CourseSearchTool(fake_vector_store).get_tool_definition()

    assert d["name"] == "search_course_content"
    assert d["input_schema"]["required"] == ["query"]
    assert set(d["input_schema"]["properties"]) == {
        "query",
        "course_name",
        "lesson_number",
    }


# --------------------------------------------------------------------------- #
# CourseOutlineTool.execute
# --------------------------------------------------------------------------- #
class TestCourseOutlineTool:
    def test_formats_outline_and_sorts_lessons(self, fake_vector_store):
        fake_vector_store.outline_result = {
            "course_title": "MCP Course",
            "course_link": "https://example.com/mcp",
            "lessons": [
                {"lesson_number": 2, "lesson_title": "Servers"},
                {"lesson_number": 1, "lesson_title": "Intro"},
            ],
        }
        tool = CourseOutlineTool(fake_vector_store)

        out = tool.execute("MCP")

        assert "Course: MCP Course" in out
        assert "Course link: https://example.com/mcp" in out
        assert "Lessons (2):" in out
        assert out.index("Lesson 1: Intro") < out.index("Lesson 2: Servers")
        assert tool.last_sources == [
            '<a href="https://example.com/mcp" target="_blank" '
            'rel="noopener noreferrer" class="source-link">MCP Course</a>'
        ]

    def test_missing_course_returns_message(self, fake_vector_store):
        fake_vector_store.outline_result = None
        tool = CourseOutlineTool(fake_vector_store)

        assert tool.execute("ghost") == "No course found matching 'ghost'."

    def test_definition_shape(self, fake_vector_store):
        d = CourseOutlineTool(fake_vector_store).get_tool_definition()

        assert d["name"] == "get_course_outline"
        assert d["input_schema"]["required"] == ["course_title"]


# --------------------------------------------------------------------------- #
# ToolManager
# --------------------------------------------------------------------------- #
class TestToolManager:
    def _manager_with_both_tools(self, fake_vector_store):
        mgr = ToolManager()
        search = CourseSearchTool(fake_vector_store)
        mgr.register_tool(search)
        mgr.register_tool(CourseOutlineTool(fake_vector_store))
        return mgr, search

    def test_register_keys_by_definition_name(self, fake_vector_store):
        mgr, _ = self._manager_with_both_tools(fake_vector_store)
        assert set(mgr.tools) == {"search_course_content", "get_course_outline"}

    def test_execute_tool_dispatches_kwargs(
        self, fake_vector_store, make_search_results
    ):
        fake_vector_store.search_result = make_search_results([("t", "C", 1)])
        mgr, _ = self._manager_with_both_tools(fake_vector_store)

        out = mgr.execute_tool("search_course_content", query="hello")

        assert "[C - Lesson 1]" in out
        assert fake_vector_store.search_calls[0]["query"] == "hello"

    def test_unknown_tool_returns_message(self):
        assert ToolManager().execute_tool("nope") == "Tool 'nope' not found"

    def test_get_last_sources_returns_first_nonempty(
        self, fake_vector_store, make_search_results
    ):
        fake_vector_store.search_result = make_search_results([("t", "C", 1)])
        mgr, _ = self._manager_with_both_tools(fake_vector_store)

        mgr.execute_tool("search_course_content", query="q")

        assert mgr.get_last_sources() == ["C - Lesson 1"]

    def test_reset_sources_clears_every_tool(
        self, fake_vector_store, make_search_results
    ):
        fake_vector_store.search_result = make_search_results([("t", "C", 1)])
        mgr, search = self._manager_with_both_tools(fake_vector_store)
        mgr.execute_tool("search_course_content", query="q")

        mgr.reset_sources()

        assert search.last_sources == []
        assert mgr.get_last_sources() == []

    def test_get_tool_definitions_lists_all_registered(self, fake_vector_store):
        mgr, _ = self._manager_with_both_tools(fake_vector_store)
        names = {d["name"] for d in mgr.get_tool_definitions()}
        assert names == {"search_course_content", "get_course_outline"}
