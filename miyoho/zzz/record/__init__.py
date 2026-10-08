"""绝区零 · 战绩：危局强袭战 / 式舆防卫战 / 零号空洞 / 迷宫诡域 / 实时便笺。

    api.py        官方战绩接口（hadal_mem_detail_v2 / hadal_info_v2 / abyss / zenkov / note）
    store.py      本地赛期存档（官方只有本期+上期，历史全靠这里一份份攒）
    image.py      危局结果图渲染（纯 Pillow）+ output_path / render_deadly_png
    shiyu_image.py 式舆防卫战结果图渲染
    rank_image.py 群排行榜单图渲染（目前只有「绝境群排行」用）
    routes.py     管理页路由 /zzz/shiyu /zzz/deadly /zzz/abyss /zzz/zenkov
                  /zzz/records* /zzz/asset/{name} /zzz/deadly/image
    capture.py    「查询时顺手存一份」+ 后台每天自动抓一轮的循环（导入即启动）

⚠️ 出图那几个文件（image.py / shiyu_image.py / rank_image.py）共用
core/image_gen.py 的绘制工具，危局专属的版面常量（画布宽 W 等）在本目录 image.py，
群排行榜单图的在 rank_image.py —— 别往公共层塞。
"""
