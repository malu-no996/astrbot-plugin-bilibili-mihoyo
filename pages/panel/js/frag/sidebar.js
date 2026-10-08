/* 米游社账号侧边栏 · 独立逻辑（plugins/_vendor/miyoushe/web/frag/sidebar.js）
 * ------------------------------------------------------------------
 * 账号列表 / 扫码登录 / 切换 / 删除 / 清空，以及**每个账号下的绝区零角色二级菜单**。
 * 本文件不注册 AdminApp 模块（页签仍由 module.js 注册），而是暴露
 * window.MysSidebar.setup(ctx, hooks)，由 module.js 在自己的 setup 里调用：
 *   - 状态共用 module.js 先 expose 的同一份 reactive `mys`（ctx.shared.mys）；
 *   - hooks 提供 main 区的回调（mysResetQueries / mysPickRole / mysHasRole）。
 * 页面片段在同目录 sidebar.html，样式在 sidebar.css。
 *
 * 角色数据分两层：
 *   mys.rolesByAccount[aid] = { loading, error, list }   ← 侧边栏按账号分组的缓存（二级菜单用）
 *   mys.roles.list + mys.role                            ← 当前账号的角色，主区各查询统一读这一份
 * 每次某个账号的角色拉回来，只要它是当前账号，就同步进 mys.roles.list。
 */
(function () {
  'use strict';

  window.MysSidebar = {
    setup(ctx, hooks) {
      hooks = hooks || {};
      var mys = ctx.shared && ctx.shared.mys;
      if (!mys) {
        console.error('[MysSidebar] ctx.shared.mys 不存在：module.js 必须先 expose 状态再调用本 setup');
        return {};
      }

      var API = '';
      var mysGet = (path, params) => ctx.get(API + path, params);
      var mysPostJson = (path, body) => ctx.post(API + path, body);

      var mysTimer = null;

      /* ---------------- 账号 ---------------- */

      /* forceRoles=true 时连角色列表一起重拉（侧边栏「刷新」按钮）；
       * 进页签时用默认值，只补还没拉过的账号，避免每次切页签都打一遍接口。 */
      async function mysRefresh(forceRoles) {
        mys.state.loading = true; mys.state.error = '';
        try {
          const j = await mysGet('/state');
          if (j.ok) {
            mys.accounts = j.accounts || [];
            mys.current = j.current || '';
            mys.state.qr_ready = !!j.qr_ready;
            mys.state.count = j.count || 0;
            if (!mys.current && mys.accounts.length) mys.current = mys.accounts[0].account_id;
          } else mys.state.error = j.message || '读取米游社状态失败';
          // 账号变了就顺手把各账号的角色（二级菜单）也补上
          await mysRolesLoadAll(!!forceRoles);
        } catch (e) {
          mys.state.error = mys.state.error || ('刷新失败：' + e.message);
        } finally {
          mys.state.loading = false;
        }
      }

      /* ---------------- 角色（侧边栏二级菜单） ---------------- */

      function mysRolesSlot(aid) {
        if (!mys.rolesByAccount[aid]) {
          mys.rolesByAccount[aid] = { loading: false, error: '', list: [] };
        }
        return mys.rolesByAccount[aid];
      }

      function mysRolesState(aid) {
        return mys.rolesByAccount[aid] || { loading: false, error: '', list: [] };
      }

      /* 角色二级菜单**默认展开**（用户要求：默认张开），点标题行才折叠。
       * mys.rolesOpen[aid] 只记「用户显式点过的」状态，undefined = 还没动过 → 视为展开。 */
      function mysRolesOpen(aid) {
        if (mys.rolesOpen[aid] === undefined) return true;
        return !!mys.rolesOpen[aid];
      }

      function mysToggleRoles(aid) {
        mys.rolesOpen[aid] = !mysRolesOpen(aid);
      }

      /* 当前账号的角色 → 主区统一的数据源（mys.roles.list + mys.role） */
      function mysSyncCurrentRoles(aid) {
        if (aid !== mys.current) return;
        const slot = mysRolesSlot(aid);
        mys.roles.error = slot.error || '';
        mys.roles.list = slot.list;
        if (hooks.mysResetQueries) hooks.mysResetQueries();
        if (slot.list.length) {
          const valid = hooks.mysHasRole && hooks.mysHasRole();
          if (!valid || Number(mys.role) >= slot.list.length) mys.role = 0;
        } else mys.role = '';
      }

      async function mysRolesLoadOne(aid) {
        const slot = mysRolesSlot(aid);
        if (slot.loading) return;
        slot.loading = true; slot.error = '';
        if (aid === mys.current) { mys.roles.loading = true; mys.roles.error = ''; }
        try {
          const j = await mysGet('/zzz/roles', { account_id: aid });
          if (j.ok) slot.list = j.roles || [];
          else slot.error = j.message || '读取角色失败';
        } catch (e) { slot.error = '读取角色失败：' + e.message; }
        slot.loading = false;
        if (aid === mys.current) mys.roles.loading = false;
        mysSyncCurrentRoles(aid);
      }

      /* force=false：只补还没拉过的账号（进页签时用，避免每次重复请求） */
      async function mysRolesLoadAll(force) {
        if (!mys.accounts.length) return;
        await Promise.all(mys.accounts.map(function (a) {
          const slot = mysRolesSlot(a.account_id);
          if (!force && (slot.list.length || slot.loading)) return Promise.resolve();
          return mysRolesLoadOne(a.account_id);
        }));
      }

      /* 单个账号的角色重拉（二级菜单上的小刷新按钮） */
      function mysRolesReload(aid) {
        return mysRolesLoadOne(aid);
      }

      function mysRoleActive(aid, uid) {
        if (mys.current !== aid) return false;
        const r = mys.roles.list[Number(mys.role)];
        return !!r && String(r.game_uid) === String(uid);
      }

      /* 点侧边栏里的角色：必要时先切账号，再把该角色设为当前查询目标 */
      async function mysPickRoleIn(aid, uid) {
        if (mys.current !== aid) await mysSelect(aid);
        const slot = mysRolesSlot(aid);
        if (!slot.list.length && !slot.loading) await mysRolesLoadOne(aid);
        const list = slot.list || [];
        const idx = list.findIndex(function (r) { return String(r.game_uid) === String(uid); });
        if (idx < 0) { ctx.notice('列表里找不到该角色，点「刷新」重试', 'err'); return; }
        mys.roles.list = list;
        if (hooks.mysPickRole) hooks.mysPickRole(idx);
      }

      /* ---------------- 登录 ---------------- */

      // mode: 'hyp' 一次扫码拿全（默认：stoken + cookie_token）｜ 'web' 兜底（不含 stoken）
      async function mysStartLogin(mode) {
        mode = mode === 'web' ? 'web' : 'hyp';
        const isFallback = mode === 'web';
        mys.login.busy = true;
        mys.login.show = true;
        mys.login.tip = '正在获取二维码…';
        mys.login.png = ''; mys.login.url = ''; mys.login.ticket = '';
        try {
          const j = await mysPostJson('/login/start', { mode: mode });
          if (!j.ok) { mys.login.busy = false; mys.login.tip = j.message || '获取二维码失败'; ctx.notice(mys.login.tip, 'err'); return; }
          mys.login.ticket = j.ticket;
          mys.login.url = j.url;
          mys.login.tip = isFallback
            ? '请用「米游社」App 扫码（' + (j.expires_in || 180)
              + ' 秒内有效；本次不含抽卡凭证，登录后可能还要再授权一次）'
            : '请用「米游社」App 扫码（' + (j.expires_in || 180) + ' 秒内有效，一次拿全凭证）';
          // AstrBot Page 的 <img> 带不上鉴权头：login/start 直接下发 base64 data URI
          mys.login.png = j.png || '';
          if (!mys.login.png) mys.login.tip = '二维码渲染失败，请用下方链接登录';
        } catch (e) { mys.login.tip = '获取二维码失败：' + e.message; ctx.notice(mys.login.tip, 'err'); mys.login.busy = false; return; }
        mys.login.busy = false;
        clearInterval(mysTimer);
        mysTimer = setInterval(async () => {
          try {
            const p = await mysGet('/login/poll', { t: mys.login.ticket });
            if (p.status === 'success') {
              clearInterval(mysTimer);
              mysLoginCancel(true);
              ctx.notice(p.message || '成功', 'ok');
              if (p.account_id) mys.current = p.account_id;
              await mysRefresh(true);
            } else if (p.status === 'scanned') { mys.login.tip = '已扫码，请在手机上确认'; }
            else if (p.status === 'waiting') { mys.login.tip = '等待扫码…'; }
            else { clearInterval(mysTimer); mys.login.tip = p.message || '二维码已失效，请重新获取'; ctx.notice(mys.login.tip, 'err'); }
          } catch (e) { /* 轮询中断不影响页面 */ }
        }, 2000);
      }

      // 主登录：一次扫码拿全（stoken + cookie_token + 等价键名）
      function mysLoginStart() { return mysStartLogin('hyp'); }
      // 兜底：账号有登录凭证但缺 stoken 时才用（HYP 接口不可用回退到 web 版的情况）
      function mysAuthStoken() { return mysStartLogin('hyp'); }

      function mysLoginCancel(silent) {
        clearInterval(mysTimer);
        if (mys.login.png && mys.login.png.startsWith('blob:')) URL.revokeObjectURL(mys.login.png);
        mys.login.show = false; mys.login.png = ''; mys.login.url = '';
        mys.login.ticket = ''; mys.login.tip = ''; mys.login.busy = false;
      }

      async function mysSelect(aid) {
        const j = await mysPostJson('/accounts/select', { account_id: aid });
        if (!j.ok) { ctx.notice(j.message || '切换账号失败', 'err'); return; }
        mys.current = j.current || aid;
        mys.accounts = j.accounts || mys.accounts;
        // 换账号后角色/查询结果都失效
        mys.roles.list = []; mys.roles.error = ''; mys.role = '';
        mys.rolesOpen[aid] = true;
        if (hooks.mysResetQueries) hooks.mysResetQueries();
        // 已有缓存就直接同步，否则拉一次
        if (mysRolesSlot(aid).list.length) mysSyncCurrentRoles(aid);
        else await mysRolesLoadOne(aid);
      }

      /* ---------------- 危险操作的二次确认 ---------------- */

      /* 删除账号 / 清空全部都走页内弹窗，**不用 window.confirm**：
       * 原生弹窗在部分环境（内嵌页面、被浏览器「不再提示此页面」勾过）会被静默屏蔽，
       * 用户根本看不到提示就删了；页内弹窗则一定看得见，并且要点两次。 */

      function mysAskDel(aid) {
        const a = mys.accounts.find(x => x.account_id === aid) || {};
        mys.confirm = {
          show: true, kind: 'del', aid: aid,
          title: '删除米游社账号「' + (a.nickname || aid) + '」？',
          ok: '确认删除',
        };
      }

      function mysAskClear() {
        mys.confirm = {
          show: true, kind: 'clear', aid: '',
          title: '清空本机保存的全部 ' + mys.accounts.length + ' 个米游社账号？',
          ok: '确认清空',
        };
      }

      function mysConfirmCancel() { mys.confirm.show = false; }

      /* 二次确认弹窗里点「确认」→ 才真正执行 */
      async function mysConfirmDo() {
        const kind = mys.confirm.kind, aid = mys.confirm.aid;
        mys.confirm.show = false;
        if (kind === 'del') await mysDelete(aid);
        else if (kind === 'clear') await mysLogout();
      }

      /* 真正删一个账号（已在上一步确认过，这里不再弹问） */
      async function mysDelete(aid) {
        const j = await mysPostJson('/accounts/delete', { account_id: aid });
        ctx.notice(j.message, j.ok ? 'ok' : 'err');
        if (j.ok) {
          mys.accounts = j.accounts || [];
          mys.current = j.current || '';
          delete mys.rolesByAccount[aid];
          delete mys.rolesOpen[aid];
          if (!mys.current) {
            mys.roles.list = []; mys.role = '';
            if (hooks.mysResetQueries) hooks.mysResetQueries();
          } else if (mysRolesSlot(mys.current).list.length) {
            mysSyncCurrentRoles(mys.current);
          } else {
            await mysRolesLoadOne(mys.current);
          }
        }
      }

      /* 真正清空全部（同样已确认过） */
      async function mysLogout() {
        const j = await mysPostJson('/logout', {});
        ctx.notice(j.message, j.ok ? 'ok' : 'err');
        if (j.ok) {
          mys.accounts = []; mys.current = ''; mys.roles.list = []; mys.role = '';
          mys.rolesByAccount = {}; mys.rolesOpen = {};
          if (hooks.mysResetQueries) hooks.mysResetQueries();
        }
      }

      // 模板绑定用（core 会把 expose 的方法合并进 Vue 实例）
      ctx.expose({}, {
        mysRefresh, mysLoginStart, mysAuthStoken, mysLoginCancel, mysSelect,
        mysAskDel, mysAskClear, mysConfirmCancel, mysConfirmDo,
        mysRolesState, mysRolesOpen, mysToggleRoles, mysPickRoleIn, mysRoleActive,
        mysRolesLoadAll, mysRolesReload,
      });

      // 返回给 module.js 内部复用（如 mysTab 里刷新账号状态、刷新角色）
      return {
        mysRefresh, mysLoginStart, mysAuthStoken, mysLoginCancel, mysSelect, mysDelete, mysLogout,
        mysRolesLoadAll, mysRolesLoadOne,
      };
    },
  };
})();
