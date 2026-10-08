/* 群订阅 · 逻辑（米游社面板一级分类「群订阅」，2026-10-08 新增）
 * ------------------------------------------------------------------
 * 页面在 index.html 的 <template v-else>（mys.zone==='subs'）里，
 * 样式在 css/frag/subscribe.css（gs-* 前缀），后端接口在 miyoho/social/subscribe_routes.py。
 *
 * 为什么要这一页
 * --------------
 * 「订阅米哈游服务」本来只在「社交命令配置 → QQ机器人配置」里顺带列了一下，
 * 群主在群里订阅完回面板根本找不着（而且那条路的保存还会把订阅整份覆盖掉，见下）。
 * 这一页把两件本来就不同的事分开摆：
 *   ① 订阅的群   —— 哪个机器人 + 哪个群订了服务；能停用（保留记录）/ 退订（删掉）。
 *   ② 群员绑定   —— 本群发过言的人 × 他自己绑的米游社账号，一行一条账号；
 *                   能禁用（他在这个群就不再被响应）/ 删除（只清这条记录）。
 *
 * 和其它 frag 的关系
 * ------------------
 *   · 状态挂在 mys.sub 上，只有本文件的 init 会建它（module.js 里调）。
 *   · 进「群订阅」这一级分类时才懒加载一次（module.js 的 mysZoneTo → gsLoad）。
 *   · 打卡：所有写操作都回**整份最新总览**，直接覆盖本地状态 —— 页面永远显示服务端真相，
 *     不做「本地改一半再点保存」那一套（那套正是订阅被清空的老病根）。
 *
 * ⚠️ 提交给后端的 body 里**不能带 Vue Proxy**（bridge 的 postMessage 走结构化克隆，
 * Proxy 会抛 "could not be cloned"）—— 本页只发普通对象字面量（sid/gid/key/enabled），
 * 天然安全；`gsRows()` 只在页面内部用，不发出去。
 */
window.MysSubscribe = {
  init(mys) {
    mys.sub = {
      loading: false, done: false,
      error: '', message: '',
      tab: 'groups',        // groups（订阅的群）/ members（群员绑定）
      gid: '',              // 群员绑定：按群筛选（空 = 全部）
      bots: [],             // 机器人一览（在线实例 + 配置里出现过的），带各自订阅了几个群
      groups: [],           // 订阅的群（后端已摊平，一行一条）
      members: [],          // 群员绑定（一行 = 一个群员的一个米游社账号）
      seenGroups: [],       // 筛选下拉：记过足迹 / 有订阅的群
      removed: 0,           // 被「删除」但还能一键恢复的记录数
      truncated: false,     // 群里人多到被截断（后端上限 500 行）
      // 危险操作的页内二次确认（原生 window.confirm 在部分环境会被静默屏蔽）
      confirm: { show: false, kind: '', title: '', text: '', ok: '', sid: '', gid: '', key: '' },
    };
  },

  setup(ctx, mys) {
    const get = (p, q) => ctx.get('' + p, q);
    const post = (p, b) => ctx.post('' + p, b);

    /* 后端总览 → 页面状态。写操作的回包也是同一份，所以只写这一个 apply。 */
    function gsApply(j) {
      mys.sub.bots = j.bots || [];
      mys.sub.groups = j.groups || [];
      mys.sub.members = j.members || [];
      mys.sub.seenGroups = j.seen_groups || [];
      mys.sub.removed = Number(j.removed || 0);
      mys.sub.truncated = !!j.truncated;
      mys.sub.done = true;
    }

    async function gsLoad() {
      mys.sub.loading = true;
      mys.sub.error = '';
      try {
        const j = await get('/subscribe/overview');
        if (j.ok) { gsApply(j); mys.sub.message = j.message || ''; }
        else mys.sub.error = j.message || '读取群订阅失败';
      } catch (e) { mys.sub.error = '读取群订阅失败：' + e.message; }
      mys.sub.loading = false;
    }

    /* 写操作统一走这里：成功 → 用回包覆盖状态 + 提示；失败 → 显示原因，不动页面。
     * okText 是页面自己的措辞（后端也会回 message，但那是给人看的一句话，两种都行）。 */
    async function mutate(path, payload, okText) {
      mys.sub.error = '';
      mys.sub.message = '';
      mys.sub.confirm.show = false;
      try {
        const j = await post(path, payload);
        if (j.ok) { gsApply(j); mys.sub.message = okText || j.message || '已保存'; }
        else mys.sub.error = j.message || '操作失败';
      } catch (e) { mys.sub.error = '操作失败：' + e.message; }
    }

    function gsTab(name) {
      if (name !== 'groups' && name !== 'members') return;
      mys.sub.tab = name;
      mys.sub.message = '';
    }

    /* ---- ① 订阅的群 ---- */

    function gsToggleGroup(g) {
      mutate('/subscribe/group/enabled', { sid: g.sid, gid: g.gid, enabled: !g.enabled },
        g.enabled ? '已停用该群（记录保留，随时可再开）' : '已启用该群');
    }

    function gsAskGroupDrop(g) {
      mys.sub.confirm = {
        show: true, kind: 'group', ok: '确认退订',
        title: '退订「' + (g.name || ('群 ' + g.gid)) + '」？',
        text: '这条订阅会被删掉，该群不再响应米哈游功能命令（危局 / 抽卡 / 签到 …）。\n'
            + '想恢复：让群主在该群里再发一次「订阅米哈游服务」。',
        sid: g.sid, gid: g.gid, key: '',
      };
    }

    /* ---- ② 群员绑定 ---- */

    /* 同一个人在同一个群的全部行键（他可能绑了好几个米游社账号）。
     * 行是后端算出来的，前端只知道「当前这几行」——所以把这一组键一起发过去，
     * 后端按成员口径整体切，避免出现「一半禁用一半启用」（见 member_binds.set_enabled）。 */
    function gsKeysOf(m) {
      return mys.sub.members
        .filter((x) => x.gid === m.gid && x.member_id === m.member_id)
        .map((x) => x.key);
    }

    function gsToggleMember(m) {
      mutate('/subscribe/member/enabled', { keys: gsKeysOf(m), enabled: !m.enabled },
        m.enabled
          ? '已禁用：该群员在这个群发米哈游功能命令不会被响应'
          : '已启用该群员');
    }

    function gsAskMemberDrop(m) {
      mys.sub.confirm = {
        show: true, kind: 'member', ok: '确认删除',
        title: '删除这条群员绑定记录？',
        text: '群员：' + (m.member_name || m.member_id) + '\n'
            + '米游社账号：' + (m.account_name || m.account_id) + '\n\n'
            + '只清掉这条「群员 ↔ 账号」记录（他从表里消失），'
            + '米游社账号本身不会被解绑 —— 要解绑让他自己发「米游社解绑」。',
        sid: '', gid: m.gid, key: m.key,
      };
    }

    function gsConfirmDo() {
      const c = mys.sub.confirm || {};
      if (!c.show) return;
      if (c.kind === 'group') {
        mutate('/subscribe/group/drop', { sid: c.sid, gid: c.gid }, '已退订：该群不再响应功能命令');
      } else if (c.kind === 'member') {
        mutate('/subscribe/member/drop', { key: c.key }, '已删除这条记录');
      } else {
        mys.sub.confirm.show = false;
      }
    }

    function gsConfirmCancel() { mys.sub.confirm.show = false; }

    function gsRestoreAll() { mutate('/subscribe/member/restore', {}, '已恢复被删掉的记录'); }

    /* ---- 展示用小工具 ---- */

    /* 群员绑定按「群」筛选后的行（空选 = 全部）。 */
    function gsRows() {
      const gid = mys.sub.gid;
      return gid ? mys.sub.members.filter((m) => m.gid === gid) : mys.sub.members;
    }

    function gsTime(ts) { return ts ? ctx.fmt(ts) : ''; }

    function gsProto(p) { return p === 'qq_official' ? '官方QQ' : 'OneBot'; }

    // 给弹窗之类的外部调用留个入口（目前没人用，但和 social.js 的 mys.social.save 同口径）
    mys.sub.reload = gsLoad;

    return {
      gsLoad, gsTab, gsToggleGroup, gsAskGroupDrop, gsToggleMember, gsAskMemberDrop,
      gsConfirmDo, gsConfirmCancel, gsRestoreAll, gsRows, gsTime, gsProto,
    };
  },
};
