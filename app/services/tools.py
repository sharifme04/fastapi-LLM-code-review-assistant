"""Tool implementations for the code review assistant.

Provides mock documentation lookup and style guide checking tools.
These are called by Claude during the review process via the manual
tool-calling loop in reviewer.py.
"""

import logging
import re

logger = logging.getLogger("code_review_assistant")

# Supported languages for review
SUPPORTED_LANGUAGES = {
    "python", "javascript", "typescript", "java", "go", "rust",
    "c", "cpp", "csharp", "ruby", "php", "swift", "kotlin",
}

# --- Tool Definitions (sent to Claude) ---

TOOL_DEFINITIONS = [
    {
        "name": "lookup_documentation",
        "description": (
            "Look up programming language documentation, best practices, "
            "or style guide references for a specific topic. Use this when "
            "you need to cite specific language rules, PEP standards, or "
            "official documentation to support your review feedback."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "language": {
                    "type": "string",
                    "description": "The programming language (e.g. 'python', 'javascript')",
                },
                "topic": {
                    "type": "string",
                    "description": "The topic to look up (e.g. 'naming conventions', 'error handling', 'type hints')",
                },
            },
            "required": ["language", "topic"],
        },
    },
    {
        "name": "check_style_guide",
        "description": (
            "Check a code snippet against a specific style guide and return "
            "any violations found. Use this to verify specific code patterns "
            "against established style standards."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "code_snippet": {
                    "type": "string",
                    "description": "The code snippet to check",
                },
                "guide_name": {
                    "type": "string",
                    "description": "The style guide to check against (e.g. 'pep8', 'google', 'airbnb')",
                },
            },
            "required": ["code_snippet", "guide_name"],
        },
    },
]

# --- Documentation Database ---

DOCUMENTATION_DB: dict[str, dict[str, str]] = {
    "python": {
        "naming conventions": (
            "PEP 8 — Naming Conventions:\n"
            "• Functions and variables: snake_case (e.g., my_function)\n"
            "• Classes: PascalCase (e.g., MyClass)\n"
            "• Constants: UPPER_SNAKE_CASE (e.g., MAX_RETRIES)\n"
            "• Private attributes: single leading underscore (_private)\n"
            "• Name-mangled attributes: double leading underscore (__mangled)\n"
            "• Avoid: single-character names except for counters (i, j, k)\n"
            "Reference: https://peps.python.org/pep-0008/#naming-conventions"
        ),
        "error handling": (
            "Python Error Handling Best Practices:\n"
            "• Catch specific exceptions, never bare 'except:'\n"
            "• Use 'except Exception as e:' at minimum\n"
            "• Use 'raise ... from e' to chain exceptions\n"
            "• Define custom exceptions for domain-specific errors\n"
            "• Use context managers (with statements) for resource cleanup\n"
            "• Log exceptions with full traceback server-side\n"
            "• Never silently swallow exceptions\n"
            "Reference: https://peps.python.org/pep-0008/#programming-recommendations"
        ),
        "type hints": (
            "PEP 484 / PEP 604 — Type Hints:\n"
            "• Use type hints for all function signatures\n"
            "• Return types: def foo() -> str:\n"
            "• Optional: Optional[str] or str | None (Python 3.10+)\n"
            "• Collections: list[int], dict[str, Any], tuple[int, ...]\n"
            "• Union types: int | str (Python 3.10+) or Union[int, str]\n"
            "• Use TypeVar for generic functions\n"
            "• Use Protocol for structural subtyping\n"
            "Reference: https://peps.python.org/pep-0484/"
        ),
        "docstrings": (
            "PEP 257 — Docstring Conventions:\n"
            "• All public modules, functions, classes, and methods should have docstrings\n"
            "• Use triple double-quotes (\"\"\"docstring\"\"\")\n"
            "• First line: brief summary, fits on one line\n"
            "• Blank line after summary if multi-line\n"
            "• Document Args, Returns, Raises sections\n"
            "• Use Google or NumPy style for consistency\n"
            "Reference: https://peps.python.org/pep-0257/"
        ),
        "imports": (
            "PEP 8 — Import Guidelines:\n"
            "• Imports should be on separate lines\n"
            "• Group order: stdlib → third-party → local\n"
            "• Use absolute imports over relative imports\n"
            "• Avoid wildcard imports (from module import *)\n"
            "• Use 'from __future__ import annotations' for forward references\n"
            "Reference: https://peps.python.org/pep-0008/#imports"
        ),
        "async": (
            "Python Async Best Practices:\n"
            "• Use 'async def' for coroutines, 'await' for calling them\n"
            "• Use asyncio.gather() for parallel execution\n"
            "• Use asyncio.create_task() for background tasks\n"
            "• Avoid blocking calls in async functions (use run_in_executor)\n"
            "• Use async context managers (async with) for resources\n"
            "• Use async generators (async for) for streaming\n"
            "Reference: https://docs.python.org/3/library/asyncio.html"
        ),
        "default": (
            "Python General Best Practices (PEP 8):\n"
            "• Maximum line length: 79 characters (99 for code, 72 for docstrings)\n"
            "• Use 4 spaces for indentation, never tabs\n"
            "• Surround top-level definitions with two blank lines\n"
            "• Use spaces around operators and after commas\n"
            "• Use meaningful variable names\n"
            "Reference: https://peps.python.org/pep-0008/"
        ),
    },
    "javascript": {
        "naming conventions": (
            "JavaScript Naming Conventions:\n"
            "• Variables and functions: camelCase (e.g., myFunction)\n"
            "• Classes: PascalCase (e.g., MyClass)\n"
            "• Constants: UPPER_SNAKE_CASE (e.g., MAX_RETRIES)\n"
            "• Private fields: prefix with # (e.g., #privateField)\n"
            "• Boolean variables: prefix with is/has/can (e.g., isValid)\n"
            "• Use descriptive names — avoid abbreviations\n"
            "Reference: https://developer.mozilla.org/en-US/docs/MDN/Guidelines/Code_guidelines/JavaScript"
        ),
        "error handling": (
            "JavaScript Error Handling Best Practices:\n"
            "• Use try/catch/finally for synchronous error handling\n"
            "• Use .catch() or try/catch with async/await for promises\n"
            "• Create custom error classes extending Error\n"
            "• Always include error messages and stack traces\n"
            "• Use Error.cause for error chaining (ES2022)\n"
            "• Never catch errors silently\n"
            "Reference: https://developer.mozilla.org/en-US/docs/Web/JavaScript/Reference/Statements/try...catch"
        ),
        "async": (
            "JavaScript Async Patterns:\n"
            "• Prefer async/await over raw Promises\n"
            "• Use Promise.all() for parallel execution\n"
            "• Use Promise.allSettled() when all results matter\n"
            "• Avoid callback hell — use async/await\n"
            "• Handle unhandled promise rejections\n"
            "• Use AbortController for cancellation\n"
            "Reference: https://developer.mozilla.org/en-US/docs/Web/JavaScript/Reference/Statements/async_function"
        ),
        "default": (
            "JavaScript Best Practices:\n"
            "• Use 'const' by default, 'let' when reassignment needed, never 'var'\n"
            "• Use strict equality (=== and !==)\n"
            "• Use template literals for string interpolation\n"
            "• Use destructuring for object/array access\n"
            "• Use optional chaining (?.) and nullish coalescing (??)\n"
            "• Use arrow functions for callbacks\n"
            "Reference: https://developer.mozilla.org/en-US/docs/Web/JavaScript/Guide"
        ),
    },
    "typescript": {
        "type safety": (
            "TypeScript Type Safety Best Practices:\n"
            "• Avoid 'any' — use 'unknown' when type is uncertain\n"
            "• Use strict mode in tsconfig.json\n"
            "• Prefer interfaces over type aliases for object shapes\n"
            "• Use discriminated unions for state machines\n"
            "• Use 'as const' for literal types\n"
            "• Use generic constraints (T extends SomeType)\n"
            "Reference: https://www.typescriptlang.org/docs/handbook/2/types-from-types.html"
        ),
        "default": (
            "TypeScript Best Practices:\n"
            "• Enable strict mode in tsconfig.json\n"
            "• Use interfaces for object shapes, types for unions/intersections\n"
            "• Avoid 'any', prefer 'unknown' or proper typing\n"
            "• Use enums sparingly — prefer const objects or union types\n"
            "• Use utility types (Partial, Required, Pick, Omit)\n"
            "Reference: https://www.typescriptlang.org/docs/"
        ),
    },
}

# --- Style Guide Rules ---

STYLE_RULES: dict[str, list[dict[str, str]]] = {
    "pep8": [
        {
            "rule": "Line length should not exceed 79 characters (99 for code)",
            "check": "line_length",
            "max_length": "99",
        },
        {
            "rule": "Use 4 spaces for indentation, never tabs",
            "check": "indentation",
        },
        {
            "rule": "Function and variable names should use snake_case",
            "check": "naming_snake_case",
        },
        {
            "rule": "Class names should use PascalCase",
            "check": "naming_pascal_case",
        },
        {
            "rule": "Surround top-level definitions with two blank lines",
            "check": "blank_lines",
        },
        {
            "rule": "Add docstrings to all public functions and classes",
            "check": "docstrings",
        },
    ],
    "google": [
        {
            "rule": "Maximum line length: 80 characters",
            "check": "line_length",
            "max_length": "80",
        },
        {
            "rule": "Use Google-style docstrings with Args/Returns/Raises sections",
            "check": "docstrings",
        },
        {
            "rule": "Use type hints for all function parameters and return values",
            "check": "type_hints",
        },
    ],
    "airbnb": [
        {
            "rule": "Use const by default, let when reassignment needed, never var",
            "check": "const_let",
        },
        {
            "rule": "Use arrow functions for anonymous functions",
            "check": "arrow_functions",
        },
        {
            "rule": "Use template literals instead of string concatenation",
            "check": "template_literals",
        },
        {
            "rule": "Use strict equality (=== and !==)",
            "check": "strict_equality",
        },
    ],
}


def lookup_documentation(language: str, topic: str) -> str:
    """Look up documentation for a given language and topic.

    Args:
        language: Programming language (e.g., 'python', 'javascript').
        topic: Topic to look up (e.g., 'naming conventions', 'error handling').

    Returns:
        Documentation text for the given topic, or a fallback message.
    """
    lang = language.lower().strip()
    topic_lower = topic.lower().strip()

    if lang not in DOCUMENTATION_DB:
        return (
            f"Documentation for '{language}' is not yet available in the database. "
            f"Supported languages: {', '.join(sorted(DOCUMENTATION_DB.keys()))}. "
            f"Proceeding with general best practices."
        )

    lang_docs = DOCUMENTATION_DB[lang]

    # Try exact match first
    if topic_lower in lang_docs:
        logger.info("Documentation lookup hit", extra={"language": lang, "topic": topic_lower})
        return lang_docs[topic_lower]

    # Try partial match
    for key, doc in lang_docs.items():
        if key == "default":
            continue
        if topic_lower in key or key in topic_lower:
            logger.info(
                "Documentation lookup partial match",
                extra={"language": lang, "topic": topic_lower, "matched": key},
            )
            return doc

    # Fallback to default
    if "default" in lang_docs:
        return lang_docs["default"]

    return f"No specific documentation found for '{topic}' in {language}. Using general review guidelines."


def check_style_guide(code_snippet: str, guide_name: str) -> str:
    """Check a code snippet against a style guide and return violations.

    Performs simple static analysis checks based on the selected style guide.

    Args:
        code_snippet: Code to check.
        guide_name: Style guide name (e.g., 'pep8', 'google', 'airbnb').

    Returns:
        String listing violations found, or a clean report.
    """
    guide = guide_name.lower().strip()
    lines = code_snippet.split("\n")
    violations: list[str] = []

    if guide not in STYLE_RULES:
        return (
            f"Style guide '{guide_name}' is not available. "
            f"Available guides: {', '.join(sorted(STYLE_RULES.keys()))}. "
            f"Proceeding with general review."
        )

    rules = STYLE_RULES[guide]

    for rule_def in rules:
        check = rule_def["check"]

        if check == "line_length":
            max_len = int(rule_def.get("max_length", "80"))
            long_lines = [
                (i + 1, len(line))
                for i, line in enumerate(lines)
                if len(line) > max_len
            ]
            if long_lines:
                examples = long_lines[:3]
                violations.append(
                    f"⚠ Line length: {len(long_lines)} line(s) exceed {max_len} chars. "
                    f"Examples: {', '.join(f'line {ln} ({length} chars)' for ln, length in examples)}"
                )

        elif check == "indentation":
            tab_lines = [i + 1 for i, line in enumerate(lines) if "\t" in line]
            if tab_lines:
                violations.append(
                    f"⚠ Tabs detected on line(s): {', '.join(str(l) for l in tab_lines[:5])}. "
                    f"Use 4 spaces instead."
                )

        elif check == "naming_snake_case":
            camel_funcs = []
            for i, line in enumerate(lines):
                match = re.match(r"\s*def\s+(\w+)", line)
                if match:
                    name = match.group(1)
                    if name != name.lower() and name != "__init__" and not name.startswith("_"):
                        camel_funcs.append((i + 1, name))
            if camel_funcs:
                violations.append(
                    f"⚠ Non-snake_case function names: "
                    f"{', '.join(f'{name} (line {ln})' for ln, name in camel_funcs[:3])}"
                )

        elif check == "docstrings":
            func_pattern = re.compile(r"^\s*(def|class)\s+\w+")
            for i, line in enumerate(lines):
                if func_pattern.match(line):
                    # Check if next non-empty line is a docstring
                    has_docstring = False
                    for j in range(i + 1, min(i + 3, len(lines))):
                        stripped = lines[j].strip()
                        if stripped.startswith('"""') or stripped.startswith("'''"):
                            has_docstring = True
                            break
                        if stripped and not stripped.startswith("#"):
                            break
                    if not has_docstring:
                        # Get the name
                        match = re.match(r"^\s*(def|class)\s+(\w+)", line)
                        if match:
                            violations.append(
                                f"⚠ Missing docstring for {match.group(1)} '{match.group(2)}' "
                                f"(line {i + 1})"
                            )

        elif check == "type_hints":
            for i, line in enumerate(lines):
                match = re.match(r"\s*def\s+(\w+)\s*\(", line)
                if match and "->" not in line:
                    violations.append(
                        f"⚠ Missing return type hint for function '{match.group(1)}' (line {i + 1})"
                    )

        elif check == "strict_equality":
            for i, line in enumerate(lines):
                if "==" in line and "===" not in line:
                    # Exclude Python-style comparisons in comments
                    stripped = line.strip()
                    if not stripped.startswith("//") and not stripped.startswith("#"):
                        violations.append(
                            f"⚠ Use strict equality (===) instead of loose equality (==) "
                            f"(line {i + 1})"
                        )

    if not violations:
        return f"✅ Code passes all {guide.upper()} style checks. No violations found."

    header = f"Style Guide Check ({guide.upper()}) — {len(violations)} issue(s) found:\n\n"
    return header + "\n".join(violations)
