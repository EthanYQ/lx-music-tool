# -*- coding: utf-8 -*-
"""第三步：把清洗去重后的歌单写入 LX Music 本地歌单 (lx.data.db)"""
import json, sqlite3, shutil, os, time, secrets, re

WORK = 'D:/Develop/Git_repository/make-something/lx-playlist-export'
DB_DIR = 'C:/Users/yuanyq/AppData/Roaming/lx-music-desktop/LxDatas'
DB = f'{DB_DIR}/lx.data.db'
LIST_NAME = '迁移合并2(pop+粤语+Sing)'

M = json.load(open(f'{WORK}/merged.json', encoding='utf-8'))

# ---- 原始平台数据查询表（补 albumMid / media_mid / albumId）----
qq_songs = json.load(open(f'{WORK}/raw_qq.json', encoding='utf-8'))
qq_map, qq_file_map = {}, {}
for s in qq_songs:
    al, f = s.get('album') or {}, s.get('file') or {}
    qq_map[s.get('mid', '')] = {
        'album_mid': al.get('mid', ''),
        'num_id': s.get('id'),
        'media_mid': f.get('media_mid', '') or s.get('ksong', {}).get('mid', ''),
    }
    qq_file_map[s.get('mid', '')] = f

nx_map = {}
for fn in ['nx1_tracks.json', 'nx2_tracks.json']:
    for t in json.load(open(f'{WORK}/{fn}', encoding='utf-8')):
        al = t.get('al') or {}
        nx_map[str(t.get('id'))] = {'album_id': al.get('id'), 'pic': al.get('picUrl', '')}


def fmt_interval(ms):
    s = int(round((ms or 0) / 1000))
    return f'{s // 60:02d}:{s % 60:02d}' if s > 0 else '00:00'


QUALITYS = ['flac24bit', 'flac', 'ape', '320k', '192k', '128k']   # 与 LX 的 QUALITYS 常量一致


def qualitys_of(src, sid):
    """从原始数据取可用音质 {类型: 字节数}。
    必须写入 meta.qualitys/_qualitys：LX 的换源逻辑要求 meta._qualitys[音质] 存在，
    播放音质设为 无损/320k 时 getPlayQuality 也会直接读它（无兜底）。"""
    out = {}
    if src == 'qq':
        f = (qq_file_map.get(sid) or {})
        cand = {'128k': f.get('size_128mp3') or f.get('size_128'),
                '320k': f.get('size_320mp3') or f.get('size_320'),
                'flac': f.get('size_flac'),
                'flac24bit': f.get('size_hires'),
                'ape': f.get('size_ape')}
    else:
        t = nx_map.get(sid, {})
        cand = {'128k': (t.get('l') or {}).get('size'),
                '192k': (t.get('m') or {}).get('size'),
                '320k': (t.get('h') or {}).get('size'),
                'flac': (t.get('sq') or {}).get('size'),
                'flac24bit': (t.get('hr') or {}).get('size')}
    for k, v in cand.items():
        if v and v > 0:
            out[k] = v
    return out


def build_row(e):
    src, sid = e['src_code'], str(e['src_id'])
    if src == 'qq':
        info = qq_map.get(sid, {})
        meta = {
            'songId': sid,
            'albumName': e['album'],
            'picUrl': e['picUrl'],
            'albumId': info.get('album_mid', ''),
            'albumMid': info.get('album_mid', ''),
            'id': info.get('num_id'),
        }
        if info.get('media_mid'):
            meta['strMediaMid'] = info['media_mid']
        source, mid = 'tx', f'tx_{sid}'
    else:
        info = nx_map.get(sid, {})
        meta = {
            'songId': int(sid) if sid.isdigit() else sid,
            'albumName': e['album'],
            'picUrl': e['picUrl'] or info.get('pic', ''),
            'albumId': info.get('album_id'),
        }
        source, mid = 'wy', f'wy_{sid}'
    q = qualitys_of(src, sid)
    if q:
        meta['qualitys'] = [{'type': k, 'size': f'{v / 1024 / 1024:.2f} MiB'}
                            for k, v in q.items() if k in QUALITYS]
        meta['_qualitys'] = {k: {'size': f'{v / 1024 / 1024:.2f} MiB'}
                             for k, v in q.items() if k in QUALITYS}
    return {
        'id': mid,
        'name': e['title'],
        'singer': ' / '.join(e['artists']),
        'source': source,
        'interval': fmt_interval(e.get('duration_ms')),
        'meta': json.dumps(meta, ensure_ascii=False),
    }


rows, seen_ids, dups = [], set(), 0
for e in M:
    r = build_row(e)
    if r['id'] in seen_ids:
        dups += 1
        continue
    seen_ids.add(r['id'])
    rows.append(r)
print(f'待写入歌曲: {len(rows)} | 跳过重复ID: {dups}')

# ---- 备份 ----
bak = f'{DB_DIR}/backup_before_import_{time.strftime("%Y%m%d_%H%M%S")}'
os.makedirs(bak, exist_ok=True)
for suffix in ['', '-wal', '-shm']:
    p = DB + suffix
    if os.path.exists(p):
        shutil.copy2(p, f'{bak}/lx.data.db{suffix}')
print('已备份:', bak)

# ---- 写入 ----
con = sqlite3.connect(DB, timeout=20)
try:
    cur = con.cursor()
    pos = cur.execute('SELECT COALESCE(MAX(position), -1) + 1 FROM my_list').fetchone()[0]
    list_id = 'pl-' + secrets.token_hex(8)
    cur.execute(
        'INSERT INTO my_list (id, name, source, sourceListId, position, locationUpdateTime) VALUES (?,?,?,?,?,?)',
        (list_id, LIST_NAME, '', '', pos, None))
    for i, r in enumerate(rows):
        cur.execute(
            'INSERT INTO my_list_music_info (id, listId, name, singer, source, interval, meta) VALUES (?,?,?,?,?,?,?)',
            (r['id'], list_id, r['name'], r['singer'], r['source'], r['interval'], r['meta']))
        cur.execute(
            'INSERT INTO my_list_music_info_order (listId, musicInfoId, "order") VALUES (?,?,?)',
            (list_id, r['id'], i))
    con.commit()
    cnt = cur.execute('SELECT COUNT(*) FROM my_list_music_info WHERE listId=?', (list_id,)).fetchone()[0]
    ocnt = cur.execute('SELECT COUNT(*) FROM my_list_music_info_order WHERE listId=?', (list_id,)).fetchone()[0]
    lcnt = cur.execute('SELECT COUNT(*) FROM my_list WHERE id=?', (list_id,)).fetchone()[0]
    src_stat = cur.execute('SELECT source, COUNT(*) FROM my_list_music_info WHERE listId=? GROUP BY source',
                           (list_id,)).fetchall()
    print(f'写入完成: id={list_id} 名称={LIST_NAME}')
    print(f'  歌单行={lcnt} 歌曲={cnt} 排序={ocnt} 来源分布={dict(src_stat)}')
    print('\n前3行回读:')
    for r in cur.execute('SELECT id,name,singer,source,interval FROM my_list_music_info WHERE listId=? ORDER BY ROWID LIMIT 3',
                         (list_id,)):
        print('  ', r)
finally:
    con.close()
