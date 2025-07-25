import pytest
import re
from scraper.utils import (
	normalize_years,
	remove_null_entries,
	sanitize_numeric_range,
	parse_range_arg,
	current_timestamp
)

# region Normalize Years Tests

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

def test_partial_failure_still_returns_valid_years(capsys):
	result = normalize_years(["20-19", "21"])
	captured = capsys.readouterr()
	assert "[Year Error]" in captured.out
	assert result == [2021]

def test_normalize_years_catches_generic_exceptions(capsys):
	with pytest.raises(SystemExit):
		normalize_years([None])  # triggers generic Exception path
	output = capsys.readouterr().out
	assert "[Year Error] Could not parse 'None':" in output
	assert "[Error] No valid years provided" in output

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

# region Parse Range Arg Tests

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

# region Sanitize Numeric Range Tests

def test_sanitize_removes_non_digits_and_hyphen():
	assert sanitize_numeric_range("$12,000 - $34,000 mi") == "12000-34000"

def test_sanitize_handles_extra_symbols():
	assert sanitize_numeric_range("~$15,000!") == "15000"

def test_sanitize_returns_clean_if_already_clean():
	assert sanitize_numeric_range("10000-20000") == "10000-20000"

def test_sanitize_with_no_hyphen():
	assert sanitize_numeric_range("$15000") == "15000"

# endregion

# region Remove Null Entries Tests

def test_remove_null_entries_with_none():
	assert remove_null_entries({"a": 1, "b": None, "c": 2}) == {"a": 1, "c": 2}

def test_remove_null_entries_all_valid():
	assert remove_null_entries({"x": 0, "y": "", "z": []}) == {"x": 0, "y": "", "z": []}

def test_remove_null_entries_all_none():
	assert remove_null_entries({"a": None, "b": None}) == {}

# endregion

# region Current Timestamp Tests

def test_current_timestamp_format():
	ts = current_timestamp()
	assert re.match(r"\d{8}_\d{6}", ts)  # Should match YYYYMMDD_HHMMSS

def test_current_timestamp_is_string():
	assert isinstance(current_timestamp(), str)

# endregion
