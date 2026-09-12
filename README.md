# lx-playlist-export — 把 QQ/网易云歌单迁移到 LX Music

把在线平台的歌单抓下来，**清洗去重**后，直接写进 **LX Music** 的本地歌单。

```
歌单链接 ──00_fetch.py──► 原始 JSON ──01_parse_clean.py──► merged.json ──03_write_lx.py──► LX Music
                                          │                                              (lx.data.db)
                                          └──► 01_原始歌单.tsv / 02_清洗去重.tsv / 03_去重明细.tsv
```

**本次实例**：pop（QQ 317 首）+ 粤语（网易云 89 首）+ Sing（网易云 247 首）= 653 首
→ 去重后 **631 首** → 写入 LX 歌单「迁移合并(pop+粤语+Sing)」

---

## 为什么值得这么做

LX Music 自带"歌单链接导入"只认单一平台、且原样照搬。真正的价值在中间一步：
**不同平台的同一首歌会被合并成一条**。机械合并（QQ 317 + 网易云 336 = 653 全塞进去）会产生重复：
比如《梦伴》在 QQ 和网易云各有一份、QQ 歌单内部同一首歌也会出现两次。
LX 自己没有跨平台去重能力，所以需要在导入前手动做掉。

---

## 文件说明

| 文件 | 说明 |
|---|---|
| `00_fetch.py` | **步骤0**：抓取歌单 → 原始 JSON（改顶部 `PLAYLISTS` 即可换歌单） |
| `01_parse_clean.py` | **步骤1+2**：读原始 JSON → 清洗去重 → 产出清单 |
| `03_write_lx.py` | **步骤3**：读 merged.json → 写入 LX 数据库 |
| `nx1_tracks.json` / `nx2_tracks.json` | 网易云两个歌单的曲目详情（步骤0 产物） |
| `raw_qq.json` | QQ 歌单原始 songlist（步骤0 产物，已剥掉 JSONP 外壳） |
| `merged.json` | 清洗去重后的中间结果（01 与 03 之间的接口文件） |
| `01_原始歌单.tsv` | 653 首原始清单：歌名｜歌手｜专辑｜原平台｜原歌曲ID |
| `02_清洗去重.tsv` | 631 首成品，含 LX 来源/ID、其他来源、原歌单归属 |
| `03_去重明细.tsv` | 22 组"被合并掉的重复"，**供人工核对有没有误杀** |

---

## 步骤0：`00_fetch.py`（抓取）

改文件顶部的 `PLAYLISTS` 即可换歌单，然后 `python 00_fetch.py`：

```python
PLAYLISTS = [
    {'tag': 'nx1', 'platform': 'wy', 'id': '993586256', 'name': '粤语'},
    {'tag': 'nx2', 'platform': 'wy', 'id': '547340707', 'name': 'Sing'},
    {'tag': 'qq',  'platform': 'qq',
     'url': 'https://c6.y.qq.com/base/fcgi-bin/u?__=Yqgcurut9U0V', 'name': 'pop'},
]
```

用到的接口：

| 平台 | 接口 | 拿什么 |
|---|---|---|
| 网易云 | `/api/v6/playlist/detail?id=` | 歌单信息 + `trackIds`（首屏 `tracks` 只有 10 首，**trackIds 才是全量**） |
| 网易云 | `/api/v3/song/detail?c=[{"id":...}]` | 按 trackIds 分批补全曲目详情（专辑/封面/时长） |
| QQ | 短链 `c6.y.qq.com/base/fcgi-bin/u?__=xxx` | 跟随跳转拿到歌单数字 ID（disstid） |
| QQ | `c.y.qq.com/qzone/fcg-bin/fcg_ucc_getcdinfo_byids_cp.fcg?disstid=` | `songlist`；返回是 JSONP，脚本里已剥 `jsonCallback(...)` 外壳 |

关键字段：QQ 的 `songmid`、网易云的数字 `id` —— 这俩就是 LX Music 直接可用的歌曲 ID。

> 抓取有 0.6s 间隔，两个网易云歌单 + 一个 QQ 歌单大约几秒钟。

---

## 步骤1+2：`01_parse_clean.py`（清洗 + 去重）

### 清洗规则（4 步）

1. **归一化** `norm_basic()`：全角→半角、空白压缩、`（）`→`()`、去首尾空白
2. **剥版本标签** `strip_tags()`：循环剥离 `(Live)`、`- Live版`、`- 伴奏`、`(Adieu Remix)`、
   `(Live In Hong Kong / 2015)`、`(翻自 RADWIMPS)` 这类后缀。先剥 `- 标签` 再剥 `(…标签…)`，直到不再变化
   - 只剥**尾部**的，`I Don't Wanna Live Forever` 这种歌名里的 Live 不会被动
3. **拆"歌手 - 歌名"倒序** `split_artist_title()`：`周杰伦 - 晴天` 与 `晴天 - 周杰伦` 都要还原。
   判断依据是**该曲已知的歌手字段** —— 哪一边能对上歌手名，另一边就是歌名
4. **歌手归一** `norm_artist()`：去括号别名、剥离中英混排时的英文前后缀
   （`G.E.M.邓紫棋`→`邓紫棋`、`李悦君ericaceae`→`李悦君`）。
   匹配用小写形式；**显示保留原始大小写**（`Beyond`/`Alan Walker` 不会变成小写）

### 去重规则

- **判据**：归一化后**歌名相同** 且 **歌手集合有交集** → 视为同一首歌
- 用**并查集**合并成组（A≈B、B≈C 时 A 与 C 也会并到一起）
- 每组只保留 1 条（winner），评分优先级：专辑+封面信息完整 > 平台偏好（QQ > 网易云）
- 组内所有平台的 ID 都记进 `merged.json` 的 `all_src`，被合并的写进 `03_去重明细.tsv`
- **不会合并**的情况：同名但歌手完全不同（视为不同歌/翻唱版本）

本次数据：653 → 631（并掉 22 组：5 组跨平台 QQ↔网易云，17 组是同平台内的重复）

### `merged.json` 单条结构

```json
{
  "title": "送你一朵小红花",          // 清洗后歌名
  "artists": ["赵英俊"],               // 原始大小写歌手名（写进 LX 显示用）
  "album": "送你一朵小红花 电影歌曲专辑",
  "picUrl": "https://y.gtimg.cn/...jpg",
  "duration_ms": 232000,
  "src_code": "qq",                    // 主来源平台
  "src_id": "002jNyoG3FhJQt",          // 主来源歌曲 ID
  "all_src": [["qq", "002jNyoG3FhJQt"]],  // 该歌在各平台的全部 ID（含被合并的）
  "playlists": ["pop(QQ)"]             // 来自哪个歌单
}
```

---

## 步骤3：`03_write_lx.py`（写入 LX）

### LX Music 的数据结构

LX Music 的本地歌单存在 SQLite 里：`%APPDATA%\lx-music-desktop\LxDatas\lx.data.db`（WAL 模式）。

| 表 | 作用 | 关键列 |
|---|---|---|
| `my_list` | 歌单 | `id`(歌单ID) `name` `source` `position` |
| `my_list_music_info` | 歌曲 | `id`(歌曲ID) `listId` `name` `singer` `source` `interval` `meta` |
| `my_list_music_info_order` | 播放顺序 | `listId` `musicInfoId` `order` |

**ID 约定**：`<source>_<平台ID>`。本 fork 里 QQ 音乐的源代码是 **`tx`**（`tx_<songmid>`），
网易云是 `wy_<数字ID>`（另有 `kg`酷狗、`kw`酷我、`mg`咪咕）。
**`meta`** 是 JSON：`{songId, albumName, picUrl, albumId}`，QQ 另带 `strMediaMid`/`albumMid`/数字 `id`。

### 为什么直接写数据库？

LX 的开放接口（端口 23330）只有 11 个播放控制端点（`/status`、`/pause`、`/play`、`/lyric`…），
**没有歌单管理接口**（已核对应用 `app.asar` 里的路由表），所以只能直写 SQLite。

### 写入流程

1. **备份** `lx.data.db` + `-wal` + `-shm` → `LxDatas/backup_before_import_时间戳/`
2. 事务插入：1 行 `my_list` + N 行 `my_list_music_info` + N 行 `my_list_music_info_order`
3. 回读校验（行数、来源分布）
4. 重启 LX Music 生效

---

## 复用到别的歌单（完整流程）

```bash
# 1. 改 00_fetch.py 顶部的 PLAYLISTS（歌单 id / 链接、tag、name）
python 00_fetch.py           # 抓取 → 原始 JSON

# 2. 改 01_parse_clean.py 顶部的 PLAYLISTS 列表（与上一步的 tag 对应）
python 01_parse_clean.py     # 清洗去重 → merged.json + 清单

# 3. 打开 03_去重明细.tsv 扫一眼有没有误合并（重要）

# 4. 改 03_write_lx.py 顶部的 LIST_NAME（歌单名）
python 03_write_lx.py        # 写入 LX 数据库

# 5. 重启 LX Music（托盘右键退出再开），侧边栏里出现新歌单
```

---

## 注意事项 / 已知坑

- 写入前确认 LX 设置里 **`sync.enable = False`**（开了数据同步有整表覆盖的风险）
- LX 运行中也能写（SQLite WAL 支持多进程），但**必须重启 LX 才会加载**；实测重启不会清掉外部写入
- 想重来：在 LX 里删掉歌单再跑一次即可（LX 会一并清掉两张子表里的行）
- **去重是启发式的**，没有标准答案：版本标签剥离可能过度（把真正的 Remix 版本并进原版），
  歌手写法差异过大时会漏合并。所以 `03_去重明细.tsv` 一定要人工过一眼
- 保留的是**原平台 ID**，不做 LX 全源重搜 —— 原 ID 在 LX 里可以直接播放，不需要重新匹配
- 本次三个歌单的 5 组跨平台重复，保留项统一选了 QQ（`tx`）；要改成网易云只需调 `PREF` 顺序

## 已知限制

- 只实现了 QQ / 网易云两个平台的抓取解析
- 不做歌单内歌曲的手动排序（顺序 = 平台歌单原顺序）
- 不支持增量更新：LX 里已有同名歌单不会合并，会新建一个
