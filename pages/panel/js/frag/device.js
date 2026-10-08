/* 设备指纹配置（plugins/_vendor/miyoushe/web/frag/device.js）
 * ------------------------------------------------------------------
 * 模板在 frag/device.html，样式在 frag/device.css（dv-* 前缀）。
 * 由 module.js 在 setup 里调 window.MysDevice.init/setup。
 *
 * 干什么：绝区零的**角色类接口**（拥有代理人 / 角色详情 / 资料总览 / 实时便笺）
 * 带设备指纹风控（retcode=10041），请求不像「这个账号登录过的手机」就一律拒。
 * 把手机端导出的一串设备 JSON 粘进来 → 换一个 device_fp → 再登记到账号，
 * 之后查角色就不被拦了（危局 / 防卫战本来就不受影响，与这里无关）。
 *
 * 接口（后端 src/device_routes.py）：
 *   GET  /device/state                    → { default, accounts:[{account_id,nickname,device}], fields, sample }
 *   POST /device/save     {scope,account_id,raw}  解析七字段 → 调官方 getFp 换指纹 → 存
 *   POST /device/register {scope,account_id}      用账号 Cookie 把设备登记到该账号
 *   POST /device/delete   {scope,account_id}
 *   POST /device/test     {account_id}            试一次 /avatar/basic，看还返不返 10041
 *
 * 两份数据的关系：**账号专属优先，没有就用默认那份**（后端 for_account 就是这个顺序），
 * 所以「先配一份默认的全用，某个账号不行再单独配」是可以直接用的。
 */
window.MysDevice = {
  init(mys) {
    mys.device = {
      loading: false, done: false,
      error: '', message: '',
      help: false,           // 顶部说明块：默认收起，点「说明」展开
      tab: 'default',        // default（所有账号共用）/ account（按米游社账号）
      raw: '',               // 粘进来的整段 JSON
      default: null,         // 默认那份（device 对象或 null）
      accounts: [],          // [{account_id, nickname, device}]
      aid: '',               // 「按账号」页签下选中的账号
      saving: false, registering: false, testing: false,
      test: { retcode: 0, count: 0, message: '' },
    };
  },

  setup(ctx, mys) {
    const API = '';
    const get = (p, params) => ctx.get(API + p, params);
    const post = (p, body) => ctx.post(API + p, body);

    async function dvLoad() {
      mys.device.loading = true;
      mys.device.error = '';
      const j = await get('/device/state');
      mys.device.loading = false;
      if (j && j.ok) {
        mys.device.default = j.default || null;
        mys.device.accounts = j.accounts || [];
        mys.device.fields = j.fields || [];
        mys.device.sample = j.sample || '';
        if (!mys.device.raw && j.sample) mys.device.raw = j.sample;
        if (!mys.device.aid && mys.device.accounts.length) {
          mys.device.aid = mys.device.accounts[0].account_id;
        }
        mys.device.done = true;
      } else {
        mys.device.error = (j && j.message) || '读取设备配置失败';
      }
    }

    function dvTab(name) {
      if (name !== 'default' && name !== 'account') return;
      mys.device.tab = name;
      mys.device.message = '';
      mys.device.error = '';
      mys.device.test = { retcode: 0, count: 0, message: '' };
    }

    /* 当前这份（按页签）：账号页签 → 选中账号那份；默认页签 → 默认那份 */
    function dvCurDevice() {
      if (mys.device.tab === 'account') {
        const row = mys.device.accounts.find(a => a.account_id === mys.device.aid);
        return (row && row.device) || null;
      }
      return mys.device.default;
    }

    function dvHas() {
      return !!dvCurDevice();
    }

    /* 把粘进来的那段整理成好看的多行 JSON；顺便把「缺哪个字段」说清楚 */
    function dvFmt() {
      mys.device.message = '';
      mys.device.error = '';
      try {
        const obj = JSON.parse(mys.device.raw);
        mys.device.raw = JSON.stringify(obj, null, 2);
        const miss = (mys.device.fields || []).map(f => f.key).filter(k => !obj[k]);
        mys.device.message = miss.length ? '这些字段是空的：' + miss.join('、') : '格式没问题，七个字段都在';
      } catch (e) {
        mys.device.error = '这不是合法 JSON：检查一下是不是复制全了';
      }
    }

    function dvUseSample() {
      mys.device.raw = mys.device.sample || '';
    }

    async function dvSave() {
      mys.device.saving = true;
      mys.device.message = '';
      mys.device.error = '';
      const scope = mys.device.tab;
      const j = await post('/device/save', {
        scope: scope,
        account_id: scope === 'account' ? mys.device.aid : '',
        raw: mys.device.raw,
      });
      mys.device.saving = false;
      if (j && j.ok) {
        mys.device.message = j.message || '已保存';
        await dvLoad();
      } else {
        mys.device.error = (j && j.message) || '保存失败';
      }
    }

    async function dvRegister() {
      mys.device.registering = true;
      mys.device.message = '';
      mys.device.error = '';
      const scope = mys.device.tab;
      const j = await post('/device/register', {
        scope: scope,
        account_id: mys.device.aid,
      });
      mys.device.registering = false;
      if (j && j.ok) mys.device.message = j.message || '已登记';
      else mys.device.error = (j && j.message) || '登记失败';
    }

    async function dvDelete() {
      mys.device.message = '';
      mys.device.error = '';
      const scope = mys.device.tab;
      const j = await post('/device/delete', {
        scope: scope,
        account_id: scope === 'account' ? mys.device.aid : '',
      });
      if (j && j.ok) {
        mys.device.message = '已删除';
        await dvLoad();
      } else {
        mys.device.error = (j && j.message) || '删除失败';
      }
    }

    async function dvTest() {
      mys.device.testing = true;
      mys.device.test = { retcode: 0, count: 0, message: '' };
      const j = await post('/device/test', { account_id: mys.device.aid });
      mys.device.testing = false;
      if (j && j.ok) {
        mys.device.test = { retcode: j.retcode, count: j.count, message: j.message };
      } else {
        mys.device.error = (j && j.message) || '自检失败';
      }
    }

    /* 说明块开关（页面上一进来就是一大段文字太吵，默认收起） */
    function dvHelp() {
      mys.device.showHelp = !mys.device.showHelp;
    }

    function dvTime(ts) {
      if (!ts) return '';
      const d = new Date(Number(ts) * 1000);
      const p = n => String(n).padStart(2, '0');
      return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
    }

    function dvShort(text, n) {
      const s = String(text || '');
      return s.length > n ? s.slice(0, n) + '…' : s;
    }

    return {
      dvLoad, dvTab, dvSave, dvRegister, dvDelete, dvTest,
      dvFmt, dvUseSample, dvCurDevice, dvHas, dvTime, dvShort, dvHelp,
    };
  },
};
