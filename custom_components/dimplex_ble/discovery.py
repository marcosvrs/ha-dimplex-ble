"""Recognize supported Dimplex panel-heater advertisements."""

from .const import ADVERTISEMENT_SUFFIX


def is_dimplex_advertisement(name: str | None) -> bool:
    """Match the manifest: ``AL``/``AM`` and 4 digits, with or without ``<Dimplex>``.

    Other Dimplex products also advertise ``<Dimplex>`` (e.g. ``FXnnnn``
    fan heaters) and must not be offered as panel heaters.
    """
    label = advertisement_label(name)
    if label is None:
        return False
    normalized = label.casefold()
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
