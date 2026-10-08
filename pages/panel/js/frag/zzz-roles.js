/* 绝区零 · 角色查询（plugins/_vendor/miyoushe/web/frag/zzz-roles.js）
 * ------------------------------------------------------------------
 * 查询 + 归一化；模板在 frag/zzz-roles.html，样式在 frag/zzz-roles.css（rl-* 前缀）。
 * 由 module.js 在 setup 里调 window.MysRoles.init/setup（frag/*.js 在 module.js 之后加载，
 * 但 setup 要到 boot 时才执行，所以文件先后顺序无所谓）。
 *
 * 后端 /zzz/avatars 的返回：{ ok, source, source_name, count, avatars[], note }
 * 每个 avatar：{ id, name, full_name, rarity, element, profession, camp,
 *              level, rank(影画数), icon, chosen, source }
 * 官方源自带中文名；Enka 降级源的名字/元素由后端 char_map 补，rank 恒为 0。
 */
window.MysRoles = {
  /* 状态挂在共享的 mys reactive 上（module.js 负责创建 mys，这里只初始化自己的字段） */
  init(mys) {
    mys.avatars = {
      loading: false, done: false, error: '', note: '',
      source: '', source_name: '', count: 0, list: [],
    };
  },

  setup(ctx, mys, hooks) {
    const mysGet = (path, params) => ctx.get('' + path, params);
    const roleInfo = () => (hooks && hooks.mysRoleInfo ? hooks.mysRoleInfo() : null);

    /* 图片挂掉时的占位（Enka 少数条目 sprite 缺失会 404，别显示破图） */
    const PLACEHOLDER = 'data:image/svg+xml;charset=utf-8,' + encodeURIComponent(
      "<svg xmlns='http://www.w3.org/2000/svg' width='120' height='120'>"
      + "<rect width='120' height='120' fill='#241c2e'/>"
      + "<text x='60' y='72' font-size='40' fill='#6d5c80' text-anchor='middle'>?</text></svg>"
    );

    /* 稀有度图标基址：官方的圆形徽章图放在插件静态目录的 texture2d/icon 下 */
    const RARITY_BASE = './assets/icon/';

    /* 稀有度 → 卡片配色（S 金 / A 紫，与危局那头像角标同一套） */
    function rlRarity(a) {
      return String(a.rarity || '').toUpperCase() === 'A' ? 'a' : 's';
    }

    /* 稀有度专用图标：S / A / B / C 用官方圆形徽章；其它值没有素材 → 返回空串，
       模板退回原来的文字小标签（与角色详情浮层同一套素材与规则）。 */
    function rlRarIcon(a) {
      const r = String((a && a.rarity) || '').toUpperCase();
      return ['S', 'A', 'B', 'C'].indexOf(r) >= 0 ? RARITY_BASE + 'Rarity_' + r + '.png' : '';
    }

    /* 稀有度图标加载失败：藏掉 img，把紧随其后的文字标签放出来（兜底） */
    function rlRarErr(e) {
      const img = e && e.target;
      if (!img) return;
      img.style.display = 'none';
      const sib = img.nextElementSibling;
      if (sib && sib.classList && sib.classList.contains('rl-star')) sib.style.display = '';
    }

    function rlImgErr(e) {
      e.target.onerror = null;
      e.target.src = PLACEHOLDER;
    }

    async function mysAvatars() {
      const ri = roleInfo();
      if (!ri) { mys.avatars.error = '请先选择角色'; return; }
      mys.avatars.loading = true;
      mys.avatars.error = '';
      mys.avatars.note = '';
      try {
        const j = await mysGet('/zzz/avatars', {
          uid: ri.uid, server: ri.server, account_id: mys.current,
        });
        if (j.ok) {
          mys.avatars.list = j.avatars || [];
          mys.avatars.count = j.count || 0;
          mys.avatars.source = j.source || '';
          mys.avatars.source_name = j.source_name || '';
          mys.avatars.note = j.note || '';
          mys.avatars.done = true;
        } else mys.avatars.error = j.message || '查询角色失败';
      } catch (e) { mys.avatars.error = '查询角色失败：' + e.message; }
      mys.avatars.loading = false;
    }

    function mysAvatarsReset() {
      mys.avatars.loading = false;
      mys.avatars.done = false;
      mys.avatars.error = '';
      mys.avatars.note = '';
      mys.avatars.source = '';
      mys.avatars.source_name = '';
      mys.avatars.count = 0;
      mys.avatars.list = [];
    }

    return { mysAvatars, mysAvatarsReset, rlRarity, rlImgErr, rlRarIcon, rlRarErr };
  },
};
