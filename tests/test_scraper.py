import argparse
import json
import pytest
import scraper.scraper as scraper
import scraper.utils as utils
from itertools import chain, repeat
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, ANY, mock_open, patch
from scraper.constants import LISTING_CARD_SELECTOR, MAX_LISTINGS, REMAPPING_RULES, PRESET_PATH
from scraper.scraper import *

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

#region Build Metadata Tests

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

#region Build Query Params Tests

def test_query_params_converts_bools_and_lists():
	args = SimpleNamespace(
		make="Toyota", model="RAV4", trim=["XLE", "Adventure"], year=["2021"],
		min_miles=None, max_miles=None, miles=None,
		min_price=None, max_price=None, price=None,
		condition=["used", "certified"], max_listings=50, sort="Newest"
	)
	metadata = build_metadata(args)
	query = build_query_params(args, metadata)

	# Confirm list joined correctly
	assert "trim" in query
	assert query["trim"] == "XLE,Adventure"
	assert query["car_type"] == "used,certified"
	assert query["sort"] == "newest"

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

#region Extract Listings Tests
	
async def test_extract_listings_success_case():
	browser = MagicMock()
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
		AsyncMock(inner_text=AsyncMock(return_value="12,345 mi")),
	])  # for title, price

	card.get_attribute = AsyncMock(return_value="/search/listings/ABC123456")

	# Inject one card into the page
	page.query_selector_all = AsyncMock(return_value=[card])

	# Run
	await extract_numbers_from_sidebar(page, metadata)
	listings = await extract_listings(browser, page, metadata)

	# Assert output
	assert len(listings) == 1
	vehicle = listings[0]
	assert vehicle["title"] == "2023 Subaru Outback"
	assert vehicle["price"] == "$30,000"
	assert vehicle["mileage"] == "12,345 mi"
	assert vehicle["vin"] == "ABC123456"
	assert metadata["site_info"]["total_for_sale"] == 1234

async def test_extract_listings_no_sidebar():
	browser = MagicMock()
	page = MagicMock()
	metadata = {"warnings": []}

	# Sidebar is not found
	page.query_selector = AsyncMock(return_value=None)

	# Still mock one card
	card = MagicMock()
	card.query_selector = AsyncMock(side_effect=[
		AsyncMock(inner_text=AsyncMock(return_value="2020 Jeep Wrangler")),
		AsyncMock(inner_text=AsyncMock(return_value="$35,000")),
		AsyncMock(inner_text=AsyncMock(return_value="45,000 mi")),
	])
	card.get_attribute = AsyncMock(return_value="/search/listings/WRANGLER123")
	page.query_selector_all = AsyncMock(return_value=[card])

	listings = await extract_listings(browser, page, metadata)

	assert len(listings) == 1
	assert "total_for_sale" not in metadata

async def test_extract_listings_empty_results():
	browser = MagicMock()
	page = MagicMock()
	metadata = {"site_info": {}, "warnings": []}

	page.query_selector = AsyncMock(return_value=None)
	page.query_selector_all = AsyncMock(return_value=[])

	listings = await extract_listings(browser, page, metadata)

	assert listings == []
	assert "total_for_sale" not in metadata["site_info"]

async def test_extract_listings_limits_to_max_listings():
	browser = AsyncMock()
	page = AsyncMock()
	metadata = {"warnings": []}

	# Two mock cards
	card1 = AsyncMock()
	card2 = AsyncMock()

	# Required mocks for minimal success
	page.query_selector_all.return_value = [card1, card2]

	card1.query_selector.side_effect = [
		AsyncMock(inner_text=AsyncMock(return_value="2023 Subaru Outback")),  # title
		AsyncMock(inner_text=AsyncMock(return_value="$30,000")),              # price
		AsyncMock(inner_text=AsyncMock(return_value="12,345 mi"))            # mileage
	]
	card1.get_attribute = AsyncMock(return_value="ABC123456")

	card2.query_selector.side_effect = card1.query_selector.side_effect
	card2.get_attribute = AsyncMock(return_value="DEF789101")

	with patch("scraper.scraper.extract_full_listing_details", new_callable=AsyncMock):
		listings = await extract_listings(browser, page, metadata, max_listings=1)

	assert len(listings) == 1


#endregion

#region Fetch Page Tests

async def test_fetch_page_success():
	page = MagicMock()
	page.goto = AsyncMock()
	page.wait_for_selector = AsyncMock()

	result = await fetch_page(page, "https://visor.vin/search")
	assert result is True
	page.goto.assert_called_once_with("https://visor.vin/search", timeout=60000)
	page.wait_for_selector.assert_called_once_with(LISTING_CARD_SELECTOR, timeout=20000)

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

@patch("pathlib.Path.exists", return_value=False)
async def test_missing_presets_file_raises(monkeypatch):
	args = SimpleNamespace(preset="outbacks", make=None, model=None)
	with pytest.raises(SystemExit):
		await scraper.scrape(args)

@patch("scraper.scraper.auto_scroll_to_load_all", new_callable=AsyncMock)
@patch("scraper.scraper.extract_numbers_from_sidebar", new_callable=AsyncMock)
@patch("scraper.scraper.fetch_page", new_callable=AsyncMock, return_value=True)
@patch("scraper.scraper.async_playwright")
async def test_scrape_calls_sidebar_and_scroll(mock_playwright, mock_fetch_page, mock_extract_sidebar, mock_scroll):
	# Setup browser mocks
	mock_browser = AsyncMock()
	mock_page = AsyncMock()

	mock_playwright.return_value.__aenter__.return_value.chromium.launch.return_value = mock_browser
	mock_browser.new_page.return_value = mock_page

	args = argparse.Namespace(
		preset=None,
		make="Subaru",
		model="Outback",
		trim=None,
		year=None,
		min_miles=None,
		max_miles=None,
		miles=None,
		min_price=None,
		max_price=None,
		price=None,
		condition=None,
		sort="Newest",
		max_listings=50,
	)

	await scraper.scrape(args)

	# Assert both are called once with the mock page
	mock_extract_sidebar.assert_awaited_once_with(mock_page, ANY)
	mock_scroll.assert_awaited_once_with(mock_page, ANY, max_listings=50)

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

#region Parse Warranty Coverage Tests

async def test_parse_warranty_full_data():
	coverage = MagicMock()
	coverage.query_selector_all = AsyncMock(return_value=[
		AsyncMock(),  # 0: not used
		AsyncMock(inner_text=AsyncMock(return_value="12 months")),  # 1
		AsyncMock(inner_text=AsyncMock(return_value="36 months")),  # 2
		AsyncMock(),  # 3: not used
		AsyncMock(inner_text=AsyncMock(return_value="12,000 mi")),  # 4
		AsyncMock(inner_text=AsyncMock(return_value="36,000 mi")),  # 5
	])

	# Mock safe_text
	scraper.safe_text = AsyncMock(side_effect=["Powertrain", "Active"])

	metadata = {"warnings": []}
	entry = await parse_warranty_coverage(coverage, 1, metadata)

	assert entry["type"] == "Powertrain"
	assert entry["status"] == "Active"
	assert entry["time_left"] == "12 months"
	assert entry["time_total"] == "36 months"
	assert entry["miles_left"] == "12,000 mi"
	assert entry["miles_total"] == "36,000 mi"

async def test_parse_warranty_partial_data():
	coverage = MagicMock()
	coverage.query_selector_all = AsyncMock(return_value=[
		AsyncMock(), AsyncMock(), AsyncMock()  # fewer than 6 elements
	])

	scraper.safe_text = AsyncMock(side_effect=["Bumper-to-Bumper", "Expired"])

	metadata = {"warnings": []}
	entry = await parse_warranty_coverage(coverage, 2, metadata)

	assert entry["type"] == "Bumper-to-Bumper"
	assert entry["status"] == "Expired"
	assert "time_left" not in entry
	assert "miles_left" not in entry

async def test_parse_warranty_safe_text_returns_na():
	coverage = MagicMock()
	coverage.query_selector_all = AsyncMock(return_value=[])

	scraper.safe_text = AsyncMock(side_effect=["N/A", "N/A"])

	metadata = {"warnings": []}
	entry = await parse_warranty_coverage(coverage, 3, metadata)

	assert entry["type"] == "N/A"
	assert entry["status"] == "N/A"
	assert "time_left" not in entry

async def test_parse_warranty_limits_raise_exception():
	# Simulate query_selector_all working but one inner_text fails
	elem_ok = AsyncMock()
	elem_ok.inner_text.return_value = "24 months"

	elem_bad = AsyncMock()
	elem_bad.inner_text.side_effect = Exception("fail")

	coverage = MagicMock()
	coverage.query_selector_all = AsyncMock(return_value=[
		None, elem_ok, elem_ok, None, elem_bad, elem_ok
	])

	scraper.safe_text = AsyncMock(side_effect=["Limited", "Pending"])

	metadata = {"warnings": []}
	entry = await parse_warranty_coverage(coverage, 4, metadata)

	# Only successful calls should be present
	assert entry["type"] == "Limited"
	assert entry["status"] == "Pending"
	assert entry["time_left"] == "24 months"
	assert "miles_left" not in entry  # failed one should be skipped silently

#endregion

#region Extract Warranty Info Tests

async def test_extract_warranty_info_success():
	page = MagicMock()
	metadata = {"warnings": []}
	listing = {}

	# Mock safe_text for overall_status
	scraper.safe_text = AsyncMock(return_value="Active Warranty")

	# Mock coverage elements
	coverage1 = MagicMock()
	coverage2 = MagicMock()
	page.query_selector_all = AsyncMock(return_value=[coverage1, coverage2])

	# Mock parse_warranty_coverage output
	scraper.parse_warranty_coverage = AsyncMock(side_effect=[
		{"type": "Powertrain", "status": "Active"},
		{"type": "Bumper", "status": "Expired"}
	])

	await extract_warranty_info(page, listing, 1, metadata)

	assert listing["warranty"]["overall_status"] == "Active Warranty"
	assert len(listing["warranty"]["coverages"]) == 2
	assert listing["warranty"]["coverages"][0]["type"] == "Powertrain"
	assert listing["warranty"]["coverages"][1]["status"] == "Expired"

async def test_extract_warranty_info_no_coverages():
	page = MagicMock()
	metadata = {"warnings": []}
	listing = {}

	scraper.safe_text = AsyncMock(return_value="No coverage found")
	page.query_selector_all = AsyncMock(return_value=[])
	scraper.parse_warranty_coverage = AsyncMock()  # should not be called

	await extract_warranty_info(page, listing, 2, metadata)

	assert listing["warranty"]["overall_status"] == "No coverage found"
	assert listing["warranty"]["coverages"] == []

#endregion

#region Extract Additional Documents Tests

@patch("scraper.scraper.get_url", new_callable=AsyncMock)
async def test_extract_additional_documents_sets_fields(mock_get_url):
	mock_get_url.side_effect = [
		"https://example.com/carfax",
		"https://example.com/sticker"
	]

	page = AsyncMock()
	listing = {}
	metadata = {"warnings": []}

	await extract_additional_documents(page, listing, 5, metadata)

	assert listing["additional_docs"]["carfax_url"] == "https://example.com/carfax"
	assert listing["additional_docs"]["window_sticker_url"] == "https://example.com/sticker"


@patch("scraper.scraper.get_url", new_callable=AsyncMock)
async def test_extract_additional_documents_calls_get_url_correctly(mock_get_url):
	mock_get_url.side_effect = AsyncMock(side_effect=["A", "B"])
	
	page = AsyncMock()
	listing = {}
	metadata = {"warnings": []}

	await extract_additional_documents(page, listing, 6, metadata)

	mock_get_url.assert_any_await(page, CARFAX_URL_ELEMENT, 6, metadata)
	mock_get_url.assert_any_await(page, WINDOW_STICKER_URL_ELEMENT, 6, metadata)

#endregion

#region Extract Seller Info Tests

async def test_extract_seller_info_missing_div():
	page = AsyncMock()
	page.query_selector.return_value = None

	listing = {}
	metadata = {"warnings": []}

	await extract_seller_info(page, listing, 1, metadata)

	assert listing["seller"] == {
		"name": "N/A",
		"location": "N/A",
		"map_url": "N/A",
		"stock_number": "N/A",
		"phone": "N/A"
	}

async def test_extract_seller_info_full_success():
	page = AsyncMock()
	seller_div = AsyncMock()
	page.query_selector.return_value = seller_div

	scraper.safe_text = AsyncMock(side_effect=[
		"CarMax in Boulder, CO",  # seller_info
		"Stock#123",
		"(555) 123-4567"
	])

	page.wait_for_selector.return_value = None
	page.get_attribute = AsyncMock(return_value="https://maps.example.com")

	seller_div.query_selector_all = AsyncMock(return_value=[AsyncMock(), AsyncMock()])

	listing = {}
	metadata = {"warnings": []}

	await extract_seller_info(page, listing, 2, metadata)

	assert listing["seller"] == {
		"name": "CarMax",
		"location": "Boulder, CO",
		"map_url": "https://maps.example.com",
		"stock_number": "Stock#123",
		"phone": "(555) 123-4567"
	}

async def test_extract_seller_info_bad_format():
	page = AsyncMock()
	seller_div = AsyncMock()
	page.query_selector.return_value = seller_div

	scraper.safe_text = AsyncMock(return_value="UnknownFormat")  # no 'in'

	seller_div.query_selector_all = AsyncMock(return_value=[])

	page.wait_for_selector = AsyncMock()
	page.get_attribute = AsyncMock()

	listing = {}
	metadata = {"warnings": []}

	await extract_seller_info(page, listing, 3, metadata)

	assert "name" not in listing["seller"]  # optional: could assert defaults here
	assert "Failed to read seller name/location in listing 3" in metadata["warnings"]

async def test_extract_seller_info_map_timeout():
	page = AsyncMock()
	seller_div = AsyncMock()
	page.query_selector.return_value = seller_div

	scraper.safe_text = AsyncMock(return_value="Dealer in Denver, CO")

	page.wait_for_selector.side_effect = TimeoutError("timeout")
	page.get_attribute = AsyncMock()

	seller_div.query_selector_all = AsyncMock(return_value=[])

	listing = {}
	metadata = {"warnings": []}

	await extract_seller_info(page, listing, 4, metadata)

	assert listing["seller"]["map_url"] == "N/A" or "map_url" not in listing["seller"]
	assert "Failed to read Map URL for seller in listing 4" in metadata["warnings"]

#endregion 

#region Extract Market Velocity Tests

async def test_extract_market_velocity_full_success():
	page = AsyncMock()
	listing = {}
	metadata = {"warnings": []}

	# Mock wait and 3 sections
	page.wait_for_selector.return_value = None

	# Mock elements inside each section
	section1 = AsyncMock()
	section1.query_selector.return_value = AsyncMock(inner_text=AsyncMock(return_value="1234"))

	section2 = AsyncMock()
	section2.query_selector_all.return_value = [
		AsyncMock(inner_text=AsyncMock(return_value="45 days")),
		AsyncMock(inner_text=AsyncMock(return_value="12 days"))
	]

	section3 = AsyncMock()
	section3.query_selector.return_value = AsyncMock(inner_text=AsyncMock(return_value="80% chance"))

	page.query_selector_all.return_value = [section1, section2, section3]

	await extract_market_velocity(page, listing, 1, metadata)

	assert listing["market_velocity"] == {
		"vehicles_sold_14d": 1234,
		"avg_days_on_market": 45,
		"this_vehicle_days": 12,
		"sell_chance_7d": 0.80
	}

async def test_extract_market_velocity_partial_data():
	page = AsyncMock()
	listing = {}
	metadata = {"warnings": []}

	page.wait_for_selector.return_value = None

	section1 = AsyncMock()
	section1.query_selector.return_value = AsyncMock(inner_text=AsyncMock(return_value="789"))

	page.query_selector_all.return_value = [section1]  # only one section

	await extract_market_velocity(page, listing, 2, metadata)

	assert listing["market_velocity"]["vehicles_sold_14d"] == 789
	assert "avg_days_on_market" not in listing["market_velocity"]

async def test_extract_market_velocity_elements_missing():
	page = AsyncMock()
	listing = {}
	metadata = {"warnings": []}

	page.wait_for_selector.return_value = None

	section1 = AsyncMock()
	section1.query_selector.return_value = None  # sold_el is missing

	page.query_selector_all.return_value = [section1]

	await extract_market_velocity(page, listing, 3, metadata)

	assert "market_velocity" not in listing

async def test_extract_market_velocity_raises_exception():
	page = AsyncMock()
	listing = {}
	metadata = {"warnings": []}

	page.wait_for_selector.side_effect = Exception("boom")

	await extract_market_velocity(page, listing, 4, metadata)

	assert "Failed to extract market velocity for listing 4: boom" in metadata["warnings"]

#endregion
 
#region Extract Install Options Tests

async def test_extract_install_options_standard_case():
	page = AsyncMock()
	listing = {}
	metadata = {"warnings": []}

	page.wait_for_selector.return_value = None

	addon_elements = [
		AsyncMock(inner_text=AsyncMock(return_value="Cargo Tray ($120)")),
		AsyncMock(inner_text=AsyncMock(return_value="Moonroof Package ($950)")),
		AsyncMock(inner_text=AsyncMock(return_value="Total options: $1,070"))
	]

	page.query_selector_all.return_value = addon_elements

	await extract_install_options(page, listing, 1, metadata)

	assert listing["installed_addons"]["items"] == [
		{"name": "Cargo Tray", "price": 120},
		{"name": "Moonroof Package", "price": 950}
	]
	assert listing["installed_addons"]["total"] == 1070

async def test_extract_install_options_addon_without_price():
	page = AsyncMock()
	listing = {}
	metadata = {"warnings": []}

	page.wait_for_selector.return_value = None

	addon_elements = [
		AsyncMock(inner_text=AsyncMock(return_value="Unknown Option")),
		AsyncMock(inner_text=AsyncMock(return_value="Total options: $0"))
	]

	page.query_selector_all.return_value = addon_elements

	await extract_install_options(page, listing, 2, metadata)

	assert listing["installed_addons"]["items"] == [
		{"name": "Unknown Option", "price": 0}
	]
	assert listing["installed_addons"]["total"] == 0

async def test_extract_install_options_total_only():
	page = AsyncMock()
	listing = {}
	metadata = {"warnings": []}

	page.wait_for_selector.return_value = None

	addon_elements = [
		AsyncMock(inner_text=AsyncMock(return_value="Total options: $1,000"))
	]

	page.query_selector_all.return_value = addon_elements

	await extract_install_options(page, listing, 3, metadata)

	assert listing["installed_addons"]["items"] == []
	assert listing["installed_addons"]["total"] == 1000

async def test_extract_install_options_ignores_invalid_line():
	page = AsyncMock()
	listing = {}
	metadata = {"warnings": []}

	page.wait_for_selector.return_value = None

	addon_elements = [
		AsyncMock(inner_text=AsyncMock(return_value="!!Garbage!!")),
		AsyncMock(inner_text=AsyncMock(return_value="Total options: $0"))
	]

	page.query_selector_all.return_value = addon_elements

	await extract_install_options(page, listing, 4, metadata)

	assert listing["installed_addons"]["items"] == [{"name": "!!Garbage!!", "price": 0}]
	assert listing["installed_addons"]["total"] == 0

async def test_extract_install_options_timeout():
	page = AsyncMock()
	listing = {}
	metadata = {"warnings": []}

	page.wait_for_selector.side_effect = TimeoutError("timeout")

	await extract_install_options(page, listing, 5, metadata)

	# Should still set the structure even if empty
	assert listing["installed_addons"] == {"items": [], "total": 0}

#endregion

#region Extract Spec Details Tests

async def test_extract_spec_details_standard_spec_row():
	page = AsyncMock()
	listing = {}
	metadata = {"warnings": []}

	page.wait_for_selector.return_value = None

	row = AsyncMock()
	row.query_selector_all.return_value = [
		AsyncMock(inner_text=AsyncMock(return_value="Color:")),
		AsyncMock(inner_text=AsyncMock(return_value="Red")),
		AsyncMock(inner_text=AsyncMock(return_value="Transmission:")),
		AsyncMock(inner_text=AsyncMock(return_value="Automatic"))
	]

	page.query_selector_all.return_value = [row]

	await extract_spec_details(page, listing, 1, metadata)

	assert listing["specs"] == {
		"Color": "Red",
		"Transmission": "Automatic"
	}

async def test_extract_spec_details_skips_label():
	page = AsyncMock()
	listing = {}
	metadata = {"warnings": []}

	page.wait_for_selector.return_value = None

	row = AsyncMock()
	row.query_selector_all.return_value = [
		AsyncMock(inner_text=AsyncMock(return_value="VIN:")),
		AsyncMock(inner_text=AsyncMock(return_value="123")),
		AsyncMock(inner_text=AsyncMock(return_value="Color:")),
		AsyncMock(inner_text=AsyncMock(return_value="Blue"))
	]

	page.query_selector_all.return_value = [row]

	await extract_spec_details(page, listing, 2, metadata)

	assert listing["specs"] == {"Color": "Blue"}

async def test_extract_spec_details_triggers_installed_options():
	page = AsyncMock()
	listing = {}
	metadata = {"warnings": []}

	page.wait_for_selector.return_value = None

	row = AsyncMock()
	row.query_selector_all.return_value = [
		AsyncMock(inner_text=AsyncMock(return_value="Installed Options")),
		AsyncMock(inner_text=AsyncMock(return_value="See options"))
	]

	page.query_selector_all.return_value = [row]

	scraper.extract_install_options = AsyncMock()

	await extract_spec_details(page, listing, 3, metadata)

	scraper.extract_install_options.assert_awaited_once_with(page, listing, 3, metadata)

async def test_extract_spec_details_skips_empty_row():
	page = AsyncMock()
	listing = {}
	metadata = {"warnings": []}

	page.wait_for_selector.return_value = None

	row = AsyncMock()
	row.query_selector_all.return_value = []

	page.query_selector_all.return_value = [row]

	await extract_spec_details(page, listing, 4, metadata)

	assert listing["specs"] == {}

async def test_extract_spec_details_timeout():
	page = AsyncMock()
	listing = {}
	metadata = {"warnings": []}

	page.wait_for_selector.side_effect = TimeoutError("timeout")

	await extract_spec_details(page, listing, 5, metadata)

	assert listing["specs"] == {}

async def test_extract_spec_details_handles_unexpected_exception():
	page = AsyncMock()
	listing = {}
	metadata = {"warnings": []}

	page.wait_for_selector.side_effect = Exception("Spec table exploded")

	await extract_spec_details(page, listing, 99, metadata)

	assert any("Could not extract spec details for listing #99: Spec table exploded" in w for w in metadata["warnings"])

@patch("scraper.scraper.extract_additional_documents", new_callable=AsyncMock)
@patch("scraper.scraper.extract_seller_info", new_callable=AsyncMock)
async def test_extract_spec_details_additional_docs_and_seller(mock_seller_info, mock_additional_docs):
	page = AsyncMock()
	listing = {}
	metadata = {"warnings": []}

	# First row: Additional Documentation
	row1 = AsyncMock()
	row1.query_selector_all.return_value = [
		AsyncMock(inner_text=AsyncMock(return_value="Additional Documentation")),
		AsyncMock(inner_text=AsyncMock(return_value="Some Value"))
	]

	# Second row: Seller
	row2 = AsyncMock()
	row2.query_selector_all.return_value = [
		AsyncMock(inner_text=AsyncMock(return_value="Seller")),
		AsyncMock(inner_text=AsyncMock(return_value="Some Seller Info"))
	]

	page.query_selector_all.return_value = [row1, row2]
	page.wait_for_selector.return_value = None

	await extract_spec_details(page, listing, 5, metadata)

	mock_additional_docs.assert_awaited_once_with(page, listing, 5, metadata)
	mock_seller_info.assert_awaited_once_with(page, listing, 5, metadata)

#endregion

#region Extract Price History Tests

async def test_extract_price_history_full_entry():
	page = AsyncMock()
	listing = {}
	metadata = {"warnings": []}

	page.wait_for_selector.return_value = None

	# Mock inner divs
	left_divs = [
		AsyncMock(inner_text=AsyncMock(return_value="Jul 1, 2024")),
		AsyncMock(inner_text=AsyncMock(return_value="- $500 price drop"))
	]

	right_divs = [
		AsyncMock(inner_text=AsyncMock(return_value="Lowest - $20,000")),
		AsyncMock(inner_text=AsyncMock(return_value="123,456 mi"))
	]

	block_0 = AsyncMock()
	block_0.query_selector_all.return_value = left_divs

	block_1 = AsyncMock()
	block_1.query_selector_all.return_value = right_divs

	change = AsyncMock()
	change.query_selector_all.return_value = [block_0, block_1]

	page.query_selector_all.return_value = [change]

	await extract_price_history(page, listing, 1, metadata)

	history = listing["price_history"]
	assert len(history) == 1
	assert history[0]["date"] == "Jul 1, 2024"
	assert history[0]["price_change"] == 500
	assert history[0]["price"] == 20000
	assert history[0]["mileage"] == 123456
	assert history[0]["lowest"] is True

async def test_extract_price_history_partial_entry():
	page = AsyncMock()
	listing = {}
	metadata = {"warnings": []}

	page.wait_for_selector.return_value = None

	block_0 = AsyncMock()
	block_0.query_selector_all.return_value = []

	right_divs = [
		AsyncMock(inner_text=AsyncMock(return_value="$19,000")),
	]

	block_1 = AsyncMock()
	block_1.query_selector_all.return_value = right_divs

	change = AsyncMock()
	change.query_selector_all.return_value = [block_0, block_1]

	page.query_selector_all.return_value = [change]

	await extract_price_history(page, listing, 2, metadata)

	history = listing["price_history"]
	assert history[0]["price"] == 19000
	assert history[0]["price_change"] is None

async def test_extract_price_history_skips_invalid_block():
	page = AsyncMock()
	listing = {}
	metadata = {"warnings": []}

	page.wait_for_selector.return_value = None

	# Only 1 block instead of 2
	change = AsyncMock()
	change.query_selector_all.return_value = [AsyncMock()]

	page.query_selector_all.return_value = [change]

	await extract_price_history(page, listing, 3, metadata)

	assert listing["price_history"] == []

async def test_extract_price_history_empty():
	page = AsyncMock()
	listing = {}
	metadata = {"warnings": []}

	page.wait_for_selector.return_value = None
	page.query_selector_all.return_value = []

	await extract_price_history(page, listing, 4, metadata)

	assert listing["price_history"] == []

#endregion

#region Extract Full Listing Details Tests

@patch("scraper.scraper.extract_spec_details", new_callable=AsyncMock)
@patch("scraper.scraper.extract_warranty_info", new_callable=AsyncMock)
@patch("scraper.scraper.extract_market_velocity", new_callable=AsyncMock)
@patch("scraper.scraper.extract_price_history", new_callable=AsyncMock)
async def test_extract_full_listing_details_success(mock_price, mock_velocity, mock_warranty, mock_specs):
	browser = AsyncMock()
	context = AsyncMock()
	page = AsyncMock()
	link = AsyncMock()
	link.get_attribute.return_value = "https://visor.vin/some/listing"

	# Setup browser mock flow
	browser.new_context.return_value = context
	context.new_page.return_value = page
	page.query_selector.return_value = link

	page.goto.return_value = None
	page.wait_for_selector.return_value = None

	listing = {"vin": "ABC123456"}
	metadata = {"warnings": []}

	await extract_full_listing_details(browser, listing, 1, metadata)

	assert listing["listing_url"] == "https://visor.vin/some/listing"
	assert "error" not in listing

	mock_specs.assert_awaited_once()
	mock_warranty.assert_awaited_once()
	mock_velocity.assert_awaited_once()
	mock_price.assert_awaited_once()

	context.add_cookies.assert_awaited_once()
	page.close.assert_awaited_once()

@patch("scraper.scraper.extract_spec_details", new_callable=AsyncMock)
@patch("scraper.scraper.extract_warranty_info", new_callable=AsyncMock)
@patch("scraper.scraper.extract_market_velocity", new_callable=AsyncMock)
@patch("scraper.scraper.extract_price_history", new_callable=AsyncMock)
async def test_extract_full_listing_details_url_timeout(*_):
	browser = AsyncMock()
	context = AsyncMock()
	page = AsyncMock()

	browser.new_context.return_value = context
	context.new_page.return_value = page

	page.goto.return_value = None
	page.wait_for_selector.return_value = None
	page.query_selector.side_effect = TimeoutError("timeout")

	listing = {"vin": "XYZ987"}
	metadata = {"warnings": []}

	await extract_full_listing_details(browser, listing, 2, metadata)

	assert listing["listing_url"] == "None"
	assert "Failed to get listing URL for #2" in metadata["warnings"]

async def test_extract_full_listing_details_runtime_failure():
	browser = AsyncMock()
	context = AsyncMock()
	page = AsyncMock()

	browser.new_context.return_value = context
	context.new_page.return_value = page

	page.goto.side_effect = Exception("navigation failed")

	listing = {"vin": "FAIL999"}
	metadata = {"warnings": []}

	await extract_full_listing_details(browser, listing, 3, metadata)

	assert listing["error"].startswith("Failed to fetch full details")
	page.close.assert_awaited_once()

#endregion

