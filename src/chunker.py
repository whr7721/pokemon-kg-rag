"""把结构化 JSON 数据规则化地转成可检索、可对齐图谱的文本块。"""

def _s(value):
    if value is None:
        return ""
    if isinstance(value, (list, tuple)):
        return "、".join(str(x) for x in value)
    return str(value).strip()

def _line(label, value):
    value = _s(value)
    return f"{label}: {value}" if value else None

def _text(lines):
    return "\n".join(x for x in lines if x)

def _stats_text(stats):
    if not stats:
        return ""
    first = stats[0] if isinstance(stats, list) else {}
    data = first.get("data") or {}
    if not data:
        return ""
    order = [("hp", "HP"), ("attack", "攻击"), ("defense", "防御"),
             ("sp_attack", "特攻"), ("sp_defense", "特防"), ("speed", "速度")]
    parts = [f"{zh}{_s(data.get(key))}" for key, zh in order if data.get(key) is not None]
    return " / ".join(parts)

def _ability_text(abilities):
    parts = []
    for a in abilities or []:
        name = a.get("name")
        if not name:
            continue
        suffix = "（隐藏）" if a.get("is_hidden") else ""
        parts.append(name + suffix)
    return "、".join(parts)

def _move_rows_text(groups):
    lines = []
    for group in groups or []:
        form = group.get("form")
        if form:
            lines.append(f"形态: {form}")
        for mv in group.get("data") or []:
            name = mv.get("name")
            if not name:
                continue
            level = _s(mv.get("level"))
            typ = _s(mv.get("type"))
            category = _s(mv.get("category"))
            power = _s(mv.get("power"))
            level_part = f"等级{level}" if level and level != "—" else "—"
            lines.append(f"{level_part}: {name}（{typ}/{category}，威力{power or '—'}）")
    return _text(lines)

def pokemon_chunks(data):
    name = data.get("name_zh") or data.get("name_en") or ""
    pid = str(data.get("pokedex_id") or "")
    form = (data.get("forms") or [{}])[0] if data.get("forms") else {}

    base_lines = [
        f"【宝可梦】{name}",
        _line("英文名", data.get("name_en")),
        _line("日文名", data.get("name_ja")),
        _line("图鉴编号", pid),
        _line("属性", form.get("types")),
        _line("分类", form.get("category")),
        _line("身高", form.get("height")),
        _line("体重", form.get("weight")),
        _line("颜色", form.get("color")),
        _line("捕获率", form.get("catch_rate")),
        _line("基础经验", form.get("base_exp")),
        _line("蛋群", form.get("egg_groups")),
    ]
    ability_text = _ability_text(form.get("abilities"))
    if ability_text:
        base_lines.append(f"特性: {ability_text}")
    stats_text = _stats_text(data.get("stats"))
    if stats_text:
        base_lines.append(f"能力值: {stats_text}")
    base_lines.extend([
        _line("描述", data.get("description")),
        _line("外貌", data.get("profile")),
    ])

    chunks = [{
        "chunk_id": f"pokemon-{pid}",
        "kind": "pokemon",
        "entity_type": "Pokemon",
        "entity_id": pid,
        "text": _text(base_lines),
    }]

    moves_text = _move_rows_text(data.get("learnable_moves"))
    if moves_text:
        chunks.append({
            "chunk_id": f"pokemon-moves-{pid}",
            "kind": "pokemon_moves",
            "entity_type": "Pokemon",
            "entity_id": pid,
            "text": f"【招式】{name}可学招式\n{moves_text}",
        })
    return chunks

def move_chunk(m):
    name = m.get("name_zh") or m.get("name_en") or ""
    lines = [
        f"【招式】{name}",
        _line("英文名", m.get("name_en")),
        _line("日文名", m.get("name_jp")),
        _line("属性", m.get("type")),
        _line("分类", m.get("category")),
        _line("威力", m.get("power")),
        _line("命中率", m.get("accuracy")),
        _line("PP", m.get("pp")),
        _line("描述", m.get("description")),
        _line("世代", m.get("generation")),
    ]
    return {
        "chunk_id": f"move-{m.get('id') or name}",
        "kind": "move",
        "entity_type": "Move",
        "entity_id": name,
        "text": _text(lines),
    }

def ability_chunk(a):
    name = a.get("name_zh") or a.get("name_en") or ""
    lines = [
        f"【特性】{name}",
        _line("英文名", a.get("name_en")),
        _line("日文名", a.get("name_ja")),
        _line("描述", a.get("description")),
        _line("世代", a.get("generation")),
    ]
    return {
        "chunk_id": f"ability-{a.get('id') or name}",
        "kind": "ability",
        "entity_type": "Ability",
        "entity_id": name,
        "text": _text(lines),
    }
