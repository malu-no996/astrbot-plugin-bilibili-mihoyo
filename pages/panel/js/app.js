/* 米游社面板 · 框架 shim（astrbot-plugin-miyoho/pages/panel/js/app.js）
 * ------------------------------------------------------------------
 * 原 malu_qq_bot 配置页 web/js/core.js 的 AstrBot Page 移植替身。
 * 原框架提供 AdminApp（模块注册）/ ctx（get/post/notice/shared/expose），
 * 各 frag js 与 module.js 只依赖这层 —— 本文件把它们 1:1 复刻，
 * 底层从「fetch /admin/api/*」换成 AstrBotPluginPage bridge：
 *   - ctx.get(path, params)  → bridge.apiGet(endpoint, params)
 *   - ctx.post(path, body)   → bridge.apiPost(endpoint, body)
 *   - endpoint 是插件内相对路径（不带 /astrbot_plugin_miyoho 前缀），
 *     所以所有 JS/HTML 里的 /admin/api/miyoushe 前缀在移植时被剥掉。
 * 响应契约：
 *   - 后端业务失败走 error_response（HTTP 4xx/5xx）→ bridge reject → 这里
 *     折成 { ok:false, message }，保住原前端的 `j.ok` 判断口径；
 *   - 后端若返回 {status:"ok",data} 信封（老版本 bridge 行为）→ 解包成 data；
 *   - 资源改写：后端下发的 /miyoho-asset/… 统一改写成 ./assets/zzz/…
 *     （Page 的 <img> 只能走相对路径；登录后的 dashboard cookie 会自动鉴权）。
 * 页面只有一个模块（mys），所以原框架的页签路由/模块开关/全局状态加载全部省略；
 * module.js setup 返回的 { mysTab }（进页签动作）在 mounted 后照常执行。
 */
(function () {
  'use strict';

  const { createApp, reactive, ref, onMounted } = Vue;

  // ---------------- 模块注册表（原 AdminApp.register） ----------------
  const modules = [];

  function register(mod) {
    if (!mod || !mod.id) throw new Error('AdminApp.register 需要 id');
    if (modules.some(m => m.id === mod.id)) throw new Error('模块重复注册：' + mod.id);
    modules.push(mod);
    return mod;
  }

  // ---------------- 启动应用 ----------------
  function boot() {
    const mountEl = document.getElementById('app');
    if (!mountEl) { console.error('[AdminApp] 找不到 #app'); return; }

    createApp({
      setup() {
        const toast = reactive({ show: false, text: '', kind: 'ok' });
        let toastTimer = null;
        const ready = ref(false);

        function notice(text, kind = 'ok') {
          toast.text = text; toast.kind = kind; toast.show = true;
          clearTimeout(toastTimer);
          toastTimer = setTimeout(() => { toast.show = false; }, 4000);
        }
        function fmt(ts) {
          if (!ts) return '';
          const d = new Date(Number(ts) * 1000);
          return isNaN(d.getTime()) ? '' : d.toLocaleString('zh-CN');
        }
        function short(text, limit) {
          text = String(text == null ? '' : text);
          return text.length <= limit ? text : text.slice(0, limit) + '…';
        }

        // ---------------- bridge 请求层 ----------------
        const bridge = window.AstrBotPluginPage;

        /* 后端下发的资源记号 → Page 相对路径（见 core/asset_cache.py 的 sync_to_page）。
         * 同时兜底迁移残留的旧 nonebot 路由 /admin/api/miyoushe/zzz/asset/<hash> ——
         * 这种前缀既不是 /miyoho-asset/ 也不被 isAssetUrl 识别，会当成相对路径打到
         * dashboard 根域名 404（音擎图标就是这个情况）。 */
        function fixAssets(v) {
          if (typeof v === 'string') {
            if (v.indexOf('/miyoho-asset/') >= 0)
              return v.split('/miyoho-asset/').join('./assets/zzz/');
            const m = v.match(/\/zzz\/asset\/([0-9a-f]+\.[a-z]+)$/);
            if (m) return './assets/zzz/' + m[1];
            return v;
          }
          if (Array.isArray(v)) {
            for (let i = 0; i < v.length; i++) v[i] = fixAssets(v[i]);
            return v;
          }
          if (v && typeof v === 'object') {
            for (const k of Object.keys(v)) v[k] = fixAssets(v[k]);
            return v;
          }
          return v;
        }

        /* ---------------- 动态图片代取（⚠️ 不能用相对路径 <img>） ----------------
         * 沙箱 iframe（sandbox 无 allow-same-origin）是不透明源：<img> 请求**不带
         * cookie**，而 Page 静态目录 / /api/plug 的图片路由都要鉴权 → 全 401 裂图
         * （静态 HTML 里的图能显示是因为服务端改写时注入了 asset_token，动态拼的不行）。
         * 所以接口数据里的图片走 apiGet 代取（父页面有身份）：zzz/asset64/<名> 返回
         * base64 → 这里转 blob URL，在数据交给 frag 之前原位换好（进 Vue 前完成，
         * 没有响应式问题）。按 URL 缓存整个会话复用。 */
        const PLACEHOLDER_IMG = 'data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7';
        const assetUrls = new Map();    // './assets/zzz/<名>' → blob URL
        const assetPending = new Map(); // 去重并发

        function isAssetUrl(v) {
          return typeof v === 'string' && v.indexOf('./assets/') === 0;
        }

        /* key 形如 ./assets/zzz/<hash>.png 或 ./assets/icon/SRANK.png 或
           ./assets/skill_bar.png —— 统一走 base64 路由（沙箱 iframe 不带 cookie，
           相对路径 401）。zzz 子目录走 asset64（读后台缓存源文件），其余走
           pageasset（读 pages/panel/assets 下的静态镜像）。 */
        function assetEndpoint(key) {
          const rel = key.slice('./assets/'.length);
          return rel.indexOf('zzz/') === 0
            ? 'zzz/asset64/' + rel.slice('zzz/'.length)
            : 'zzz/pageasset/' + rel;
        }

        function loadAsset(key) {
          if (assetUrls.has(key)) return Promise.resolve(assetUrls.get(key));
          if (assetPending.has(key)) return assetPending.get(key);
          const p = (async () => {
            try {
              const j = norm(await bridge.apiGet(assetEndpoint(key), null));
              if (!j || !j.b64) throw new Error('asset64 返回异常');
              const bin = atob(j.b64);
              const bytes = new Uint8Array(bin.length);
              for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
              const url = URL.createObjectURL(new Blob([bytes], { type: j.mime || 'image/png' }));
              assetUrls.set(key, url);
              return url;
            } catch (e) {
              return PLACEHOLDER_IMG;  // 单图失败不炸整个查询
            } finally {
              assetPending.delete(key);
            }
          })();
          assetPending.set(key, p);
          return p;
        }

        async function resolveAssetsDeep(v) {
          if (Array.isArray(v)) {
            await Promise.all(v.map(async (val, i) => {
              if (isAssetUrl(val)) v[i] = await loadAsset(val);
              else await resolveAssetsDeep(val);
            }));
          } else if (v && typeof v === 'object') {
            await Promise.all(Object.keys(v).map(async (k) => {
              const val = v[k];
              if (isAssetUrl(val)) v[k] = await loadAsset(val);
              else await resolveAssetsDeep(val);
            }));
          }
          return v;
        }

        /* 模板里『现生成』的静态图（稀有度方徽章 ./assets/icon/SRANK.png、
           技能条 ./assets/skill_bar.png、属性图标等）不出现在接口响应里，
           resolveAssetsDeep 抓不到；用 MutationObserver 在 <img> 插入 / src
           变化时异步换成 blob（走 pageasset 路由）。只认 ./assets/ 开头的相对路径，
           blob: 换成后不再匹配，不会死循环。 */
        function rewriteDomAssets(root) {
          if (!root || !root.querySelectorAll) return;
          const imgs = root.querySelectorAll('img[src^="./assets/"]');
          imgs.forEach((img) => {
            const key = img.getAttribute('src');
            if (key && key.indexOf('./assets/') === 0) {
              loadAsset(key).then((url) => {
                if (img.getAttribute('src') === key) img.src = url;
              });
            }
          });
        }

        let _assetObserver = null;
        function observeDomAssets() {
          if (_assetObserver) return;
          const target = document.getElementById('app') || document.body;
          if (!target) return;
          rewriteDomAssets(target);
          _assetObserver = new MutationObserver((muts) => {
            for (const m of muts) {
              if (m.type === 'attributes' && m.attributeName === 'src' && m.target && m.target.tagName === 'IMG') {
                const key = m.target.getAttribute('src');
                if (key && key.indexOf('./assets/') === 0) {
                  loadAsset(key).then((url) => { if (m.target.getAttribute('src') === key) m.target.src = url; });
                }
              } else if (m.addedNodes) {
                m.addedNodes.forEach((n) => { if (n.nodeType === 1) rewriteDomAssets(n); });
              }
            }
          });
          _assetObserver.observe(target, { childList: true, subtree: true, attributes: true, attributeFilter: ['src'] });
        }

        /* bridge 返回值归一成原框架口径：{ok, message, ...} / 任意业务 JSON。 */
        function norm(v) {
          if (v && typeof v === 'object') {
            if (v.status === 'ok' && v.data !== undefined) return fixAssets(v.data);
            if (v.status === 'error') {
              return { ok: false, message: v.message || '请求失败' };
            }
            return fixAssets(v);
          }
          return { ok: false, message: '接口返回非 JSON' };
        }

        function cleanEndpoint(path) {
          // bridge 不允许 endpoint 带 query —— 原前端若有拼串写法在这里拆开
          let ep = String(path || '');
          const params = {};
          const qi = ep.indexOf('?');
          if (qi >= 0) {
            new URLSearchParams(ep.slice(qi + 1)).forEach((v, k) => { params[k] = v; });
            ep = ep.slice(0, qi);
          }
          while (ep.startsWith('/')) ep = ep.slice(1);
          return [ep, params];
        }

        async function get(path, params) {
          const [ep, qs] = cleanEndpoint(path);
          if (params) {
            for (const [k, v] of Object.entries(params)) {
              // 与原 core.js 同口径：空串 / null 不进 query
              if (v !== '' && v != null) qs[k] = v;
            }
          }
          try {
            const out = norm(await bridge.apiGet(ep, qs));
            return await resolveAssetsDeep(out);  // 图片换 blob URL（失败原样返回）
          } catch (e) {
            return { ok: false, message: (e && e.message) || '请求失败' };
          }
        }

        async function post(path, bodyData) {
          const [ep] = cleanEndpoint(path);
          try {
            const out = norm(await bridge.apiPost(ep, bodyData || {}));
            return await resolveAssetsDeep(out);
          } catch (e) {
            return { ok: false, message: (e && e.message) || '请求失败' };
          }
        }
        const request = get; // 原框架的 request(url, options) 没人用，留个别名防万一

        // ---------------- 模块 setup：收集状态与方法 ----------------
        const stateBag = {};
        const methodBag = {};
        const ctx = {
          notice, fmt, short, get, post, request, ready,
          shared: stateBag,
          expose(state, methods) {
            Object.assign(stateBag, state || {});
            Object.assign(methodBag, methods || {});
          },
        };

        const actions = {};
        for (const m of modules) {
          if (typeof m.setup === 'function') {
            try { m._actions = m.setup(ctx) || null; }
            catch (e) { console.error('[AdminApp] setup 失败：' + m.id, e); }
          }
          if (m._actions && typeof m._actions === 'object') Object.assign(actions, m._actions);
        }

        onMounted(async () => {
          // 等父页面下发初始上下文再触发「进页签」动作（原框架 tabClick 的替身）
          try { await bridge.ready(); } catch (e) { /* 拿不到上下文也照常跑 */ }
          // 沙箱 iframe 不带 cookie：模板里现生成的 ./assets/* 静态图异步换 blob
          observeDomAssets();
          ready.value = true;
          for (const fn of Object.values(actions)) {
            if (typeof fn === 'function') {
              try { fn(ctx); }
              catch (e) { console.error('[AdminApp] 页签加载失败', e); }
            }
          }
        });

        return Object.assign({ toast, ready, notice, fmt, short }, stateBag, methodBag);
      },
    }).mount('#app');
  }

  window.AdminApp = { register, boot, _modules: modules };

  // 所有 <script>（含各 frag js 与 module.js）按顺序解析完毕后才启动
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', boot);
  } else {
    boot();
  }
})();
