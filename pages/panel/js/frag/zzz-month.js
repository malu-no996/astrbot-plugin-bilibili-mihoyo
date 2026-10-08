/* 绝区零 · 绳网月报（plugins/_vendor/miyoushe/web/frag/zzz-month.js）
 * 查询 + 数据归一化；模板在 frag/zzz-month.html，样式在 frag/zzz-month.css（mo-* 前缀）。
 * 由 module.js 在 setup 里调 window.MysMonth.init/setup。
 *
 * 后端 /zzz/month 返回的原始字段：
 *   data_month（"202610"）/ current_month / role_info{nickname}
 *   month_data { list[{data_name, count}], income_components[{action, num, percent}] }
 *   optional_month（可查月份列表，["202609","202610",...]）
 */
window.MysMonth = {
  init(mys) {
    mys.month = {
      loading: false, done: false, error: '', raw: '', view: null, loaded: false,
      pick: '', months: [],
    };
  },

  setup(ctx, mys, hooks) {
    const mysGet = (path, params) => ctx.get('' + path, params);
    const roleInfo = () => (hooks && hooks.mysRoleInfo ? hooks.mysRoleInfo() : null);

    const ACTION_NAMES = {
      daily_activity_rewards: '日常活跃奖励',
      mail_rewards: '邮件奖励',
      growth_rewards: '成长奖励',
      event_rewards: '活动奖励',
      hollow_rewards: '零号空洞奖励',
      shiyu_rewards: '式舆防卫战奖励',
      other_rewards: '其他奖励',
    };

    /* "202610" → "2026-10" */
    function moPretty(m) {
      const s = String(m || '');
      return s.length === 6 ? (s.slice(0, 4) + '-' + s.slice(4, 6)) : s;
    }

    function moBuild(j) {
      const d = j.data || {};
      const md = d.month_data || {};
      const role = d.role_info || {};
      return {
        month: moPretty(d.data_month),
        nick: String(role.nickname || ''),
        items: (md.list || []).map((it) => ({
          name: String(it.data_name || ''),
          count: Number(it.count) || 0,
        })),
        incomes: (md.income_components || []).map((c) => {
          const action = String(c.action || '');
          return {
            action: action,
            name: ACTION_NAMES[action] || action,
            num: Number(c.num) || 0,
            percent: Number(c.percent) || 0,
          };
        }),
      };
    }

    async function mysMonth(month) {
      const ri = roleInfo();
      if (!ri) { mys.month.error = '请先选择角色'; return; }
      mys.month.loading = true;
      mys.month.error = '';
      mys.month.view = null;
      try {
        const j = await mysGet('/zzz/month', {
          uid: ri.uid, server: ri.server, month: month || '', account_id: mys.current,
        });
        if (j.ok) {
          mys.month.raw = JSON.stringify(j.data, null, 2);
          mys.month.view = moBuild(j);
          // 把可查月份填进下拉（首次），并默认选中本次实际查到的月份
          if (Array.isArray(j.data && j.data.optional_month)) {
            mys.month.months = j.data.optional_month.map(String);
          }
          if (j.data && j.data.data_month) mys.month.pick = String(j.data.data_month);
          mys.month.done = true;
        } else mys.month.error = j.message || '查询绳网月报失败';
      } catch (e) { mys.month.error = '查询绳网月报失败：' + e.message; }
      mys.month.loaded = true;
      mys.month.loading = false;
    }

    function mysMonthReset() {
      mys.month.loading = false;
      mys.month.done = false;
      mys.month.error = '';
      mys.month.raw = '';
      mys.month.view = null;
      mys.month.loaded = false;
      mys.month.pick = '';
      mys.month.months = [];
    }

    return { mysMonth, mysMonthReset, moPretty };
  },
};
