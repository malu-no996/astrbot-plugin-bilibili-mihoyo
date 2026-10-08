/* 危局强袭战 · 独立逻辑（plugins/_vendor/miyoushe/web/frag/zzz-deadly.js）
 * ------------------------------------------------------------------
 * 查询 + 数据归一化；模板在 frag/zzz-deadly.html，样式在 frag/zzz-deadly.css（hd-* 前缀）。
 * 由 module.js 在 setup 里调 window.MysDeadly.init/setup（frag/*.js 在 module.js 之后加载，
 * 但 setup 要到 boot 时才执行，所以文件先后顺序无所谓）。
 *
 * 接口 hadal_mem_detail_v2 的原始字段（对照 SIMNet 上游模型，别凭空猜）：
 *   顶层: nick_name / avatar_icon / total_score / total_star / rank_percent
 *         / start_time / end_time (PartialTime) / has_data
 *         / has_hard / hard_list (绝境模式，结构与 list 完全相同) / hard_rank_percent
 *   list[]: score / star / challenge_time(PartialTime)
 *           boss[{name, icon(BOSS立绘), bg_icon(通用渐变底图), race_icon}]
 *           buffer[{name, desc, icon}]
 *           avatar_list[{role_square_url, rarity("S"/"A"), rank(影画数，0 也会显示), level, ...}]
 *           buddy{bangboo_rectangle_url, rarity("S"/"A")} | null
 * PartialTime = {year, month, day, hour, minute, second?}
 */
window.MysDeadly = {
  /* 状态挂在共享的 mys reactive 上（module.js 负责创建 mys，这里只初始化自己的字段） */
  init(mys) {
    mys.deadly = {
      loading: false, done: false, error: '', previous: false,
      raw: '', view: null,
      showTime: false,    // 通关时间开关（默认不显示）
      full: false,        // 「全面」开关（默认关）：查询时给头像左下角带音擎图标（需绑设备）
      saving: false,      // 生成图片中
      savedAt: 0,         // 当前显示的这份数据的存档时刻（0 = 没存上）
      savedKey: '',       // 当前显示的是哪一期（用于在存档列表里高亮）
      // 本地存档列表（官方只有本期/上期，历史都在后台攒的快照里）
      history: { open: false, loading: false, error: '', count: 0, list: [] },
    };
  },

  setup(ctx, mys, hooks) {
    const mysGet = (path, params) => ctx.get('' + path, params);
    const roleInfo = () => (hooks && hooks.mysRoleInfo ? hooks.mysRoleInfo() : null);

    /* PartialTime → "2026.09.22 02:36:34"（游戏内是点分日期） */
    function dzTime(t) {
      if (!t || !t.year) return '';
      const p = (n) => String(n || 0).padStart(2, '0');
      return t.year + '.' + p(t.month) + '.' + p(t.day) + ' '
        + p(t.hour) + ':' + p(t.minute) + ':' + p(t.second);
    }
    function dzDay(t) { const s = dzTime(t); return s ? s.slice(0, 10) : ''; }
    /* rank_percent 官方口径是 百分比×100（5284 → 52.84%+），游戏内显示两位小数 */
    function dzPct(p) {
      const n = (Number(p) || 0) / 100;
      return n.toFixed(2) + '%+';
    }
    /* 头像卡配色：S 金 / A 紫（rarity 官方给 "S"/"A" 字符串） */
    function dzRarity(a) {
      return String(a.rarity || '').toUpperCase() === 'A' ? 'a' : 's';
    }
    /* 稀有度**方徽章**图（S / A / B）—— 和 QQ 出图 / 绝境群排行 / 代理人详情浮层
       用的是同一套素材（texture2d/icon/{S,A,B}RANK.png，带「S RANK」字样的方徽章），
       取代原来贴在头像左上角的字母小标签。
       等级没素材（C 级等）时返回空串 → 模板退回字母小标签，不会裂图。 */
    const DZ_ICON_BASE = './assets/icon/';
    function dzRarIcon(rarity) {
      const r = String(rarity || '').toUpperCase();
      return ['S', 'A', 'B'].indexOf(r) >= 0 ? DZ_ICON_BASE + r + 'RANK.png' : '';
    }
    function dzAva(list, weapons) {
      /* list → 头像模型。weapons 是「全面」版后端给的 {角色id: 音擎图标本地路由}，
         没查到的角色 weapon 为空 → 模板不画左下角那枚音擎标（优雅降级）。 */
      const wp = weapons || {};
      return (list || []).map((a) => ({
        icon: a.role_square_url || a.icon || '',
        rarity: String(a.rarity || 'S').toUpperCase(),
        rank: Number(a.rank) || 0,   // 影画数（命座），0 也显示
        weapon: wp[String(a.id)] || '',   // 音擎图标（「全面」版，贴头像左下角）
      })).filter((a) => a.icon);
    }

    /* 增益：保留 name + 详情 desc（去掉 <color> 富文本标签、保留换行） */
    function dzBuffs(list) {
      return (list || []).map((b) => {
        let desc = b && b.desc ? String(b.desc) : '';
        desc = desc.replace(/<color=[^>]*>/g, '').replace(/<\/color>/g, '');
        return { name: b && b.name ? b.name : '', desc };
      }).filter((b) => b.name);
    }

    /* 单条挑战记录 → 渲染模型（list 与 hard_list 结构相同，共用） */
    function dzItem(c, weapons) {
      const boss = (c.boss && c.boss[0]) || {};
      return {
        boss_name: boss.name || '',
        /* icon 是 BOSS 立绘；bg_icon 只是官方的通用渐变底图（别再用它当立绘） */
        boss_img: boss.icon || boss.bg_icon || '',
        score: c.score || 0,
        star: c.star || 0,
        time: dzTime(c.challenge_time),
        avatars: dzAva(c.avatar_list, weapons),
        buddy_icon: (c.buddy && (c.buddy.bangboo_rectangle_url || c.buddy.icon)) || '',
        buddy_rarity: c.buddy ? String(c.buddy.rarity || 'S').toUpperCase() : '',
        buffers: dzBuffs(c.buffer),
      };
    }

    /* 原始返回 → 渲染模型（模板只认 view，不直接摸原始字段） */
    function dzBuild(j) {
      const d = j.data || {};
      const p1 = dzDay(d.start_time);
      const p2 = dzDay(d.end_time);
      /* 「全面」版专用字段：weapons = {角色id: 音擎图标}；weapon_warn = 缺设备配置时的提醒 */
      const weapons = d.weapons || {};
      return {
        previous: !!j.previous,
        saved: !!j.saved,              // true = 这份是本地存档里的历史快照
        nick_name: d.nick_name || '',
        avatar_icon: d.avatar_icon || '',
        total_score: d.total_score || 0,
        total_star: d.total_star || 0,
        rank: dzPct(d.rank_percent),
        /* 绝境模式：hard_list 与 list 同构；hard_rank_percent 是绝境自己的排名 */
        hard: (d.hard_list || []).map((c) => dzItem(c, weapons)),
        hard_rank: dzPct(d.hard_rank_percent),
        period: (p1 || p2) ? (p1 + ' - ' + p2) : '',
        list: (d.list || []).map((c) => dzItem(c, weapons)),
        weapon_warn: d.weapon_warn || '',
      };
    }

    /* ---------------- 本地存档（历史） ----------------
     * 官方 hadal_mem_detail_v2 只有本期/上期两份；后台每查一次就按赛期存一份快照
     * （见插件里 record_store.py），所以这里列的是「攒下来的历史」。
     * 同一赛期只保留最新一版，但会记下被刷新过几次（revisions）。 */

    function dzHistAt(ts) {
      if (!ts) return '';
      const d = new Date(Number(ts) * 1000);
      if (isNaN(d.getTime())) return '';
      const p = (n) => String(n).padStart(2, '0');
      return (d.getMonth() + 1) + '-' + p(d.getDate()) + ' ' + p(d.getHours()) + ':' + p(d.getMinutes());
    }

    /* 存档列表里的赛期：用和页面上一样的点分日期格式 */
    function dzHistPeriod(h) {
      const a = dzDay(h.start), b = dzDay(h.end);
      if (a || b) return a + ' - ' + b;
      return h.period || h.key || '';
    }

    function dzHistSum(h) {
      const s = h.summary || {};
      const parts = [];
      if (s.score) parts.push('总分 ' + s.score);
      if (s.star) parts.push('★ × ' + s.star);
      if (s.rank_percent) parts.push('前 ' + (Number(s.rank_percent) / 100).toFixed(2) + '%');
      if (s.hard) parts.push('含绝境');
      return parts.length ? parts.join(' · ') : '（无成绩数据）';
    }

    async function mysDeadlyHistory() {
      const ri = roleInfo();
      if (!ri) { mys.deadly.history.error = '请先选择角色'; return; }
      mys.deadly.history.loading = true;
      mys.deadly.history.error = '';
      try {
        const j = await mysGet('/zzz/records', { kind: 'deadly', uid: ri.uid });
        if (j.ok) {
          mys.deadly.history.list = j.periods || [];
          mys.deadly.history.count = j.count || 0;
        } else mys.deadly.history.error = j.message || '读取本地存档失败';
      } catch (e) { mys.deadly.history.error = '读取本地存档失败：' + e.message; }
      mys.deadly.history.loading = false;
    }

    async function mysDeadlyHistoryToggle() {
      mys.deadly.history.open = !mys.deadly.history.open;
      if (mys.deadly.history.open) await mysDeadlyHistory();
    }

    /* 点某一期 → 用本地快照渲染（复用 dzBuild，画面与实时查询完全一致） */
    async function mysDeadlyPick(key) {
      const ri = roleInfo();
      if (!ri) return;
      mys.deadly.loading = true;
      mys.deadly.error = '';
      try {
        const j = await mysGet('/zzz/records/item', { kind: 'deadly', uid: ri.uid, key: key });
        if (j.ok) {
          mys.deadly.previous = false;
          mys.deadly.raw = JSON.stringify(j.data, null, 2);
          mys.deadly.view = dzBuild({ data: j.data, previous: false, saved: true });
          mys.deadly.savedAt = (j.record && j.record.updated_at) || 0;
          mys.deadly.savedKey = key;
          mys.deadly.done = true;
        } else mys.deadly.error = j.message || '读取该期存档失败';
      } catch (e) { mys.deadly.error = '读取该期存档失败：' + e.message; }
      mys.deadly.loading = false;
    }

    async function mysDeadly(previous) {
      const ri = roleInfo();
      if (!ri) { mys.deadly.error = '请先选择角色'; return; }
      mys.deadly.loading = true;
      mys.deadly.error = '';
      mys.deadly.view = null;
      try {
        const j = await mysGet('/zzz/deadly', {
          uid: ri.uid, server: ri.server, previous: previous ? 'true' : '', account_id: mys.current,
          full: mys.deadly.full ? 'true' : '',   // 「全面」：后端顺手查音擎图标（→ data.weapons）
        });
        if (j.ok) {
          mys.deadly.previous = !!previous;
          mys.deadly.raw = JSON.stringify(j.data, null, 2);
          mys.deadly.view = dzBuild(j);
          mys.deadly.savedAt = (j.record && j.record.updated_at) || 0;   // 后台已按赛期存档
          mys.deadly.savedKey = (j.record && j.record.key) || '';
          mys.deadly.done = true;
          if (mys.deadly.history.open) await mysDeadlyHistory();         // 面板开着就顺手刷一下列表
        } else mys.deadly.error = j.message || '查询危局强袭战失败';
      } catch (e) { mys.deadly.error = '查询危局强袭战失败：' + e.message; }
      mys.deadly.loading = false;
    }

    function mysDeadlyReset() {
      mys.deadly.loading = false;
      mys.deadly.done = false;
      mys.deadly.error = '';
      mys.deadly.previous = false;
      mys.deadly.raw = '';
      mys.deadly.view = null;
      mys.deadly.showTime = false;
      mys.deadly.saving = false;
      mys.deadly.savedAt = 0;
      mys.deadly.savedKey = '';
      // 换账号/换角色后，列表属于上一个 uid，收起并清空（再打开会重新拉）
      mys.deadly.history.open = false;
      mys.deadly.history.loading = false;
      mys.deadly.history.error = '';
      mys.deadly.history.count = 0;
      mys.deadly.history.list = [];
    }

    /* 动态加载 html-to-image（UMD），只加载一次 */
    function loadScript(src) {
      return new Promise((resolve, reject) => {
        if (document.querySelector('script[data-html2img]')) return resolve();
        const s = document.createElement('script');
        s.src = src; s.dataset.html2img = '1';
        s.onload = () => resolve();
        s.onerror = () => reject(new Error('图片库加载失败（可能无外网）'));
        document.head.appendChild(s);
      });
    }

    /* 将危局结果（#hd-wrap）渲染成 PNG 并下载 */
    async function mysDeadlySaveImage() {
      if (!mys.deadly.view) return;
      const node = document.getElementById('hd-wrap');
      if (!node) { mys.deadly.error = '没有可导出的内容'; return; }
      mys.deadly.saving = true;
      try {
        await loadScript('https://cdn.jsdelivr.net/npm/html-to-image@1.11.11/dist/html-to-image.js');
        const dataUrl = await window.htmlToImage.toPng(node, {
          pixelRatio: 2, backgroundColor: '#0f0c14', cacheBust: true,
        });
        const a = document.createElement('a');
        a.download = '危局强袭战_' + (mys.deadly.view.nick_name || 'export') + '.png';
        a.href = dataUrl; a.click();
      } catch (e) {
        mys.deadly.error = '生成图片失败：' + e.message + '（立绘图可能受米游社 CDN 跨域限制，可关掉再试）';
      }
      mys.deadly.saving = false;
    }

    return {
      mysDeadly, mysDeadlyReset, mysDeadlySaveImage, dzTime, dzPct, dzRarity, dzRarIcon,
      // 本地存档（历史）
      mysDeadlyHistory, mysDeadlyHistoryToggle, mysDeadlyPick,
      dzHistAt, dzHistPeriod, dzHistSum,
    };
  },
};
