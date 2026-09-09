"""多格式问答路由（组内增强 Schema：Form + HITS_TYPE + 叙事边可用时）。

三类结构化问题直接走图查询（确定性、可带证据）：
  - 进化链/条件   ：EVOLVES_TO 路径（前/后向、含条件）
  - 克制推荐      ：目标属性 → HITS_TYPE 18×18 全表 → 候选宝可梦（单/双属性，倍率乘积、免疫按 0）
  - 种间关系      ：RIVAL_OF/PREDATES_ON 等语义边 + 证据句
其余开放题返回 None，交由外部 GraphRAG 处理。

口径：
  - 克制倍率方向：攻击属性 → 防御属性（HITS_TYPE）；双属性防御方取乘积，0 主导。
  - 进化条件为原样自由文本（仅展示）。
"""
from __future__ import annotations

import os

from dotenv import load_dotenv
from neo4j import GraphDatabase

load_dotenv()

NARRATIVE_RELS = ("RIVAL_OF", "PREDATES_ON", "COMPETES_WITH", "ALLIED_WITH",
                  "COMMENSAL_OF", "MENTOR_OF", "SYMBIOTIC_WITH")
NARRATIVE_CN = {
    "RIVAL_OF": "宿敌/敌对", "PREDATES_ON": "捕食", "COMPETES_WITH": "竞争",
    "ALLIED_WITH": "结盟/并肩", "COMMENSAL_OF": "共生/寄居", "MENTOR_OF": "师徒/教导",
    "SYMBIOTIC_WITH": "互利共生",
}

REL_WORDS = ("关系", "宿敌", "捕食", "共生", "寄居", "寄生", "师徒", "教导",
             "劲敌", "敌人", "竞争", "结盟", "伙伴", "讨厌", "痛恨", "仇视",
             "敌视", "厌恶", "势不两立",
             "喜欢", "朋友", "好友", "队友", "战友", "合作", "联手", "携手",
             "并肩", "结伴", "互助", "互相帮助", "保护", "互利", "相依", "传授", "友好", "亲密")
# 情感极性：按问题里出现的词决定展示哪些关系边（避免"讨厌谁"里混进"结盟谁"）
NEG_WORDS = ("讨厌", "痛恨", "仇视", "敌视", "敌人", "宿敌", "劲敌", "厌恶", "势不两立", "竞争")
POS_WORDS = ("喜欢", "朋友", "好友", "队友", "战友", "合作", "联手", "携手", "并肩",
             "结伴", "互助", "互相帮助", "保护", "互利", "共生", "相依", "师徒",
             "教导", "传授", "友好", "亲密", "伙伴", "结盟")
POS_RELS = ("ALLIED_WITH", "SYMBIOTIC_WITH", "COMMENSAL_OF", "MENTOR_OF")
NEG_RELS = ("RIVAL_OF", "PREDATES_ON", "COMPETES_WITH")
EVO_WORDS = ("进化成", "最终进化", "进化到", "怎么进化", "如何进化", "进化链",
             "进化来源", "进化条件", "进化分支", "进化出来", "怎么获得", "如何获得", "获取")
COUNTER_WORDS = ("克制", "弱点", "克星", "谁克制", "打谁", "怕", "推荐", "免疫", "天敌")
HOLDER_WORDS = ("拥有", "具有", "带着", "携带", "谁有", "哪些宝可梦拥有", "有哪些", "谁会", "特性")
TYPES18 = ("一般", "格斗", "飞行", "毒", "地面", "岩石", "虫", "幽灵", "钢", "火", "水", "草", "电",
           "超能力", "冰", "龙", "恶", "妖精")
STRATEGY_WORDS = ("特性", "携带", "带什么", "适合", "应对", "面对", "对策", "推荐带")

# 确凿的俗称→官方名（仅收录"语义确定"的，其余交给候选建议，避免硬编码过死）
ALIASES = {
    "板匙蛇": "饭匙蛇",   # Seviper 旧称 → 官方译名（此前误写成阿柏怪，已更正）
}


def alias_normalize(question: str) -> str:
    q = question
    for k, v in ALIASES.items():
        q = q.replace(k, v)
    return q


def get_driver():
    return GraphDatabase.driver(
        os.getenv("NEO4J_URL"),
        auth=(os.getenv("NEO4J_USER"), os.getenv("NEO4J_PASSWORD")),
    )


def classify(question: str) -> str:
    if any(w in question for w in EVO_WORDS):
        return "evolution"
    if any(w in question for w in REL_WORDS) and not any(w in question for w in COUNTER_WORDS):
        return "relation"
    if any(w in question for w in COUNTER_WORDS):
        return "counter"
    return "open"


class MultiQA:
    def __init__(self):
        self.driver = get_driver()
        self.db = os.getenv("NEO4J_DB") or "neo4j"
        self._names = None
        self._abilities = None
        self._chart = None
        self._pkm_types = None
        self._evo = None
        self._narr = None

    def close(self):
        self.driver.close()

    def _run(self, query, **params):
        with self.driver.session(database=self.db) as s:
            return s.run(query, **params).data()

    def names(self):
        if self._names is None:
            self._names = sorted(r["n"] for r in self._run("MATCH (p:Pokemon) RETURN p.name_zh AS n"))
        return self._names

    def species_in(self, question: str):
        """按在问题中出现的先后顺序返回宝可梦名（长度优先匹配避免子串抢词）。"""
        cand = []
        for name in sorted(self.names(), key=len, reverse=True):
            idx = question.find(name)
            if idx >= 0:
                cand.append((idx, name))
        seen, res = set(), []
        for _, name in sorted(cand):
            if name not in seen:
                seen.add(name)
                res.append(name)
        return res

    def abilities(self):
        if self._abilities is None:
            self._abilities = [r["n"] for r in self._run("MATCH (a:Ability) RETURN a.name_zh AS n")]
        return self._abilities

    def ability_in(self, question: str):
        for name in sorted(self.abilities(), key=len, reverse=True):
            if name and name in question:
                return name
        return None

    @staticmethod
    def _edit(a: str, b: str) -> int:
        """字符级编辑距离（小串够用）。"""
        if a == b:
            return 0
        if not a:
            return len(b)
        if not b:
            return len(a)
        prev = list(range(len(b) + 1))
        for i, ca in enumerate(a, 1):
            cur = [i]
            for j, cb in enumerate(b, 1):
                cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
            prev = cur
        return prev[-1]

    def guess_abilities(self, question: str):
        """按"窗口编辑距离 ≤1"找相近特性；返回 ('single', 名称) / ('multi', [名称]) / None。"""
        abilities = [a for a in self.abilities() if len(a) >= 2 and len(a) <= 4]
        cands = []
        for ab in abilities:
            best = 99
            L = len(ab)
            for width in (max(1, L - 1), L, L + 1):
                for i in range(0, len(question) - width + 1):
                    d = self._edit(ab, question[i:i + width])
                    if d < best:
                        best = d
            if best <= 1:
                cands.append(ab)
        if not cands:
            return None
        # 找"最大连通组"（候选间编辑距离 ≤1 的归一组），避免混进无关项
        n = len(cands)
        adj = [[i != j and self._edit(cands[i], cands[j]) <= 1 for j in range(n)] for i in range(n)]
        best_group, seen = [], set()
        for i in range(n):
            if i in seen:
                continue
            stack, group = [i], []
            while stack:
                k = stack.pop()
                if k in seen:
                    continue
                seen.add(k)
                group.append(k)
                stack.extend(j for j in range(n) if adj[k][j] and j not in seen)
            if len(group) > len(best_group):
                best_group = group
        group = [cands[i] for i in best_group]
        return ("single", group[0]) if len(group) == 1 else ("multi", group)

    def ability_meta(self, names):
        rows = self._run("""
            UNWIND $names AS n
            MATCH (a:Ability {name_zh: n})
            OPTIONAL MATCH (f:Form)-[:HAS_ABILITY]->(a)
            RETURN n AS name, a.description AS dsc,
                   count(DISTINCT f.pokedex_id) AS holders
        """, names=names)
        return {r["name"]: r for r in rows}

    def type_chart(self):
        """攻击属性名 -> {防御属性名: 倍率}（HITS_TYPE 攻击→防御）。"""
        if self._chart is None:
            nm = {r["id"]: r["nm"] for r in self._run("MATCH (t:Type) RETURN t.id AS id, t.name_zh AS nm")}
            self._chart = {}
            for r in self._run("MATCH (a)-[h:HITS_TYPE]->(b) RETURN a.id AS a, b.id AS b, h.multiplier AS m"):
                atk, dfn = nm.get(r["a"]), nm.get(r["b"])
                if atk and dfn:
                    self._chart.setdefault(atk, {})[dfn] = float(r["m"])
        return self._chart

    def pokemon_default_types(self):
        if self._pkm_types is None:
            rows = self._run("""
                MATCH (p:Pokemon)-[:HAS_FORM]->(f:Form)
                OPTIONAL MATCH (f)-[:HAS_TYPE]->(t:Type)
                WITH p, f, collect(DISTINCT t.name_zh) AS types
                WHERE f.is_default IS NULL OR f.is_default = true OR toString(f.is_default) IN ['True', 'true', '1']
                RETURN p.name_zh AS name, types
            """)
            merged = {}
            for r in rows:
                merged.setdefault(r["name"], r["types"])
            self._pkm_types = merged
        return self._pkm_types

    def evo_graph(self):
        if self._evo is None:
            g = {"out": {}, "in": {}, "cond": {}}
            for r in self._run("MATCH (a)-[r:EVOLVES_TO]->(b) RETURN a.name_zh AS a, b.name_zh AS b, r.condition AS c"):
                g["out"].setdefault(r["a"], []).append(r["b"])
                g["in"].setdefault(r["b"], []).append(r["a"])
                g["cond"][(r["a"], r["b"])] = r["c"]
            self._evo = g
        return self._evo

    def narrative_edges(self):
        if self._narr is None:
            self._narr = self._run(
                "MATCH (a:Pokemon)-[r]->(b:Pokemon) "
                "WHERE type(r) IN $rels "
                "RETURN a.name_zh AS a, b.name_zh AS b, type(r) AS t, "
                "properties(r).evidence AS ev, properties(r).confidence AS cf",
                rels=list(NARRATIVE_RELS))
        return self._narr

    # ---------- 路由 ----------
    def answer(self, question: str):
        q = alias_normalize(question)   # 确凿俗称归一（目前仅 板匙蛇→饭匙蛇）
        holder = any(w in q for w in HOLDER_WORDS)
        ability = self.ability_in(q)
        if ability and holder:
            return self.answer_ability(q, ability)
        kind = classify(q)
        if kind == "evolution":
            return self.answer_evolution(q)
        if kind == "counter":
            return self.answer_counter(q)
        if kind == "relation":
            return self.answer_relation(q)
        if self.species_in(q) and any(w in q for w in STRATEGY_WORDS):
            return self.answer_strategy(q)
        if holder:                       # 特性名不精确时 → 候选建议（悬浮→飘浮 / 蓄水→储水·引水…）
            guess = self.guess_abilities(q)
            if guess:
                return self.answer_guess(q, guess)
        return None

    def answer_guess(self, question: str, guess):
        if guess[0] == "single":
            ab = guess[1]
            res = self.answer_ability(question, ab)
            res["answer"] = f"未找到完全一致的特性名，按最接近的「{ab}」回答：\n" + res["answer"]
            return res
        names = guess[1]
        meta = self.ability_meta(names)
        lines = ["输入可能不精确，猜你想搜以下相近特性（逐个介绍）："]
        for ab in names:
            m = meta.get(ab, {})
            dsc = str(m.get("dsc") or "").replace("\n", " ").strip()
            lines.append(f"  · {ab}：{dsc[:110]}{'…' if len(dsc) > 110 else ''}"
                         + (f"（拥有者 {m.get('holders', 0)} 只）" if m.get("holders") else ""))
        return {"kind": "suggest", "ok": True, "answer": "\n".join(lines)}

    # ---------- 特性策略（带推荐：按"能否免疫/吸收对方属性招式"打分） ----------
    def _ability_rows(self, name):
        rows = self._run("""
            MATCH (p:Pokemon {name_zh: $n})-[:HAS_FORM]->(f:Form)-[r:HAS_ABILITY]->(a:Ability)
            RETURN a.name_zh AS ab, a.description AS dsc, a.effect AS eff,
                   coalesce(f.is_default,'') AS def, coalesce(r.hidden,'False') AS hd
        """, n=name)
        best = {}
        for r in rows:
            is_def = r["def"] in ("True", "") or str(r["def"]).lower() == "true"
            hidden = str(r["hd"]).lower() in ("true", "1", "yes")
            cur = best.get(r["ab"])
            if cur is None or (is_def and not cur.get("def")):
                best[r["ab"]] = {"hidden": hidden, "def": is_def,
                                 "dsc": r["dsc"] or "", "eff": r["eff"] or ""}
            elif is_def:
                best[r["ab"]]["hidden"] = best[r["ab"]]["hidden"] or hidden
        return best

    @staticmethod
    def _covers(text, types):
        """文本里是否出现『某属性招式 + 免疫/不受/吸收/无效』语义。"""
        if not text:
            return set()
        keys = ("不受", "免疫", "吸收", "吸引", "无效", "没有效果", "不会受到伤害")
        return {t for t in types if (f"{t}属性" in text or f"{t}系" in text)
                and any(k in text for k in keys)}

    def answer_strategy(self, question: str):
        sp = self.species_in(question)
        subject = sp[0]
        # 对手：第二个宝可梦 > 问题里出现的属性
        opp_species = sp[1] if len(sp) >= 2 else None
        opp_types = []
        if opp_species:
            opp_types = self.pokemon_default_types().get(opp_species) or []
        else:
            opp_types = [t for t in TYPES18 if (t + "系") in question or t in question]
        stypes = self.pokemon_default_types().get(subject) or []
        abs_ = self._ability_rows(subject)
        if not abs_:
            return {"kind": "strategy", "ok": False, "answer": f"未找到「{subject}」的特性数据。"}
        chart = self.type_chart()
        scored = []
        for ab, info in abs_.items():
            text = f"{info['eff']} {info['dsc']}"
            cover = self._covers(text, opp_types)
            bonus = 1 if (opp_types and set(opp_types).issubset(cover)) else 0
            scored.append((bonus + len(cover), ab, info, sorted(cover)))
        scored.sort(key=lambda x: -x[0])
        lines = []
        # 明确推荐
        top = scored[0]
        if top[0] > 0 and opp_types:
            info = top[2]
            tag = "（隐藏）" if info["hidden"] else "（普特）"
            dsc = str(info["dsc"]).replace("\n", " ").strip()
            lines.append(f"推荐：{top[1]}{tag} —— {dsc[:100]}")
            lines.append(f"理由：能{'免疫/吸收' if any(k in (info['eff'] or '') for k in ('不受','免疫','吸收','吸引')) else '有效应对'}对方「{opp_species or '/'.join(opp_types)}」的"
                         + ("/".join(top[3]) if top[3] else "") + "属性招式。")
        else:
            lines.append("说明：该宝可梦的特性中未检测到能直接免疫/吸收对方属性的选项，下面列出机制事实供自判。")
        lines.append(f"「{subject}」（{'/'.join(stypes) if stypes else '?'}）可选特性：")
        for _, ab, info, cover in scored:
            tag = "（隐藏）" if info["hidden"] else "（普特）"
            dsc = str(info["dsc"]).replace("\n", " ").strip()
            lines.append(f"  · {ab}{tag}：{dsc[:110]}{'…' if len(dsc) > 110 else ''}")
        if opp_types:
            m = 1.0
            for ty in stypes:
                for t in opp_types:
                    m *= chart.get(t, {}).get(ty, 1.0)
            lines.append(f"机制提示：对方{opp_species or '/'.join(opp_types)}的招式命中你 ≈ {m:g} 倍"
                         + ("（已抵抗）" if m < 1 else ("（克制，注意规避）" if m > 1 else "")))
        # 对手自带免疫类特性提醒（如电击魔兽的电气引擎）
        if opp_species:
            for oab, oinfo in self._ability_rows(opp_species).items():
                otext = f"{oinfo['eff']} {oinfo['dsc']}"
                ocover = self._covers(otext, stypes)
                if ocover and any(k in otext for k in ("不受", "免疫", "吸收", "吸引")):
                    lines.append(f"对战提醒：对方「{opp_species}」的『{oab}』会免疫/吸收你的"
                                 + "/".join(sorted(ocover)) + "属性招式，别指望用它输出。")
                    break
        lines.append("说明：以上为数据内机制事实；‘最适合’取决于对战环境（物/特攻、双打、梦特可得性），请结合队伍自判。")
        return {"kind": "strategy", "ok": True, "answer": "\n".join(lines)}

    # ---------- 特性拥有者 ----------
    def answer_ability(self, question: str, ability: str):
        rows = self._run("""
            MATCH (f:Form)-[:HAS_ABILITY]->(a:Ability {name_zh: $ab})
            MATCH (p:Pokemon)-[:HAS_FORM]->(f)
            RETURN p.name_zh AS name, collect(DISTINCT f.form_name) AS forms
            ORDER BY p.name_zh
        """, ab=ability)
        names = [r["name"] for r in rows]
        if not names:
            return {"kind": "ability", "ok": False, "answer": f"数据里没有拥有「{ability}」的宝可梦。"}
        shown = "、".join(names[:30]) + (f" 等共 {len(names)} 只" if len(names) > 30 else f"（共 {len(names)} 只）")
        return {"kind": "ability", "ok": True, "answer": f"拥有「{ability}」特性的宝可梦：{shown}。"}

    # ---------- 进化 ----------
    def _fmt_chain(self, seq, cond):
        """seq 为正向进化顺序 [首..末]，输出 首 → 次（条件） → …"""
        text = seq[0]
        for i in range(len(seq) - 1):
            c = cond.get((seq[i], seq[i + 1]))
            text += f" → {seq[i + 1]}（{c}）" if c else f" → {seq[i + 1]}"
        return text

    def _forward_paths(self, start, g, depth=0):
        if depth > 3 or not g["out"].get(start):
            return [[start]]
        res = []
        for nxt in g["out"][start]:
            for tail in self._forward_paths(nxt, g, depth + 1):
                res.append([start] + tail)
        return res

    def _root_paths(self, start, g, depth=0):
        """从 start 逆推到没有来源的祖先（返回正向顺序 祖…start）。"""
        if depth > 4 or not g["in"].get(start):
            return [[start]]
        res = []
        for prv in g["in"][start]:
            for tail in self._root_paths(prv, g, depth + 1):
                res.append([start] + tail)
        return res

    def answer_evolution(self, question: str):
        g = self.evo_graph()
        sp = self.species_in(question)
        if not sp:
            return {"kind": "evolution", "ok": False, "answer": "未识别到宝可梦名"}
        name = sp[-1]  # 目标取最后提到的（起点/终点均可由路由语义处理）
        want_source = any(w in question for w in ("来源", "怎么获得", "如何获得", "获取", "进化出来"))
        lines = []
        if want_source:
            paths = self._root_paths(name, g)
            if not paths:
                return {"kind": "evolution", "ok": True,
                        "answer": f"数据里没有「{name}」的进化来源（可能需捕捉/孵蛋；数据集不含捕捉地点）。"}
            lines.append(f"「{name}」的获取/进化来源：")
            for p in paths:
                lines.append("  " + self._fmt_chain(list(reversed(p)), g["cond"]))
        else:
            paths = self._forward_paths(name, g)
            if len(paths) == 1 and len(paths[0]) == 1:
                return {"kind": "evolution", "ok": True,
                        "answer": f"「{name}」没有进一步进化。"}
            lines.append(f"「{name}」的进化路线：")
            for p in paths:
                lines.append("  " + self._fmt_chain(p, g["cond"]))
        return {"kind": "evolution", "ok": True, "answer": "\n".join(lines)}

    # ---------- 克制 ----------
    def answer_counter(self, question: str):
        sp = self.species_in(question)
        if not sp:
            if any(t in question for t in TYPES18):
                # 纯“属性克制哪些属性”类问题没有目标宝可梦，交给 GraphRAG 的 Type 事实路径。
                return None
            return {"kind": "counter", "ok": False, "answer": "未识别到目标宝可梦"}
        target = sp[-1]
        chart = self.type_chart()
        types = self.pokemon_default_types().get(target) or []
        if not types:
            return {"kind": "counter", "ok": False, "answer": f"未找到「{target}」的属性"}
        eff = {}
        for atk, defs in chart.items():
            m = 1.0
            for t in types:
                m *= defs.get(t, 1.0)
            if m != 1.0:
                eff[atk] = m
        strong = sorted([(a, m) for a, m in eff.items() if m >= 2], key=lambda x: -x[1])
        cand = self._counter_candidates({a for a, _ in strong})
        strong_txt = "、".join(f"{a}（{m:g}倍）" for a, m in strong) if strong else "无 ≥2 倍属性"
        lines = [f"「{target}」（{'/'.join(types)}）受击 ≥2 倍的攻击属性：{strong_txt}。"]
        if cand:
            lines.append("含上述属性的本系候选（默认形态）：" + "、".join(cand[:10]) + "。")
        return {"kind": "counter", "ok": True, "target": target, "types": types,
                "strong": strong, "candidates": cand, "answer": "\n".join(lines)}

    def _counter_candidates(self, strong_types):
        hits = []
        for name, types in self.pokemon_default_types().items():
            inter = strong_types & set(types)
            if inter:
                hits.append((len(inter), name, "/".join(types)))
        hits.sort(key=lambda x: (-x[0], x[1]))
        return [f"{n}（{t}）" for _, n, t in hits[:12]]

    # ---------- 关系 ----------
    def answer_relation(self, question: str):
        sp = self.species_in(question)
        # 情感极性过滤：问题含正面词→只看友好类边；含负面词→只看敌对/捕食类边
        pos = any(w in question for w in POS_WORDS) and not any(w in question for w in NEG_WORDS)
        neg = any(w in question for w in NEG_WORDS)
        allowed = None
        if pos:
            allowed = POS_RELS
        elif neg:
            allowed = NEG_RELS
        edges = [e for e in self.narrative_edges() if allowed is None or e["t"] in allowed]
        lines, found = [], False
        if len(sp) >= 2:
            a, b = sp[0], sp[1]
            for e in edges:
                if {e["a"], e["b"]} == {a, b}:
                    found = True
                    ev = str(e["ev"])[:60] if e["ev"] else ""
                    lines.append(f"「{a}」与「{b}」：{NARRATIVE_CN.get(e['t'], e['t'])}"
                                 + (f"（置信度 {e['cf']}；证据：{ev}…）" if ev else ""))
            if not found:
                tip = "友好/合作关系" if pos else ("敌对/竞争关系" if neg else "叙事关系")
                return {"kind": "relation", "ok": False,
                        "answer": f"数据里没有「{a}」与「{b}」的{tip}记录。"}
        elif len(sp) == 1:
            who = sp[0]
            for e in edges:
                if e["a"] == who or e["b"] == who:
                    found = True
                    other = e["b"] if e["a"] == who else e["a"]
                    ev = str(e["ev"])[:50] if e["ev"] else ""
                    lines.append(f"{NARRATIVE_CN.get(e['t'], e['t'])}：{other}"
                                 + (f"（证据：{ev}…）" if ev else ""))
            if not found:
                tip = "友好/合作关系" if pos else ("敌对/竞争关系" if neg else "叙事关系")
                return {"kind": "relation", "ok": False,
                        "answer": f"数据里没有「{who}」的{tip}记录（此类边为关键词抽取，覆盖有限）。"}
        return {"kind": "relation", "ok": True, "answer": "\n".join(lines)}
