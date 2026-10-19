# -*- coding: utf-8 -*-
"""
LX Music 歌单去重工具（可复用）

规则：歌名相同且歌手列表至少有一个交集，视为重复，保留其中一首。
      歌名比较默认先去掉版本后缀（Remix / Live / 伴奏 / 版 / Dark Version 等），
      且同名组里优先保留不带后缀的原版；默认保留最早出现的那首。

用法（先演练，确认无误再加 --apply 实际写入）：
    python dedupe_playlist.py --playlist "你的歌单名"
    python dedupe_playlist.py --playlist "你的歌单名" --apply
    python dedupe_playlist.py --list-id <你的歌单id> --apply
    python dedupe_playlist.py --playlist "xxx" --keep last      # 保留最后出现的一首
    python dedupe_playlist.py --playlist "xxx" --db "D:/path/lx.data.db"

注意：
  * 默认是演练模式（dry-run），不会改数据库。
  * --apply 前必须先完全退出 LX Music（否则会拒绝执行），写完后重新打开 LX。
  * 写入前会自动备份 lx.data.db / -wal / -shm 到 LxDatas/backup_before_dedupe_时间戳/。
  * 只删除该歌单里的重复条目，不动其他歌单，也不删全局歌词/缓存表。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

DEFAULT_DB = Path(os.environ.get("APPDATA", "")) / "lx-music-desktop" / "LxDatas" / "lx.data.db"
LX_PROCESS = "lx-music-desktop.exe"

# 歌手分隔符：、，,/／&＆;；| 以及 feat./ft.
ARTIST_SPLIT_RE = re.compile(r"[、,，/／&＆;；|]|\s+(?:feat|ft)\.?\s+", re.IGNORECASE)
WS_RE = re.compile(r"\s+")

# 版本后缀关键词（默认比较时去掉）
SUFFIX_KEYWORDS = (
    r"remix|live|version|ver\.?|demo|instrumental|inst\.?|karaoke|accompaniment|"
    r"acoustic|cover|radio\s*edit|extended|edit|dark\s*version|"
    r"伴奏|纯音乐|现场|翻唱|混音|重制|重混|dj|版"
)
PAREN_SUFFIX_RE = re.compile(
    rf"\s*[\(\（\[\【][^\)\）\]\】]*?(?:{SUFFIX_KEYWORDS})[^\)\）\]\】]*[\)\）\]\】]\s*$",
    re.IGNORECASE,
)
DASH_SUFFIX_RE = re.compile(
    rf"\s*[-–—－]\s*(?:{SUFFIX_KEYWORDS})\s*$",
    re.IGNORECASE,
)


def strip_suffix(name: str) -> str:
    """去掉歌名末尾的版本后缀，如 '犯贱 (Remix)' -> '犯贱'。"""
    s = WS_RE.sub(" ", (name or "").strip())
    s = PAREN_SUFFIX_RE.sub("", s)
    s = DASH_SUFFIX_RE.sub("", s)
    return s.strip()


def norm_name(name: str, strip: bool = True) -> str:
    """歌名归一化：可选去版本后缀，再压缩空白、忽略大小写。"""
    s = strip_suffix(name) if strip else (name or "").strip()
    return WS_RE.sub(" ", s).casefold()


def has_suffix(name: str) -> bool:
    return strip_suffix(name) != WS_RE.sub(" ", (name or "").strip())


def parse_artists(singer: str) -> set[str]:
    """把 '歌手A、歌手B' / '歌手A / 歌手B' 拆成 {'歌手A','歌手B'}。"""
    parts = ARTIST_SPLIT_RE.split(singer or "")
    return {WS_RE.sub(" ", p.strip()).casefold() for p in parts if p.strip()}


def lx_is_running() -> bool:
    """Windows 下检测 LX Music 是否在运行。"""
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


def resolve_playlist(con: sqlite3.Connection, name: str | None, list_id: str | None) -> tuple[str, str]:
    """按 id 或名称定位歌单，返回 (id, name)。"""
    if list_id:
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


def format_lists(rows) -> str:
    return "\n".join(f"  {r[0]}  {r[1]}" for r in rows)


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


def find_duplicates(
    rows: list[sqlite3.Row], keep: str = "first",
    strip: bool = True, prefer_base: bool = True,
) -> tuple[list[sqlite3.Row], list[dict]]:
    """返回 (kept, removed)。同一歌名分组内，歌手有交集即判重。

    prefer_base: 同名组里优先保留不带版本后缀的原版（再按 keep 决定先后）。
    """
    ordered = list(rows)
    if prefer_base:
        # 无后缀的排前面；同组内再按播放顺序（或倒序）处理
        ordered.sort(key=lambda r: (has_suffix(r["name"]), r["order"] if keep == "first" else -r["order"]))
    elif keep == "last":
        ordered.reverse()

    kept_by_name: dict[str, list[sqlite3.Row]] = {}
    kept: list[sqlite3.Row] = []
    removed: list[dict] = []

    for row in ordered:
        key = norm_name(row["name"], strip=strip)
        artists = parse_artists(row["singer"])
        kept_for_name = kept_by_name.setdefault(key, [])

        match = None
        for prev in kept_for_name:
            overlap = artists & parse_artists(prev["singer"])
            if overlap:
                match = (prev, overlap)
                break

        if match is None:
            kept_for_name.append(row)
            kept.append(row)
        else:
            prev, overlap = match
            removed.append({
                "removed": row,
                "kept": prev,
                "overlap": sorted(overlap),
            })

    # 恢复原顺序输出（keep=last 时 kept 是倒序）
    kept.sort(key=lambda r: r["order"])
    removed.sort(key=lambda x: x["removed"]["order"])
    return kept, removed


def backup_db(db: Path) -> Path:
    stamp = time.strftime("%Y%m%d_%H%M%S")
    bak = db.parent / f"backup_before_dedupe_{stamp}"
    bak.mkdir(parents=True, exist_ok=True)
    for suffix in ("", "-wal", "-shm"):
        src = Path(str(db) + suffix)
        if src.exists():
            shutil.copy2(src, bak / (db.name + suffix))
    return bak


def write_changes(con: sqlite3.Connection, list_id: str, removed: list[dict], kept: list[sqlite3.Row]) -> None:
    cur = con.cursor()
    with con:  # 事务
        for item in removed:
            mid = item["removed"]["id"]
            cur.execute("DELETE FROM my_list_music_info WHERE listId=? AND id=?", (list_id, mid))
            cur.execute("DELETE FROM my_list_music_info_order WHERE listId=? AND musicInfoId=?", (list_id, mid))

        # 重新编号，保持连续 0..n-1
        for i, row in enumerate(kept):
            cur.execute(
                'UPDATE my_list_music_info_order SET "order"=? WHERE listId=? AND musicInfoId=?',
                (i, list_id, row["id"]),
            )


def main() -> int:
    ap = argparse.ArgumentParser(description="LX Music 歌单去重（同名 + 歌手有交集）")
    ap.add_argument("--playlist", "-p", help="歌单名称（精确或唯一子串）")
    ap.add_argument("--list-id", help="歌单 id（名称有歧义时使用）")
    ap.add_argument("--db", default=str(DEFAULT_DB), help=f"lx.data.db 路径（默认 {DEFAULT_DB}）")
    ap.add_argument("--keep", choices=["first", "last"], default="first", help="保留首条还是末条（默认 first）")
    ap.add_argument("--no-strip-suffix", action="store_true", help="比较歌名时不去版本后缀（默认会去掉 Remix/Live/伴奏/版 等）")
    ap.add_argument("--no-prefer-base", action="store_true", help="不优先保留无后缀原版（默认优先）")
    ap.add_argument("--apply", action="store_true", help="实际写入（默认仅演练）")
    ap.add_argument("--report", help="可选：把去重明细写成 JSON 文件")
    args = ap.parse_args()

    if not args.playlist and not args.list_id:
        ap.error("必须提供 --playlist 或 --list-id")

    db = Path(args.db)
    if not db.exists():
        ap.error(f"数据库不存在：{db}")

    # 先只读演练
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=20)
    con.row_factory = sqlite3.Row
    try:
        list_id, list_name = resolve_playlist(con, args.playlist, args.list_id)
        rows = load_entries(con, list_id)
    finally:
        con.close()

    kept, removed = find_duplicates(
        rows, keep=args.keep,
        strip=not args.no_strip_suffix,
        prefer_base=not args.no_prefer_base,
    )

    print(f"歌单: {list_name}  ({list_id})")
    print(f"歌曲: {len(rows)} 首 → 去重后 {len(kept)} 首，删除 {len(removed)} 首，保留策略: {args.keep}")
    if not removed:
        print("没有发现重复，无需处理。")
        return 0

    print("\n重复明细：")
    for item in removed:
        a, b = item["removed"], item["kept"]
        print(f"  删 {a['name']} | {a['singer']}  [order {a['order']}, {a['id']}]")
        print(f"    留 {b['name']} | {b['singer']}  [order {b['order']}, {b['id']}]  交集: {', '.join(item['overlap'])}")

    if args.report:
        payload = {
            "playlist": {"id": list_id, "name": list_name},
            "total": len(rows),
            "kept": len(kept),
            "removed": [
                {
                    "name": x["removed"]["name"],
                    "singer": x["removed"]["singer"],
                    "id": x["removed"]["id"],
                    "order": x["removed"]["order"],
                    "kept_id": x["kept"]["id"],
                    "kept_order": x["kept"]["order"],
                    "overlap": x["overlap"],
                }
                for x in removed
            ],
        }
        Path(args.report).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n明细已写入: {args.report}")

    if not args.apply:
        print("\n[演练模式] 数据库未改动。确认无误后加 --apply 实际写入（需先退出 LX Music）。")
        return 0

    if lx_is_running():
        raise SystemExit("\n检测到 LX Music 正在运行。请先完全退出 LX Music，再重新执行 --apply。")

    bak = backup_db(db)
    print(f"\n已备份: {bak}")

    con = sqlite3.connect(db, timeout=30)
    con.row_factory = sqlite3.Row
    try:
        write_changes(con, list_id, removed, kept)
        left_info = con.execute("SELECT COUNT(*) FROM my_list_music_info WHERE listId=?", (list_id,)).fetchone()[0]
        left_order = con.execute("SELECT COUNT(*) FROM my_list_music_info_order WHERE listId=?", (list_id,)).fetchone()[0]
    finally:
        con.close()

    print(f"写入完成: 歌曲表 {left_info} 首，排序表 {left_order} 条。")
    print("现在可以重新打开 LX Music 了。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
