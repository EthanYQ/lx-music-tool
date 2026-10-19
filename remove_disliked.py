# -*- coding: utf-8 -*-
"""
LX Music 清理工具：把歌单里已被标记「不喜欢」的歌曲删掉

LX Music 的「不喜欢」只是一个全局规则表（dislike_list 表，内容是 "歌名@歌手"），
它只会在播放时跳过这些歌，并不会从歌单里删除。本脚本按 LX 自己的匹配规则
（忽略大小写、首尾空白，'@' 视作 '#') 找出命中的歌，再从歌单里移除。

用法（默认演练，确认无误再加 --apply）：
    python remove_disliked.py                          # 预览：所有歌单里会被删的歌
    python remove_disliked.py --apply                  # 实际删除（需先退出 LX Music）
    python remove_disliked.py --playlist "歌单名" --apply
    python remove_disliked.py --list-id tx_xxx --apply
    python remove_disliked.py --user-lists-only         # 不管默认列表/我喜欢/临时列表
    python remove_disliked.py --show-rules             # 看看当前解析出的不喜欢规则

注意：
  * 默认是演练模式，不会改数据库。
  * --apply 前必须先完全退出 LX Music（否则会拒绝执行），写完后重新打开 LX。
  * 写入前自动备份 lx.data.db / -wal / -shm 到 LxDatas/backup_before_dislike_时间戳/。
  * 只删目标歌单里的歌曲条目，不动不喜欢规则本身，也不动歌词/下载等缓存。
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

DEFAULT_DB = Path(os.environ.get("APPDATA", "")) / "lx-music-desktop" / "LxDatas" / "lx.data.db"
LX_PROCESS = "lx-music-desktop.exe"

# LX 内部用 '@' 分隔歌名和歌手；歌名里若含 '@' 会被替换成 '#'
DISLIKE_SEP = "@"
DISLIKE_SEP_ALIAS = "#"

SYSTEM_LISTS = {
    "default": "默认列表",
    "love": "我喜欢",
    "temp": "临时列表",
}


def norm(text: str | None) -> str:
    """与 LX 的归一化保持一致：'@'→'#'、转小写、去首尾空白。"""
    return (text or "").replace(DISLIKE_SEP, DISLIKE_SEP_ALIAS).lower().strip()


def load_dislike_rules(con: sqlite3.Connection) -> tuple[set[str], set[str], set[str]]:
    """读取 dislike_list，返回 (name@歌手, 仅歌名, 仅歌手) 三个集合。"""
    names: set[str] = set()
    music_names: set[str] = set()
    singer_names: set[str] = set()

    rows = con.execute("SELECT content FROM dislike_list WHERE type='music'").fetchall()
    for (content,) in rows:
        if not content:
            continue
        # 与 LX 的 `content.split('@')` 一致：只取前两段
        parts = content.split(DISLIKE_SEP)
        name = parts[0] if len(parts) > 0 else ""
        singer = parts[1] if len(parts) > 1 else ""

        if name:
            name = norm(name)
            if singer:
                singer = norm(singer)
                names.add(f"{name}{DISLIKE_SEP}{singer}")
            else:
                music_names.add(name)
        elif singer:
            singer_names.add(norm(singer))

    return names, music_names, singer_names


def is_disliked(row: sqlite3.Row, rules: tuple[set[str], set[str], set[str]]) -> bool:
    names, music_names, singer_names = rules
    name = norm(row["name"])
    singer = norm(row["singer"])
    return (
        name in music_names
        or singer in singer_names
        or f"{name}{DISLIKE_SEP}{singer}" in names
    )


def lx_is_running() -> bool:
    if os.name != "nt":
        return False
    try:
        out = subprocess.run(
            ["tasklist", "/FI", f"IMAGENAME eq {LX_PROCESS}", "/NH"],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=15,
        ).stdout
    except Exception:
        return False
    return LX_PROCESS.lower() in (out or "").lower()


def format_lists(rows) -> str:
    return "\n".join(f"  {r[0]}  {r[1]}" for r in rows)


def resolve_playlist(con: sqlite3.Connection, name: str | None, list_id: str | None) -> tuple[str, str]:
    """按 id 或名称定位歌单，返回 (id, name)。支持 default/love/temp。"""
    if list_id:
        if list_id in SYSTEM_LISTS:
            return list_id, SYSTEM_LISTS[list_id]
        row = con.execute("SELECT id, name FROM my_list WHERE id=?", (list_id,)).fetchone()
        if not row:
            raise SystemExit(f"找不到 id 为 {list_id!r} 的歌单")
        return row[0], row[1]

    rows = con.execute("SELECT id, name FROM my_list ORDER BY position").fetchall()
    exact = [r for r in rows if r[1] == name]
    if len(exact) == 1:
        return exact[0][0], exact[0][1]
    if len(exact) > 1:
        raise SystemExit(f"有 {len(exact)} 个同名歌单，请改用 --list-id 指定：\n" + format_lists(exact))

    fuzzy = [r for r in rows if name and name in r[1]]
    if len(fuzzy) == 1:
        return fuzzy[0][0], fuzzy[0][1]
    if len(fuzzy) > 1:
        raise SystemExit(f"名称 {name!r} 匹配到多个歌单，请改用 --list-id 指定：\n" + format_lists(fuzzy))
    raise SystemExit(f"找不到名称包含 {name!r} 的歌单。现有歌单：\n" + format_lists(rows))


def list_name_map(con: sqlite3.Connection) -> dict[str, str]:
    mapping = {r[0]: r[1] for r in con.execute("SELECT id, name FROM my_list").fetchall()}
    existing = {
        r[0]
        for r in con.execute("SELECT DISTINCT listId FROM my_list_music_info").fetchall()
    }
    for list_id, name in SYSTEM_LISTS.items():
        if list_id in existing:
            mapping.setdefault(list_id, name)
    return mapping


def load_entries(con: sqlite3.Connection, list_id: str) -> list[sqlite3.Row]:
    return con.execute(
        """
        SELECT m.id, m.name, m.singer, m.interval, o."order"
        FROM my_list_music_info m
        JOIN my_list_music_info_order o
          ON o.listId = m.listId AND o.musicInfoId = m.id
        WHERE m.listId = ?
        ORDER BY o."order", m.rowid
        """,
        (list_id,),
    ).fetchall()


def collect_targets(
    con: sqlite3.Connection, list_ids: list[str], rules
) -> tuple[dict[str, list[sqlite3.Row]], dict[str, int]]:
    """返回 {list_id: 命中的行} 和 {list_id: 歌单总歌数}。"""
    hits: dict[str, list[sqlite3.Row]] = {}
    totals: dict[str, int] = {}
    for list_id in list_ids:
        rows = load_entries(con, list_id)
        totals[list_id] = len(rows)
        matched = [r for r in rows if is_disliked(r, rules)]
        if matched:
            hits[list_id] = matched
    return hits, totals


def backup_db(db: Path) -> Path:
    stamp = time.strftime("%Y%m%d_%H%M%S")
    bak = db.parent / f"backup_before_dislike_{stamp}"
    bak.mkdir(parents=True, exist_ok=True)
    for suffix in ("", "-wal", "-shm"):
        src = Path(str(db) + suffix)
        if src.exists():
            shutil.copy2(src, bak / (db.name + suffix))
    return bak


def write_changes(con: sqlite3.Connection, hits: dict[str, list[sqlite3.Row]]) -> None:
    cur = con.cursor()
    with con:  # 事务
        for list_id, rows in hits.items():
            for row in rows:
                cur.execute(
                    "DELETE FROM my_list_music_info WHERE listId=? AND id=?",
                    (list_id, row["id"]),
                )
                cur.execute(
                    "DELETE FROM my_list_music_info_order WHERE listId=? AND musicInfoId=?",
                    (list_id, row["id"]),
                )

            # 删除后把剩余歌曲的 order 重新编号为 0..n-1（与 LX 重排逻辑等价）
            remaining = cur.execute(
                'SELECT musicInfoId FROM my_list_music_info_order WHERE listId=? ORDER BY "order", rowid',
                (list_id,),
            ).fetchall()
            cur.execute("DELETE FROM my_list_music_info_order WHERE listId=?", (list_id,))
            cur.executemany(
                'INSERT INTO my_list_music_info_order (listId, musicInfoId, "order") VALUES (?, ?, ?)',
                [(list_id, r[0], i) for i, r in enumerate(remaining)],
            )


def main() -> int:
    ap = argparse.ArgumentParser(description="删除 LX Music 歌单中已标记「不喜欢」的歌曲")
    ap.add_argument("--playlist", "-p", help="只处理这个歌单（精确或唯一子串）")
    ap.add_argument("--list-id", help="只处理这个歌单 id（名称有歧义时使用）")
    ap.add_argument("--user-lists-only", action="store_true", help="不处理默认列表/我喜欢/临时列表")
    ap.add_argument("--db", default=str(DEFAULT_DB), help=f"lx.data.db 路径（默认 {DEFAULT_DB}）")
    ap.add_argument("--show-rules", action="store_true", help="打印解析出的不喜欢规则")
    ap.add_argument("--apply", action="store_true", help="实际写入（默认仅演练）")
    ap.add_argument("--report", help="可选：把删除明细写成 JSON 文件")
    args = ap.parse_args()

    db = Path(args.db)
    if not db.exists():
        ap.error(f"数据库不存在：{db}")

    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=20)
    con.row_factory = sqlite3.Row
    try:
        rules = load_dislike_rules(con)
        names, music_names, singer_names = rules
        if args.show_rules:
            print("不喜欢规则：")
            print(f"  歌名+歌手: {len(names)} 条")
            for r in sorted(names):
                print(f"    {r.replace(DISLIKE_SEP, ' @ ')}")
            print(f"  仅歌名: {len(music_names)} 条")
            for r in sorted(music_names):
                print(f"    {r}")
            print(f"  仅歌手: {len(singer_names)} 条")
            for r in sorted(singer_names):
                print(f"    {r}")
            print()

        if args.playlist or args.list_id:
            list_id, list_label = resolve_playlist(con, args.playlist, args.list_id)
            targets = [(list_id, list_label)]
        else:
            name_map = list_name_map(con)
            existing = [
                r[0]
                for r in con.execute(
                    "SELECT DISTINCT listId FROM my_list_music_info ORDER BY listId"
                ).fetchall()
            ]
            if args.user_lists_only:
                existing = [i for i in existing if i not in SYSTEM_LISTS]
            targets = [(i, name_map.get(i, i)) for i in existing]

        hits, totals = collect_targets(con, [i for i, _ in targets], rules)
    finally:
        con.close()

    if not names and not music_names and not singer_names:
        print("不喜欢列表是空的，没有可删除的歌曲。")
        return 0

    total_hits = sum(len(v) for v in hits.values())
    print(f"扫描 {len(targets)} 个歌单，命中不喜欢规则 {total_hits} 首。")
    if not hits:
        print("没有需要删除的歌曲。")
        return 0

    print()
    for list_id, list_label in targets:
        rows = hits.get(list_id)
        if not rows:
            continue
        print(f"[{list_label}] ({list_id})  共 {totals[list_id]} 首，删除 {len(rows)} 首：")
        for row in rows:
            print(f"  删 {row['name']} | {row['singer']}  [order {row['order']}, {row['id']}]")
        print()

    if args.report:
        payload = {
            "db": str(db),
            "rules": {
                "name_singer": sorted(names),
                "name_only": sorted(music_names),
                "singer_only": sorted(singer_names),
            },
            "playlists": [
                {
                    "id": list_id,
                    "name": list_label,
                    "total": totals[list_id],
                    "removed": [
                        {
                            "id": row["id"],
                            "name": row["name"],
                            "singer": row["singer"],
                            "order": row["order"],
                        }
                        for row in hits.get(list_id, [])
                    ],
                }
                for list_id, list_label in targets
                if list_id in hits
            ],
        }
        Path(args.report).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"明细已写入: {args.report}\n")

    if not args.apply:
        print("[演练模式] 数据库未改动。确认无误后加 --apply 实际写入（需先退出 LX Music）。")
        return 0

    if lx_is_running():
        raise SystemExit("\n检测到 LX Music 正在运行。请先完全退出 LX Music，再重新执行 --apply。")

    bak = backup_db(db)
    print(f"已备份: {bak}")

    con = sqlite3.connect(db, timeout=30)
    con.row_factory = sqlite3.Row
    try:
        write_changes(con, hits)
        print("\n写入完成：")
        for list_id, list_label in targets:
            if list_id not in hits:
                continue
            left_info = con.execute(
                "SELECT COUNT(*) FROM my_list_music_info WHERE listId=?", (list_id,)
            ).fetchone()[0]
            left_order = con.execute(
                "SELECT COUNT(*) FROM my_list_music_info_order WHERE listId=?", (list_id,)
            ).fetchone()[0]
            print(f"  [{list_label}] 歌曲表 {left_info} 首，排序表 {left_order} 条")
    finally:
        con.close()

    print("\n现在可以重新打开 LX Music 了。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
