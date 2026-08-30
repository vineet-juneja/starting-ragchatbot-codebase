"""Shared fixtures for the backend test suite.

Everything here is offline: no real ChromaDB, no real Anthropic calls. `pytest`
is configured with `pythonpath = ["backend"]` (see pyproject.toml), so the flat
imports the backend uses (`from vector_store import ...`) resolve here too.
"""

import types

import pytest
from vector_store import SearchResults


# --------------------------------------------------------------------------- #
# Search-result construction
# --------------------------------------------------------------------------- #
@pytest.fixture
def make_search_results():
    """Factory for real `SearchResults` objects.

    `chunks` is a list of (text, course_title, lesson_number) tuples. Pass
    `error=...` to build the error shape (documents stay empty).
    """

    def _make(chunks=(), error=None):
        docs = [text for (text, _title, _lesson) in chunks]
        meta = [
            {"course_title": title, "lesson_number": lesson}
            for (_text, title, lesson) in chunks
        ]
        distances = [round(0.1 * (i + 1), 3) for i in range(len(docs))]
        return SearchResults(
            documents=docs, metadata=meta, distances=distances, error=error
        )

    return _make


# --------------------------------------------------------------------------- #
# Fake vector store
# --------------------------------------------------------------------------- #
class FakeVectorStore:
    """Stand-in for `VectorStore` exposing only what the tools/RAG layer touch.

    Deliberately hand-rolled (not `MagicMock(spec=VectorStore)`) so that a
    renamed or dropped method raises `AttributeError` in the test instead of
    silently returning a mock.
    """

    def __init__(self):
        # search() returns this; may be a SearchResults or a zero-arg callable
        self.search_result = SearchResults(documents=[], metadata=[], distances=[])
        self.search_calls = []
        self.lesson_links = {}  # (course_title, lesson_number) -> url
        self.outline_result = None  # dict or None, returned by get_course_outline
        self.outline_calls = []
        self.course_count = 0
        self.course_titles = []

    def search(self, query, course_name=None, lesson_number=None, limit=None):
        self.search_calls.append(
            {
                "query": query,
                "course_name": course_name,
                "lesson_number": lesson_number,
                "limit": limit,
            }
        )
        result = self.search_result
        return result() if callable(result) else result

    def get_lesson_link(self, course_title, lesson_number):
        return self.lesson_links.get((course_title, lesson_number))

    def get_course_outline(self, course_name):
        self.outline_calls.append(course_name)
        return self.outline_result

    def get_course_count(self):
        return self.course_count

    def get_existing_course_titles(self):
        return list(self.course_titles)


@pytest.fixture
def fake_vector_store():
    return FakeVectorStore()


# --------------------------------------------------------------------------- #
# Anthropic client stub
# --------------------------------------------------------------------------- #
def _block(**kwargs):
    return types.SimpleNamespace(**kwargs)


class MessageFactory:
    """Builds objects shaped like `anthropic` `Message` responses."""

    @staticmethod
    def text(text, stop_reason="end_turn"):
        return types.SimpleNamespace(
            stop_reason=stop_reason,
            content=[_block(type="text", text=text)],
        )

    @staticmethod
    def tool_use(name, tool_input, tool_id="tool_1", preamble=None):
        content = []
        if preamble is not None:
            content.append(_block(type="text", text=preamble))
        content.append(
            _block(type="tool_use", name=name, input=dict(tool_input), id=tool_id)
        )
        return types.SimpleNamespace(stop_reason="tool_use", content=content)


@pytest.fixture
def messages():
    """Response builder: `messages.text(...)` / `messages.tool_use(...)`."""
    return MessageFactory


@pytest.fixture
def anthropic_create(mocker):
    """Patch `anthropic.Anthropic` inside `ai_generator` and hand back the
    `client.messages.create` Mock so a test can script `.return_value` /
    `.side_effect` and assert on `.call_args_list`.
    """
    create = mocker.Mock(name="messages.create")
    fake_client = types.SimpleNamespace(messages=types.SimpleNamespace(create=create))
    mocker.patch("ai_generator.anthropic.Anthropic", return_value=fake_client)
    return create


# --------------------------------------------------------------------------- #
# Recording tool manager (for AIGenerator tests)
# --------------------------------------------------------------------------- #
class RecordingToolManager:
    """Minimal `ToolManager` substitute that records `execute_tool` calls."""

    def __init__(self, result="TOOL RESULT"):
        self.result = result
        self.calls = []

    def execute_tool(self, name, **kwargs):
        self.calls.append({"name": name, "kwargs": kwargs})
        return self.result


@pytest.fixture
def recording_tool_manager():
    return RecordingToolManager
