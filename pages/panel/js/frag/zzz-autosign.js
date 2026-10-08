/* 绝区零 · 自动签到（plugins/_vendor/miyoushe/web/frag/zzz-autosign.js）
 * ------------------------------------------------------------------
 * 模板在 frag/zzz-autosign.html，样式在 frag/zzz-autosign.css（as-* 前缀）。
 * 由 module.js 在 setup 里调 window.MysAutoSign.init/setup 挂到 mys.as 上；
 * 签到页（frag/zzz-sign.html）顶部那条 .sg-auto-bar 用的是同一份状态。
 *
 * 接口（后端 src/autosign.py + src/autosign_routes.py）：
 *   GET  /zzz/autosign          → { ok, config:{enabled,mode,time,jitter,gap,gap_jitter,
 *                                             targets[],notify{},last_result{}}, running,
 *                                   next_label, last_label, count }
 *   GET  /zzz/autosign/targets  → { ok, accounts:[{account_id,account,logged,error,
 *                                                 roles:[{uid,server,role,region}]}] }
 *   GET  /zzz/autosign/instances→ { ok, instances:[{id,name}] } 仅 OneBot（通知用）
 *   POST /zzz/autosign          → 保存（只传表单那几项，服务端按字段合并）
 *   POST /zzz/autosign/run      → 立刻签一轮
 *
 * 为什么「一键签到」不等那个 POST 返回：目标一多就要好几分钟（每个请求至少隔 3 秒），
 * 后端把任务丢到后台、立刻回响应，这里轮询 running，结束再刷新「最近一次」。
 * 表单值和已保存值分开：轮询只更新状态与「最近一次」，**绝不回填表单**，
 * 否则用户正在改的东西会被覆盖。
 */
window.MysAutoSign = {
  init(mys) {
    mys.as = {
      show: false, loading: false, saving: false, running: false,
      tLoading: false, tError: '', iLoading: false,
      msg: '', nextLabel: '', line: '', loaded: false,
      cfg: {
        enabled: true, mode: 'auto', time: '08:30', jitter: 10,
        gap: 4, gap_jitter: 1.5, targets: [], last_result: {},
        // 签到结果私聊通知：只用 OneBot 实例（QQ 官方发不了主动私聊）
        notify: { enabled: false, self_id: '', targets: [], when: 'auto' },
      },
      targets: [],       // 「读取账号角色」拿到的账号 × 角色清单
      bots: [],          // 在线的 OneBot 实例（通知的「用哪个机器人发」）
      notifyText: '',    // 接收者 QQ，一行一个（提交时拆成数组）
      poll: 0,           // 轮询定时器 id
    };
  },

  setup(ctx, mys) {
    const API = '';
    const asGet = (p) => ctx.get(API + p);
    const asPost = (p, body) => ctx.post(API + p, body);

    const asNum = (v, d) => { const n = Number(v); return Number.isFinite(n) ? n : d; };

    /* 接收者输入框 → QQ 号数组：允许一行一个，也允许逗号/空格混着写。
       只留 5~12 位的纯数字（QQ 号），脏字符直接丢掉，别把整段原文发给后端。 */
    function asQQList(text) {
      return String(text || '').split(/[^0-9]+/)
        .filter(s => s.length >= 5 && s.length <= 12);
    }

    /* 签到页顶部那行状态文案（也是详情弹窗里的「服务端现状」） */
    function asLineFrom(j) {
      const c = j.config || {};
      const n = (c.targets || []).length;
      if (j.running) return '自动签到：正在签到…（已选 ' + n + ' 个角色）';
      if (!c.enabled) return '自动签到：已关闭（已选 ' + n + ' 个角色）';
      const head = c.mode === 'auto'
        ? '自动签到：每天 ' + (c.time || '08:30') + ' 前后（±' + asNum(c.jitter, 0) + ' 分钟）'
        : '自动签到：仅手动触发';
      return head + ' · 已选 ' + n + ' 个角色' + (j.next_label ? ' · 下次 ' + j.next_label : '');
    }

    /* soft=true：只更新「状态 + 最近一次」，不动表单（轮询用，别覆盖用户正在改的东西） */
    async function asLoad(soft) {
      mys.as.loading = true;
      try {
        const j = await asGet('/zzz/autosign');
        if (!j.ok) { mys.as.msg = j.message || '读取失败'; return; }
        mys.as.loaded = true;
        mys.as.running = !!j.running;
        mys.as.nextLabel = j.next_label || '';
        mys.as.line = asLineFrom(j);
        if (soft) mys.as.cfg.last_result = (j.config || {}).last_result || {};
        else asFill(j.config || {});
      } catch (e) {
        mys.as.msg = '读取失败：' + e.message;
      } finally {
        mys.as.loading = false;
      }
    }

    function asFill(c) {
      mys.as.cfg.enabled = c.enabled !== false;
      mys.as.cfg.mode = c.mode === 'manual' ? 'manual' : 'auto';
      mys.as.cfg.time = c.time || '08:30';
      mys.as.cfg.jitter = asNum(c.jitter, 0);
      mys.as.cfg.gap = asNum(c.gap, 4);
      mys.as.cfg.gap_jitter = asNum(c.gap_jitter, 0);
      mys.as.cfg.targets = (c.targets || []).map(x => ({
        account_id: x.account_id, account: x.account, uid: x.uid,
        server: x.server, role: x.role,
      }));
      const nf = c.notify || {};
      mys.as.cfg.notify = {
        enabled: !!nf.enabled,
        self_id: String(nf.self_id || ''),
        when: nf.when === 'always' ? 'always' : 'auto',
        targets: Array.isArray(nf.targets) ? nf.targets.map(String) : [],
      };
      mys.as.notifyText = mys.as.cfg.notify.targets.join('\n');
      mys.as.cfg.last_result = c.last_result || {};
    }

    /* 提交给后端的字段：只这几项，next_run_at / last_* 由服务端自己维护。
       ⚠️ targets 必须**深拷成普通对象**：mys.as 是 Vue reactive，直接发 Proxy 会在
       postMessage 的结构化克隆里抛
         Failed to execute 'postMessage' on 'Window': [object Object] could not be cloned.
       （现象就是点「保存」弹这条错）—— 用 map 重新造一遍字面量，别偷懒直接传。 */
    function asBody() {
      const c = mys.as.cfg;
      return {
        enabled: !!c.enabled,
        mode: c.mode === 'manual' ? 'manual' : 'auto',
        time: c.time || '08:30',
        jitter: asNum(c.jitter, 0),
        gap: asNum(c.gap, 4),
        gap_jitter: asNum(c.gap_jitter, 0),
        targets: (c.targets || []).map(t => ({
          account_id: String(t.account_id || ''),
          account: String(t.account || ''),
          uid: String(t.uid || ''),
          server: String(t.server || 'prod_gf_cn'),
          role: String(t.role || ''),
        })),
        notify: {
          enabled: !!c.notify.enabled,
          self_id: String(c.notify.self_id || ''),
          when: c.notify.when === 'always' ? 'always' : 'auto',
          targets: asQQList(mys.as.notifyText),
        },
      };
    }

    async function asOpen() {
      mys.as.show = true;
      mys.as.msg = '';
      await asLoad(false);
      if (!mys.as.targets.length) asLoadTargets();   // 清单是网络请求 → 只在打开时拉
      if (!mys.as.bots.length) asLoadInstances();    // OneBot 实例同理（通知用）
    }

    function asClose() {
      mys.as.show = false;
      mys.as.msg = '';
      // 有意**不**清轮询：正在签到就别让他关掉弹窗后看不到结果（顶部状态行还在更新）
    }

    async function asSave(quiet) {
      mys.as.saving = true;
      if (!quiet) mys.as.msg = '';
      try {
        const j = await asPost('/zzz/autosign', asBody());
        if (j.ok) {
          mys.as.running = !!j.running;
          mys.as.nextLabel = j.next_label || '';
          mys.as.line = asLineFrom(j);
          if (!quiet) mys.as.msg = '已保存（定时时刻已重新排定）';
          return true;
        }
        mys.as.msg = j.message || '保存失败';
      } catch (e) {
        mys.as.msg = '保存失败：' + e.message;
      } finally {
        mys.as.saving = false;
      }
      return false;
    }

    /* 一键签到。saveFirst=true 时先保存表单（弹窗里的「保存并签一次」），
       页面上的「一键签到」直接用已保存的配置（不碰表单）。 */
    async function asRun(saveFirst) {
      if (saveFirst && !(await asSave(true))) return;
      mys.as.msg = '';
      try {
        const j = await asPost('/zzz/autosign/run', {});
        mys.as.running = !!j.running;
        mys.as.line = asLineFrom(j);
        if (!j.ok) { mys.as.msg = j.message || '无法开始签到'; return; }
        mys.as.msg = '已开始签到，完成后这里会显示结果…';
        asPoll();
      } catch (e) {
        mys.as.msg = '触发失败：' + e.message;
      }
    }

    function asPoll() {
      clearTimeout(mys.as.poll);
      mys.as.poll = setTimeout(async () => {
        await asLoad(true);
        if (mys.as.running) asPoll();
        else mys.as.msg = '签到完成';
      }, 3000);
    }

    async function asLoadTargets() {
      mys.as.tLoading = true;
      mys.as.tError = '';
      try {
        const j = await asGet('/zzz/autosign/targets');
        if (j.ok) mys.as.targets = j.accounts || [];
        else mys.as.tError = j.message || '读取失败';
      } catch (e) {
        mys.as.tError = '读取失败：' + e.message;
      } finally {
        mys.as.tLoading = false;
      }
    }

    /* 通知里「用哪个机器人发」的候选：后端只回 OneBot 实例（官方机器人发不了私聊）。 */
    async function asLoadInstances() {
      mys.as.iLoading = true;
      try {
        const j = await asGet('/zzz/autosign/instances');
        if (j.ok) mys.as.bots = j.instances || [];
        else mys.as.msg = j.message || '实例读取失败';
      } catch (e) {
        mys.as.msg = '实例读取失败：' + e.message;
      } finally {
        mys.as.iLoading = false;
      }
    }

    function asPickedOne(aid, uid) {
      return mys.as.cfg.targets.some(t => t.account_id === aid && t.uid === uid);
    }

    function asPick(a, r, on) {
      const list = mys.as.cfg.targets;
      const i = list.findIndex(t => t.account_id === a.account_id && t.uid === r.uid);
      if (on && i < 0) {
        list.push({
          account_id: a.account_id, account: a.account, uid: r.uid,
          server: r.server || 'prod_gf_cn', role: r.role || '',
        });
      } else if (!on && i >= 0) {
        list.splice(i, 1);
      }
      mys.as.msg = '';
    }

    function asAccAll(a) {
      return a.roles.length > 0 && a.roles.every(r => asPickedOne(a.account_id, r.uid));
    }

    function asAccToggle(a, on) {
      a.roles.forEach(r => asPick(a, r, on));
    }

    function asLastText() {
      return (mys.as.cfg.last_result || {}).text || '';
    }

    return {
      asLoad, asOpen, asClose, asSave, asRun, asLoadTargets, asLoadInstances,
      asPick, asPickedOne, asAccAll, asAccToggle, asLastText,
    };
  },
};
