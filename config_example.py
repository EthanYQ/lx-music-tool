# -*- coding: utf-8 -*-
"""示例配置。

复制本文件为 config_local.py，填入自己的歌单信息即可。
config_local.py 已在 .gitignore 中，不会被提交。
"""

# 00_fetch.py 抓取配置
PLAYLISTS = [
    # 网易云：直接给 playlist id
    {'tag': 'nx1', 'platform': 'wy', 'id': '你的网易云歌单ID', 'name': 'playlist1'},
    {'tag': 'nx2', 'platform': 'wy', 'id': '你的网易云歌单ID', 'name': 'playlist2'},
    # QQ 音乐：给歌单分享链接（支持短链）或 disstid
    {'tag': 'qq', 'platform': 'qq',
     'url': '你的QQ歌单分享链接', 'name': 'playlist3'},
]

# 03_write_lx.py 写入 LX 时使用的歌单名
LIST_NAME = '迁移合并歌单'
