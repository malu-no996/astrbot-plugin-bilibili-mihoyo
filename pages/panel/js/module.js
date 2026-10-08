/* 米游社模块 · 前端逻辑（plugins/_vendor/miyoushe/web/module.js）
 * ------------------------------------------------------------------
 * 本文件只管「主内容区」：当前账号 + 绝区零（角色选择 / 抽卡 / 危局 / 防卫战 /
 * 角色查询 / 图鉴 / 签到 / 自动签到），以及一级分类里的「社交命令配置」。各功能都是 frag/
 * 下的独立 html/js/css 三件套。
 * 「米游社账号」侧边栏的逻辑在 frag/sidebar.js（window.MysSidebar，独立文件），
 * 页面骨架在 partial.html，其余片段在 frag/*.html，由 config_web 服务端拼装。
 */
(function () {
  'use strict';

  var { reactive } = Vue;

  AdminApp.register({
    id: 'mys',
    label: '米游社',

    setup(ctx) {
      var API = '';

      // 唯一一份共享状态：侧边栏（sidebar.js）也通过 ctx.shared.mys 操作它
      const mys = reactive({
        state: { loading: false, error: '', qr_ready: true, current: '', count: 0 },
        accounts: [],
        current: '',
        login: { show: false, busy: false, ticket: '', url: '', png: '', tip: '' },
        roles: { loading: false, list: [], error: '' },
        role: '',
        // 侧边栏的「角色二级菜单」：按账号分组缓存 + 展开状态
        // （当前账号这一份会同步进上面的 mys.roles.list，主区各查询只认那一份）
        // rolesOpen[aid] 只为「用户显式点过折叠/展开」的账号存值，undefined = 默认展开
        rolesByAccount: {},
        rolesOpen: {},
        // 危险操作（删除账号 / 清空全部）的页内二次确认弹窗，见 frag/sidebar.js
        confirm: { show: false, kind: '', aid: '', title: '', ok: '' },
        // 一级分类：绝区零（依赖选中账号）/ 社交命令配置（全局配置，与账号无关）
        zone: 'zzz',
        tab: 'gacha',
        gacha: {
          loading: false, error: '', data: null,
          elapsed: 0,         // 同步已用秒数（全量可能几分钟，给用户个进度感）
          tab: 'summary',      // 总结子页签：summary / cards / recent / versions
          pool: '2',           // 抽卡总结里当前展开的频段
        },
        // 危局（mys.deadly）/ 防卫战（mys.shiyu）的状态在 frag/zzz-deadly.js、frag/zzz-shiyu.js 里 init
        // 社交命令配置（mys.social）的状态在 frag/social.js 里 init
      });

      // 危局 / 防卫战 / 角色查询 / 图鉴 / 签到：状态与查询逻辑各自独立成文件
      // （每个功能都是 html/js/css 三件套，全在 frag/ 下，装配器自动挂载）
      if (window.MysDeadly) window.MysDeadly.init(mys);
      if (window.MysShiyu) window.MysShiyu.init(mys);
      if (window.MysAbyss) window.MysAbyss.init(mys);
      if (window.MysZenkov) window.MysZenkov.init(mys);
      if (window.MysRoles) window.MysRoles.init(mys);
      // 角色详情浮层（frag/zzz-avatar.js）：入口在角色查询页的卡片上，状态挂 mys.avatar
      if (window.MysAvatar) window.MysAvatar.init(mys);
      if (window.MysNote) window.MysNote.init(mys);
      if (window.MysVoid) window.MysVoid.init(mys);
      if (window.MysProfile) window.MysProfile.init(mys);
      if (window.MysMonth) window.MysMonth.init(mys);
      if (window.MysCodex) window.MysCodex.init(mys);
      if (window.MysSign) window.MysSign.init(mys);
      // 自动签到（签到页的配置浮层 + 一键签到）：状态挂在 mys.as 上
      if (window.MysAutoSign) window.MysAutoSign.init(mys);
      // 社交命令配置：容器（social.js）建状态，两个子页面各自出方法
      if (window.MysSocial) window.MysSocial.init(mys);
      // 设备指纹配置（frag/device.js）：解开角色列表 10041 风控那两步，状态挂 mys.device
      if (window.MysDevice) window.MysDevice.init(mys);

      // 先交状态（sidebar.js 靠 ctx.shared.mys 拿同一份引用）
      ctx.expose({ mys }, {});

      // 账号侧边栏：独立文件 frag/sidebar.js
      // （角色列表现在挂在侧边栏账号下面做二级菜单，加载/刷新逻辑全归它管）
      const sidebar = (window.MysSidebar && window.MysSidebar.setup(ctx, {
        mysResetQueries: () => mysResetQueries(),
        mysPickRole: (i) => mysPickRole(i),
        mysHasRole: () => mysHasRole(),
      })) || {};

      // 各功能查询与渲染逻辑（frag/zzz-deadly.js、zzz-shiyu.js、zzz-roles.js、zzz-codex.js、zzz-sign.js）
      const deadlyApi = (window.MysDeadly && window.MysDeadly.setup(ctx, mys, { mysRoleInfo })) || {};
      const shiyuApi = (window.MysShiyu && window.MysShiyu.setup(ctx, mys, { mysRoleInfo })) || {};
      const abyssApi = (window.MysAbyss && window.MysAbyss.setup(ctx, mys, { mysRoleInfo })) || {};
      const zenkovApi = (window.MysZenkov && window.MysZenkov.setup(ctx, mys, { mysRoleInfo })) || {};
      const rolesApi = (window.MysRoles && window.MysRoles.setup(ctx, mys, { mysRoleInfo })) || {};
      const avatarApi = (window.MysAvatar && window.MysAvatar.setup(ctx, mys, { mysRoleInfo })) || {};
      const noteApi = (window.MysNote && window.MysNote.setup(ctx, mys, { mysRoleInfo })) || {};
      const voidApi = (window.MysVoid && window.MysVoid.setup(ctx, mys, { mysRoleInfo })) || {};
      const profileApi = (window.MysProfile && window.MysProfile.setup(ctx, mys, { mysRoleInfo })) || {};
      const monthApi = (window.MysMonth && window.MysMonth.setup(ctx, mys, { mysRoleInfo })) || {};
      const codexApi = (window.MysCodex && window.MysCodex.setup(ctx, mys, { mysRoleInfo })) || {};
      const signApi = (window.MysSign && window.MysSign.setup(ctx, mys, { mysRoleInfo })) || {};
      const autosignApi = (window.MysAutoSign && window.MysAutoSign.setup(ctx, mys)) || {};

      // 社交命令配置：容器负责页签 / 读写配置 / 组装机器人行；
      // 两个子页面各自出「增删命令」「批量开关」这类纯 UI 方法。
      // （状态都挂在 mys.social 上，由上面的 init 建立，所以子页面只有 setup、没有 init。）
      const socialApi = (window.MysSocial && window.MysSocial.setup(ctx, mys)) || {};
      const socialApiPage = (window.MysSocialApi && window.MysSocialApi.setup(ctx, mys, {
        // 命令增删后要重建机器人矩阵的行（新命令得出现在开关列表里）
        socialRows: () => { if (socialApi.mysSocialRows) socialApi.mysSocialRows(); },
      })) || {};
      const socialBotPage = (window.MysSocialBot && window.MysSocialBot.setup(ctx, mys)) || {};
      // 「详细设置」弹窗：按接口 schema 渲染表单 + 两套回复模板（frag/social-detail.js）
      const socialDetail = (window.MysSocialDetail && window.MysSocialDetail.setup(ctx, mys)) || {};
      // 设备指纹配置：状态在 mys.device（上面的 init 建好），所以这里只有 setup
      const devicePage = (window.MysDevice && window.MysDevice.setup(ctx, mys)) || {};

      const mysGet = (path, params) => ctx.get(API + path, params);

      function mysCurrent() {
        return mys.accounts.find(a => a.account_id === mys.current) || {};
      }

      function mysResetQueries() {
        // 抽卡数据按 uid 落盘（data/zzz/{uid}.csv），换角色 = 换 uid，旧汇总一并清掉
        mys.gacha.data = null; mys.gacha.error = ''; mys.gacha.pool = '2'; mys.gacha.elapsed = 0;
        // 危局 / 防卫战 / 角色查询 / 签到的重置逻辑在各自独立文件里
        // （图鉴是静态数据、与角色无关，不参与重置）
        if (deadlyApi.mysDeadlyReset) deadlyApi.mysDeadlyReset();
        if (shiyuApi.mysShiyuReset) shiyuApi.mysShiyuReset();
        if (abyssApi.mysAbyssReset) abyssApi.mysAbyssReset();
        if (zenkovApi.mysZenkovReset) zenkovApi.mysZenkovReset();
        if (rolesApi.mysAvatarsReset) rolesApi.mysAvatarsReset();
        // 换角色 = 换 uid，之前那个角色的详情就作废了（否则会串号显示上一个角色的音擎）
        if (avatarApi.avReset) avatarApi.avReset();
        if (noteApi.mysNoteReset) noteApi.mysNoteReset();
        if (voidApi.mysVoidReset) voidApi.mysVoidReset();
        if (profileApi.mysProfileReset) profileApi.mysProfileReset();
        if (monthApi.mysMonthReset) monthApi.mysMonthReset();
        if (signApi.mysSignReset) signApi.mysSignReset();
      }

      /* 一级分类切换：绝区零 / 社交命令配置 / 设备配置（纯本地状态，与登录账号无关）。
       * 两个配置页的数据都是第一次进来才拉 —— 没点过去就不发这个请求。 */
      function mysZoneTo(name) {
        if (name !== 'zzz' && name !== 'social' && name !== 'device') return;
        mys.zone = name;
        if (name === 'social' && !mys.social.done && socialApi.mysSocialLoad) {
          socialApi.mysSocialLoad();
        }
        if (name === 'device' && !mys.device.done && devicePage.dvLoad) {
          devicePage.dvLoad();
        }
      }

      /* 二级页签切换：图鉴 / 签到首次进入才懒加载（图鉴取本地快照，签到拉状态+奖励表）。
       * 切走时顺手关掉图鉴的详情浮层，免得留在状态里下次进来还开着。 */
      function mysTabTo(name) {
        mys.tab = name;
        if (codexApi.codexClose) codexApi.codexClose();
        // 离开「角色查询」时顺手收起详情浮层（浮层挂在页面外层，不关会一直盖着）
        if (name !== 'roles' && avatarApi.avClose) avatarApi.avClose();
        if (name === 'codex' && codexApi.mysCodex) codexApi.mysCodex(false);
        if (name === 'sign' && signApi.mysSignLoad && !mys.sign.done) signApi.mysSignLoad();
        // 自动签到的状态行（每天定时 / 已选几个角色）也挂在签到页上，进来就刷一次
        if (name === 'sign' && autosignApi.asLoad) autosignApi.asLoad(true);
        // 新增的五个页签：首次进入懒加载（避免一进绝区零就打五个接口）
        if (name === 'note' && !mys.note.loaded && noteApi.mysNote) noteApi.mysNote();
        if (name === 'void' && !mys.void.loaded && voidApi.mysVoid) voidApi.mysVoid();
        if (name === 'profile' && !mys.profile.loaded && profileApi.mysProfile) profileApi.mysProfile();
        if (name === 'month' && !mys.month.loaded && monthApi.mysMonth) monthApi.mysMonth(mys.month.pick);
      }

      /* 角色列表已移到侧边栏（每个账号下面一份二级菜单），加载/刷新逻辑全在 frag/sidebar.js，
       * 这里只负责读当前选中的那一个角色。 */

      function mysRoleInfo() {
        const idx = Number(mys.role);
        const r = mys.roles.list[idx];
        // nickname 是给角色详情浮层顶栏「名字 + UID」用的（/zzz/roles 本来就下发）。
        return r ? {
          uid: r.game_uid, server: r.region || 'prod_gf_cn', nickname: r.nickname || '',
        } : null;
      }

      /* 当前查询角色名（主区标题上显示，让用户知道现在查的是哪个） */
      function mysRoleName() {
        const r = mys.roles.list[Number(mys.role)];
        if (!r) return '';
        return r.nickname || ('UID ' + r.game_uid);
      }

      /* 是否已选中角色。
       * 注意：mys.role 存的是 roles.list 的**下标**，0 是合法值，
       * 所以绝不能用 `!mys.role` / `v-if="mys.role"` 判断，会把第 1 个角色当成未选。 */
      function mysHasRole() {
        return mys.role !== '' && mys.role !== null && mys.role !== undefined;
      }

      /* 绝区零查询区是否可用：有没有选中角色（下标 0 合法，见 mysHasRole）。 */
      function mysZoneOpen() {
        return mysHasRole();
      }

      // 切换查询目标（由侧边栏的角色二级菜单调用；换角色后旧的查询结果作废）
      function mysPickRole(i) {
        if (mys.role === i) return;
        mys.role = i;
        mysResetQueries();
      }

      /* 计时器：全量同步是长任务（几百页 × 0.5s），没有进度条的话页面像卡死，
       * 这里用「已用时 Ns」给用户一个还在跑的信号。 */
      let gachaTimer = null;
      function gzTickStart() {
        mys.gacha.elapsed = 0;
        clearInterval(gachaTimer);
        gachaTimer = setInterval(() => { mys.gacha.elapsed += 1; }, 1000);
      }
      function gzTickStop() {
        clearInterval(gachaTimer);
        gachaTimer = null;
      }

      /* 查本地 CSV：默认数据源，不发任何网络请求。
       * 调 /zzz/gacha/local —— 服务端只读 data/zzz/{uid}.csv 并跑统计汇总。
       * 本地没存档时返回 ok:false，提示先「增量更新」/「强制全量」。 */
      async function mysGachaLocal() {
        const ri = mysRoleInfo();
        if (!ri) { mys.gacha.error = '请先选择角色'; return; }
        mys.gacha.loading = true;
        mys.gacha.error = '';
        gzTickStart();
        try {
          const j = await mysGet('/zzz/gacha/local', { uid: ri.uid });
          if (j.ok) {
            mys.gacha.data = j;
            mys.gacha.tab = 'summary';
            const pools = j.pools || [];
            if (pools.length && !pools.some(p => p.base === mys.gacha.pool)) {
              mys.gacha.pool = pools[0].base;
            }
          } else mys.gacha.error = j.message || '本地还没有抽卡记录';
        } catch (e) { mys.gacha.error = '读取本地记录失败：' + e.message; }
        gzTickStop();
        mys.gacha.loading = false;
      }

      /* 抽卡同步：调 /zzz/gacha/sync。
       * 服务端负责：首次全量 / 之后增量（页间隔 1s）→ 落盘 data/zzz/{uid}.csv → 统计汇总。
       * force=false = 只拉比本地存档更新的记录（增量）；force=true = 忽略存档重头全量拉（慢）。 */
      async function mysGacha(force) {
        const ri = mysRoleInfo();
        if (!ri) { mys.gacha.error = '请先选择角色'; return; }
        mys.gacha.loading = true;
        mys.gacha.error = '';
        if (force) mys.gacha.data = null;
        gzTickStart();
        try {
          const j = await mysGet('/zzz/gacha/sync', {
            uid: ri.uid, server: ri.server, account_id: mys.current, force: force ? '1' : '',
          });
          if (j.ok) {
            mys.gacha.data = j;
            mys.gacha.tab = 'summary';
            // 之前选的频段可能在新数据里不存在了，回落到第一个有数据的频段
            const pools = j.pools || [];
            if (pools.length && !pools.some(p => p.base === mys.gacha.pool)) {
              mys.gacha.pool = pools[0].base;
            }
          } else mys.gacha.error = j.message || '同步抽卡记录失败';
        } catch (e) { mys.gacha.error = '同步抽卡记录失败：' + e.message; }
        gzTickStop();
        mys.gacha.loading = false;
      }

      /* 出金链条形条宽度：90 抽为满格（ZZZ 独家频段保底线），超过就顶格显示。 */
      function gzBarW(pity) {
        const p = Number(pity) || 0;
        return Math.max(4, Math.min(100, Math.round(p / 90 * 100))) + '%';
      }
      /* 条形/卡片配色：按「距上一次出金抽数」(g.pity) 染色 —— 用户要求：
         <50 抽绿、50~69 抽黄、≥70 抽红。UP/歪信息仍由右侧 gz-tag 文字标出，不与颜色混淆。 */
      function gzBarCls(g) {
        const p = Number(g.pity) || 0;
        if (p >= 70) return 'red';
        if (p >= 50) return 'yellow';
        return 'green';
      }
      function gzPoolName(base) {
        const NAMES = {
          '1': '常驻频段', '2': '独家频段', '3': '音擎频段',
          '5': '邦布频段', '102': '独家重映', '103': '音擎回响',
        };
        return NAMES[String(base)] || '其他';
      }

      // 危局 / 防卫战查询已拆到 frag/zzz-deadly.js、frag/zzz-shiyu.js（含渲染归一化），
      // 这里只保留抽卡的逻辑；暴露集合见下方 ctx.expose。

      // 进入一级选项卡时：刷账号状态（侧边栏，顺带补各账号的角色列表）
      // （抽卡不再需要「频段类型表」下拉：sync 接口一次把 6 个频段全同步回来）
      async function mysTab() {
        if (sidebar.mysRefresh) await sidebar.mysRefresh();
      }

      /* 命令的完整触发词（`zzz deadly` / `mhy login`）：表格的 title、详细设置标题用它。
       * 实现在 frag/social.js 的 MysSocial.trigger —— 模板不能直接摸 window，必须在这里登记。 */
      function mysTrigger(c) {
        return window.MysSocial ? window.MysSocial.trigger(c) : '';
      }

      // 危局 / 防卫战：函数本体在 frag/zzz-deadly.js、frag/zzz-shiyu.js
      // 社交命令配置：容器 frag/social.js + 两个子页面 frag/social-api.js、frag/social-bot.js
      // （注意：expose 对象字面量里千万不要写 // 注释 —— check_admin.mjs 按逗号分块解析，
      //   注释会跟着它后面那个键一起被当成「非标识符开头」而整块丢掉，表现为
      //   「模板引用但无人导出」，且被吞的键在页面上是 undefined，很难查。）
      ctx.expose({}, {
        mysPickRole, mysHasRole, mysZoneOpen, mysTabTo, mysRoleName, mysZoneTo,
        mysGacha, mysGachaLocal, mysCurrent,
        gzBarW, gzBarCls, gzPoolName,
        mysDeadly: deadlyApi.mysDeadly, mysDeadlySaveImage: deadlyApi.mysDeadlySaveImage,
        mysDeadlyHistory: deadlyApi.mysDeadlyHistory,
        mysDeadlyHistoryToggle: deadlyApi.mysDeadlyHistoryToggle,
        mysDeadlyPick: deadlyApi.mysDeadlyPick,
        dzHistAt: deadlyApi.dzHistAt, dzHistPeriod: deadlyApi.dzHistPeriod, dzHistSum: deadlyApi.dzHistSum,
        dzRarity: deadlyApi.dzRarity, dzRarIcon: deadlyApi.dzRarIcon,
        mysShiyu: shiyuApi.mysShiyu, syRatingCls: shiyuApi.syRatingCls, syRarity: shiyuApi.syRarity,
        syRarIcon: shiyuApi.syRarIcon,
        mysShiyuHistory: shiyuApi.mysShiyuHistory,
        mysShiyuHistoryToggle: shiyuApi.mysShiyuHistoryToggle,
        mysShiyuPick: shiyuApi.mysShiyuPick,
        syHistAt: shiyuApi.syHistAt, syHistPeriod: shiyuApi.syHistPeriod, syHistSum: shiyuApi.syHistSum,
        mysAbyss: abyssApi.mysAbyss, mysZenkov: zenkovApi.mysZenkov,
        mysNote: noteApi.mysNote, mysNoteReset: noteApi.mysNoteReset,
        ntDur: noteApi.ntDur, ntStamp: noteApi.ntStamp, ntPct: noteApi.ntPct,
        mysVoid: voidApi.mysVoid, mysVoidReset: voidApi.mysVoidReset,
        vtImgErr: voidApi.vtImgErr, vtLeft: voidApi.vtLeft,
        mysProfile: profileApi.mysProfile, mysProfileReset: profileApi.mysProfileReset,
        pvImgErr: profileApi.pvImgErr,
        mysMonth: monthApi.mysMonth, mysMonthReset: monthApi.mysMonthReset,
        moPretty: monthApi.moPretty,
        mysAvatars: rolesApi.mysAvatars, rlRarity: rolesApi.rlRarity, rlImgErr: rolesApi.rlImgErr,
        rlRarIcon: rolesApi.rlRarIcon, rlRarErr: rolesApi.rlRarErr,
        avOpen: avatarApi.avOpen, avClose: avatarApi.avClose,
        avD: avatarApi.avD, avW: avatarApi.avW,
        avDiscs: avatarApi.avDiscs, avSkills: avatarApi.avSkills,
        avRanks: avatarApi.avRanks, avProps: avatarApi.avProps,
        avRarity: avatarApi.avRarity, avImgErr: avatarApi.avImgErr,
        avRating: avatarApi.avRating, avRatingCls: avatarApi.avRatingCls, avWStarN: avatarApi.avWStarN,
        avWRarity: avatarApi.avWRarity,
        avSkillBarUrl: avatarApi.avSkillBarUrl, avSkillNumStyle: avatarApi.avSkillNumStyle,
        avSkillLvClass: avatarApi.avSkillLvClass,
        // 属性 / 元素 / 职业图标（角色详情里的图标条）—— 新增函数必须在这里登记，
        // 否则模板里调用会报 “xxx is not a function”（expose 是白名单，不是展开）。
        avPropIcon: avatarApi.avPropIcon, avElemIcon: avatarApi.avElemIcon,
        avProfIcon: avatarApi.avProfIcon, avIcErr: avatarApi.avIcErr,
        avRarIcon: avatarApi.avRarIcon, avRarErr: avatarApi.avRarErr,
        // 驱动盘卡片上的稀有度徽章（同一套 Rarity_*.png 素材，但按每张盘自己的
        // 稀有度取图，所以是带参函数）。同样必须登记，否则 “is not a function”。
        avDiscRarIcon: avatarApi.avDiscRarIcon,
        // 详情浮层顶栏的「玩家名字 + UID」（取自侧边栏选中的绝区零角色）
        avPlayer: avatarApi.avPlayer,
        // 详情浮层底部的「查看 JSON 数据」按钮（展开/收起原始返回）
        avToggleRaw: avatarApi.avToggleRaw,
        mysCodex: codexApi.mysCodex, mysCodexRefresh: codexApi.mysCodexRefresh,
        codexPick: codexApi.codexPick, codexClose: codexApi.codexClose,
        cxDetail: codexApi.cxDetail, cxTalent: codexApi.cxTalent,
        cxList: codexApi.cxList, cxSub: codexApi.cxSub, cxChips: codexApi.cxChips,
        cxRarity: codexApi.cxRarity, cxTabName: codexApi.cxTabName, cxTabCount: codexApi.cxTabCount,
        cxImgErr: codexApi.cxImgErr,
        cxFilters: codexApi.cxFilters, cxFilterSet: codexApi.cxFilterSet,
        cxFilterClear: codexApi.cxFilterClear, cxFilterOn: codexApi.cxFilterOn,
        cxSkillVal: codexApi.cxSkillVal, cxMatName: codexApi.cxMatName,
        cxT: codexApi.cxT, cxProp: codexApi.cxProp, cxLangSet: codexApi.cxLangSet,
        cxHow: codexApi.cxHow, cxHowShow: codexApi.cxHowShow, cxHowHide: codexApi.cxHowHide,
        mysSignLoad: signApi.mysSignLoad, mysSignDo: signApi.mysSignDo,
        sgSigned: signApi.sgSigned, sgToday: signApi.sgToday, sgImgErr: signApi.sgImgErr,
        asOpen: autosignApi.asOpen, asClose: autosignApi.asClose, asSave: autosignApi.asSave,
        asRun: autosignApi.asRun, asLoadTargets: autosignApi.asLoadTargets,
        asLoadInstances: autosignApi.asLoadInstances,
        asPick: autosignApi.asPick, asPickedOne: autosignApi.asPickedOne,
        asAccAll: autosignApi.asAccAll, asAccToggle: autosignApi.asAccToggle,
        asLastText: autosignApi.asLastText,
        mysSocialLoad: socialApi.mysSocialLoad, mysSocialSave: socialApi.mysSocialSave,
        mysSocialTab: socialApi.mysSocialTab,
        mysTrigger,
        saAdd: socialApiPage.saAdd, saDel: socialApiPage.saDel,
        sbMaster: socialBotPage.sbMaster, sbAll: socialBotPage.sbAll,
        sbSubToggle: socialBotPage.sbSubToggle, sbSubAll: socialBotPage.sbSubAll,
        sdOpen: socialDetail.sdOpen, sdClose: socialDetail.sdClose, sdSave: socialDetail.sdSave,
        sdReset: socialDetail.sdReset, sdProto: socialDetail.sdProto, sdCount: socialDetail.sdCount,
        sdTplSample: socialDetail.sdTplSample, sdTplClear: socialDetail.sdTplClear,
        sdPreview: socialDetail.sdPreview,
        dvLoad: devicePage.dvLoad, dvTab: devicePage.dvTab,
        dvSave: devicePage.dvSave, dvRegister: devicePage.dvRegister,
        dvDelete: devicePage.dvDelete, dvTest: devicePage.dvTest,
        dvFmt: devicePage.dvFmt, dvUseSample: devicePage.dvUseSample,
        dvCurDevice: devicePage.dvCurDevice, dvHas: devicePage.dvHas,
        dvTime: devicePage.dvTime, dvShort: devicePage.dvShort,
        dvHelp: devicePage.dvHelp,
      });

      return { mysTab };
    },
  });
})();
