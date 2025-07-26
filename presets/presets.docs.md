# `presets.json` Format Guide

This document describes the structure and accepted values for the `presets.json` file used by the scraper CLI.

Each top-level key is the name of a preset (e.g., `"default"`), and the value is an object containing search filters and behavior controls.

## Required Fields

### `make` (string)
The vehicle manufacturer.

**Example:** 
```json
"make": "Jeep"
```

### `model` (string)
The vehicle model name.

**Example:** 
```json
"model": "Grand Cherokee"
```

## Optional Filters

### `trim` (array of strings)
One or more trim levels. Multi-word trims must be quoted.

**Example:** 
```json
"trim": ["Adventure", "TRD Off-Road"]
```

### `year` (array of strings)
Year(s) or ranges of years.

**Examples:**
```json
"year": [
        "2010",
        "2012-2015",
        "17-19",        // 2017-2019
        "50",           // interpreted as 1950
        "49",           // interpreted as 2049
        "87-02",        // 1987-2002
        "2020-22",      // 2020-2022
        "23-2025"       // 2023-2025
    ] 
```

### `min_miles` / `max_miles` (integer)
Lower and upper bounds on vehicle mileage.

**Example:**
```json
"min_miles": 10000,
"max_miles": 60000
```

### `miles` (string)
Range in a single string (overrides `min_miles` and `max_miles`).

**Example:** 
```json
"miles": "10000-60000"
"miles": "10,000-60,000"
```

### `min_price` / `max_price` (integer)
Lower and upper bounds on price (in USD).

**Example:**
```json
"min_price": 25000,
"max_price": 40000
```

### `price` (string)
Range in a single string (overrides `min_price` and `max_price`).

**Example:** 
```json
"price": "25000-40000"
"price": "25,000-40,000"
"price": "$25000-$40000"
```

### `condition` (array of strings)
Filter by vehicle condition.

**Allowed values:**
- `"New"`
- `"Used"`
- `"Certified"`

**Example:** 
```json
"condition": ["Used", "Certified"]
```

## Sorting

### `sort` (string)
Sort order for results.

**Allowed values:**
- `"Newest"` (default)
- `"Oldest"`
- `"Highest Price"`
- `"Lowest Price"`
- `"Highest Mileage"`
- `"owest Mileage"`
- `"Best Match"`

**Example:** 
```json
"sort": "Oldest"
```

## Scraper Behavior

### `max_listings` (integer)
Maximum number of listings to retrieve (capped at 500).

**Example:** 
```json
"max_listings": 100
```

## Notes

- Fields like `miles` and `price` will override their `min_*` / `max_*` counterparts.
- Fields are case sensitive when parsed `Jeep` is not equal to `jeep`.
- Presets are referenced by name using the `--preset` flag (e.g., `--preset default`).
