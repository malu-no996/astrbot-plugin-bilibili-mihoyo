/* 绝区零 · 零号空洞 · 独立逻辑（plugins/_vendor/miyoushe/web/frag/zzz-abyss.js）
 * ------------------------------------------------------------------
 * 查询 + 数据归一化；模板在 frag/zzz-abyss.html，样式在 frag/zzz-abyss.css（ab-* 前缀）。
 * 由 module.js 在 setup 里调 window.MysAbyss.init/setup。
 *
 * 接口 abyss_abstract 的原始字段（★ 参数是 role_id + server，传 uid + region 会 -400005）：
 *   data: {
 *     abyss_level  { cur_level, max_level, icon },
 *     abyss_talent { cur_talent, max_talent },        ← 鸣徽等级
 *     abyss_duty   { cur_duty, max_duty } | null      ← ★ 可能是 null，取值必须判空
 *     abyss_point  { cur_point, max_point },          ← 老账号可能 0/0（游戏里已改版）
 *     abyss_collect[{ type, cur_collect, max_collect }],   type 1~5 见 COLLECT_NAME
 *     abyss_nest   { is_nest, is_hard_nest },         ← 枯败花圃（普通 / 地狱）
 *     abyss_throne { is_throne, max_damage },         ← 刀耕火焚（max_damage 是字符串数字）
 *     refresh_time, unlock }
 * 接口**不返回昵称 / 头像**，所以头部用当前查询角色名（模板里取 mysRoleName()）。
 */
window.MysAbyss = {
  /* 状态挂在共享的 mys reactive 上（module.js 负责创建 mys，这里只初始化自己的字段） */
  init(mys) {
    mys.abyss = {
      loading: false, done: false, error: '', raw: '', view: null,
    };
  },

  setup(ctx, mys, hooks) {
    const mysGet = (path, params) => ctx.get('' + path, params);
    const roleInfo = () => (hooks && hooks.mysRoleInfo ? hooks.mysRoleInfo() : null);

    /* 数据收集 type → 名称（沿用参考项目 ZZZeroUID 的 COLLECT_MAP，顺序即游戏内顺序） */
    const COLLECT_NAME = {
      1: '鸣徽图鉴', 2: '特殊区域记录', 3: '哨站课题', 4: '侵蚀研究', 5: '旧都失物',
    };

    /* 纯数字字符串（"98293878"）→ 带千分位；不是纯数字就原样返回 */
    function abNum(v) {
      const s = String(v == null ? '' : v).trim();
      return /^\d+$/.test(s) ? s.replace(/\B(?=(\d{3})+(?!\d))/g, ',') : s;
    }

    /* 进度条宽度：max <= 0（老账号的 0/0）时按空条处理，避免 NaN / Infinity */
    function abW(cur, max) {
      const c = Number(cur) || 0;
      const m = Number(max) || 0;
      if (m <= 0) return '0%';
      return Math.min(100, Math.round((c / m) * 100)) + '%';
    }

    /* {cur,max} 对 → 渲染模型（cur/max/进度宽度/是否已完成） */
    function abPair(o, keyCur, keyMax) {
      if (!o) return null;
      const cur = Number(o[keyCur]) || 0;
      const max = Number(o[keyMax]) || 0;
      return { cur: cur, max: max, w: abW(cur, max), full: max > 0 && cur >= max };
    }

    /* 原始返回 → 渲染模型（模板只认 view，不直接摸原始字段） */
    function abBuild(j) {
      const d = (j && j.data) || {};
      const collect = (d.abyss_collect || []).map(function (c) {
        const cur = Number(c.cur_collect) || 0;
        const max = Number(c.max_collect) || 0;
        return {
          name: COLLECT_NAME[c.type] || ('数据 ' + c.type),
          cur: cur, max: max, w: abW(cur, max), full: max > 0 && cur >= max,
        };
      }).filter(function (c) { return c.max > 0 || c.cur > 0; });

      const nest = d.abyss_nest || {};
      const throne = d.abyss_throne || {};
      return {
        unlock: !!d.unlock,
        level: Object.assign({ icon: (d.abyss_level || {}).icon || '' },
          abPair(d.abyss_level, 'cur_level', 'max_level') || { cur: 0, max: 0, w: '0%', full: false }),
        talent: abPair(d.abyss_talent, 'cur_talent', 'max_talent') || { cur: 0, max: 0, w: '0%', full: false },
        duty: abPair(d.abyss_duty, 'cur_duty', 'max_duty'),      // null = 本期没有悬赏委托
        point: (function () {
          const p = abPair(d.abyss_point, 'cur_point', 'max_point');
          return p && p.max > 0 ? p : null;                      // 0/0 视为无数据
        })(),
        collect: collect,
        nest: { normal: !!nest.is_nest, hard: !!nest.is_hard_nest },
        throne: { done: !!throne.is_throne, damage: abNum(throne.max_damage) },
      };
    }

    async function mysAbyss() {
      const ri = roleInfo();
      if (!ri) { mys.abyss.error = '请先选择角色'; return; }
      mys.abyss.loading = true;
      mys.abyss.error = '';
      mys.abyss.view = null;
      try {
        const j = await mysGet('/zzz/abyss', {
          uid: ri.uid, server: ri.server, account_id: mys.current,
        });
        if (j.ok) {
          mys.abyss.raw = JSON.stringify(j.data, null, 2);
          mys.abyss.view = abBuild(j);
          mys.abyss.done = true;
        } else mys.abyss.error = j.message || '查询零号空洞失败';
      } catch (e) { mys.abyss.error = '查询零号空洞失败：' + e.message; }
      mys.abyss.loading = false;
    }

    function mysAbyssReset() {
      mys.abyss.loading = false;
      mys.abyss.done = false;
      mys.abyss.error = '';
      mys.abyss.raw = '';
      mys.abyss.view = null;
    }

    return { mysAbyss, mysAbyssReset, abW };
  },
};
