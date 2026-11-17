(() => {
  const key = 'event-review-v1';
  const fields = ['kalshi_uid', 'polyus_uid', 'relationship', 'event_group', 'split', 'reviewer', 'notes'];
  const status = document.getElementById('storage-status');
  const reviewer = document.getElementById('reviewer');
  const split = document.getElementById('split');
  let state = {reviews: {}, reviewer: '', split: ''};
  let storageAvailable = true;
  function persist() {
    if (!storageAvailable) return false;
    try { localStorage.setItem(key, JSON.stringify(state)); }
    catch (_) { storageAvailable = false; status.textContent = 'Browser storage is unavailable. Download your CSV before leaving this page.'; }
    return storageAvailable;
  }
  try {
    const saved = localStorage.getItem(key);
    if (saved) {
      const parsed = JSON.parse(saved);
      if (!parsed || typeof parsed.reviews !== 'object' || Array.isArray(parsed.reviews)) throw Error('Invalid saved reviews');
      state = parsed;
    }
    reviewer.value = state.reviewer || '';
    split.value = state.split || '';
  } catch (_) { storageAvailable = false; status.textContent = 'Saved reviews could not be read. Download any new reviews before leaving this page.'; }
  const rowKey = (q, c) => JSON.stringify([q, c]);
  const controls = [...document.querySelectorAll('.review')];
  function refresh() {
    controls.forEach(box => {
      const record = state.reviews[rowKey(box.dataset.query, box.dataset.candidate)];
      box.querySelector('.review-relation').value = record?.relationship || '';
      box.querySelector('.review-notes').value = record?.notes || '';
      box.querySelector('.review-status').textContent = record ? 'Review recorded' : '';
    });
  }
  function save(box) {
    const query = box.dataset.query, candidate = box.dataset.candidate;
    const relationship = box.querySelector('.review-relation').value;
    const notes = box.querySelector('.review-notes').value;
    const k = rowKey(query, candidate);
    if (!relationship && !notes) delete state.reviews[k];
    else {
      const old = state.reviews[k] || {};
      state.reviews[k] = {kalshi_uid: query, polyus_uid: candidate, relationship,
        event_group: old.event_group || query, split: split.value, reviewer: reviewer.value, notes};
    }
    const saved = persist();
    box.querySelector('.review-status').textContent = saved ? 'Saved in browser' : 'In memory only — download CSV';
  }
  controls.forEach(box => {
    const relation = box.querySelector('.review-relation');
    const notes = box.querySelector('.review-notes');
    relation.addEventListener('change', () => {
      if (relation.value === 'no_match_found' && !notes.value) notes.value = 'No match found among displayed suggestions';
      save(box);
    });
    notes.addEventListener('input', () => save(box));
  });
  reviewer.addEventListener('input', () => { state.reviewer = reviewer.value; persist(); });
  split.addEventListener('change', () => { state.split = split.value; persist(); });
  document.getElementById('export-reviews').addEventListener('click', () => {
    const rows = Object.values(state.reviews).filter(r => r.relationship);
    const quote = value => '"' + String(value ?? '').replaceAll('"', '""') + '"';
    const csv = [fields.join(','), ...rows.map(row => fields.map(f => quote(row[f])).join(','))].join('\r\n') + '\r\n';
    const url = URL.createObjectURL(new Blob([csv], {type: 'text/csv;charset=utf-8'}));
    const a = document.createElement('a'); a.href = url; a.download = 'event-reviews.csv'; a.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
    status.textContent = `Downloaded ${rows.length} review(s). Keep the CSV as a backup.` + (storageAvailable ? '' : ' Browser storage is unavailable.');
  });
  function parseCSV(text) {
    const rows = []; let row = [], field = '', quoted = false;
    for (let i = 0; i < text.length; i++) {
      const c = text[i];
      if (quoted) {
        if (c === '"' && text[i + 1] === '"') { field += '"'; i++; }
        else if (c === '"') quoted = false;
        else field += c;
      } else if (c === '"' && field === '') quoted = true;
      else if (c === ',') { row.push(field); field = ''; }
      else if (c === '\n' || c === '\r') {
        if (c === '\r' && text[i + 1] === '\n') i++;
        row.push(field); field = ''; rows.push(row); row = [];
      } else field += c;
    }
    if (quoted) throw Error('Unclosed quoted field');
    if (field || row.length) { row.push(field); rows.push(row); }
    return rows;
  }
  document.getElementById('import-reviews').addEventListener('change', async event => {
    const file = event.target.files[0]; if (!file) return;
    try {
      const rows = parseCSV((await file.text()).replace(/^\uFEFF/, ''));
      const header = rows.shift() || [];
      if (!fields.every(f => header.includes(f))) throw Error('CSV must contain the event review columns');
      let added = 0, skipped = 0;
      for (const values of rows) {
        if (values.every(v => !v)) continue;
        const record = Object.fromEntries(fields.map(f => [f, values[header.indexOf(f)] || '']));
        if (!record.kalshi_uid || !record.relationship) { skipped++; continue; }
        const k = rowKey(record.kalshi_uid, record.polyus_uid);
        if (state.reviews[k]) { skipped++; continue; }
        state.reviews[k] = record; added++;
      }
      const saved = persist(); refresh();
      status.textContent = `Imported ${added} review(s); skipped ${skipped} existing or incomplete row(s).` + (saved ? '' : ' Browser storage unavailable: download CSV before leaving.');
    } catch (error) { status.textContent = `Could not import CSV: ${error.message}`; }
    event.target.value = '';
  });
  document.querySelectorAll('button[data-rail]').forEach(button => button.addEventListener('click', () => {
    const rail = document.getElementById(button.dataset.rail);
    rail.scrollBy({left: Number(button.dataset.direction) * (rail.querySelector('.candidate')?.offsetWidth || 370), behavior: matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth'});
  }));
  refresh();
})();
