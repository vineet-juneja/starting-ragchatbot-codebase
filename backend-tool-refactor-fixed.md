# Backend Tool Refactor — Sequential Tool Calling

**Status:** Done. 56 tests passing. Verified live (and confirmed by the user) that the
reported bug is fixed.

**Files changed**

| File | Change |
|---|---|
| `backend/ai_generator.py` | Core refactor: one tool round → up to 2 sequential rounds |
| `tests/test_ai_generator.py` | 1 test renamed + reworked, new `TestSequentialToolRounds` (8 tests) |
| `tests/test_rag_system.py` | 1 new end-to-end test for the 3-call flow |

---

## 1. The problem

### Symptom (seen in the running app)

Asking *"What was covered in lesson 5 of the MCP course?"* returned only:

> Let me get the specific content details for that lesson.

No lesson content — just the lead-in sentence. A *Sources* entry was populated, but the
answer body was empty.

### Root cause

`AIGenerator.generate_response` did exactly **one** tool round:

1. First API call — Claude is offered the tools, responds with `stop_reason == "tool_use"`.
2. `_handle_tool_execution` runs the tool(s), appends the results, and makes a **second API
   call with the tools removed** — forcing a prose answer.
3. Returns `final_response.content[0].text`.

A question like "details of lesson 5" needs **two** tool calls in sequence:

1. `get_course_outline` → find lesson 5's title (this is what populated *Sources*).
2. `search_course_content` → fetch the actual lesson text, using the title from step 1.

But step 2 can never happen: the second call has no tools. Claude emits the sentence it
*would* have said before searching — *"Let me get the specific content details for that
lesson."* — and that string becomes the whole answer. Every query needing outline→search,
a comparison, or a multi-part lookup failed the same way.

The one-round limit was also baked into the system prompt (*"One search per query
maximum."*).

---

## 2. The fix — `backend/ai_generator.py`

Replaced the single-shot `_handle_tool_execution` with a **bounded loop** over one growing
`messages` list. Each round is its own API request that **still offers the tools**, so
Claude can look at a result and decide whether to call another tool.

### New module constant

```python
# Maximum number of sequential tool-calling rounds Claude gets per user query.
MAX_TOOL_ROUNDS = 2
```

### New `generate_response` control flow

```python
messages = [{"role": "user", "content": query}]

# Round 1 — tools offered.
response = self._call_llm(messages, system_content, tools)

rounds = 0
while (tool_manager
       and response.stop_reason == "tool_use"
       and rounds < MAX_TOOL_ROUNDS):
    rounds += 1
    tool_results, failed = self._run_tool_round(response, tool_manager)
    if not tool_results:                       # tool_use stop but no tool_use blocks
        return self._extract_text(response)

    messages.append({"role": "assistant", "content": response.content})
    messages.append({"role": "user", "content": tool_results})

    if failed or rounds >= MAX_TOOL_ROUNDS:
        break                                  # tool error, or round cap reached

    # Next round — tools still offered so Claude can chain another call.
    response = self._call_llm(messages, system_content, tools)

# Left the loop mid-tool-use (cap hit or a tool failed) → one final tools-less call
# so Claude actually answers in prose.
if tool_manager and response.stop_reason == "tool_use":
    response = self._call_llm(messages, system_content, tools=None)

return self._extract_text(response)
```

### Termination — the three stop conditions

| Condition | How it exits |
|---|---|
| **(a) 2 rounds completed** | `rounds >= MAX_TOOL_ROUNDS` breaks the loop; if round 2 was still `tool_use`, the post-loop `if` makes one tools-less synthesis call |
| **(b) response has no `tool_use`** | `while` guard is false → return that response's text directly |
| **(c) a tool call raises** | `_run_tool_round` catches it, feeds `"Tool execution failed: <exc>"` back as the tool result, sets `failed=True` → loop breaks → tools-less synthesis call so Claude can respond gracefully with what it has |

### API-call budget

- **Max 3** `create()` calls: round 1 (tools) + round 2 (tools) + at most one tools-less
  synthesis call.
- The synthesis call fires **only** if the loop stopped mid-tool-use (cap or failure). If
  Claude answers with text in round 1 or 2, that text is returned directly — so the common
  *"one search → answer"* path still costs exactly **2** calls.

### New private helpers

| Helper | Purpose |
|---|---|
| `_call_llm(messages, system, tools)` | Builds params from `base_params` + a **copy** of `messages` + `system`; adds `tools` and `tool_choice={"type": "auto"}` only when `tools` is given. Copying `messages` per call means each request carries an independent snapshot of the conversation. |
| `_run_tool_round(response, tool_manager)` | Iterates `response.content` for `tool_use` blocks, calls `tool_manager.execute_tool(name, **input)` inside a `try/except`, returns `(tool_results, failed)`. |
| `_extract_text(response)` | Returns the first `type == "text"` block's text, or `""`. Replaces the fragile `response.content[0].text` (which breaks when block 0 is a text preamble followed by a `tool_use` block, or a `tool_use` block itself). |

`_handle_tool_execution` was **deleted** — private, no external or test callers; its
param-assembly and tool loop moved into the helpers above.

### System-prompt update

- Removed *"One search per query maximum."*
- Added a **Sequential tool use** bullet: up to 2 rounds; make a second call only when the
  first result is insufficient (comparisons, multi-part questions, or searching a
  course/topic identified from the first result); otherwise answer immediately.
- Added a **Comparisons / multi-part questions** response-protocol line: gather each piece
  with its own tool call, then give one synthesized answer.

### Unchanged

- `generate_response` signature and `str` return type — its only production caller,
  `rag_system.py:124`, and the chain up to `app.py`'s Pydantic `answer: str` are untouched.
- `__init__`, `base_params` (`max_tokens: 800`, `thinking: {"type": "disabled"}`, no
  sampling params).
- `config.py`, `rag_system.py`, `app.py`, `search_tools.py` — no changes.

---

## 3. Test changes

Tests assert **external behavior** — number and shape of `create()` calls, which tools
`execute_tool` received, the final returned string — never private state.

### `tests/test_ai_generator.py`

**One existing test reworked:** `test_makes_second_call_without_tools` →
`test_second_round_still_offers_tools`. The old test asserted the 2nd call had no tools;
under sequential rounds the 2nd call *does* carry tools, so it now asserts exactly that.

All other pre-existing tests pass unchanged.

**New class `TestSequentialToolRounds` (8 tests):**

| Test | What it pins |
|---|---|
| `test_two_tool_rounds_then_synthesis` | outline → search → synthesis; `execute_tool` called twice with the right names/args; 3 `create()` calls; rounds 1–2 carry tools, call 3 does not |
| `test_stops_when_first_response_has_no_tool_use` | plain answer → 1 call, no tool execution |
| `test_stops_at_two_round_cap` | model keeps asking for tools → capped at 2 executions + 1 synthesis; the 4th scripted response is never consumed |
| `test_tool_failure_terminates_with_synthesis` | a tool that raises → `"Tool execution failed: chroma down"` fed back, loop stops, synthesis call made, no round 2 |
| `test_context_preserved_across_rounds` | round 2 sees round 1's `assistant` + `tool_result` messages; the synthesis call sees the full 5-message transcript |
| `test_each_round_offers_tools_but_synthesis_does_not` | `tools` + `tool_choice` present on every round call, absent on the synthesis call |
| `test_single_tool_round_then_answer_makes_two_calls` | when round 2 answers with text, **no** forced 3rd call |
| `test_max_tool_rounds_constant_is_two` | `MAX_TOOL_ROUNDS == 2` |

### `tests/test_rag_system.py`

New `test_two_search_rounds_make_three_calls` in `TestFullChainNetworkMocked`: real
`AIGenerator` + `ToolManager` + tools, fake vector store, mocked Anthropic. A
comparison-style query drives two search rounds → asserts `answer == "synthesized"`,
`call_count == 3`, both searches ran, sources populated. The two existing full-chain tests
still pass without changes (`[tool_use, text]` scripts terminate in 2 calls as before).

---

## 4. Verification

```
uv run pytest -q          →  56 passed   (was 47: +8 test_ai_generator, +1 test_rag_system, 1 reworked)
```

**Live end-to-end** (`RAGSystem.query` against the real API + ChromaDB):

Query: *"What was covered in lesson 5 of the MCP course?"*

Before → `Let me get the specific content details for that lesson.`

After → a full breakdown: client-server connection, low-level MCP library code, listing
available tools, integrating with Claude, tool execution flow, and the physics-papers
example — plus the correct **Lesson 5** source link (now coming from the
`search_course_content` round that previously never ran).

---

## 5. Known limitations / follow-ups

Not addressed by this change (deliberately out of scope for a minimal fix):

1. **Sources across rounds.** `ToolManager.get_last_sources()` returns the first tool with a
   non-empty `last_sources`, and `rag_system.query` reads it once then calls
   `reset_sources()`. A 2-tool answer (e.g. outline + search) surfaces citations for only
   one of them. A real fix would aggregate sources per round in `ToolManager`.
2. **`max_tokens: 800`** is shared by the synthesis call. A synthesis folding two tool
   payloads into one answer could truncate on long comparisons.
3. **`CLAUDE.md`** query-flow step 3 still says *"Only one search round-trip is possible by
   design"* — now stale; worth updating.
