"""Canonical ultra-marathon food & nutrient library (offline, pure Python).

Shared with ``frontend/food_library.js`` so both autocomplete search and the
fueling engine know calories, sodium, potassium, magnesium, caffeine,
portability, and heat sensitivity for every trail & aid-station food.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class FoodProfile:
    key: str
    name: str
    carbs_g: float
    kcal: float
    sodium_mg: float
    potassium_mg: float
    magnesium_mg: float
    caffeine_mg: float
    flavor: str
    form: str  # gel, liquid, solid
    portable: bool
    heat_sensitive: bool
    scopes: tuple[str, ...]
    aliases: tuple[str, ...]


FOOD_PROFILES: dict[str, FoodProfile] = {
    'gel_25': FoodProfile('gel_25', 'Standard Gel', 25.0, 100.0, 50.0, 20.0, 0.0, 0.0, 'Sweet', 'gel', True, False, ('vest', 'station'), ('energy gel', 'gel', 'gu')),
    'iso_gel_22': FoodProfile('iso_gel_22', 'Isotonic Gel', 22.0, 88.0, 40.0, 15.0, 0.0, 0.0, 'Sweet', 'gel', True, False, ('vest', 'station'), ('sis', 'iso gel', 'isotonic')),
    'high_carb_gel': FoodProfile('high_carb_gel', 'High-Carb Gel', 40.0, 160.0, 80.0, 25.0, 0.0, 0.0, 'Sweet', 'gel', True, False, ('vest',), ('40g gel', 'beta fuel', 'high carb')),
    'caffeine_gel': FoodProfile('caffeine_gel', 'Caffeine Gel', 25.0, 100.0, 50.0, 20.0, 0.0, 100.0, 'Sweet', 'gel', True, False, ('vest',), ('caf gel', 'caffeine', 'espresso gel')),
    'maurten_100': FoodProfile('maurten_100', 'Maurten Gel 100', 25.0, 100.0, 85.0, 0.0, 0.0, 0.0, 'Sweet', 'gel', True, False, ('vest',), ('maurten', 'hydrogel')),
    'maurten_caf': FoodProfile('maurten_caf', 'Maurten Gel 100 Caf', 25.0, 100.0, 55.0, 0.0, 0.0, 100.0, 'Sweet', 'gel', True, False, ('vest',), ('maurten caf', 'maurten caffeine')),
    'maurten_160': FoodProfile('maurten_160', 'Maurten Gel 160', 40.0, 160.0, 80.0, 0.0, 0.0, 0.0, 'Sweet', 'gel', True, False, ('vest',), ('maurten 160', 'hydrogel 40')),
    'sis_beta': FoodProfile('sis_beta', 'SiS Beta Fuel Gel', 40.0, 160.0, 30.0, 0.0, 0.0, 0.0, 'Sweet', 'gel', True, False, ('vest',), ('sis beta', 'beta fuel')),
    'sis_electrolyte': FoodProfile('sis_electrolyte', 'SiS Electrolyte Gel', 22.0, 87.0, 118.0, 10.0, 2.0, 0.0, 'Sweet', 'gel', True, False, ('vest',), ('sis salt', 'electrolyte gel', 'salted gel')),
    'precision_30': FoodProfile('precision_30', 'Precision Fuel 30 Gel', 30.0, 120.0, 0.0, 0.0, 0.0, 0.0, 'Sweet', 'gel', True, False, ('vest',), ('pf30', 'precision hydration')),
    'gu_roctane': FoodProfile('gu_roctane', 'GU Roctane Gel', 21.0, 100.0, 125.0, 55.0, 0.0, 35.0, 'Sweet', 'gel', True, False, ('vest',), ('gu', 'roctane')),
    'maple_gel': FoodProfile('maple_gel', 'Maple Syrup / Untapped Gel', 26.0, 100.0, 5.0, 80.0, 10.0, 0.0, 'Sweet', 'gel', True, False, ('vest',), ('maple', 'untapped', 'syrup')),
    'honey_packet': FoodProfile('honey_packet', 'Honey Sachet / Stick', 17.0, 64.0, 1.0, 10.0, 1.0, 0.0, 'Sweet', 'gel', True, False, ('vest', 'station'), ('honey', 'med')),
    'energy_chews': FoodProfile('energy_chews', 'Energy Chews (3 pcs)', 24.0, 100.0, 70.0, 20.0, 0.0, 0.0, 'Sweet', 'gel', True, False, ('vest', 'station'), ('chews', 'bloks', 'clif bloks', 'skratch chews')),
    'fruit_puree': FoodProfile('fruit_puree', 'Fruit Puree / Applesauce Pouch', 18.0, 75.0, 10.0, 140.0, 8.0, 0.0, 'Sweet', 'gel', True, False, ('vest', 'station'), ('puree', 'applesauce', 'baby food', 'pouch')),
    'drink_30': FoodProfile('drink_30', 'Drink Mix', 30.0, 120.0, 250.0, 60.0, 15.0, 0.0, 'Sweet', 'liquid', True, False, ('vest',), ('carb drink', 'tailwind', 'maurten drink', 'mix')),
    'electrolyte_drink': FoodProfile('electrolyte_drink', 'Electrolyte Drink', 8.0, 35.0, 350.0, 120.0, 40.0, 0.0, 'Savoury/Salty', 'liquid', False, False, ('station',), ('electrolytes', 'iso', 'salt drink', 'nuun', 'hydralyte')),
    'isotonic_drink': FoodProfile('isotonic_drink', 'Isotonic Sports Drink', 16.0, 65.0, 180.0, 60.0, 15.0, 0.0, 'Sweet', 'liquid', False, False, ('station',), ('isotonic', 'isostar', 'powerade', 'gatorade')),
    'magnesium_shot': FoodProfile('magnesium_shot', 'Magnesium Shot', 3.0, 15.0, 15.0, 40.0, 250.0, 0.0, 'Sweet', 'liquid', True, False, ('station', 'vest'), ('magnesium', 'mag', 'ampoule', 'cramp', 'mg')),
    'magnesium_tab': FoodProfile('magnesium_tab', 'Magnesium + Electrolyte Tab', 2.0, 10.0, 200.0, 100.0, 150.0, 0.0, 'Savoury/Salty', 'liquid', True, False, ('station', 'vest'), ('mag tab', 'effervescent', 'magnesium tablet', 'electrolyte tab')),
    'cola_broth': FoodProfile('cola_broth', 'Cola or warm broth', 18.0, 72.0, 250.0, 50.0, 10.0, 15.0, 'Sweet', 'liquid', False, False, ('station',), ('cola or broth',)),
    'cola': FoodProfile('cola', 'Flat Cola (200ml)', 21.0, 84.0, 10.0, 5.0, 0.0, 20.0, 'Sweet', 'liquid', False, False, ('station',), ('coca cola', 'coke', 'pepsi', 'soda')),
    'warm_broth': FoodProfile('warm_broth', 'Warm Broth / Bouillon', 4.0, 25.0, 650.0, 120.0, 15.0, 0.0, 'Savoury/Salty', 'liquid', False, False, ('station',), ('broth', 'bouillon', 'soup', 'salty soup', 'bulion')),
    'noodle_soup': FoodProfile('noodle_soup', 'Warm Noodle / Ramen Soup', 22.0, 120.0, 700.0, 140.0, 20.0, 0.0, 'Savoury/Salty', 'liquid', False, False, ('station',), ('soup', 'ramen', 'noodles', 'supa')),
    'pickle_juice': FoodProfile('pickle_juice', 'Pickle Juice Shot (60ml)', 1.0, 5.0, 520.0, 40.0, 10.0, 0.0, 'Savoury/Salty', 'liquid', False, False, ('station', 'vest'), ('pickle', 'cramp', 'brine', 'sour')),
    'sweet_tea': FoodProfile('sweet_tea', 'Sweet Warm Tea', 15.0, 60.0, 5.0, 30.0, 5.0, 25.0, 'Sweet', 'liquid', False, False, ('station',), ('tea', 'chai', 'lemon tea')),
    'iced_coffee': FoodProfile('iced_coffee', 'Coffee / Espresso Shot', 2.0, 10.0, 5.0, 90.0, 10.0, 75.0, 'Sweet', 'liquid', False, False, ('station',), ('coffee', 'espresso', 'kafe', 'caffeine')),
    'non_alc_beer': FoodProfile('non_alc_beer', 'Non-Alcoholic Beer (200ml)', 12.0, 52.0, 15.0, 80.0, 20.0, 0.0, 'Savoury/Salty', 'liquid', False, False, ('station',), ('beer', 'bira', 'alkoholfrei')),
    'coconut_water': FoodProfile('coconut_water', 'Coconut Water (250ml)', 11.0, 48.0, 65.0, 450.0, 25.0, 0.0, 'Sweet', 'liquid', False, False, ('station',), ('coconut', 'potassium')),
    'cucumber': FoodProfile('cucumber', 'Salted Cucumber', 3.0, 15.0, 320.0, 150.0, 15.0, 0.0, 'Savoury/Salty', 'solid', False, False, ('station',), ('cucumber', 'krastavitsa', 'salted cucumber')),
    'sour_pickles': FoodProfile('sour_pickles', 'Sour Pickles / Gherkins', 2.0, 12.0, 480.0, 75.0, 10.0, 0.0, 'Savoury/Salty', 'solid', False, False, ('station',), ('sour cucumber', 'pickles', 'gherkins', 'kiseli krastavichki', 'sour')),
    'salted_potatoes': FoodProfile('salted_potatoes', 'Salted Boiled Potatoes', 25.0, 115.0, 400.0, 520.0, 30.0, 0.0, 'Savoury/Salty', 'solid', True, False, ('station', 'vest'), ('potato', 'potatoes', 'kartofi', 'boiled potato')),
    'chips': FoodProfile('chips', 'Salted Chips', 22.0, 210.0, 300.0, 420.0, 25.0, 0.0, 'Savoury/Salty', 'solid', True, False, ('station', 'vest'), ('chips', 'crisps', 'potato chips', 'pringles')),
    'pretzels': FoodProfile('pretzels', 'Salted Pretzels / Soleti', 24.0, 115.0, 380.0, 45.0, 10.0, 0.0, 'Savoury/Salty', 'solid', True, False, ('station', 'vest'), ('pretzel', 'soleti', 'sticks', 'salty sticks')),
    'saltines': FoodProfile('saltines', 'Salted Crackers / TUC', 20.0, 140.0, 310.0, 40.0, 8.0, 0.0, 'Savoury/Salty', 'solid', True, False, ('station', 'vest'), ('crackers', 'tuc', 'saltines')),
    'olives': FoodProfile('olives', 'Salted Olives', 2.0, 55.0, 420.0, 25.0, 5.0, 0.0, 'Savoury/Salty', 'solid', False, False, ('station',), ('olives', 'maslini')),
    'tomato_salt': FoodProfile('tomato_salt', 'Salted Tomato Wedges', 4.0, 20.0, 280.0, 240.0, 12.0, 0.0, 'Savoury/Salty', 'solid', False, False, ('station',), ('tomato', 'domati')),
    'sandwich': FoodProfile('sandwich', 'Half Sandwich', 25.0, 175.0, 250.0, 90.0, 15.0, 0.0, 'Savoury/Salty', 'solid', True, False, ('vest', 'station'), ('sandwich', 'wrap', 'sandvich')),
    'cheese_sandwich': FoodProfile('cheese_sandwich', 'Cheese / Kashkaval Sandwich', 26.0, 220.0, 360.0, 85.0, 18.0, 0.0, 'Savoury/Salty', 'solid', True, False, ('station', 'vest'), ('cheese', 'kashkaval', 'grilled cheese')),
    'ham_sandwich': FoodProfile('ham_sandwich', 'Ham & Cheese Sandwich', 26.0, 210.0, 450.0, 120.0, 18.0, 0.0, 'Savoury/Salty', 'solid', True, False, ('station', 'vest'), ('ham', 'shunka', 'meat sandwich')),
    'pbj_sandwich': FoodProfile('pbj_sandwich', 'PB&J Half Sandwich', 32.0, 230.0, 210.0, 140.0, 35.0, 0.0, 'Sweet', 'solid', True, False, ('vest', 'station'), ('pbj', 'peanut butter', 'jam sandwich')),
    'rice_ball': FoodProfile('rice_ball', 'Salted Rice Ball / Onigiri', 30.0, 145.0, 320.0, 65.0, 15.0, 0.0, 'Savoury/Salty', 'solid', True, False, ('vest', 'station'), ('rice', 'onigiri', 'rice cake savoury')),
    'quesadilla': FoodProfile('quesadilla', 'Cheese Quesadilla / Tortilla', 28.0, 210.0, 380.0, 90.0, 18.0, 0.0, 'Savoury/Salty', 'solid', True, False, ('station', 'vest'), ('quesadilla', 'tortilla', 'wrap')),
    'banitsa': FoodProfile('banitsa', 'Banitsa / Cheese Pastry', 28.0, 250.0, 390.0, 75.0, 15.0, 0.0, 'Savoury/Salty', 'solid', True, False, ('station', 'vest'), ('banitsa', 'banica', 'pastry', 'cheese pie')),
    'pizza_slice': FoodProfile('pizza_slice', 'Cold Pizza Bite / Mini Slice', 26.0, 195.0, 420.0, 110.0, 18.0, 0.0, 'Savoury/Salty', 'solid', True, False, ('station', 'vest'), ('pizza',)),
    'pasta_cup': FoodProfile('pasta_cup', 'Salted Plain Pasta (cup)', 32.0, 155.0, 320.0, 70.0, 20.0, 0.0, 'Savoury/Salty', 'solid', False, False, ('station',), ('pasta', 'macaroni', 'noodles')),
    'cheese_cubes': FoodProfile('cheese_cubes', 'Cheese Cubes / Kashkaval', 2.0, 115.0, 260.0, 35.0, 10.0, 0.0, 'Savoury/Salty', 'solid', False, True, ('station',), ('cheese', 'kashkaval', 'sirene', 'parmesan')),
    'salami_slices': FoodProfile('salami_slices', 'Salami / Lukanka Slices', 1.0, 120.0, 480.0, 90.0, 8.0, 0.0, 'Savoury/Salty', 'solid', False, False, ('station',), ('salami', 'lukanka', 'sausage', 'bacon', 'cured meat')),
    'salted_nuts': FoodProfile('salted_nuts', 'Salted Peanuts / Cashews', 6.0, 175.0, 210.0, 210.0, 65.0, 0.0, 'Savoury/Salty', 'solid', True, False, ('station', 'vest'), ('nuts', 'peanuts', 'cashews', 'almonds', 'trail mix')),
    'banana': FoodProfile('banana', 'Banana', 22.0, 95.0, 1.0, 360.0, 27.0, 0.0, 'Sweet', 'solid', True, False, ('vest', 'station'), ('banana', 'banan')),
    'dates': FoodProfile('dates', 'Medjool Dates', 25.0, 100.0, 5.0, 240.0, 20.0, 0.0, 'Sweet', 'solid', True, False, ('vest', 'station'), ('dates', 'furmi', 'medjool')),
    'watermelon': FoodProfile('watermelon', 'Watermelon', 12.0, 50.0, 5.0, 180.0, 16.0, 0.0, 'Sweet', 'solid', False, False, ('station',), ('watermelon', 'dinya', 'melon')),
    'apple': FoodProfile('apple', 'Apple Wedges', 18.0, 72.0, 2.0, 150.0, 7.0, 0.0, 'Sweet', 'solid', True, False, ('station', 'vest'), ('apple', 'yabalka')),
    'orange': FoodProfile('orange', 'Orange Wedges', 15.0, 62.0, 1.0, 235.0, 14.0, 0.0, 'Sweet', 'solid', False, False, ('station',), ('orange', 'portokal', 'citrus')),
    'raisins': FoodProfile('raisins', 'Raisins / Dried Cranberries', 28.0, 115.0, 10.0, 260.0, 12.0, 0.0, 'Sweet', 'solid', True, False, ('vest', 'station'), ('raisins', 'stafidi', 'cranberries', 'dried fruit')),
    'dried_figs': FoodProfile('dried_figs', 'Dried Figs / Apricots', 26.0, 105.0, 8.0, 300.0, 28.0, 0.0, 'Sweet', 'solid', True, False, ('vest', 'station'), ('figs', 'apricots', 'smokini', 'kaisii')),
    'bar': FoodProfile('bar', 'Oat / Energy Bar', 30.0, 175.0, 100.0, 120.0, 35.0, 0.0, 'Sweet', 'solid', True, False, ('vest', 'station'), ('bar', 'oat bar', 'granola', 'corny')),
    'clif_bar': FoodProfile('clif_bar', 'Clif Bar / Flapjack', 42.0, 250.0, 200.0, 220.0, 55.0, 0.0, 'Sweet', 'solid', True, False, ('vest', 'station'), ('clif', 'flapjack', 'oatcake')),
    'waffle': FoodProfile('waffle', 'Stroopwafel / Energy Waffle', 24.0, 150.0, 110.0, 40.0, 8.0, 0.0, 'Sweet', 'solid', True, False, ('vest', 'station'), ('waffle', 'stroopwafel', 'stinger', 'vafla')),
    'rice_cake': FoodProfile('rice_cake', 'Sweet Rice Cake', 28.0, 150.0, 160.0, 60.0, 12.0, 0.0, 'Sweet', 'solid', True, False, ('vest', 'station'), ('rice cake', 'allen lim', 'portables')),
    'chocolate': FoodProfile('chocolate', 'Chocolate', 20.0, 190.0, 20.0, 150.0, 45.0, 10.0, 'Sweet', 'solid', True, True, ('vest', 'station'), ('chocolate', 'shokolad', 'milka')),
    'dark_chocolate': FoodProfile('dark_chocolate', 'Dark Chocolate 70%', 14.0, 170.0, 10.0, 210.0, 65.0, 25.0, 'Sweet', 'solid', True, True, ('vest', 'station'), ('dark chocolate', 'choko', 'cocoa')),
    'snickers': FoodProfile('snickers', 'Snickers / Peanut Choco Bar', 30.0, 240.0, 120.0, 150.0, 35.0, 0.0, 'Sweet', 'solid', True, True, ('vest', 'station'), ('snickers', 'mars', 'candy bar')),
    'gummy_bears': FoodProfile('gummy_bears', 'Gummy Bears / Haribo', 24.0, 100.0, 15.0, 0.0, 0.0, 0.0, 'Sweet', 'solid', True, False, ('vest', 'station'), ('gummies', 'haribo', 'jelly', 'candy')),
    'halva': FoodProfile('halva', 'Sesame Halva (piece)', 22.0, 210.0, 75.0, 130.0, 75.0, 0.0, 'Sweet', 'solid', True, False, ('vest', 'station'), ('halva', 'tahan', 'sesame')),
    'biscuits': FoodProfile('biscuits', 'Plain Biscuits / Petit Beurre', 25.0, 145.0, 130.0, 45.0, 10.0, 0.0, 'Sweet', 'solid', True, False, ('station', 'vest'), ('biscuits', 'cookies', 'biskviti', 'petit beurre')),
    'fig_bar': FoodProfile('fig_bar', 'Fig Bar / Soft Cookie', 28.0, 140.0, 110.0, 110.0, 18.0, 0.0, 'Sweet', 'solid', True, False, ('vest', 'station'), ('fig newton', 'fig bar', 'soft cookie')),
    'nutella_bread': FoodProfile('nutella_bread', 'Nutella / Jam Bread Bite', 28.0, 165.0, 90.0, 85.0, 18.0, 0.0, 'Sweet', 'solid', True, False, ('station', 'vest'), ('nutella', 'jam', 'bread', 'toast')),
    'sweet_potato': FoodProfile('sweet_potato', 'Roasted Sweet Potato', 24.0, 105.0, 180.0, 420.0, 28.0, 0.0, 'Sweet', 'solid', True, False, ('vest', 'station'), ('sweet potato', 'batat')),
    'muffin': FoodProfile('muffin', 'Mini Banana Muffin / Cake', 28.0, 170.0, 150.0, 110.0, 15.0, 0.0, 'Sweet', 'solid', True, False, ('station', 'vest'), ('muffin', 'cake', 'keks')),
}

_ALIAS_INDEX: list[tuple[str, FoodProfile]] = []
for _profile in FOOD_PROFILES.values():
    _ALIAS_INDEX.append((_profile.name.casefold(), _profile))
    for _alias in _profile.aliases:
        _ALIAS_INDEX.append((_alias.casefold(), _profile))
_ALIAS_INDEX.sort(key=lambda pair: -len(pair[0]))


def lookup_food(key: str = "", name: str = "") -> FoodProfile | None:
    """Resolve a food profile by catalog key, exact name, or alias substring."""

    clean_key = (key or "").strip().casefold()
    if clean_key in FOOD_PROFILES:
        return FOOD_PROFILES[clean_key]
    clean_name = (name or "").strip().casefold()
    if not clean_name:
        return None
    for term, profile in _ALIAS_INDEX:
        if clean_name == term:
            return profile
    for term, profile in _ALIAS_INDEX:
        if len(term) >= 4 and (term in clean_name or clean_name in term):
            return profile
    return None
