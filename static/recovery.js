/* Opt-in local recovery; no network calls and no third-party storage. */
(function (root) {
  const DAY = 86400000;
  class IndexedStore {
    async transaction(mode, callback) {
      const db = await new Promise((resolve, reject) => {
        const request = indexedDB.open('civic-pulse-recovery', 1);
        request.onupgradeneeded = () => request.result.createObjectStore('records');
        request.onsuccess = () => resolve(request.result);
        request.onerror = () => reject(request.error);
        request.onblocked = () => reject(new Error('Close other Civic Pulse tabs to use local recovery.'));
      });
      try {
        return await new Promise((resolve, reject) => {
          const transaction = db.transaction('records', mode);
          const request = callback(transaction.objectStore('records'));
          transaction.oncomplete = () => resolve(request.result);
          transaction.onerror = () => reject(transaction.error);
          transaction.onabort = () => reject(transaction.error || new Error('Local recovery was not saved.'));
        });
      } finally { db.close(); }
    }
    get(key) { return this.transaction('readonly', s => s.get(key)); }
    set(key, value) { return this.transaction('readwrite', s => s.put(value, key)); }
    delete(key) { return this.transaction('readwrite', s => s.delete(key)); }
  }
  function validReceipts(value) {
    if (!Array.isArray(value) || value.length > 1000) throw new Error('Invalid receipt file.');
    return value.map(r => {
      if (!r || !/^report-[a-f0-9]{32}$/.test(r.report_id) ||
          !/^(CP-\d{6,}|cluster-[a-z0-9-]+)$/.test(r.ticket_id) ||
          typeof r.token !== 'string' || r.token.length < 16 || r.token.length > 128) throw new Error('Invalid tracking receipt.');
      return {report_id: r.report_id, ticket_id: r.ticket_id, token: r.token};
    });
  }
  class Recovery {
    constructor(store = new IndexedStore(), now = () => Date.now()) { this.store = store; this.now = now; this.queue = Promise.resolve(); }
    enqueue(operation) { const result = this.queue.then(operation); this.queue = result.catch(() => {}); return result; }
    async save(key, value) {
      if (!['draft', 'receipts'].includes(key)) throw new Error('Invalid recovery record.');
      if (key === 'receipts') value = validReceipts(value);
      await this.enqueue(() => this.store.set(key, {value, expires: this.now() + 7 * DAY}));
    }
    async load(key) {
      return this.enqueue(async () => {
        const record = await this.store.get(key);
        if (!record) return null;
        if (!Number.isFinite(record.expires) || record.expires <= this.now()) { await this.store.delete(key); return null; }
        return key === 'receipts' ? validReceipts(record.value) : record.value;
      });
    }
    async discardDraft() { await this.enqueue(() => this.store.delete('draft')); }
    async clear() { await this.enqueue(async () => { await this.store.delete('draft'); await this.store.delete('receipts'); }); }
  }
  const api = {Recovery, validReceipts};
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else root.CivicRecovery = api;
})(globalThis);
