import logging
from unittest.mock import AsyncMock, MagicMock
import pytest
import re
from scraper.utils import *

#region Normalize Years Tests

def test_four_digit_year():
	assert normalize_years(["2021"]) == [2021]

def test_two_digit_shorthand_post_2000():
	assert normalize_years(["20"]) == [2020]

def test_two_digit_shorthand_pre_2000():
	assert normalize_years(["99"]) == [1999]

def test_year_range():
	assert normalize_years(["2020-2022"]) == [2020, 2021, 2022]

def test_mixed_shorthand_and_full_years():
	assert normalize_years(["20", "2021", "22"]) == [2020, 2021, 2022]

def test_shorthand_range():
	assert normalize_years(["20-22"]) == [2020, 2021, 2022]

def test_reversed_range_skipped():
	assert normalize_years(["2022-2020", "2023"]) == [2023]

def test_partial_failure_still_returns_valid_years(caplog):
	with caplog.at_level(logging.ERROR):
		result = normalize_years(["20-19", "21"])
	assert any("[Year Error]" in msg for msg in caplog.messages)
	assert result == [2021]

def test_normalize_years_catches_generic_exceptions(caplog):
	with caplog.at_level(logging.ERROR), pytest.raises(SystemExit):
		normalize_years([None])
	assert any("[Year Error] Could not parse 'None':" in msg for msg in caplog.messages)
	assert any("No valid years provided" in msg for msg in caplog.messages)
def test_invalid_non_numeric_year_exits():
	with pytest.raises(SystemExit):
		normalize_years(["abcd"])

def test_invalid_empty_string_exits():
	with pytest.raises(SystemExit):
		normalize_years([""])

def test_all_reversed_ranges_exit():
	with pytest.raises(SystemExit):
		normalize_years(["2022-2020", "24-22"])
		
def test_edge_years():
    assert normalize_years(["50" , "49"]) == [ 1950, 2049]
	
# endregion

#region Parse Range Arg Tests

def test_valid_range():
	assert parse_range_arg("price", "10000-60000") == (10000, 60000)

def test_valid_single_value():
	assert parse_range_arg("miles", "5000") == (5000, None)

def test_missing_lower_bound():
	assert parse_range_arg("price", "-40000") == (None, 40000)

def test_missing_upper_bound():
	assert parse_range_arg("price", "10000-") == (10000, None)

def test_invalid_non_numeric_exits():
	with pytest.raises(SystemExit):
		parse_range_arg("miles", "abc-def")

def test_too_many_hyphens_exits():
	with pytest.raises(SystemExit):
		parse_range_arg("price", "10000-20000-30000")

def test_reversed_range_exits():
	with pytest.raises(SystemExit):
		parse_range_arg("price", "60000-10000")

# endregion

#region Sanitize Numeric Range Tests

def test_sanitize_removes_non_digits_and_hyphen():
	assert sanitize_numeric_range("$12,000 - $34,000 mi") == "12000-34000"

def test_sanitize_handles_extra_symbols():
	assert sanitize_numeric_range("~$15,000!") == "15000"

def test_sanitize_returns_clean_if_already_clean():
	assert sanitize_numeric_range("10000-20000") == "10000-20000"

def test_sanitize_with_no_hyphen():
	assert sanitize_numeric_range("$15000") == "15000"

# endregion

#region Remove Null Entries Tests

def test_remove_null_entries_with_none():
	assert remove_null_entries({"a": 1, "b": None, "c": 2}) == {"a": 1, "c": 2}

def test_remove_null_entries_all_valid():
	assert remove_null_entries({"x": 0, "y": "", "z": []}) == {"x": 0, "y": "", "z": []}

def test_remove_null_entries_all_none():
	assert remove_null_entries({"a": None, "b": None}) == {}

# endregion

#region Current Timestamp Tests

def test_current_timestamp_format():
	ts = current_timestamp()
	assert re.match(r"\d{8}_\d{6}", ts)  # Should match YYYYMMDD_HHMMSS

def test_current_timestamp_is_string():
	assert isinstance(current_timestamp(), str)

# endregion

#region Safe Text Tests

async def test_safe_text_element_found():
	card = MagicMock()
	element = AsyncMock()
	element.inner_text.return_value = "Test Title"
	card.query_selector = AsyncMock(return_value=element)
	metadata = {"warnings": []}

	result = await safe_text(card, "h2", "title #1", metadata)

	assert result == "Test Title"
	assert metadata["warnings"] == []

async def test_safe_text_element_missing():
	card = MagicMock()
	card.query_selector = AsyncMock(return_value=None)
	metadata = {"warnings": []}

	result = await safe_text(card, "h2", "title #1", metadata)

	assert result == "N/A"
	assert metadata["warnings"] == []

async def test_safe_text_selector_raises():
	card = MagicMock()
	card.query_selector = AsyncMock(side_effect=Exception("Boom!"))
	metadata = {"warnings": []}

	result = await safe_text(card, "h2", "title #1", metadata)

	assert result == "N/A"
	assert any("title #1" in msg for msg in metadata["warnings"])

#endregion

#region Safe Inner Test

async def test_safe_inner_text_success():
	element = AsyncMock()
	element.inner_text.return_value = "  36,000 mi  "

	metadata = {"warnings": []}
	result = await safe_inner_text(element, "miles_total", 1, metadata)

	assert result == "36,000 mi"
	assert metadata["warnings"] == []

async def test_safe_inner_text_failure():
	element = AsyncMock()
	element.inner_text.side_effect = Exception("boom")

	metadata = {"warnings": []}
	result = await safe_inner_text(element, "miles_total", 1, metadata)

	assert result is None
	assert any("Listing #1: Failed to read miles_total: boom" in w for w in metadata["warnings"])

#endregion

#region Get URL Tests

async def test_get_url_success():
	page = MagicMock()
	link = AsyncMock()
	link.get_attribute.return_value = "https://example.com/doc"
	page.query_selector = AsyncMock(return_value=link)

	metadata = {"warnings": []}
	result = await get_url(page, "#some-selector", 1, metadata)

	assert result == "https://example.com/doc"
	assert metadata["warnings"] == []

async def test_get_url_missing_element():
	page = MagicMock()
	page.query_selector = AsyncMock(return_value=None)

	metadata = {"warnings": []}
	result = await get_url(page, "#missing", 2, metadata)

	assert result == "Unavailable"

async def test_get_url_timeout():
	page = MagicMock()
	page.query_selector = AsyncMock(side_effect=TimeoutError())

	metadata = {"warnings": []}
	result = await get_url(page, "#timeout", 3, metadata)

	assert result == "Unavailable"
	assert metadata["warnings"] == [
		"[Info] Additional document timed out for listing #3. Cookies out of date/not set or subscription inactive"
	]

async def test_get_url_exception(monkeypatch):
	page = MagicMock()
	page.query_selector = AsyncMock(side_effect=Exception("Boom"))
	metadata = {"warnings": []}
	logged = []

	monkeypatch.setattr(logging, "error", lambda msg: logged.append(msg))

	result = await get_url(page, "#broken", 4, metadata)

	assert result == "Unavailable"
	assert "Boom" in logged[0]

#endregion
