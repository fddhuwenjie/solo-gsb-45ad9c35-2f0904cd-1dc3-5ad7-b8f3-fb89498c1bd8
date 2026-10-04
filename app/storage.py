# -*- coding: utf-8 -*-
"""SQLite 持久层：建表、读写对象/关系/版本/事件日志。"""

import json
import sqlite3
from datetime import datetime, timezone


SCHEMA = """
CREATE TABLE IF NOT EXISTS areas (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    code    TEXT NOT NULL UNIQUE,
    name    TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS units (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    area_id  INTEGER NOT NULL REFERENCES areas(id),
    code     TEXT NOT NULL,
    kind     TEXT NOT NULL DEFAULT 'layer',   -- layer 地层 / feature 遗迹
    name     TEXT NOT NULL DEFAULT '',
    UNIQUE(area_id, code)
);

CREATE TABLE IF NOT EXISTS relations (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    subject   INTEGER NOT NULL REFERENCES units(id),
    object    INTEGER NOT NULL REFERENCES units(id),
    type      TEXT NOT NULL,                  -- earlier / cuts / fills
    note      TEXT NOT NULL DEFAULT '',
    created   TEXT NOT NULL,
    UNIQUE(subject, object, type)
);

CREATE TABLE IF NOT EXISTS versions (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    created  TEXT NOT NULL,
    label    TEXT NOT NULL DEFAULT '',
    action   TEXT NOT NULL,
    snapshot TEXT NOT NULL                    -- 完整图快照（JSON）
);

CREATE TABLE IF NOT EXISTS events (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    created  TEXT NOT NULL,
    action   TEXT NOT NULL,
    status   TEXT NOT NULL,                   -- ok / rejected
    detail   TEXT NOT NULL DEFAULT ''
);
"""


def now_iso():
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def get_db(path):
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(conn):
    conn.executescript(SCHEMA)
    conn.commit()


# ---------------------------------------------------------------------
# 读取
# ---------------------------------------------------------------------
def list_areas(conn):
    rows = conn.execute("SELECT * FROM areas ORDER BY code")
    return [dict(r) for r in rows]


def list_units(conn):
    rows = conn.execute(
        "SELECT u.*, a.code AS area_code FROM units u "
        "JOIN areas a ON a.id = u.area_id ORDER BY a.code, u.code")
    return [dict(r) for r in rows]


def list_relations(conn):
    rows = conn.execute(
        "SELECT r.*, s.code AS s_code, o.code AS o_code, "
        "sa.code AS s_area, oa.code AS o_area "
        "FROM relations r "
        "JOIN units s ON s.id = r.subject JOIN units o ON o.id = r.object "
        "JOIN areas sa ON sa.id = s.area_id JOIN areas oa ON oa.id = o.area_id "
        "ORDER BY r.id")
    return [dict(r) for r in rows]


def list_versions(conn, limit=50):
    rows = conn.execute(
        "SELECT id, created, label, action FROM versions "
        "ORDER BY id DESC LIMIT ?", (limit,))
    return [dict(r) for r in rows]


def list_events(conn, limit=50):
    rows = conn.execute(
        "SELECT * FROM events ORDER BY id DESC LIMIT ?", (limit,))
    return [dict(r) for r in rows]


def get_version(conn, vid):
    row = conn.execute("SELECT * FROM versions WHERE id=?", (vid,)).fetchone()
    if row is None:
        return None
    data = dict(row)
    data["snapshot"] = json.loads(data["snapshot"])
    return data


# ---------------------------------------------------------------------
# 写入
# ---------------------------------------------------------------------
def insert_area(conn, code, name=""):
    cur = conn.execute("INSERT INTO areas(code, name) VALUES (?,?)",
                       (code, name))
    return cur.lastrowid


def insert_unit(conn, area_id, code, kind, name=""):
    cur = conn.execute(
        "INSERT INTO units(area_id, code, kind, name) VALUES (?,?,?,?)",
        (area_id, code, kind, name))
    return cur.lastrowid


def insert_relation(conn, subject, object_, type_, note=""):
    cur = conn.execute(
        "INSERT INTO relations(subject, object, type, note, created) "
        "VALUES (?,?,?,?,?)", (subject, object_, type_, note, now_iso()))
    return cur.lastrowid


def delete_relation(conn, rid):
    conn.execute("DELETE FROM relations WHERE id=?", (rid,))


def delete_unit_relations(conn, uid):
    conn.execute("DELETE FROM relations WHERE subject=? OR object=?", (uid, uid))


def delete_unit(conn, uid):
    conn.execute("DELETE FROM units WHERE id=?", (uid,))


def save_version(conn, label, action, snapshot):
    cur = conn.execute(
        "INSERT INTO versions(created, label, action, snapshot) "
        "VALUES (?,?,?,?)",
        (now_iso(), label, action, json.dumps(snapshot, ensure_ascii=False)))
    return cur.lastrowid


def log_event(conn, action, status, detail=""):
    conn.execute(
        "INSERT INTO events(created, action, status, detail) VALUES (?,?,?,?)",
        (now_iso(), action, status, detail))
