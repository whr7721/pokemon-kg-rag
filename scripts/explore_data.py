import json
from pathlib import Path

base = Path("data/raw/data")

for folder in ["pokemon", "moves", "abilities", "pokedex"]:
    files = sorted((base / folder).glob("*.json"))
    print(folder, "文件数 =", len(files))
    if files:
        first = files[0]
        with first.open(encoding="utf-8") as f:
            obj = json.load(f)
        print("  样例文件:", first.name)
        if isinstance(obj, dict):
            print("  字段:", ", ".join(list(obj.keys())[:24]))
        else:
            print("  类型:", type(obj).__name__, "条数 =", len(obj))

for name in ["move_list.json", "ability_list.json", "simple_pokedex.json"]:
    p = base.parent / name
    if p.exists():
        with p.open(encoding="utf-8") as f:
            obj = json.load(f)
        print(name, "类型:", type(obj).__name__, "条数 =", len(obj))
