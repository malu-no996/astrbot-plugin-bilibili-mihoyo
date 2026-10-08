/* 社交命令配置 · 容器逻辑（plugins/_vendor/miyoushe/web/frag/social.js）
 * ------------------------------------------------------------------
 * 模板在 frag/social.html（样式 ss-* 在 frag/social.css）。
 * 由 module.js 在 setup 里调 window.MysSocial.init(mys) / setup(ctx, mys, {}).
 *
 * 为什么要有这个功能
 * ------------------
 * 原来 4 个 QQ 命令是写死在 __init__.py 里的（on_command("绝区零危局", aliases={...})），
 * 改个命令名得改 Python + 重启 bot。现在命令表、别名、绑定哪个后台接口全在页面上配，
 * 保存即时生效（后端 social.py，配置落 data/zzz/social.json）。
 *
 * 三个文件各管什么
 * ----------------
 *   social.js      ← 本文件：页签 / 读写配置 / 把「机器人 + 命令」组装成可渲染的行
 *   social-api.js  ← 接口配置子页面的方法（saAdd / saDel）
 *   social-bot.js  ← QQ 机器人子页面的方法（sbAll）
 * 状态统一挂在 mys.social 上，只有本文件的 init 会创建它，所以子页面只有 setup、没有 init。
 *
 * 数据形状（GET /social/config）
 * -----------------------------------------------
 *   commands   [{id, cmd, aliases:[], api, enabled, admin_only}]   命令表（顺序即页面顺序）
 *   interfaces [{key, label, desc}]                                可绑定的后台接口
 *   instances  [{id, name, protocol}]                              当前在线的机器人实例
 *   bots       {self_id: bool}                                     机器人总开关（默认关）
 *   bot_cmds   {self_id: {cmd_id: bool}}                           逐命令覆盖开关（缺省跟随总开关）
 *
 * 页面上的 alias_text 是「空格分隔的别名串」，只在读写配置时与 aliases 数组互转 ——
 * 这样表格里一个普通 v-model 就能编辑，不用额外挂 formatter 函数（门禁也少两个标识符）。
 */
window.MysSocial = {
  init(mys) {
    mys.social = {
      loading: false, saving: false, done: false,
      error: '', message: '',
      tab: 'api',            // api（接口配置）/ bot（QQ机器人配置）
      commands: [],          // [{id, cmd, alias_text, api, enabled, admin_only, options, tpl}]
      interfaces: [],        // [{key, label, desc, options(schema), tpl_vars, sample}]
      // 「详细设置」弹窗的编辑态（每条命令点开时把它的 options/tpl 拷进来改，确定再写回）
      detail: {
        show: false, index: -1, id: '', cmd: '', api: '', apiLabel: '',
        schema: [], options: {}, tpl: { onebot: '', qq: '' },
        varsDoc: [], sample: '', proto: 'onebot', arg: '',
        user: '',              // 预览身份（QQ 号 / openid，留空 = 后端自动挑一个已绑定用户）
        preview: '', previewErr: '', previewing: false, previewEmpty: false,
        previewUser: '',       // 上次预览实际用的身份（后端回填）
        previewVarsJson: '',   // 上次预览的模板变量 / 原始数据（JSON 文本，页面直接展示）
        previewDataJson: '',
      },
      instances: [],         // [{id, name, protocol}] 在线实例
      bots: {},              // {self_id: bool} 后端原样
      botCmds: {},           // {self_id: {cmd_id: bool}} 后端原样
      botRows: [],           // 渲染用：机器人行（含逐命令开关的当前值）
    };
  },

  setup(ctx, mys) {
    const mysGet = (path, params) => ctx.get('' + path, params);
    const mysPost = (path, body) => ctx.post('' + path, body);

    /* 一个机器人一行。cmds 里的 val 是「当前生效值」：
     * 后端没单独设过就跟随总开关，设过就用它的值。 */
    function makeRow(id, name, protocol, online) {
      const master = !!(mys.social.bots || {})[id];
      const per = (mys.social.botCmds || {})[id] || {};
      return {
        id, name, protocol, online, on: master,
        cmds: (mys.social.commands || []).map(c => ({
          id: c.id,
          label: c.cmd || '（未命名）',
          val: Object.prototype.hasOwnProperty.call(per, c.id) ? !!per[c.id] : master,
        })),
      };
    }

    /* 在线实例 + 「配置里有、此刻不在线」的机器人，一起列出来（离线的也能先配好）。
     * 命令增删后也要重跑一次（新命令要出现在矩阵里），见 mysSocialRows。 */
    function rebuildRows() {
      const seen = {};
      const rows = [];
      for (const b of mys.social.instances || []) {
        const id = String(b.id || '');
        if (!id || seen[id]) continue;
        seen[id] = 1;
        rows.push(makeRow(id, b.name || '', b.protocol || 'onebot', true));
      }
      for (const id of Object.keys(mys.social.bots || {})) {
        if (seen[id]) continue;
        seen[id] = 1;
        rows.push(makeRow(id, '', 'onebot', false));
      }
      mys.social.botRows = rows;
    }

    /* 后端配置 → 页面状态。保存成功后也用同一份，所以页面显示的永远是「服务端的真相」。 */
    function applyData(j) {
      mys.social.commands = (j.commands || []).map(c => ({
        id: String(c.id || ''),
        cmd: String(c.cmd || ''),
        alias_text: (c.aliases || []).join(' '),
        api: String(c.api || ''),
        enabled: c.enabled !== false,
        admin_only: !!c.admin_only,
        // 详细设置：接口选项（按 schema 的键）+ 两套回复模板
        options: Object.assign({}, c.options || {}),
        tpl: {
          onebot: String(((c.tpl || {}).onebot) || ''),
          qq: String(((c.tpl || {}).qq) || ''),
        },
      }));
      mys.social.interfaces = j.interfaces || [];
      mys.social.instances = j.instances || [];
      mys.social.bots = j.bots || {};
      mys.social.botCmds = j.bot_cmds || {};
      rebuildRows();
    }

    /* 页面状态 → 后端配置。逐命令开关**全量写出**（不用「缺省」形态）：
     * 后端 bot_allows 先看总开关，总开关关着怎么写都执行不了，所以显式写出不会跑偏。 */
    function collect() {
      const commands = (mys.social.commands || []).map(c => ({
        id: c.id,
        cmd: String(c.cmd || '').trim(),
        aliases: String(c.alias_text || '').split(/[\s,，、;；]+/).filter(Boolean),
        api: String(c.api || ''),
        enabled: !!c.enabled,
        admin_only: !!c.admin_only,
        options: Object.assign({}, c.options || {}),
        tpl: {
          onebot: String(((c.tpl || {}).onebot) || ''),
          qq: String(((c.tpl || {}).qq) || ''),
        },
      }));
      const bots = {};
      const bot_cmds = {};
      for (const r of mys.social.botRows || []) {
        bots[r.id] = !!r.on;
        const per = {};
        for (const c of r.cmds || []) per[c.id] = !!c.val;
        bot_cmds[r.id] = per;
      }
      return { commands, bots, bot_cmds };
    }

    async function mysSocialLoad() {
      mys.social.loading = true;
      mys.social.error = '';
      try {
        const j = await mysGet('/social/config');
        if (j.ok) { applyData(j); mys.social.done = true; }
        else mys.social.error = j.message || '读取社交命令配置失败';
      } catch (e) { mys.social.error = '读取社交命令配置失败：' + e.message; }
      mys.social.loading = false;
    }

    async function mysSocialSave() {
      mys.social.saving = true;
      mys.social.error = '';
      mys.social.message = '';
      try {
        const j = await mysPost('/social/config', collect());
        if (j.ok) {
          applyData(j);
          mys.social.done = true;
          mys.social.message = '已保存，立即生效（不用重启机器人）';
        } else mys.social.error = j.message || '保存失败';
      } catch (e) { mys.social.error = '保存失败：' + e.message; }
      mys.social.saving = false;
    }

    function mysSocialTab(name) {
      if (name !== 'api' && name !== 'bot') return;
      mys.social.tab = name;
      if (!mys.social.done) mysSocialLoad();
    }

    /* 「详细设置」弹窗的「确定」要能**直接落盘**。
     * 原来是「确定」只写回表格、还得再点页面右上角「保存」—— 少点一步就白配了，
     * 表现是「明明选了图片消息，发出来还是文字」（2026-10-01 踩过）。
     * 把保存函数挂到状态上给弹窗调用（frag/social-detail.js）。 */
    mys.social.save = mysSocialSave;

    return {
      mysSocialLoad, mysSocialSave, mysSocialTab,
      // 给子页面用（hooks）：命令增删后重建机器人矩阵的行
      mysSocialRows: rebuildRows,
    };
  },
};
