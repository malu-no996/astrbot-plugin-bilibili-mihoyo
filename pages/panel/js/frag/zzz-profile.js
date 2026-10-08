/* 绝区零 · 玩家概览（plugins/_vendor/miyoushe/web/frag/zzz-profile.js）
 * 查询 + 数据已是后端归一化好的 view；模板在 frag/zzz-profile.html，样式在 frag/zzz-profile.css（pv-* 前缀）。
 * 由 module.js 在 setup 里调 window.MysProfile.init/setup。
 */
window.MysProfile = {
  init(mys) {
    mys.profile = {
      loading: false, done: false, error: '', raw: '', view: null, loaded: false,
    };
  },

  setup(ctx, mys, hooks) {
    const mysGet = (path, params) => ctx.get('' + path, params);
    const roleInfo = () => (hooks && hooks.mysRoleInfo ? hooks.mysRoleInfo() : null);

    function pvImgErr(e) {
      e.target.onerror = null;
      e.target.style.visibility = 'hidden';
    }

    async function mysProfile() {
      const ri = roleInfo();
      if (!ri) { mys.profile.error = '请先选择角色'; return; }
      mys.profile.loading = true;
      mys.profile.error = '';
      mys.profile.view = null;
      try {
        const j = await mysGet('/zzz/profile', {
          uid: ri.uid, server: ri.server, account_id: mys.current,
        });
        if (j.ok) {
          mys.profile.raw = JSON.stringify(j.data, null, 2);
          mys.profile.view = j.view;
          mys.profile.done = true;
        } else mys.profile.error = j.message || '查询玩家概览失败';
      } catch (e) { mys.profile.error = '查询玩家概览失败：' + e.message; }
      mys.profile.loaded = true;
      mys.profile.loading = false;
    }

    function mysProfileReset() {
      mys.profile.loading = false;
      mys.profile.done = false;
      mys.profile.error = '';
      mys.profile.raw = '';
      mys.profile.view = null;
      mys.profile.loaded = false;
    }

    return { mysProfile, mysProfileReset, pvImgErr };
  },
};
