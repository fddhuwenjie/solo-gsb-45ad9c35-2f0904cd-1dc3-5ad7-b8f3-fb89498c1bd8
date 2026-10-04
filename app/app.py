# -*- coding: utf-8 -*-
"""考古地层关系推断台 —— Flask 应用。

接口总览（均返回 JSON）：

GET  /api/state                 区域、对象、关系、层位推断、可互换组、图
POST /api/areas                 新建发掘区
POST /api/units                 新建地层/遗迹
DELETE /api/units/<id>          删除对象（连同其关系）
POST /api/relations             录入关系（校验跨区/自环/重复/有向环）
DELETE /api/relations/<id>      删除关系
GET  /api/relations/<id>/basis  某条关系成立的推断依据（可达链）
GET  /api/versions              版本列表
GET  /api/versions/<id>         查看某版本快照
POST /api/versions/<id>/restore 回滚到某版本
GET  /api/events                操作/拒绝事件日志
"""

import os
import threading

from flask import Flask, jsonify, request, send_from_directory

from engine.graph import (
    REL_TYPES, REL_LABEL, Poset,
)
import storage

DB_PATH = os.environ.get("DATABASE_PATH", "/data/strata.db")

app = Flask(__name__, static_folder="static", static_url_path="")

# 引擎内存在单进程内常驻，承载增量缓存；写操作加锁串行化
_LOCK = threading.RLock()
_ENGINE = Poset()
_DB = None


# ---------------------------------------------------------------------
# 引擎与数据库同步
# ---------------------------------------------------------------------
def db():
    global _DB
    if _DB is None:
        need_seed = not os.path.exists(DB_PATH)
        os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
        _DB = storage.get_db(DB_PATH)
        storage.init_db(_DB)
        if need_seed:
            _seed_demo(_DB)
    return _DB


def reload_engine(conn=None):
    """从数据库完整重建引擎（启动/回滚后使用）。"""
    conn = conn or db()
    p = Poset()
    for u in storage.list_units(conn):
        p.add_node(u["code"], u["kind"], u["name"], str(u["area_id"]),
                   node_id=u["id"])
    for r in storage.list_relations(conn):
        p.rels[str(r["id"])] = {
            "id": str(r["id"]), "s": str(r["subject"]),
            "o": str(r["object"]), "type": r["type"],
        }
        p._put_rel_in_adj(str(r["id"]))
    p._reset_caches()
    return p


def ensure_engine():
    if not _ENGINE.nodes:
        rebuild()
    return _ENGINE


def rebuild():
    global _ENGINE
    _ENGINE = reload_engine()
    return _ENGINE


# ---------------------------------------------------------------------
# 演示数据
# ---------------------------------------------------------------------
def _seed_demo(conn):
    """首次启动写入一个典型 Harris 矩阵样例。"""
    a1 = storage.insert_area(conn, "T1", "一号发掘区")
    a2 = storage.insert_area(conn, "T2", "二号发掘区（跨区校验用）")
    units = {}
    def unit(code, kind="layer", name="", area=a1):
        uid = storage.insert_unit(conn, area, code, kind, name)
        units[code] = uid
        return uid
    # 由晚到早编号的自然堆积：L4 最下（最早）→ L1 耕土（最晚）
    unit("L4", "layer", "原生生土层")
    unit("L3", "layer", "下层文化堆积")
    unit("L2", "layer", "上层文化堆积")
    unit("L1", "layer", "耕土层")
    unit("H1", "feature", "灰坑（打破 L3，内有填土）")
    unit("F1", "feature", "H1 填土")
    unit("H2", "feature", "柱洞 A（打破 L2）")
    unit("H3", "feature", "柱洞 B（打破 L2，与 H2 关系不明）")
    unit("G1", "layer", "T2 地层", area=a2)

    def rel(t, s, o, note=""):
        storage.insert_relation(conn, units[s], units[o], t, note)

    rel("earlier", "L4", "L3")
    rel("earlier", "L3", "L2")
    rel("earlier", "L2", "L1")
    rel("cuts", "H1", "L3", "灰坑打破下层堆积")
    rel("fills", "F1", "H1", "坑内填土晚于坑体")
    rel("earlier", "F1", "L2", "填土形成后才叠压上层堆积")
    rel("cuts", "H2", "L2")
    rel("cuts", "H3", "L2")
    storage.save_version(conn, "初始演示数据", "seed",
                         {"areas": storage.list_areas(conn),
                          "units": storage.list_units(conn),
                          "relations": storage.list_relations(conn)})
    storage.log_event(conn, "seed", "ok", "写入演示数据")
    conn.commit()


# ---------------------------------------------------------------------
# 状态序列化
# ---------------------------------------------------------------------
def _edge_view(p, rel_id):
    r = p.rels[str(rel_id)]
    early, late = p.endpoints(r["type"], r["s"], r["o"])
    return {
        "id": r["id"],
        "subject": r["s"],
        "object": r["o"],
        "type": r["type"],
        "type_label": REL_LABEL[r["type"]],
        "early": early,
        "late": late,
        "forward": (r["s"], r["o"]) == (early, late),
    }


def build_state(affected=None):
    p = ensure_engine()
    conn = db()
    units = {u["id"]: u for u in storage.list_units(conn)}
    rels_db = {r["id"]: r for r in storage.list_relations(conn)}
    area_rows = storage.list_areas(conn)
    area_heights = p.area_heights()

    nodes = []
    for nid, n in sorted(p.nodes.items(), key=lambda kv: int(kv[0])):
        e, l = p.earliest(nid), p.latest(nid)
        u = units.get(int(nid), {})
        nodes.append({
            "id": nid,
            "code": n["code"],
            "kind": n["kind"],
            "name": n.get("name", ""),
            "area_id": n.get("area_id"),
            "area_code": u.get("area_code", ""),
            "earliest": e,
            "latest": l,
            # 层位窗口按所在区偏序计算：本区高度决定最晚层位
            "area_height": area_heights.get(n.get("area_id"), 0),
            "fixed": e == l,
            "slack": l - e,
        })

    edges = []
    for rid in sorted(p.rels, key=int):
        view = _edge_view(p, rid)
        row = rels_db.get(int(rid), {})
        view["note"] = row.get("note", "")
        view["created"] = row.get("created", "")
        edges.append(view)

    groups = []
    for g in p.interchangeable_groups():
        members = g["members"]
        area_row = next((a for a in area_rows
                         if str(a["id"]) == str(g["area_id"])), None)
        groups.append({
            "members": members,
            "area_id": g["area_id"],
            "area_code": area_row["code"] if area_row else "",
            "earliest": g["earliest"],
            "latest": g["latest"],
            "codes": [p.nodes[m]["code"] for m in members],
        })

    return {
        "areas": area_rows,
        "nodes": nodes,
        "edges": edges,
        "groups": groups,
        "height": p._get_height(),
        "area_heights": {str(a): h for a, h in area_heights.items()},
        "relation_types": [{"value": t, "label": REL_LABEL[t]}
                           for t in REL_TYPES],
        "affected": sorted(affected or [], key=int),
    }


def snapshot_db(conn=None):
    conn = conn or db()
    return {
        "areas": storage.list_areas(conn),
        "units": storage.list_units(conn),
        "relations": storage.list_relations(conn),
    }


def commit_version(label, action):
    conn = db()
    vid = storage.save_version(conn, label, action, snapshot_db(conn))
    conn.commit()
    return vid


# ---------------------------------------------------------------------
# 页面
# ---------------------------------------------------------------------
@app.get("/")
def index():
    return send_from_directory(app.static_folder, "index.html")


@app.get("/api/state")
def api_state():
    with _LOCK:
        return jsonify(build_state())


# ---------------------------------------------------------------------
# 区域 / 对象
# ---------------------------------------------------------------------
@app.post("/api/areas")
def api_create_area():
    data = request.get_json(force=True)
    code = (data.get("code") or "").strip()
    name = (data.get("name") or "").strip()
    if not code:
        return jsonify({"error": "区域编号不能为空"}), 400
    with _LOCK:
        conn = db()
        try:
            aid = storage.insert_area(conn, code, name)
            conn.commit()
        except Exception as exc:
            conn.rollback()
            return jsonify({"error": f"区域已存在：{exc}"}), 409
        storage.log_event(conn, "create_area", "ok", code)
        conn.commit()
    return jsonify({"id": aid, "ok": True}), 201


@app.post("/api/units")
def api_create_unit():
    data = request.get_json(force=True)
    code = (data.get("code") or "").strip()
    kind = data.get("kind", "layer")
    name = (data.get("name") or "").strip()
    area_id = data.get("area_id")
    if not code:
        return jsonify({"error": "对象编号不能为空"}), 400
    if kind not in ("layer", "feature"):
        return jsonify({"error": "类型必须是 layer 或 feature"}), 400
    if area_id is None:
        return jsonify({"error": "必须指定发掘区"}), 400
    with _LOCK:
        conn = db()
        exists = conn.execute("SELECT 1 FROM areas WHERE id=?",
                              (area_id,)).fetchone()
        if not exists:
            return jsonify({"error": "发掘区不存在"}), 400
        try:
            uid = storage.insert_unit(conn, area_id, code, kind, name)
            conn.commit()
        except Exception:
            conn.rollback()
            return jsonify({"error": f"本区已有编号 {code}"}), 409
        nid = ensure_engine().add_node(code, kind, name, str(area_id),
                                       node_id=uid)
        storage.log_event(conn, "create_unit", "ok", code)
        conn.commit()
        commit_version(f"新增对象 {code}", "create_unit")
        return jsonify({"id": nid, "ok": True, **build_state()}), 201


@app.delete("/api/units/<uid>")
def api_delete_unit(uid):
    with _LOCK:
        conn = db()
        row = conn.execute("SELECT code FROM units WHERE id=?",
                           (uid,)).fetchone()
        if row is None:
            return jsonify({"error": "对象不存在"}), 404
        code = row["code"]
        p = ensure_engine()
        storage.delete_unit_relations(conn, uid)
        storage.delete_unit(conn, uid)
        p.remove_node(str(uid))
        storage.log_event(conn, "delete_unit", "ok", code)
        conn.commit()
        commit_version(f"删除对象 {code}", "delete_unit")
        return jsonify({"ok": True, **build_state()})


# ---------------------------------------------------------------------
# 关系
# ---------------------------------------------------------------------
@app.post("/api/relations")
def api_create_relation():
    data = request.get_json(force=True)
    rtype = data.get("type")
    s = data.get("subject")
    o = data.get("object")
    note = (data.get("note") or "").strip()
    if rtype not in REL_TYPES:
        return jsonify({"error": f"关系类型必须是 {REL_TYPES} 之一"}), 400
    with _LOCK:
        conn = db()
        su = conn.execute("SELECT * FROM units WHERE id=?", (s,)).fetchone()
        ou = conn.execute("SELECT * FROM units WHERE id=?", (o,)).fetchone()
        if su is None or ou is None:
            return jsonify({"error": "关系端点不存在"}), 404

        # 自环
        if int(s) == int(o):
            storage.log_event(conn, "create_relation", "rejected",
                              f"自环：{su['code']} -> {su['code']}")
            conn.commit()
            return jsonify({
                "error": "不能建立自环：关系的两个端点必须是不同对象",
                "code": "self_loop",
            }), 409

        # 跨发掘区
        if su["area_id"] != ou["area_id"]:
            storage.log_event(
                conn, "create_relation", "rejected",
                f"跨区引用：{su['code']}({su['area_id']}) -> "
                f"{ou['code']}({ou['area_id']})")
            conn.commit()
            return jsonify({
                "error": f"拒绝跨发掘区引用：{su['code']} 属于 "
                         f"{su['area_id']} 区，{ou['code']} 属于 "
                         f"{ou['area_id']} 区",
                "code": "cross_area",
            }), 409

        p = ensure_engine()

        # 重复关系
        dup = conn.execute(
            "SELECT id FROM relations WHERE subject=? AND object=? AND type=?",
            (s, o, rtype)).fetchone()
        if dup:
            return jsonify({
                "error": f"关系已存在（R{dup['id']}："
                         f"{su['code']} {REL_LABEL[rtype]} {ou['code']}）",
                "code": "duplicate",
            }), 409

        # 环检测（只读，不改动引擎与数据库）
        cycle = p.check_cycle(rtype, str(s), str(o))
        if cycle is not None:
            cycle_nodes, _edge_ids, details = cycle
            codes = [p.nodes[x]["code"] for x in cycle_nodes]
            chain = [{
                "from": codes[i],
                "to": codes[i + 1],
                "detail": details[i],
            } for i in range(len(details))]
            storage.log_event(
                conn, "create_relation", "rejected",
                f"有向环：{' -> '.join(codes)}")
            conn.commit()
            return jsonify({
                "error": "该关系与已有约束构成矛盾（有向环），已拒绝",
                "code": "cycle",
                "cycle": codes,
                "chain": chain,
                "length": len(details),
            }), 409

        # 通过校验：数据库分配 id，引擎使用同一 id 装入（不会再抛环异常）
        rid_db = storage.insert_relation(conn, s, o, rtype, note)
        rid, affected = p.add_rel(rtype, str(s), str(o), rel_id=rid_db)

        storage.log_event(
            conn, "create_relation", "ok",
            f"{su['code']} {REL_LABEL[rtype]} {ou['code']}")
        conn.commit()
        commit_version(
            f"{su['code']} {REL_LABEL[rtype]} {ou['code']}",
            "create_relation")
        return jsonify({"ok": True, "id": rid,
                        **build_state(affected)}), 201


@app.delete("/api/relations/<rid>")
def api_delete_relation(rid):
    with _LOCK:
        conn = db()
        row = conn.execute(
            "SELECT r.*, s.code AS sc, o.code AS oc FROM relations r "
            "JOIN units s ON s.id=r.subject JOIN units o ON o.id=r.object "
            "WHERE r.id=?", (rid,)).fetchone()
        if row is None:
            return jsonify({"error": "关系不存在"}), 404
        p = ensure_engine()
        affected = p.remove_rel(str(rid))
        storage.delete_relation(conn, rid)
        storage.log_event(
            conn, "delete_relation", "ok",
            f"{row['sc']} {REL_LABEL[row['type']]} {row['oc']}")
        conn.commit()
        commit_version(
            f"删除关系 {row['sc']} {REL_LABEL[row['type']]} {row['oc']}",
            "delete_relation")
        return jsonify({"ok": True, **build_state(affected)})


@app.get("/api/basis")
def api_basis():
    """推断依据：from -> to 的最短时间路径。

    参数 from、to 为单位 id。直接有时间边时 direct=true；
    否则若存在多跳路径，返回最短间接推断链。
    """
    a = request.args.get("from")
    b = request.args.get("to")
    with _LOCK:
        p = ensure_engine()
        if a not in p.nodes or b not in p.nodes:
            return jsonify({"error": "对象不存在"}), 404
        direct_edges = p.adj.get(a, {}).get(b, [])
        path = p.shortest_path(a, b)
        if path is None:
            return jsonify({"reachable": False, "direct": False})
        nodes, edges = path
        return jsonify({
            "reachable": True,
            "direct": len(nodes) == 2 and bool(direct_edges),
            "nodes": nodes,
            "codes": [p.nodes[n]["code"] for n in nodes],
            "edges": [_edge_view(p, eid) for eid in edges],
        })


@app.get("/api/relations/<rid>/basis")
def api_relation_basis(rid):
    """某条已存关系的间接推断依据：求“去掉该条关系后”
    其早端 -> 晚端的最短路径（若该对节点还有其他类型关系，
    时间边仍然存在，会返回长度 1）。
    """
    with _LOCK:
        conn = db()
        row = conn.execute("SELECT * FROM relations WHERE id=?",
                           (rid,)).fetchone()
        if row is None:
            return jsonify({"error": "关系不存在"}), 404
        p = ensure_engine()
        early, late = p.endpoints(row["type"], str(row["subject"]),
                                  str(row["object"]))
        path = p.shortest_path(early, late, exclude_edge=str(rid))
        if path is None:
            return jsonify({"path": None, "nodes": [], "edges": []})
        nodes, edges = path
        edge_info = [_edge_view(p, eid) for eid in edges]
        return jsonify({
            "path": True,
            "nodes": nodes,
            "codes": [p.nodes[n]["code"] for n in nodes],
            "edges": edge_info,
        })


# ---------------------------------------------------------------------
# 版本
# ---------------------------------------------------------------------
@app.get("/api/versions")
def api_versions():
    with _LOCK:
        return jsonify({"versions": storage.list_versions(db())})


@app.get("/api/versions/<vid>")
def api_version_detail(vid):
    with _LOCK:
        v = storage.get_version(db(), vid)
        if v is None:
            return jsonify({"error": "版本不存在"}), 404
        return jsonify(v)


@app.post("/api/versions/<vid>/restore")
def api_restore(vid):
    with _LOCK:
        conn = db()
        v = storage.get_version(conn, vid)
        if v is None:
            return jsonify({"error": "版本不存在"}), 404
        conn.execute("DELETE FROM relations")
        conn.execute("DELETE FROM units")
        conn.execute("DELETE FROM areas")
        snap = v["snapshot"]
        for a in snap.get("areas", []):
            conn.execute(
                "INSERT INTO areas(id, code, name) VALUES (?,?,?)",
                (a["id"], a["code"], a.get("name", "")))
        for u in snap["units"]:
            conn.execute(
                "INSERT INTO units(id, area_id, code, kind, name) "
                "VALUES (?,?,?,?,?)",
                (u["id"], u["area_id"], u["code"], u["kind"], u["name"]))
        for r in snap["relations"]:
            conn.execute(
                "INSERT INTO relations(id, subject, object, type, note, created)"
                " VALUES (?,?,?,?,?,?)",
                (r["id"], r["subject"], r["object"], r["type"],
                 r.get("note", ""), r.get("created", storage.now_iso())))
        conn.commit()
        rebuild()
        storage.log_event(conn, "restore_version", "ok", f"R{vid}")
        conn.commit()
        new_vid = commit_version(f"回滚到版本 R{vid}", "restore")
        return jsonify({"ok": True, "restored_from": vid,
                        "new_version": new_vid, **build_state()})


# ---------------------------------------------------------------------
# 事件
# ---------------------------------------------------------------------
@app.get("/api/events")
def api_events():
    with _LOCK:
        return jsonify({"events": storage.list_events(db())})


if __name__ == "__main__":
    ensure_engine()
    app.run(host="0.0.0.0", port=8000)
