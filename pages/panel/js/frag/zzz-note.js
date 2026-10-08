/* 绝区零 · 实时便笺 / 体力（plugins/_vendor/miyoushe/web/frag/zzz-note.js）
 * 查询 + 数据已是后端归一化好的 view；模板在 frag/zzz-note.html，样式在 frag/zzz-note.css（nt-* 前缀）。
 * 由 module.js 在 setup 里调 window.MysNote.init/setup。
 */
window.MysNote = {
  init(mys) {
    mys.note = {
      loading: false, done: false, error: '', raw: '', view: null, loaded: false,
    };
  },

  setup(ctx, mys, hooks) {
    const mysGet = (path, params) => ctx.get('' + path, params);
    const roleInfo = () => (hooks && hooks.mysRoleInfo ? hooks.mysRoleInfo() : null);

    /* 秒数 → "3小时20分钟"（接口给剩余秒数） */
    function ntDur(sec) {
      const s = Number(sec) || 0;
      if (s <= 0) return '';
      const d = Math.floor(s / 86400);
      const h = Math.floor((s % 86400) / 3600);
      const m = Math.floor((s % 3600) / 60);
      if (d > 0) return d + '天' + (h ? ' ' + h + '小时' : '');
      if (h > 0) return h + '小时' + (m ? ' ' + m + '分' : '');
      return m + '分钟';
    }

    /* 时间戳（秒）→ "2026.10.06 04:00" */
    function ntStamp(sec) {
      const t = Number(sec) || 0;
      if (!t) return '';
      const d = new Date(t * 1000);
      if (isNaN(d.getTime())) return '';
      const p = (n) => String(n).padStart(2, '0');
      return d.getFullYear() + '.' + p(d.getMonth() + 1) + '.' + p(d.getDate())
        + ' ' + p(d.getHours()) + ':' + p(d.getMinutes());
    }

    /* 比例 → 百分比宽度 */
    function ntPct(cur, max) {
      const c = Number(cur) || 0, m = Number(max) || 0;
      if (m <= 0) return '0%';
      return Math.min(100, Math.round((c / m) * 100)) + '%';
    }

    async function mysNote() {
      const ri = roleInfo();
      if (!ri) { mys.note.error = '请先选择角色'; return; }
      mys.note.loading = true;
      mys.note.error = '';
      mys.note.view = null;
      try {
        const j = await mysGet('/zzz/note', {
          uid: ri.uid, server: ri.server, account_id: mys.current,
        });
        if (j.ok) {
          mys.note.raw = JSON.stringify(j.data, null, 2);
          mys.note.view = j.view;
          mys.note.done = true;
        } else mys.note.error = j.message || '查询实时便笺失败';
      } catch (e) { mys.note.error = '查询实时便笺失败：' + e.message; }
      mys.note.loaded = true;
      mys.note.loading = false;
    }

    function mysNoteReset() {
      mys.note.loading = false;
      mys.note.done = false;
      mys.note.error = '';
      mys.note.raw = '';
      mys.note.view = null;
      mys.note.loaded = false;
    }

    return { mysNote, mysNoteReset, ntDur, ntStamp, ntPct };
  },
};
