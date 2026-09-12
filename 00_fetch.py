# -*- coding: utf-8 -*-
"""
步骤0：抓取歌单 -> 原始 JSON

改 PLAYLISTS 里的歌单即可复用到别的歌单。
产出：
  nx*_tracks.json  —— 网易云曲目详情数组（喂给 01_parse_clean.py）
  raw_qq.json      —— QQ 原始 songlist（喂给 01_parse_clean.py / 03_write_lx.py）
"""
import json, re, time, urllib.request, urllib.parse

WORK = 'D:/Develop/Git_repository/make-something/lx-playlist-export'
UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'
DELAY = 0.6          # 请求间隔，别把接口打急了

PLAYLISTS = [
    # 网易云：直接给 playlist id
    {'tag': 'nx1', 'platform': 'wy', 'id': '993586256', 'name': '粤语'},
    {'tag': 'nx2', 'platform': 'wy', 'id': '547340707', 'name': 'Sing'},
    # QQ 音乐：给歌单链接（支持短链）或 disstid
    {'tag': 'qq', 'platform': 'qq',
     'url': 'https://c6.y.qq.com/base/fcgi-bin/u?__=Yqgcurut9U0V', 'name': 'pop'},
]


def get_json(url, referer):
    req = urllib.request.Request(url, headers={'User-Agent': UA, 'Referer': referer})
    return json.loads(urllib.request.urlopen(req, timeout=20).read().decode('utf-8'))


def resolve_qq_disstid(link):
    """QQ 短链 -> 歌单数字 ID"""
    if link.isdigit():
        return link
    req = urllib.request.Request(link, headers={'User-Agent': UA})
    final = urllib.request.urlopen(req, timeout=20).geturl()
    m = re.search(r'playlist/(\d+)', final)
    if not m:
        raise RuntimeError(f'无法从 {final} 解析歌单 ID')
    return m.group(1)


def fetch_wy(pl_id):
    """网易云：playlist detail 拿 trackIds，再分批 v3/song/detail 补详情"""
    d = get_json(f'https://music.163.com/api/v6/playlist/detail?id={pl_id}',
                 'https://music.163.com/')
    pl = d['playlist']
    tids = [t['id'] for t in pl['trackIds']]           # tracks 只有前 10 首，trackIds 是全量
    print(f"  歌单「{pl['name']}」trackIds={len(tids)}")
    tracks = []
    for i in range(0, len(tids), 100):
        chunk = tids[i:i + 100]
        c = json.dumps([{'id': x} for x in chunk], separators=(',', ':'))
        for attempt in range(3):
            try:
                r = get_json('https://music.163.com/api/v3/song/detail?c=' + urllib.parse.quote(c),
                             'https://music.163.com/')
                tracks.extend(r.get('songs', []))
                break
            except Exception as e:
                print('  重试', attempt, e)
                time.sleep(2)
        time.sleep(DELAY)
    return pl['name'], tracks


def fetch_qq(link):
    """QQ 音乐：短链 -> disstid -> songlist（JSONP 要剥壳）"""
    disstid = resolve_qq_disstid(link)
    url = ('https://c.y.qq.com/qzone/fcg-bin/fcg_ucc_getcdinfo_byids_cp.fcg'
           f'?type=1&json=1&utf8=1&onlysong=0&new_format=1&disstid={disstid}')
    req = urllib.request.Request(url, headers={'User-Agent': UA, 'Referer': 'https://y.qq.com/'})
    raw = urllib.request.urlopen(req, timeout=20).read().decode('utf-8', errors='replace')
    m = re.search(r'jsonCallback\((.*)\)\s*;?\s*$', raw, re.S)
    cdlist = json.loads(m.group(1))['cdlist'][0]
    print(f"  歌单「{cdlist['dissname']}」disstid={disstid} 歌曲={len(cdlist['songlist'])}")
    return cdlist['dissname'], cdlist['songlist']


for pl in PLAYLISTS:
    print(f"[{pl['tag']}] {pl['platform']} {pl['name']}")
    if pl['platform'] == 'wy':
        name, data = fetch_wy(pl['id'])
        json.dump(data, open(f"{WORK}/{pl['tag']}_tracks.json", 'w', encoding='utf-8'), ensure_ascii=False)
        print(f"  -> {pl['tag']}_tracks.json ({len(data)} 首)")
    else:
        name, data = fetch_qq(pl['url'])
        json.dump(data, open(f"{WORK}/raw_qq.json", 'w', encoding='utf-8'), ensure_ascii=False)
        print(f"  -> raw_qq.json ({len(data)} 首)")
    time.sleep(DELAY)

print('\n抓取完成')
