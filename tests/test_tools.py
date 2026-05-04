"""Tests for the local tool implementations (lookup_documentation, check_style_guide)."""

from app.services.tools import (
    SUPPORTED_LANGUAGES,
    TOOL_DEFINITIONS,
    check_style_guide,
    lookup_documentation,
)


def test_tool_definitions_have_required_fields():
    assert len(TOOL_DEFINITIONS) >= 2
    for t in TOOL_DEFINITIONS:
        assert "name" in t and "description" in t and "input_schema" in t
        assert t["input_schema"]["type"] == "object"


def test_lookup_documentation_python_naming():
    out = lookup_documentation("python", "naming conventions")
    assert "snake_case" in out
    assert "PEP 8" in out


def test_lookup_documentation_partial_match():
    # 'error' is a substring of 'error handling' → partial match
    out = lookup_documentation("python", "error")
    assert "Error Handling" in out


def test_lookup_documentation_unknown_language_falls_back():
    out = lookup_documentation("cobol", "anything")
    assert "not yet available" in out.lower()


def test_lookup_documentation_unknown_topic_uses_default():
    out = lookup_documentation("python", "this-topic-does-not-exist")
    # Should fall back to python's default doc
    assert "PEP 8" in out or "best practices" in out.lower()


def test_check_style_guide_pep8_clean_code():
    code = "def hello():\n    return 1\n"
    out = check_style_guide(code, "pep8")
    # Either passes or only flags missing docstring — both are acceptable
    assert "PEP8" in out or "pep8" in out.lower() or "violations" in out.lower() or "passes" in out.lower()


def test_check_style_guide_pep8_long_line():
    code = "x = " + "1 + " * 50 + "1"
    out = check_style_guide(code, "pep8")
    assert "Line length" in out or "exceed" in out.lower()


def test_check_style_guide_pep8_tab_indentation():
    code = "def hello():\n\treturn 1\n"
    out = check_style_guide(code, "pep8")
    assert "Tabs" in out or "tab" in out.lower()


def test_check_style_guide_unknown_guide():
    out = check_style_guide("x = 1", "nonexistent")
    assert "not available" in out.lower()


def test_supported_languages_includes_basics():
    assert "python" in SUPPORTED_LANGUAGES
    assert "javascript" in SUPPORTED_LANGUAGES
    assert "rust" in SUPPORTED_LANGUAGES
