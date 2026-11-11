# ABOUTME: Tests for permissions.py — suspicious input detection,
# free-text field exemptions, and tool permission callbacks.

import pytest
from openeo_ai.sdk.permissions import check_suspicious_input


class TestSuspiciousInputDetection:
    """Test that dangerous patterns are blocked."""

    def test_path_traversal_blocked(self):
        result = check_suspicious_input({"path": "../../etc/passwd"})
        assert result is not None
        assert "Suspicious" in result

    def test_system_path_blocked(self):
        result = check_suspicious_input({"file": "/etc/shadow"})
        assert result is not None

    def test_xss_blocked(self):
        result = check_suspicious_input({"name": '<script>alert("xss")</script>'})
        assert result is not None

    def test_exec_injection_blocked(self):
        result = check_suspicious_input({"code": "exec('rm -rf /')"})
        assert result is not None

    def test_eval_injection_blocked(self):
        result = check_suspicious_input({"code": "eval('malicious')"})
        assert result is not None

    def test_import_injection_blocked(self):
        result = check_suspicious_input({"code": "__import__('os').system('ls')"})
        assert result is not None

    def test_shell_metachar_in_path_blocked(self):
        """Shell metacharacters in non-free-text fields are still blocked."""
        result = check_suspicious_input({"path": "file.txt; rm -rf /"})
        assert result is not None

    def test_clean_input_passes(self):
        result = check_suspicious_input({
            "collection": "sentinel-2-l2a",
            "bands": ["B04", "B08"],
        })
        assert result is None

    def test_nested_dict_scanned(self):
        result = check_suspicious_input({
            "args": {"nested": {"deep": "../../etc/passwd"}}
        })
        assert result is not None

    def test_list_values_scanned(self):
        result = check_suspicious_input({
            "items": ["safe", "../../etc/passwd"]
        })
        assert result is not None


class TestFreeTextExemptions:
    """Test that free-text fields are exempt from shell metachar pattern
    but still checked for other dangerous patterns."""

    def test_content_allows_dollar_sign(self):
        """Report content with dollar signs (e.g. costs) should pass."""
        result = check_suspicious_input({
            "content": "Total cost: $0.56 for this analysis run."
        })
        assert result is None

    def test_content_allows_backticks(self):
        """Markdown code in content field should pass."""
        result = check_suspicious_input({
            "content": "Use `filter_bbox` to clip the datacube."
        })
        assert result is None

    def test_content_allows_pipe(self):
        """Pipe characters in documentation content should pass."""
        result = check_suspicious_input({
            "content": "| Column A | Column B |"
        })
        assert result is None

    def test_content_allows_ampersand(self):
        result = check_suspicious_input({
            "content": "value > 0.5 && is_valid"
        })
        assert result is None

    def test_content_allows_semicolon(self):
        result = check_suspicious_input({
            "content": "Step 1: load; Step 2: process; Step 3: save"
        })
        assert result is None

    def test_pattern_allows_regex(self):
        """Grep pattern field should allow regex metacharacters."""
        result = check_suspicious_input({
            "pattern": "filter_spatial|mask_polygon|filter_bbox"
        })
        assert result is None

    def test_pattern_allows_dollar_anchor(self):
        result = check_suspicious_input({
            "pattern": "end_of_line$"
        })
        assert result is None

    def test_query_allows_special_chars(self):
        result = check_suspicious_input({
            "query": "what is $NDVI & how does it work?"
        })
        assert result is None

    def test_free_text_still_blocks_path_traversal(self):
        """Even free-text fields should block path traversal."""
        result = check_suspicious_input({
            "content": "Read the file at ../../etc/passwd"
        })
        assert result is not None

    def test_free_text_still_blocks_xss(self):
        result = check_suspicious_input({
            "content": '<script>alert("xss")</script>'
        })
        assert result is not None

    def test_free_text_still_blocks_exec(self):
        result = check_suspicious_input({
            "content": "try exec('import os')"
        })
        assert result is not None

    def test_non_free_text_field_still_blocked(self):
        """Dollar sign in a non-exempt field like 'path' is still blocked."""
        result = check_suspicious_input({
            "file_path": "/tmp/$USER/data.tif"
        })
        assert result is not None

    def test_description_field_exempt(self):
        result = check_suspicious_input({
            "description": "Analysis cost $1.20; 3 bands | 2 time steps"
        })
        assert result is None

    def test_report_field_exempt(self):
        result = check_suspicious_input({
            "report": "```bash\necho $HOME | grep user\n```"
        })
        assert result is None

    def test_case_insensitive_field_matching(self):
        """Field name matching should be case-insensitive."""
        result = check_suspicious_input({
            "Content": "Cost: $5.00"
        })
        assert result is None

    def test_mixed_fields(self):
        """Free-text and non-free-text fields in same dict."""
        # Dollar in content = OK, path traversal in id = blocked
        result = check_suspicious_input({
            "content": "Cost: $5",
            "id": "../../secret"
        })
        assert result is not None

    def test_nested_free_text_exempt(self):
        """Free-text key exemption works in nested dicts."""
        result = check_suspicious_input({
            "report_data": {
                "content": "Revenue: $100 | Expenses: $50"
            }
        })
        assert result is None
