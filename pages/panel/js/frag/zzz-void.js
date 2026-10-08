/* 绝区零 · 临界推演（plugins/_vendor/miyoushe/web/frag/zzz-void.js）
 * 查询 + 数据已是后端归一化好的 view；模板在 frag/zzz-void.html，样式在 frag/zzz-void.css（vt-* 前缀）。
 * 由 module.js 在 setup 里调 window.MysVoid.init/setup。
 */
window.MysVoid = {
  init(mys) {
    mys.void = {
      loading: false, done: false, error: '', raw: '', view: null, loaded: false,
    };
  },

  setup(ctx, mys, hooks) {
    const mysGet = (path, params) => ctx.get('' + path, params);
    const roleInfo = () => (hooks && hooks.mysRoleInfo ? hooks.mysRoleInfo() : null);

    /* 图片挂掉时的占位（避免破图） */
    function vtImgErr(e) {
      e.target.onerror = null;
      e.target.style.visibility = 'hidden';
    }

    /* 剩余秒数 → "还剩 3 天 4 小时" */
    function vtLeft(sec) {
      const t = Number(sec) || 0;
      if (t <= 0) return '';
      const d = Math.floor(t / 86400), h = Math.floor((t % 86400) / 3600);
      if (d && h) return '还剩 ' + d + ' 天 ' + h + ' 小时';
      return d ? ('还剩 ' + d + ' 天') : ('还剩 ' + h + ' 小时');
    }

    async function mysVoid() {
      const ri = roleInfo();
      if (!ri) { mys.void.error = '请先选择角色'; return; }
      mys.void.loading = true;
      mys.void.error = '';
      mys.void.view = null;
      try {
        const j = await mysGet('/zzz/void', {
          uid: ri.uid, server: ri.server, account_id: mys.current,
        });
        if (j.ok) {
          mys.void.raw = JSON.stringify(j.data, null, 2);
          mys.void.view = j.view;
          mys.void.done = true;
        } else mys.void.error = j.message || '查询临界推演失败';
      } catch (e) { mys.void.error = '查询临界推演失败：' + e.message; }
      mys.void.loaded = true;
      mys.void.loading = false;
    }

    function mysVoidReset() {
      mys.void.loading = false;
      mys.void.done = false;
      mys.void.error = '';
      mys.void.raw = '';
      mys.void.view = null;
      mys.void.loaded = false;
    }

    return { mysVoid, mysVoidReset, vtImgErr, vtLeft };
  },
};
