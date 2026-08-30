from abc import ABC, abstractmethod
from typing import Any, Dict, Optional

from vector_store import SearchResults, VectorStore


class Tool(ABC):
    """Abstract base class for all tools"""

    @abstractmethod
    def get_tool_definition(self) -> Dict[str, Any]:
        """Return Anthropic tool definition for this tool"""
        pass

    @abstractmethod
    def execute(self, **kwargs) -> str:
        """Execute the tool with given parameters"""
        pass


class CourseSearchTool(Tool):
    """Tool for searching course content with semantic course name matching"""

    def __init__(self, vector_store: VectorStore):
        self.store = vector_store
        self.last_sources = []  # Track sources from last search

    def get_tool_definition(self) -> Dict[str, Any]:
        """Return Anthropic tool definition for this tool"""
        return {
            "name": "search_course_content",
            "description": "Search course materials with smart course name matching and lesson filtering",
            "input_schema": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "What to search for in the course content",
                    },
                    "course_name": {
                        "type": "string",
                        "description": "Course title (partial matches work, e.g. 'MCP', 'Introduction')",
                    },
                    "lesson_number": {
                        "type": "integer",
                        "description": "Specific lesson number to search within (e.g. 1, 2, 3)",
                    },
                },
                "required": ["query"],
            },
        }

    def execute(
        self,
        query: str,
        course_name: Optional[str] = None,
        lesson_number: Optional[int] = None,
    ) -> str:
        """
        Execute the search tool with given parameters.

        Args:
            query: What to search for
            course_name: Optional course filter
            lesson_number: Optional lesson filter

        Returns:
            Formatted search results or error message
        """

        # Use the vector store's unified search interface
        results = self.store.search(
            query=query, course_name=course_name, lesson_number=lesson_number
        )

        # Handle errors
        if results.error:
            return results.error

        # Handle empty results
        if results.is_empty():
            filter_info = ""
            if course_name:
                filter_info += f" in course '{course_name}'"
            if lesson_number:
                filter_info += f" in lesson {lesson_number}"
            return f"No relevant content found{filter_info}."

        # Format and return results
        return self._format_results(results)

    def _format_results(self, results: SearchResults) -> str:
        """Format search results with course and lesson context"""
        formatted = []
        sources = []  # Track sources for the UI
        link_cache: Dict[tuple, Optional[str]] = {}  # Avoid repeat catalog lookups

        for doc, meta in zip(results.documents, results.metadata):
            course_title = meta.get("course_title", "unknown")
            lesson_num = meta.get("lesson_number")

            # Build context header
            header = f"[{course_title}"
            if lesson_num is not None:
                header += f" - Lesson {lesson_num}"
            header += "]"

            # Human-readable label for the source citation
            label = course_title
            if lesson_num is not None:
                label += f" - Lesson {lesson_num}"

            # Resolve the lesson's video link from the course_catalog collection
            lesson_link = None
            if lesson_num is not None:
                key = (course_title, lesson_num)
                if key not in link_cache:
                    link_cache[key] = self.store.get_lesson_link(
                        course_title, lesson_num
                    )
                lesson_link = link_cache[key]

            # Embed the link invisibly: the label stays visible, the URL does not.
            # The frontend renders sources as HTML, so an <a> tag becomes a click target.
            if lesson_link and lesson_link.startswith(("http://", "https://")):
                sources.append(
                    f'<a href="{lesson_link}" target="_blank" '
                    f'rel="noopener noreferrer" class="source-link">{label}</a>'
                )
            else:
                sources.append(label)

            formatted.append(f"{header}\n{doc}")

        # Store sources for retrieval, de-duplicated while preserving order
        # (multiple chunks from the same lesson would otherwise repeat the citation)
        seen = set()
        self.last_sources = [s for s in sources if not (s in seen or seen.add(s))]

        return "\n\n".join(formatted)


class CourseOutlineTool(Tool):
    """Tool for retrieving a course outline: title, link, and full lesson list"""

    def __init__(self, vector_store: VectorStore):
        self.store = vector_store
        self.last_sources = []  # Track sources from last lookup

    def get_tool_definition(self) -> Dict[str, Any]:
        """Return Anthropic tool definition for this tool"""
        return {
            "name": "get_course_outline",
            "description": (
                "Get the full outline of a course: its title, course link, and "
                "the complete list of lessons (each lesson's number and title). "
                "Use for questions about a course's structure, syllabus, lesson "
                "list, or overview."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "course_title": {
                        "type": "string",
                        "description": "Course title (partial matches work, e.g. 'MCP', 'Introduction')",
                    }
                },
                "required": ["course_title"],
            },
        }

    def execute(self, course_title: str) -> str:
        """
        Execute the outline lookup.

        Args:
            course_title: Course title (partial matches allowed)

        Returns:
            Formatted course outline or an error message
        """
        outline = self.store.get_course_outline(course_title)
        if not outline:
            return f"No course found matching '{course_title}'."

        lines = [f"Course: {outline['course_title']}"]
        if outline.get("course_link"):
            lines.append(f"Course link: {outline['course_link']}")

        lessons = outline.get("lessons") or []
        if lessons:
            lines.append(f"Lessons ({len(lessons)}):")
            for lesson in sorted(lessons, key=lambda x: x.get("lesson_number", 0)):
                lines.append(
                    f"  Lesson {lesson.get('lesson_number')}: {lesson.get('lesson_title')}"
                )
        else:
            lines.append("No lessons found for this course.")

        # Surface the course as a clickable source in the UI, reusing the
        # invisible-link pattern from CourseSearchTool._format_results.
        link = outline.get("course_link")
        if link and link.startswith(("http://", "https://")):
            self.last_sources = [
                f'<a href="{link}" target="_blank" '
                f'rel="noopener noreferrer" class="source-link">{outline["course_title"]}</a>'
            ]
        else:
            self.last_sources = [outline["course_title"]]

        return "\n".join(lines)


class ToolManager:
    """Manages available tools for the AI"""

    def __init__(self):
        self.tools = {}

    def register_tool(self, tool: Tool):
        """Register any tool that implements the Tool interface"""
        tool_def = tool.get_tool_definition()
        tool_name = tool_def.get("name")
        if not tool_name:
            raise ValueError("Tool must have a 'name' in its definition")
        self.tools[tool_name] = tool

    def get_tool_definitions(self) -> list:
        """Get all tool definitions for Anthropic tool calling"""
        return [tool.get_tool_definition() for tool in self.tools.values()]

    def execute_tool(self, tool_name: str, **kwargs) -> str:
        """Execute a tool by name with given parameters"""
        if tool_name not in self.tools:
            return f"Tool '{tool_name}' not found"

        return self.tools[tool_name].execute(**kwargs)

    def get_last_sources(self) -> list:
        """Get sources from the last search operation"""
        # Check all tools for last_sources attribute
        for tool in self.tools.values():
            if hasattr(tool, "last_sources") and tool.last_sources:
                return tool.last_sources
        return []

    def reset_sources(self):
        """Reset sources from all tools that track sources"""
        for tool in self.tools.values():
            if hasattr(tool, "last_sources"):
                tool.last_sources = []
