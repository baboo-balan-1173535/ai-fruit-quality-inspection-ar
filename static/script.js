// ══════════════════════════════════════════════════════════
//  Fruit Quality Sorter — script.js
// ══════════════════════════════════════════════════════════

var selectedFile    = null;
var scanCount       = 0;
var currentCamIndex = 0;
var currentFruitTab = 'apple';

var SORT_LABELS = {
  "1": "⚡ Use Immediately",
  "2": "🔜 Use Soon",
  "3": "📦 Store",
  "4": "🗑️ Discard"
};

var FRUIT_ICONS = { apple:"🍎", banana:"🍌", orange:"🍊", kiwi:"🥝" };

var HSV_DEFAULTS = {
  apple:  { lower:[0,80,60],   upper:[10,255,255] },
  banana: { lower:[20,80,100], upper:[35,255,255] },
  orange: { lower:[8,100,100], upper:[20,255,255] },
  kiwi:   { lower:[10,30,30],  upper:[35,255,180] }
};

var hsvValues = JSON.parse(JSON.stringify(HSV_DEFAULTS));

function setStatus(msg) {
  document.getElementById('status-bar').textContent = msg;
}

// ── Live Log ───────────────────────────────────────────────
var terminal  = document.getElementById('terminal');
var evtSource = new EventSource('/logs');

evtSource.onmessage = function(e) {
  var line = document.createElement('span');
  line.className = 'log-line ' + classifyLog(e.data);
  line.textContent = e.data;
  terminal.appendChild(line);
  terminal.appendChild(document.createTextNode('\n'));
  terminal.scrollTop = terminal.scrollHeight;
};

function classifyLog(msg) {
  if (msg.indexOf('✅') !== -1 || msg.indexOf('✔') !== -1) return 'log-ok';
  if (msg.indexOf('⚠️') !== -1 || msg.indexOf('✖') !== -1) return 'log-warn';
  if (msg.indexOf('error') !== -1 || msg.indexOf('Error') !== -1) return 'log-error';
  if (msg.indexOf('━') !== -1) return 'log-sep';
  return 'log-info';
}

function clearLog() { terminal.innerHTML = ''; }

// ── Tab Switching ──────────────────────────────────────────
function switchTab(tab) {
  var cameraTab = document.getElementById('tab-camera');
  var uploadTab = document.getElementById('tab-upload');
  var stream    = document.getElementById('camera-stream');

  if (tab === 'upload') {
    cameraTab.classList.add('hidden');
    uploadTab.classList.remove('hidden');
    stream.src = '';
    fetch('/stop_camera', { method: 'POST' }).catch(function(e) { console.warn(e); });
    setStatus('Upload mode - select or drag an image. Camera paused.');
  } else {
    uploadTab.classList.add('hidden');
    cameraTab.classList.remove('hidden');
    fetch('/start_camera', { method: 'POST' }).catch(function(e) { console.warn(e); });
    setTimeout(function() { stream.src = '/video_feed'; }, 600);
    setStatus('Camera mode - point at fruit and click Analyse.');
  }
}

// ── Analyse Camera ─────────────────────────────────────────
function analyseCamera() {
  var overlay = document.getElementById('camera-overlay');
  overlay.classList.remove('hidden');
  setStatus('Analysing...');
  fetch('/analyse', { method: 'POST' })
    .then(function(res) { return res.json(); })
    .then(function(data) {
      if (data.error) { setStatus('⚠️ ' + data.error); }
      else { addScanCard(data); setStatus('✅ ' + data.fruits.length + ' fruit(s) analysed'); }
    })
    .catch(function(e) { setStatus('❌ ' + e.message); })
    .finally(function() { overlay.classList.add('hidden'); });
}

// ── File Upload ────────────────────────────────────────────
function handleFileSelect(e) { if (e.target.files[0]) loadPreview(e.target.files[0]); }
function handleDrop(e) { e.preventDefault(); if (e.dataTransfer.files[0]) loadPreview(e.dataTransfer.files[0]); }

function loadPreview(file) {
  selectedFile = file;
  var reader = new FileReader();
  reader.onload = function(e) {
    document.getElementById('preview-img').src = e.target.result;
    document.getElementById('upload-preview').classList.remove('hidden');
    document.getElementById('upload-zone').classList.add('hidden');
    setStatus('Ready: ' + file.name);
  };
  reader.readAsDataURL(file);
}

function analyseUpload() {
  if (!selectedFile) return;
  setStatus('Uploading and analysing...');
  var formData = new FormData();
  formData.append('image', selectedFile);
  fetch('/upload', { method: 'POST', body: formData })
    .then(function(res) { return res.json(); })
    .then(function(data) {
      if (data.error) { setStatus('⚠️ ' + data.error); }
      else {
        addScanCard(data);
        setStatus('✅ ' + data.fruits.length + ' fruit(s) analysed');
        selectedFile = null;
        document.getElementById('upload-zone').classList.remove('hidden');
        document.getElementById('upload-preview').classList.add('hidden');
        document.getElementById('file-input').value = '';
      }
    })
    .catch(function(e) { setStatus('❌ ' + e.message); });
}

// ── Scan Card ──────────────────────────────────────────────
function addScanCard(scan) {
  scanCount++;
  document.getElementById('no-results').classList.add('hidden');
  document.getElementById('scan-count').textContent = scanCount + ' scan(s)';

  var card   = document.createElement('div');
  card.className = 'scan-card';
  var time   = new Date().toLocaleTimeString();
  var source = scan.source === 'upload' ? '📁 Upload' : '📷 Camera';
  var rows   = scan.fruits.map(buildFruitRow).join('');

  card.innerHTML =
    '<div class="scan-card-header">' +
      '<span>Scan #' + scan.id + ' - ' + source + ' - ' + scan.fruits.length + ' fruit(s)</span>' +
      '<span>' + time + '</span>' +
    '</div>' +
    '<div class="scan-image-row">' +
      '<img src="data:image/jpeg;base64,' + scan.image + '" alt="Scan"/>' +
    '</div>' +
    '<div class="table-scroll-wrapper">' +
      '<table class="fruits-table">' +
        '<thead><tr>' +
          '<th>Crop</th><th>ID</th><th>Fruit</th><th>YOLO %</th>' +
          '<th>Quality</th><th>Decay Stage</th><th>Days Left</th>' +
          '<th>AI Conf.</th><th>Size</th>' +
          '<th>Width</th><th>Height</th><th>Area</th>' +
          '<th>Defects</th><th>Colour</th>' +
          '<th>Action</th><th>Recommendation</th>' +
        '</tr></thead>' +
        '<tbody>' + rows + '</tbody>' +
      '</table>' +
    '</div>';

  document.getElementById('results-list').insertBefore(card, document.getElementById('results-list').firstChild);
}

// ── Fruit Row ──────────────────────────────────────────────
function buildFruitRow(f) {
  var icon       = FRUIT_ICONS[f.fruit] || '🍑';
  var qualityCls = 'quality-' + f.quality;
  var sortCls    = 'sort-'    + f.sort;
  var sortLabel  = SORT_LABELS[f.sort] || f.sort;
  var days       = parseInt(f.days) || 0;
  var daysColor  = days <= 1 ? '#FF4444' : days <= 3 ? '#FFC107' : '#00FF41';
  var bc         = f.bbox_color || [100,100,100];
  var accent     = 'rgb(' + bc[2] + ',' + bc[1] + ',' + bc[0] + ')';
  var cropImg    = f.crop_b64
    ? '<img src="data:image/jpeg;base64,' + f.crop_b64 + '" class="crop-thumb" alt="' + f.fruit + '"/>'
    : '<div class="crop-placeholder">' + icon + '</div>';

  return '<tr class="fruit-row" style="--accent-col:' + accent + '">' +
    '<td class="col-crop">'    + cropImg + '</td>' +
    '<td class="col-id"><div class="id-badge" style="background:' + accent + '">#' + f.index + '</div></td>' +
    '<td class="col-nowrap"><strong>' + icon + ' ' + f.fruit.toUpperCase() + '</strong></td>' +
    '<td class="col-yolo">' +
      '<div class="yolo-bar-wrap"><div class="yolo-bar" style="width:' + f.confidence + '%;background:' + accent + '"></div></div>' +
      '<span class="yolo-pct">' + f.confidence + '%</span>' +
    '</td>' +
    '<td><span class="quality-pill ' + qualityCls + '">' + f.quality + '</span></td>' +
    '<td class="col-wrap">'   + (f.decay_stage    || '-') + '</td>' +
    '<td class="col-nowrap"><span class="days-val" style="color:' + daysColor + '">' + f.days + 'd</span></td>' +
    '<td class="col-center">' + (f.confidence_ai  || '-') + '</td>' +
    '<td class="col-center">' + (f.size           || '-') + '</td>' +
    '<td class="col-measure col-nowrap">' + f.width_cm  + ' cm</td>' +
    '<td class="col-measure col-nowrap">' + f.height_cm + ' cm</td>' +
    '<td class="col-measure col-nowrap">' + f.area_cm2  + ' cm²</td>' +
    '<td class="col-wrap col-defects">'   + (f.defects    || '-') + '</td>' +
    '<td class="col-wrap col-colour">'    + (f.color_desc || '-') + '</td>' +
    '<td class="col-nowrap"><div class="sort-pill ' + sortCls + '">' + sortLabel + '</div></td>' +
    '<td class="col-rec">'    + (f.recommendation || '-') + '</td>' +
  '</tr>';
}

// ── Clear History ──────────────────────────────────────────
function clearHistory() {
  fetch('/clear', { method: 'POST' }).then(function() {
    document.getElementById('results-list').innerHTML = '';
    document.getElementById('no-results').classList.remove('hidden');
    document.getElementById('scan-count').textContent = '0 scans';
    scanCount = 0;
  });
}

// ── Modals ─────────────────────────────────────────────────
function openModal(id) {
  document.getElementById(id).classList.remove('hidden');
}
function closeModal(id) { document.getElementById(id).classList.add('hidden'); }

// ── Settings modal (Camera | Colour | Devices) ─────────────
function openSettings() {
  openModal('modal-settings');
  selectSettingsTab('camera');
}

function selectSettingsTab(name) {
  ['camera', 'colour', 'devices'].forEach(function(t) {
    document.getElementById('spane-' + t).classList.toggle('hidden', t !== name);
    document.getElementById('stab-'  + t).classList.toggle('active', t === name);
  });
  if (name === 'colour')  { loadHsvFromServer(); renderHsvSliders(currentFruitTab); }
  if (name === 'devices') { loadDevices(); }
}

// ── Devices (access control) ───────────────────────────────
function loadDevices() {
  fetch('/devices')
    .then(function(r) { return r.json(); })
    .then(function(data) {
      if (data.error) {
        document.getElementById('device-list').innerHTML =
          '<p class="cal-hint" style="color:#FF4444">' + data.error + '</p>';
        return;
      }
      document.getElementById('enforce-toggle').checked = !!data.enforce;
      var list = document.getElementById('device-list');
      if (!data.devices.length) {
        list.innerHTML = '<p class="cal-hint">No remote devices have connected yet. ' +
          'Open the mobile scanner or AR app from another device and it will appear here.</p>';
        return;
      }
      list.innerHTML = '';
      data.devices.forEach(function(d) {
        var row = document.createElement('div');
        row.className = 'device-row';
        var seen = d.last_seen_s === null ? 'never seen'
                 : d.last_seen_s < 60  ? d.last_seen_s + 's ago'
                 : d.last_seen_s < 3600 ? Math.round(d.last_seen_s / 60) + ' min ago'
                 : Math.round(d.last_seen_s / 3600) + ' h ago';
        var chip = d.allowed
          ? '<span class="dev-chip dev-allowed">allowed</span>'
          : (d.blocked_count > 0
              ? '<span class="dev-chip dev-blocked">blocked ×' + d.blocked_count + '</span>'
              : '<span class="dev-chip dev-open">open</span>');
        var btn = d.allowed
          ? '<button class="btn btn-outline btn-sm" onclick="revokeDevice(\'' + d.ip + '\')">Remove</button>'
          : '<button class="btn btn-primary btn-sm" onclick="allowDevice(\'' + d.ip + '\')">Allow</button>';
        row.innerHTML = '<span class="dev-ip">' + d.ip + '</span>' +
                        '<span class="dev-seen">' + seen + '</span>' + chip + btn;
        list.appendChild(row);
      });
    });
}

function allowDevice(ip) {
  fetch('/devices/allow', { method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify({ip: ip}) }).then(loadDevices);
}
function revokeDevice(ip) {
  fetch('/devices/revoke', { method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify({ip: ip}) }).then(loadDevices);
}
function setEnforce(on) {
  fetch('/devices/enforce', { method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify({on: on}) }).then(loadDevices);
}

// ── Mobile connect modal (QR) ──────────────────────────────
var MOBILE_URL = 'http://' + (window.LAN_IP || location.hostname) + ':5000/mobile';
var _qrDone = false;

function openMobileModal() {
  openModal('modal-mobile');
  document.getElementById('mobile-url').textContent = MOBILE_URL;
  if (!_qrDone) {
    var box = document.getElementById('qr-box');
    if (window.QRCode) {
      new QRCode(box, { text: MOBILE_URL, width: 200, height: 200,
                        correctLevel: QRCode.CorrectLevel.M });
    } else {
      // CDN unavailable (offline) — the URL text below is the fallback
      box.style.display = 'none';
    }
    _qrDone = true;
  }
}
function openMobileHere() { window.open(MOBILE_URL, '_blank'); }

// ── Reports & Chat (DocMindAI RAG app on port 5001) ────────
function openReports() {
  window.open('http://' + (window.LAN_IP || location.hostname) + ':5001', '_blank');
}

// ── AR glasses: modal, on/off switch, live status ───────────
var arEnabled    = true;
var arPollTimer  = null;

function renderArButton() {
  var nav = document.getElementById('ar-toggle-btn');
  if (nav) {
    nav.innerHTML = '<span class="msi">' + (arEnabled ? 'visibility' : 'visibility_off') + '</span> AR';
    nav.classList.toggle('ar-on',  arEnabled);
    nav.classList.toggle('ar-off', !arEnabled);
  }
  var sw = document.getElementById('ar-switch-btn');
  if (sw) {
    sw.innerHTML = '<span class="msi">' + (arEnabled ? 'visibility' : 'visibility_off') + '</span> AR: ' + (arEnabled ? 'ON' : 'OFF');
    sw.className = 'btn full-width ' + (arEnabled ? 'btn-primary' : 'btn-danger');
  }
}

function toggleAR() {
  fetch('/ar-toggle', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ enabled: !arEnabled })
  })
    .then(function(r) { return r.json(); })
    .then(function(s) {
      arEnabled = !!s.ar_enabled;
      renderArButton();
      setStatus('AR endpoints ' + (arEnabled ? 'enabled' : 'disabled') + '.');
    })
    .catch(function() { setStatus('Could not reach server to toggle AR.'); });
}

function refreshArStatus() {
  fetch('/status')
    .then(function(r) { return r.json(); })
    .then(function(s) {
      if (typeof s.ar_enabled === 'boolean') { arEnabled = s.ar_enabled; renderArButton(); }
      var ipEl   = document.getElementById('ar-device-ip');
      var seenEl = document.getElementById('ar-last-seen');
      var chipEl = document.getElementById('ar-device-chip');
      if (!ipEl) return;
      if (s.ar_device_ip && s.ar_last_seen !== null) {
        ipEl.textContent = s.ar_device_ip;
        var fresh = s.ar_last_seen < 5;
        seenEl.textContent = fresh
          ? 'Receiving detection requests now'
          : 'Last request ' + Math.round(s.ar_last_seen) + 's ago';
        chipEl.textContent = fresh ? 'connected' : 'idle';
        chipEl.className   = 'dev-chip ' + (fresh ? 'dev-allowed' : 'dev-open');
      } else {
        ipEl.textContent   = 'no contact yet';
        seenEl.textContent = 'The glasses app appears here when it connects';
        chipEl.textContent = 'offline';
        chipEl.className   = 'dev-chip dev-blocked';
      }
    })
    .catch(function() {});
}

function openArModal() {
  document.getElementById('modal-ar').classList.remove('hidden');
  refreshArStatus();
  arPollTimer = setInterval(function() {
    if (document.getElementById('modal-ar').classList.contains('hidden')) {
      clearInterval(arPollTimer); arPollTimer = null; return;
    }
    refreshArStatus();
  }, 2000);
}

// ── Sync camera index + AR state with the server ───────────
fetch('/status')
  .then(function(r) { return r.json(); })
  .then(function(s) {
    if (typeof s.camera_index === 'number') {
      currentCamIndex = s.camera_index;
      document.querySelectorAll('.cam-btn').forEach(function(b) { b.classList.remove('active'); });
      var btn = document.getElementById('cam-btn-' + s.camera_index);
      if (btn) btn.classList.add('active');
      document.getElementById('cam-index-label').textContent = 'Camera ' + s.camera_index;
    }
    if (typeof s.ar_enabled === 'boolean') arEnabled = s.ar_enabled;
    renderArButton();
  })
  .catch(function() { renderArButton(); });

document.querySelectorAll('.modal-backdrop').forEach(function(el) {
  el.addEventListener('click', function(e) { if (e.target === el) el.classList.add('hidden'); });
});

// ── Camera Calibration ─────────────────────────────────────
function switchCamera(index) {
  document.querySelectorAll('.cam-btn').forEach(function(b) { b.classList.remove('active'); });
  document.getElementById('cam-btn-' + index).classList.add('active');
  var statusEl = document.getElementById('cam-switch-status');
  statusEl.textContent = 'Switching...';
  fetch('/switch_camera', { method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({index:index}) })
    .then(function(res) { return res.json(); })
    .then(function(data) {
      if (data.error) { statusEl.textContent = '✖ ' + data.error; statusEl.style.color = '#FF4444'; }
      else {
        currentCamIndex = index;
        statusEl.textContent = '✔ Switched to camera ' + index;
        statusEl.style.color = '#00FF41';
        document.getElementById('cam-index-label').textContent = 'Camera ' + index;
        var s = document.getElementById('camera-stream');
        s.src = '';
        setTimeout(function() { s.src = '/video_feed'; }, 400);
      }
    });
}

function savePxCm() {
  var val = document.getElementById('pxcm-slider').value;
  fetch('/set_pxcm', { method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({pixels_per_cm:parseInt(val)}) });
  setStatus('📏 Pixels per cm set to ' + val);
}

// ── HSV Calibration ────────────────────────────────────────
function loadHsvFromServer() {
  fetch('/get_hsv').then(function(res) { return res.json(); })
    .then(function(data) {
      Object.keys(data).forEach(function(fruit) {
        if (data[fruit]) hsvValues[fruit] = { lower: data[fruit].lower, upper: data[fruit].upper };
      });
    }).catch(function() {});
}

function selectFruitTab(fruit) {
  currentFruitTab = fruit;
  document.querySelectorAll('.fruit-tab').forEach(function(t) { t.classList.remove('active'); });
  event.target.classList.add('active');
  renderHsvSliders(fruit);
}

function renderHsvSliders(fruit) {
  var v = hsvValues[fruit] || HSV_DEFAULTS[fruit];
  document.getElementById('hsv-sliders').innerHTML =
    '<div class="hsv-group"><div class="hsv-group-title">Lower Bound</div>' +
    makeSlider('H Low','h-low',v.lower[0],0,179) + makeSlider('S Low','s-low',v.lower[1],0,255) + makeSlider('V Low','v-low',v.lower[2],0,255) +
    '</div><div class="hsv-group"><div class="hsv-group-title">Upper Bound</div>' +
    makeSlider('H High','h-high',v.upper[0],0,179) + makeSlider('S High','s-high',v.upper[1],0,255) + makeSlider('V High','v-high',v.upper[2],0,255) +
    '</div>';
}

function makeSlider(label, id, val, min, max) {
  return '<div class="slider-row"><label>' + label + '</label>' +
    '<input type="range" id="sl-' + id + '" min="' + min + '" max="' + max + '" value="' + val + '" oninput="document.getElementById(\'sv-' + id + '\').textContent=this.value">' +
    '<span id="sv-' + id + '" class="slider-val">' + val + '</span></div>';
}

function saveHsv() {
  function get(id) { return parseInt(document.getElementById('sl-' + id).value); }
  var lower = [get('h-low'), get('s-low'), get('v-low')];
  var upper = [get('h-high'), get('s-high'), get('v-high')];
  hsvValues[currentFruitTab] = { lower:lower, upper:upper };
  fetch('/set_hsv', { method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({fruit:currentFruitTab, lower:lower, upper:upper}) });
  var msg = document.getElementById('hsv-save-msg');
  msg.textContent = '✔ Saved for ' + currentFruitTab;
  setTimeout(function() { msg.textContent = ''; }, 2500);
}

function resetHsv() {
  hsvValues[currentFruitTab] = JSON.parse(JSON.stringify(HSV_DEFAULTS[currentFruitTab]));
  renderHsvSliders(currentFruitTab);
}
