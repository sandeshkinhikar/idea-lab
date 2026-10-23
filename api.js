(() => {
  'use strict';
  const API = {
    async request(path, options = {}) {
      const headers = { ...(options.body ? { 'Content-Type': 'application/json' } : {}), ...(options.headers || {}) };
      let response;
      try { response = await fetch(path, { ...options, headers }); }
      catch (e) { throw new Error(`Cannot reach the API (${e.message}). Check that the GeoCool server is running.`); }
      if (!response.ok) {
        let detail = `${response.status} ${response.statusText}`;
        try { const payload = await response.clone().json(); detail = payload.detail || payload.error || detail; } catch (_) {}
        throw new Error(detail);
      }
      const type = response.headers.get('content-type') || '';
      return type.includes('application/json') ? response.json() : response;
    },
    get(path) { return this.request(path); },
    post(path, body = {}) { return this.request(path, { method: 'POST', body: JSON.stringify(body) }); },
    put(path, body = {}) { return this.request(path, { method: 'PUT', body: JSON.stringify(body) }); },
    patch(path, body = {}) { return this.request(path, { method: 'PATCH', body: JSON.stringify(body) }); },
    async download(path, filename) {
      const response = await this.request(path);
      const blob = await response.blob();
      const link = document.createElement('a'); link.href = URL.createObjectURL(blob); link.download = filename || path.split('/').pop();
      document.body.append(link); link.click(); link.remove(); setTimeout(() => URL.revokeObjectURL(link.href), 1000);
    }
  };
  window.GeoAPI = API;
  window.escapeHTML = (value) => String(value ?? '').replace(/[&<>"']/g, c => ({ '&':'&amp;', '<':'&lt;', '>':'&gt;', '"':'&quot;', "'":'&#39;' }[c]));
  window.num = (value, digits = 2) => Number.isFinite(Number(value)) ? Number(value).toFixed(digits) : '—';
  window.toast = (message, type = 'info') => {
    const region = document.getElementById('toast-region');
    if (!region) return;
    const item = document.createElement('div'); item.className = `toast ${type}`; item.setAttribute('role', type === 'error' ? 'alert' : 'status');
    item.textContent = message; region.append(item); setTimeout(() => item.remove(), 5500);
  };
  window.actionRun = async (button, fn, success = 'Completed.') => {
    if (!button || button.disabled) return;
    const old = button.innerHTML; button.disabled = true; button.setAttribute('aria-busy', 'true');
    button.innerHTML = '<span class="loading"><span class="spinner" aria-hidden="true"></span> Working…</span>';
    try { const value = await fn(); toast(success, 'success'); return value; }
    catch (error) { toast(error.message || 'Action failed.', 'error'); return null; }
    finally { button.disabled = false; button.removeAttribute('aria-busy'); button.innerHTML = old; }
  };
})();
