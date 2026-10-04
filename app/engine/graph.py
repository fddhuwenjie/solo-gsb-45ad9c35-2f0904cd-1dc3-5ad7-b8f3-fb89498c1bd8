# -*- coding: utf-8 -*-
"""带关系类型的偏序图引擎。

维护地层/遗迹之间的有向关系，提供：

* 增加/删除关系时的有向环检测，并给出涉及新边的最短矛盾链；
* 每个对象在**本发掘区**所有约束下的最早层位（ASAP，本区最长前驱链）
  与最晚层位（ALAP，本区偏序高度减去本区最长后继链），二者相等意味着
  层位被完全锁定；跨区关系被禁止，每个发掘区是一张独立偏序，层位高度
  按区分别计算，一区的长链不会影响别区对象的最晚层位；
* 可互换对象组（同一区内偏序位置等价：祖先集合与后代集合完全相同，
  分属不同区的对象不会成组）；
* 增量重算：加边/删边后只失效、重建**所在区**的闭包与层位缓存，
  受影响（本区）节点集合随结果返回，别区缓存与推断保持不变。

时间语义统一为 ``early -> late``（早者指向晚者）。三类考古关系的
端点映射：

* ``earlier``（早于）：subject 早于 object，直接 early=subject；
* ``cuts``（切割）：切割者打破被切割者，切割活动更晚，early=object；
* ``fills``（填充）：填土/遗迹填充物晚于被填充的遗迹单位，early=object。
"""

from __future__ import annotations

import sys
from collections import deque

# 演示数据与可能的长链都需要较深的惰性 DFS
sys.setrecursionlimit(10000)

REL_TYPES = ("earlier", "cuts", "fills")

REL_LABEL = {
    "earlier": "早于",
    "cuts": "切割",
    "fills": "填充",
}

# 每种关系 (早端, 晚端) 的取法；True 表示取关系里的 subject
_EARLY_END = {"earlier": ("s", "o"), "cuts": ("o", "s"), "fills": ("o", "s")}

# 矛盾链里每条关系的可读描述，按“链上出现的顺序”（early -> late）
def edge_phrase(rtype: str, forward: bool, subject: str, object_: str) -> str:
    """生成关系在时间链上的文字说明。

    forward=True 时链方向为 subject -> object，否则为 object -> subject。
    """
    label = REL_LABEL.get(rtype, rtype)
    if rtype == "earlier":
        return f"{subject} {label} {object_}"
    # cuts / fills 在考古语义上 subject 是动作方（更晚）
    if forward:
        return f"{subject} {label} {object_}"
    return f"{object_} 被{label}（{subject}）"


class CycleError(Exception):
    """加入某条边会形成有向环。

    :param cycle_nodes: 形成环的节点序列，首尾相同，如 [a, b, c, a]
    :param cycle_edges: 与节点序列对应的关系 id 列表（长度 = len-1）
    :param detail: 每条边的可读说明
    """

    def __init__(self, cycle_nodes, cycle_edges, detail):
        self.cycle_nodes = cycle_nodes
        self.cycle_edges = cycle_edges
        self.detail = detail
        super().__init__("；".join(detail))


class Poset:
    def __init__(self, nodes=None):
        # node id -> {"id", "code", "kind", "name", "area_id"}
        self.nodes = {}
        # rel id -> dict（含 s/o/type，且 s/o 必须存在）
        self.rels = {}
        # 邻接表：early node -> {late node: [rel_id, ...]}（同一对节点
        # 可有多种关系类型）
        self.adj = {}
        self.rad = {}

        # —— 增量缓存 ——
        self._desc = {}      # node -> set(可达后代，含自身)
        self._anc = {}       # node -> set(祖先，含自身)
        self._early = {}     # node -> 最早层位
        self._late = {}      # node -> 最晚层位
        self._tail = {}      # node -> 从该节点出发的最长链边数
        # 跨区关系被禁止，每个发掘区各自构成一张独立偏序：高度必须按区
        # 分别计算，否则长链区会把孤立区对象的最晚层位“压”到本区之外。
        self._area_heights = {}  # area_id -> 该区偏序高度（最长链边数）
        self._height = None      # 全局高度 = max(各区高度)，仅供总览/布局

        self._next_node_id = 1
        self._next_rel_id = 1
        for n in nodes or ():
            self.add_node(n.get("code", ""), n.get("kind", "layer"),
                          n.get("name", ""), n.get("area_id"))

    # ------------------------------------------------------------------
    # 基础维护
    # ------------------------------------------------------------------
    def add_node(self, code, kind="layer", name="", area_id=None, node_id=None):
        if node_id is None:
            node_id = self._next_node_id
            self._next_node_id += 1
        else:
            node_id = int(node_id)
            self._next_node_id = max(self._next_node_id, node_id + 1)
        node_id = str(node_id)
        if area_id is not None:
            area_id = str(area_id)
        self.nodes[node_id] = {
            "id": node_id, "code": code, "kind": kind,
            "name": name, "area_id": area_id,
        }
        self.adj.setdefault(node_id, {})
        self.rad.setdefault(node_id, {})
        # 缓存一律在首次查询时惰性构建，避免预填值污染全局统计
        return node_id

    def remove_node(self, node_id):
        """删除节点及其全部关联关系。节点删除影响面大，直接重建缓存。"""
        node_id = str(node_id)
        if node_id not in self.nodes:
            return
        rel_ids = [rid for rid, r in self.rels.items()
                   if r["s"] == node_id or r["o"] == node_id]
        for rid in rel_ids:
            self._drop_rel_from_adj(rid)
            del self.rels[rid]
        self.nodes.pop(node_id, None)
        self.adj.pop(node_id, None)
        self.rad.pop(node_id, None)
        for frm in self.adj.values():
            frm.pop(node_id, None)
        for to in self.rad.values():
            to.pop(node_id, None)
        self._reset_caches()

    def _reset_caches(self):
        self._desc = {}
        self._anc = {}
        self._early = {}
        self._late = {}
        self._tail = {}
        self._area_heights = {}
        self._height = None

    def _invalidate_area_layers(self, area_id):
        """一区结构变更后，仅失效该区的层位缓存；别区推断完全不变。

        可达闭包（_anc/_desc）由 :meth:`_invalidate_area_closures` 同批
        失效；跨区无边，两类缓存都不会越区。
        """
        if area_id is None:
            self._early = {}
            self._late = {}
            self._tail = {}
            self._area_heights = {}
        else:
            for nid, n in self.nodes.items():
                if n.get("area_id") == area_id:
                    self._early.pop(nid, None)
                    self._late.pop(nid, None)
                    self._tail.pop(nid, None)
            self._area_heights.pop(area_id, None)
        self._height = None

    @staticmethod
    def endpoints(rtype, s, o):
        es, eo = _EARLY_END[rtype]
        early = s if es == "s" else o
        late = s if eo == "s" else o
        return early, late

    def _drop_rel_from_adj(self, rid):
        r = self.rels[rid]
        early, late = self.endpoints(r["type"], r["s"], r["o"])
        bucket = self.adj.get(early, {}).get(late)
        if bucket and rid in bucket:
            bucket.remove(rid)
            if not bucket:
                del self.adj[early][late]
        rbucket = self.rad.get(late, {}).get(early)
        if rbucket and rid in rbucket:
            rbucket.remove(rid)
            if not rbucket:
                del self.rad[late][early]

    def _put_rel_in_adj(self, rid):
        r = self.rels[rid]
        early, late = self.endpoints(r["type"], r["s"], r["o"])
        self.adj.setdefault(early, {}).setdefault(late, []).append(rid)
        self.rad.setdefault(late, {}).setdefault(early, []).append(rid)

    # ------------------------------------------------------------------
    # 最短路径 / 环检测
    # ------------------------------------------------------------------
    def shortest_path(self, src, dst, exclude_edge=None):
        """BFS 求 src -> dst 的最短有向路径。

        :param exclude_edge: 可选；求“去掉某条关系后”的路径时传入该
            关系 id（当两节点间还有其他类型关系时边仍存在）。
        返回 (nodes, rel_ids) 或 None。节点按确定性顺序遍历，
        保证多条最短时结果稳定。
        """
        if src == dst:
            return [src], []
        prev_node = {src: None}
        prev_rel = {}
        q = deque([src])
        while q:
            u = q.popleft()
            for v in sorted(self.adj.get(u, {})):
                if v in prev_node:
                    continue
                rids = [r for r in self.adj[u][v]
                        if exclude_edge is None or r != exclude_edge]
                if not rids:
                    continue
                prev_node[v] = u
                prev_rel[v] = rids[0]
                if v == dst:
                    nodes = [v]
                    edges = []
                    while prev_node[nodes[-1]] is not None:
                        edges.append(prev_rel[nodes[-1]])
                        nodes.append(prev_node[nodes[-1]])
                    nodes.reverse()
                    edges.reverse()
                    return nodes, edges
                q.append(v)
        return None

    # ------------------------------------------------------------------
    # 关系增删（含环检测与增量缓存维护）
    # ------------------------------------------------------------------
    def check_cycle(self, rtype, s, o):
        """只读环检测：返回 (cycle_nodes, cycle_edges, detail) 或 None。

        用于提交前校验，不修改图结构。cycle_nodes 首尾相同，
        cycle_edges 为路径上的关系 id（末位 None 代表拟议新边），
        detail 为每条边按时间方向的可读说明。
        """
        s, o = str(s), str(o)
        early, late = self.endpoints(rtype, s, o)
        path = self.shortest_path(late, early)
        if path is None:
            return None
        pnodes, pedges = path
        cycle_nodes = pnodes + [late]
        detail = []
        for i, rid in enumerate(pedges):
            rr = self.rels[rid]
            fwd = (rr["s"], rr["o"]) == (pnodes[i], pnodes[i + 1])
            detail.append(edge_phrase(
                rr["type"], fwd,
                self.nodes[rr["s"]]["code"],
                self.nodes[rr["o"]]["code"]))
        detail.append(edge_phrase(
            rtype, (s, o) == (early, late),
            self.nodes[s]["code"], self.nodes[o]["code"]))
        return cycle_nodes, pedges + [None], detail

    def add_rel(self, rtype, s, o, rel_id=None):
        s, o = str(s), str(o)
        if rtype not in _EARLY_END:
            raise ValueError(f"未知关系类型：{rtype}")
        if s == o:
            raise ValueError("自环：关系的两个端点必须是不同对象")
        if s not in self.nodes or o not in self.nodes:
            raise ValueError("关系引用了不存在的对象")

        # 同类型关系唯一
        dup = next((rid for rid, r in self.rels.items()
                    if r["s"] == s and r["o"] == o
                    and r["type"] == rtype), None)
        if dup is not None:
            raise ValueError(f"该关系已存在（R{dup}）")

        cycle = self.check_cycle(rtype, s, o)
        if cycle is not None:
            raise CycleError(*cycle)

        early, late = self.endpoints(rtype, s, o)
        if rel_id is None:
            rel_id = self._next_rel_id
            self._next_rel_id += 1
        rel_id = str(int(rel_id))
        self._next_rel_id = max(self._next_rel_id, int(rel_id) + 1)

        existed_pair = bool(self.adj.get(early, {}).get(late))
        self.rels[rel_id] = {"id": rel_id, "s": s, "o": o, "type": rtype}
        self._put_rel_in_adj(rel_id)

        if existed_pair:
            # 时间边已存在（只是补充另一种关系类型），偏序结构不变
            affected = set()
        else:
            affected = self._incremental_add_edge(early, late)
        return rel_id, affected

    def _incremental_add_edge(self, early, late):
        """加一条新的时间边后的增量处理。

        新可达对恰为 ``Anc(early) × Desc(late)``（加边前图上的闭包），
        这也是需要在前端高亮的受影响节点。

        闭包缓存不做就地并合并：删边只会失效“当时可达性发生变化”的
        节点缓存，而与早期断边同处一区、当时无关的节点缓存里可能仍含
        陈旧条目（例如 x2 曾可达 x0，边删除后 x0 的祖先缓存未动，日后
        新增一条进入 x0 的边时，若按 ``旧缓存 | 新祖先`` 合并，就会把
        已消失的 x2 重新带回）。因此结构变更时本区闭包缓存整体失效、
        下次查询惰性重建；跨区无边，只影响本区，别区缓存与推断不变。
        """
        anc_e = self.ancestors(early)
        desc_l = self.descendants(late)
        affected = set(anc_e) | set(desc_l)

        self._invalidate_area_closures(self.nodes[early].get("area_id"))
        # 层位缓存在结构变化后统一失效，下次查询时沿新边惰性重算。
        self._invalidate_area_layers(self.nodes[early].get("area_id"))
        return affected

    def remove_rel(self, rid):
        rid = str(rid)
        r = self.rels.get(rid)
        if r is None:
            return set()
        early, late = self.endpoints(r["type"], r["s"], r["o"])

        # 先在“删边前”的现存图上做无缓存 BFS。删边后唯一可能消失的
        # 可达对恰为 Anc(early) × Desc(late)：任何新断开的 x→y 路径，
        # 其尾段必含 early→late。不能改用删边后的图，也不能从
        # _anc/_desc 缓存反推——前者会漏掉仅经此边可达的节点，
        # 后者本身可能已含正要消失的可达对（链式删边时的陈旧条目）。
        A = self._bfs(early, self.rad)   # 删边前 early 的祖先
        D = self._bfs(late, self.adj)    # 删边前 late 的后代
        parallel = len(self.adj.get(early, {}).get(late, ())) > 1

        self._drop_rel_from_adj(rid)
        del self.rels[rid]

        affected = set()
        if not parallel:
            # 该对节点间已无任何关系，时间边真正消失。
            area_id = self.nodes[early].get("area_id")
            # 闭包缓存按区失效：可能变短的条目恰为 A 中节点的 _desc、
            # D 中节点的 _anc；为稳妥起见（陈旧缓存可能牵连更多条目），
            # 直接清本区全部闭包——跨区无边，闭包不会越区，别区缓存与
            # 推断全部保留，下次查询惰性重建。
            self._invalidate_area_closures(area_id)
            # 受影响节点（前端用于“只重算高亮”）：可达性或最长出链可能
            # 变化的节点 = A ∪ D。
            affected = set(A) | set(D)
            # 本区层位缓存整体失效、按需惰性重算；别区推断保持不变。
            self._invalidate_area_layers(area_id)
        return affected

    def _bfs(self, src, neighbors):
        """在现存邻接表上从 src 出发的无缓存可达集合（含自身）。"""
        seen = {src}
        q = deque([src])
        while q:
            u = q.popleft()
            for v in neighbors.get(u, {}):
                if v not in seen:
                    seen.add(v)
                    q.append(v)
        return seen

    def _invalidate_area_closures(self, area_id):
        """失效本区全部祖先/后代闭包缓存（别区不动）。"""
        if area_id is None:
            self._desc = {}
            self._anc = {}
            return
        for nid, n in self.nodes.items():
            if n.get("area_id") == area_id:
                self._desc.pop(nid, None)
                self._anc.pop(nid, None)

    # ------------------------------------------------------------------
    # 可达性（惰性 DFS，带记忆）
    # ------------------------------------------------------------------
    def descendants(self, node):
        cached = self._desc.get(node)
        if cached is not None:
            return cached
        acc = {node}
        for nxt in self.adj.get(node, {}):
            acc |= self.descendants(nxt)
        self._desc[node] = acc
        return acc

    def ancestors(self, node):
        cached = self._anc.get(node)
        if cached is not None:
            return cached
        acc = {node}
        for prv in self.rad.get(node, {}):
            acc |= self.ancestors(prv)
        self._anc[node] = acc
        return acc

    # ------------------------------------------------------------------
    # 层位推断
    # ------------------------------------------------------------------
    def earliest(self, node):
        """最早层位：到该节点的最长链长度（0 = 可作为最早单位）。"""
        cached = self._early.get(node)
        if cached is not None:
            return cached
        best = 0
        for prv in self.rad.get(node, {}):
            best = max(best, self.earliest(prv) + 1)
        self._early[node] = best
        return best

    def _area_ids(self):
        """图中出现的全部发掘区 id（未归属对象归入 None 桶）。"""
        return {n.get("area_id") for n in self.nodes.values()}

    def _area_height(self, area_id):
        """某发掘区偏序高度 = 区内最长链边数。

        跨区关系被禁止，不同区的层位互不可比；直接遍历该区节点的
        tail_len，而不是复用 earliest 缓存——未查询过的节点没有缓存，
        取缓存最大值会漏算。
        """
        cached = self._area_heights.get(area_id)
        if cached is not None:
            return cached
        h = 0
        for nid, n in self.nodes.items():
            if n.get("area_id") == area_id:
                h = max(h, self._tail_len(nid))
        self._area_heights[area_id] = h
        return h

    def area_height(self, node):
        """节点所在区的偏序高度。"""
        return self._area_height(self.nodes[node].get("area_id"))

    def _get_height(self):
        """全局高度 = 各区高度的最大值（供总览/整体布局使用）。

        层位窗口的实际计算用 :meth:`area_height` 按区取高度，
        避免长链区影响孤立区对象的最晚层位。
        """
        if self._height is not None:
            return self._height
        h = 0
        for area_id in self._area_ids():
            h = max(h, self._area_height(area_id))
        self._height = h
        return h

    def _tail_len(self, node):
        """从 node 出发的最长链边数（记忆化）。"""
        if node in self._tail:
            return self._tail[node]
        best = 0
        for nxt in self.adj.get(node, {}):
            best = max(best, self._tail_len(nxt) + 1)
        self._tail[node] = best
        return best

    def latest(self, node):
        """最晚层位：所在区高度 - 从该节点出发的最长链长度。

        层位编号在每个发掘区内各自从最早（0）到最晚（area_height），
        不跨区比较。earliest == latest 时该单位的相对位置在本区约束下
        被完全锁定；窗口越大，可浮动空间越大。
        """
        cached = self._late.get(node)
        if cached is not None:
            return cached
        val = self._area_height(self.nodes[node].get("area_id")) \
            - self._tail_len(node)
        self._late[node] = val
        return val

    # ------------------------------------------------------------------
    # 可互换对象组
    # ------------------------------------------------------------------
    def interchangeable_groups(self):
        """位置等价类：同发掘区内、严格祖先集合与严格后代集合完全相同
        的节点。

        必须先按区划分再比较闭包：跨区对象之间没有任何时间边，分属
        不同区的孤立对象虽各自的祖先/后代集合都为空，却并不可互换，
        因为它们参与的是两套互相独立的偏序。

        （使用去掉自身的集合：组内两节点不可比，故彼此不在对方的
        祖先/后代中，其余前驱后继则必须完全一致。）
        同组节点可在本区全部线性序中互换排列，即“仍可互换的对象组”。
        仅返回大小 >= 2 的组。
        """
        groups = {}
        for n in self.nodes:
            key = (self.nodes[n].get("area_id"),
                   frozenset(self.ancestors(n) - {n}),
                   frozenset(self.descendants(n) - {n}))
            groups.setdefault(key, []).append(n)
        result = []
        for (area_id, _, _), members in groups.items():
            if len(members) >= 2:
                members_sorted = sorted(
                    members,
                    key=lambda x: (self.nodes[x].get("area_id") or "",
                                   self.nodes[x]["code"]))
                result.append({
                    "members": members_sorted,
                    "area_id": area_id,
                    "earliest": self.earliest(members_sorted[0]),
                    "latest": self.latest(members_sorted[0]),
                })
        result.sort(key=lambda g: (g.get("area_id") or "",
                                   g["earliest"], g["members"][0]))
        return result
