"""astrbot-plugin-miyoho —— 米哈游（米游社）插件后端包。

  core/     公共层：登录 / 凭证 / 绑定 / 设备指纹 / 图片缓存 / 绘图 / Web API 公共件
  social/   QQ 群社交命令（可配置命令 → 接口分发）
  zzz/      绝区零：record（战绩）/ gacha（调频）/ codex（图鉴）/ avatar（角色）/
            sign（签到+自动签到）/ month（绳网月报）
  send.py   AstrBot 发送适配（私聊通知 / 平台实例列表）
  securestore.py  凭证落盘加密（自带，密钥 MIYOHO_AUTH_KEY 或 data/.auth_key）
  paths.py  插件内路径（data/ 全在这里）

导入本包即可完成：接口注册表填充、各 store 的数据文件加载。
路由注册表（各 routes.ROUTES）由 main.py 统一挂到 AstrBot。
"""
from . import core as core  # noqa: F401
