"""Unit tests for deterministic nutrition math, Atwater, yield, units, and resolver."""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import aiohttp
import pytest

from custom_components.ha_calories_buddy.core.models import (
    ConfidenceLevel,
    FoodItem,
    FoodSource,
    LabelStandard,
    Macros,
    PersonFoodOverride,
    PersonProfile,
    PreparationState,
    WarningCode,
)
from custom_components.ha_calories_buddy.core.nutrition.atwater import check_atwater
from custom_components.ha_calories_buddy.core.nutrition.calculator import (
    compute_macros_for_grams,
    sum_macros,
)
from custom_components.ha_calories_buddy.core.nutrition.providers.open_food_facts import (
    OpenFoodFactsProvider,
)
from custom_components.ha_calories_buddy.core.nutrition.providers.usda import USDAProvider
from custom_components.ha_calories_buddy.core.nutrition.resolver import FoodResolver
from custom_components.ha_calories_buddy.core.nutrition.units import (
    convert_to_grams,
    normalize_unit,
)
from custom_components.ha_calories_buddy.core.nutrition.yield_factors import (
    convert_to_reference_grams,
    resolve_preparation_state,
)
from custom_components.ha_calories_buddy.core.storage.database import Database
from custom_components.ha_calories_buddy.core.storage.food_repo import FoodRepository
from custom_components.ha_calories_buddy.core.storage.profile_repo import ProfileRepository
from custom_components.ha_calories_buddy.core.storage.seeder import (
    normalize_text,
    seed_database_if_empty,
)
from tests.core.http_mock import FakeClientSession


class _StubLLMEstimator:
    async def estimate_food(
        self,
        name: str,
        brand: str | None,
        preparation_state: PreparationState,
        language: str,
    ) -> FoodItem | None:
        return FoodItem(
            food_id="llm_est_1",
            name=name,
            normalized_name=normalize_text(name),
            brand=brand,
            category="other",
            preparation_state=(
                PreparationState.READY_TO_EAT
                if preparation_state == PreparationState.UNSPECIFIED
                else preparation_state
            ),
            macros_per_100g=Macros(kcal=250.0, protein_g=10.0, carbs_g=30.0, fat_g=10.0),
            source=FoodSource.LLM_ESTIMATED,
        )


def test_atwater_eu_vs_us_and_warning() -> None:
    """Verify EU vs US fiber handling and Atwater typo detection."""
    eu_macros = Macros(kcal=116.0, protein_g=9.0, carbs_g=16.0, fat_g=0.4, fiber_g=7.9)
    res_eu = check_atwater(eu_macros, LabelStandard.EU)
    assert res_eu.warning is False

    us_macros = Macros(kcal=148.0, protein_g=10.0, carbs_g=24.0, fat_g=4.0, fiber_g=12.0)
    res_us = check_atwater(us_macros, LabelStandard.US)
    assert res_us.warning is False

    bad_macros = Macros(kcal=900.0, protein_g=10.0, carbs_g=5.0, fat_g=1.0)
    res_bad = check_atwater(bad_macros, LabelStandard.EU)
    assert res_bad.warning is True

    espresso = Macros(kcal=2.0, protein_g=0.1, carbs_g=0.0, fat_g=0.0)
    assert check_atwater(espresso, LabelStandard.EU).warning is False


def test_yield_conversions_and_ambiguity() -> None:
    """Verify raw/cooked conversions and category ambiguity heuristics."""
    ref_g = convert_to_reference_grams(200.0, PreparationState.COOKED, PreparationState.RAW, 2.5)
    assert ref_g == pytest.approx(80.0)

    ref_g_cooked = convert_to_reference_grams(
        100.0, PreparationState.RAW, PreparationState.COOKED, 2.4
    )
    assert ref_g_cooked == pytest.approx(240.0)

    state_rice, amb_rice = resolve_preparation_state(
        "rice", 80.0, PreparationState.UNSPECIFIED, PreparationState.RAW
    )
    assert state_rice == PreparationState.RAW
    assert amb_rice is False

    _, amb_big_rice = resolve_preparation_state(
        "rice", 260.0, PreparationState.UNSPECIFIED, PreparationState.RAW
    )
    assert amb_big_rice is True

    _, amb_legumes = resolve_preparation_state(
        "legumes", 250.0, PreparationState.UNSPECIFIED, PreparationState.COOKED
    )
    assert amb_legumes is True


def test_units_and_calculator() -> None:
    """Verify Italian/English unit normalization and macro math."""
    assert normalize_unit("cucchiaio") == "tbsp"
    assert normalize_unit("cucchiaini") == "tsp"
    assert normalize_unit("grammi") == "g"

    grams, unknown = convert_to_grams(2.0, "cucchiai", None)
    assert grams == 30.0
    assert unknown is False

    ml_grams, ml_unknown = convert_to_grams(200.0, "ml", None)
    assert ml_grams == 200.0
    assert ml_unknown is False

    _, unknown_custom = convert_to_grams(1.0, "manciata_ignota", None)
    assert unknown_custom is True

    m1 = compute_macros_for_grams(
        Macros(kcal=352.0, protein_g=8.5, carbs_g=77.7, fat_g=0.5, fiber_g=1.4), 80.0
    )
    assert m1.kcal == pytest.approx(281.6)
    total = sum_macros([m1, Macros(kcal=88.4, protein_g=0.0, carbs_g=0.0, fat_g=9.8)])
    assert total.kcal == pytest.approx(370.0)


@pytest.fixture
async def seeded_db(tmp_path: Path) -> AsyncIterator[Database]:
    db = Database(tmp_path / "nutrition.db")
    await db.initialize()
    await seed_database_if_empty(db)
    profile_repo = ProfileRepository(db)
    await profile_repo.upsert_person(
        PersonProfile(person_id="p_nico", config_entry_id="e_nico", display_name="Nico")
    )
    try:
        yield db
    finally:
        await db.close()


async def test_signature_lunch_scenario_with_off_and_seed(seeded_db: Database) -> None:
    """Test '80g riso basmati Scotti, 1 cucchiaio olio di oliva, 250g lenticchie cotte'."""
    food_repo = FoodRepository(seeded_db)
    http = FakeClientSession()
    http.add_get(
        "https://world.openfoodfacts.org/cgi/search.pl",
        payload={
            "products": [
                {
                    "code": "8001800001111",
                    "product_name_it": "Riso Basmati",
                    "brands": "Scotti",
                    "categories_tags": ["en:rices", "en:basmati-rice"],
                    "nutriments": {
                        "energy-kcal_100g": 352,
                        "proteins_100g": 8.5,
                        "carbohydrates_100g": 77.7,
                        "fat_100g": 0.5,
                        "fiber_100g": 1.4,
                    },
                }
            ]
        },
        repeat=True,
    )

    off = OpenFoodFactsProvider(http.as_aiohttp_session())
    resolver = FoodResolver(food_repo, off_provider=off)

    rice = await resolver.resolve_item(
        person_id="p_nico",
        name="riso basmati",
        brand="Scotti",
        quantity=80.0,
        unit="g",
        state=PreparationState.UNSPECIFIED,
        language="it",
    )
    oil = await resolver.resolve_item(
        person_id="p_nico",
        name="olio di oliva",
        quantity=1.0,
        unit="cucchiaio",
        state=PreparationState.UNSPECIFIED,
        language="it",
    )
    lentils = await resolver.resolve_item(
        person_id="p_nico",
        name="lenticchie cotte",
        quantity=250.0,
        unit="g",
        state=PreparationState.COOKED,
        language="it",
    )
    ambiguous_lentils = await resolver.resolve_item(
        person_id="p_nico",
        name="lenticchie",
        quantity=250.0,
        unit="g",
        state=PreparationState.UNSPECIFIED,
        language="it",
    )

    assert rice.brand == "Scotti"
    assert rice.confidence == ConfidenceLevel.HIGH
    assert rice.macros.kcal == pytest.approx(281.6)
    assert rice.warning_code is None

    assert oil.resolved_grams_ref == 10.0
    assert oil.macros.kcal == pytest.approx(88.4)
    assert oil.confidence == ConfidenceLevel.HIGH

    assert lentils.resolved_grams_ref == 250.0
    assert lentils.macros.kcal == pytest.approx(290.0)
    assert lentils.confidence == ConfidenceLevel.HIGH
    assert lentils.warning_code is None

    assert ambiguous_lentils.warning_code == WarningCode.AMBIGUOUS_STATE
    assert ambiguous_lentils.confidence == ConfidenceLevel.MEDIUM

    # Custom person portion override changes 1 cucchiaio oil from 10g to 12g
    await food_repo.upsert_person_override(
        PersonFoodOverride(
            override_id="ovr_oil",
            person_id="p_nico",
            food_id="seed_olive_oil",
            custom_portions={"tbsp": 12.0},
        )
    )
    oil_custom = await resolver.resolve_item(
        person_id="p_nico",
        name="olio di oliva",
        quantity=1.0,
        unit="cucchiaio",
        language="it",
    )
    assert oil_custom.resolved_grams_ref == 12.0


async def test_barcode_atwater_usda_and_llm_fallback(seeded_db: Database) -> None:
    """Verify barcode caching, Atwater warning propagation, USDA, and LLM fallback."""
    food_repo = FoodRepository(seeded_db)
    http = FakeClientSession()
    http.add_get(
        "https://world.openfoodfacts.org/api/v2/product/1234567890123.json",
        payload={
            "status": 1,
            "product": {
                "code": "1234567890123",
                "product_name": "Weird Protein Bar",
                "brands": "TestBrand",
                "serving_quantity": 50,
                "nutriments": {
                    "energy-kcal_100g": 850,
                    "proteins_100g": 20.0,
                    "carbohydrates_100g": 15.0,
                    "fat_100g": 5.0,
                },
            },
        },
    )
    http.add_get(
        "https://world.openfoodfacts.org/cgi/search.pl",
        payload={"products": []},
        repeat=True,
    )
    http.add_get(
        "https://api.nal.usda.gov/fdc/v1/foods/search",
        payload={
            "foods": [
                {
                    "fdcId": 999,
                    "description": "Wild Bison Steak Raw",
                    "foodNutrients": [
                        {"nutrientId": 1008, "value": 109},
                        {"nutrientId": 1003, "value": 21.6},
                        {"nutrientId": 1005, "value": 0.0},
                        {"nutrientId": 1004, "value": 1.8},
                    ],
                }
            ]
        },
    )
    http.add_get(
        "https://api.nal.usda.gov/fdc/v1/foods/search",
        payload={"foods": []},
    )

    off = OpenFoodFactsProvider(http.as_aiohttp_session())
    usda = USDAProvider(http.as_aiohttp_session(), api_key="demo_key")
    resolver = FoodResolver(food_repo, off_provider=off, usda_provider=usda)

    bar_item = await resolver.resolve_item(
        person_id="p_nico",
        name="Weird Protein Bar",
        barcode="1234567890123",
        quantity=50.0,
        unit="g",
    )
    assert bar_item.warning_code == WarningCode.ATWATER_WARNING
    assert bar_item.food_id == "off_1234567890123"

    bison = await resolver.resolve_item(
        person_id="p_nico",
        name="Wild Bison Steak",
        quantity=200.0,
        unit="g",
        state=PreparationState.RAW,
    )
    assert bison.food_source == FoodSource.USDA
    assert bison.macros.kcal == pytest.approx(218.0)

    llm_item = await resolver.resolve_item(
        person_id="p_nico",
        name="Grandma Secret Pastry",
        quantity=100.0,
        unit="g",
        llm_estimator=_StubLLMEstimator(),
    )
    assert llm_item.food_id is None
    assert llm_item.confidence == ConfidenceLevel.LOW
    assert llm_item.warning_code == WarningCode.LLM_ESTIMATED
    assert llm_item.macros.kcal == 250.0

    # Network error handling in providers never raises unhandled exceptions
    http.add_get(
        "https://world.openfoodfacts.org/api/v2/product/9999999999999.json",
        exc=aiohttp.ClientError("offline"),
    )
    assert await off.lookup_barcode("9999999999999") is None
