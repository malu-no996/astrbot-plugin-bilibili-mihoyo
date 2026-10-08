/* 绝区零 · 签到（plugins/_vendor/miyoushe/web/frag/zzz-sign.js）
 * ------------------------------------------------------------------
 * 模板在 frag/zzz-sign.html，样式在 frag/zzz-sign.css（sg-* 前缀）。
 * 由 module.js 在 setup 里调 window.MysSign.init/setup。
 *
 * 接口：
 *   GET  /zzz/sign?uid&server&account_id  → { ok, info:{}（累计天数/今日状态）,
 *                                             awards:[{icon,name,cnt}], month,
 *                                             total_sign_day, is_sign }
 *   POST /zzz/sign/do  body {uid,server,account_id} → { ok, message }
 * 后端走 luna 活动接口（带 DS 签名）；「签到失败」多为今日已签或该 uid 未绑定当前账号。
 */
window.MysSign = {
  init(mys) {
    mys.sign = {
      loading: false, signing: false, done: false,
      error: '', message: '',
      month: '', awards: [], info: {},
    };
  },

  setup(ctx, mys, hooks) {
    const mysGet = (path, params) => ctx.get('' + path, params);
    const mysPost = (path, body) => ctx.post('' + path, body);
    const roleInfo = () => (hooks && hooks.mysRoleInfo ? hooks.mysRoleInfo() : null);

    const PLACEHOLDER = 'data:image/svg+xml;charset=utf-8,' + encodeURIComponent(
      "<svg xmlns='http://www.w3.org/2000/svg' width='80' height='80'>"
      + "<rect width='80' height='80' fill='#f0f2f5'/>"
      + "<text x='40' y='50' font-size='28' fill='#a9a2b5' text-anchor='middle'>?</text></svg>"
    );

    function sgImgErr(e) {
      e.target.onerror = null;
      e.target.src = PLACEHOLDER;
    }

    function sgSigned() {
      return !!(mys.sign.info && mys.sign.info.is_sign);
    }

    /* 今日对应第几张奖励卡（0 基）：未签 → 就是「下一天」；已签 → 今天已领的那张 */
    function sgToday() {
      const n = Number(mys.sign.info && mys.sign.info.total_sign_day) || 0;
      return sgSigned() ? Math.max(0, n - 1) : n;
    }

    async function mysSignLoad() {
      const ri = roleInfo();
      if (!ri) { mys.sign.error = '请先选择角色'; return; }
      mys.sign.loading = true;
      mys.sign.error = '';
      try {
        const j = await mysGet('/zzz/sign', { uid: ri.uid, server: ri.server, account_id: mys.current });
        if (j.ok) {
          mys.sign.info = {
            total_sign_day: Number(j.total_sign_day) || 0,
            is_sign: !!j.is_sign,
            sign_cnt_missed: Number((j.info || {}).sign_cnt_missed) || 0,
          };
          mys.sign.awards = j.awards || [];
          mys.sign.month = j.month || '';
          mys.sign.done = true;
        } else mys.sign.error = j.message || '读取签到状态失败';
      } catch (e) { mys.sign.error = '读取签到状态失败：' + e.message; }
      mys.sign.loading = false;
    }

    async function mysSignDo() {
      const ri = roleInfo();
      if (!ri) { mys.sign.error = '请先选择角色'; return; }
      mys.sign.signing = true;
      mys.sign.error = '';
      mys.sign.message = '';
      try {
        const j = await mysPost('/zzz/sign/do', { uid: ri.uid, server: ri.server, account_id: mys.current });
        if (j.ok) {
          mys.sign.message = j.message || '签到成功';
          await mysSignLoad();      // 签完刷新状态（总天数 +1、格子打勾）
        } else mys.sign.error = j.message || '签到失败';
      } catch (e) { mys.sign.error = '签到失败：' + e.message; }
      mys.sign.signing = false;
    }

    function mysSignReset() {
      mys.sign.loading = false;
      mys.sign.signing = false;
      mys.sign.done = false;
      mys.sign.error = '';
      mys.sign.message = '';
      mys.sign.month = '';
      mys.sign.awards = [];
      mys.sign.info = {};
    }

    return { mysSignLoad, mysSignDo, mysSignReset, sgSigned, sgToday, sgImgErr };
  },
};
