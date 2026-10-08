"""绝区零 · 绳网月报（nap_ledger）。

  api.py     官方接口 `/month_info`：本月 / 指定月份的资源收入构成
  routes.py  管理页路由（/admin/api/miyoushe/zzz/month*）—— 导入即注册
  ../social/month.py 里是 QQ 命令那一份接口（`@interface("zzz_month")`）

月报和「战绩」不是一回事（它是按自然月统计的资源流水，域名也在活动域），
所以单独占一个功能目录，不塞进 record。
"""
