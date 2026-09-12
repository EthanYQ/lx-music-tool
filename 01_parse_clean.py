# -*- coding: utf-8 -*-
"""
导入 QQ / 网易云 三个歌单 -> 统一 song 清单
步骤1: 原始歌单 -> song(歌名|歌手|专辑|原平台|原歌曲ID)
步骤2: 规范化 + 剥离版本标签 + 跨平台去重
输出: 01_原始歌单.tsv / 02_清洗去重.tsv
"""
import json, re, sys, time

WORK = 'D:/Develop/Git_repository/make-something/lx-playlist-export'
N1 = json.load(open(f'{WORK}/nx1_tracks.json', encoding='utf-8'))   # 粤语 89
N2 = json.load(open(f'{WORK}/nx2_tracks.json', encoding='utf-8'))   # Sing 247

# QQ 歌单（00_fetch.py 已剥掉 JSONP 外壳，存为纯数组）
q = json.load(open(f'{WORK}/raw_qq.json', encoding='utf-8'))        # pop 317

PLAYLISTS = [
    ('pop(QQ)',      'qq', 'qq', q),
    ('粤语(网易云)', 'wy', 'wy', N1),
    ('Sing(网易云)', 'wy', 'wy', N2),
]

def parse_nx(t, source):
    artists = [a.get('name', '') for a in t.get('ar', []) if a.get('name')]
    al = t.get('al', {}) or {}
    return {
        'playlist_name': None,
        'source': source,
        'source_id': str(t.get('id', '')),
        'title': t.get('name', '') or '',
        'artists': artists,
        'album': al.get('name', '') or '',
        'album_id': al.get('id', ''),
        'picUrl': al.get('picUrl', ''),
        'duration_ms': t.get('dt') or 0,
    }

def parse_qq(s):
    artists = [x.get('name', '') for x in s.get('singer', []) if x.get('name')]
    al = s.get('album', {}) or {}
    mid = al.get('mid', '')
    pic = f'https://y.gtimg.cn/music/photo_new/T002R500x500M000{mid}.jpg' if mid else ''
    return {
        'playlist_name': None,
        'source': 'qq',
        'source_id': s.get('mid', ''),
        'title': s.get('name', '') or '',
        'artists': artists,
        'album': al.get('name', '') or '',
        'album_id': s.get('id', ''),
        'picUrl': pic,
        'duration_ms': int((s.get('interval') or 0) * 1000),
    }

raw_records = []
for pl_name, src_code, src, data in PLAYLISTS:
    for t in data:
        r = parse_qq(t) if src == 'qq' else parse_nx(t, src)
        r['playlist_name'] = pl_name
        r['src_code'] = src_code
        raw_records.append(r)

print('原始曲目数:', len(raw_records), '| 来源:',
      {name: len(d) for name, _, _, d in [('pop', 'qq', 'qq', q),
                                          ('粤语', 'wy', 'wy', N1),
                                          ('Sing', 'wy', 'wy', N2)]})

# ---------- 规范化 ----------
def norm_basic(s: str) -> str:
    """全角→半角、空白压缩、空白括号统一"""
    if not s:
        return ''
    out = []
    for ch in s:
        o = ord(ch)
        if o == 0x3000:
            out.append(' ')
        elif 0xFF01 <= o <= 0xFF5E:
            out.append(chr(o - 0xFEE0))
        else:
            out.append(ch)
    s = ''.join(out)
    s = s.replace('（', '(').replace('）', ')')
    s = re.sub(r'\s+', ' ', s).strip()
    return s

def norm_artist(s: str) -> str:
    """歌手规范化：大小写、去空白、去括号、剥离中英文混排时的英文前后缀"""
    if not s:
        return ''
    s = norm_basic(s)
    s = re.sub(r'\s*\(.*?\)\s*', '', s).strip()
    s = re.sub(r"\s*[（(].*?[）)]\s*", '', s)
    s = re.sub(r'\s{2,}', ' ', s).strip()
    # 含中日韩字时，剥离开头的"G.E.M."/结尾的"ericaceae"/英文艺名后缀
    if re.search(r'[一-鿿぀-ヿ가-힯]', s):
        s = re.sub(r'(^[\sA-Za-z0-9.&’\'\-：:]+)', '', s)
        s = re.sub(r'([\sA-Za-z0-9&.’\'\-：:]+)$', '', s)
        s = s.strip()
    return s.lower()

# 版本/现场 标签：剥离但记录是否带tag（影响排序不参与去重键）
TAG_RXP = re.compile(
    r'(?i)'
    r'(?:'
    r'(?:\s*[—–\-]\s*)?[(\s]\(?'
    r'((?:\d{2,4}\s*年?\s*)?(?:live|现场|live版|演唱会|演唱会版|acoustic|不插电|伴奏|'
    r'off\s*vocal|karaoke|纯音乐|instrumental|inst|remix|k?tv版|'
    r'dance\s*remix|demo|piano|吉他|reprise|单曲版|独唱|翻唱|cover))'
    r'[)\s]*[)）]?\s*$'
    r')'
)

# 版本/现场标签：连字符或括号包住即剥离（用于匹配；- 歌手形式不动）
DASH_TAG_RXP = re.compile(
    r'(?i)^(.*?)\s*[—–\-]\s*(?:\d{2,4}\s*年?\s*)?'
    r'(?:live|现场|live版|演唱会(?:版)?|acoustic|不插电|伴奏(?:版)?|off\s*vocal|karaoke|'
    r'纯音乐|instrumental|inst|remix|tv版|ktv版|dance\s*remix|demo|piano|吉他版|reprise|单曲版|独唱版|翻唱版|cover版)\s*$'
)
PAREN_TAG_RXP = re.compile(
    r'(?i)^(.*?)\s*[\(（].*?(?:\d{2,4}\s*年?\s*)?'
    r'(?:live|现场|live版|演唱会(?:版)?|acoustic|不插电|伴奏(?:版)?|off\s*vocal|karaoke|'
    r'纯音乐|instrumental|inst|remix|tv版|ktv版|dance\s*remix|demo|piano|吉他版|reprise|单曲版|'
    r'独唱|翻唱|翻自|cover)[^)）]*\s*[\)）]\s*$'
)

def strip_tags(title: str):
    """剥离 - 标签 与 (标签...) 尾部结构，返回干净名"""
    t = norm_basic(title)
    while True:
        m = DASH_TAG_RXP.match(t)
        if m:
            t = m.group(1).rstrip()
            continue
        m = PAREN_TAG_RXP.match(t)
        if m:
            t = m.group(1).rstrip()
            continue
        break
    return t.strip()

def split_artist_title(title: str, artists: list):
    """
    处理 '歌手 - 歌名' / '歌名 - 歌手' 混排。
    优先用已知歌手判定。返回 (title, artist_from_title or None)
    """
    t = dbg_orig = title
    known = {norm_artist(a) for a in artists if a}
    # 分隔符: - — – | / 前后空格
    parts = re.split(r'\s*[-—–|]\s*', t)
    if len(parts) >= 2:
        left, right = parts[0].strip(), parts[1].strip()
        left_n, right_n = norm_artist(left), norm_artist(right)
        if right_n and right_n in known:          # '歌名 - 歌手'
            return left, right
        if left_n and left_n in known:            # '歌手 - 歌名'
            return right, left
        if left_n and right_n and right_n in known:
            return left, right
        # 都没命中：可能是 'Title - feat. X' 或 '歌名 - 副标题'，保留主体
        # 但如果右侧是常见feat/版本词，只保留左侧
        if re.match(r'(?i)^(feat|ft\.?|with)\b', right):
            return left, None
    return t, None

# ---------- 构建可去重条目 ----------
def build_entries():
    entries = []
    for r in raw_records:
        title = norm_basic(r['title'])
        artists = [a for a in (x.strip() for x in r['artists']) if a]
        title = strip_tags(title)
        title, artist_from_t = split_artist_title(title, artists)

        artist_set = [norm_artist(a) for a in artists if a]
        if artist_from_t:
            artist_set.append(norm_artist(artist_from_t))
        # 合并歌手行内重复（规范化，用于匹配）
        seen = []
        for a in artist_set:
            if a and a not in seen:
                seen.append(a)
        # 显示用：保留原始大小写
        display = []
        for a in list(artists) + ([artist_from_t] if artist_from_t else []):
            a = (a or '').strip()
            if a and a not in display:
                display.append(a)

        key_title = norm_basic(title).lower().rstrip(' ')
        key_artists = frozenset(seen) if seen else frozenset()

        entries.append({
            'r': r,
            'key_title': key_title,
            'key_artists': key_artists,
            'title': title,               # 清洗后主名
            'artists': seen,              # 规范化歌手（用于匹配）
            'artists_display': display,   # 原始大小写歌手名（用于显示）
        })
    return entries

entries = build_entries()

# ---- 并查集去重：同歌名 + 歌手有交集 => 合并 ----
from collections import defaultdict
n = len(entries)
parent = list(range(n))

def find(x):
    while parent[x] != x:
        parent[x] = parent[parent[x]]
        x = parent[x]
    return x

def union(a, b):
    ra, rb = find(a), find(b)
    if ra != rb:
        parent[rb] = ra

by_title = defaultdict(list)
for i, e in enumerate(entries):
    by_title[e['key_title']].append(i)

for title, idxs in by_title.items():
    for a in range(len(idxs)):
        for b in range(a + 1, len(idxs)):
            ea, eb = entries[idxs[a]], entries[idxs[b]]
            # 歌手集合有交集，或一方为空且另一方也空都不合并（不同歌手同名不同歌）
            if ea['key_artists'] and ea['key_artists'] & eb['key_artists']:
                union(idxs[a], idxs[b])

# 按并查集根分组
group_map = defaultdict(list)
for i in range(n):
    group_map[find(i)].append(entries[i])

# 选择 winner：优先同时覆盖多平台（信息全），再按来源顺序
PREF = {'qq': 0, 'wy': 1, 'kg': 2, 'kw': 3, 'mg': 4, 'tx': 0}

def winner_of(grp):
    # 评分：有 album+pic 完整 > 多来源 > 顺序靠前
    def score(e):
        r = e['r']
        s = 0
        if r['album'] and r['picUrl']:
            s += 10
        if r['picUrl']:
            s += 5
        # 平台偏好：qq 优先
        s += PREF.get(r['src_code'], 5) * -1
        return s
    return max(grp, key=score)

merged = []
for k, grp in group_map.items():
    w = winner_of(grp)
    # 收集该去重组所有 (平台,id) 与专辑
    src_list = [(e['r']['src_code'], e['r']['source_id']) for e in grp]
    test_key = w['title']
    merged.append({
        'title': w['title'],
        'artists': w['artists_display'],
        'album': w['r']['album'],
        'album_id': w['r']['album_id'],
        'picUrl': w['r']['picUrl'],
        'duration_ms': w['r']['duration_ms'],
        'src_code': w['r']['src_code'],
        'src_id': w['r']['source_id'],
        'all_src': sorted(set(tup for tup in src_list)),
        'playlists': sorted(set(e['r']['playlist_name'] for e in grp)),
    })

# ---------- 输出 ----------
def join_artists(artists):
    return ' / '.join(artists)

with open(f'{WORK}/01_原始歌单.tsv', 'w', encoding='utf-8-sig') as f:
    f.write('#歌单名称\t歌名\t歌手\t专辑\t原平台\t原歌曲ID\n')
    for r in raw_records:
        f.write(f"{r['playlist_name']}\t{r['title']}\t{join_artists(r['artists'])}"
                f"\t{r['album']}\t{r['src_code']}\t{r['source_id']}\n")

with open(f'{WORK}/02_清洗去重.tsv', 'w', encoding='utf-8-sig') as f:
    f.write('#歌名\t歌手\t专辑\tLX来源\tLX来源ID\t其他来源(平台:ID)\t原歌单\n')
    for e in merged:
        f.write(f"{e['title']}\t{join_artists(e['artists'])}\t{e['album']}"
                f"\t{e['src_code']}\t{e['src_id']}\t"
                f"{' ; '.join('%s:%s' % s for s in e['all_src'])}\t"
                f"{' ; '.join(e['playlists'])}\n")

print('原始条目:', len(raw_records), '→ 去重后:', len(merged))
json.dump(merged, open(f'{WORK}/merged.json', 'w', encoding='utf-8'), ensure_ascii=False)

# ---------- 去重明细（人工核对用） ----------
lines = ['#被合并的重复歌曲：每组只保留 1 条，其余视为重复',
         '#保留歌名\t歌手\t保留来源\t被合并掉的来源(平台:ID)\t原歌单']
for e in merged:
    own = f"{e['src_code']}:{e['src_id']}"
    alts = [f'{s}:{i}' for s, i in e['all_src'] if f'{s}:{i}' != own]
    if alts:
        lines.append(f"{e['title']}\t{'/'.join(e['artists'])}\t{own}\t{'; '.join(alts)}\t{'; '.join(e['playlists'])}")
with open(f'{WORK}/03_去重明细.tsv', 'w', encoding='utf-8-sig') as f:
    f.write('\n'.join(lines))

print('被合并的重复组:', len(lines) - 2)
print('文件已写入: 01_原始歌单.tsv, 02_清洗去重.tsv, 03_去重明细.tsv, merged.json')