/* 式舆防卫战 · 独立逻辑（plugins/_vendor/miyoushe/web/frag/zzz-shiyu.js）
 * ------------------------------------------------------------------
 * 查询 + 数据归一化；模板在 frag/zzz-shiyu.html，样式在 frag/zzz-shiyu.css（sy-* 前缀）。
 * 由 module.js 在 setup 里调 window.MysShiyu.init/setup。
 *
 * 接口 hadal_info_v2 的原始字段（对照 SIMNet 上游模型，别凭空猜）：
 *   data.hadal_info_v2: {
 *     hadal_begin_time / hadal_end_time (PartialTime),
 *     pass_fifth_floor,
 *     brief {score, rank_percent, rating("S+"/"S"/...), max_score},
 *     fitfh_layer_detail {        ← 官方就拼成 fitfh（fifth 的历史 typo），别"修"它
 *       layer_challenge_info_list[]: {layer_id, challenge_time, avatar_list, buddy,
 *         rating, buffer{title,text}, score, monster_pic, max_score, battle_time} },
 *     fourth_layer_detail { buffer{title,text}, challenge_time, rating,
 *       layer_challenge_info_list[]: {layer_id, challenge_time, avatar_list, buddy, battle_time} } },
 *   data 顶层: nick_name / icon
 * PartialTime = {year, month, day, hour, minute, second?}
 */
window.MysShiyu = {
  /* 状态挂在共享的 mys reactive 上（module.js 负责创建 mys，这里只初始化自己的字段） */
  init(mys) {
    mys.shiyu = {
      loading: false, done: false, error: '', previous: false,
      raw: '', view: null,
      showTime: false,    // 通关时刻开关（默认不显示，和危局的 mys.deadly.showTime 一致）
      // 第四防线开关：官方只给头像（没有得分 / 评级 / 立绘），默认关掉不显示
      showFourth: false,
      full: false,        // 「全面」开关（默认关）：查询时给头像左下角带音擎图标（需绑设备）
      savedAt: 0,         // 当前这份数据的存档时刻（0 = 没存上）
      savedKey: '',       // 当前显示的是哪一期（用于在存档列表里高亮）
      // 本地存档列表（官方只有本期/上期，历史都在后台攒的快照里）
      history: { open: false, loading: false, error: '', count: 0, list: [] },
    };
  },

  setup(ctx, mys, hooks) {
    const mysGet = (path, params) => ctx.get('' + path, params);
    const roleInfo = () => (hooks && hooks.mysRoleInfo ? hooks.mysRoleInfo() : null);

    /* PartialTime → "2026-09-22 01:43:00"（游戏内是横杠日期） */
    function syTime(t) {
      if (!t || !t.year) return '';
      const p = (n) => String(n || 0).padStart(2, '0');
      return t.year + '-' + p(t.month) + '-' + p(t.day) + ' '
        + p(t.hour) + ':' + p(t.minute) + ':' + p(t.second);
    }
    function syDay(t) { const s = syTime(t); return s ? s.slice(0, 10) : ''; }
    /* rank_percent → "50.00%+"（游戏样式）。
       注意：接口返回的 rank_percent 是「百分点 × 100」整数（5146 = 51.46%），要先 ÷100。 */
    function syPct(p) {
      const n = Number(p) || 0;
      const v = n / 100;
      return (v > 0 ? v.toFixed(2) : '0.00') + '%+';
    }
    /* 评级配色：S+ 金渐变 / S 金 / A 紫 / 其余蓝 */
    function syRatingCls(r) {
      const s = String(r || '').toUpperCase();
      if (s.startsWith('S+')) return 'sp';
      if (s.startsWith('S')) return 's';
      if (s.startsWith('A')) return 'a';
      return 'b';
    }
    /* 头像卡配色：S 金 / A 紫 */
    function syRarity(a) {
      return String(a.rarity || '').toUpperCase() === 'A' ? 'a' : 's';
    }
    /* 稀有度**方徽章**图（S / A / B）—— 和 QQ 出图 / 危局 / 绝境群排行同一套素材
       （texture2d/icon/{S,A,B}RANK.png，带「S RANK」字样的方徽章），
       取代原来贴在头像左上角的字母小标签。
       等级没素材（C 级等）时返回空串 → 模板退回字母小标签，不会裂图。 */
    const SY_ICON_BASE = './assets/icon/';
    function syRarIcon(rarity) {
      const r = String(rarity || '').toUpperCase();
      return ['S', 'A', 'B'].indexOf(r) >= 0 ? SY_ICON_BASE + r + 'RANK.png' : '';
    }
    function syAva(list, weapons) {
      /* weapons 是「全面」版后端给的 {角色id: 音擎图标本地路由}，
         没查到的角色 weapon 为空 → 模板不画左下角音擎标。 */
      const wp = weapons || {};
      return (list || []).map((a) => ({
        icon: a.role_square_url || a.icon || '',
        rarity: String(a.rarity || 'S').toUpperCase(),
        rank: Number(a.rank) || 0,   // 影画数（命座），0 也显示（与危局 .hd-ava-rank 同款）
        weapon: wp[String(a.id)] || '',   // 音擎图标（「全面」版，贴头像左下角）
      })).filter((a) => a.icon);
    }

    /* 一个出战小队（防线的一个 node）→ 渲染模型 */
    function syTeam(n, i, weapons) {
      return {
        no: i + 1,
        score: n.score || 0,
        rating: n.rating || '',
        time: syTime(n.challenge_time),
        avatars: syAva(n.avatar_list, weapons),
        buddy_icon: (n.buddy && (n.buddy.bangboo_rectangle_url || n.buddy.icon)) || '',
        /* 邦布稀有度 S / A：决定角标与边框配色，和危局 buddy_rarity 同一套 */
        buddy_rarity: n.buddy ? String(n.buddy.rarity || 'S').toUpperCase() : '',
        monster_pic: n.monster_pic || '',
        buffer: (n.buffer && n.buffer.title) || '',
        buffer_text: (n.buffer && n.buffer.text) || '',
      };
    }

    /* 原始返回 → 渲染模型（模板只认 view，不直接摸原始字段） */
    function syBuild(j) {
      const d = j.data || {};
      const info = d.hadal_info_v2 || {};
      const brief = info.brief || {};
      const fifth = info.fitfh_layer_detail || {};
      const fourth = info.fourth_layer_detail || {};
      /* 「全面」版专用字段：weapons = {角色id: 音擎图标}；weapon_warn = 缺设备配置时的提醒 */
      const weapons = d.weapons || {};
      const teams5 = (fifth.layer_challenge_info_list || []).map((n, i) => syTeam(n, i, weapons));
      const score5 = teams5.reduce((s, t) => s + (t.score || 0), 0);
      const b1 = syDay(info.hadal_begin_time);
      const b2 = syDay(info.hadal_end_time);
      return {
        previous: !!j.previous,
        saved: !!j.saved,              // true = 这份是本地存档里的历史快照
        nick_name: d.nick_name || '',
        icon: d.icon || '',
        rating: brief.rating || '',
        rank: brief.rank_percent != null ? syPct(brief.rank_percent) : '',
        score: brief.score || score5,
        period: (b1 || b2) ? (b1 + ' - ' + b2) : '',
        fifth: { teams: teams5 },
        fourth: {
          rating: fourth.rating || '',
          buffer: (fourth.buffer && fourth.buffer.title) || '',
          buffer_text: (fourth.buffer && fourth.buffer.text) || '',
          teams: (fourth.layer_challenge_info_list || []).map((n, i) => syTeam(n, i, weapons)),
        },
        weapon_warn: d.weapon_warn || '',
      };
    }

    /* ---------------- 本地存档（历史） ----------------
     * 官方 hadal_info_v2 只有本期/上期两份；后台每查一次就按赛期存一份快照
     * （见插件里 record_store.py），这里列的是「攒下来的历史」。
     * 同一赛期只保留最新一版，但会记下被刷新过几次（revisions）。 */

    function syHistAt(ts) {
      if (!ts) return '';
      const d = new Date(Number(ts) * 1000);
      if (isNaN(d.getTime())) return '';
      const p = (n) => String(n).padStart(2, '0');
      return (d.getMonth() + 1) + '-' + p(d.getDate()) + ' ' + p(d.getHours()) + ':' + p(d.getMinutes());
    }

    /* 存档列表里的赛期：用和页面上一样的横杠日期格式 */
    function syHistPeriod(h) {
      const a = syDay(h.start), b = syDay(h.end);
      if (a || b) return a + ' - ' + b;
      return h.period || h.key || '';
    }

    function syHistSum(h) {
      const s = h.summary || {};
      const parts = [];
      if (s.rating) parts.push('评级 ' + s.rating);
      if (s.score) parts.push('总分 ' + s.score);
      if (s.rank_percent) parts.push('前 ' + (Number(s.rank_percent) / 100).toFixed(2) + '%');
      if (s.teams) parts.push(s.teams + ' 队');
      return parts.length ? parts.join(' · ') : '（无成绩数据）';
    }

    async function mysShiyuHistory() {
      const ri = roleInfo();
      if (!ri) { mys.shiyu.history.error = '请先选择角色'; return; }
      mys.shiyu.history.loading = true;
      mys.shiyu.history.error = '';
      try {
        const j = await mysGet('/zzz/records', { kind: 'shiyu', uid: ri.uid });
        if (j.ok) {
          mys.shiyu.history.list = j.periods || [];
          mys.shiyu.history.count = j.count || 0;
        } else mys.shiyu.history.error = j.message || '读取本地存档失败';
      } catch (e) { mys.shiyu.history.error = '读取本地存档失败：' + e.message; }
      mys.shiyu.history.loading = false;
    }

    async function mysShiyuHistoryToggle() {
      mys.shiyu.history.open = !mys.shiyu.history.open;
      if (mys.shiyu.history.open) await mysShiyuHistory();
    }

    /* 点某一期 → 用本地快照渲染（复用 syBuild，画面与实时查询完全一致） */
    async function mysShiyuPick(key) {
      const ri = roleInfo();
      if (!ri) return;
      mys.shiyu.loading = true;
      mys.shiyu.error = '';
      try {
        const j = await mysGet('/zzz/records/item', { kind: 'shiyu', uid: ri.uid, key: key });
        if (j.ok) {
          mys.shiyu.previous = false;
          mys.shiyu.raw = JSON.stringify(j.data, null, 2);
          mys.shiyu.view = syBuild({ data: j.data, previous: false, saved: true });
          mys.shiyu.savedAt = (j.record && j.record.updated_at) || 0;
          mys.shiyu.savedKey = key;
          mys.shiyu.done = true;
        } else mys.shiyu.error = j.message || '读取该期存档失败';
      } catch (e) { mys.shiyu.error = '读取该期存档失败：' + e.message; }
      mys.shiyu.loading = false;
    }

    async function mysShiyu(previous) {
      const ri = roleInfo();
      if (!ri) { mys.shiyu.error = '请先选择角色'; return; }
      mys.shiyu.loading = true;
      mys.shiyu.error = '';
      mys.shiyu.view = null;
      try {
        const j = await mysGet('/zzz/shiyu', {
          uid: ri.uid, server: ri.server, previous: previous ? 'true' : '', account_id: mys.current,
          full: mys.shiyu.full ? 'true' : '',   // 「全面」：后端顺手查音擎图标（→ data.weapons）
        });
        if (j.ok) {
          mys.shiyu.previous = !!previous;
          mys.shiyu.raw = JSON.stringify(j.data, null, 2);
          mys.shiyu.view = syBuild(j);
          mys.shiyu.savedAt = (j.record && j.record.updated_at) || 0;   // 后台已按赛期存档
          mys.shiyu.savedKey = (j.record && j.record.key) || '';
          mys.shiyu.done = true;
          if (mys.shiyu.history.open) await mysShiyuHistory();          // 面板开着就顺手刷一下列表
        } else mys.shiyu.error = j.message || '查询式舆防卫战失败';
      } catch (e) { mys.shiyu.error = '查询式舆防卫战失败：' + e.message; }
      mys.shiyu.loading = false;
    }

    function mysShiyuReset() {
      mys.shiyu.loading = false;
      mys.shiyu.done = false;
      mys.shiyu.error = '';
      mys.shiyu.previous = false;
      mys.shiyu.raw = '';
      mys.shiyu.view = null;
      mys.shiyu.showTime = false;
      mys.shiyu.showFourth = false;
      mys.shiyu.savedAt = 0;
      mys.shiyu.savedKey = '';
      // 换账号/换角色后，列表属于上一个 uid，收起并清空（再打开会重新拉）
      mys.shiyu.history.open = false;
      mys.shiyu.history.loading = false;
      mys.shiyu.history.error = '';
      mys.shiyu.history.count = 0;
      mys.shiyu.history.list = [];
    }

    return {
      mysShiyu, mysShiyuReset, syTime, syPct, syRatingCls, syRarity, syRarIcon,
      // 本地存档（历史）
      mysShiyuHistory, mysShiyuHistoryToggle, mysShiyuPick,
      syHistAt, syHistPeriod, syHistSum,
    };
  },
};
