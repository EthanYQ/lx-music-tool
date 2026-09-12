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
| `04_backfill_qualitys.py` | **补救脚本**：给已导入的歌补 `meta.qualitys/_qualitys`（就地 UPDATE，不重建歌单） |
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

写入流程：

1. **备份** `lx.data.db` + `-wal` + `-shm` → `LxDatas/backup_before_import_时间戳/`
2. 事务插入：1 行 `my_list` + N 行 `my_list_music_info` + N 行 `my_list_music_info_order`
3. 回读校验（行数、来源分布）
4. **重启 LX Music 生效**（启动时读一次 `my_list`，运行中不感知外部写入）

细节见下方「LX Music 机制笔记」§1–§3。

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

# 5. 重启 LX Music，侧边栏里出现新歌单
```

---
---

# 附：LX Music 机制笔记

以下结论均为本项目实测 / 查源码所得。源码位置：`D:\Tools\lx-music-desktop\LX_MUSIC\resources\app.asar`。

## §1 数据存在哪

| 路径 | 内容 |
|---|---|
| `%APPDATA%\lx-music-desktop\LxDatas\lx.data.db` | **歌单主数据库**（SQLite，WAL 模式） |
| `LxDatas\config_v2.json` | 设置（含当前音源 `common.apiSource`、播放音质 `player.playQuality`、`sync.enable`） |
| `LxDatas\user_api.json` | 自定义源脚本（见 §4） |
| `LxDatas\data.json` | 界面状态、上次播放信息 |

数据库主要表：

| 表 | 作用 | 关键列 |
|---|---|---|
| `my_list` | 歌单 | `id` `name` `source` `sourceListId` `position` |
| `my_list_music_info` | 歌曲 | `id` `listId` `name` `singer` `source` `interval` `meta` |
| `my_list_music_info_order` | 播放顺序 | `listId` `musicInfoId` `order` |
| `music_info_other_source` | 歌曲的其他来源映射 | 只在换源时写入；实测常为空（见 §6） |
| `music_url` | 播放地址缓存 | `id` = `<musicInfoId>_<音质>`；**看 URL 域名可反推实际走的源** |
| `lyric` | 歌词缓存 | `id` `source` `type` |

`default` / `love` 是内置列表 id（默认列表、我的收藏），不在 `my_list` 里。

## §2 歌曲 ID 约定与 meta 字段

**ID = `<source>_<平台ID>`**，各平台格式不同：

| LX 源代码 | 平台 | 歌曲 ID 格式 | 备注 |
|---|---|---|---|
| `tx` | QQ音乐 | `tx_<songmid>` | 如 `tx_002jNyoG3FhJQt` |
| `wy` | 网易云 | `wy_<数字id>` | 如 `wy_25640392` |
| `kw` | 酷我 | `kw_<rid>` | |
| `mg` | 咪咕 | `mg_<id>` | |
| `kg` | 酷狗 | **`<audioid>_<hash>`**（无源前缀） | 唯一异类，写错播不了 |

`meta` 是 JSON，**必须包含音质字段**：

```json
{ "songId": "...", "albumName": "...", "picUrl": "...", "albumId": "...",
  "qualitys":  [{"type": "128k", "size": "3.55 MiB"}, {"type": "320k", "size": "8.87 MiB"}],
  "_qualitys": {"128k": {"size": "3.55 MiB"}, "320k": {"size": "8.87 MiB"}, "flac": {"size": "46.33 MiB"}} }
```

（QQ 另带 `strMediaMid` / `albumMid` / 数字 `id`，有助取高音质。）

### ⚠️ 缺 `qualitys` / `_qualitys` 的后果（源码实测，无兜底）

| 后果 | 位置 |
|---|---|
| **下载失败** | `createDownloadInfo` → `getMusicType` → `musicInfo.meta._qualitys[type]`，缺字段 = `undefined[...]` 抛异常 |
| **换源会跳过这些歌** | `getOnlineOtherSourceMusicUrl`：`if (!musicInfo.meta._qualitys[itemQuality]) continue;` |
| **播放音质设为 无损/320k 时报错** | `getPlayQuality` 直接读 `meta._qualitys[type]` |

数据来源：QQ 用 `file.size_128mp3 / size_320mp3 / size_flac / size_hires / size_ape`；
网易云用 `l / m / h / sq / hr` 的 `size`（只写真实存在的音质）。

> 本项目第一次导入时漏了这两个字段（只写了 songId/albumName/picUrl/albumId），
> 症状是"歌能播但下载不了"。`04_backfill_qualitys.py` 就是干这个补救的，逻辑已固化进 `03_write_lx.py`。

## §3 为什么只能直写数据库

LX 的开放接口（`openAPI.port`，默认 23330）**只实现了这些端点**，全是播放控制：

```
/status  /lyric  /lyric-all  /play  /pause  /skip-next  /skip-prev
/seek  /collect  /uncollect  /volume  /mute  /subscribe-player-status
```

**没有歌单管理接口**（其它路径一律返回 `Forbidden`）。所以导入歌单只能直写 SQLite。

LX 运行中也能写（WAL 支持多进程），但**必须重启 LX 才会加载**；实测重启不会清掉外部写入。

## §4 自定义源（user_api）完全解析

### 存哪、长什么样

`LxDatas\user_api.json` 里 `script` 字段是 **`gz_` + base64 + zlib 压缩**后的**混淆 JS**。解码：

```python
import base64, zlib, json
api = json.load(open('user_api.json', encoding='utf-8'))['userApis'][0]
js = zlib.decompress(base64.b64decode(api['script'][3:])).decode('utf-8')
```

### 运行时行为（实测）

脚本跑在 LX 的隐藏窗口里，LX 注入 `globalThis.lx`（`request` / `send` / `on` / `env` / `version` /
`currentScriptInfo` / `utils.crypto|buffer|zlib`）。启动流程：

1. 向自己的服务器拉配置（本机两个源都是**拿 npm registry 当配置通道**）
2. 取 `vinfo[脚本version].s`，形如 `kw|128k&wy|128k&...`
3. 按 `&` 拆段、每段按 `|` 拆 → 第一段=平台代码，其余=音质
4. `send('inited', { sources: {...} })` 上报给 LX

LX 侧：

```js
for (const [source, { actions, type, qualitys }] of Object.entries(apiInfo.sources)) {
    if (type != 'music') continue;       // 非 music 类型会被跳过
    qualitys[source] = sourceQualitys;
}
qualityList.value = qualitys;            // ← 决定"哪些平台的歌可用"
```

### 本机实例（实测抓到的配置）

| 源 | 配置包 | `vinfo["1"].s` | 支持的平台 |
|---|---|---|---|
| **野花🌷** | `registry.npmjs.org/flower-source-info` | `kw\|128k&wy\|128k&mg\|128k&tx\|128k&kg\|128k` | 全部五个 |
| **野草🌾** | `registry.npmjs.org/grass-source-info` | `kw\|128k` | **只有酷我，且只有 128k** |

### ⚠️ 不要试图改脚本

脚本会把 `md5(rawScript 去首尾空白)` 与服务器配置里的 `m` 对比，不一致就抛「服务器异常」、
**整个源初始化失败**。（实测两个脚本的 md5 均与服务器值一致，所以现在能正常初始化。）

## §5 列表里的"灰"是什么意思

```js
:class="[..., { disabled: !assertApiSupport(item.source) }]"
// assertApiSupport(source) = source == 'local' || qualityList.value[source] != null
```

**灰 = 当前音源没有声明支持这首歌的平台**（跟歌曲怎么导入的、数据对不对全无关）。
表现：文字变灰 + **下载按钮禁用**。

推论：在只支持酷我的源下，**所有 QQ/网易云歌都灰**；换回支持五平台的野花即正常。

## §6 "灰但还能播"：LX 会自动跨平台换源

播放地址的获取不检查 `assertApiSupport`，而是走一条降级链：

```
getMusicUrl
 ├─ 本地文件 → ① 缓存的其他来源
 └─ getOtherSourceByLocal
      └─ getOtherSource → musicSdk.findMusic({ name, singer, albumName, interval })
           ↑ 拿「歌名+歌手+专辑+时长」去【当前音源可用的平台】里搜同一首歌
```

在野草下（只有酷我可用），QQ 歌会被搜到**酷我的等价版本**并从酷我播放。
**列表里它仍然是 `tx`（所以还是灰的），只是播放时换了平台。**

取证方法（本项目实测用过）：

- 查 `music_url` 表：`tx_xxx` 条目如果存的是 `car-*.kuwo.cn` 地址 → 就是换源播的
- 那些 URL 里的 `M500` 前缀在应用代码中出现 **0 次** → 地址来自自定义源服务器，不是应用拼的
- `music_info_other_source` 表始终为空 → 因为这份映射**只在内存缓存**（源码里写库那行被注释掉了）

⚠️ 换源靠搜索匹配，**可能匹配到不同版本**（LX 用专辑+时长校验，但不保证 100%）。

## §7 下载的机制

```js
createDownloadInfo(musicInfo, type, fileName, qualityList, listId)
  └─ getMusicType(musicInfo, type, qualityList)
       ├─ qualityList[musicInfo.source] 不存在 → 直接返回 '128k'
       └─ 否则遍历音质，要求 musicInfo.meta._qualitys[type] 存在    // ← 缺就抛异常
```

两个关键点：

1. **下载依赖 `meta._qualitys`**（§2）—— 这就是本项目补字段的原因
2. **下载不换源**（`download.isUseOtherSource = False` 时）—— "能播"不等于"能下"

## §8 自己动手验证这些结论的方法

```bash
# 1. 看 openAPI 实现了哪些端点（未实现的返回 Forbidden）
curl http://127.0.0.1:23330/status

# 2. 解出自定义源脚本
python -c "import base64,zlib,json;d=json.load(open(r'%APPDATA%\lx-music-desktop\LxDatas\user_api.json',encoding='utf-8'));print(zlib.decompress(base64.b64decode(d['userApis'][0]['script'][3:])).decode())"

# 3. 查 asar 里的实现（webpack 保留了模块路径注释，很好用）
python -c "import re;d=open(r'D:\Tools\lx-music-desktop\LX_MUSIC\resources\app.asar','rb').read();print(sorted(set(m.group(1).decode() for m in re.finditer(rb';// \./(src/[^\s]+)', d))))"
```

**沙箱跑自定义源**（复刻 `globalThis.lx` 注入环境，抓它上报的 `sources`）：
脚本在 `%TEMP%\lx_apitest\run.js`，用法 `node run.js <解码后的脚本.js>`。
关键点：`currentScriptInfo.rawScript` 必须传**去首尾空白**的解码脚本，否则 md5 自校验不过。

## §9 坑汇总

- 写入前确认 **`sync.enable = False`**（开了数据同步有整表覆盖风险）
- 写完**必须重启 LX**；LX 启动只读，不会清外部写入
- 想重来：在 LX 里删掉歌单再跑一次（会一并清掉两张子表的行）
- **去重是启发式的**：版本标签剥离可能过度、歌手写法差异大时会漏合并 → `03_去重明细.tsv` 要人工过一眼
- 保留的是**原平台 ID**，不做 LX 全源重搜；跨平台重复时优先保留 QQ（改 `PREF` 可调整）
- 只支持酷我之类的窄源：歌会灰、不能下载（§5/§7），但**能播**（§6，走换源）

## §10 已知限制

- 只实现了 QQ / 网易云两个平台的抓取解析
- 不做歌单内歌曲的手动排序（顺序 = 平台歌单原顺序）
- 不支持增量更新：LX 里已有同名歌单不会合并，会新建一个
- **没有实现"外源匹配"**：即把一个平台的歌批量重新匹配成另一个平台（如全部转成酷我 ID）。
  若需要，思路是：拿歌名+歌手搜目标平台 → 用专辑/时长二次校验 → 写回 ID。
