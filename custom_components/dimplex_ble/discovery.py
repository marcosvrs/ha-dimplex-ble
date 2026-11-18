"""Helpers for model-neutral Dimplex advertisement discovery."""

from .const import ADVERTISEMENT_SUFFIX


def is_dimplex_advertisement(name: str | None) -> bool:
    """Recognize observed Dimplex names without depending on a specific model."""
    if not name:
        return False
    normalized = name.casefold()
    if normalized.endswith(ADVERTISEMENT_SUFFIX.casefold()):
        return True
    return (
        len(normalized) == 6
        and normalized[:2] in {"al", "am"}
        and normalized[2:].isdigit()
    )


def advertisement_label(name: str | None) -> str | None:
    """Return the advertised local name without the Dimplex marker."""
    if not name:
        return None
    label = name.strip()
    suffix = ADVERTISEMENT_SUFFIX
    if label.casefold().endswith(suffix.casefold()):
        label = label[: -len(suffix)].strip()
    return label or None


