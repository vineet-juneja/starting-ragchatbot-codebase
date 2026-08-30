"""Tests for the FastAPI endpoints (`/api/query`, `/api/courses`,
`/api/session/{id}`, `/`).

The app under test is `api_app` from conftest — the routes are re-declared
there against `mock_rag_system` so the real `backend/app.py` (which mounts a
non-existent static dir and constructs the real RAG stack at import) never
loads. See the note in conftest.py.
"""

import pytest

pytestmark = pytest.mark.api


# --------------------------------------------------------------------------- #
# POST /api/query
# --------------------------------------------------------------------------- #
class TestQueryEndpoint:
    def test_happy_path_returns_answer_sources_and_session(
        self, api_client, mock_rag_system
    ):
        mock_rag_system.query.return_value = (
            "MCP is the Model Context Protocol.",
            ["MCP Course - Lesson 1"],
        )

        resp = api_client.post(
            "/api/query", json={"query": "what is mcp", "session_id": "s-1"}
        )

        assert resp.status_code == 200
        assert resp.json() == {
            "answer": "MCP is the Model Context Protocol.",
            "sources": ["MCP Course - Lesson 1"],
            "session_id": "s-1",
        }
        mock_rag_system.query.assert_called_once_with("what is mcp", "s-1")

    def test_creates_a_session_when_none_supplied(
        self, api_client, mock_rag_system
    ):
        mock_rag_system.session_manager.create_session.return_value = "fresh-sid"
        mock_rag_system.query.return_value = ("answer", [])

        resp = api_client.post("/api/query", json={"query": "hello"})

        assert resp.status_code == 200
        assert resp.json()["session_id"] == "fresh-sid"
        mock_rag_system.session_manager.create_session.assert_called_once_with()
        mock_rag_system.query.assert_called_once_with("hello", "fresh-sid")

    def test_reuses_supplied_session_without_creating_one(
        self, api_client, mock_rag_system
    ):
        api_client.post(
            "/api/query", json={"query": "q", "session_id": "keep-me"}
        )

        mock_rag_system.session_manager.create_session.assert_not_called()

    def test_missing_query_field_is_422(self, api_client):
        resp = api_client.post("/api/query", json={"session_id": "s-1"})
        assert resp.status_code == 422

    def test_empty_body_is_422(self, api_client):
        resp = api_client.post("/api/query", json={})
        assert resp.status_code == 422

    def test_rag_failure_becomes_500_with_detail(
        self, api_client, mock_rag_system
    ):
        mock_rag_system.query.side_effect = RuntimeError("boom")

        resp = api_client.post("/api/query", json={"query": "q"})

        assert resp.status_code == 500
        assert resp.json()["detail"] == "boom"


# --------------------------------------------------------------------------- #
# GET /api/courses
# --------------------------------------------------------------------------- #
class TestCoursesEndpoint:
    def test_returns_analytics_payload(self, api_client, mock_rag_system):
        mock_rag_system.get_course_analytics.return_value = {
            "total_courses": 2,
            "course_titles": ["Course A", "Course B"],
        }

        resp = api_client.get("/api/courses")

        assert resp.status_code == 200
        assert resp.json() == {
            "total_courses": 2,
            "course_titles": ["Course A", "Course B"],
        }

    def test_empty_catalog(self, api_client, mock_rag_system):
        mock_rag_system.get_course_analytics.return_value = {
            "total_courses": 0,
            "course_titles": [],
        }

        resp = api_client.get("/api/courses")

        assert resp.status_code == 200
        assert resp.json() == {"total_courses": 0, "course_titles": []}

    def test_analytics_failure_becomes_500(self, api_client, mock_rag_system):
        mock_rag_system.get_course_analytics.side_effect = RuntimeError("no db")

        resp = api_client.get("/api/courses")

        assert resp.status_code == 500
        assert resp.json()["detail"] == "no db"


# --------------------------------------------------------------------------- #
# DELETE /api/session/{session_id}
# --------------------------------------------------------------------------- #
class TestClearSessionEndpoint:
    def test_deletes_named_session(self, api_client, mock_rag_system):
        resp = api_client.delete("/api/session/abc-123")

        assert resp.status_code == 200
        assert resp.json() == {"status": "ok"}
        mock_rag_system.session_manager.delete_session.assert_called_once_with(
            "abc-123"
        )

    def test_delete_failure_becomes_500(self, api_client, mock_rag_system):
        mock_rag_system.session_manager.delete_session.side_effect = KeyError(
            "missing"
        )

        resp = api_client.delete("/api/session/nope")

        assert resp.status_code == 500


# --------------------------------------------------------------------------- #
# GET /
# --------------------------------------------------------------------------- #
class TestRootEndpoint:
    def test_root_is_reachable(self, api_client):
        resp = api_client.get("/")
        assert resp.status_code == 200
