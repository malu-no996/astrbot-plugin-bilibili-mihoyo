/* 绝区零 · 角色详情浮层（plugins/_vendor/miyoushe/web/frag/zzz-avatar.js）
 * ------------------------------------------------------------------
 * 模板 frag/zzz-avatar.html，样式 frag/zzz-avatar.css（av-* 前缀）。
 * 入口在 frag/zzz-roles.html 的角色卡片上：@click="avOpen(a)"。
 *
 * 后端 /zzz/avatar/info 返回 { ok, detail } —— detail 的形状见
 * src/avatar_detail.py 顶部的 DETAIL_SHAPE（两套源归一化后一致）。
 * 官方被风控时后端自动降级 Enka，detail.source 会变成 'enka'、note 里写明原因。
 *
 * 状态挂在共享的 mys.avatar 上：{ show, loading, error, detail, id }
 * —— show 与 detail 分开存，关掉浮层不清数据，再点同一张卡片直接复用（不发请求）。
 */
window.MysAvatar = {
  init(mys) {
    mys.avatar = {
      show: false, loading: false, error: '', detail: null, id: '',
      raw: '',          // 接口给的**原始 JSON**（格式化后的文本），给「查看 JSON 数据」用
      showRaw: false,   // 那段 JSON 是否展开
    };
  },

  setup(ctx, mys, hooks) {
    const mysGet = (path, params) => ctx.get('' + path, params);
    const roleInfo = () => (hooks && hooks.mysRoleInfo ? hooks.mysRoleInfo() : null);

    /* 图片挂掉时的占位（与角色列表同一套深色底 + 问号） */
    const PLACEHOLDER = 'data:image/svg+xml;charset=utf-8,' + encodeURIComponent(
      "<svg xmlns='http://www.w3.org/2000/svg' width='120' height='120'>"
      + "<rect width='120' height='120' fill='#241c2e'/>"
      + "<text x='60' y='72' font-size='40' fill='#6d5c80' text-anchor='middle'>?</text></svg>"
    );

    /* 当前详情（模板里到处要用，写个取值函数省得反复判空） */
    function avD() {
      return mys.avatar.detail;
    }

    /* 属性 / 元素 / 职业图标：直接取插件里的游戏素材（和出图用的是同一套）。
       后端下发的是**文件名**，文件不存在时给空串，这里便退回纯文字，不会出现裂图。 */
    const ICON_BASE = './assets/icon/';
    function avIcon(kind, name) {
      if (!name) return '';
      return ICON_BASE + (kind ? kind + '/' : '') + encodeURIComponent(name) + '.png';
    }
    function avPropIcon(p) { return avIcon('prop', p && p.icon); }
    function avElemIcon() { const d = avD(); return avIcon('', d && d.element_icon); }
    function avProfIcon() { const d = avD(); return avIcon('pro', d && d.profession_icon); }
    /* 图挂了（换版删了素材等）就地藏掉，别留个破图标 */
    function avIcErr(e) { if (e && e.target) e.target.style.display = 'none'; }

    /* 稀有度图标加载失败：藏掉 img，并把紧随其后的文字小标签放出来（兜底）。
       两类地方都走这个：角色详情的头像框（.av-rar）和驱动盘卡片的稀有度徽章（.av-drar-t）。 */
    function avRarErr(e) {
      const img = e && e.target;
      if (!img) return;
      img.style.display = 'none';
      const sib = img.nextElementSibling;
      if (sib && sib.classList &&
          (sib.classList.contains('av-rar') || sib.classList.contains('av-drar-t'))) {
        sib.style.display = '';
      }
    }

    function avW() {
      const d = avD();
      return (d && d.weapon) || null;
    }

    function avDiscs() {
      const d = avD();
      return (d && d.discs) || [];
    }

    function avSkills() {
      const d = avD();
      return (d && d.skills) || [];
    }

    function avRanks() {
      const d = avD();
      return (d && d.ranks) || [];
    }

    function avProps() {
      const d = avD();
      return (d && d.props) || [];
    }

    /* 稀有度 → 配色（与角色卡片同一套：S 金 / A 紫） */
    function avRarity() {
      const d = avD();
      return String((d && d.rarity) || '').toUpperCase() === 'A' ? 'a' : 's';
    }

    /* 稀有度徽章（2026-10-05 改）：S / A / B 用官方 `{S,A,B}RANK.png` **方徽章**
       （带「S RANK」字样，与绝境群排行出图同一套素材），不再用 Rarity_*.png 圆形徽章。
       C 级没有对应素材 → 返回空串，模板退回文字小标签，保证不出现裂图。
       ⚠️ 驱动盘卡片上的稀有度**不走这里**（那个是下面的 avDiscRarIcon，仍是
       Rarity_*.png 圆形徽章）—— 驱动的 S/A 用户明确要求保持不变，别顺手一起换。 */
    function avRarIcon() {
      const r = String((avD() && avD().rarity) || '').toUpperCase();
      return ['S', 'A', 'B'].indexOf(r) >= 0 ? avIcon('', r + 'RANK') : '';
    }

    /* 驱动盘的稀有度徽章：和代理人/音擎用的是同一套官方圆形素材（Rarity_S/A/B/C.png）。
       带参数是因为每张盘的稀有度不同，不能用上面那个只读当前角色 avD() 的 avRarIcon()。
       没素材（或数据源不给 rarity，比如 Enka 侧驱动盘 rarity 恒为空串）→ 返回空串，
       模板退回文字小标签，不会出裂图。 */
    function avDiscRarIcon(r) {
      const s = String(r || '').toUpperCase();
      return ['S', 'A', 'B', 'C'].indexOf(s) >= 0 ? avIcon('', 'Rarity_' + s) : '';
    }

    /* 评分角标：官方给的是 ER_S / ER_A / ER_S_PLUS / ER_SS_PLUS…（后端 detail._clean_rating
       已统一成 S / SS / SSS / S+ / SS+ / SSS+ / A / B / C）。这里再兜一次
       「S 个数不限 + 加号的几种写法」和「DEFAULT 占位」。
       评级不到 A 时官方回的是 DEFAULT 占位，那不是真评分 → 返回空串，
       模板整个 .av-wside（「DEFAULT / 驱动盘」）就不渲染了。 */
    function avRating() {
      const d = avD();
      let r = String((d && d.rating) || '').toUpperCase().replace(/^ER_/, '');
      r = r.replace(/_PLUS/g, '+').replace(/PLUS/g, '+').replace(/\++$/, '+');
      if (r === 'SP') r = 'S+';
      if (r === 'DEFAULT' || r === 'NONE' || r === 'NULL') r = '';
      return r;
    }

    /* 评分角标的配色类：S / SS（带不带 +）用默认的橙色（不加类），A 紫、B/C 浅蓝，
       **SSS / SSS+ 走彩虹渐变**（.av-rate.rb）—— 和角色 / 音擎 / 驱动盘稀有度同一套色系。 */
    function avRatingCls() {
      const r = avRating();
      if (/^SSS/.test(r)) return 'rb';        // 最高档：彩虹渐变字
      if (r === 'A') return 'a';
      if (r === 'B' || r === 'C') return 'b';
      return '';
    }

    /* 音擎「进阶」层数（官方字段 star，1~5；没进阶就是 1 层）。
       ⚠️ 这不是稀有度 —— 稀有度已经由 .av-wico 的边框颜色表示（S 金 / A 紫 / B 蓝）。
       接口偶尔给 0 或干脆不给（老缓存），一律按「1 层」兜底。
       展示照游戏：图标下沿压一整排 5 格星，前 N 颗点亮（模板 v-for 5 格 + :class on）。 */
    function avWStarN() {
      const w = avW();
      if (!w) return 0;
      return Math.min(5, Math.max(1, parseInt(w.star, 10) || 0));
    }

    /* 音擎自己的稀有度（不是角色的）：S 金边 / A 紫边 / B 浅蓝边，别的归 b */
    function avWRarity() {
      const w = avW();
      const r = String((w && w.rarity) || '').toUpperCase();
      return r === 'S' ? 's' : (r === 'A' ? 'a' : 'b');
    }

    /* 技能条：整张 skill_bar.png（350×70）铺开，图里自带 6 个圆形黑底，
     * 等级数字直接按百分比叠到对应那个圆上 —— 坐标抄 ZZZeroUID
     * zzzerouid_char_list/draw_char_list.py：`text((32 + pos*50.3, 50))`。
     * 用百分比的好处：图缩放成多宽，数字都跟着落在同一个圆里。 */
    function avSkillBarUrl() {
      return './assets/skill_bar.png';
    }

    function avSkillNumStyle(s) {
      const pos = (s && s.pos) || 0;
      const x = 32 + pos * 50.3;      // 圆心横坐标（原图 350 宽）
      return {
        left: (x / 350 * 100).toFixed(2) + '%',
        top: (50 / 70 * 100).toFixed(2) + '%',   // 圆心纵坐标（原图 70 高）
      };
    }

    /* 技能等级配色（抄 ZZZeroUID：11+ 黄 / 6+ 蓝 / 3+ 白 / 其余灰） */
    function avSkillLvClass(s) {
      const lv = (s && s.level) || 0;
      return lv >= 11 ? 'gold' : (lv >= 6 ? 'blue' : (lv >= 3 ? '' : 'dim'));
    }

    /* 玩家名字 + UID —— 详情浮层最上面那一栏（照官方角色详情页的排布）。
       数据来自侧边栏当前选中的那个绝区零角色（/zzz/roles 的 nickname / game_uid，
       由 module.js 的 mysRoleInfo() 带过来），跟角色本身的数据源（米游社 / Enka）无关。 */
    function avPlayer() {
      const ri = roleInfo() || {};
      return { name: String(ri.nickname || ''), uid: String(ri.uid || '') };
    }

    function avImgErr(e) {
      e.target.onerror = null;
      e.target.src = PLACEHOLDER;
    }

    /* 打开详情：同一张卡片直接复用上次的数据，换角色才发请求 */
    async function avOpen(a) {
      const ri = roleInfo();
      if (!ri) { mys.avatar.error = '请先在左侧选择要查询的角色'; mys.avatar.show = true; return; }
      const id = String((a && a.id) || '');
      if (!id) return;
      mys.avatar.show = true;
      if (mys.avatar.detail && mys.avatar.id === id) return;
      mys.avatar.id = id;
      mys.avatar.detail = null;
      mys.avatar.error = '';
      mys.avatar.loading = true;
      try {
        const j = await mysGet('/zzz/avatar/info', {
          uid: ri.uid, server: ri.server, id: id, account_id: mys.current,
        });
        if (j.ok) {
          mys.avatar.detail = j.detail || null;
          // raw 是后端原样转出的官方(或 Enka)原始数据；没有就不显示那个按钮
          mys.avatar.raw = j.raw ? JSON.stringify(j.raw, null, 2) : '';
        } else mys.avatar.error = j.message || '读取角色详情失败';
      } catch (e) {
        mys.avatar.error = '读取角色详情失败：' + e.message;
      }
      mys.avatar.loading = false;
    }

    /* 关闭：只收起浮层，数据留着（再点同一张卡片不用重新拉） */
    function avClose() {
      mys.avatar.show = false;
    }

    /* 展开 / 收起「查看 JSON 数据」—— 纯展示，不动数据 */
    function avToggleRaw() {
      mys.avatar.showRaw = !mys.avatar.showRaw;
    }

    /* 换角色 / 换账号时要把缓存清掉，否则会串号 */
    function avReset() {
      mys.avatar.show = false;
      mys.avatar.detail = null;
      mys.avatar.id = '';
      mys.avatar.error = '';
      mys.avatar.loading = false;
      mys.avatar.raw = '';
      mys.avatar.showRaw = false;
    }

    return {
      avOpen, avClose, avReset,
      avD, avW, avDiscs, avSkills, avRanks, avProps,
      avRarity, avImgErr, avRating, avRatingCls, avWStarN, avWRarity, avRarIcon, avRarErr,
      avDiscRarIcon, avPlayer, avToggleRaw,
      avPropIcon, avElemIcon, avProfIcon, avIcErr,
      avSkillBarUrl, avSkillNumStyle, avSkillLvClass,
    };
  },
};
