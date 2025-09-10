import re

from visor_scraper.constants import BASE_SUFFIXES

ENGINE_MARKERS = {"s", "turbo", "hybrid", "phev", "plug-in"}
DRIVETRAINS = {"4x4", "4wd", "2wd", "4xe"}
BODY_STYLE_PAT = re.compile(
    r"\s+(Sedan|Coupe|Hatchback|Sport Utility|SUV|Crew Cab|Extended Cab|Van|Wagon|Pickup)"
    r"\s+4D(\s+(\d+(\s+\d+/\d+)?)(\s*ft))?$",
    re.IGNORECASE,
)
DISPLACEMENT_PAT = re.compile(r"^\d+(\.\d+)?[LS]?$", re.IGNORECASE)


def find_visor_key(norm_trim: str, visor_keys: list[str], model: str) -> str | None:
    """
    Map a normalized KBB trim string to one of the visor keys.
    Handles:
      - Empty normalized trims (zero tokens) => 'Base'
      - Exact matches
      - Leading-token drop fallback
    """
    # Tokenize once
    toks = norm_trim.split()

    # 1. Special case: no tokens left after normalization
    if not toks and "Base" in visor_keys:
        return "Base"

        # special case: trim equals model name → Base
    if model and norm_trim.lower() == model.lower() and "Base" in visor_keys:
        return "Base"

    # 2. Exact match (case-insensitive)
    for vk in visor_keys:
        if norm_trim.lower() == vk.lower():
            return vk

    # 3. Leading-token drop fallback
    def tokenize(s: str) -> list[str]:
        return s.lower().split()

    norm_tokens = toks
    key_tokens = {vk: tokenize(vk) for vk in visor_keys}

    for vk, toks_vk in key_tokens.items():
        if len(norm_tokens) > 1 and norm_tokens[1:] == toks_vk:
            return vk
        if len(toks_vk) > 1 and toks_vk[1:] == norm_tokens:
            return vk

    return None


def normalize_trim(raw: str) -> str:
    name = raw.strip()
    # if the whole thing is just a body style suffix → Base
    if any(name.lower() == s.lower() for s in BASE_SUFFIXES):
        return ""

    # 1. strip body style
    name = BODY_STYLE_PAT.sub("", name).strip()
    # print(f"Raw name: {raw}, Stripped: {name}")

    # 2. tokenize
    tokens = name.split()

    # ✅ strip marketing prefixes like "All New" / "The All-New"
    if len(tokens) >= 2 and tokens[0].lower() == "all" and tokens[1].lower() == "new":
        tokens = tokens[2:]
    elif tokens and tokens[0].lower() == "all-new":
        tokens = tokens[1:]
    elif (
        len(tokens) >= 3
        and tokens[0].lower() == "the"
        and tokens[1].lower() == "all"
        and tokens[2].lower() == "new"
    ):
        tokens = tokens[3:]

    # 3. remove displacement if first
    if tokens and DISPLACEMENT_PAT.match(tokens[0]):
        tokens = tokens[1:]

    # 4. keep engine markers separate (Turbo, Hybrid, PHEV, etc.)
    engine_markers = []
    while tokens and tokens[0].lower() in {"turbo", "hybrid", "phev", "plug-in"}:
        marker = tokens.pop(0).lower()
        if marker == "phev":
            engine_markers.append("PHEV")
        elif marker == "plug-in":
            engine_markers.append("Plug-In")
        else:
            engine_markers.append(marker.title())

    # 5. special handling for "S"
    result_tokens = []
    if tokens and tokens[0].upper() == "S":
        result_tokens.append("S")
        tokens = tokens[1:]

    # 6. add rest back
    result_tokens.extend([smart_title(t) for t in tokens])

    # 7. prepend engine markers if any
    if engine_markers:
        result_tokens = engine_markers + result_tokens

    return " ".join(result_tokens)


def strip_engine_tokens(tokens: list[str]) -> list[str]:
    return [t for t in tokens if t.lower() not in ENGINE_MARKERS]


def resolve_cache_key(raw_title: str, cache_entries: dict[str, dict]) -> str:
    raw_title = raw_title.strip()
    # print("DEBUG resolve_cache_key: raw_title =", raw_title)

    year, make, model, *trim_parts = raw_title.split(maxsplit=3)
    raw_trim = trim_parts[0] if trim_parts else ""
    norm_trim = normalize_trim(raw_trim)

    # find any cache key with same year/make/model and normalized trim match
    related_keys = [
        k for k in cache_entries.keys() if k.startswith(f"{year} {make} {model}")
    ]
    for k in related_keys:
        # print("DEBUG compare:", k, "vs", norm_trim)

        if k.startswith(f"{year} {make} {model}"):
            cache_trim = k.replace(f"{year} {make} {model}", "").strip()
            if normalize_trim(cache_trim) == norm_trim:
                return k

    # fallback: case-insensitive whole string match
    for k in cache_entries.keys():
        if k.lower() == raw_title.lower():
            return k

    # if nothing matched, return the raw_title so the caller can handle KeyError
    return raw_title


def canonicalize_trim(raw_trim: str, model: str) -> str:
    """
    Normalize and apply consistent Title Case for trims across cache + listings.
    Removes body styles/displacements via normalize_trim first.
    """
    norm_trim = normalize_trim(raw_trim)
    if not norm_trim:
        return "Base"
        # if KBB trim is just the model name (e.g. "Forester"), map to Base
    if model and norm_trim.lower() == model.lower():
        return "Base"
    return " ".join(smart_title(p) for p in norm_trim.split())


def smart_title(token: str) -> str:
    # preserve all-caps for 3-letter codes or anything with a hyphen
    if len(token) <= 3 and token.isalpha():
        return token.upper()
    # Preserve casing for drivetrain / alphanumeric codes like 4x4, 4WD, etc.
    if token.lower() in DRIVETRAINS:
        return token
    if "-" in token:
        return token.upper()
    return token.title()
