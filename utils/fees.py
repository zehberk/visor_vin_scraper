from unidecode import unidecode

from utils.constants import *


def has_fee_signal(text: str) -> bool:
    """
    Determines if a block of text has any mention or context for fees.
    """
    return (
        "$" in text
        or FEE_SIGNAL_RE.search(text) is not None
        or NO_FEE_RE.search(text) is not None
    )


def decode_unicode(text: str) -> str:
    text = text.replace("\\/", "/")
    text = text.encode("utf-8").decode("unicode_escape", errors="ignore")
    return unidecode(text)


def clean_token(token: str) -> str:
    token = LEADING_NON_LETTER_RE.sub("", token)
    token = TRAILING_NON_LETTER_RE.sub("", token)
    return token


def extract_fee_label(text: str) -> str | None:
    tokens = [clean_token(t) for t in text.split()]
    tokens = [t for t in tokens if t]

    for i, tok in enumerate(tokens):
        if tok in FEE_WORDS:
            start = max(0, i - 2)
            label = tokens[start : i + 1]

            # Reject bare fee words like "fee" or "cost"
            if len(label) == 1:
                return None

            return " ".join(label)

    return None


def shorten_fee_text(text: str, dollar_text: str | None) -> str | None:
    if dollar_text:
        text = re.sub(re.escape(dollar_text), " ", text)
    text = text.lower()
    text = MARKUP_RE.sub(" ", text)
    text = FUNCTION_WORDS_RE.sub(" ", text)
    text = PRICE_SCOPE_RE.sub(" ", text)
    text = APPLICABILITY_RE.sub(" ", text)
    text = COLLECTION_CONTEXT_RE.sub(" ", text)

    text = NEGATION_REMNANTS_RE.sub(" ", text)
    text = DANGLING_FILLERS_RE.sub(" ", text)
    text = DISCLAIMERS_MARKETING_RE.sub(" ", text)

    text = VEHICLE_CONTEXT_RE.sub(" ", text)
    text = NON_LETTER_RE.sub(" ", text)
    text = WHITESPACE_RE.sub(" ", text)

    return extract_fee_label(text.strip())


# def parse_fee_snippets(html: str) -> list[tuple[str, float, bool | None]]:
def parse_fee_snippets(snippets: list[str]) -> list[tuple[str, float, bool | None]]:

    all_fees: list[tuple[str, float, bool | None]] = []
    for snippet in snippets:
        if not has_fee_signal(snippet):
            continue

        dealer_text = decode_unicode(snippet)
        sentences = [s for s in SENTENCE_RE.split(dealer_text) if has_fee_signal(s)]

        included: bool | None = None
        for sentence in sentences:
            if any(keyword in sentence.lower() for keyword in EXCLUDE_KEYWORDS):
                included = False

            sections = [s for s in SEMICOLON_RE.split(sentence) if has_fee_signal(s)]

            subsections: list[str] = []
            for section in sections:
                if section.count("$") > 1:  # More than 1 dollar amount found
                    # May want to use pattern = re.compile(r"\$[0-9][0-9,]*(?:\.\d+)?") instead
                    subsections.extend(CONJUNCTION_RE.split(section))
                else:
                    subsections.append(section)

            for sub in subsections:
                for part in CLAUSE_SPLIT_RE.split(sub):
                    part = part.strip()
                    if not part:
                        continue

                    if NO_FEE_RE.search(part):  # Check for no dealer fee phrases
                        fee_text = shorten_fee_text(part, "$0")
                        if fee_text:
                            all_fees.append((fee_text, 0, True))
                        continue

                    if RATE_SLASH_RE.search(part) and not FEE_WORD_RE.search(part):
                        continue  # Detect if the dollar amount is attached to a rate, nto a fee

                    if QUOTED_KV_RE.search(part):
                        continue  # Skip "key": "value" structures

                    max_num: float | None = None
                    num_str: str | None = None
                    for m in DOLLAR_RE.finditer(part):
                        if (
                            RATE_RE.search(part)
                            or PAYMENT_RE.search(part)
                            or DOWN_RE.search(part)
                            or CREDIT_RE.search(part)
                            or WARRANTY_RE.search(part)
                            or UPSELL_RE.search(part)
                            or FIRST_PERSON_RE.search(part)
                        ):
                            continue  # Skip amounts related to financing, warranties, etc

                        dollar_str = m.group(0)
                        num = float(dollar_str.replace("$", "").replace(",", ""))
                        if max_num is None or num > max_num:
                            max_num = num
                            num_str = dollar_str

                    if max_num is None or max_num > 2000:  # arbitrary limit
                        continue

                    if max_num == 0 or any(k in part.lower() for k in INCLUDE_KEYWORDS):
                        included = True

                    if max_num <= 2000:
                        fee_text = shorten_fee_text(part, num_str)
                        if fee_text:
                            all_fees.append((fee_text, round(max_num, 2), included))

    return all_fees
