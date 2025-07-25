import argparse
import json
import pytest
from itertools import chain, repeat
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, mock_open, patch
from scraper.constants import HREF_ELEMENT, MAX_LISTINGS, REMAPPING_RULES
from scraper.scraper import (
	auto_scroll_to_load_all,
    build_metadata, 
    build_query_params,
	capped_max_listings,
	extract_listings,
    extract_mileage_and_listed,
    extract_numbers_from_sidebar,
	fetch_page,
	save_results,
	safe_text,
	safe_vin,
	scrape
)

#region Capped Max Listings Tests

def test_capped_max_listings_within_limit():
	assert capped_max_listings("300") == 300

def test_capped_max_listings_exceeds_limit():
	with pytest.raises(argparse.ArgumentTypeError) as excinfo:
		capped_max_listings(str(MAX_LISTINGS + 1))
	assert str(excinfo.value) == f"Maximum allowed listings is {MAX_LISTINGS}."

def test_capped_max_listings_invalid_input():
	with pytest.raises(ValueError):
		capped_max_listings("not-a-number")

#endregion

# region Build Metadata Tests

def test_build_metadata_missing_make():
	args = SimpleNamespace(make=None, model="RAV4", trim=None, year=None,
		min_miles=None, max_miles=None, miles=None,
		min_price=None, max_price=None, price=None,
		condition=None, max__listings=50, sort="Newest")
	with pytest.raises(SystemExit):
		build_metadata(args)

def test_build_metadata_missing_model():
	args = SimpleNamespace(make="Toyota", model=None, trim=None, year=None,
		min_miles=None, max_miles=None, miles=None,
		min_price=None, max_price=None, price=None,
		condition=None, max__listings=50, sort="Newest")
	with pytest.raises(SystemExit):
		build_metadata(args)

def test_build_metadata_minimal():
	args = SimpleNamespace(
		make="Ford", model="Bronco", trim=None, year=None,
		min_miles=None, max_miles=None, miles=None,
		min_price=None, max_price=None, price=None,
		condition=None, max__listings=50, sort="Newest"
	)
	metadata = build_metadata(args)
	assert metadata["vehicle"]["make"] == "Ford"
	assert metadata["vehicle"]["model"] == "Bronco"
	assert metadata["vehicle"]["year"] == []
	assert "timestamp" in metadata["runtime"]
	assert isinstance(metadata["filters"], dict)
	assert metadata["filters"]["sort"] == "Newest"

def test_build_metadata_with_years():
	args = SimpleNamespace(
		make="Subaru", model="Outback", trim=["Wilderness"], year=["22", "2023-2024"],
		min_miles=10000, max_miles=50000, miles=None,
		min_price=None, max_price=None, price=None,
		condition=["Used"], max__listings=50, sort="PriceHigh"
	)
	metadata = build_metadata(args)
	assert metadata["vehicle"]["year"] == [2022, 2023, 2024]
	assert metadata["filters"]["min_miles"] == 10000
	assert metadata["filters"]["max_miles"] == 50000
	assert metadata["filters"]["condition"] == ["Used"]

# endregion

# region Build Query Params Tests

def test_query_params_converts_bools_and_lists():
	args = SimpleNamespace(
		make="Toyota", model="RAV4", trim=["XLE", "Adventure"], year=["2021"],
		min_miles=None, max_miles=None, miles=None,
		min_price=None, max_price=None, price=None,
		condition=["used", "certified"], max__listings=50, sort="Newest"
	)
	metadata = build_metadata(args)
	query = build_query_params(args, metadata)

	# Confirm list joined correctly
	assert "trim" in query
	assert query["trim"] == "XLE,Adventure"
	assert query["car_type"] == "used,certified"
	assert query["sort"] == "Newest"

def test_query_params_overrides_miles_and_price():
	args = SimpleNamespace(
		make="Toyota", model="RAV4", trim=None, year=["2021"],
		min_miles=10000, max_miles=60000, miles="15000-30000",
		min_price=20000, max_price=40000, price="25000-35000",
		condition=["Used"], max__listings=50, sort="Newest"
	)
	metadata = build_metadata(args)
	query = build_query_params(args, metadata)

	# Ensure range override took precedence
	assert query["miles_min"] == 15000
	assert query["miles_max"] == 30000
	assert query["price_min"] == 25000
	assert query["price_max"] == 35000

def test_query_params_filters_out_empty_keys():
	args = SimpleNamespace(
		make="Toyota", model="RAV4", trim=None, year=None,
		min_miles=None, max_miles=None, miles=None,
		min_price=None, max_price=None, price=None,
		condition=None, max__listings=50, sort="Newest"
	)
	metadata = build_metadata(args)
	query = build_query_params(args, metadata)

	# These keys are excluded from query string
	for key in ["trim", "year", "min_miles", "max_miles", "miles", "min_price", "max_price", "price", "condition"]:
		assert key not in query

def test_query_params_normalizes_sort_key():
	args = SimpleNamespace(
		make="Subaru",
		model="Outback",
		trim=["Wilderness"],
		year=["2023"],
		sort="Lowest Price",
		condition=None,
		max_listings=None,
		miles=None, min_miles=None, max_miles=None,
		price=None, min_price=None, max_price=None
	)

	metadata = {
		"vehicle": {},
		"filters": {},
		"site_info": {},
		"runtime": {},
		"warnings": []
	}

	query_params = build_query_params(args, metadata)
	assert query_params["sort"] == "cheapest"  # Should map to "Lowest Price"

def test_query_params_invalid_argument_type_logs_warning(caplog):
	args = SimpleNamespace(
		make="Subaru",
		model="Outback",
		trim=["Wilderness"],
		year=["2023"],
		sort="Newest",
		condition=None,
		max_listings=None,
		miles=None, min_miles=None, max_miles=None,
		price=None, min_price=None, max_price=None,
		broken_field="value"
	)

	metadata = {
		"vehicle": {},
		"filters": {},
		"site_info": {},
		"runtime": {},
		"warnings": []
	}

	REMAPPING_RULES["broken_field"] = lambda v: (_ for _ in ()).throw(ValueError("boom"))
	build_query_params(args, metadata)

	assert any("Failed to process argument 'broken_field'" in w for w in metadata["warnings"])

# endregion

#region Save Results Tests

def test_save_results_writes_json_file(tmp_path):
	# Arrange
	listings = [
		{"title": "Test Car", "price": "$25,000", "mileage": "10,000 mi", "listed": "Listed 2 days ago", "location": "Denver, CO", "vin": "123ABC"}
	]
	metadata = {
		"make": "Honda",
		"model": "Civic",
		"trim": ["EX"],
		"year": [2022],
		"timestamp": "fake_timestamp",
		"filters": {"sort": "Newest"},
		"warnings": []
	}
	args = SimpleNamespace(make="Honda", model="Civic")

	filename_prefix = f"{args.make}_{args.model}_listings_"
	save_results(listings, metadata, args, output_dir=tmp_path)

	# Find the generated file in the working directory
	matching_files = list(tmp_path.glob(f"{filename_prefix}*.json"))
	print(f"{tmp_path}")
	assert matching_files, "Expected a JSON file to be created"
	with open(matching_files[0], "r", encoding="utf-8") as f:
		data = json.load(f)
		assert data["metadata"]["make"] == "Honda"
		assert data["listings"][0]["vin"] == "123ABC"
		
def test_save_results_fails_on_bad_path():

	args = SimpleNamespace(make="Test", model="Fail")
	metadata = {"make": "Test", "model": "Fail", "year": [], "trim": None, "timestamp": "now", "filters": {}, "warnings": []}
	listings = []

	with pytest.raises(Exception):
		save_results(listings, metadata, args, output_dir="/invalid/path")

#endregion

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

#region Safe VIN Tests

async def test_safe_vin_valid_href():
	card = MagicMock()
	card.get_attribute = AsyncMock(return_value="/search/listings/123ABC?foo=bar")
	metadata = {"warnings": []}

	result = await safe_vin(card, 0, metadata)

	assert result == "123ABC"
	assert metadata["warnings"] == []

async def test_safe_vin_href_is_none():
	card = MagicMock()
	card.get_attribute = AsyncMock(return_value=None)
	metadata = {"warnings": []}

	result = await safe_vin(card, 0, metadata)

	assert result is None
	assert metadata["warnings"] == []

async def test_safe_vin_raises_exception():
	card = MagicMock()
	card.get_attribute = AsyncMock(side_effect=Exception("Boom!"))
	metadata = {"warnings": []}

	result = await safe_vin(card, 0, metadata)

	assert result is None
	assert any("Failed to extract VIN" in msg for msg in metadata["warnings"])

#endregion

#region Extract Mileage and Listed Tests

async def test_extract_mileage_and_listed_normal_case():
	card = MagicMock()

	block1 = AsyncMock()
	block1.inner_text.return_value = "12,345 mi"

	block2 = AsyncMock()
	block2.inner_text.return_value = "Listed 3 days ago"

	card.query_selector_all = AsyncMock(return_value=[block1, block2])
	metadata = {"warnings": []}

	mileage, listed = await extract_mileage_and_listed(card, 0, metadata)

	assert mileage == "12,345 mi"
	assert listed == "Listed 3 days ago"
	assert metadata["warnings"] == []

async def test_extract_mileage_and_listed_empty_blocks():
	card = MagicMock()
	card.query_selector_all = AsyncMock(return_value=[])
	metadata = {"warnings": []}

	mileage, listed = await extract_mileage_and_listed(card, 0, metadata)

	assert mileage == "N/A"
	assert listed == "N/A"

async def test_extract_mileage_and_listed_block_error_recovery():
	card = MagicMock()

	bad_block = AsyncMock()
	bad_block.inner_text.side_effect = Exception("fail")

	good_block = AsyncMock()
	good_block.inner_text.return_value = "Listed yesterday"

	card.query_selector_all = AsyncMock(return_value=[bad_block, good_block])
	metadata = {"warnings": []}

	mileage, listed = await extract_mileage_and_listed(card, 0, metadata)

	assert mileage == "N/A"
	assert listed == "Listed yesterday"
	assert metadata["warnings"] == []  # inner exceptions are suppressed

async def test_extract_mileage_and_listed_outer_exception():
	card = MagicMock()
	card.query_selector_all = AsyncMock(side_effect=Exception("query failed"))
	metadata = {"warnings": []}

	mileage, listed = await extract_mileage_and_listed(card, 1, metadata)

	assert mileage == "N/A"
	assert listed == "N/A"
	assert any("Listing #2" in msg for msg in metadata["warnings"])

#endregion

#region Extract Listings Tests
	
async def test_extract_listings_success_case():
	page = MagicMock()
	metadata = {"site_info": {}, "warnings": []}

	# Mock sidebar showing listing count
	sidebar = AsyncMock()
	sidebar.inner_text.return_value = "1,234 for sale nationwide"
	page.query_selector = AsyncMock(return_value=sidebar)

	# Mock vehicle card
	card = MagicMock()

	# Configure card's helper calls
	card.query_selector = AsyncMock(side_effect=[
		AsyncMock(inner_text=AsyncMock(return_value="2023 Subaru Outback")),
		AsyncMock(inner_text=AsyncMock(return_value="$30,000")),
		AsyncMock(inner_text=AsyncMock(return_value="Boulder, CO")),
	])  # for title, price, location

	card.get_attribute = AsyncMock(return_value="/search/listings/ABC123456")

	# Mock mileage/Listed block
	mileage_block = AsyncMock()
	mileage_block.inner_text.return_value = "12,345 mi"
	listed_block = AsyncMock()
	listed_block.inner_text.return_value = "Listed 2 days ago"
	card.query_selector_all = AsyncMock(return_value=[mileage_block, listed_block])

	# Inject one card into the page
	page.query_selector_all = AsyncMock(return_value=[card])

	# Run
	await extract_numbers_from_sidebar(page, metadata)
	listings = await extract_listings(page, metadata)

	# Assert output
	assert len(listings) == 1
	vehicle = listings[0]
	assert vehicle["title"] == "2023 Subaru Outback"
	assert vehicle["price"] == "$30,000"
	assert vehicle["location"] == "Boulder, CO"
	assert vehicle["mileage"] == "12,345 mi"
	assert vehicle["listed"] == "Listed 2 days ago"
	assert vehicle["vin"] == "ABC123456"
	assert metadata["site_info"]["total_for_sale"] == 1234

async def test_extract_listings_no_sidebar():
	page = MagicMock()
	metadata = {"warnings": []}

	# Sidebar is not found
	page.query_selector = AsyncMock(return_value=None)

	# Still mock one card
	card = MagicMock()
	card.query_selector = AsyncMock(side_effect=[
		AsyncMock(inner_text=AsyncMock(return_value="2020 Jeep Wrangler")),
		AsyncMock(inner_text=AsyncMock(return_value="$35,000")),
		AsyncMock(inner_text=AsyncMock(return_value="Denver, CO")),
	])
	card.get_attribute = AsyncMock(return_value="/search/listings/WRANGLER123")
	block = AsyncMock()
	block.inner_text.return_value = "45,000 mi"
	card.query_selector_all = AsyncMock(return_value=[block])

	page.query_selector_all = AsyncMock(return_value=[card])

	listings = await extract_listings(page, metadata)

	assert len(listings) == 1
	assert "total_for_sale" not in metadata

async def test_extract_listings_empty_results():
	page = MagicMock()
	metadata = {"site_info": {}, "warnings": []}

	page.query_selector = AsyncMock(return_value=None)
	page.query_selector_all = AsyncMock(return_value=[])

	listings = await extract_listings(page, metadata)

	assert listings == []
	assert "total_for_sale" not in metadata["site_info"]

#endregion

#region Fetch Page Tests

async def test_fetch_page_success():
	page = MagicMock()
	page.goto = AsyncMock()
	page.wait_for_selector = AsyncMock()

	result = await fetch_page(page, "https://visor.vin/search")
	assert result is True
	page.goto.assert_called_once_with("https://visor.vin/search", timeout=60000)
	page.wait_for_selector.assert_called_once_with(HREF_ELEMENT, timeout=20000)

async def test_fetch_page_failure_on_goto():
	page = MagicMock()
	page.goto = AsyncMock(side_effect=Exception("Timeout"))
	page.wait_for_selector = AsyncMock()

	result = await fetch_page(page, "https://visor.vin/search")
	assert result is False

async def test_fetch_page_failure_on_selector():
	page = MagicMock()
	page.goto = AsyncMock()
	page.wait_for_selector = AsyncMock(side_effect=Exception("Not found"))

	result = await fetch_page(page, "https://visor.vin/search")
	assert result is False

#endregion

#region Scrape Tests

async def test_scrape_exits_without_preset_or_make_model():
	args = SimpleNamespace(
		preset=None,
		make=None,
		model=None,
		trim=None, year=None,
		min_miles=None, max_miles=None, miles=None,
		min_price=None, max_price=None, price=None,
		condition=None, max_listings=50, sort="Newest"
	)

	with pytest.raises(SystemExit):
		await scrape(args)

async def test_scrape_loads_preset(monkeypatch):

	# Simulate --preset=outbacks
	args = SimpleNamespace(
		preset="outbacks",
		make=None,
		model=None,
		trim=None,
		year=None,
		min_miles=None, max_miles=None, miles=None,
		min_price=None, max_price=None, price=None,
		condition=None, max_listings=50, sort="Newest"
	)

	# Provide mock preset data
	preset_data = {
		"outbacks": {
			"make": "Subaru",
			"model": "Outback",
			"trim": ["Wilderness"],
			"year": ["2023"]
		}
	}

	monkeypatch.setattr("scraper.scraper.fetch_page", AsyncMock(return_value=False))
	monkeypatch.setattr("scraper.scraper.save_results", lambda *a, **k: None)

	with patch("builtins.open", mock_open(read_data=json.dumps(preset_data))):
		await scrape(args)

	assert args.make == "Subaru"
	assert args.model == "Outback"
	assert args.trim == ["Wilderness"]
	assert args.year == ["2023"]

async def test_scrape_invalid_preset_exits():
	args = SimpleNamespace(
		preset="nonexistent",
		make=None, model=None,
		trim=None, year=None,
		min_miles=None, max_miles=None, miles=None,
		min_price=None, max_price=None, price=None,
		condition=None, max__listings=50, sort="Newest"
	)

	preset_data = {"some_other": {"make": "Ford", "model": "Escape"}}

	with patch("builtins.open", mock_open(read_data=json.dumps(preset_data))):
		with pytest.raises(SystemExit):
			await scrape(args)

@patch("scraper.scraper.fetch_page", new_callable=AsyncMock)
@patch("scraper.scraper.async_playwright")
async def test_scrape_exits_if_fetch_page_fails(mock_playwright, mock_fetch_page):
	mock_fetch_page.return_value = False

	mock_browser = AsyncMock()
	mock_browser.close = AsyncMock()

	mock_context = MagicMock()

	mock_page = AsyncMock()
	mock_page.goto = AsyncMock()
	mock_page.wait_for_load_state = AsyncMock()

	mock_playwright.return_value.__aenter__.return_value.chromium.launch.return_value = mock_browser
	mock_browser.new_context.return_value = mock_context
	mock_context.new_page.return_value = mock_page

	args = MagicMock()
	args.preset = None
	args.make = "Subaru"
	args.model = "Outback"
	args.trim = ["Wilderness"]
	args.year = ["2023", "2024", "2025"]
	args.max_listings = 50
	args.sort = "Newest"
	args.condition = None
	args.miles = args.min_miles = args.max_miles = None
	args.price = args.min_price = args.max_price = None

	await scrape(args)

	mock_fetch_page.assert_called_once()
	mock_browser.close.assert_called_once()

#endregion

#region Auto Scroll Tests

async def test_scroll_stops_at_max_listings():
	page = MagicMock()
	page.query_selector_all = AsyncMock(return_value=[1] * 300)  # Simulate 300 listings already present

	metadata = {"runtime": {}}
	await auto_scroll_to_load_all(page, metadata, max_listings=300)

	assert metadata["runtime"]["scrolls"] == 0

async def test_scroll_stops_when_no_new_listings():
	page = MagicMock()
	# Simulate same number of listings on repeated scrolls
	page.query_selector_all = AsyncMock(side_effect=[[1]*50, [1]*50])

	page.evaluate = AsyncMock()
	page.wait_for_selector = AsyncMock()
	page.wait_for_timeout = AsyncMock()

	metadata = {"runtime": {}}
	await auto_scroll_to_load_all(page, metadata, max_listings=200)

	assert metadata["runtime"]["scrolls"] == 1

async def test_scroll_stops_on_selector_timeout():
	page = MagicMock()
	page.query_selector_all = AsyncMock(side_effect=[[1]*50, [1]*100])

	page.evaluate = AsyncMock()
	page.wait_for_selector = AsyncMock(side_effect=Exception("Timeout"))
	page.wait_for_timeout = AsyncMock()

	metadata = {"runtime": {}}
	await auto_scroll_to_load_all(page, metadata, max_listings=200)

	assert metadata["runtime"]["scrolls"] == 1

async def test_scroll_progresses_multiple_times():
	page = MagicMock()
	scroll_sequence = [
	[1]*50,   # 0 → 50
	[1]*100,  # 50 → 100
	[1]*150,  # 100 → 150
	[1]*200,  # 150 → 200
	[1]*250,  # 200 → 250 (should stop here: meets max_listings)
]

	page.query_selector_all = AsyncMock(side_effect=chain(scroll_sequence, repeat([1]*250)))

	page.evaluate = AsyncMock()
	page.wait_for_selector = AsyncMock()
	page.wait_for_timeout = AsyncMock()

	metadata = {"runtime": {}}
	await auto_scroll_to_load_all(page, metadata, max_listings=250)

	assert metadata["runtime"]["scrolls"] == 4

#endregion
