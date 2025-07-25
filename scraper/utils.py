# utils.py
import re
from datetime import datetime

def normalize_years(raw_years):
	result = set()
	errors = []

	def convert_year(year_str: str) -> int:
		y = int(year_str)
		if len(year_str) == 4:
			return y
		elif y >= 50:
			return 1900 + y
		else:
			return 2000 + y

	for entry in raw_years:
		try:
			if "-" in entry:
				start_str, end_str = entry.split("-")
				start = convert_year(start_str)
				end = convert_year(end_str)
				if start > end:
					raise ValueError(f"Start year '{start}' is after end year '{end}'")
				result.update(range(start, end + 1))
			else:
				result.add(convert_year(entry))
		except ValueError as e:
			errors.append(f"[Year Error] Skipping '{entry}': {e}")
		except Exception as e:
			errors.append(f"[Year Error] Could not parse '{entry}': {e}")

	if not result:
		for msg in errors:
			print(msg)
		print("[Error] No valid years provided. Please check your --year format.")
		exit(1)
	elif errors:
		for msg in errors:
			print(msg)

	return sorted(result)

def remove_null_entries(d: dict) -> dict:
	return {k: v for k, v in d.items() if v is not None}

def sanitize_numeric_range(raw: str) -> str:
	return re.sub(r"[^\d\-]", "", raw)

def parse_range_arg(name: str, raw: str):
	try:
		raw = sanitize_numeric_range(raw)
		parts = raw.split("-")
		if len(parts) == 2:
			min_val = int(parts[0]) if parts[0] else None
			max_val = int(parts[1]) if parts[1] else None
		elif len(parts) == 1:
			min_val = int(parts[0])
			max_val = None
		else:
			raise ValueError("Too many hyphens in range input.")

		if min_val is None and max_val is None:
			raise ValueError(f"{name} range cannot be completely empty.")
		if min_val and max_val and min_val > max_val:
			raise ValueError(f"{name} range start cannot exceed end.")
		
		return min_val, max_val
	except Exception as e:
		print(f"[Error] Invalid format for --{name}: '{raw}' → {e}")
		exit(1)

def current_timestamp():
	return datetime.now().strftime("%Y%m%d_%H%M%S")
