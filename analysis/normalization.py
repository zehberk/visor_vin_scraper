from difflib import SequenceMatcher

from analysis.models import TrimProfile


def get_token_score(
    visor: TrimProfile,
    kbb: TrimProfile,
    compare_engine: bool,
    compare_drivetrain: bool,
    compare_body: bool,
    compare_bed: bool,
) -> int:
    score = 0

    # token overlap
    score += len(set(visor.tokens) & set(kbb.tokens))

    # conditional comparisons
    if compare_engine and visor.engine and kbb.engine and visor.engine == kbb.engine:
        score += 1
    if (
        compare_drivetrain
        and visor.drivetrain
        and kbb.drivetrain
        and visor.drivetrain == kbb.drivetrain
    ):
        score += 1
    if (
        compare_body
        and visor.body_style
        and kbb.body_style
        and visor.body_style == kbb.body_style
    ):
        score += 1
    if (
        compare_bed
        and visor.bed_length
        and kbb.bed_length
        and visor.bed_length == kbb.bed_length
    ):
        score += 1

    return score


def get_sequence_score(
    visor: TrimProfile,
    kbb: TrimProfile,
    compare_engine: bool,
    compare_drivetrain: bool,
    compare_body: bool,
    compare_bed: bool,
) -> float:
    visor_string = visor.build_compare_string(
        compare_engine, compare_drivetrain, compare_body, compare_bed
    )
    kbb_string = kbb.build_compare_string(
        compare_engine, compare_drivetrain, compare_body, compare_bed
    )

    return SequenceMatcher(None, visor_string, kbb_string).ratio()


def best_kbb_match(visor_trim: str, kbb_trims: list[str]) -> str | None:
    if not visor_trim or not kbb_trims:
        return None

    # 1. 'Base' models
    if visor_trim.lower() == "base":
        return kbb_trims[0]  # will always be the cheapest trim

    # 2. Exact trim match
    for k in kbb_trims:
        if k.lower() == visor_trim.lower():
            return k

    visor_profile = TrimProfile.from_string(visor_trim)
    kbb_profiles: list[TrimProfile] = [TrimProfile.from_string(k) for k in kbb_trims]
    compare_engine = len({p.engine for p in kbb_profiles}) > 1
    compare_drivetrain = len({p.drivetrain for p in kbb_profiles}) > 1
    compare_body = len({p.body_style for p in kbb_profiles}) > 1
    compare_bed = len({p.bed_length for p in kbb_profiles}) > 1

    best_trim = None
    best_token_score = -1
    best_ratio = -1.0

    # 3. Scoring
    for kbb in kbb_profiles:
        # Score tokens first
        score = get_token_score(
            visor_profile,
            kbb,
            compare_engine,
            compare_drivetrain,
            compare_body,
            compare_bed,
        )
        if score > best_token_score:
            best_token_score = score
            best_ratio = get_sequence_score(
                visor_profile,
                kbb,
                compare_engine,
                compare_drivetrain,
                compare_body,
                compare_bed,
            )
            best_trim = kbb.full_trim
        elif score == best_token_score:
            # Score sequences second
            ratio = get_sequence_score(
                visor_profile,
                kbb,
                compare_engine,
                compare_drivetrain,
                compare_body,
                compare_bed,
            )
            if ratio > best_ratio:
                best_ratio = ratio
                best_trim = kbb.full_trim

    return best_trim
