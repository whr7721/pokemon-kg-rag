import json
from pathlib import Path

RAW = Path("data/raw/data")

def load_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)

def pokemon_files():
    return sorted((RAW / "pokemon").glob("*.json"))

def load_move_list():
    return load_json(RAW / "move_list.json")

def load_ability_list():
    return load_json(RAW / "ability_list.json")

def load_national():
    return load_json(RAW / "pokedex" / "national.json")
