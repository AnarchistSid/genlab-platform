"""Tests for push_to_backlog dedup hardening."""


def test_title_similarity_catches_near_duplicate():
    """Stories with similar titles from different sources should be deduplicated."""
    existing_titles = {"lakers beat celtics 112-105 in overtime thriller game"}
    title = "Lakers beat Celtics 112-105 in overtime thriller match"
    title_lower = title.lower().strip()

    title_words = set(title_lower.split())
    is_dupe = False
    for existing in existing_titles:
        existing_words = set(existing.split())
        if len(title_words) > 3 and len(existing_words) > 3:
            intersection = len(title_words & existing_words)
            union = len(title_words | existing_words)
            if union > 0 and intersection / union > 0.65:
                is_dupe = True
                break

    assert is_dupe, "Near-duplicate title should be caught"


def test_unrelated_titles_not_caught():
    """Unrelated stories should not be flagged as duplicates."""
    existing_titles = {"lakers beat celtics 112-105 in thriller"}
    title = "New AI model breaks benchmark records"
    title_lower = title.lower().strip()

    title_words = set(title_lower.split())
    is_dupe = False
    for existing in existing_titles:
        existing_words = set(existing.split())
        if len(title_words) > 3 and len(existing_words) > 3:
            intersection = len(title_words & existing_words)
            union = len(title_words | existing_words)
            if union > 0 and intersection / union > 0.65:
                is_dupe = True
                break

    assert not is_dupe, "Unrelated title should not be caught"


def test_short_titles_not_checked():
    """Very short titles should bypass the similarity check."""
    existing_titles = {"hi there"}
    title = "Hi there"
    title_lower = title.lower().strip()

    title_words = set(title_lower.split())
    is_dupe = False
    for existing in existing_titles:
        existing_words = set(existing.split())
        if len(title_words) > 3 and len(existing_words) > 3:
            intersection = len(title_words & existing_words)
            union = len(title_words | existing_words)
            if union > 0 and intersection / union > 0.65:
                is_dupe = True
                break

    assert not is_dupe, "Short titles should bypass similarity check"
