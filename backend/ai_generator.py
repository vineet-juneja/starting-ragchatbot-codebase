from typing import Any, Dict, List, Optional

import anthropic

# Maximum number of sequential tool-calling rounds Claude gets per user query.
# Each round is its own API request, so Claude can reason over one round's
# results before deciding whether to call another tool.
MAX_TOOL_ROUNDS = 2


class AIGenerator:
    """Handles interactions with Anthropic's Claude API for generating responses"""

    # Static system prompt to avoid rebuilding on each call
    SYSTEM_PROMPT = """ You are an AI assistant specialized in course materials and educational content with access to tools for course information.

Tool Usage:
- **search_course_content**: use for questions about specific course content or detailed educational materials.
- **get_course_outline**: use for questions about a course's structure — its lesson list, syllabus, or overview. It returns the course title, the course link, and every lesson's number and title. When answering an outline question, present the course title, the course link, and the complete list of lessons, giving each lesson's number and title.
- **Sequential tool use**: you may use tools in up to 2 separate rounds. After you see a tool's results you may make one additional tool call if the question still needs it — e.g. comparing two courses or lessons, answering a multi-part question, or searching a course you identified from a previous result. Use the second round only when the first result is insufficient; otherwise answer immediately.
- Synthesize tool results into accurate, fact-based responses
- If a tool yields no results, state this clearly without offering alternatives

Response Protocol:
- **General knowledge questions**: Answer using existing knowledge without using tools
- **Course content questions**: search first, then answer — run a second, refined search only if the first did not surface what you need
- **Course outline / structure questions**: Use get_course_outline first, then answer
- **Comparisons / multi-part questions**: gather each piece with its own tool call (2 rounds maximum), then give one synthesized answer
- **No meta-commentary**:
 - Provide direct answers only — no reasoning process, search explanations, or question-type analysis
 - Do not mention "based on the search results"


All responses must be:
1. **Brief, Concise and focused** - Get to the point quickly
2. **Educational** - Maintain instructional value
3. **Clear** - Use accessible language
4. **Example-supported** - Include relevant examples when they aid understanding
Provide only the direct answer to what was asked.
"""

    def __init__(self, api_key: str, model: str):
        self.client = anthropic.Anthropic(api_key=api_key)
        self.model = model

        # Pre-build base API parameters
        # claude-sonnet-5 rejects sampling params (temperature/top_p/top_k) with a 400,
        # and runs adaptive thinking by default. Disable thinking so each response is a
        # plain sequence of text / tool_use blocks (see _extract_text, _run_tool_round).
        self.base_params = {
            "model": self.model,
            "max_tokens": 800,
            "thinking": {"type": "disabled"},
        }

    def generate_response(
        self,
        query: str,
        conversation_history: Optional[str] = None,
        tools: Optional[List] = None,
        tool_manager=None,
    ) -> str:
        """
        Generate an AI response, letting Claude call tools across up to
        MAX_TOOL_ROUNDS sequential rounds before it must answer in prose.

        Each round is a separate API request that still offers the tools, so
        Claude can reason about the previous round's results. The loop stops when
        Claude answers without a tool call, the round cap is reached, or a tool
        raises; if it stops mid-tool-use, one final tools-less call is made to
        force a prose answer.

        Args:
            query: The user's question or request
            conversation_history: Previous messages for context
            tools: Available tools the AI can use
            tool_manager: Manager to execute tools

        Returns:
            Generated response as string
        """

        # Build system content efficiently - avoid string ops when possible
        system_content = (
            f"{self.SYSTEM_PROMPT}\n\nPrevious conversation:\n{conversation_history}"
            if conversation_history
            else self.SYSTEM_PROMPT
        )

        messages: List[Dict[str, Any]] = [{"role": "user", "content": query}]

        # Round 1 - tools offered.
        response = self._call_llm(messages, system_content, tools)

        rounds = 0
        while (
            tool_manager
            and response.stop_reason == "tool_use"
            and rounds < MAX_TOOL_ROUNDS
        ):
            rounds += 1
            tool_results, failed = self._run_tool_round(response, tool_manager)
            if not tool_results:
                # stop_reason was "tool_use" but no tool_use blocks were present -
                # nothing to execute, so return whatever text the response carries.
                return self._extract_text(response)

            messages.append({"role": "assistant", "content": response.content})
            messages.append({"role": "user", "content": tool_results})

            if failed or rounds >= MAX_TOOL_ROUNDS:
                break

            # Next round - tools still offered so Claude can chain another call.
            response = self._call_llm(messages, system_content, tools)

        # If we left the loop mid-tool-use (round cap hit or a tool failed), Claude
        # has not yet produced a prose answer: make one final call without tools.
        if tool_manager and response.stop_reason == "tool_use":
            response = self._call_llm(messages, system_content, tools=None)

        return self._extract_text(response)

    def _call_llm(
        self, messages: List[Dict[str, Any]], system: str, tools: Optional[List]
    ):
        """Make a single Claude API call, offering tools when provided.

        ``messages`` is copied so each request carries an independent snapshot of
        the conversation so far (the caller keeps mutating the original list
        across rounds).
        """
        params = {
            **self.base_params,
            "messages": list(messages),
            "system": system,
        }
        if tools:
            params["tools"] = tools
            params["tool_choice"] = {"type": "auto"}
        return self.client.messages.create(**params)

    def _run_tool_round(self, response, tool_manager):
        """
        Execute every tool_use block in ``response``.

        Returns:
            (tool_results, failed) - ``tool_results`` is the list of tool_result
            blocks to feed back to Claude (empty when the response had no
            tool_use blocks); ``failed`` is True if any tool raised.
        """
        tool_results: List[Dict[str, Any]] = []
        failed = False
        for block in response.content:
            if getattr(block, "type", None) != "tool_use":
                continue
            try:
                result = tool_manager.execute_tool(block.name, **block.input)
            except Exception as exc:  # tool_manager.execute_tool can propagate
                result = f"Tool execution failed: {exc}"
                failed = True
            tool_results.append(
                {
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": result,
                }
            )
        return tool_results, failed

    @staticmethod
    def _extract_text(response) -> str:
        """Return the first text block's text, or '' if the response has none."""
        return next(
            (
                block.text
                for block in response.content
                if getattr(block, "type", None) == "text"
            ),
            "",
        )
