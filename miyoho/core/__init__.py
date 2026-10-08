"""core 层：与游戏无关的公共件。

  mys.py          米游社通行证 / 接口客户端（扫码登录、角色、签到、抽卡链接…）
  store.py        多账号凭证持久化（securestore 加密）
  bind.py         QQ 用户 ↔ 米游社账号绑定
  device.py       设备指纹（解 10041 风控）
  device_routes.py 设备配置页接口（ROUTES）
  asset_cache.py  米游社图片缓存（含 Page 静态目录镜像）
  image_gen.py    后台绘图公共件（Pillow）
  web.py          AstrBot Web API 公共件（异常收敛 / body / fail / ok）
"""
