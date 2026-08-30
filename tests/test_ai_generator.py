"""Tests for `AIGenerator` — does it drive the Anthropic tool-call loop
correctly? The `anthropic` client is patched (see `anthropic_create` fixture);
no network calls happen."""

import pytest
from ai_generator import AIGenerator


@pytest.fixture
def generator(anthropic_create):
    # `anthropic_create` patches `ai_generator.anthropic.Anthropic` before this
    # constructs its client.
    return AIGenerator(api_key="test-key", model="claude-sonnet-5")


# --------------------------------------------------------------------------- #
# Plain generation (no tools / no tool call)
# --------------------------------------------------------------------------- #
class TestGenerateResponsePlain:
    def test_returns_text_from_single_call(self, generator, anthropic_create, messages):
        anthropic_create.return_value = messages.text("hello world")

        out = generator.generate_response("hi")

        assert out == "hello world"
        assert anthropic_create.call_count == 1

    def test_first_call_shape_with_tools(self, generator, anthropic_create, messages):
        anthropic_create.return_value = messages.text("x")

        generator.generate_response(
            "what is mcp", tools=[{"name": "search_course_content"}]
        )

        kwargs = anthropic_create.call_args.kwargs
        assert kwargs["messages"] == [{"role": "user", "content": "what is mcp"}]
        assert kwargs["model"] == "claude-sonnet-5"
        assert kwargs["max_tokens"] == 800
        assert kwargs["tools"] == [{"name": "search_course_content"}]
        assert kwargs["tool_choice"] == {"type": "auto"}
        assert "AI assistant" in kwargs["system"]

    def test_history_injected_into_system_prompt(
        self, generator, anthropic_create, messages
    ):
        anthropic_create.return_value = messages.text("x")

        generator.generate_response("q", conversation_history="User: hi\nAssistant: yo")

        system = anthropic_create.call_args.kwargs["system"]
        assert "Previous conversation:" in system
        assert "User: hi" in system

    def test_no_tools_means_no_tool_choice_key(
        self, generator, anthropic_create, messages
    ):
        anthropic_create.return_value = messages.text("x")

        generator.generate_response("q")

        kwargs = anthropic_create.call_args.kwargs
        assert "tools" not in kwargs
        assert "tool_choice" not in kwargs


# --------------------------------------------------------------------------- #
# Tool-call loop
# --------------------------------------------------------------------------- #
class TestToolExecutionFlow:
    def test_dispatches_tool_with_block_name_and_input(
        self, generator, anthropic_create, messages, recording_tool_manager
    ):
        tm = recording_tool_manager(result="SEARCH TEXT")
        anthropic_create.side_effect = [
            messages.tool_use(
                "search_course_content",
                {"query": "mcp", "lesson_number": 1},
                tool_id="tu_1",
            ),
            messages.text("final answer"),
        ]

        out = generator.generate_response(
            "q", tools=[{"name": "search_course_content"}], tool_manager=tm
        )

        assert tm.calls == [
            {
                "name": "search_course_content",
                "kwargs": {"query": "mcp", "lesson_number": 1},
            }
        ]
        assert out == "final answer"

    def test_second_round_still_offers_tools(
        self, generator, anthropic_create, messages, recording_tool_manager
    ):
        # Round 1 returns tool_use; round 2's call must STILL carry the tools so
        # Claude can chain another call (this is the sequential-rounds behavior).
        tm = recording_tool_manager()
        anthropic_create.side_effect = [
            messages.tool_use("search_course_content", {"query": "x"}),
            messages.text("done"),
        ]

        generator.generate_response("q", tools=[{"name": "t"}], tool_manager=tm)

        assert anthropic_create.call_count == 2
        second = anthropic_create.call_args_list[1].kwargs
        assert second["tools"] == [{"name": "t"}]
        assert second["tool_choice"] == {"type": "auto"}
        assert second["model"] == "claude-sonnet-5"

    def test_tool_result_message_is_appended(
        self, generator, anthropic_create, messages, recording_tool_manager
    ):
        tm = recording_tool_manager(result="THE RESULT")
        anthropic_create.side_effect = [
            messages.tool_use("search_course_content", {"query": "x"}, tool_id="tu_9"),
            messages.text("done"),
        ]

        generator.generate_response("q", tools=[{"name": "t"}], tool_manager=tm)

        second_messages = anthropic_create.call_args_list[1].kwargs["messages"]
        assert second_messages[0] == {"role": "user", "content": "q"}
        assert second_messages[-2]["role"] == "assistant"
        assert second_messages[-1] == {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": "tu_9",
                    "content": "THE RESULT",
                }
            ],
        }

    def test_tool_use_with_text_preamble_still_dispatches(
        self, generator, anthropic_create, messages, recording_tool_manager
    ):
        tm = recording_tool_manager()
        anthropic_create.side_effect = [
            messages.tool_use(
                "search_course_content", {"query": "x"}, preamble="Let me search."
            ),
            messages.text("done"),
        ]

        out = generator.generate_response("q", tools=[{"name": "t"}], tool_manager=tm)

        assert len(tm.calls) == 1
        assert out == "done"

    def test_tool_use_without_tool_manager_falls_through(
        self, generator, anthropic_create, messages
    ):
        # stop_reason == "tool_use" but no tool_manager -> no loop, no second
        # call; _extract_text returns the response's text block.
        anthropic_create.return_value = messages.tool_use(
            "search_course_content", {"query": "x"}, preamble="hi there"
        )

        out = generator.generate_response("q", tools=[{"name": "t"}])

        assert out == "hi there"
        assert anthropic_create.call_count == 1


# --------------------------------------------------------------------------- #
# Request-parameter regression guards (the temperature -> thinking change)
# --------------------------------------------------------------------------- #
class TestRequestParamRegression:
    def test_base_params_disable_thinking_and_omit_sampling(self, generator):
        assert generator.base_params["thinking"] == {"type": "disabled"}
        for key in ("temperature", "top_p", "top_k"):
            assert key not in generator.base_params

    def test_thinking_type_is_an_accepted_value(self, generator):
        assert generator.base_params["thinking"]["type"] in {"disabled", "enabled"}

    def test_create_never_receives_sampling_params(
        self, generator, anthropic_create, messages, recording_tool_manager
    ):
        tm = recording_tool_manager()
        anthropic_create.side_effect = [
            messages.tool_use("search_course_content", {"query": "x"}),
            messages.text("done"),
        ]

        generator.generate_response("q", tools=[{"name": "t"}], tool_manager=tm)

        for call in anthropic_create.call_args_list:
            assert "temperature" not in call.kwargs
            assert "top_p" not in call.kwargs
            assert "top_k" not in call.kwargs

    def test_model_is_passed_through(self, anthropic_create):
        gen = AIGenerator(api_key="k", model="some-model-id")
        assert gen.base_params["model"] == "some-model-id"


# --------------------------------------------------------------------------- #
# Sequential tool rounds (up to MAX_TOOL_ROUNDS separate API rounds)
# --------------------------------------------------------------------------- #
class TestSequentialToolRounds:
    def test_two_tool_rounds_then_synthesis(
        self, generator, anthropic_create, messages, recording_tool_manager
    ):
        # outline lookup -> content search -> synthesized answer
        tm = recording_tool_manager(result="TR")
        anthropic_create.side_effect = [
            messages.tool_use(
                "get_course_outline", {"course_title": "MCP"}, tool_id="tu_a"
            ),
            messages.tool_use(
                "search_course_content", {"query": "lesson 2 topic"}, tool_id="tu_b"
            ),
            messages.text("combined answer"),
        ]

        out = generator.generate_response(
            "q",
            tools=[
                {"name": "get_course_outline"},
                {"name": "search_course_content"},
            ],
            tool_manager=tm,
        )

        assert out == "combined answer"
        assert tm.calls == [
            {"name": "get_course_outline", "kwargs": {"course_title": "MCP"}},
            {"name": "search_course_content", "kwargs": {"query": "lesson 2 topic"}},
        ]
        assert anthropic_create.call_count == 3
        calls = anthropic_create.call_args_list
        assert "tools" in calls[0].kwargs
        assert "tools" in calls[1].kwargs
        assert "tools" not in calls[2].kwargs
        assert "tool_choice" not in calls[2].kwargs

    def test_stops_when_first_response_has_no_tool_use(
        self, generator, anthropic_create, messages, recording_tool_manager
    ):
        tm = recording_tool_manager()
        anthropic_create.return_value = messages.text("direct answer")

        out = generator.generate_response("q", tools=[{"name": "t"}], tool_manager=tm)

        assert out == "direct answer"
        assert anthropic_create.call_count == 1
        assert tm.calls == []

    def test_stops_at_two_round_cap(
        self, generator, anthropic_create, messages, recording_tool_manager
    ):
        # Model keeps asking for tools; the 4th response must never be consumed.
        tm = recording_tool_manager()
        anthropic_create.side_effect = [
            messages.tool_use("search_course_content", {"query": "a"}, tool_id="t1"),
            messages.tool_use("search_course_content", {"query": "b"}, tool_id="t2"),
            messages.text("final"),
            messages.tool_use("search_course_content", {"query": "c"}, tool_id="t3"),
        ]

        out = generator.generate_response("q", tools=[{"name": "t"}], tool_manager=tm)

        assert anthropic_create.call_count == 3
        assert [c["kwargs"] for c in tm.calls] == [{"query": "a"}, {"query": "b"}]
        assert out == "final"
        assert "tools" not in anthropic_create.call_args_list[2].kwargs

    def test_tool_failure_terminates_with_synthesis(
        self, generator, anthropic_create, messages
    ):
        class BoomToolManager:
            def __init__(self):
                self.calls = []

            def execute_tool(self, name, **kwargs):
                self.calls.append({"name": name, "kwargs": kwargs})
                raise RuntimeError("chroma down")

        tm = BoomToolManager()
        anthropic_create.side_effect = [
            messages.tool_use("search_course_content", {"query": "x"}, tool_id="tu_1"),
            messages.text("Sorry, I could not retrieve that right now."),
        ]

        out = generator.generate_response("q", tools=[{"name": "t"}], tool_manager=tm)

        assert out == "Sorry, I could not retrieve that right now."
        assert len(tm.calls) == 1
        assert anthropic_create.call_count == 2  # round 1 + synthesis, no round 2
        synth = anthropic_create.call_args_list[1].kwargs
        assert "tools" not in synth
        assert synth["messages"][-1] == {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": "tu_1",
                    "content": "Tool execution failed: chroma down",
                }
            ],
        }

    def test_context_preserved_across_rounds(
        self, generator, anthropic_create, messages, recording_tool_manager
    ):
        tm = recording_tool_manager(result="R")
        anthropic_create.side_effect = [
            messages.tool_use(
                "get_course_outline", {"course_title": "MCP"}, tool_id="tu_a"
            ),
            messages.tool_use("search_course_content", {"query": "x"}, tool_id="tu_b"),
            messages.text("ok"),
        ]

        generator.generate_response("q", tools=[{"name": "t"}], tool_manager=tm)

        calls = anthropic_create.call_args_list

        round2_messages = calls[1].kwargs["messages"]
        assert round2_messages[0] == {"role": "user", "content": "q"}
        assert round2_messages[1]["role"] == "assistant"
        assert round2_messages[2] == {
            "role": "user",
            "content": [{"type": "tool_result", "tool_use_id": "tu_a", "content": "R"}],
        }

        synth_messages = calls[2].kwargs["messages"]
        assert len(synth_messages) == 5
        assert synth_messages[3]["role"] == "assistant"
        assert synth_messages[4] == {
            "role": "user",
            "content": [{"type": "tool_result", "tool_use_id": "tu_b", "content": "R"}],
        }
        assert synth_messages[:3] == round2_messages

    def test_each_round_offers_tools_but_synthesis_does_not(
        self, generator, anthropic_create, messages, recording_tool_manager
    ):
        tm = recording_tool_manager()
        anthropic_create.side_effect = [
            messages.tool_use("search_course_content", {"query": "a"}, tool_id="t1"),
            messages.tool_use("search_course_content", {"query": "b"}, tool_id="t2"),
            messages.text("done"),
        ]

        generator.generate_response(
            "q", tools=[{"name": "search_course_content"}], tool_manager=tm
        )

        calls = anthropic_create.call_args_list
        assert calls[0].kwargs["tools"] == [{"name": "search_course_content"}]
        assert calls[0].kwargs["tool_choice"] == {"type": "auto"}
        assert calls[1].kwargs["tools"] == [{"name": "search_course_content"}]
        assert calls[1].kwargs["tool_choice"] == {"type": "auto"}
        assert "tools" not in calls[2].kwargs
        assert "tool_choice" not in calls[2].kwargs

    def test_single_tool_round_then_answer_makes_two_calls(
        self, generator, anthropic_create, messages, recording_tool_manager
    ):
        # Round 2 answers with text -> no forced 3rd synthesis call.
        tm = recording_tool_manager()
        anthropic_create.side_effect = [
            messages.tool_use("search_course_content", {"query": "x"}, tool_id="t1"),
            messages.text("answer"),
        ]

        out = generator.generate_response("q", tools=[{"name": "t"}], tool_manager=tm)

        assert anthropic_create.call_count == 2
        assert out == "answer"
        assert anthropic_create.call_args_list[1].kwargs["tools"] == [{"name": "t"}]

    def test_max_tool_rounds_constant_is_two(self):
        from ai_generator import MAX_TOOL_ROUNDS

        assert MAX_TOOL_ROUNDS == 2
