/* 绝区零 · 图鉴（plugins/_vendor/miyoushe/web/frag/zzz-codex.js）
 * ------------------------------------------------------------------
 * 模板在 frag/zzz-codex.html，样式在 frag/zzz-codex.css（cx-* 前缀）。
 * 由 module.js 在 setup 里调 window.MysCodex.init/setup（frag/*.js 在 module.js 之后加载，
 * 但 setup 要到 boot 时才执行，所以文件先后顺序无所谓）。
 *
 * 数据来自 /zzz/codex 的**本地快照**（plugins/_vendor/miyoushe/codex_data.json）：
 *   { ok, version, updated, counts, agents[], wengines[], discs[], bangboo[] }
 *   agent  : {id, name, full_name, en_name, rarity(S/A), element, profession, camp, hit_type, icon}
 *   wengine: {id, name, rarity, main(基础属性), sub(高级属性), talents[{name,desc}] 1~5 阶, icon}
 *   disc   : {id, name, desc1(2件套), desc2(4件套), icon}
 *   bangboo: {id, name, rarity, icon}
 *
 * 图标在后台抓取阶段就已下载到本机（icon 是 /zzz/asset/… 本地路由），
 * 打开图鉴不访问任何外部地址。想更新数据点「更新数据」（后台重新抓取并落盘）。
 * 与账号无关，无需选角色，所以不做「切角色即清空」。
 */
window.MysCodex = {
  init(mys) {
    mys.codex = {
      loading: false, refreshing: false, done: false, error: '', version: '', updated: '',
      tab: 'agents', q: '',
      lang: 'zh-cn',        // 显示语言：zh-cn = 原文（图鉴数据本身就是简中），不需要映射
      langs: [{ id: 'zh-cn', name: '简体中文' }],   // 可选语言，来自接口的 lang_meta
      langMap: null,        // by_cat[类别][id][语言] = 翻译对象；**懒加载**，切到非简中才拉
      langText: null,       // 当前语言的「中文原文 → 译文」扁平查表（由 langMap 压出来，cxT 用）
      filter: { element: '', profession: '', camp: '' },  // 代理人筛选：属性 / 职业 / 阵营
      skillLv: 8,            // 技能倍率按这个等级换算（绝区零技能等级 1~12）
      detail: null,          // 当前打开的详情条目（含 kind = 打开时的页签）
      talent: 1,             // 音擎效果阶数（1~5）
      ranks: [1, 2, 3, 4, 5],
      howData: null,         // 「更新方式」流程说明（来自接口的 pipeline，后台按真实常量拼的）
      howOpen: false,        // 说明浮层是否展开
      data: { agents: [], wengines: [], discs: [], bangboo: [] },
    };
  },

  setup(ctx, mys) {
    const mysGet = (path, params) => ctx.get('' + path, params);

    /* 图片挂了时的占位（个别 sprite 缺失会 404，别显示破图） */
    const PLACEHOLDER = 'data:image/svg+xml;charset=utf-8,' + encodeURIComponent(
      "<svg xmlns='http://www.w3.org/2000/svg' width='120' height='120'>"
      + "<rect width='120' height='120' fill='#efece9'/>"
      + "<text x='60' y='74' font-size='42' fill='#b9b2c2' text-anchor='middle'>?</text></svg>"
    );

    function cxImgErr(e) {
      e.target.onerror = null;
      e.target.src = PLACEHOLDER;
    }

    /* 稀有度 → 配色（S 金 / A 紫 / 其余灰） */
    function cxRarity(it) {
      const r = String((it && it.rarity) || '').toUpperCase();
      return r === 'S' ? 's' : r === 'A' ? 'a' : 'b';
    }

    /* ---- 显示语言（多语言） ----
       后端 /zzz/codex/lang 返回 by_cat[类别][id][语言] = 与普通 json 同构的翻译对象
       （见 codex_lang.py：字段名和本地快照 agents.json 等逐一对齐）。前端切到非简中时
       拉一次，用 buildLangText() 把它压成「中文原文 → 译文」扁平表（langText），
       之后所有展示文本都走 cxT(text) 查这张表；**查不到就原样回退中文**（宁缺毋错）。
       简中直接返回原文，连表都不用加载（默认打开图鉴不产生任何额外请求）。 */
    function cxT(text) {
      if (!text || mys.codex.lang === 'zh-cn') return text || '';
      const m = mys.codex.langText;
      return (m && m[text]) || text;
    }

    /* 音擎的「基础攻击力 46」这类串：数字和属性名拆开，翻译只翻属性名、数字原样保留。
       翻译对象里 main/sub 存的也只是属性名（数字在生成阶段就被剥离了），所以这里拆开后
       cxT 查属性名，再拼回数字。 */
    const _PROP_RE = /^(.*?)\s*([\d.]+)\s*$/;
    function _splitProp(t) {
      const m = _PROP_RE.exec(t || '');
      return m ? m[1].trim() : (t || '').trim();
    }

    /* 把「源条目（中文）+ 翻译对象（同结构）」并行走一遍，把所有中文原文映射到译文，
       得到一个扁平查表 {中文原文: 译文}。key 全部来自源条目里的中文值，翻译对象只提供译文，
       所以不会出现「用译文当 key」的反向错误。数组按索引对齐（skills/params/extras/talents）。 */
    function _walkText(src, tr, m) {
      if (!tr || typeof tr !== 'object') return;
      if (typeof src !== 'object' || src == null) return;
      if (Array.isArray(src) && Array.isArray(tr)) {
        const n = Math.min(src.length, tr.length);
        for (let i = 0; i < n; i++) _walkText(src[i], tr[i], m);
        return;
      }
      if (Array.isArray(src) || Array.isArray(tr)) return;
      for (const k in tr) {
        const tv = tr[k];
        const sv = src[k];
        if (typeof tv === 'string') {
          if (typeof sv === 'string' && sv) m[sv] = tv;
          // main/sub 是「属性名 + 数字」整串，cxProp 会拆数字；这里额外登记「属性名→译文」
          // 让 cxProp 拆出来的属性名也能查到译文（翻译对象里 main/sub 就只是属性名）。
          if ((k === 'main' || k === 'sub') && typeof sv === 'string') {
            const pn = _splitProp(sv);
            if (pn) m[pn] = tv;
          }
        } else if (tv && typeof tv === 'object') {
          _walkText(sv, tv, m);
        }
      }
    }

    /* 为当前选中的语言构建「中文原文→译文」扁平表：遍历四类本地快照 + 对应的翻译对象。 */
    function buildLangText() {
      const m = {};
      const lang = mys.codex.lang;
      const bm = mys.codex.langMap;   // by_cat[类别][id][语言]
      if (!bm) return m;
      for (const cat of ['agents', 'wengines', 'discs', 'bangboo']) {
        const srcList = mys.codex.data[cat] || [];
        const trBucket = bm[cat] || {};
        for (const it of srcList) {
          const entry = trBucket[String(it.id)];
          const tr = entry && entry[lang];
          if (tr) _walkText(it, tr, m);
        }
      }
      return m;
    }

    /* 音擎的「基础攻击力 46」是后台拼出来的字符串：只翻译属性名，数值原样保留 */
    function cxProp(text) {
      const m = /^(.*?)\s*([\d.]+)\s*$/.exec(text || '');
      if (!m) return cxT(text);
      return (cxT(m[1]) + ' ' + m[2]).trim();
    }

    /* 切语言：by_cat（按类别×id）只有真的切到非简中才拉一次；拉回来后压成
       「中文原文→译文」扁平表（langText）走内存。在简中与两个非简中语言之间互切都重建。 */
    async function cxLangSet(id) {
      if (!id || id === mys.codex.lang) return;
      mys.codex.lang = id;
      if (id === 'zh-cn') { mys.codex.langText = null; return; }   // 简中：不加载、不翻译
      if (!mys.codex.langMap) {
        try {
          const j = await mysGet('/zzz/codex/lang');
          if (!j.ok) {
            if (ctx.notice) ctx.notice(j.message || '多语言数据加载失败', 'err');
            return;
          }
          mys.codex.langMap = j.by_cat || {};
        } catch (e) {
          if (ctx.notice) ctx.notice('多语言数据加载失败：' + e.message, 'err');
          return;
        }
      }
      mys.codex.langText = buildLangText();
    }

    const TAB_NAMES = { agents: '代理人', wengines: '音擎', discs: '驱动盘套装', bangboo: '邦布' };

    function cxTabName() {
      return TAB_NAMES[mys.codex.tab] || '';
    }

    /* 页签上的条数 */
    function cxTabCount(name) {
      const list = mys.codex.data[name] || [];
      return list.length;
    }

    /* ---- 代理人筛选（属性 / 职业 / 阵营） ----
       只对「代理人」页签生效；选项从当前数据里去重汇总，所以新角色/新阵营自动出现，
       不需要在前端维护一份写死的枚举。三个下拉框，值 '' = 全部（不筛该项）。 */
    const FILTER_DEFS = [
      { key: 'element', label: '属性' },
      { key: 'profession', label: '职业' },
      { key: 'camp', label: '阵营' },
    ];

    function cxFilters() {
      if (mys.codex.tab !== 'agents') return [];
      const list = mys.codex.data.agents || [];
      return FILTER_DEFS.map((d) => {
        const seen = [];
        for (const it of list) {
          const v = it[d.key];
          if (v && seen.indexOf(v) === -1) seen.push(v);
        }
        return { key: d.key, label: d.label, options: seen };
      }).filter((f) => f.options.length > 1);
    }

    /* 选中某个下拉框的值（'' = 全部）；原来胶囊是「再点一次取消」，换成 select 后
       同值再选不会触发 change，所以这里只做赋值。 */
    function cxFilterSet(key, val) {
      mys.codex.filter[key] = val || '';
    }

    function cxFilterClear() {
      mys.codex.filter = { element: '', profession: '', camp: '' };
    }

    function cxFilterOn() {
      const f = mys.codex.filter;
      return !!(f.element || f.profession || f.camp);
    }

    /* ---- 排序：稀有度 S 在前；同一稀有度内**新的在前** ----
       图鉴 id 随时间递增（角色 1021 猫又 → 1641 菲欧妮），所以「新在前」= id 数值降序。
       放在前端做一次，好处是本地快照即使还是旧的顺序，刷新页面也能立刻看到新排序；
       后台 codex_build 也按同一规则生成，两边一致。 */
    function cxIdNum(it) {
      const s = String((it && it.id) || '').replace(/[^0-9]/g, '');
      return s ? parseInt(s, 10) : 0;
    }

    function cxSorted(list) {
      return list.slice().sort((a, b) => {
        const ra = a.rarity === 'S' ? 0 : 1;
        const rb = b.rarity === 'S' ? 0 : 1;
        if (ra !== rb) return ra - rb;
        return cxIdNum(b) - cxIdNum(a);
      });
    }

    /* 当前页签的条目：先按代理人筛选，再按搜索词过滤名称/全名/英文名，最后统一排序 */
    function cxList() {
      let list = mys.codex.data[mys.codex.tab] || [];
      if (mys.codex.tab === 'agents') {
        const f = mys.codex.filter;
        list = list.filter((it) =>
          (!f.element || it.element === f.element) &&
          (!f.profession || it.profession === f.profession) &&
          (!f.camp || it.camp === f.camp));
      }
      const q = (mys.codex.q || '').trim().toLowerCase();
      if (!q) return cxSorted(list);
      return cxSorted(list.filter((it) => {
        // 中文原名、英文名、以及当前语言的译名都能搜到（切到日文后按日文名也能搜）
        const hay = ((it.name || '') + ' ' + (it.full_name || '') + ' ' + (it.en_name || '')
                     + ' ' + cxT(it.name) + ' ' + cxT(it.full_name)).toLowerCase();
        return hay.indexOf(q) !== -1;
      }));
    }

    /* 技能倍率：数据里存的是 [Main, Growth]，实际倍率 = (Main + Growth × 技能等级) / 10000
       （与参考项目 ZZZeroUID 的 utils/data.py 同一套算法），这里按百分比显示。 */
    function cxSkillVal(p) {
      const lv = Number(mys.codex.skillLv) || 1;
      const v = (Number(p.main || 0) + Number(p.growth || 0) * lv) / 10000;
      return (v * 100).toFixed(1) + '%';
    }

    /* 突破材料：数据源只给 ID（10 = 丁尼），没有「材料名 → 名字」对照表，所以不猜名字 */
    function cxMatName(m) {
      const id = String((m && m.id) || '');
      const n = (m && m.n) || 0;
      return (id === '10' ? '丁尼 ×' : '材料 ' + id + ' ×') + n;
    }

    /* 卡片副标题：代理人=阵营、音擎=基础属性、驱动盘=2件套效果（都过一层翻译） */
    function cxSub(it) {
      if (mys.codex.tab === 'agents') return cxT(it.camp || '');
      if (mys.codex.tab === 'wengines') return cxProp(it.main || '');
      if (mys.codex.tab === 'discs') return cxT(it.desc1 || '');
      return '';
    }

    /* 卡片小标签：代理人=元素/职业、音擎=高级属性 */
    function cxChips(it) {
      if (mys.codex.tab === 'agents') return [it.element, it.profession].filter(Boolean).map(cxT);
      if (mys.codex.tab === 'wengines') return [it.sub].filter(Boolean).map(cxProp);
      return [];
    }

    function codexPick(it) {
      // 记下打开时的页签，详情按它渲染（避免切换页签后浮层里内容错乱）
      mys.codex.detail = Object.assign({}, it, { kind: mys.codex.tab });
      mys.codex.talent = 1;
    }

    function codexClose() {
      mys.codex.detail = null;
    }

    /* ---- 「更新方式」说明浮层 ----
       内容由接口 /zzz/codex 的 pipeline 字段给出（后台 codex_build.pipeline() 用真实常量拼的）：
       源地址、缓存目录、文件名都取自代码本身，所以不会和实际抓取流程脱节。
       接口拿不到（例如后端还没重启、路由里还没有这个字段）时返回空对象，链接整条不显示。 */
    const HOW_EMPTY = { version: '', summary: '', steps: [], table_total: '' };

    function cxHow() {
      return mys.codex.howData || HOW_EMPTY;
    }

    function cxHowShow() {
      if (cxHow().steps.length) mys.codex.howOpen = true;
    }

    function cxHowHide() {
      mys.codex.howOpen = false;
    }

    /* 把条目归一成详情浮层要用的形状：{kind_name, name, rarity, icon, chips, rows, blocks, talents} */
    function cxDetail() {
      const it = mys.codex.detail;
      const rows = [], chips = [], blocks = [];
      if (!it) {
        return { kind_name: '', name: '', full_name: '', rarity: '', icon: '',
                 chips: [], rows: [], blocks: [], talents: [],
                 skills: [], levels: [], extras: [] };
      }
      const kind = it.kind || mys.codex.tab;
      if (kind === 'agents') {
        if (it.element) chips.push(it.element);
        if (it.profession) chips.push(it.profession);
        if (it.rarity) rows.push({ k: '稀有度', v: it.rarity + ' 级' });
        if (it.camp) rows.push({ k: '阵营', v: cxT(it.camp) });
        if (it.hit_type) rows.push({ k: '伤害类型', v: cxT(it.hit_type) });
        if (it.en_name) rows.push({ k: '英文名', v: it.en_name });
        rows.push({ k: '角色 ID', v: it.id });
      } else if (kind === 'wengines') {
        if (it.main) rows.push({ k: '基础属性', v: cxProp(it.main) });
        if (it.sub) rows.push({ k: '高级属性', v: cxProp(it.sub) });
        rows.push({ k: '音擎 ID', v: it.id });
      } else if (kind === 'discs') {
        if (it.desc1) blocks.push({ title: '2 件套效果', text: cxT(it.desc1) });
        if (it.desc2) blocks.push({ title: '4 件套效果', text: cxT(it.desc2) });
        rows.push({ k: '套装 ID', v: it.id });
      } else {
        if (it.rarity) rows.push({ k: '稀有度', v: it.rarity + ' 级' });
        rows.push({ k: '邦布 ID', v: it.id });
      }
      // 代理人专属的扩展段（技能倍率 / 突破档位 / 核心技）；其它页签给空数组
      const isAgent = kind === 'agents';
      return {
        kind_name: TAB_NAMES[kind] || '',
        name: cxT(it.name), full_name: cxT(it.full_name), rarity: it.rarity || '',
        icon: it.icon || '', chips: chips.map(cxT), rows: rows, blocks: blocks,
        talents: it.talents || [],
        // 技能名与参数名都要翻译（参数名如「一段伤害倍率」→ 1st-Hit DMG Multiplier）
        skills: isAgent ? (it.skills || []).map((s) => ({
          name: cxT(s.name),
          params: (s.params || []).map((p) => ({ k: cxT(p.k), main: p.main, growth: p.growth })),
        })) : [],
        levels: (isAgent && it.levels) || [],
        extras: isAgent ? (it.extras || []).map((e) => ({
          max: e.max, items: (e.items || []).map((x) => ({ k: cxT(x.k), v: x.v })),
        })) : [],
      };
    }

    /* 当前选中的音擎效果（阶数越界时自动夹到有效范围） */
    function cxTalent() {
      const list = (mys.codex.detail && mys.codex.detail.talents) || [];
      if (!list.length) return { name: '', desc: '' };
      const i = Math.min(Math.max(1, Number(mys.codex.talent) || 1), list.length) - 1;
      const t = list[i] || { name: '', desc: '' };
      return { name: cxT(t.name), desc: cxT(t.desc) };
    }

    async function mysCodex(force) {
      if (mys.codex.done && !force) return;
      mys.codex.loading = true;
      mys.codex.error = '';
      try {
        const j = await mysGet('/zzz/codex');
        if (j.ok) {
          mys.codex.data = {
            agents: j.agents || [], wengines: j.wengines || [],
            discs: j.discs || [], bangboo: j.bangboo || [],
          };
          mys.codex.version = j.version || '';
          mys.codex.updated = j.updated || '';
          mys.codex.howData = j.pipeline || null;   // 「更新方式」说明（后台按真实常量生成）
          // 可选语言清单（只有真的生成过映射才会有非简中的选项）
          if (j.lang_meta && j.lang_meta.langs && j.lang_meta.langs.length) {
            mys.codex.langs = j.lang_meta.langs;
            if (!mys.codex.langs.some((l) => l.id === mys.codex.lang)) mys.codex.lang = 'zh-cn';
          }
          mys.codex.done = true;
        } else mys.codex.error = j.message || '读取图鉴失败';
      } catch (e) { mys.codex.error = '读取图鉴失败：' + e.message; }
      mys.codex.loading = false;
    }

    /* 让后台重新抓取（文本 + 图标）并落盘，然后重新读本地快照。 */
    async function mysCodexRefresh() {
      if (mys.codex.refreshing) return;
      mys.codex.refreshing = true;
      mys.codex.error = '';
      try {
        const j = await ctx.post('/zzz/codex/refresh', {});
        if (!j.ok) mys.codex.error = j.message || '更新图鉴数据失败';
        else { if (ctx.notice) ctx.notice(j.message || '图鉴数据已更新', 'ok'); await mysCodex(true); }
      } catch (e) { mys.codex.error = '更新图鉴数据失败：' + e.message; }
      mys.codex.refreshing = false;
    }

    return {
      mysCodex, mysCodexRefresh, codexPick, codexClose, cxDetail, cxTalent,
      cxList, cxSub, cxChips, cxRarity, cxTabName, cxTabCount, cxImgErr,
      // 代理人筛选 + 详情扩展段 + 多语言
      cxFilters, cxFilterSet, cxFilterClear, cxFilterOn, cxSkillVal, cxMatName,
      cxT, cxProp, cxLangSet,
      cxHow, cxHowShow, cxHowHide,
    };
  },
};
