/* Behavior additions for individual complaints; existing visual layout is retained. */
const recovery = new CivicRecovery.Recovery();
const remember = document.getElementById('remember-local');
let recoverTimer;

function saveSessionReceipts() {
  try { sessionStorage.setItem('civic-receipts', JSON.stringify(receipts)); }
  catch { statusEl.textContent = 'Receipt kept in memory only. Export it before closing this page.'; }
}
async function persistReceipts() {
  saveSessionReceipts();
  if (remember.checked) await recovery.save('receipts', receipts);
}
function draftSnapshot() {
  return {fields: Object.fromEntries(['transcript', 'phone', 'lat', 'lon', 'category-override'].map(id => [id, document.getElementById(id).value])),
    photo: document.getElementById('image-file').files[0] || null,
    inputSource, locationSource, locationAccuracy, pendingKey, pendingFingerprint};
}
async function saveDraft() {
  if (!remember.checked) throw new Error('Enable local recovery first. This saves your description, photo, location and tracking receipts on this device for up to 7 days.');
  const draft = draftSnapshot();
  if (draft.photo?.size > 8 * 1024 * 1024) throw new Error('Choose a photo under 8 MiB before saving a draft.');
  await recovery.save('draft', draft);
}
async function restoreDraft() {
  const draft = await recovery.load('draft');
  if (!draft?.fields) { statusEl.textContent = 'No unexpired saved draft was found.'; return; }
  for (const id of ['transcript', 'phone', 'lat', 'lon', 'category-override']) {
    if (typeof draft.fields[id] === 'string') document.getElementById(id).value = draft.fields[id];
  }
  inputSource = draft.inputSource === 'voice_transcript' ? 'voice_transcript' : 'typed';
  locationSource = ['manual', 'map', 'device'].includes(draft.locationSource) ? draft.locationSource : 'manual';
  locationAccuracy = Number.isFinite(draft.locationAccuracy) ? draft.locationAccuracy : null;
  pendingKey = typeof draft.pendingKey === 'string' ? draft.pendingKey : null;
  pendingFingerprint = typeof draft.pendingFingerprint === 'string' ? draft.pendingFingerprint : null;
  const transfer = new DataTransfer();
  if (draft.photo instanceof Blob) transfer.items.add(new File([draft.photo], draft.photo.name || 'saved-photo', {type: draft.photo.type, lastModified: draft.photo.lastModified || Date.now()}));
  document.getElementById('image-file').files = transfer.files;
  document.getElementById('upload-label').textContent = draft.photo?.name || 'Select Image File';
  document.getElementById('location-consent').checked = false;
  document.getElementById('analysis-consent').checked = false;
  setPinFromCoords(Number(latEl.value), Number(lonEl.value));
  statusEl.textContent = 'Draft restored, including its saved photo. Check the location and confirm consent again. Nothing has been submitted.';
}
window.afterComplaintSaved = async function(receipt) {
  clearTimeout(recoverTimer);
  try {
    await persistReceipts();
    if (remember.checked) await recovery.discardDraft();
  } catch { statusEl.textContent = 'Complaint saved on the server, but local recovery failed. Export your tracking receipts now.'; }
};
document.getElementById('save-draft').addEventListener('click', async () => {
  try { await saveDraft(); statusEl.textContent = 'Draft saved on this device for up to 7 days, including the selected photo.'; }
  catch (err) { statusEl.textContent = err.message; }
});
document.getElementById('restore-draft').addEventListener('click', () => restoreDraft().catch(err => { statusEl.textContent = err.message; }));
document.getElementById('report-form').addEventListener('input', () => {
  if (!remember.checked) return;
  clearTimeout(recoverTimer);
  recoverTimer = setTimeout(() => saveDraft().catch(err => { statusEl.textContent = err.message; }), 500);
});
remember.addEventListener('change', async () => {
  try {
    if (remember.checked) { await persistReceipts(); await saveDraft(); }
    else { clearTimeout(recoverTimer); await recovery.clear(); statusEl.textContent = 'Persistent recovery cleared. Current-session receipts remain available for export.'; }
  } catch (err) { remember.checked = false; statusEl.textContent = 'Local recovery unavailable: ' + err.message; }
});
document.getElementById('forget-local').addEventListener('click', async () => {
  clearTimeout(recoverTimer); remember.checked = false;
  try { await recovery.clear(); receipts = []; saveSessionReceipts(); renderTickets(); statusEl.textContent = 'Saved local drafts and tracking receipts cleared. Server complaints remain intact.'; }
  catch (err) { statusEl.textContent = 'Could not clear saved data: ' + err.message; }
});
document.getElementById('export-receipts').addEventListener('click', () => {
  const url = URL.createObjectURL(new Blob([JSON.stringify({format:'civic-receipts-v1', receipts}, null, 2)], {type:'application/json'}));
  const link = document.createElement('a'); link.href = url; link.download = 'civic-tracking-receipts.json'; link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
});
document.getElementById('import-receipts').addEventListener('change', async event => {
  try {
    const file = event.target.files[0];
    if (!file) return;
    if (file.size > 1024 * 1024) throw new Error('Receipt file is too large.');
    const imported = JSON.parse(await file.text());
    if (imported.format !== 'civic-receipts-v1') throw new Error('Unsupported receipt format.');
    const clean = CivicRecovery.validReceipts(imported.receipts);
    receipts = CivicRecovery.validReceipts([...new Map([...receipts, ...clean].map(r => [r.report_id, r])).values()]);
    await reconcileReceipts(); await persistReceipts(); await loadTickets(); statusEl.textContent = 'Tracking receipts imported. No complaints were submitted.';
  } catch (err) { statusEl.textContent = err.message; }
  finally { event.target.value = ''; }
});
document.getElementById('recover-complaint').addEventListener('click', async () => {
  try {
    const number = document.getElementById('recover-number').value.trim();
    const token = document.getElementById('recover-token').value.trim();
    if (!/^CP-\d{6,}$/.test(number) || token.length < 16) throw new Error('Enter a complaint number and its private tracking token.');
    const response = await fetch(`/api/complaints/${number}`, {headers:{Authorization:`Bearer ${token}`}});
    if (!response.ok) throw new Error('Complaint not found or tracking token invalid.');
    const report = await response.json();
    receipts = [...receipts.filter(r => r.report_id !== report.report_id), {report_id:report.report_id, ticket_id:report.ticket_id, token}];
    document.getElementById('recover-token').value = '';
    await persistReceipts(); await loadTickets(); statusEl.textContent = `Recovered ${number}.`;
  } catch (err) { statusEl.textContent = err.message; }
});

async function reportApi(reportId, suffix = '', options = {}) {
  const token = currentRole === 'authority' ? reviewerToken.value : receipts.find(r => r.report_id === reportId)?.token;
  const headers = new Headers(options.headers || {}); headers.set('Authorization', `Bearer ${token || ''}`);
  const response = await fetch(`/api/reports/${reportId}${suffix}`, {...options, headers});
  const body = await response.json();
  if (!response.ok) throw new Error(typeof body.detail === 'string' ? body.detail : JSON.stringify(body.detail));
  return body;
}
async function reconcileReceipts() {
  // Migration may give a previously grouped report a new individual complaint number.
  receipts = await Promise.all(receipts.map(async receipt => {
    try {
      const response = await fetch(`/api/reports/${receipt.report_id}`, {headers:{Authorization:`Bearer ${receipt.token}`}});
      if (response.ok) return {...receipt, ticket_id:(await response.json()).ticket_id};
    } catch { /* Keep offline/temporarily unavailable receipts for a later retry. */ }
    return receipt;
  }));
  saveSessionReceipts();
}
window.mountComplaintTools = async function(reports) {
  for (const original of reports) {
    const report = await reportApi(original.report_id);
    const followups = await reportApi(report.report_id, '/followups');
    const panel = document.createElement('section');
    panel.className = 'border border-slate-700 rounded-xl p-4 space-y-3';
    panel.innerHTML = `<h4 class="font-bold">${escapeHtml(report.complaint_number)} · ${escapeHtml(report.status)}</h4>
      <p class="text-xs">${report.similarity.same_reason_count_30d} other complaints with the same keyword reason in 30 days; ${report.similarity.nearby_similar_count_30d} within 150 metres. ${report.similarity.many_similar_complaints ? 'Multiple nearby complaints warrant reviewer attention.' : ''}</p>
      <p class="text-xs">${escapeHtml(report.similarity.notice)}</p>`;
    for (const candidate of report.similarity.candidates) {
      const row = document.createElement('div'); row.className = 'text-xs space-x-2';
      row.textContent = `${candidate.complaint_number} · approximately ${candidate.distance_m} m · ${candidate.reason_hint} `;
      for (const [label, assessment] of [['Possibly related','possibly_related'], ['Different issue','different']]) {
        const button = document.createElement('button'); button.className = 'underline text-cyan-300'; button.textContent = label;
        button.addEventListener('click', async () => {
          try { await reportApi(report.report_id, '/similar-confirmations', {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({complaint_number:candidate.complaint_number,assessment})}); statusEl.textContent = 'Opinion recorded; both complaints remain separate.'; button.disabled = true; }
          catch (err) { statusEl.textContent = err.message; }
        }); row.append(button);
      } panel.append(row);
    }
    const history = document.createElement('div'); history.className = 'text-xs space-y-2';
    history.innerHTML = `<h5 class="font-bold">Complaint history</h5>` + report.events.map(e => `<p>${escapeHtml(e.created_at)} · ${escapeHtml(e.status)} · ${escapeHtml(e.note)}</p>`).join(''); panel.append(history);
    for (const resolution of followups.resolutions) {
      const item = document.createElement('div'); item.className = 'text-xs'; item.textContent = `Resolution: ${resolution.note}`;
      if (resolution.photo) {
        const button = document.createElement('button'); button.textContent = ' View repair photo'; button.className = 'underline text-cyan-300';
        button.addEventListener('click', async () => {
          try {
            const token = currentRole === 'authority' ? reviewerToken.value : receipts.find(r => r.report_id === report.report_id)?.token;
            const response = await fetch(resolution.photo.url, {headers:{Authorization:`Bearer ${token}`}});
            if (!response.ok) throw new Error('Repair photo unavailable.');
            const url = URL.createObjectURL(await response.blob()); evidenceUrls.push(url);
            const image = document.createElement('img'); image.src = url; image.alt = 'Reviewer resolution evidence'; image.className='w-full rounded-xl'; button.replaceWith(image);
          } catch (err) { statusEl.textContent = err.message; }
        }); item.append(button);
      } panel.append(item);
    }
    const note = document.createElement('textarea'); note.className='block w-full rounded bg-slate-950 border border-slate-700 p-2 text-xs'; note.maxLength=2000; note.setAttribute('aria-label','Complaint follow-up note'); note.placeholder='Explain the resolution or why the issue needs reopening.';
    const pending = followups.reopen_requests.find(r => r.decision === 'PENDING');
    const refresh = async () => { await loadTickets(); await openDetailsModal(report.ticket_id); };
    if (currentRole === 'authority' && report.status === 'DISPATCHED') {
      const file = document.createElement('input'); file.type='file'; file.accept='image/jpeg,image/png,image/webp'; file.setAttribute('aria-label','Optional repair photo');
      const button=document.createElement('button'); button.textContent='Resolve with evidence'; button.className='underline text-emerald-300';
      button.addEventListener('click', async () => { button.disabled=true; try {const form=new FormData();form.append('note',note.value);if(file.files[0])form.append('file',file.files[0]);await reportApi(report.report_id,'/resolution',{method:'POST',body:form});await refresh();}catch(err){statusEl.textContent=err.message;}finally{button.disabled=false;} });
      panel.append(note,file,button);
    } else if (currentRole !== 'authority' && report.status === 'RESOLVED' && !pending) {
      const button=document.createElement('button');button.textContent='Request reopening';button.className='underline text-cyan-300';
      button.addEventListener('click',async()=>{button.disabled=true;try{await reportApi(report.report_id,'/reopen-requests',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({note:note.value})});await refresh();}catch(err){statusEl.textContent=err.message;}finally{button.disabled=false;}});panel.append(note,button);
    }
    for (const reopen of followups.reopen_requests) {
      const item=document.createElement('p');item.className='text-xs';item.textContent=`Reopen request: ${reopen.note} · ${reopen.decision}${reopen.decision_note ? ' · '+reopen.decision_note : ''}`;panel.append(item);
      if(currentRole==='authority' && reopen.decision==='PENDING') {
        panel.append(note);
        for(const decision of ['APPROVED','REJECTED']) {
          const button=document.createElement('button');button.textContent=decision==='APPROVED'?'Approve reopening':'Reject reopening';button.className='underline text-cyan-300 mr-3';
          button.addEventListener('click',async()=>{button.disabled=true;try{await reportApi(report.report_id,`/reopen-requests/${reopen.request_id}`,{method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify({decision,note:note.value})});await refresh();}catch(err){statusEl.textContent=err.message;}finally{button.disabled=false;}});panel.append(button);
        }
      }
    }
    document.getElementById('modal-reports-list').append(panel);
  }
};
(async () => {
  try {
    const saved = await recovery.load('receipts');
    const draft = await recovery.load('draft');
    if (saved || draft) { remember.checked=true; if(saved){ receipts=[...new Map([...receipts,...saved].map(r=>[r.report_id,r])).values()]; } statusEl.textContent='Saved local recovery is available. Restore the draft explicitly; no complaint has been submitted.'; }
    await reconcileReceipts(); await loadTickets();
  } catch { statusEl.textContent='Local recovery is unavailable. Current-session reporting still works.'; }
})();
