"""绝区零 · 调频（抽卡）记录。

    api.py     申请 authkey + 拉 getGachaLog + 增量同步（六个频段一次同步完）
    store.py   本地存档 data/zzz/{uid}.csv（+ 旧格式 legacy/）
    stats.py   统计汇总（每频段的垫刀数 / 五星出货 / 平均抽数…）
    image.py   调频总结图（纯 Pillow）
    routes.py  管理页路由 /zzz/gacha*

抽卡是「两步走」且**只认 stoken**，细节全写在 api.py 的模块说明里（含 UIGF / gsuid_core
配方对照），改之前先看那段。
"""
