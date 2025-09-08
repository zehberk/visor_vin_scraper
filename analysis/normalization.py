import re

ENGINE_MARKERS = {"s", "turbo", "hybrid", "phev", "plug-in"}
BODY_STYLE_PAT = re.compile(
    r"\s+(Sedan|Coupe|Hatchback|Sport Utility|Crew Cab|Extended Cab|Van|Wagon|Pickup)\s+4D(\s+\d+(\s*/\s*\d+)?\s*ft)?$",
    re.IGNORECASE,
)
DISPLACEMENT_PAT = re.compile(r"^\d+(\.\d+)?[LS]?$", re.IGNORECASE)


def find_visor_key(norm_trim: str, visor_keys: list[str]) -> str | None:
    """
    Match a normalized KBB trim to visor keys with controlled fuzziness:
    1. Exact match against visor keys (ideal).
    2. If no match, progressively strip leading tokens from the KBB trim
       and only accept if the remainder matches a visor key exactly.
    3. If nothing matches, skip with a warning.
    """
    norm_trim_clean = norm_trim.lower().strip()
    visor_norm_map = {normalize_trim(vk).lower().strip(): vk for vk in visor_keys}

    # 1. Exact match
    if norm_trim_clean in visor_norm_map:
        return visor_norm_map[norm_trim_clean]

    # 2. Progressive prefix stripping
    tokens = norm_trim_clean.split()
    while len(tokens) > 1:
        tokens = tokens[1:]  # drop the first token
        candidate = " ".join(tokens)
        if candidate in visor_norm_map:
            return visor_norm_map[candidate]

    # 3. Nothing matched
    print(f"\tSkipping unmapped trim '{norm_trim}' (visor keys: {visor_keys})")
    return None


def normalize_trim(raw: str) -> str:
    # 1. strip body style
    name = BODY_STYLE_PAT.sub("", raw).strip()

    # 2. tokenize
    tokens = name.split()

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
    result_tokens.extend([t.title() for t in tokens])

    # 7. prepend engine markers if any
    if engine_markers:
        result_tokens = engine_markers + result_tokens

    return " ".join(result_tokens)


def strip_engine_tokens(tokens: list[str]) -> list[str]:
    return [t for t in tokens if t.lower() not in ENGINE_MARKERS]
