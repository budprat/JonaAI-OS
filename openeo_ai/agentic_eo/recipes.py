# ABOUTME: Loads decision thresholds as data from recipes/*.json so policy numbers (dispatch/
# ABOUTME: drought/burn gates + scene-mask floor) live in one versioned place, not scattered literals.

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Dict, Optional, Tuple

from pydantic import BaseModel, Field

from .models import AnalysisType, Variable

_RECIPES_DIR = Path(__file__).parent / "recipe_packs"


class Threshold(BaseModel):
    """A single-valued numeric decision magnitude — the only thing that is 'data'."""

    value: float
    unit: str = ""
    comparator: str = ""   # informational; the code branch enforces direction
    applies_at: str = ""
    source: str = ""


class Recipe(BaseModel):
    """Per-variable thresholds. Applied to every (variable × analysis_type) cell of that variable;
    the cell identity rides on the plan as recipe_ref for the Method Card."""

    variable: Variable
    recipe_version: int = 1
    thresholds: Dict[str, Threshold] = Field(default_factory=dict)

    def recipe_ref(self, analysis_type: AnalysisType) -> str:
        return f"{self.variable.value}_{analysis_type.value}"


class Registry:
    """Authoritative, read-only view over the recipe pack. Thresholds are keyed by variable
    (one home each); recipe_ref names the applied cell for provenance."""

    def __init__(self, min_valid_fraction: float, recipes: Dict[Variable, Recipe]):
        self._min_valid_fraction = min_valid_fraction
        self._recipes = recipes

    @property
    def min_valid_fraction(self) -> float:
        return self._min_valid_fraction

    def get(self, variable: Variable, analysis_type: AnalysisType) -> Optional[Recipe]:
        return self._recipes.get(variable)

    def recipe_ref(self, variable: Variable, analysis_type: AnalysisType) -> Optional[str]:
        recipe = self._recipes.get(variable)
        return recipe.recipe_ref(analysis_type) if recipe else None

    def parse_recipe_id(self, recipe_id: str) -> Optional[Tuple[Variable, AnalysisType]]:
        """Reverse of recipe_ref: '{variable}_{analysis_type}' → the typed cell, or None.
        Variables and analysis types carry no underscores, so the first '_' splits cleanly."""
        if not recipe_id or "_" not in recipe_id:
            return None
        var_s, _, at_s = recipe_id.partition("_")
        try:
            return Variable(var_s), AnalysisType(at_s)
        except ValueError:
            return None

    def threshold(self, variable: Variable, name: str) -> float:
        """Return the scalar for a named gate. Fail-closed: unknown variable/name raises,
        so a mis-wired consumer surfaces loudly instead of silently changing behavior."""
        recipe = self._recipes.get(variable)
        if recipe is None or name not in recipe.thresholds:
            raise KeyError(f"no threshold '{name}' for variable {variable}")
        return recipe.thresholds[name].value


@lru_cache(maxsize=1)
def load_registry() -> Registry:
    defaults = json.loads((_RECIPES_DIR / "_defaults.json").read_text())
    recipes: Dict[Variable, Recipe] = {}
    for path in sorted(_RECIPES_DIR.glob("*.json")):
        if path.name.startswith("_"):
            continue
        recipe = Recipe(**json.loads(path.read_text()))
        recipes[recipe.variable] = recipe
    return Registry(
        min_valid_fraction=float(defaults["scene_gate"]["min_valid_fraction"]),
        recipes=recipes,
    )


REGISTRY = load_registry()
