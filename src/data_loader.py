# -*- coding: utf-8 -*-
"""数据读取。"""
from __future__ import annotations
import json, os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RAW = Path(os.getenv("POKEMON_DATASET", str(ROOT / "data" / "raw" / "data")))

def load_json(path: Path):
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

if __name__ == "__main__":
    print("RAW =", RAW)
    print("pokemon 文件:", len(list(pokemon_files())))
