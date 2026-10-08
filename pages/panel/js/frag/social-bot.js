/* 社交命令配置 · QQ机器人配置子页面逻辑（plugins/_vendor/miyoushe/web/frag/social-bot.js）
 * 模板在 frag/social-bot.html，样式在 frag/social-bot.css（sb-* 前缀）。
 * 机器人行由 frag/social.js 的 rebuildRows() 组装（在线实例 + 配置里出现过的合并），
 * 这里出两个开关方法：
 *   sbMaster(b, on) —— 总开关，连带把该机器人的逐命令开关一起同步
 *   sbAll(b, on)    —— 只批量改该机器人下的逐命令开关
 * 都只改页面上的值，仍要点右上角「保存」才落盘。
 * 状态由 frag/social.js 的 init 建立，所以这里只有 setup、没有 init。
 *
 * ⚠️ 群订阅（哪些群能用 + 群员绑定账号）**不在这一页**（2026-10-08 挪走）：
 * 一级分类「群订阅」→ frag/subscribe.js → subscribe/* 接口。这里只管「机器人 × 命令」。
 */
window.MysSocialBot = {
  setup(ctx, mys) {
    /* 总开关。**必须连带同步逐命令开关**，否则有个很难发现的坑：
     * 逐命令开关的初始值取自「保存时那份配置」——主开关本来是关的，
     * 那些值就全是 false；用户把主开关一开（子开关从隐藏变可见、全是未勾选）
     * 直接保存 → bot_cmds 里全写 false → 后端 bot_allows 先过总开关、再看逐命令，
     * 结果一条都不执行，用户看到的是「开了开关也没反应」。
     * 所以这里开总开关时把子开关一起打开（想只留几条，开完再单独取消即可）。 */
    function sbMaster(b, on) {
      b.on = !!on;
      for (const c of (b && b.cmds) || []) c.val = b.on;
      mys.social.message = '';
    }

    /* 批量设置某个机器人下所有命令的开关。
     * val 由模板传 true / false —— 不写成「取反」，因为「全开」要幂等：
     * 已经全开时再点一次不该变成全关。 */
    function sbAll(b, val) {
      const on = !!val;
      for (const c of (b && b.cmds) || []) c.val = on;
      mys.social.message = '';
    }

    return { sbMaster, sbAll };
  },
};
