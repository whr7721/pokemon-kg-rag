import os
from dotenv import load_dotenv
from neo4j import GraphDatabase
import data_loader as dl

load_dotenv()

def get_driver():
    return GraphDatabase.driver(
        os.getenv("NEO4J_URL"),
        auth=(os.getenv("NEO4J_USER"), os.getenv("NEO4J_PASSWORD")),
    )

def batch(tx, query, rows, size=5000):
    for start in range(0, len(rows), size):
        tx.run(query, rows=rows[start:start + size])

def import_graph(
    tx,
    move_rows,
    ability_rows,
    pokemon_rows,
    has_type_rows,
    has_ability_rows,
    egg_group_rows,
    generation_rows,
    evolution_rows,
    learns_move_rows,
    strong_rows,
    resist_rows,
    immune_rows,
):
    tx.run("MATCH (n) DETACH DELETE n")
    tx.run("UNWIND range(1, 9) AS g MERGE (:Generation {num: g})")

    batch(
        tx,
        """
        UNWIND $rows AS row
        MERGE (m:Move {name_zh: row.name})
        SET m.name_en = row.name_en, m.name_ja = row.name_ja, m.type = row.type,
            m.category = row.category, m.power = row.power, m.accuracy = row.accuracy,
            m.pp = row.pp, m.description = row.description
        """,
        move_rows,
    )

    batch(
        tx,
        """
        UNWIND $rows AS row
        MERGE (a:Ability {name_zh: row.name})
        SET a.name_en = row.name_en, a.name_ja = row.name_ja,
            a.description = row.description, a.generation = row.generation
        """,
        ability_rows,
    )

    batch(
        tx,
        """
        UNWIND $rows AS row
        MERGE (p:Pokemon {pokedex_id: row.id})
        SET p.name_zh = row.name, p.name_en = row.name_en, p.name_ja = row.name_ja,
            p.generation = row.generation, p.types = row.types, p.category = row.category,
            p.height = row.height, p.weight = row.weight, p.color = row.color,
            p.catch_rate = row.catch_rate, p.base_exp = row.base_exp,
            p.description = row.description, p.profile = row.profile
        """,
        pokemon_rows,
    )

    batch(
        tx,
        "UNWIND $rows AS row MATCH (p:Pokemon {pokedex_id: row.id}) MERGE (t:Type {name: row.type}) MERGE (p)-[:HAS_TYPE]->(t)",
        has_type_rows,
    )

    batch(
        tx,
        "UNWIND $rows AS row MATCH (p:Pokemon {pokedex_id: row.id}) MERGE (a:Ability {name_zh: row.ability}) MERGE (p)-[r:HAS_ABILITY]->(a) SET r.is_hidden = row.hidden",
        has_ability_rows,
    )

    batch(
        tx,
        "UNWIND $rows AS row MATCH (p:Pokemon {pokedex_id: row.id}) MERGE (e:EggGroup {name: row.egg}) MERGE (p)-[:BELONGS_TO_EGG_GROUP]->(e)",
        egg_group_rows,
    )

    batch(
        tx,
        "UNWIND $rows AS row MATCH (p:Pokemon {pokedex_id: row.id}) MATCH (g:Generation {num: row.generation}) MERGE (p)-[:BELONGS_TO_GENERATION]->(g)",
        generation_rows,
    )

    batch(
        tx,
        "UNWIND $rows AS row MATCH (a:Pokemon {name_zh: row.from_name}) MATCH (b:Pokemon {name_zh: row.to_name}) MERGE (a)-[r:EVOLVES_TO]->(b) SET r.stage = row.stage, r.method = row.method",
        evolution_rows,
    )

    batch(
        tx,
        "UNWIND $rows AS row MATCH (p:Pokemon {pokedex_id: row.id}) MATCH (m:Move {name_zh: row.move}) MERGE (p)-[r:LEARNS_MOVE]->(m) SET r.method = row.method, r.level = row.level, r.machine = row.machine",
        learns_move_rows,
    )

    batch(
        tx,
        "UNWIND $rows AS row MERGE (s:Type {name: row.source}) MERGE (t:Type {name: row.target}) MERGE (s)-[r:STRONG_AGAINST]->(t) SET r.multiplier = row.multiplier",
        strong_rows,
    )

    batch(
        tx,
        "UNWIND $rows AS row MERGE (s:Type {name: row.source}) MERGE (t:Type {name: row.target}) MERGE (s)-[r:RESISTS]->(t) SET r.multiplier = row.multiplier",
        resist_rows,
    )

    batch(
        tx,
        "UNWIND $rows AS row MERGE (s:Type {name: row.source}) MERGE (t:Type {name: row.target}) MERGE (s)-[r:IMMUNE_TO]->(t)",
        immune_rows,
    )

def build_graph():
    national = dl.load_national()
    gen_map = {str(item.get("id")): item.get("gen") for item in national}

    move_rows = []
    for m in dl.load_move_list():
        name = m.get("name_zh")
        if name:
            move_rows.append({
                "name": name,
                "name_en": m.get("name_en"),
                "name_ja": m.get("name_jp"),
                "type": m.get("type"),
                "category": m.get("category"),
                "power": m.get("power"),
                "accuracy": m.get("accuracy"),
                "pp": m.get("pp"),
                "description": m.get("description"),
            })

    ability_rows = []
    for a in dl.load_ability_list():
        name = a.get("name_zh")
        if name:
            ability_rows.append({
                "name": name,
                "name_en": a.get("name_en"),
                "name_ja": a.get("name_ja"),
                "description": a.get("description"),
                "generation": a.get("generation"),
            })

    pokemon_rows = []
    has_type_rows = []
    has_ability_rows = []
    egg_group_rows = []
    generation_rows = []
    evolution_rows = []
    learns_move_rows = []
    type_chart = {}

    for path in dl.pokemon_files():
        data = dl.load_json(path)
        pokedex_id = str(data.get("pokedex_id"))
        name = data.get("name_zh") or path.stem
        forms = data.get("forms") or []
        form = forms[0] if forms else {}
        types = form.get("types") or []
        generation = gen_map.get(pokedex_id)

        pokemon_rows.append({
            "id": pokedex_id,
            "name": name,
            "name_en": data.get("name_en"),
            "name_ja": data.get("name_ja"),
            "generation": generation,
            "types": types,
            "category": form.get("category"),
            "height": form.get("height"),
            "weight": form.get("weight"),
            "color": form.get("color"),
            "catch_rate": form.get("catch_rate"),
            "base_exp": form.get("base_exp"),
            "description": data.get("description"),
            "profile": data.get("profile"),
        })

        for t in types:
            has_type_rows.append({"id": pokedex_id, "type": t})

        if generation:
            generation_rows.append({"id": pokedex_id, "generation": generation})

        for ab in form.get("abilities") or []:
            ability = ab.get("name")
            if ability:
                has_ability_rows.append({"id": pokedex_id, "ability": ability, "hidden": bool(ab.get("is_hidden"))})

        for egg in form.get("egg_groups") or []:
            egg_group_rows.append({"id": pokedex_id, "egg": egg})

        for te in data.get("type_effectiveness") or []:
            te_types = te.get("types") or []
            if len(te_types) == 1:
                source_type = te_types[0]
                for row in te.get("data") or []:
                    target = row.get("type")
                    damage = float(row.get("damage") or 1)
                    if target and damage != 1:
                        type_chart.setdefault(source_type, {})[target] = damage

        for chain in data.get("evolution_chains") or []:
            for evo in chain:
                to_name = evo.get("name")
                from_name = evo.get("from")
                if to_name and from_name:
                    evolution_rows.append({
                        "from_name": from_name,
                        "to_name": to_name,
                        "stage": evo.get("stage"),
                        "method": evo.get("text"),
                    })

        for group, method in [("learnable_moves", "level_up"), ("machine_moves", "machine"), ("egg_moves", "egg")]:
            for item in data.get(group) or []:
                for mv in item.get("data") or []:
                    move_name = mv.get("name")
                    if move_name:
                        learns_move_rows.append({
                            "id": pokedex_id,
                            "move": move_name,
                            "method": method,
                            "level": mv.get("level"),
                            "machine": mv.get("machine"),
                        })

    strong_rows = []
    resist_rows = []
    immune_rows = []
    for source, targets in type_chart.items():
        for target, damage in targets.items():
            row = {"source": source, "target": target, "multiplier": damage}
            if damage == 0:
                immune_rows.append(row)
            elif damage > 1:
                strong_rows.append(row)
            else:
                resist_rows.append(row)

    driver = get_driver()
    db = os.getenv("NEO4J_DB")
    with driver.session(database=db) as session:
        session.execute_write(
            import_graph,
            move_rows,
            ability_rows,
            pokemon_rows,
            has_type_rows,
            has_ability_rows,
            egg_group_rows,
            generation_rows,
            evolution_rows,
            learns_move_rows,
            strong_rows,
            resist_rows,
            immune_rows,
        )
    driver.close()
    print("build_graph done")

if __name__ == "__main__":
    build_graph()
