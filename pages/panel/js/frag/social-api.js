/* 社交命令配置 · 接口配置子页面逻辑（plugins/_vendor/miyoushe/web/frag/social-api.js）
 * 模板在 frag/social-api.html，样式在 frag/social-api.css（sa-* 前缀）。
 * 只有「加一行 / 删一行」两个纯本地改动：改动留在 mys.social.commands 里，
 * 点页面右上角的「保存」才写回后端（frag/social.js 的 mysSocialSave）。
 * 状态由 frag/social.js 的 init 建立，所以这里只有 setup、没有 init。 */
window.MysSocialApi = {
  setup(ctx, mys, hooks) {
    const rebuild = () => { if (hooks && hooks.socialRows) hooks.socialRows(); };

    /* 新行给个稳定但不重复的 id：后端认 id 作为逐命令开关的键，
     * 保存时若 id 为空后端会自己补随机串，但那时页面已经刷新过一轮，不如现在就给。 */
    function newId() {
      const used = {};
      for (const c of mys.social.commands || []) used[c.id] = 1;
      let n = (mys.social.commands || []).length + 1;
      while (used['new_' + n]) n += 1;
      return 'new_' + n;
    }

    function saAdd() {
      const list = mys.social.commands || [];
      const last = list[list.length - 1] || {};
      mys.social.commands.push({
        id: newId(),
        // 新行默认跟上一行的指令组（大多数命令都是 zzz，没得参考就 zzz）
        group: String(last.group || 'zzz'),
        cmd: '',
        alias_text: '',
        api: (mys.social.interfaces[0] || {}).key || '',
        enabled: true,
        admin_only: false,
        // 详细设置是「接口选项 + 两套回复模板」：新行先空着，
        // 点「详细设置」时按接口 schema 补默认值（见 frag/social-detail.js）。
        options: {},
        tpl: { onebot: '', qq: '' },
      });
      rebuild();
      mys.social.message = '';
    }

    function saDel(i) {
      const c = (mys.social.commands || [])[i];
      if (!c) return;
      mys.social.commands.splice(i, 1);
      rebuild();                       // 机器人矩阵里那一列要跟着消失
      mys.social.message = '';
    }

    return { saAdd, saDel };
  },
};
