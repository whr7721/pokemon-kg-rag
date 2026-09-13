#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
宝可梦知识图谱构建器 (Pokemon KG Builder)
==========================================
输入 : dataset/  —— 42arch/pokemon-dataset-zh 稀疏克隆
      data/pokemon/*.json   1025 宝可梦  | data/abilities/*.json  特性详情
      data/moves/*.json     招式详情     | data/pokedex/*.json    地区图鉴
      *_list.json           权威名单
输出 : kg/
  1) entities/entity_<Label>.jsonl      实体（中性 JSONL）
  2) relations/rel_<Type>.jsonl         关系边（带属性）
  3) neo4j/nodes_<Label>.csv / rels_<Type>.csv / import.cypher
  4) chunks/entity_chunks.jsonl         实体检索文本块（向量/全文语料）
     chunks/relation_chunks.jsonl       关系三元组自然语言句（GraphRAG 检索单位）
  5) build_report.json                  统计 / 解析率 / 未命中 / 一致性告警

用法 : python3 build_kg.py [--dataset ../dataset] [--out ../kg] [--no-learn-text]
"""
import argparse
import json
import os
import re
from collections import Counter, defaultdict

# ---------------------------- 词表 / 归一 ----------------------------
TYPE_VOCAB = {"一般","格斗","飞行","毒","地面","岩石","虫","幽灵","钢","火","水","草",
              "电","超能力","冰","龙","恶","妖精"}

EGG_CANON = {
    "陆上群":"陆上","水中1群":"水中1","水中2群":"水中2","水中3群":"水中3",
    "人型群":"人型","妖精群":"妖精","植物群":"植物","矿物群":"矿物","虫群":"虫",
    "怪兽群":"怪兽","飞行群":"飞行","龙群":"龙","不定形群":"不定形",
    "百变怪群":"百变怪","未发现群":"未发现","未知蛋群":"未知",
    "陆上":"陆上","水中1":"水中1","水中2":"水中2","水中3":"水中3","人型":"人型",
    "妖精":"妖精","植物":"植物","矿物":"矿物","虫":"虫","怪兽":"怪兽",
    "飞行":"飞行","龙":"龙","不定形":"不定形","未发现":"未发现","未知":"未知",
    "百变怪":"百变怪",
}

METHOD_CN = {"level":"升级","machine":"招式学习器","egg":"蛋招式遗传","trade":"连接交换",
             "item":"使用道具","friendship":"亲密度","beauty":"美丽度","time":"时段条件",
             "weather":"天气条件","location":"特定地点","gender":"性别条件","other":"特殊条件"}
MULT_CN = {"0":"无效","0.25":"0.25倍(极大抵抗)","0.5":"0.5倍(抵抗)","1":"1倍(普通)",
           "2":"2倍(克制)","4":"4倍(极克制)"}

def classify_evo_method(text):
    t = text or ""
    if not t:
        return "other"
    if any(k in t for k in ("连接交换","联系绳","通信交换","连线交换","交换")):
        return "trade"
    if any(k in t for k in ("亲密度","友好度","伙伴度","亲密")):
        return "friendship"
    if "美丽度" in t:
        return "beauty"
    if any(k in t for k in ("之石","进化石","连接绳","使用叶","使用水","使用火","使用雷",
                            "使用月","使用日","使用光","使用暗","使用觉醒","使用冰",
                            "使用太阳","使用道具","携带","使用")):
        return "item"
    if any(k in t for k in ("等级","提升到","升级","提升等级")):
        return "level"
    if any(k in t for k in ("白天","夜晚","黄昏","清晨","时间","正午","午夜")):
        return "time"
    if any(k in t for k in ("下雨","晴天","日照","下雪","沙暴","冰雹","天气")):
        return "weather"
    if any(k in t for k in ("究极空间","反转世界","其他地区","旷野","特定地点","磁场","电磁")):
        return "location"
    if any(k in t for k in ("性别","雌性","雄性","♀","♂")):
        return "gender"
    return "other"

def dmg_word(m):
    return MULT_CN.get(str(m), f"{str(m)}倍")

def read_json(p):
    with open(p, encoding="utf-8") as f:
        return json.load(f)

# 特性别名：宝可梦文件用语 vs 权威名单用语（实测差异）
ABILITY_ALIAS = {"咒术之躯": "诅咒之躯", "诱爆": "引爆"}
# '无' = 无该槽位；'未知' = 数据源占位（多变特性等）
ABILITY_PLACEHOLDER = {"无", "未知"}

def norm_move(name):
    """招式中常见注释后缀清理（* ‡ † 与版本标注 USUM 等）。"""
    n = (name or "").strip()
    n = re.sub(r"[*\u2021\u2020\u2021\u271D\u270D]+$", "", n)
    n = re.sub(r"USUM$", "", n)
    return n.strip()

def num(v):
    try:
        x = str(v).strip().replace(",", "")
        if x.lstrip("-").isdigit():
            return int(x)
        return float(x)
    except Exception:
        return 0

def split_para(text, limit=700):
    out = []
    for para in re.split(r"\n+", text or ""):
        para = re.sub(r"[ \t]+", " ", para).strip()
        while len(para) > limit:
            cut = para.rfind("。", 0, limit)
            cut = cut if cut > 80 else limit
            out.append((para[:cut + 1] if cut != limit else para[:limit]).strip())
            para = para[cut + 1:].strip()
        if para:
            out.append(para)
    return out

def csv_escape(v):
    if isinstance(v, (dict, list)):
        s = json.dumps(v, ensure_ascii=False)
    elif v is None:
        s = ""
    else:
        s = str(v)
    if any(ch in s for ch in ',"\n\r'):
        return '"' + s.replace('"', '""') + '"'
    return s

def jl_open(path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    return open(path, "w", encoding="utf-8")

def stat_join(d):
    order = ["hp","attack","defense","sp_attack","sp_defense","speed"]
    return " ".join(f"{k}:{d[k]}" for k in order if k in d)

# ---------------------------- 加载器 ----------------------------
class Loader:
    def __init__(self, dataset_dir):
        self.ds = dataset_dir
        self.pokemon_dir  = os.path.join(dataset_dir, "data", "pokemon")
        self.abilities_dir= os.path.join(dataset_dir, "data", "abilities")
        self.moves_dir    = os.path.join(dataset_dir, "data", "moves")
        self.pokedex_dir  = os.path.join(dataset_dir, "data", "pokedex")
        self.simple = {e["index"]: e for e in
                       read_json(os.path.join(dataset_dir, "data", "simple_pokedex.json"))}
        self.move_list    = read_json(os.path.join(dataset_dir, "data", "move_list.json"))
        self.ability_list = read_json(os.path.join(dataset_dir, "data", "ability_list.json"))
        self.name2id = {e["name_zh"]: idx for idx, e in self.simple.items()}
        self.id2name = {idx: e["name_zh"] for idx, e in self.simple.items()}

    def iter_pokemon(self):
        for fn in sorted(os.listdir(self.pokemon_dir)):
            yield read_json(os.path.join(self.pokemon_dir, fn))

    def iter_abilities(self):
        for fn in sorted(os.listdir(self.abilities_dir)):
            if fn.endswith(".json"):
                yield read_json(os.path.join(self.abilities_dir, fn))

    def iter_moves(self):
        for fn in sorted(os.listdir(self.moves_dir)):
            if fn.endswith(".json"):
                yield read_json(os.path.join(self.moves_dir, fn))

    def iter_regions(self):
        for fn in sorted(os.listdir(self.pokedex_dir)):
            if fn.endswith(".json"):
                yield fn[:-5], read_json(os.path.join(self.pokedex_dir, fn))

# ---------------------------- 构建器 ----------------------------
class Builder:
    def __init__(self, L: Loader, out: str, include_learn_text=True):
        self.L = L
        self.out = out
        self.include_learn_text = include_learn_text
        self.entities = defaultdict(list)
        self.relations = defaultdict(list)
        self._seen = set()
        self.chunks = []
        self.rel_chunks = []
        self._te_single = defaultdict(list)   # defender_type -> [(rows,pid)]
        self._te_variant = []                 # [(pid, form_tag, types, rows)] 特性变体表
        self._te_profiles = {}                # pid -> (types, rows) 基础受击表
        self._ability_ids = {}                # 特性名 -> id（用于区分特性变体表）
        self._evo_raw = []                    # (fid, chain)
        self.report = {"pokemon_files": 0, "forms": 0, "types_seen": Counter(),
                       "abilities_used": 0, "abilities_unresolved": set(),
                       "moves_used": 0, "moves_unresolved": set(),
                       "evo_unresolved": [], "type_chart_warns": [],
                       "te_ability_variant": 0, "hit_profiles": 0,
                       "hit_profile_warns": []}

    # -------- 通用 --------
    def add_rel(self, rtype, frm, to, props=None):
        key = (rtype, frm, to)
        if key in self._seen:
            return
        self._seen.add(key)
        rec = {"from": frm, "to": to, "type": rtype}
        rec.update(props or {})
        self.relations[rtype].append(rec)

    def name_of(self, nid):
        return self._names.get(nid, nid)

    # ========= 实体：Type / Ability / Move / EggGroup =========
    def init_types(self):
        for t in sorted(TYPE_VOCAB):
            self.entities["Type"].append({"id": f"type:{t}", "name_zh": t, "text": ""})

    def _ensure_type(self, t):
        if f"type:{t}" not in {e["id"] for e in self.entities["Type"]}:
            self.entities["Type"].append({"id": f"type:{t}", "name_zh": t, "text": ""})

    def build_ability_entities(self):
        detail = {a["name_zh"]: a for a in self.L.iter_abilities()}
        # 权威名单 + 详情目录并集（名称对齐：详情目录存在但名单缺失时也建实体）
        used = set()
        for al in self.L.ability_list:
            name = al["name_zh"]
            used.add(name)
            det = detail.get(name, {})
            effect = det.get("effect") or ""
            if isinstance(effect, list):
                effect = "\n".join(str(x) for x in effect if isinstance(x, str))
            text = "\n".join(x for x in [al.get("description", ""),
                                         det.get("introduction", ""), str(effect)] if x)
            self.entities["Ability"].append({
                "id": f"ability:{name}", "name_zh": name,
                "name_en": al.get("name_en", ""), "name_ja": al.get("name_ja", ""),
                "generation": al.get("generation"),
                "common_count": al.get("common_count"), "hidden_count": al.get("hidden_count"),
                "description": al.get("description", ""), "effect": str(effect), "text": text})
        for dname, det in detail.items():
            if dname in used:
                continue
            effect = det.get("effect") or ""
            if isinstance(effect, list):
                effect = "\n".join(str(x) for x in effect if isinstance(x, str))
            text = "\n".join(x for x in [det.get("introduction", ""), str(effect)] if x)
            self.entities["Ability"].append({
                "id": f"ability:{dname}", "name_zh": dname,
                "name_en": det.get("name_en", ""), "name_ja": det.get("name_ja", ""),
                "generation": det.get("id"),
                "description": "", "effect": str(effect), "text": text})
        self._ability_ids = {a["name_zh"]: a["id"] for a in self.entities["Ability"]}

    def _resolve_ability(self, raw):
        """解析宝可梦侧特性名 -> Ability 实体 id；占位符返回 None。"""
        if raw in ABILITY_PLACEHOLDER:
            return None, raw
        name = ABILITY_ALIAS.get(raw, raw)
        aid = self._ability_ids.get(name)
        if aid:
            return aid, name
        # 未知名：建占位实体，保证图内无悬挂引用
        self._ability_ids[name] = f"ability:{name}"
        self.entities["Ability"].append({
            "id": f"ability:{name}", "name_zh": name,
            "name_en": "", "name_ja": "", "generation": None,
            "common_count": None, "hidden_count": None,
            "description": "", "effect": "", "placeholder": True,
            "text": f"特性：{name}（数据源占位，详情缺失）"})

        return self._ability_ids[name], name

    def _is_ability_tag(self, tag):
        """受击表 form 字段指向特性变体（而非形态简称）。

        数据里 form 取值混杂：完整形态名（“草木蓑衣”）、形态简称（“草木”“洗翠”）、
        特性名或括号注解（“毛茸茸”、“(避雷针)”）、形态+特性组合（“超级进化过滤”）。
        只有后两类是特性变体表；误判成基础表会把带特性修正的倍率混进克制表投票。
        """
        if not tag:
            return False
        if any(c in tag for c in "()（）"):
            return True
        clean = tag.strip("*‡† \t")
        if ABILITY_ALIAS.get(clean, clean) in self._ability_ids:
            return True
        return any(clean.endswith(ab) for ab in self._ability_ids if len(ab) >= 2)

    def build_move_entities(self):
        detail = {m["name_zh"]: m for m in self.L.iter_moves()}
        for ml in self.L.move_list:
            det = detail.get(ml["name_zh"], {})
            def _j(x):
                if isinstance(x, list):
                    return "\n".join(str(i) for i in x if isinstance(i, str))
                return str(x or "")
            text = "\n".join(x for x in [ml.get("description",""), _j(det.get("intro","")),
                                         _j(det.get("effect","")),
                                         _j(det.get("additional_effect",""))] if x)
            self.entities["Move"].append({
                "id": f"move:{ml['name_zh']}", "name_zh": ml["name_zh"],
                "name_en": ml.get("name_en",""), "name_ja": ml.get("name_ja",""),
                "type": ml.get("type",""), "category": ml.get("category",""),
                "power": ml.get("power"), "accuracy": ml.get("accuracy"),
                "pp": ml.get("pp"), "generation": ml.get("generation"),
                "description": ml.get("description",""), "text": text})
        self._move_ids = {m["name_zh"]: m["id"] for m in self.entities["Move"]}

    # ========= 主解析：1025 宝可梦 =========
    def parse_pokemon(self):
        egg_tokens = Counter()
        for d in self.L.iter_pokemon():
            pid = d["pokedex_id"]
            self.report["pokemon_files"] += 1
            self.L.name2id.setdefault(d["name_zh"], pid)

            # --- Pokemon 文本（图鉴条目按世代聚合） ---
            dex_by_gen = defaultdict(list)
            for entry in d.get("pokedex_entries", []):
                gen = entry.get("name", "")
                for v in entry.get("versions", []):
                    if v.get("text"):
                        dex_by_gen[gen].append(f"[{v.get('group') or v.get('name')}] {v['text']}")
            parts = [d.get("description","")]
            for gen, lines in dex_by_gen.items():
                parts.append(f"《{gen}》图鉴：\n" + "\n".join(lines))
            prof = (d.get("profile") or "")
            if prof:
                parts.append("外貌/设定：" + prof[:2000])
            legendary = ("传说的宝可梦" in (d.get("description") or "")) or \
                        ("幻之宝可梦" in (d.get("description") or ""))
            main = d["forms"][0] if d["forms"] else {}
            self.entities["Pokemon"].append({
                "id": pid, "pokedex_id": pid, "name_zh": d["name_zh"],
                "name_ja": d.get("name_ja",""), "name_en": d.get("name_en",""),
                "main_form": main.get("name",""), "category": main.get("category",""),
                "description": d.get("description",""), "legendary": legendary,
                "text": "\n\n".join(x for x in parts if x)})

            # --- Form ---
            form_names = [f.get("name") or f"形态{i}" for i, f in enumerate(d.get("forms", []))]
            for i, f in enumerate(d.get("forms", [])):
                fname = form_names[i]
                fid = f"{pid}::{fname}"
                self.report["forms"] += 1
                types = list(f.get("types") or [])
                for t in types:
                    self.report["types_seen"][t] += 1
                    self._ensure_type(t)
                stats = {}
                for st in d.get("stats", []):
                    if st.get("form") in ("", fname, "一般") or not stats:
                        stats = {k: num(v) for k, v in st.get("data", {}).items()}
                egg_raw = list(f.get("egg_groups") or [])
                egg_tokens.update(egg_raw)
                ab = [(a["name"], bool(a.get("is_hidden"))) for a in f.get("abilities", [])]
                ftext = (f"{d['name_zh']}（形态：{fname}）"
                         + (f"；属性 {'/'.join(types)}" if types else "")
                         + (f"；分类 {f.get('category','')}" if f.get('category') else "")
                         + (f"；身高 {f.get('height')}" if f.get('height') else "")
                         + (f"；体重 {f.get('weight')}" if f.get('weight') else "")
                         + (f"；颜色 {f.get('color')}" if f.get('color') else "")
                         + (f"；捕获率 {f.get('catch_rate')}" if f.get('catch_rate') else "")
                         + (f"；种族值 {stat_join(stats)}" if stats else ""))
                self.entities["Form"].append({
                    "id": fid, "pokedex_id": pid, "form_name": fname,
                    "is_default": (i == 0), "form_index": i, "types": types,
                    "category": f.get("category",""), "height": f.get("height",""),
                    "weight": f.get("weight",""), "color": f.get("color",""),
                    "egg_groups_raw": egg_raw, "gender_ratio": f.get("gender_ratio",{}),
                    "egg_cycles": f.get("egg_cycles",""), "stats": stats,
                    "base_stats_total": sum(v for v in stats.values()),
                    "text": ftext})
                self.add_rel("HAS_FORM", pid, fid, {"default": i == 0})
                for slot, t in enumerate(types):
                    self.add_rel("HAS_TYPE", fid, f"type:{t}", {"slot": slot + 1})
                for slot, (aname, hidden) in enumerate(ab):
                    self.report["abilities_used"] += 1
                    aid, _resolved = self._resolve_ability(aname)
                    if not aid:
                        continue
                    self.add_rel("HAS_ABILITY", fid, aid, {"slot": slot + 1, "hidden": hidden})
                for raw in egg_raw:
                    canon = EGG_CANON.get(raw, raw)
                    self.add_rel("IN_EGG_GROUP", fid, f"egggroup:{canon}")

                # 招式学习：不设唯一约束（同招式多方式并存），收尾统一按 (form,move,method) 去重
                for method, blocks in (("level", d.get("learnable_moves", [])),
                                       ("machine", d.get("machine_moves", [])),
                                       ("egg", d.get("egg_moves", []))):
                    for blk in blocks:
                        if blk.get("form") not in ("", fname, "一般", None):
                            continue
                        for mv in blk.get("data", []):
                            mname = norm_move(mv.get("name"))
                            if not mname:
                                continue
                            self.report["moves_used"] += 1
                            if mname not in self._move_ids:
                                self.report["moves_unresolved"].add(mname)
                            label = ""
                            if method == "level":
                                lv = mv.get("level")
                                label = f"Lv{lv}" if lv not in (None, "", "—") else "初始/升级"
                            elif method == "machine":
                                label = mv.get("machine", "招式学习器")
                            elif method == "egg":
                                pars = [p.get("name") for p in mv.get("parents", []) if p.get("name")]
                                label = "蛋招式" + (f"(遗传自{'、'.join(pars)})" if pars else "")
                            key = (fid, f"move:{mname}", method)
                            if key not in self._seen:
                                self._seen.add(key)
                                self.relations["LEARNS"].append({
                                    "from": fid, "to": f"move:{mname}", "type": "LEARNS",
                                    "method": method, "label": label,
                                    "move_type": mv.get("type", ""),
                                    "category": mv.get("category", "")})
                # --- 受击表收集 -------------------------------------------------
                # 该数据集 type_effectiveness 含两类：
                #   A) 基础属性表（form 为空 / '一般' / 该宝可梦真实形态名）  -> 用于 18x18 规范表
                #   B) 特性变体表（form 为特性名/注解，如 '(避雷针)'、'毛茸茸'、'洁净之盐'）
                #      -> 存入 _te_variant，作为“特性应对”派生边（ABILITY_TYPE_MOD）来源
                real_forms = {f.get("name") for f in d.get("forms", [])} | {""}
                for te in d.get("type_effectiveness", []):
                    tform = (te.get("form") or "").strip()
                    ttypes = list(te.get("types") or [])
                    rows = [(str(r.get("type")), str(r.get("damage")))
                            for r in te.get("data", []) if r.get("type")]
                    if not rows:
                        continue
                    if (tform not in real_forms and tform != "一般"
                            and self._is_ability_tag(tform)):
                        self.report["te_ability_variant"] += 1
                        if len(ttypes) == 1:
                            self._te_variant.append((pid, tform, ttypes[0], rows))
                        continue
                    # 基础表：空 form（默认形态）优先，其余仅在尚无记录时采用
                    if ttypes and (tform == "" or pid not in self._te_profiles):
                        self._te_profiles[pid] = (tuple(ttypes), rows)
                    if len(ttypes) == 1:
                        self._te_single[ttypes[0]].append((rows, pid))
                # --- 进化链原始信息（名称解析放收集阶段，链全量已在此文件内） ---
                if d.get("evolution_chains"):
                    self._evo_raw.append((pid, d["evolution_chains"]))

        # --- EggGroup 实体（含规范名合并） ---
        agg = defaultdict(list)
        for raw, cnt in egg_tokens.items():
            agg[EGG_CANON.get(raw, raw)].append((raw, cnt))
        for canon in sorted(agg):
            total = sum(c for _, c in agg[canon])
            raws = sorted({r for r, _ in agg[canon]})
            self.entities["EggGroup"].append({
                "id": f"egggroup:{canon}", "name_zh": canon, "raw_forms": raws,
                "form_count": total,
                "breedable": canon not in ("未发现", "未知"),
                "text": f"蛋群：{canon}（出现于 {total} 个形态）。"})
        self.report["egg_raw"] = dict(egg_tokens)

    # ========= 进化边 =========
    def collect_evolutions(self):
        for fid, chains in self._evo_raw:
            for chain in chains:
                prev = None
                for node in chain:
                    nm = node.get("name")
                    if not nm:
                        prev = None
                        continue
                    if prev is not None and node.get("from") == prev:
                        src = self.L.name2id.get(prev)
                        dst = self.L.name2id.get(nm)
                        cond = node.get("text") or ""
                        if src and dst and src != dst:
                            self.add_rel("EVOLVES_TO", src, dst, {
                                "condition": cond,
                                "method": classify_evo_method(cond),
                                "stage": node.get("stage",""),
                                "via_file": fid})
                        else:
                            self.report["evo_unresolved"].append((prev, nm, cond))
                    prev = nm

    # ========= 18×18 属性克制（单属性防御方聚合 + 一致性校验） =========
    def build_type_chart(self):
        chart = {}
        for def_type, rows in self._te_single.items():
            cnt = Counter((att, m) for rws, _ in rows for att, m in rws)
            per_att = defaultdict(list)
            for (att, m), c in cnt.items():
                per_att[att].append((m, c))
            for att, mc in per_att.items():
                # 多数票取 mode
                best_m, best_c = max(mc, key=lambda x: x[1])
                others = [(m, c) for m, c in mc if m != best_m]
                if others:
                    self.report["type_chart_warns"].append(
                        (att, def_type, best_m, {m: c for m, c in mc}))
                chart[(att, def_type)] = best_m
        self._chart = chart
        for (att, defn), m in chart.items():
            self.add_rel("HITS_TYPE", f"type:{att}", f"type:{defn}", {"multiplier": m})
        # Type 语义文本
        type_by_name = {e["name_zh"]: e for e in self.entities["Type"]}
        for defn, e in type_by_name.items():
            se, res, imm = [], [], []
            for (att, d2), m in chart.items():
                if d2 != defn:
                    continue
                if m in ("2", "4"):
                    se.append(att)
                elif m in ("0.5", "0.25"):
                    res.append(att)
                elif m == "0":
                    imm.append(att)
            txt = f"属性：{defn}。"
            if se:
                txt += f"弱于（受到克制）：{'、'.join(se)}。"
            if res:
                txt += f"抵抗：{'、'.join(res)}。"
            if imm:
                txt += f"免疫：{'、'.join(imm)}。"
            e["text"] = txt
            e["weak_to"] = se
            e["resist_to"] = res
            e["immune_to"] = imm

    # ========= 特性变体受击表 -> 特性应对派生边 (Ability -TYPE_MOD-> Type) ======
    # 数据自带“携带某特性时的属性相性表”，与基础表对照即可得到：
    #   避雷针/蓄电/引水/飘浮… 使某属性招式无效或异于常理 —— 即“特性应对”的图证据。
    def build_ability_type_mods(self):
        chart = self._chart
        seen = set()
        for pid, tag, def_type, rows in self._te_variant:
            aname = tag.strip("()（）*\u2021\u2020 \t")
            if not aname:
                continue
            aid, _ = self._resolve_ability(aname)
            if not aid:
                continue
            for att, m in rows:
                base = chart.get((att, def_type))
                if base is None or base == m:
                    continue
                key = (aid, f"type:{att}")
                if key in seen:
                    continue
                seen.add(key)
                self.add_rel("TYPE_MOD", aid, f"type:{att}", {
                    "pokemon": pid, "defender_type": def_type,
                    "base": base, "variant": m, "form_tag": tag,
                    "note": f"特性 {aname} 使 {att} 属性招式作用于{def_type}属性的伤害由 {base} 变为 {m}"})

    # ========= 地区图鉴归属 =========
    def build_regions(self):
        for name, data in self.L.iter_regions():
            is_nat = name == "national"
            entries = data if isinstance(data, list) else []
            member = 0
            for e in entries:
                nat = e.get("national_id") or e.get("id")
                if nat is None:
                    continue
                nid = str(nat).zfill(4)
                self.add_rel("IN_DEX", nid, f"region:{name}",
                             {"local_id": str(e.get("id")),
                              "gen": e.get("gen") if is_nat else None})
                member += 1
            self.entities["RegionDex"].append({
                "id": f"region:{name}", "name_zh": name, "is_national": is_nat,
                "member_count": member,
                "text": f"地区图鉴：{name}，共收录 {member} 只宝可梦。"})

    # ========= 受击倍率文本块（双属性直接取数据，不靠相乘） =========
    def build_hit_profiles(self):
        """每个宝可梦默认形态一条受击块。

        数据的 type_effectiveness 对每个属性组合都给了完整 18 项倍率，双属性
        无需查询端相乘；这里同时与单属性表相乘结果比对，记录不一致供核查。
        """
        for pid, (types, rows) in self._te_profiles.items():
            name = self._names.get(pid)
            if not name or not types:
                continue
            by_mult = defaultdict(list)
            for att, dmg in rows:
                try:
                    mult = float(dmg)
                except (TypeError, ValueError):
                    continue
                if mult != 1:
                    by_mult[mult].append(att)
            if not by_mult:
                continue
            parts = "；".join(
                f"{m:g}倍[{'、'.join(sorted(set(v)))}]"
                for m, v in sorted(by_mult.items(), reverse=True))
            self.chunks.append({
                "id": f"hitprofile|{pid}", "kind": "hit-profile",
                "entity_label": "Pokemon", "entity_id": pid, "name_zh": name,
                "text": f"{name}（{'/'.join(types)}）受到攻击时的伤害倍率：{parts}。",
                "lang": "zh"})
            self.report["hit_profiles"] += 1
            if len(types) > 1:
                bad = []
                for att, dmg in rows:
                    try:
                        mult = float(dmg)
                    except (TypeError, ValueError):
                        continue
                    calc = 1.0
                    for t in types:
                        base = self._chart.get((att, t))
                        calc = None if base is None else calc * float(base)
                        if calc is None:
                            break
                    if calc is not None and abs(calc - mult) > 1e-6:
                        bad.append((att, mult, calc))
                if bad:
                    self.report["hit_profile_warns"].append((pid, bad[:5]))

    # ========= 名字表 + 文本负载 =========
    def build_name_table(self):
        self._names = {}
        for label in ("Pokemon", "Form", "Type", "Ability", "Move", "EggGroup", "RegionDex"):
            for e in self.entities[label]:
                self._names[e["id"]] = e.get("name_zh") or e.get("form_name") or e["id"]

    def build_chunks(self):
        for label in ("Pokemon", "Form", "Ability", "Move", "Type", "EggGroup", "RegionDex"):
            for e in self.entities[label]:
                txt = e.get("text") or e.get("description") or ""
                for i, p in enumerate(split_para(txt)):
                    self.chunks.append({
                        "id": f"entity|{label}|{e['id']}|{i}", "kind": "entity",
                        "entity_label": label, "entity_id": e["id"],
                        "name_zh": e.get("name_zh") or e.get("form_name") or "",
                        "text": p, "lang": "zh"})
        core = {"HAS_TYPE", "HAS_ABILITY", "EVOLVES_TO", "IN_EGG_GROUP", "IN_DEX", "HITS_TYPE",
                "TYPE_MOD"}
        if self.include_learn_text:
            core.add("LEARNS")
        for rtype, recs in self.relations.items():
            if rtype not in core:
                continue
            if rtype in ("LEARNS", "IN_DEX"):
                self._append_overview_rel_chunks(rtype, recs)
                continue
            for r in recs:
                s, o = self.name_of(r["from"]), self.name_of(r["to"])
                sent = None
                if rtype == "HAS_TYPE":
                    sent = f"{s} 的属性包含 {o}。"
                elif rtype == "HAS_ABILITY":
                    h = "隐藏特性" if r.get("hidden") else "普通特性"
                    sent = f"{s} 的{h}为 {o}。"
                elif rtype == "EVOLVES_TO":
                    c = r.get("condition") or ""
                    m = METHOD_CN.get(r.get("method"), r.get("method"))
                    sent = f"{s} 进化为 {o}（{m}"
                    if c:
                        sent += f"，{c}"
                    sent += "）。"
                elif rtype == "IN_EGG_GROUP":
                    sent = f"{s} 属于蛋群 {o}。"
                elif rtype == "IN_DEX":
                    sent = f"{s} 收录于图鉴 {o}。"
                elif rtype == "HITS_TYPE":
                    sent = f"属性 {s} 攻击属性 {o} 时，伤害倍率为{dmg_word(r.get('multiplier'))}。"
                elif rtype == "TYPE_MOD":
                    sent = (f"特性 {s} 使 {o} 属性招式对 {r.get('defender_type')} 属性宝可梦的"
                            f"伤害由 {r.get('base')} 变为 {r.get('variant')}（实例：{self.L.id2name.get(r.get('pokemon'), r.get('pokemon'))}）。")
                elif rtype == "LEARNS":
                    lb = r.get("label") or ""
                    extra = f"（{lb}）" if lb else ""
                    sent = f"{s} 可通过{METHOD_CN.get(r.get('method'),'')}{extra}学会招式 {o}。"
                if sent:
                    self.rel_chunks.append({
                        "id": f"rel|{rtype}|{r['from']}|{r['to']}|{r.get('method','')}",
                        "kind": "relation", "relation": rtype,
                        "subject": r["from"], "object": r["to"],
                        "subject_name": s, "object_name": o,
                        "method": r.get("method"), "hidden": r.get("hidden"),
                        "multiplier": r.get("multiplier"),
                        "text": sent, "lang": "zh"})

    def _append_overview_rel_chunks(self, rtype, recs):
        """LEARNS / IN_DEX 按主语聚合为概览句。

        这两类逐边成句合计约 8.8 万条，全量入库会让向量语料膨胀近 3 倍；
        其余关系仍逐边生成，兼顾“普通 RAG 看得到关系事实”与索引体积。
        """
        by_subject = defaultdict(list)
        for r in recs:
            by_subject[r["from"]].append(r)
        for sub, rows in by_subject.items():
            sname = self.name_of(sub)
            if rtype == "LEARNS":
                level = [r for r in rows if r.get("method") == "level"]
                pool = sorted(level or rows,
                              key=lambda r: num(re.sub(r"\D", "", str(r.get("label") or ""))))
                names = [self.name_of(r["to"]) for r in pool][:20]
                text = f"{sname} 的升级招式包括：{'、'.join(names)}。" if names else ""
            else:
                names = [f"{self.name_of(r['to'])}#{r.get('local_id')}" for r in rows][:20]
                text = f"{sname} 收录于图鉴：{'、'.join(names)}。" if names else ""
            if not text:
                continue
            self.rel_chunks.append({
                "id": f"rel|{rtype}|{sub}|overview", "kind": "relation",
                "relation": rtype, "subject": sub, "object": "",
                "subject_name": sname, "object_name": "",
                "text": text, "lang": "zh"})

    # ========= 输出 =========
    def write_all(self):
        for label, recs in self.entities.items():
            with jl_open(os.path.join(self.out, "entities", f"entity_{label}.jsonl")) as f:
                for r in recs:
                    f.write(json.dumps(r, ensure_ascii=False) + "\n")
        for rtype, recs in self.relations.items():
            with jl_open(os.path.join(self.out, "relations", f"rel_{rtype}.jsonl")) as f:
                for r in recs:
                    f.write(json.dumps(r, ensure_ascii=False) + "\n")
        with jl_open(os.path.join(self.out, "chunks", "entity_chunks.jsonl")) as f:
            for c in self.chunks:
                f.write(json.dumps(c, ensure_ascii=False) + "\n")
        with jl_open(os.path.join(self.out, "chunks", "relation_chunks.jsonl")) as f:
            for c in self.rel_chunks:
                f.write(json.dumps(c, ensure_ascii=False) + "\n")

        ndir = os.path.join(self.out, "neo4j")
        os.makedirs(ndir, exist_ok=True)
        for label, recs in self.entities.items():
            keys = ["id"]
            for r in recs:
                for k in r:
                    if k not in keys:
                        keys.append(k)
            with open(os.path.join(ndir, f"nodes_{label}.csv"), "w", encoding="utf-8") as f:
                f.write(",".join(keys) + "\n")
                for r in recs:
                    f.write(",".join(csv_escape(r.get(k)) for k in keys) + "\n")
        for rtype, recs in self.relations.items():
            keys = ["from", "to", "type"]
            for r in recs:
                for k in r:
                    if k not in keys:
                        keys.append(k)
            with open(os.path.join(ndir, f"rels_{rtype}.csv"), "w", encoding="utf-8") as f:
                f.write(",".join(keys) + "\n")
                for r in recs:
                    f.write(",".join(csv_escape(r.get(k)) for k in keys) + "\n")
        self.write_cypher(ndir)

        report = {
            "pokemon_files": self.report["pokemon_files"],
            "forms": self.report["forms"],
            "types_seen": dict(self.report["types_seen"]),
            "egg_raw": dict(self.report["egg_raw"]),
            "abilities": {"used": self.report["abilities_used"],
                          "unresolved": sorted(self.report["abilities_unresolved"])},
            "moves": {"used": self.report["moves_used"],
                      "unresolved": sorted(self.report["moves_unresolved"])},
            "evo_edges": len(self.relations["EVOLVES_TO"]),
            "evo_unresolved": self.report["evo_unresolved"][:50],
            "learn_edges": len(self.relations["LEARNS"]),
            "type_mod_edges": len(self.relations["TYPE_MOD"]),
            "te_ability_variant_rows": self.report["te_ability_variant"],
            "type_chart_edges": len(self.relations["HITS_TYPE"]),
            "type_chart_warns": self.report["type_chart_warns"][:20],
            "hit_profiles": self.report["hit_profiles"],
            "hit_profile_warns": self.report["hit_profile_warns"][:20],
            "entities_total": {k: len(v) for k, v in self.entities.items()},
            "relations_total": {k: len(v) for k, v in self.relations.items()},
            "entity_chunks": len(self.chunks),
            "relation_chunks": len(self.rel_chunks),
        }
        with open(os.path.join(self.out, "build_report.json"), "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=1)

    def write_cypher(self, ndir):
        node_labels = ["Pokemon", "Form", "Type", "Ability", "Move", "EggGroup", "RegionDex"]
        rel_src = {"HAS_FORM": ("Pokemon", "Form"), "HAS_TYPE": ("Form", "Type"),
                   "HAS_ABILITY": ("Form", "Ability"), "IN_EGG_GROUP": ("Form", "EggGroup"),
                   "EVOLVES_TO": ("Pokemon", "Pokemon"), "IN_DEX": ("Pokemon", "RegionDex"),
                   "LEARNS": ("Form", "Move"), "HITS_TYPE": ("Type", "Type"),
                   "TYPE_MOD": ("Ability", "Type")}
        cols = {}
        for rtype, recs in self.relations.items():
            ks = []
            for r in recs:
                for k in r:
                    if k not in ("from", "to", "type") and k not in ks:
                        ks.append(k)
            cols[rtype] = ks
        lines = [
            "// 宝可梦知识图谱 —— Neo4j 建库脚本（build_kg.py 生成）",
            "// 前置：把 kg/neo4j/*.csv 拷入 Neo4j import 目录；Neo4j >= 5",
            "",
            "// ---- 1) 唯一性约束 ----",
        ]
        for lb in node_labels:
            lines.append(f"CREATE CONSTRAINT {lb}_id IF NOT EXISTS FOR (n:{lb}) REQUIRE n.id IS UNIQUE;")
        lines.append("")
        lines.append("// ---- 2) 节点 ----")
        for lb in node_labels:
            lines.append(f"LOAD CSV WITH HEADERS FROM 'file:///nodes_{lb}.csv' AS row")
            lines.append(f"MERGE (n:{lb} {{id: row.id}}) SET n = row;")
        lines.append("")
        lines.append("// ---- 3) 关系 ----")
        for rtype, (s, t) in rel_src.items():
            ks = [k for k in cols.get(rtype, [])]
            if rtype == "LEARNS":
                # 同一 (Form,Move) 可含 level/machine/egg 多条边 -> 以 method 为合并键
                rest = [k for k in ks if k != "method"]
                set_expr = (" SET r += {" + ", ".join(f"{k}: row.{k}" for k in rest) + "}") if rest else ""
                lines.append(f"LOAD CSV WITH HEADERS FROM 'file:///rels_{rtype}.csv' AS row")
                lines.append(f"MATCH (a:{s} {{id: row.from}}), (b:{t} {{id: row.to}})")
                lines.append(f"MERGE (a)-[r:{rtype} {{method: row.method}}]->(b){set_expr};")
            else:
                set_expr = (" SET r += {" + ", ".join(f"{k}: row.{k}" for k in ks) + "}") if ks else ""
                lines.append(f"LOAD CSV WITH HEADERS FROM 'file:///rels_{rtype}.csv' AS row")
                lines.append(f"MATCH (a:{s} {{id: row.from}}), (b:{t} {{id: row.to}})")
                lines.append(f"MERGE (a)-[r:{rtype}]->(b){set_expr};")
        lines.append("")
        lines.append("// ---- 4) 全文 / 向量索引（供 Retriever） ----")
        lines.append("CREATE FULLTEXT INDEX pokemonFulltext IF NOT EXISTS FOR (n:Pokemon) ON EACH [n.name_zh, n.name_ja, n.name_en, n.text];")
        lines.append("CREATE FULLTEXT INDEX formFulltext IF NOT EXISTS FOR (n:Form) ON EACH [n.form_name, n.text];")
        lines.append("CREATE FULLTEXT INDEX abilityFulltext IF NOT EXISTS FOR (n:Ability) ON EACH [n.name_zh, n.effect, n.text];")
        lines.append("CREATE FULLTEXT INDEX moveFulltext IF NOT EXISTS FOR (n:Move) ON EACH [n.name_zh, n.description, n.text];")
        lines.append("// 向量索引：先跑嵌入管线写入 n.embedding 再执行（维度随模型调整）")
        lines.append("// CREATE VECTOR INDEX pokemonVector IF NOT EXISTS FOR (n:Pokemon) ON (n.embedding) OPTIONS {indexConfig: {`vector.dimensions`: 768, `vector.similarity_function`: 'cosine'}};")
        lines.append("// CREATE VECTOR INDEX entityVector IF NOT EXISTS FOR (n:Form|Ability|Move) ON (n.embedding) OPTIONS {indexConfig: {`vector.dimensions`: 768, `vector.similarity_function`: 'cosine'}};")
        lines.append("")
        lines.append("// ---- 5) 示例 ----")
        lines.append("// MATCH (t:Type)-[h:HITS_TYPE]->(:Type {name_zh:'毒'}) WHERE h.multiplier='2' RETURN t.name_zh;")
        lines.append("// MATCH (p:Pokemon {pokedex_id:'0025'})-[:HAS_FORM]->(f:Form) OPTIONAL MATCH (f)-[r]->(x) RETURN f.form_name, type(r), x.name_zh LIMIT 60;")
        lines.append("")
        with open(os.path.join(ndir, "import.cypher"), "w", encoding="utf-8") as f:
            f.write("\n".join(lines))

# ============================== main ==================================
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dataset", default=os.path.normpath(
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "dataset")))
    ap.add_argument("--out", default=os.path.normpath(
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "kg")))
    ap.add_argument("--no-learn-text", action="store_true",
                    help="关系文本负载不含 LEARNS 招式边")
    args = ap.parse_args()

    L = Loader(args.dataset)
    b = Builder(L, args.out, include_learn_text=not args.no_learn_text)

    print("[1/7] 权威名单实体 (Type/Ability/Move)...")
    b.init_types(); b.build_ability_entities(); b.build_move_entities()

    print("[2/7] 解析 pokemon 文件（Form/属性/特性/招式/蛋群/受击表）...")
    b.parse_pokemon()

    print("[3/7] 进化边 ...")
    b.collect_evolutions()

    print("[4/7] 18×18 属性克制表 + Type 文本 ...")
    b.build_type_chart()

    print("[4b] 特性变体 -> TYPE_MOD 特性应对边 ...")
    b.build_ability_type_mods()

    print("[5/7] 地区图鉴归属 ...")
    b.build_regions()

    print("[6/7] 检索文本块 ...")
    b.build_name_table(); b.build_chunks()
    b.build_hit_profiles()

    print("[7/7] 输出 ...")
    b.write_all()

    r = b.report
    print(json.dumps({
        "pokemon": r["pokemon_files"], "forms": r["forms"],
        "abilities_unresolved": len(r["abilities_unresolved"]),
        "moves_unresolved": len(r["moves_unresolved"]),
        "evo_edges": len(b.relations["EVOLVES_TO"]),
        "learn_edges": len(b.relations["LEARNS"]),
        "chart_edges": len(b.relations["HITS_TYPE"]),
        "chart_consistent": not r["type_chart_warns"],
        "entity_chunks": len(b.chunks), "relation_chunks": len(b.rel_chunks),
        "entities": {k: len(v) for k, v in b.entities.items()},
    }, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
