// Keep these in sync with fusion/fusion_model.py thresholds
const TDS_SAFE_MAX = 500;
const TURBIDITY_SAFE_MAX = 5;
const PH_SAFE_MIN = 6.5;
const PH_SAFE_MAX = 8.5;

// Gauge display ranges (just for pointer position, not safety logic)
const GAUGE_RANGES = {
  tds: [0, 1000],
  turbidity: [0, 15],
  ph: [0, 14],
  temperature: [0, 45],
  waterLevelDistance: [0, 200],
};

const GAUGE_CIRCUMFERENCE = 2 * Math.PI * 52; // r=52 in the gauge svg

let historyChart = null;

function setGaugeDot(key, value) {
  const gaugeWrap = document.querySelector(`.gauge[data-gauge="${key}"]`);
  if (!gaugeWrap) return;
  const dotEl = gaugeWrap.querySelector('.gauge-dot');
  const fillEl = gaugeWrap.querySelector('.gauge-fill');
  const [min, max] = GAUGE_RANGES[key] || [0, 100];
  let pct = 0;
  if (value != null && !isNaN(value)) {
    pct = (value - min) / (max - min);
    pct = Math.max(0, Math.min(1, pct));
  }
  const degrees = pct * 360;
  if (dotEl) dotEl.setAttribute('transform', `rotate(${degrees} 60 60)`);
  if (fillEl) fillEl.style.strokeDashoffset = String(GAUGE_CIRCUMFERENCE * (1 - pct));
}

function flagCard(cardId, flagId, value, safeMax, unit) {
  const card = document.getElementById(cardId);
  const flag = document.getElementById(flagId);
  card.classList.remove('flag-danger');
  flag.classList.remove('flag-ok');
  flag.textContent = '';

  if (value == null) return;
  if (value > safeMax) {
    card.classList.add('flag-danger');
    flag.textContent = `Above safe limit (${safeMax} ${unit})`;
  } else {
    flag.classList.add('flag-ok');
    flag.textContent = 'Within safe range';
  }
}

function flagRangeCard(cardId, flagId, value, min, max) {
  const card = document.getElementById(cardId);
  const flag = document.getElementById(flagId);
  card.classList.remove('flag-danger');
  flag.classList.remove('flag-ok');
  flag.textContent = '';

  if (value == null) return;
  if (value < min || value > max) {
    card.classList.add('flag-danger');
    flag.textContent = `Outside safe range (${min}–${max})`;
  } else {
    flag.classList.add('flag-ok');
    flag.textContent = 'Within safe range';
  }
}

async function refreshSensorData() {
  try {
    const res = await fetch('/api/latest');
    const data = await res.json();
    const s = data.sensors;

    document.getElementById('tdsValue').textContent = s.tds ?? '—';
    document.getElementById('turbidityValue').textContent = s.turbidity ?? '—';
    document.getElementById('tempValue').textContent = s.temperature ?? '—';
    document.getElementById('phValue').textContent = s.ph ?? '—';
    document.getElementById('waterLevelDistanceValue').textContent = s.water_level_distance_cm ?? '—';

    setDummyButtonState(data.dummy_active);

    // Update the pill's label text without disturbing the status-dot span
    const pill = document.getElementById('sourcePill');
    const label = s.source + (s.timestamp ? ` · ${s.timestamp}` : '');
    let textNode = [...pill.childNodes].find(n => n.nodeType === Node.TEXT_NODE);
    if (textNode) {
      textNode.textContent = label;
    } else {
      pill.appendChild(document.createTextNode(label));
    }

    flagCard('tdsCard', 'tdsFlag', s.tds, TDS_SAFE_MAX, 'ppm');
    flagCard('turbidityCard', 'turbidityFlag', s.turbidity, TURBIDITY_SAFE_MAX, 'NTU');
    flagRangeCard('phCard', 'phFlag', s.ph, PH_SAFE_MIN, PH_SAFE_MAX);

    setGaugeDot('tds', s.tds);
    setGaugeDot('turbidity', s.turbidity);
    setGaugeDot('temperature', s.temperature);
    setGaugeDot('ph', s.ph);
    setGaugeDot('waterLevelDistance', s.water_level_distance_cm);

    updateOverallStatus(s);
  } catch (err) {
    console.error('Failed to fetch sensor data', err);
  }
}

function updateOverallStatus(s) {
  const pill = document.getElementById('overallStatusPill');
  if (!pill) return;
  const label = pill.querySelector('span:last-child') || pill;
  const haveAnyReading = [s.tds, s.turbidity, s.ph, s.temperature, s.water_level_distance_cm].some(v => v != null);

  if (!haveAnyReading) {
    pill.className = 'status-pill';
    setPillText(pill, 'awaiting readings');
    return;
  }

  const flaggedCount = document.querySelectorAll('.reading-card.flag-danger').length;
  if (flaggedCount === 0) {
    pill.className = 'status-pill status-ok';
    setPillText(pill, 'all readings normal');
  } else if (flaggedCount === 1) {
    pill.className = 'status-pill status-warn';
    setPillText(pill, '1 reading out of range');
  } else {
    pill.className = 'status-pill status-danger';
    setPillText(pill, `${flaggedCount} readings out of range`);
  }
}

function setPillText(pill, text) {
  let textNode = [...pill.childNodes].find(n => n.nodeType === Node.TEXT_NODE);
  if (textNode) {
    textNode.textContent = text;
  } else {
    pill.appendChild(document.createTextNode(text));
  }
}

function setDummyButtonState(active) {
  const btn = document.getElementById('dummyToggleBtn');
  btn.classList.toggle('on', !!active);
  btn.textContent = active ? 'Dummy data: on' : 'Dummy data: off';
}

document.getElementById('dummyToggleBtn').addEventListener('click', async () => {
  const btn = document.getElementById('dummyToggleBtn');
  btn.disabled = true;
  try {
    const res = await fetch('/api/toggle-dummy', { method: 'POST' });
    const data = await res.json();
    setDummyButtonState(data.dummy_active);
    await refreshSensorData();
  } catch (err) {
    console.error('Failed to toggle dummy data', err);
  } finally {
    btn.disabled = false;
  }
});

async function refreshHistory() {
  try {
    const res = await fetch('/api/history');
    const history = await res.json();
    latestReadingHistory = history;
    updateChart(history);
    updateReadingsTable(history);
  } catch (err) {
    console.error('Failed to fetch history', err);
  }
}

let latestReadingHistory = [];

function updateReadingsTable(history) {
  const body = document.getElementById('readingsTableBody');
  body.innerHTML = '';
  // Newest first is easier to scan in a table
  [...history].reverse().forEach(h => {
    const tr = document.createElement('tr');
    const time = h.timestamp ? h.timestamp.split(' ')[1] : '—';
    tr.innerHTML = `
      <td>${time}</td>
      <td>${h.tds ?? '—'}</td>
      <td>${h.turbidity ?? '—'}</td>
      <td>${h.ph ?? '—'}</td>
      <td>${h.temperature ?? '—'}</td>
    `;
    body.appendChild(tr);
  });
}

function updateChart(history) {
  const emptyNote = document.getElementById('historyEmptyNote');
  const canvas = document.getElementById('historyChart');
  const chartView = document.getElementById('chartView');
  const tableView = document.getElementById('tableView');

  if (!history || history.length === 0) {
    emptyNote.style.display = 'block';
    chartView.style.display = 'none';
    tableView.style.display = 'none';
    return;
  }
  emptyNote.style.display = 'none';
  // Respect whichever view is currently active
  const tableActive = document.getElementById('tableViewBtn').classList.contains('active');
  chartView.style.display = tableActive ? 'none' : 'block';
  tableView.style.display = tableActive ? 'block' : 'none';

  const labels = history.map(h => h.timestamp ? h.timestamp.split(' ')[1] : '');
  const tds = history.map(h => h.tds);
  const turbidity = history.map(h => h.turbidity);
  const temperature = history.map(h => h.temperature);
  const ph = history.map(h => h.ph);

  if (!historyChart) {
    const ctx = canvas.getContext('2d');
    historyChart = new Chart(ctx, {
      type: 'line',
      data: {
        labels,
        datasets: [
          {
            label: 'TDS (ppm)',
            data: tds,
            borderColor: '#1F6F9F',
            backgroundColor: 'rgba(31, 111, 159, 0.08)',
            tension: 0.35,
            fill: true,
            pointRadius: 0,
            pointHoverRadius: 4,
            borderWidth: 2,
            yAxisID: 'y1',
          },
          {
            label: 'Turbidity (NTU)',
            data: turbidity,
            borderColor: '#B7791F',
            backgroundColor: 'transparent',
            tension: 0.35,
            pointRadius: 0,
            pointHoverRadius: 4,
            borderWidth: 2,
            yAxisID: 'y',
          },
          {
            label: 'Temperature (°C)',
            data: temperature,
            borderColor: '#2F855A',
            backgroundColor: 'transparent',
            tension: 0.35,
            pointRadius: 0,
            pointHoverRadius: 4,
            borderWidth: 2,
            yAxisID: 'y',
          },
          {
            label: 'pH',
            data: ph,
            borderColor: '#C0392B',
            backgroundColor: 'transparent',
            tension: 0.35,
            pointRadius: 0,
            pointHoverRadius: 4,
            borderWidth: 2,
            yAxisID: 'y',
          },
        ],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        interaction: { mode: 'index', intersect: false },
        plugins: {
          legend: { labels: { color: '#0F172A', boxWidth: 12, font: { family: 'Inter', size: 11 } } },
        },
        scales: {
          x: { ticks: { color: '#64748B', font: { size: 10 }, maxTicksLimit: 8, maxRotation: 0 }, grid: { color: '#E2E8F0' } },
          y: {
            position: 'left',
            ticks: { color: '#64748B' },
            grid: { color: '#E2E8F0' },
          },
          y1: {
            position: 'right',
            ticks: { color: '#64748B' },
            grid: { display: false },
          },
        },
      },
    });
  } else {
    historyChart.data.labels = labels;
    historyChart.data.datasets[0].data = tds;
    historyChart.data.datasets[1].data = turbidity;
    historyChart.data.datasets[2].data = temperature;
    historyChart.data.datasets[3].data = ph;
    historyChart.update();
  }
}

function renderVerdict(result) {
  const panel = document.getElementById('verdictPanel');
  const badge = document.getElementById('verdictBadge');
  const icon = document.getElementById('verdictIcon');
  const text = document.getElementById('verdictText');
  const advice = document.getElementById('verdictAdvice');
  const list = document.getElementById('issuesList');
  const recBlock = document.getElementById('recommendationsBlock');
  const recPlanList = document.getElementById('recommendationPlanList');

  document.getElementById('verdictEmpty').style.display = 'none';
  document.getElementById('verdictContent').style.display = 'block';
  text.textContent = result.verdict;
  advice.textContent = result.overall_advice || '';

  const styleMap = {
    'Safe': { cls: 'verdict-safe', icon: '✓', panelCls: 'status-safe' },
    'Needs Treatment': { cls: 'verdict-warn', icon: '⚠', panelCls: 'status-warn' },
    'Contaminated': { cls: 'verdict-danger', icon: '✕', panelCls: 'status-danger' },
  };
  const style = styleMap[result.verdict] || styleMap['Needs Treatment'];
  badge.className = 'verdict-badge ' + style.cls;
  icon.textContent = style.icon;
  panel.classList.remove('status-safe', 'status-warn', 'status-danger');
  panel.classList.add(style.panelCls);

  list.innerHTML = '';
  if (result.issues && result.issues.length > 0) {
    result.issues.forEach(issue => {
      const li = document.createElement('li');
      li.textContent = issue;
      list.appendChild(li);
    });
  } else {
    const li = document.createElement('li');
    li.textContent = 'No issues detected — all readings within safe range.';
    list.appendChild(li);
  }

  renderRecommendationPlan(result, recBlock, recPlanList);
}

const SEVERITY_CLASS = {
  'Critical': 'rec-sev-critical',
  'Moderate': 'rec-sev-moderate',
  'Mild': 'rec-sev-mild',
};

function renderRecommendationPlan(result, recBlock, recPlanList) {
  recPlanList.innerHTML = '';

  const plan = result.recommendation_plan;

  // Fall back to the old flat string list for any older history entry
  // saved before the detailed plan existed.
  if (!plan || plan.length === 0) {
    if (result.recommendations && result.recommendations.length > 0) {
      recBlock.style.display = 'block';
      const ul = document.createElement('ul');
      ul.className = 'recommendations-list';
      result.recommendations.forEach(rec => {
        const li = document.createElement('li');
        li.textContent = rec;
        ul.appendChild(li);
      });
      recPlanList.appendChild(ul);
    } else {
      recBlock.style.display = 'none';
    }
    return;
  }

  recBlock.style.display = 'block';

  if (result.priority_action) {
    const priority = document.createElement('div');
    priority.className = 'rec-priority';
    priority.innerHTML = `<span class="rec-priority-label">Highest priority right now</span><p>${result.priority_action}</p>`;
    recPlanList.appendChild(priority);
  }

  plan.forEach(item => {
    const card = document.createElement('div');
    card.className = 'rec-card ' + (SEVERITY_CLASS[item.severity] || 'rec-sev-mild');

    const header = document.createElement('div');
    header.className = 'rec-card-header';
    header.innerHTML = `
      <span class="rec-severity-badge">${item.severity} · ${item.urgency}</span>
      <span class="rec-param-name">${item.parameter}</span>
      <span class="rec-reading">${item.reading} <span class="rec-safe-range">(safe: ${item.safe_range})</span></span>
    `;
    card.appendChild(header);

    const immediateP = document.createElement('p');
    immediateP.className = 'rec-immediate';
    immediateP.innerHTML = `<span class="rec-label">Now</span> ${item.immediate_action}`;
    card.appendChild(immediateP);

    const details = document.createElement('details');
    details.className = 'rec-details';
    const summary = document.createElement('summary');
    summary.textContent = 'Full treatment plan';
    details.appendChild(summary);

    const treatWrap = document.createElement('div');
    treatWrap.className = 'rec-line';
    const treatLabel = document.createElement('span');
    treatLabel.className = 'rec-label';
    treatLabel.textContent = 'Treatment';
    treatWrap.appendChild(treatLabel);
    const treatUl = document.createElement('ul');
    (item.treatment_steps || []).forEach(step => {
      const li = document.createElement('li');
      li.textContent = step;
      treatUl.appendChild(li);
    });
    treatWrap.appendChild(treatUl);
    details.appendChild(treatWrap);

    const longTermP = document.createElement('p');
    longTermP.className = 'rec-line';
    longTermP.innerHTML = `<span class="rec-label">Long-term</span> ${item.long_term}`;
    details.appendChild(longTermP);

    const retestP = document.createElement('p');
    retestP.className = 'rec-retest';
    retestP.textContent = '⏱ ' + item.retest_interval;
    details.appendChild(retestP);

    card.appendChild(details);
    recPlanList.appendChild(card);
  });
}

function renderImagePreview(result) {
  const wrap = document.getElementById('imagePreviewWrap');
  const img = document.getElementById('previewImg');
  const canvas = document.getElementById('overlayCanvas');
  const list = document.getElementById('detectionsList');

  wrap.style.display = 'block';
  img.src = result.image_url + '?t=' + Date.now(); // cache-bust

  const cvResult = result.cv_result || {};
  const detections = cvResult.detections || [];

  list.innerHTML = '';
  if (detections.length === 0) {
    const li = document.createElement('li');
    li.textContent = 'No contamination objects detected in image.';
    list.appendChild(li);
  } else {
    detections.forEach(d => {
      const li = document.createElement('li');
      li.textContent = `${d.class} — ${(d.confidence * 100).toFixed(0)}% confidence`;
      list.appendChild(li);
    });
  }

  img.onload = () => {
    const scaleX = img.clientWidth / (cvResult.image_width || img.naturalWidth);
    const scaleY = img.clientHeight / (cvResult.image_height || img.naturalHeight);

    canvas.width = img.clientWidth;
    canvas.height = img.clientHeight;
    const ctx = canvas.getContext('2d');
    ctx.clearRect(0, 0, canvas.width, canvas.height);

    detections.forEach(d => {
      const [x1, y1, x2, y2] = d.box || [0, 0, 0, 0];
      const bx = x1 * scaleX, by = y1 * scaleY;
      const bw = (x2 - x1) * scaleX, bh = (y2 - y1) * scaleY;

      ctx.strokeStyle = '#C0392B';
      ctx.lineWidth = 2;
      ctx.strokeRect(bx, by, bw, bh);

      const label = `${d.class} ${(d.confidence * 100).toFixed(0)}%`;
      ctx.font = '12px "JetBrains Mono", monospace';
      const textWidth = ctx.measureText(label).width;
      ctx.fillStyle = '#C0392B';
      ctx.fillRect(bx, by - 16, textWidth + 8, 16);
      ctx.fillStyle = '#FFFFFF';
      ctx.fillText(label, bx + 4, by - 4);
    });
  };
}

async function refreshCameraFrame() {
  const img = document.getElementById('esp32CamImage');
  const empty = document.getElementById('cameraEmptyNote');
  const pill = document.getElementById('cameraStatusPill');
  const stamp = document.getElementById('cameraTimestamp');
  if (!img || !pill) return;

  try {
    const res = await fetch('/api/latest');
    const data = await res.json();
    const cam = data.camera || {};

    if (cam.available) {
      img.src = (cam.url || '/api/camera/latest.jpg') + '?t=' + Date.now();
      img.style.display = 'block';
      if (empty) empty.style.display = 'none';
      pill.className = 'status-pill status-ok';
      pill.innerHTML = '<span class="status-dot"></span>JPEG received';
      if (stamp) stamp.textContent = `Last frame: ${cam.timestamp || '—'}`;
    } else {
      img.style.display = 'none';
      if (empty) empty.style.display = 'block';
      pill.className = 'status-pill';
      pill.innerHTML = '<span class="status-dot"></span>waiting…';
      if (stamp) stamp.textContent = '';
    }
  } catch (err) {
    pill.className = 'status-pill status-warn';
    pill.innerHTML = '<span class="status-dot"></span>camera unavailable';
  }
}

async function analyzeLatestCameraFrame() {
  const btn = document.getElementById('analyzeCameraBtn');
  const errorBox = document.getElementById('analysisError');
  if (!btn) return;

  const original = btn.textContent;
  btn.disabled = true;
  btn.textContent = 'Analyzing…';
  if (errorBox) errorBox.textContent = '';

  try {
    const formData = new FormData();
    const slider = document.getElementById('confidenceSlider');
    formData.append('confidence', slider ? slider.value : '0.4');

    const res = await fetch('/api/analyze-camera', { method: 'POST', body: formData });
    const result = await res.json();
    if (result.error) {
      if (errorBox) errorBox.textContent = result.error;
      return;
    }

    renderVerdict(result);
    renderImagePreview(result);
    refreshVerdictHistory();
    maybeShowContaminationAlert(result);
  } catch (err) {
    if (errorBox) errorBox.textContent = 'Camera analysis failed. Check the server console.';
  } finally {
    btn.disabled = false;
    btn.textContent = original;
  }
}

const refreshCameraBtn = document.getElementById('refreshCameraBtn');
if (refreshCameraBtn) refreshCameraBtn.addEventListener('click', refreshCameraFrame);
const analyzeCameraBtn = document.getElementById('analyzeCameraBtn');
if (analyzeCameraBtn) analyzeCameraBtn.addEventListener('click', analyzeLatestCameraFrame);

async function runAnalysis(blob, submitBtn) {
  const errorBox = document.getElementById('analysisError');
  errorBox.textContent = '';

  const formData = new FormData();
  formData.append('image', blob, 'sample.jpg');
  const confidenceSlider = document.getElementById('confidenceSlider');
  formData.append('confidence', confidenceSlider ? confidenceSlider.value : '0.4');

  const originalText = submitBtn.textContent;
  submitBtn.disabled = true;
  submitBtn.textContent = 'Analyzing…';

  try {
    const res = await fetch('/api/analyze-image', { method: 'POST', body: formData });
    const result = await res.json();

    if (result.error) {
      errorBox.textContent = result.error;
      return;
    }
    renderVerdict(result);
    renderImagePreview(result);
    refreshVerdictHistory();
    maybeShowContaminationAlert(result);
  } catch (err) {
    errorBox.textContent = 'Analysis failed. Check the server console.';
  } finally {
    submitBtn.disabled = false;
    submitBtn.textContent = originalText;
  }
}

function maybeShowContaminationAlert(result) {
  if (result.verdict !== 'Contaminated') return;

  const banner = document.getElementById('alertBanner');
  const text = document.getElementById('alertBannerText');
  text.textContent = `⚠ Water sample flagged CONTAMINATED — ${result.issues.length} issue(s) found. See verdict panel below.`;
  banner.style.display = 'flex';

  if ('Notification' in window) {
    if (Notification.permission === 'granted') {
      new Notification('AquaScan Alert', { body: 'Contaminated water sample detected.' });
    } else if (Notification.permission !== 'denied') {
      Notification.requestPermission();
    }
  }
}

const alertBannerClose = document.getElementById('alertBannerClose');
if (alertBannerClose) {
  alertBannerClose.addEventListener('click', () => {
    document.getElementById('alertBanner').style.display = 'none';
  });
}

const confidenceSliderEl = document.getElementById('confidenceSlider');
const confidenceValueEl = document.getElementById('confidenceValue');
if (confidenceSliderEl && confidenceValueEl) {
  confidenceSliderEl.addEventListener('input', () => {
    confidenceValueEl.textContent = parseFloat(confidenceSliderEl.value).toFixed(2);
  });
}

async function refreshVerdictHistory() {
  try {
    const res = await fetch('/api/verdict-history');
    const history = await res.json();
    renderVerdictHistory(history);
  } catch (err) {
    console.error('Failed to fetch verdict history', err);
  }
}

function renderVerdictHistory(history) {
  const list = document.getElementById('verdictHistoryList');
  const emptyNote = document.getElementById('verdictHistoryEmptyNote');

  if (!history || history.length === 0) {
    list.innerHTML = '';
    emptyNote.style.display = 'block';
    return;
  }
  emptyNote.style.display = 'none';

  const badgeMap = {
    'Safe': 'verdict-safe',
    'Needs Treatment': 'verdict-warn',
    'Contaminated': 'verdict-danger',
  };

  list.innerHTML = '';
  history.forEach(entry => {
    const item = document.createElement('div');
    item.className = 'history-item';

    const thumb = document.createElement('img');
    thumb.className = 'history-thumb';
    thumb.src = entry.image_url;
    thumb.alt = 'Sample thumbnail';

    const info = document.createElement('div');
    info.className = 'history-info';

    const top = document.createElement('div');
    top.className = 'history-top';

    const badge = document.createElement('span');
    badge.className = 'history-badge ' + (badgeMap[entry.verdict] || 'verdict-warn');
    badge.textContent = entry.verdict;

    const time = document.createElement('span');
    time.className = 'history-time';
    time.textContent = entry.timestamp || '';

    top.appendChild(badge);
    top.appendChild(time);

    const issuesLine = document.createElement('p');
    issuesLine.className = 'history-issues';
    issuesLine.textContent = (entry.issues && entry.issues.length > 0)
      ? entry.issues.join(' · ')
      : 'No issues detected';

    info.appendChild(top);
    info.appendChild(issuesLine);

    item.appendChild(thumb);
    item.appendChild(info);
    list.appendChild(item);
  });
}

document.getElementById('uploadForm').addEventListener('submit', async (e) => {
  e.preventDefault();
  const fileInput = document.getElementById('imageInput');
  const submitBtn = e.target.querySelector('button');
  if (!fileInput.files.length) return;
  await runAnalysis(fileInput.files[0], submitBtn);
});

// ---------- Tab switching ----------
const uploadTabBtn = document.getElementById('uploadTabBtn');
const webcamTabBtn = document.getElementById('webcamTabBtn');
const uploadForm = document.getElementById('uploadForm');
const webcamSection = document.getElementById('webcamSection');

let webcamStream = null;

function stopWebcam() {
  if (webcamStream) {
    webcamStream.getTracks().forEach(track => track.stop());
    webcamStream = null;
  }
  document.getElementById('startWebcamBtn').style.display = 'inline-block';
  document.getElementById('captureBtn').style.display = 'none';
}

uploadTabBtn.addEventListener('click', () => {
  uploadTabBtn.classList.add('active');
  webcamTabBtn.classList.remove('active');
  uploadForm.style.display = 'flex';
  webcamSection.style.display = 'none';
  stopWebcam();
});

webcamTabBtn.addEventListener('click', () => {
  webcamTabBtn.classList.add('active');
  uploadTabBtn.classList.remove('active');
  uploadForm.style.display = 'none';
  webcamSection.style.display = 'block';
});

document.getElementById('startWebcamBtn').addEventListener('click', async () => {
  const errorBox = document.getElementById('analysisError');
  errorBox.textContent = '';
  try {
    webcamStream = await navigator.mediaDevices.getUserMedia({ video: { facingMode: 'environment' } });
    document.getElementById('webcamVideo').srcObject = webcamStream;
    document.getElementById('startWebcamBtn').style.display = 'none';
    document.getElementById('captureBtn').style.display = 'inline-block';
  } catch (err) {
    errorBox.textContent = 'Could not access webcam. Check browser permissions.';
  }
});

document.getElementById('captureBtn').addEventListener('click', async (e) => {
  const video = document.getElementById('webcamVideo');
  const captureCanvas = document.createElement('canvas');
  captureCanvas.width = video.videoWidth;
  captureCanvas.height = video.videoHeight;
  captureCanvas.getContext('2d').drawImage(video, 0, 0);

  captureCanvas.toBlob(async (blob) => {
    await runAnalysis(blob, e.target);
  }, 'image/jpeg', 0.9);
});

// ---------- Chart / Table view toggle ----------
document.getElementById('chartViewBtn').addEventListener('click', () => {
  document.getElementById('chartViewBtn').classList.add('active');
  document.getElementById('tableViewBtn').classList.remove('active');
  if (latestReadingHistory.length > 0) {
    document.getElementById('chartView').style.display = 'block';
    document.getElementById('tableView').style.display = 'none';
  }
});

document.getElementById('tableViewBtn').addEventListener('click', () => {
  document.getElementById('tableViewBtn').classList.add('active');
  document.getElementById('chartViewBtn').classList.remove('active');
  if (latestReadingHistory.length > 0) {
    document.getElementById('chartView').style.display = 'none';
    document.getElementById('tableView').style.display = 'block';
  }
});

// ---------- Alert recipients ----------
const recipientList = document.getElementById('recipientList');
const recipientEmptyNote = document.getElementById('recipientEmptyNote');
const recipientError = document.getElementById('recipientError');
const addRecipientForm = document.getElementById('addRecipientForm');
const recipientInput = document.getElementById('recipientInput');
const smtpStatusPill = document.getElementById('smtpStatusPill');

let currentRecipients = [];

function renderRecipients() {
  recipientList.innerHTML = '';
  recipientEmptyNote.style.display = currentRecipients.length ? 'none' : 'block';
  currentRecipients.forEach(email => {
    const li = document.createElement('li');
    li.className = 'recipient-row';
    const span = document.createElement('span');
    span.textContent = email;
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'recipient-remove-btn';
    btn.title = `Remove ${email}`;
    btn.setAttribute('aria-label', `Remove ${email}`);
    btn.textContent = '×';
    btn.addEventListener('click', () => removeRecipient(email));
    li.appendChild(span);
    li.appendChild(btn);
    recipientList.appendChild(li);
  });
}

async function saveRecipients(nextList) {
  const res = await fetch('/api/alert-settings', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ recipients: nextList }),
  });
  const data = await res.json();
  currentRecipients = data.recipients || [];
  renderRecipients();
  return data;
}

async function removeRecipient(email) {
  await saveRecipients(currentRecipients.filter(e => e !== email));
}

async function refreshAlertSettings() {
  try {
    const res = await fetch('/api/alert-settings');
    const data = await res.json();
    currentRecipients = data.recipients || [];
    renderRecipients();

    if (!data.enabled) {
      smtpStatusPill.className = 'status-pill status-warn';
      smtpStatusPill.innerHTML = '<span class="status-dot"></span>alerts disabled on server';
    } else if (!data.smtp_configured) {
      smtpStatusPill.className = 'status-pill status-warn';
      smtpStatusPill.innerHTML = '<span class="status-dot"></span>SMTP not configured';
    } else {
      smtpStatusPill.className = 'status-pill status-ok';
      smtpStatusPill.innerHTML = '<span class="status-dot"></span>ready to send';
    }
  } catch (err) {
    smtpStatusPill.className = 'status-pill status-warn';
    smtpStatusPill.innerHTML = '<span class="status-dot"></span>couldn\'t load settings';
  }
}

addRecipientForm.addEventListener('submit', async (e) => {
  e.preventDefault();
  recipientError.textContent = '';
  const email = recipientInput.value.trim();
  if (!email) return;

  if (currentRecipients.some(existing => existing.toLowerCase() === email.toLowerCase())) {
    recipientError.textContent = 'That address is already in the list.';
    return;
  }

  const data = await saveRecipients([...currentRecipients, email]);
  if (data.skipped_invalid && data.skipped_invalid.length) {
    recipientError.textContent = `Not a valid email address: ${data.skipped_invalid.join(', ')}`;
  } else {
    recipientInput.value = '';
  }
});

refreshSensorData();
refreshHistory();
refreshVerdictHistory();
refreshAlertSettings();
refreshCameraFrame();
setInterval(refreshSensorData, 3000);
setInterval(refreshHistory, 5000);
setInterval(refreshCameraFrame, 5000);
