"""绝区零 · 签到：每日签到（luna 活动接口）+ 自动签到。

    api.py            签到接口：zzz_sign_boards（只读状态）/ zzz_sign_do（执行签到）
    routes.py         管理页路由 /zzz/sign、/zzz/sign/do
    autosign.py       自动签到：配置读写 + 后台循环 + 启动钩子（定时时刻带浮动）
    autosign_routes.py 管理页路由 /zzz/autosign*（配置 / 目标清单 / 一键签到）

「定时为什么必须带浮动、为什么必须落盘」写在 autosign.py 的模块说明里，改之前先看。
"""
