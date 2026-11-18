const storageKey = 'embedding-top10-event-reviews-v1';
const form = document.querySelector('#filters');
const events = document.querySelector('#events');
const status = document.querySelector('#status');
const error = document.querySelector('#error');
const previous = document.querySelector('#previous');
const next = document.querySelector('#next');
const pageLabel = document.querySelector('#page');
let page = 1, total = 0, requestId = 0, applied = new URLSearchParams(new FormData(form));

function el(tag, text, cls) {
  const n = document.createElement(tag);
  if (text !== undefined) n.textContent = text;
  if (cls) n.className = cls;
  return n;
}
async function get(url) {
  const response = await fetch(url), data = await response.json();
  if (!response.ok) throw new Error(data.error || 'Request failed');
  return data;
}
function readReviews(key = storageKey) {
  const value = localStorage.getItem(key);
  if (!value) return {};
  const parsed = JSON.parse(value);
  if (!parsed || Array.isArray(parsed) || typeof parsed !== 'object') throw new Error('Saved reviews are invalid.');
  return parsed;
}
function readSafely(key = storageKey) {
  try { return readReviews(key); } catch (err) { error.textContent = 'Could not read saved reviews: ' + err.message; return null; }
}
function selectedFrom(review) {
  if (Array.isArray(review.related_events)) return review.related_events;
  if (review.outcome === 'found_top10' && review.matched_event_uid) return [{event_uid: review.matched_event_uid, rank: review.candidate_rank || '', title: ''}];
  return [];
}
function updateProgress() {
  const saved = readSafely(); if (!saved) return;
  const reviews = Object.values(saved), count = name => reviews.filter(r => r.outcome === name).length;
  const hits = count('found_top10'), misses = count('in_collection_not_top10');
  const exact = reviews.filter(r => selectedFrom(r).some(e => e.judgment === 'likely_exact')).length;
  const possible = reviews.filter(r => selectedFrom(r).some(e => e.judgment === 'possible')).length;
  document.querySelector('#review-progress').textContent = `${reviews.length} events reviewed · ${hits} with related suggestions (${exact} likely exact; ${possible} possible) · ${misses} missed in collection · ${count('not_in_collection')} missing from collection · ${count('not_on_platform')} not on platform · ${count('unsure')} unsure`;
}
function reviewLabel(eventId) {
  const r = readSafely()?.[eventId];
  return r ? 'Reviewed' : 'Not reviewed';
}
function makeReview(event, suggestions, onSaved, reviewKey = storageKey) {
  const stored = readSafely(reviewKey)?.[event.event_uid] || {};
  const wrapper = el('div', undefined, 'comparison');
  const heading = el('h3', 'Review suggested events');
  wrapper.append(heading, el('p', 'Choose likely exact match or possible match for each relevant suggestion. Leave the rest unselected.', 'comparison-intro'));
  if (suggestions.length < 10) wrapper.append(el('p', `Only ${suggestions.length} suggestions were saved for this event.`, 'notice'));
  const review = el('form', undefined, 'review-form');
  const list = el('div', undefined, 'candidates');
  const savedIds = new Set(selectedFrom(stored).map(item => item.event_uid));
  const inputs = [];
  const unavailable = [...savedIds].filter(id => !suggestions.some(candidate => candidate.candidate_uid === id));
  if (unavailable.length) wrapper.append(el('p', 'Previously selected events not in these suggestions: ' + unavailable.join(', ') + '. Saving this review will replace those selections.', 'notice'));
  for (const candidate of suggestions) {
    const row = el('div', undefined, 'candidate'), text = el('div');
    const title = el('strong', candidate.candidate_title || candidate.candidate_uid);
    const meta = el('small', `#${candidate.rank} · similarity ${Number(candidate.score).toFixed(3)} · ${candidate.candidate_uid}`);
    const label = el('label', 'Match judgment'), select = el('select');
    for (const [value, description] of [['', 'Not selected'], ['likely_exact', 'Likely exact match'], ['possible', 'Possible match']]) {
      const option = el('option', description); option.value = value; select.append(option);
    }
    const previous = selectedFrom(stored).find(item => item.event_uid === candidate.candidate_uid);
    if (previous && !['likely_exact', 'possible'].includes(previous.judgment)) {
      const legacy = el('option', 'Previously related — choose a category'); legacy.value = 'unclassified'; select.append(legacy);
    }
    select.value = previous ? (previous.judgment || 'unclassified') : '';
    label.append(select); text.append(title, meta); row.append(text, label); list.append(row);
    inputs.push({select, candidate});
  }
  if (!suggestions.length) list.append(el('p', 'No suggested events were saved.'));
  review.append(list);
  const none = el('fieldset');
  none.append(el('legend', 'If none is related, what did you find?'));
  const options = [
    ['in_collection_not_top10', 'A related event is in our collection, but was not suggested'],
    ['not_in_collection', 'A related event is on Polymarket US, but is missing from our collection'],
    ['not_on_platform', 'No related event exists on Polymarket US (verified)'],
    ['unsure', 'I haven’t found one / not sure yet'],
  ];
  for (const [value, text] of options) {
    const label = el('label', undefined, 'radio-option'), input = el('input');
    input.type = 'radio'; input.name = 'missing_reason'; input.value = value; input.checked = stored.outcome === value;
    label.append(input, el('span', text)); none.append(label);
  }
  review.append(none);
  const lookup = el('section', undefined, 'lookup'), lookupTitle = el('strong', 'Find an event in the collection');
  const lookupLabel = el('label', 'Search Polymarket US event title or ID'), lookupInput = el('input');
  lookupInput.type = 'search'; lookupInput.placeholder = 'At least 2 characters'; lookupLabel.append(lookupInput);
  const searchButton = el('button', 'Search collection'); searchButton.type = 'button';
  const lookupStatus = el('p'), lookupResults = el('div', undefined, 'lookup-results'); lookupStatus.setAttribute('role', 'status');
  const chosen = el('p');
  let missingEvent = stored.outcome === 'in_collection_not_top10' ? (stored.missed_event_uid || stored.matched_event_uid || (/^POLYUS:[^\s]+$/.test(stored.evidence || '') ? stored.evidence : '')) : '';
  function showChosen() { chosen.textContent = missingEvent ? 'Selected collected event: ' + missingEvent : ''; }
  showChosen();
  searchButton.addEventListener('click', async () => {
    lookupResults.replaceChildren(); lookupStatus.textContent = 'Searching…';
    try {
      const data = await get('/api/collection-events?' + new URLSearchParams({search: lookupInput.value}));
      lookupStatus.textContent = `${data.events.length} events found` + (data.truncated ? ' (first 100 shown; refine your search)' : '');
      for (const candidate of data.events) {
        const row = el('div', undefined, 'lookup-result'), text = el('div');
        text.append(el('strong', candidate.title || 'Untitled event'), el('small', candidate.event_uid + ' · ' + (candidate.in_retrieval_pool ? 'in candidate pool' : 'outside candidate pool')));
        const select = el('button', 'Select'); select.type = 'button';
        select.addEventListener('click', () => {
          const suggestion = inputs.find(item => item.candidate.candidate_uid === candidate.event_uid);
          if (suggestion) { if (!suggestion.select.querySelector('option[value="unclassified"]')) { const option = el('option', 'Choose a match category'); option.value = 'unclassified'; suggestion.select.append(option); } suggestion.select.value = 'unclassified'; missingEvent = ''; lookupStatus.textContent = 'Event is in the suggestions above. Choose its match category.'; }
          else { for (const item of inputs) item.select.value = ''; none.querySelector('input[value="in_collection_not_top10"]').checked = true; missingEvent = candidate.event_uid; lookupStatus.textContent = 'Selected as an event missed by the suggestions.'; }
          showChosen(); sync();
        });
        row.append(text, select); lookupResults.append(row);
      }
    } catch (err) { lookupStatus.textContent = err.message; }
  });
  lookup.append(lookupTitle, lookupLabel, searchButton, lookupStatus, lookupResults, chosen); review.append(lookup);
  const evidenceLabel = el('label', 'Evidence or external URL (optional)'), evidence = el('input');
  evidence.type = 'text'; evidence.value = stored.evidence || ''; evidenceLabel.append(evidence);
  const notesLabel = el('label', 'Notes (optional)'), notes = el('textarea'); notes.value = stored.notes || ''; notesLabel.append(notes);
  const actions = el('div', undefined, 'actions'), save = el('button', 'Save review', 'primary'), remove = el('button', 'Remove review');
  save.type = 'submit'; remove.type = 'button'; actions.append(save, remove);
  const feedback = el('p', undefined, 'feedback'); feedback.setAttribute('role', 'status');
  review.append(evidenceLabel, notesLabel, actions, feedback);
  function sync() { none.hidden = inputs.some(item => item.select.value); inputs.forEach(item => { item.select.closest('.candidate').dataset.selected = item.select.value ? 'true' : 'false'; }); }
  inputs.forEach(item => item.select.addEventListener('change', sync)); sync();
  review.addEventListener('submit', ev => {
    ev.preventDefault(); feedback.className = 'feedback';
    const selected = inputs.filter(item => item.select.value).map(item => ({event_uid: item.candidate.candidate_uid, title: item.candidate.candidate_title || '', rank: item.candidate.rank, score: item.candidate.score, judgment: item.select.value}));
    if (selected.some(item => item.judgment === 'unclassified')) { feedback.className = 'feedback error'; feedback.textContent = 'Choose likely exact or possible for each selected event before saving.'; return; }
    const reason = review.querySelector('input[name="missing_reason"]:checked')?.value;
    if (!selected.length && !reason) { feedback.className = 'feedback error'; feedback.textContent = 'Select related events or choose what you found when none is related.'; return; }
    if (!selected.length && reason === 'in_collection_not_top10' && !missingEvent) { feedback.className = 'feedback error'; feedback.textContent = 'Search the collection and select the related event.'; return; }
    try {
      const all = readReviews(reviewKey);
      all[event.event_uid] = {kalshi_uid: event.event_uid, kalshi_title: event.title || '', outcome: selected.length ? 'found_top10' : reason, related_events: selected, missed_event_uid: selected.length ? '' : (reason === 'in_collection_not_top10' ? missingEvent : ''), evidence: evidence.value.trim(), notes: notes.value.trim(), reviewed_at: new Date().toISOString()};
      localStorage.setItem(reviewKey, JSON.stringify(all)); updateProgress(); onSaved(); feedback.textContent = 'Review saved.';
    } catch (err) { feedback.className = 'feedback error'; feedback.textContent = 'Could not save review: ' + err.message; }
  });
  remove.addEventListener('click', () => {
    try { const all = readReviews(reviewKey); delete all[event.event_uid]; localStorage.setItem(reviewKey, JSON.stringify(all)); inputs.forEach(item => {item.select.value = '';}); none.querySelectorAll('input').forEach(input => {input.checked = false;}); missingEvent = ''; showChosen(); evidence.value = ''; notes.value = ''; sync(); updateProgress(); onSaved(); feedback.textContent = 'Review removed.'; }
    catch (err) { feedback.className = 'feedback error'; feedback.textContent = 'Could not remove review: ' + err.message; }
  });
  wrapper.append(review); return wrapper;
}
async function load() {
  const id = ++requestId; error.textContent = ''; status.textContent = 'Loading events…';
  try {
    const params = new URLSearchParams(applied); params.set('page', page);
    const data = await get('/api/events?' + params); if (id !== requestId) return;
    total = data.total; events.replaceChildren();
    if (!data.events.length) events.append(el('p', 'No events match these filters. Try a broader search or category.'));
    for (const event of data.events) {
      const card = el('article', undefined, 'event-card'), head = el('div', undefined, 'event-head'), info = el('div');
      info.append(el('div', event.title || 'Untitled event', 'event-title'));
      const volume = event.total_volume === null ? 'Volume unavailable' : event.total_volume.toLocaleString(undefined, {maximumFractionDigits: 2}) + ' contracts traded';
      info.append(el('div', `${event.category || 'Unclassified'} · ${volume}${event.markets_with_volume < event.market_count ? ' (partial)' : ''}`, 'meta'), el('small', event.event_uid));
      const actions = el('div', undefined, 'event-actions'), badge = el('span', reviewLabel(event.event_uid), 'reviewed'), button = el('button', 'Review suggestions');
      button.type = 'button'; button.setAttribute('aria-expanded', 'false'); actions.append(badge, button); head.append(info, actions); card.append(head);
      button.addEventListener('click', async () => {
        const old = card.querySelector('.comparison');
        if (old) { old.remove(); button.textContent = 'Review suggestions'; button.setAttribute('aria-expanded', 'false'); return; }
        button.disabled = true;
        try { const data = await get('/api/matches?' + new URLSearchParams({event_uid: event.event_uid})); card.append(makeReview(event, data.matches.slice(0, 10), () => {badge.textContent = reviewLabel(event.event_uid);})); button.textContent = 'Close review'; button.setAttribute('aria-expanded', 'true'); }
        catch (err) { error.textContent = 'Could not load suggestions: ' + err.message; } finally { button.disabled = false; }
      });
      events.append(card);
    }
    status.textContent = `${total.toLocaleString()} events found`; pageLabel.textContent = `Page ${page} of ${Math.max(1, Math.ceil(total / 50))}`;
    previous.disabled = page <= 1; next.disabled = page * 50 >= total;
  } catch (err) { if (id === requestId) { status.textContent = 'Could not load events.'; error.textContent = err.message; } }
}
document.querySelector('#export-event-reviews').addEventListener('click', () => {
  const all = readSafely(); if (!all) return;
  const fields = ['kalshi_uid','kalshi_title','outcome','related_event_ids','related_event_ranks','related_event_judgments','missed_event_uid','evidence','notes','reviewed_at'];
  const rows = [fields, ...Object.values(all).map(r => { const related = selectedFrom(r); return [r.kalshi_uid, r.kalshi_title, r.outcome, JSON.stringify(related.map(x => x.event_uid)), JSON.stringify(related.map(x => x.rank)), JSON.stringify(related.map(x => x.judgment || 'unclassified')), r.missed_event_uid || (r.outcome === 'in_collection_not_top10' ? r.matched_event_uid || (/^POLYUS:[^\s]+$/.test(r.evidence || '') ? r.evidence : '') : ''), r.evidence, r.notes, r.reviewed_at]; })];
  const csv = rows.map(row => row.map(v => '"' + String(v ?? '').replaceAll('"', '""') + '"').join(',')).join('\r\n');
  const a = el('a'); a.href = URL.createObjectURL(new Blob([csv], {type:'text/csv;charset=utf-8'})); a.download = 'event-reviews.csv'; a.click(); setTimeout(() => URL.revokeObjectURL(a.href), 1000);
});
form.addEventListener('submit', ev => { ev.preventDefault(); applied = new URLSearchParams(new FormData(form)); page = 1; load(); });
previous.addEventListener('click', () => { if (page > 1) { page--; load(); } });
next.addEventListener('click', () => { if (page * 50 < total) { page++; load(); } });
window.addEventListener('storage', updateProgress); window.addEventListener('focus', updateProgress); updateProgress();
(async () => { try { const data = await get('/api/filters'), category = document.querySelector('#category'); for (const name of data.categories) { const option = el('option', name); option.value = 'category:' + name; category.append(option); } if (data.snapshot_ms !== null) document.querySelector('#notice').append(' Latest source snapshot: ' + new Date(data.snapshot_ms).toLocaleString() + '. Individual markets may be older.'); await load(); } catch (err) { status.textContent = 'Could not load report.'; error.textContent = err.message; } })();

const setsKey = 'embedding-evaluation-sets-v1', sampleForm = document.querySelector('#sample-form');
const samplePicker = document.querySelector('#sample-picker'), samplePanel = document.querySelector('#sample-review');
const sampleError = document.querySelector('#sample-error');
let sampleIndex = 0;
function readSets() {
  const data = JSON.parse(localStorage.getItem(setsKey) || '{}');
  if (!data || Array.isArray(data) || typeof data !== 'object') throw Error('Saved evaluation sets are invalid.');
  return data;
}
function refreshSets(selected) {
  const sets = readSets(), ids = Object.keys(sets).sort((a,b) => sets[b].created_at.localeCompare(sets[a].created_at));
  samplePicker.replaceChildren();
  for (const id of ids) {
    const set = sets[id], option = el('option', `${new Date(set.created_at).toLocaleString()} · ${set.method === 'volume' ? 'Volume weighted' : 'Uniform'} · ${sampleCategoryLabel(set.category)}`);
    option.value = id; samplePicker.append(option);
  }
  document.querySelector('#sample-picker-label').hidden = !ids.length;
  if (ids.length) { samplePicker.value = sets[selected] ? selected : ids[0]; showSample(); }
  else samplePanel.replaceChildren();
}
function sampleCategoryLabel(category) { return ({'all': 'All categories', 'sports': 'Sports', 'non-sports': 'Non-sports'})[category] || category; }
function sampleReviewsKey(id) { return 'embedding-evaluation-reviews-v1:' + id; }
function showSample() {
  sampleError.textContent = '';
  try {
    const id = samplePicker.value, set = readSets()[id]; if (!set) { samplePanel.replaceChildren(); return; }
    const all = readReviews(sampleReviewsKey(id)), item = set.events[sampleIndex];
    samplePanel.replaceChildren();
    const panel = el('div', undefined, 'sample-panel');
    const reviewed = set.events.filter(event => all[event.event_uid]).length;
    panel.append(el('strong', `Event ${sampleIndex + 1} of ${set.events.length} · ${reviewed} reviewed`));
    const progress = el('p');
    function showEvaluationCounts(reviews) {
      const records = set.events.map(event => reviews[event.event_uid]).filter(Boolean);
      const hits = records.filter(r => r.outcome === 'found_top10').length;
      const misses = records.filter(r => r.outcome === 'in_collection_not_top10').length;
      const other = records.length - hits - misses;
      const exact = records.filter(r => selectedFrom(r).some(e => e.judgment === 'likely_exact')).length;
      const possible = records.filter(r => selectedFrom(r).some(e => e.judgment === 'possible')).length;
      progress.textContent = `${hits} with related suggestions (${exact} likely exact; ${possible} possible) · ${misses} missed in collection · ${other} missing or unsure`; 
    }
    showEvaluationCounts(all); panel.append(progress);
    panel.append(el('p', `Seed ${set.seed} · ${set.eligible_events.toLocaleString()} eligible events · ${set.method === 'volume' ? 'volume weighted' : 'uniform random'} · ${sampleCategoryLabel(set.category)}`, 'notice'));
    panel.append(el('div', item.title || 'Untitled event', 'event-title'), el('small', item.event_uid));
    panel.append(el('p', `${item.category || 'Unclassified'} · ${item.total_volume === null ? 'Volume unavailable' : item.total_volume.toLocaleString(undefined, {maximumFractionDigits: 2}) + ' contracts traded'}${item.markets_with_volume < item.market_count ? ' (partial)' : ''}`));
    const review = makeReview(item, item.candidates, () => {
      const reviews = readReviews(sampleReviewsKey(id)), n = set.events.filter(event => reviews[event.event_uid]).length;
      panel.querySelector('strong').textContent = `Event ${sampleIndex + 1} of ${set.events.length} · ${n} reviewed`; showEvaluationCounts(reviews);
    }, sampleReviewsKey(id));
    panel.append(review);
    panel.append(el('small', 'Save the review before moving to another event.'));
    const nav = el('nav', undefined, 'sample-nav'); nav.setAttribute('aria-label', 'Evaluation events');
    const back = el('button', 'Previous event'), forward = el('button', 'Next event'), download = el('button', 'Download evaluation CSV');
    back.type = forward.type = download.type = 'button'; back.disabled = sampleIndex === 0; forward.disabled = sampleIndex === set.events.length - 1;
    back.addEventListener('click', () => { sampleIndex--; showSample(); });
    forward.addEventListener('click', () => { sampleIndex++; showSample(); });
    download.addEventListener('click', () => exportSample(id, set));
    nav.append(back, forward, download); panel.append(nav); samplePanel.append(panel);
  } catch (err) { sampleError.textContent = 'Could not open the saved set: ' + err.message; }
}
function exportSample(id, set) {
  try {
    const reviews = readReviews(sampleReviewsKey(id));
    const fields = ['set_id','created_at','seed','method','category','eligible_events','snapshot_ms','sample_order','kalshi_uid','kalshi_title','total_volume','markets_with_volume','market_count','suggestions','outcome','related_events','missed_event_uid','evidence','notes','reviewed_at'];
    const rows = [fields, ...set.events.map((event, index) => {
      const review = reviews[event.event_uid] || {};
      return [id,set.created_at,set.seed,set.method,set.category,set.eligible_events,set.snapshot_ms,index+1,event.event_uid,event.title,event.total_volume,event.markets_with_volume,event.market_count,JSON.stringify(event.candidates),review.outcome||'unreviewed',JSON.stringify(selectedFrom(review)),review.missed_event_uid||'',review.evidence||'',review.notes||'',review.reviewed_at||''];
    })];
    const csv = rows.map(row => row.map(value => '"' + String(value ?? '').replaceAll('"','""') + '"').join(',')).join('\r\n');
    const a = el('a'); a.href = URL.createObjectURL(new Blob([csv], {type:'text/csv;charset=utf-8'})); a.download = 'evaluation-' + id + '.csv'; a.click(); setTimeout(() => URL.revokeObjectURL(a.href),1000);
  } catch (err) { sampleError.textContent = 'Could not export the set: ' + err.message; }
}
sampleForm.addEventListener('submit', async ev => {
  ev.preventDefault(); sampleError.textContent = '';
  const button = sampleForm.querySelector('button'); button.disabled = true; button.textContent = 'Creating set…';
  try {
    const seed = crypto.getRandomValues(new Uint32Array(1))[0], params = new URLSearchParams(new FormData(sampleForm)); params.set('seed', seed);
    const set = await get('/api/sample?' + params), sets = readSets();
    const id = crypto.randomUUID(); set.created_at = new Date().toISOString(); sets[id] = set;
    localStorage.setItem(setsKey, JSON.stringify(sets)); sampleIndex = 0; refreshSets(id);
  } catch (err) { sampleError.textContent = 'Could not create the set: ' + err.message; }
  finally { button.disabled = false; button.textContent = 'Create a set of 20'; }
});
samplePicker.addEventListener('change', () => { sampleIndex = 0; showSample(); });
try { refreshSets(); } catch (err) { sampleError.textContent = 'Could not read saved sets: ' + err.message; }
