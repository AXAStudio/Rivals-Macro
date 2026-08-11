"""Weapon template catalogue, derived from the template filenames."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

SLOTS = ("primary", "secondary", "melee", "utility")
SLOT_LABELS = {
    "primary": "Primary",
    "secondary": "Secondary",
    "melee": "Melee",
    "utility": "Utility",
}
RANDOM_CARD = "random_card"


@dataclass(frozen=True)
class Weapon:
    slot: str
    key: str  # "energy_rifle"
    template: str  # "weapon_primary_energy_rifle"
    label: str  # "Energy Rifle"


def discover(templates_dir: Path) -> dict[str, list[Weapon]]:
    """Map slot -> weapons, parsed from ``weapon_<slot>_<key>.png`` names."""
    catalogue: dict[str, list[Weapon]] = {slot: [] for slot in SLOTS}
    for path in sorted(templates_dir.glob("weapon_*.png")):
        parts = path.stem.split("_")
        if len(parts) < 3 or parts[1] not in catalogue:
            continue
        slot, key = parts[1], "_".join(parts[2:])
        catalogue[slot].append(
            Weapon(slot=slot, key=key, template=path.stem, label=key.replace("_", " ").title())
        )
    return catalogue


def template_names(catalogue: dict[str, list[Weapon]], slot: str, choice: str) -> list[str]:
    """Templates to hunt for, in priority order, for one loadout slot."""
    weapons = catalogue.get(slot, [])
    if choice == "random":
        return [RANDOM_CARD]
    if choice == "any":
        return [w.template for w in weapons]
    for weapon in weapons:
        if weapon.key == choice:
            return [weapon.template]
    return [RANDOM_CARD]


def all_templates(catalogue: dict[str, list[Weapon]]) -> list[str]:
    return [w.template for slot in SLOTS for w in catalogue.get(slot, [])]
