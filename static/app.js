const API_BASE = 'https://voxpop-ixt9.onrender.com';
const latEl = document.getElementById('lat');
const lonEl = document.getElementById('lon');
const transcriptEl = document.getElementById('transcript');
const statusEl = document.getElementById('status');
const coordLabel = document.getElementById('coord-label');
const pin = document.getElementById('pin');
const mockToggle = document.getElementById('mock-toggle');
const modal = document.getElementById('dispatch-modal');
const modalMemo = document.getElementById('dispatch-memo');
let tickets = [];

let mockTickets = [{
  ticket_id: 'cluster-uuid-8801',
  title: 'Severe Structural Pothole Grid Near Main Entrance',
  category: 'Road Hazard',
  composite_severity: 'Critical',
  active_report_count: 4,
  ai_impact_synthesis: 'Four unique citizens have verified a dangerous road depression near the primary gate. Aggregated inputs report frequent evasive maneuvers into opposing traffic lanes, indicating severe collision risks.',
  representative_lat: 10.0625,
  representative_lon: 76.5312,
  status: 'OPEN',
  generated_dispatch_memo: 'PENDING_APPROVAL'
}];

function isMockMode() {
  return mockToggle.checked;
}

function coordsToPixel(lat, lon) {
  return { x: ((lon - 76.5295) / 0.0034) * 500, y: 320 - ((lat - 10.0607) / 0.0030) * 320 };
}

function pixelToCoords(x, y) {
  return { lat: 10.0607 + ((320 - y) / 320) * 0.0030, lon: 76.5295 + (x / 500) * 0.0034 };
}

function setPinFromCoords(lat, lon) {
  const point = coordsToPixel(lat, lon);
  pin.setAttribute('cx', Math.max(0, Math.min(500, point.x)));
  pin.setAttribute('cy', Math.max(0, Math.min(320, point.y)));
  coordLabel.textContent = `${Number(lat).toFixed(4)}, ${Number(lon).toFixed(4)}`;
}

function escapeHtml(value) {
  return String(value).replace(/[&<>"']/g, char => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
  }[char]));
}

function renderMapNodes() {
  const group = document.getElementById('map-nodes');
  group.innerHTML = tickets.map(ticket => {
    const p = coordsToPixel(ticket.representative_lat, ticket.representative_lon);
    const hot = ticket.active_report_count > 3;
    return `<g><circle class="${hot ? 'animate-ping' : ''}" cx="${p.x}" cy="${p.y}" r="${10 + ticket.active_report_count * 3}" fill="${hot ? '#ef4444' : '#f97316'}" opacity=".45"></circle><circle cx="${p.x}" cy="${p.y}" r="${6 + ticket.active_report_count}" fill="url(#hot)" stroke="white" stroke-width="2"></circle><text x="${p.x + 14}" y="${p.y - 12}" fill="white" font-size="13" font-weight="800">${ticket.active_report_count}</text></g>`;
  }).join('');
}

function renderTickets() {
  document.getElementById('tickets').innerHTML = tickets.map(ticket => {
    const hot = ticket.active_report_count > 3;
    const safeCategory = escapeHtml(ticket.category);
    const safeTitle = escapeHtml(ticket.title);
    const safeSynthesis = escapeHtml(ticket.ai_impact_synthesis);
    const safeStatus = escapeHtml(ticket.status);
    return `<article class="rounded-3xl border ${hot ? 'border-rose-400/70 bg-rose-950/30' : 'border-white/10 bg-slate-950/70'} p-5 shadow-2xl">
      <div class="flex items-start justify-between gap-3"><div><p class="text-sm font-black ${hot ? 'text-rose-200' : 'text-cyan-200'}">[${safeCategory}] ${ticket.active_report_count} Active Claims Combined</p><h3 class="mt-2 text-2xl font-black">${safeTitle}</h3></div><span class="rounded-full ${hot ? 'bg-rose-500 animate-pulse' : 'bg-amber-400'} px-3 py-1 text-xs font-black text-slate-950">${ticket.composite_severity}</span></div>
      <p class="mt-4 text-slate-300">${safeSynthesis}</p>
      <div class="mt-4 grid grid-cols-3 gap-2 text-xs text-slate-400"><span>Lat ${ticket.representative_lat.toFixed(4)}</span><span>Lon ${ticket.representative_lon.toFixed(4)}</span><span>${safeStatus}</span></div>
      <button data-dispatch="${ticket.ticket_id}" class="mt-4 rounded-xl bg-white px-4 py-2 text-sm font-black text-slate-950">Generate Dispatch</button>
    </article>`;
  }).join('');
  document.querySelectorAll('[data-dispatch]').forEach(button => button.addEventListener('click', () => dispatchTicket(button.dataset.dispatch)));
}

function setTickets(nextTickets) {
  tickets = nextTickets;
  renderTickets();
  renderMapNodes();
}

async function loadTickets() {
  if (isMockMode()) {
    setTickets(mockTickets);
    return;
  }
  const response = await fetch(`${API_BASE}/api/tickets`);
  setTickets(await response.json());
}

function mockSubmit(payload) {
  const ticket = mockTickets[0];
  ticket.active_report_count += 1;
  ticket.ai_impact_synthesis = `${ticket.active_report_count} unique citizens have verified a dangerous road hazard hotspot near the selected coordinate. Aggregated reports indicate recurring public-safety impact requiring rapid municipal triage.`;
  ticket.composite_severity = 'Critical';
  statusEl.textContent = `Mock cluster ${ticket.ticket_id} updated to ${ticket.active_report_count} active claims.`;
  return ticket;
}

async function submitReport(event) {
  event.preventDefault();
  const payload = {
    reporter_phone: document.getElementById('phone').value,
    transcript_text: transcriptEl.value,
    latitude: Number(latEl.value),
    longitude: Number(lonEl.value)
  };
  if (isMockMode()) {
    mockSubmit(payload);
    setTickets(mockTickets);
    return;
  }
  const response = await fetch(`${API_BASE}/api/grievances/submit`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload)
  });
  const ticket = await response.json();
  statusEl.textContent = `Cluster ${ticket.ticket_id} updated to ${ticket.active_report_count} active claims.`;
  await loadTickets();
}

async function dispatchTicket(ticketId) {
  let memo;
  if (isMockMode()) {
    const ticket = mockTickets.find(item => item.ticket_id === ticketId);
    memo = `OFFICIAL DISPATCH ORDER - DEPT OF PUBLIC WORKS. Location: Point [${ticket.representative_lat}, ${ticket.representative_lon}]. Urgency: High. Action Required: ${ticket.ai_impact_synthesis}`;
    ticket.generated_dispatch_memo = memo;
    ticket.status = 'DISPATCHED';
    setTickets(mockTickets);
  } else {
    const response = await fetch(`${API_BASE}/api/tickets/${ticketId}/dispatch`, { method: 'POST' });
    const result = await response.json();
    memo = result.generated_dispatch_memo;
    await loadTickets();
  }
  modalMemo.textContent = memo;
  modal.classList.remove('modal-hidden');
}

document.getElementById('mic').addEventListener('click', () => {
  const SpeechRecognition = window.webkitSpeechRecognition || window.SpeechRecognition;
  if (!SpeechRecognition) { statusEl.textContent = 'Speech recognition is not available in this browser.'; return; }
  const recognition = new SpeechRecognition();
  recognition.continuous = false;
  recognition.onresult = (event) => { transcriptEl.value = event.results[0][0].transcript; };
  recognition.start();
});

document.getElementById('map').addEventListener('click', (event) => {
  const rect = event.currentTarget.getBoundingClientRect();
  const x = ((event.clientX - rect.left) / rect.width) * 500;
  const y = ((event.clientY - rect.top) / rect.height) * 320;
  const coords = pixelToCoords(x, y);
  latEl.value = coords.lat.toFixed(6);
  lonEl.value = coords.lon.toFixed(6);
  setPinFromCoords(coords.lat, coords.lon);
});

document.getElementById('report-form').addEventListener('submit', submitReport);
document.getElementById('reset').addEventListener('click', async () => {
  if (isMockMode()) {
    mockTickets[0].active_report_count = 4;
    mockTickets[0].status = 'OPEN';
    mockTickets[0].generated_dispatch_memo = 'PENDING_APPROVAL';
  } else {
    await fetch(`${API_BASE}/api/demo/reset`, { method: 'POST' });
  }
  statusEl.textContent = 'Demo reset to the golden four-report Road Hazard cluster.';
  await loadTickets();
});
document.getElementById('refresh').addEventListener('click', loadTickets);
document.getElementById('close-modal').addEventListener('click', () => modal.classList.add('modal-hidden'));
mockToggle.addEventListener('change', loadTickets);
setPinFromCoords(Number(latEl.value), Number(lonEl.value));
loadTickets();
