/* 绝区零 · 迷宫诡域 · 独立逻辑（plugins/_vendor/miyoushe/web/frag/zzz-zenkov.js）
 * ------------------------------------------------------------------
 * 查询 + 数据归一化；模板在 frag/zzz-zenkov.html，样式在 frag/zzz-zenkov.css（zk-* 前缀）。
 * 由 module.js 在 setup 里调 window.MysZenkov.init/setup。
 *
 * 接口 zenkov_abstract_info（参数 uid + region）的原始字段：
 *   data: {
 *     nick_name, avatar_icon,
 *     collect_total_value ("214.75M"), big_red_num ("203"), millions_evacuations ("53"),
 *     refresh_time (剩余秒数), abyss_duty {cur_duty, max_duty},
 *     abyss_unlock, season_unlock,
 *     season_data { cur_season_id, season_level, season_stage,
 *                   season_quest {cur_quest, max_quest}, season_coin {cur_coin, max_coin},
 *                   refresh_time },
 *     map_list [{ map_id, map_name, leave_percent, max_price,
 *                 hell_unlock, hard_unlock, is_challenge }],
 *     max_rank, is_show_percent,
 *     collection_data { medal_data {list[{medal_icon,name,medal_id,unlock}], cur, total},
 *                       goods_data {list[{goods_icon,name,number,goods_id,unlock}], cur, total} } }
 *
 * 两个换算口径（与危局 rank_percent 同一套"×100 存整数"的写法）：
 *   leave_percent 7778 → 77.78%（撤离度）
 *   max_rank 1841 + is_show_percent=true → 前 18.41%（否则按"第 N 名"显示）
 */
window.MysZenkov = {
  /* 状态挂在共享的 mys reactive 上（module.js 负责创建 mys，这里只初始化自己的字段） */
  init(mys) {
    mys.zenkov = {
      loading: false, done: false, error: '', raw: '', view: null,
    };
  },

  setup(ctx, mys, hooks) {
    const mysGet = (path, params) => ctx.get('' + path, params);
    const roleInfo = () => (hooks && hooks.mysRoleInfo ? hooks.mysRoleInfo() : null);

    /* 秒数 → "3 天 20 小时"（接口给的是**剩余秒数**，不是时间戳：334651 秒 ≈ 3.9 天） */
    function zkDur(sec) {
      const s = Number(sec) || 0;
      if (s <= 0) return '';
      const d = Math.floor(s / 86400);
      const h = Math.floor((s % 86400) / 3600);
      const m = Math.floor((s % 3600) / 60);
      if (d > 0) return d + ' 天' + (h ? ' ' + h + ' 小时' : '');
      if (h > 0) return h + ' 小时 ' + m + ' 分钟';
      return m + ' 分钟';
    }

    /* 进度条宽度（0~100%） */
    function zkW(cur, max) {
      const c = Number(cur) || 0;
      const m = Number(max) || 0;
      if (m <= 0) return '0%';
      return Math.min(100, Math.round((c / m) * 100)) + '%';
    }

    /* {cur,max} 对 → 渲染模型 */
    function zkPair(o, keyCur, keyMax) {
      if (!o) return null;
      const cur = Number(o[keyCur]) || 0;
      const max = Number(o[keyMax]) || 0;
      return { cur: cur, max: max, w: zkW(cur, max), full: max > 0 && cur >= max };
    }

    /* ×100 的整数 → 百分比文本（7778 → "77.78%"） */
    function zkPct(v) {
      const n = Number(v) || 0;
      return (n / 100).toFixed(2) + '%';
    }

    /* 原始返回 → 渲染模型（模板只认 view，不直接摸原始字段） */
    function zkBuild(j) {
      const d = (j && j.data) || {};
      const sd = d.season_data || {};
      const col = d.collection_data || {};
      const medal = col.medal_data || {};
      const goods = col.goods_data || {};
      const rank = Number(d.max_rank) || 0;
      const seasonId = Number(sd.cur_season_id) || 0;

      return {
        nick: d.nick_name || '',
        avatar: d.avatar_icon || '',
        total_value: d.collect_total_value || '—',
        big_red: d.big_red_num || '—',
        millions: d.millions_evacuations || '—',
        refresh: zkDur(d.refresh_time),
        duty: zkPair(d.abyss_duty, 'cur_duty', 'max_duty'),
        unlocked: !!d.abyss_unlock,
        season_unlocked: !!d.season_unlock,
        rank: rank > 0 ? (d.is_show_percent ? '前 ' + zkPct(rank) + '+' : '第 ' + rank + ' 名') : '',
        season: {
          id: seasonId,
          level: Number(sd.season_level) || 0,
          stage: Number(sd.season_stage) || 0,
          left: zkDur(sd.refresh_time),
          quest: zkPair(sd.season_quest, 'cur_quest', 'max_quest'),
          coin: zkPair(sd.season_coin, 'cur_coin', 'max_coin'),
        },
        maps: (d.map_list || []).map(function (m) {
          const lp = Number(m.leave_percent) || 0;
          return {
            id: m.map_id,
            name: m.map_name || ('幻境 ' + m.map_id),
            leave: zkPct(lp),
            w: Math.min(100, lp / 100) + '%',
            price: m.max_price || '',
            challenge: !!m.is_challenge,
            hell: !!m.hell_unlock,
            hard: !!m.hard_unlock,
          };
        }),
        medal: {
          cur: Number(medal.cur) || 0,
          total: Number(medal.total) || 0,
          list: (medal.list || []).map(function (m) {
            return { id: m.medal_id, name: m.name || '', icon: m.medal_icon || '', unlock: !!m.unlock };
          }),
        },
        goods: {
          cur: Number(goods.cur) || 0,
          total: Number(goods.total) || 0,
          list: (goods.list || []).map(function (g) {
            return {
              id: g.goods_id, name: g.name || '',
              icon: g.goods_icon || '', number: Number(g.number) || 0, unlock: !!g.unlock,
            };
          }),
        },
      };
    }

    async function mysZenkov() {
      const ri = roleInfo();
      if (!ri) { mys.zenkov.error = '请先选择角色'; return; }
      mys.zenkov.loading = true;
      mys.zenkov.error = '';
      mys.zenkov.view = null;
      try {
        const j = await mysGet('/zzz/zenkov', {
          uid: ri.uid, server: ri.server, account_id: mys.current,
        });
        if (j.ok) {
          mys.zenkov.raw = JSON.stringify(j.data, null, 2);
          mys.zenkov.view = zkBuild(j);
          mys.zenkov.done = true;
        } else mys.zenkov.error = j.message || '查询迷宫诡域失败';
      } catch (e) { mys.zenkov.error = '查询迷宫诡域失败：' + e.message; }
      mys.zenkov.loading = false;
    }

    function mysZenkovReset() {
      mys.zenkov.loading = false;
      mys.zenkov.done = false;
      mys.zenkov.error = '';
      mys.zenkov.raw = '';
      mys.zenkov.view = null;
    }

    return { mysZenkov, mysZenkovReset };
  },
};
