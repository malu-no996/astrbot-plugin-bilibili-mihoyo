/* 社交命令配置 · 「详细设置」弹窗逻辑（plugins/_vendor/miyoushe/web/frag/social-detail.js）
 * 模板 frag/social-detail.html，样式 frag/social-detail.css（sd-* 前缀）。
 * 状态挂在 mys.social.detail 上（由 frag/social.js 的 init 建立），所以这里只有 setup。
 *
 * 弹窗里两件事
 * ------------
 * ① 接口设置：表单**按后端给的 schema 渲染**（后端 @interface(options=[...]) 声明什么字段，
 *    这里就出什么控件 select / number / switch / text / textarea）—— 以后给接口加设置项只改 social.py，
 *    不用碰前端。
 * ② 回复模板：OneBot 第三方 / QQ 官方各一套（两套协议能发的格式不一样，分开配才不用互相迁就），
 *    留空 = 用接口自带的默认文本；可以点「预览」真跑一次接口看渲染结果，
 *    并把接口返回的 vars（模板变量）与 data（原始数据）以 JSON 摆出来。
 *    预览要带「身份」——绑定类接口查的是某个 QQ 绑的米游社账号，管理页没有身份，
 *    所以留空时后端自动挑一个已绑定用户（见 social_routes 的 /social/preview）。
 *
 * 编辑的是**副本**：sdOpen 时把命令上的 options / tpl 拷进来，中途「取消」不会污染表格里的数据；
 * 「确定」（sdSave）写回那一行并**立即落盘**（调 mys.social.save，见 social.js）——
 * 不再要求额外点一次页面右上角的「保存」，那一步漏掉的话配置就是白改的。 */
window.MysSocialDetail = {
  setup(ctx, mys) {
    const mysPost = (path, body) => ctx.post('' + path, body);

    /* 当前弹窗编辑的那条命令绑定的接口定义（schema / 变量说明 / 示例模板都在它身上） */
    function sdIface(api) {
      const key = String(api || '');
      return (mys.social.interfaces || []).find(f => f.key === key) || null;
    }

    /* schema 给了 default，但老配置里可能压根没这个键 —— 补齐再编辑，
     * 否则下拉框会空白、v-model 绑到一个不存在的键上。 */
    function fillDefaults(schema, saved) {
      const out = {};
      for (const f of schema || []) {
        const k = String(f.key || '');
        if (!k) continue;
        const v = (saved || {})[k];
        out[k] = (v === undefined || v === null || v === '') ? f.default : v;
      }
      return out;
    }

    /* 表格按钮上那个「（N）」：数一数这条命令到底改了几项，
     * 只看「跟默认值不一样的选项」+「填了内容的模板」，避免每行都挂个数字。 */
    function sdCount(c) {
      if (!c) return 0;
      let n = 0;
      const tpl = c.tpl || {};
      if (String(tpl.onebot || '').trim()) n += 1;
      if (String(tpl.qq || '').trim()) n += 1;
      const it = sdIface(c.api);
      for (const f of ((it && it.options) || [])) {
        const v = (c.options || {})[f.key];
        if (v !== undefined && v !== null && String(v) !== String(f.default)) n += 1;
      }
      return n;
    }

    function sdOpen(i) {
      const c = (mys.social.commands || [])[i];
      if (!c) return;
      const it = sdIface(c.api);
      const schema = (it && it.options) || [];
      const d = mys.social.detail;
      d.index = i;
      d.id = c.id;
      d.cmd = c.cmd || '';
      d.api = c.api || '';
      d.apiLabel = it ? it.label : (c.api || '（未绑定接口）');
      d.schema = schema;
      d.varsDoc = (it && it.tpl_vars) || [];
      d.sample = (it && it.sample) || '';
      d.options = fillDefaults(schema, c.options);
      d.tpl = { onebot: String(((c.tpl || {}).onebot) || ''), qq: String(((c.tpl || {}).qq) || '') };
      d.proto = 'onebot';
      d.arg = '';
      d.user = '';
      d.preview = '';
      d.previewErr = '';
      d.previewEmpty = false;
      d.previewUser = '';
      d.previewVarsJson = '';
      d.previewDataJson = '';
      d.show = true;
    }

    function sdClose() {
      mys.social.detail.show = false;
    }

    /* 「确定」= 写回表格那一行 **并立刻落盘**。
     *
     * 为什么不再要求「再点一次页面右上角的保存」：那样少点一步就白配了 ——
     * 表现是「明明在弹窗里选了图片消息，发给机器人还是文字」，而且没有任何提示
     * （表格里的值改了、但后端没收到）。这里直接调页面级的保存（social.js 挂在
     * mys.social.save 上），一次点击就生效；保存失败也会把原因显示在页面顶部。
     *
     * 只在**确实写回了**那一行时才保存（id 对不上说明表格被重新读取过，
     * 这时盲目保存会把别的改动一起写进去，不如让用户重开一次弹窗）。 */
    async function sdSave() {
      const d = mys.social.detail;
      const c = (mys.social.commands || [])[d.index];
      if (!c || String(c.id || '') !== String(d.id || '')) {
        mys.social.error = '这条命令已经变过了（表格被重新读取过），请关掉弹窗重开一次';
        d.show = false;
        return;
      }
      c.options = Object.assign({}, d.options);
      c.tpl = { onebot: d.tpl.onebot, qq: d.tpl.qq };
      d.show = false;
      if (typeof mys.social.save === 'function') {
        mys.social.message = '';
        await mys.social.save();          // 内部会把成功/失败写进 mys.social.message / error
      } else {
        mys.social.message = '详细设置已改，记得点「保存」';
      }
    }

    function sdReset() {
      const d = mys.social.detail;
      d.options = fillDefaults(d.schema, {});
      mys.social.message = '';
    }

    function sdProto(name) {
      if (name !== 'onebot' && name !== 'qq') return;
      mys.social.detail.proto = name;
      mys.social.detail.preview = '';      // 切了协议，旧的预览不再对应这一套
      mys.social.detail.previewErr = '';
    }

    /* 示例模板只填**当前这一套** —— 用户往往只想改一边，两套都填反而要删一遍 */
    function sdTplSample() {
      const d = mys.social.detail;
      if (!d.sample) return;
      d.tpl[d.proto] = d.sample;
      mys.social.message = '';
    }

    function sdTplClear() {
      mys.social.detail.tpl[mys.social.detail.proto] = '';
      mys.social.message = '';
    }

    /* 预览：把弹窗里的设置原样发后端真跑一次接口，看模板渲染成什么样。
     * 后端用与保存时同一套清洗规则纠正脏值，所以预览看到的就是真正会发出去的东西。
     * 这套没配模板时后端返回的是接口默认文本 —— 正好顺便展示「留空会发出什么」。
     * 顺带把后端给的 vars（模板变量）与 data（接口原始数据）以 JSON 展示出来，
     * 配模板时就能一眼看出「有哪些字段/属性可以用」。
     *
     * 「预览身份」是绑定类接口（危局 / 防卫战 / 抽卡 / 签到 / 角色）的必要输入：
     * 那些接口查的是「某个 QQ 用户自己绑的米游社账号」，管理页本身没有身份，
     * 留空时后端会自动挑一个已绑定用户，并把实际用的身份回在 j.user 里显示出来。 */
    async function sdPreview() {
      const d = mys.social.detail;
      d.previewing = true;
      d.previewErr = '';
      try {
        const j = await mysPost('/social/preview', {
          api: d.api, cmd: d.cmd, arg: d.arg || '', user: d.user || '',
          options: d.options, tpl: d.tpl,
        });
        if (j.ok) {
          d.preview = (j.preview || {})[d.proto] || j.default || '';
          d.previewEmpty = !!j.empty;
          d.previewUser = String(j.user || '');
          d.previewVarsJson = (j.vars && Object.keys(j.vars).length)
            ? JSON.stringify(j.vars, null, 2) : '';
          d.previewDataJson = j.data ? JSON.stringify(j.data, null, 2) : '';
        } else {
          d.previewErr = j.message || '预览失败';
          d.previewVarsJson = '';
          d.previewDataJson = '';
        }
      } catch (e) {
        d.previewErr = '预览失败：' + e.message;
        d.previewVarsJson = '';
        d.previewDataJson = '';
      }
      d.previewing = false;
    }

    return { sdOpen, sdClose, sdSave, sdReset, sdProto, sdTplSample, sdTplClear, sdPreview, sdCount };
  },
};
