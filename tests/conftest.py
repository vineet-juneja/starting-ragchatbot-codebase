"""Shared fixtures for the backend test suite.

Everything here is offline: no real ChromaDB, no real Anthropic calls. `pytest`
is configured with `pythonpath = ["backend"]` (see pyproject.toml), so the flat
imports the backend uses (`from vector_store import ...`) resolve here too.
"""

import types
from typing import List, Optional
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from pydantic import BaseModel
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


# --------------------------------------------------------------------------- #
# FastAPI endpoint testing
# --------------------------------------------------------------------------- #
# `backend/app.py` mounts `../frontend` as static files at import time, which
# does not exist in the test environment (and pulls in the real RAGSystem /
# ChromaDB / Anthropic client). Rather than import that module, the endpoints
# are re-declared here against a mocked RAGSystem. The request/response models
# and the route bodies mirror `backend/app.py` — keep them in sync.


@pytest.fixture
def mock_rag_system():
    """A stand-in for `RAGSystem` covering everything the API routes touch."""
    rag = MagicMock(name="RAGSystem")
    rag.query.return_value = ("stub answer", [])
    rag.get_course_analytics.return_value = {
        "total_courses": 0,
        "course_titles": [],
    }
    rag.session_manager.create_session.return_value = "test-session"
    rag.session_manager.delete_session.return_value = None
    return rag


@pytest.fixture
def api_app(mock_rag_system):
    """A minimal FastAPI app exposing the same routes as `backend/app.py`,
    without the static-file mount or startup document loading."""

    app = FastAPI(title="Course Materials RAG System (test)")

    class QueryRequest(BaseModel):
        query: str
        session_id: Optional[str] = None

    class QueryResponse(BaseModel):
        answer: str
        sources: List[str]
        session_id: str

    class CourseStats(BaseModel):
        total_courses: int
        course_titles: List[str]

    @app.post("/api/query", response_model=QueryResponse)
    async def query_documents(request: QueryRequest):
        try:
            session_id = request.session_id
            if not session_id:
                session_id = mock_rag_system.session_manager.create_session()
            answer, sources = mock_rag_system.query(request.query, session_id)
            return QueryResponse(
                answer=answer, sources=sources, session_id=session_id
            )
        except Exception as e:  # noqa: BLE001 - mirrors app.py
            raise HTTPException(status_code=500, detail=str(e))

    @app.get("/api/courses", response_model=CourseStats)
    async def get_course_stats():
        try:
            analytics = mock_rag_system.get_course_analytics()
            return CourseStats(
                total_courses=analytics["total_courses"],
                course_titles=analytics["course_titles"],
            )
        except Exception as e:  # noqa: BLE001 - mirrors app.py
            raise HTTPException(status_code=500, detail=str(e))

    @app.delete("/api/session/{session_id}")
    async def clear_session(session_id: str):
        try:
            mock_rag_system.session_manager.delete_session(session_id)
            return {"status": "ok"}
        except Exception as e:  # noqa: BLE001 - mirrors app.py
            raise HTTPException(status_code=500, detail=str(e))

    @app.get("/")
    async def root():
        # `app.py` serves the static frontend index here; the test app just
        # confirms the route is reachable.
        return {"status": "ok"}

    return app


@pytest.fixture
def api_client(api_app):
    return TestClient(api_app)
