import pytest

from scdl.scdl import _VERSION_MARKERS, _normalize_track_name, _token_set_ratio

THRESHOLD = 90


def _is_match(uploader: str, title: str, existing: str) -> bool:
    candidate = f"{uploader} - {title}" if uploader else title
    candidate_n = _normalize_track_name(candidate)
    title_n = _normalize_track_name(title)
    existing_n = _normalize_track_name(existing)

    candidate_tokens = set(candidate_n.split())
    existing_tokens = set(existing_n.split())
    if (candidate_tokens & _VERSION_MARKERS) != (existing_tokens & _VERSION_MARKERS):
        return False

    score = max(_token_set_ratio(candidate_n, existing_n), _token_set_ratio(title_n, existing_n))
    return score >= THRESHOLD


@pytest.mark.parametrize(
    ("uploader", "title", "existing"),
    [
        ("DJ Snake", "Magenta Riddim", "045. DJ Snake - Magenta Riddim (Official Audio)"),
        ("Anonymous Reup", "Some Track", "Original Uploader - Some Track [Free DL]"),
        ("Uploader", "Song Title ft. Other Artist", "Uploader - Song Title feat. Other Artist (Official Video)"),
        ("Uploader", "Café del Mar", "Uploader - Cafe Del Mar"),
    ],
)
def test_true_positive_matches(uploader, title, existing):
    assert _is_match(uploader, title, existing)


@pytest.mark.parametrize(
    ("uploader", "title", "existing"),
    [
        ("Uploader", "Song Title", "Uploader - Song Title (Remix)"),
        ("Uploader", "Song Title (Sped Up)", "Uploader - Song Title (Slowed)"),
        ("Uploader", "Intro", "OtherUploader - Intro (Reprise)"),
    ],
)
def test_true_negative_non_matches(uploader, title, existing):
    assert not _is_match(uploader, title, existing)


def test_version_marker_asymmetry_blocks_match_even_with_high_text_score():
    candidate_n = _normalize_track_name("Uploader - Song Title")
    existing_n = _normalize_track_name("Uploader - Song Title (Remix)")
    # token_set_ratio alone would treat this as a near-perfect subset match —
    # the asymmetry veto is what actually rejects it, not the text score.
    assert _token_set_ratio(candidate_n, existing_n) >= THRESHOLD
    assert not _is_match("Uploader", "Song Title", "Uploader - Song Title (Remix)")
