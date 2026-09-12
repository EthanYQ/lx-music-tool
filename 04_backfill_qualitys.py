# -*- coding: utf-8 -*-
"""
补写音质字段：给已导入的歌曲补 meta.qualitys / meta._qualitys

背景：应用自己写入的歌曲 meta 里有 qualitys(数组) + _qualitys(字典)，
缺了会有两个后果：
  1) LX 的"换源"逻辑会跳过这些歌（换源时要求 meta._qualitys[音质] 存在）
  2) 播放音质设为 无损/320k 时，getPlayQuality 会读 meta._qualitys[q]（无兜底）
本脚本就地 UPDATE，不重建歌单，不产生重复。
"""
import json, sqlite3, shutil, os, time

WORK = 'D:/Develop/Git_repository/make-something/lx-playlist-export'
DB_DIR = 'C:/Users/yuanyq/AppData/Roaming/lx-music-desktop/LxDatas'
DB = f'{DB_DIR}/lx.data.db'

M = json.load(open(f'{WORK}/merged.json', encoding='utf-8'))
qq_songs = {s.get('mid', ''): s for s in json.load(open(f'{WORK}/raw_qq.json', encoding='utf-8'))}
nx_map = {}
for fn in ['nx1_tracks.json', 'nx2_tracks.json']:
    for t in json.load(open(f'{WORK}/{fn}', encoding='utf-8')):
        nx_map[str(t.get('id'))] = t

QUALITYS = ['flac24bit', 'flac', 'ape', '320k', '192k', '128k']   # 与 LX 的 QUALITYS 常量一致


def fmt(size):
    return f'{size / 1024 / 1024:.2f} MiB'


def qualitys_of(e):
    """返回 {类型: 字节数}，只保留真实存在的音质"""
    out = {}
    src, sid = e['src_code'], str(e['src_id'])
    if src == 'qq':
        f = (qq_songs.get(sid, {}).get('file') or {})
        cand = {
            '128k': f.get('size_128mp3') or f.get('size_128'),
            '320k': f.get('size_320mp3') or f.get('size_320'),
            'flac': f.get('size_flac'),
            'flac24bit': f.get('size_hires'),
            'ape': f.get('size_ape'),
        }
    else:
        t = nx_map.get(sid, {})
        cand = {
            '128k': (t.get('l') or {}).get('size'),
            '192k': (t.get('m') or {}).get('size'),
            '320k': (t.get('h') or {}).get('size'),
            'flac': (t.get('sq') or {}).get('size'),
            'flac24bit': (t.get('hr') or {}).get('size'),
        }
    for k, v in cand.items():
        if v and v > 0:
            out[k] = v
    return out


# 备份
bak = f'{DB_DIR}/backup_before_qualitys_{time.strftime("%Y%m%d_%H%M%S")}'
os.makedirs(bak, exist_ok=True)
for suffix in ['', '-wal', '-shm']:
    p = DB + suffix
    if os.path.exists(p):
        shutil.copy2(p, f'{bak}/lx.data.db{suffix}')
print('已备份:', bak)

# 组装更新（按 musicInfoId 索引）
updates = {}
for e in M:
    src, sid = e['src_code'], str(e['src_id'])
    mid = f"{'tx' if src == 'qq' else 'wy'}_{sid}"
    q = qualitys_of(e)
    if q:
        updates[mid] = q

con = sqlite3.connect(DB, timeout=20)
try:
    cur = con.cursor()
    lid = cur.execute("SELECT id FROM my_list LIMIT 1").fetchone()[0]
    rows = cur.execute("SELECT id, meta FROM my_list_music_info WHERE listId=?", (lid,)).fetchall()
    print(f'歌单 {lid} 共 {len(rows)} 首，其中可补音质 {len(updates)} 首')

    done = skip = 0
    for mid, meta_raw in rows:
        q = updates.get(mid)
        if not q:
            skip += 1
            continue
        meta = json.loads(meta_raw)
        if '_qualitys' in meta and meta['_qualitys']:
            skip += 1
            continue
        meta['qualitys'] = [{'type': k, 'size': fmt(v)} for k, v in q.items() if k in QUALITYS]
        meta['_qualitys'] = {k: {'size': fmt(v)} for k, v in q.items() if k in QUALITYS}
        cur.execute('UPDATE my_list_music_info SET meta=? WHERE id=? AND listId=?',
                    (json.dumps(meta, ensure_ascii=False), mid, lid))
        done += 1
    con.commit()
    print(f'更新 {done} 首，跳过 {skip} 首')

    # 抽验
    print('\n抽验 3 首:')
    for r in cur.execute('SELECT id,name,meta FROM my_list_music_info WHERE listId=? LIMIT 3', (lid,)):
        m = json.loads(r[2])
        print(f"  {r[0]} {r[1]} -> _qualitys={m.get('_qualitys')}")
    n = cur.execute("SELECT COUNT(*) FROM my_list_music_info WHERE listId=? AND meta LIKE '%_qualitys%'",
                    (lid,)).fetchone()[0]
    print(f'\n带 _qualitys 的歌曲: {n}/{len(rows)}')
finally:
    con.close()
