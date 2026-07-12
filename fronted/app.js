// ---------------------------------------------------------------------------
// Настройка адреса бэкенда.
// Если сайт открыт с того же сервера, что и API (локальный запуск) —
// оставляем пусто, запросы идут на тот же origin.
// Если фронтенд лежит статикой на GitHub Pages, а бэкенд (Flask+yt-dlp)
// задеплоен отдельно (Render/Railway/VPS) — впиши его адрес сюда один раз:
// ---------------------------------------------------------------------------
const DEFAULT_API_BASE = ""; // например: "https://downlink-backend.onrender.com"

let API_BASE = localStorage.getItem("downlink_api_base") || DEFAULT_API_BASE;

const urlInput    = document.getElementById('urlInput');
const pasteBtn    = document.getElementById('pasteBtn');
const clearBtn    = document.getElementById('clearBtn');
const downloadBtn = document.getElementById('downloadBtn');
const qualitySel  = document.getElementById('quality');
const queueEl     = document.getElementById('queue');
const emptyState  = document.getElementById('emptyState');
const statusLed   = document.getElementById('statusLed');
const statusText  = document.getElementById('statusText');
const apiNote     = document.getElementById('apiNote');
const installBtn  = document.getElementById('installBtn');

let SERVER_MODE = "local"; // обновится после health-check

// --- проверка соединения с бэкендом -----------------------------------------
async function checkHealth(){
  try{
    const res = await fetch(`${API_BASE}/api/health`, {cache:"no-store"});
    const data = await res.json();
    SERVER_MODE = data.mode || "local";
    statusLed.className = "led online";
    statusText.textContent = SERVER_MODE === "local"
      ? "движок запущен локально"
      : "подключено к серверу загрузки";
  }catch(e){
    statusLed.className = "led offline";
    statusText.textContent = API_BASE
      ? "нет связи с сервером загрузки — проверьте адрес"
      : "нет связи с локальным движком — запустите Запустить.bat";
  }
}
checkHealth();
setInterval(checkHealth, 15000);

if(!DEFAULT_API_BASE){
  apiNote.innerHTML = `Работает через: <b style="color:#8a90ac">${API_BASE || 'этот же сервер'}</b> · зажмите Shift и кликните по индикатору сверху, чтобы изменить адрес движка`;
}

statusLed.parentElement.addEventListener('click', (e) => {
  if(!e.shiftKey) return;
  const val = prompt("Адрес backend-сервера (Flask), например https://your-backend.onrender.com\nОставьте пустым, чтобы использовать этот же домен:", API_BASE);
  if(val !== null){
    API_BASE = val.trim();
    localStorage.setItem("downlink_api_base", API_BASE);
    checkHealth();
  }
});

// --- PWA: установка приложения ----------------------------------------------
let deferredPrompt = null;
window.addEventListener('beforeinstallprompt', (e) => {
  e.preventDefault();
  deferredPrompt = e;
  installBtn.classList.add('show');
});
installBtn.addEventListener('click', async () => {
  if(!deferredPrompt) return;
  deferredPrompt.prompt();
  await deferredPrompt.userChoice;
  deferredPrompt = null;
  installBtn.classList.remove('show');
});
window.addEventListener('appinstalled', () => installBtn.classList.remove('show'));

if('serviceWorker' in navigator){
  window.addEventListener('load', () => {
    navigator.serviceWorker.register('sw.js').catch(() => {});
  });
}

// --- вставка / очистка -------------------------------------------------------
pasteBtn.onclick = async () => {
  try{
    const text = await navigator.clipboard.readText();
    urlInput.value = (urlInput.value + ' ' + text).trim();
  }catch(e){ urlInput.focus(); }
};
clearBtn.onclick = () => { urlInput.value = ''; urlInput.focus(); };

// --- сегментированный индикатор (фирменный элемент) --------------------------
function segMeter(percent, state){
  const segments = 30;
  const filled = Math.round((percent/100)*segments);
  let html = '';
  for(let i=0;i<segments;i++){
    let cls = 'seg';
    if(i < filled) cls += state === 'error' ? ' err' : (state === 'done' ? ' ok' : ' on');
    html += `<div class="${cls}"></div>`;
  }
  return html;
}

function cardTemplate(index, total){
  return `
  <div class="card" id="job-${index}">
    <div class="card-top">
      <div class="card-title" id="title-${index}">Анализ ссылки ${index} из ${total}…</div>
      <div class="card-badge" id="badge-${index}">${index}/${total}</div>
    </div>
    <div class="card-meta" id="meta-${index}"></div>
    <div class="meter" id="meter-${index}">${segMeter(0)}</div>
    <div class="telemetry" id="tel-${index}">
      <span>Загружено: <b>—</b></span>
      <span>Скорость: <b>—</b></span>
      <span>Осталось: <b>—</b></span>
    </div>
    <div class="status-line" id="status-${index}">В очереди…</div>
  </div>`;
}

// --- основной сценарий загрузки ----------------------------------------------
downloadBtn.onclick = async () => {
  const urls = urlInput.value.trim();
  if(!urls){ urlInput.focus(); return; }

  downloadBtn.disabled = true;
  downloadBtn.textContent = '⏳ ЗАПУСК…';
  emptyState.style.display = 'none';
  queueEl.innerHTML = '';

  let data;
  try{
    const res = await fetch(`${API_BASE}/api/queue`, {
      method:'POST',
      headers:{'Content-Type':'application/json'},
      body: JSON.stringify({urls, quality: qualitySel.value})
    });
    data = await res.json();
  }catch(e){
    downloadBtn.disabled = false;
    downloadBtn.textContent = '⬇ ЗАГРУЗИТЬ';
    alert('Не удалось связаться с сервером загрузки. Проверьте адрес движка (Shift + клик по индикатору сверху).');
    return;
  }

  if(data.error){
    downloadBtn.disabled = false;
    downloadBtn.textContent = '⬇ ЗАГРУЗИТЬ';
    alert(data.error);
    return;
  }

  downloadBtn.textContent = '⬇ ИДЁТ ЗАГРУЗКА…';
  const total = data.total;
  const jobId = data.job_id;
  for(let i=1;i<=total;i++){
    queueEl.insertAdjacentHTML('beforeend', cardTemplate(i, total));
  }

  const es = new EventSource(`${API_BASE}/api/stream/${jobId}`);

  es.onmessage = (e) => {
    const ev = JSON.parse(e.data);
    const i = ev.index;

    if(ev.type === 'status'){
      const t = document.getElementById(`title-${i}`);
      if(t) t.textContent = ev.message;
    }

    if(ev.type === 'info'){
      document.getElementById(`title-${i}`).textContent = ev.title;
      document.getElementById(`badge-${i}`).textContent = ev.source || `${i}/${ev.total}`;
      document.getElementById(`meta-${i}`).innerHTML = `
        <span>Автор: <b>${ev.author}</b></span>
        <span>Длительность: <b>${ev.duration}</b></span>
        <span>Просмотры: <b>${ev.views}</b></span>
        <span>Лайки: <b>${ev.likes}</b></span>`;
      document.getElementById(`status-${i}`).textContent = 'Загрузка началась…';
    }

    if(ev.type === 'progress'){
      document.getElementById(`meter-${i}`).innerHTML = segMeter(ev.percent, ev.stage === 'merging' ? 'done' : 'on');
      document.getElementById(`tel-${i}`).innerHTML = `
        <span>Загружено: <b>${ev.downloaded} / ${ev.total_size}</b></span>
        <span>Скорость: <b>${ev.speed}</b></span>
        <span>Осталось: <b>${ev.eta}</b></span>`;
      document.getElementById(`status-${i}`).textContent =
        ev.stage === 'merging' ? '⚙️ Сборка файла (аудио+видео)…' : `${ev.percent}% завершено`;
    }

    if(ev.type === 'item_done'){
      document.getElementById(`meter-${i}`).innerHTML = segMeter(100, 'done');
      const s = document.getElementById(`status-${i}`);
      s.classList.add('success');
      if(ev.downloadable){
        s.innerHTML = `✅ Готово <a class="save-btn" href="${API_BASE}/api/file/${jobId}/${i}" target="_blank" rel="noopener">💾 Сохранить файл</a>`;
      }else{
        s.textContent = '✅ Готово — файл сохранён на сервере';
      }
    }

    if(ev.type === 'item_error'){
      document.getElementById(`meter-${i}`).innerHTML = segMeter(100, 'error');
      const s = document.getElementById(`status-${i}`);
      s.textContent = '❌ Ошибка: ' + ev.message;
      s.classList.add('error');
    }

    if(ev.type === 'job_done'){
      downloadBtn.disabled = false;
      downloadBtn.textContent = '⬇ ЗАГРУЗИТЬ';
      const summary = document.createElement('div');
      summary.className = 'summary';
      let actions = '';
      if(SERVER_MODE === 'local'){
        actions = `
          <button class="small-btn" id="openFolderBtn">📂 Открыть папку</button>
          <button class="small-btn" id="clearFolderBtn">🗑️ Очистить папку</button>`;
      }
      summary.innerHTML = `
        <span>${ev.cancelled ? '🛑 Остановлено' : '📦 Готово'}: <b>${ev.success}/${ev.total}</b> файлов успешно</span>
        <div class="actions">${actions}</div>`;
      queueEl.appendChild(summary);
      if(SERVER_MODE === 'local'){
        document.getElementById('openFolderBtn').onclick = () => fetch(`${API_BASE}/api/open-folder`, {method:'POST'});
        document.getElementById('clearFolderBtn').onclick = async () => {
          if(!confirm('Удалить все файлы из папки загрузок на сервере?')) return;
          await fetch(`${API_BASE}/api/clear-folder`, {method:'POST'});
          alert('Папка очищена');
        };
      }
      es.close();
    }
  };

  es.onerror = () => {
    downloadBtn.disabled = false;
    downloadBtn.textContent = '⬇ ЗАГРУЗИТЬ';
    es.close();
  };
};

urlInput.addEventListener('keydown', (e) => {
  if(e.key === 'Enter') downloadBtn.click();
});
