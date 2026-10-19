/**
 * 在 sandbox iframe 中顯示 Anki 渲染好的卡面。
 * Shows Anki-rendered card faces inside a sandboxed iframe.
 *
 * iframe 給 `allow-scripts` 與 `allow-popups(-to-escape-sandbox)`，**不給** `allow-same-origin`：
 * 模板的腳本可以執行，連結可以開新分頁（TG 深連結），但碰不到網頁本身與 API。代價是 iframe
 * 的 origin 是 opaque（`null`）：它自己發出的請求不保證帶得到登入 cookie（例如 Cloudflare
 * Access 的 SameSite cookie、Safari 一律擋第三方 cookie）。所以媒體檔改由網頁代為下載：注入
 * 的腳本（{@link FRAME_BRIDGE_SCRIPT}）攔下模板設定的檔名，用 postMessage 請網頁下載，收到
 * Blob 後在 iframe 內建立 blob URL。
 *
 * 攔得到：`img` / `audio` / `video` / `source` 的 `src`（屬性設定、`setAttribute`、HTML 中直接
 * 寫的屬性；`<source>` 若在瀏覽器選完來源之後才換成 blob URL，不會重新載入）、`new Audio(檔名)`，以及後端把 `[anki:play]` 標記換成的播放鈕。攔不到：CSS 的
 * `url(...)`、`srcset`、`@font-face`；HTML 中直接寫的 `src` 在被改寫前，瀏覽器會先自己發一次
 * 請求（打到網頁本身、失敗，無害）。
 *
 * The bridge proxies media through the parent because the opaque-origin
 * frame may not carry the login cookie. CSS `url()`, `srcset` and fonts are
 * not covered.
 *
 * 模板也會直接呼叫 AnkiConnect（`fetch('http://127.0.0.1:8765', …)`）：「查看母卡片」等按鈕用
 * `guiBrowse` 在 Anki 瀏覽器中打開某張筆記。橋接腳本攔下這些請求，不讓它們打到使用者電腦上的
 * Anki：`guiBrowse` 改請網頁在這個頁面中切換到那張卡片，其他動作（寫入欄位等）回覆不支援並請
 * 網頁顯示提示。
 *
 * 攔截只為了讓按鈕有作用；真正擋住連線的是卡面文件的 CSP（`connect-src 'none'`、
 * `form-action 'none'`，見 {@link CARD_FRAME_CSP}）：模板用任何寫法（其他網址寫法、子 iframe、
 * 表單）都連不出去。媒體檔本來就經 postMessage 由網頁下載，不受影響。
 *
 * Templates also call AnkiConnect directly. The bridge intercepts those
 * calls: `guiBrowse` becomes in-page navigation, anything else is refused
 * with a notice. The document's CSP is what actually blocks connections, so
 * nothing reaches an Anki running on the viewer's machine.
 */

/**
 * 卡面文件的 CSP：不准發出連線與送出表單（卡面不需要；媒體經 postMessage）。
 * The card document's CSP: no connections, no form submissions.
 */
export const CARD_FRAME_CSP = "connect-src 'none'; form-action 'none'"

/** `guiBrowse` 一次最多打開幾張筆記的 ID（超過的不理會）。Max note IDs per guiBrowse. */
export const MAX_BROWSE_NOTES = 20

/** iframe → 網頁：請代為下載一個媒體檔。Frame → parent: fetch a media file. */
export interface MediaRequestMessage {
  type: 'anki-media-request'
  /** 這份 iframe 文件的識別碼，回覆時原樣帶回。Identifies the frame document. */
  nonce: string
  id: number
  name: string
}

/** iframe → 網頁：內容高度改變。Frame → parent: the content height changed. */
export interface FrameHeightMessage {
  type: 'anki-frame-height'
  nonce: string
  height: number
}

/** iframe → 網頁：卡面腳本出錯（含橋接腳本本身）。Frame → parent: a script error. */
export interface FrameErrorMessage {
  type: 'anki-frame-error'
  nonce: string
  message: string
}

/** iframe → 網頁：模板要在 Anki 瀏覽器中搜尋（`guiBrowse`）。Frame → parent: a guiBrowse search. */
export interface BrowseRequestMessage {
  type: 'anki-browse'
  nonce: string
  query: string
}

/**
 * iframe → 網頁：使用者點了卡面上不是按鈕的地方（網頁顯示正面時據此翻到背面）。
 * Frame → parent: a click on the face outside any control.
 */
export interface FaceClickMessage {
  type: 'anki-face-click'
  nonce: string
}

/**
 * 卡面內的捲動手勢（滾輪、觸控）。iframe 內的事件不會傳到網頁；卡面本身不捲動，捲動會由瀏覽器
 * 接給外層，這裡只是讓外層知道，好在捲到邊界時做彈性回饋。觸控用 `screenY`：外層捲動時 iframe
 * 跟著移動，`clientY` 會不準。
 * Scroll gestures inside the face, forwarded so the parent can add
 * rubber-band feedback at its edges.
 */
export type ScrollGesture =
  | { kind: 'wheel'; deltaY: number }
  | { kind: 'touch'; phase: 'start' | 'move' | 'end'; y?: number }

/** iframe → 網頁：一個捲動手勢。Frame → parent: a scroll gesture. */
export interface ScrollGestureMessage {
  type: 'anki-scroll-gesture'
  nonce: string
  gesture: ScrollGesture
}

/** iframe → 網頁：模板呼叫了網頁版不支援的 AnkiConnect 動作。Frame → parent: an unsupported call. */
export interface FrameNoticeMessage {
  type: 'anki-frame-notice'
  nonce: string
  message: string
}

/** 網頁 → iframe：媒體檔下載結果，成功帶 Blob、失敗帶原因。Parent → frame: the fetch result. */
export type MediaResponseMessage =
  | { type: 'anki-media-response'; nonce: string; id: number; blob: Blob }
  | { type: 'anki-media-response'; nonce: string; id: number; error: string }

/** 高度上限，避免卡面腳本把頁面撐到無限高。Height cap. */
export const MAX_FRAME_HEIGHT = 20000

/**
 * 是否為 iframe 可以攔下、交給網頁下載的檔名：相對檔名（沒有 scheme、不以 `/` `#` `?` 開頭、
 * 不是空字串或 `none`）。`blob:` 等 URL 不攔，所以設定 blob URL 後不會再被攔一次。
 * 這個函式會原樣注入 iframe，只能用到 JavaScript 內建功能。
 *
 * Whether a src value is a relative media name for the bridge. Injected
 * verbatim into the frame, so it must be self-contained.
 *
 * @param value - `src` 的值。The src value.
 * @returns 是否攔下。Whether to intercept it.
 */
export function isLocalMediaName(value: unknown): value is string {
  return (
    typeof value === 'string' &&
    value !== '' &&
    value !== 'none' &&
    !/^[a-z][a-z0-9+.-]*:/i.test(value) &&
    !/^[/#?]/.test(value)
  )
}

/**
 * 解析模板對 AnkiConnect 的呼叫：網址指向本機（`127.x.x.x`、`localhost`、`[::1]`、`0.0.0.0`，
 * 以瀏覽器的網址解析為準，`127.1` 之類的寫法也算），埠為 8765 或省略時，從請求內容取出
 * `action` 與 `params`；其他請求回傳 null（照常送出，CSP 仍會擋）。是 AnkiConnect 但內容讀不懂
 * 時，`action` 為空字串（仍然攔下，不送出）。
 * 這個函式會原樣注入 iframe，只能用到 JavaScript 內建功能。
 *
 * Parses a template's AnkiConnect call. Returns null for other URLs, and an
 * empty action for an unreadable body. Injected verbatim into the frame.
 *
 * @param url - 請求網址。The request URL.
 * @param body - 請求內容（JSON 字串）。The request body.
 * @returns 動作與參數，或 null。The action and params, or null.
 */
export function parseAnkiConnectCall(url: unknown, body: unknown): { action: string; params: unknown } | null {
  let parsed
  try {
    // 相對網址（含 //host 這種寫法）以 http 解析；iframe 內的 location 是 about:srcdoc
    parsed = new URL(String(url ?? '').trim(), 'http://frame.invalid/')
  } catch {
    return null
  }
  const host = parsed.hostname.toLowerCase().replace(/\.$/, '')
  const isLocal = /^127\./.test(host) || host === 'localhost' || host === '[::1]' || host === '0.0.0.0'
  if (!/^https?:$/.test(parsed.protocol) || !isLocal || (parsed.port !== '8765' && parsed.port !== '')) return null
  try {
    const data = typeof body === 'string' ? JSON.parse(body) : null
    if (data && typeof data === 'object' && typeof data.action === 'string') {
      return { action: data.action, params: data.params ?? null }
    }
  } catch {
    // 讀不懂的內容一樣攔下
  }
  return { action: '', params: null }
}

/**
 * 從 `guiBrowse` 的搜尋語句取出筆記 ID：只接受 `nid:<ID>` 或 `nid:<ID>,<ID>…`（模板都用這種寫法），
 * 其他搜尋回傳 null；最多取前 {@link MAX_BROWSE_NOTES} 個。
 * Extracts note IDs from a guiBrowse query of the form `nid:1,2`.
 * @param query - 搜尋語句。The query.
 * @returns 筆記 ID（依原順序、去掉重複），或 null。The note IDs, or null.
 */
export function parseNoteQuery(query: string): number[] | null {
  const match = /^\s*nid:(\d+(?:,\d+)*)\s*$/i.exec(query)
  if (!match) return null
  const ids = match[1].split(',').map(Number)
  if (!ids.every((id) => Number.isSafeInteger(id) && id > 0)) return null
  return [...new Set(ids)].slice(0, MAX_BROWSE_NOTES)
}

/**
 * 檢查 iframe 送來的捲動手勢（不可信）：形狀不對或數值不是有限數字時為 null；滾輪量限制在
 * ±1000。
 * Validates a scroll gesture from the frame.
 * @param raw - 送來的資料。The raw data.
 * @returns 手勢，或 null。The gesture, or null.
 */
export function parseScrollGesture(raw: unknown): ScrollGesture | null {
  if (typeof raw !== 'object' || raw === null) return null
  const value = raw as { kind?: unknown; deltaY?: unknown; phase?: unknown; y?: unknown }
  if (value.kind === 'wheel') {
    return typeof value.deltaY === 'number' && Number.isFinite(value.deltaY)
      ? { kind: 'wheel', deltaY: Math.max(-1000, Math.min(1000, value.deltaY)) }
      : null
  }
  if (value.kind === 'touch' && (value.phase === 'start' || value.phase === 'move' || value.phase === 'end')) {
    if (value.y === undefined) return { kind: 'touch', phase: value.phase }
    return typeof value.y === 'number' && Number.isFinite(value.y) ? { kind: 'touch', phase: value.phase, y: value.y } : null
  }
  return null
}

/**
 * 網頁願意代為下載的檔名：只接受媒體資料夾中的單一檔名（不含 `/` `\` NUL、不是 `.` `..`）。
 * 後端 `validate_media_filename` 是權威版本；這裡先擋，省一趟請求並回覆明確的原因。
 *
 * Whether the parent will fetch a name: a bare file name only. The backend
 * check is authoritative.
 *
 * @param name - 檔名。The name.
 * @returns 是否為單一檔名。Whether it is a bare file name.
 */
export function isBareMediaName(name: string): boolean {
  return name !== '' && name !== '.' && name !== '..' && !/[\\/\0]/.test(name)
}

/**
 * 注入 iframe 的腳本（放在 `<head>` 最前面，比模板腳本先執行）。`__NONCE__` 由
 * {@link buildCardDocument} 換成這份文件的識別碼。
 *
 * - 攔下媒體 `src`（見模組說明），同一檔名只請求一次（失敗也會記住，重新打開卡面才重試）；
 *   超過 30 秒沒有回覆視為失敗。
 * - `play()` 等檔案到了才播放；檔案已經下載過就同步換上 blob URL、直接播放（不經過 Promise，
 *   點擊的使用者手勢才不會失效——Safari 只認同步呼叫）；檔案載入失敗時以真正的原因 reject。
 * - 攔下對 AnkiConnect 的 `fetch`、`XMLHttpRequest`、`sendBeacon`（見模組說明）；`guiBrowse`
 *   只在使用者剛點過時才請網頁切換卡片，卡面載入時自己呼叫的不會跳走。
 * - 點擊 `data-anki-sound` 播放鈕時播放該檔案；這些檔案在卡面載入時就先下載。
 * - 把滾輪與觸控手勢轉給網頁（`anki-scroll-gesture`，見 {@link ScrollGesture}）。
 * - 點擊卡面上不是控制項的地方時通知網頁（`anki-face-click`）。控制項：連結、按鈕、表單元件、
 *   播放鈕，以及游標是手指（`cursor: pointer`）的元素——模板的按鈕多半是設了 `onclick` 的
 *   `div`，只能從樣式判斷；選取文字時不算。等一下雙擊的時間才通知，雙擊選字不會翻面。
 * - 回報內容高度，並把 iframe 內的腳本錯誤轉給網頁顯示。
 *
 * The bridge script, injected before the template scripts.
 */
export const FRAME_BRIDGE_SCRIPT = String.raw`(() => {
  const parentWindow = window.parent;
  const nonce = '__NONCE__';
  const post = (message) => parentWindow.postMessage(Object.assign({ nonce }, message), '*');
  const reportError = (message) => post({ type: 'anki-frame-error', message: String(message) });
  window.addEventListener('error', (event) => reportError(event.message || 'script error'));
  window.addEventListener('unhandledrejection', (event) => {
    const reason = event.reason;
    reportError(reason && reason.message ? reason.message : reason);
  });
  try {
    const isLocalName = ${isLocalMediaName.toString()};
    const cache = new Map();
    // 已下載好的檔名 → blob URL：再次設定時同步換上
    const resolved = new Map();
    const waiting = new Map();
    let nextId = 0;
    window.addEventListener('message', (event) => {
      if (event.source !== parentWindow) return;
      const data = event.data;
      if (!data || data.type !== 'anki-media-response' || data.nonce !== nonce) return;
      const pending = waiting.get(data.id);
      if (!pending) return;
      waiting.delete(data.id);
      if (data.blob) pending.resolve(URL.createObjectURL(data.blob));
      else pending.reject(new Error(data.error || 'media error'));
    });
    const load = (name) => {
      let promise = cache.get(name);
      if (!promise) {
        promise = new Promise((resolve, reject) => {
          const id = ++nextId;
          const timer = setTimeout(() => {
            waiting.delete(id);
            reject(new Error(name + '：等待逾時'));
          }, 30000);
          waiting.set(id, {
            resolve: (url) => { clearTimeout(timer); resolve(url); },
            reject: (error) => { clearTimeout(timer); reject(error); },
          });
          post({ type: 'anki-media-request', id, name });
        });
        promise.then((url) => resolved.set(name, url), () => undefined);
        cache.set(name, promise);
      }
      return promise;
    };
    const pendingKey = Symbol('ankiMediaPending');
    const readyKey = Symbol('ankiMediaReady');
    const mediaTypes = [HTMLImageElement, HTMLMediaElement, HTMLSourceElement];
    // 各類型原本的 src setter：拿到 blob URL 後用它設定，不會再被攔一次
    const setters = new Map();
    const redirect = (element, name) => {
      const Ctor = mediaTypes.find((type) => element instanceof type);
      const setter = setters.get(Ctor);
      if (resolved.has(name)) {
        element[pendingKey] = null;
        element[readyKey] = true;
        setter.call(element, resolved.get(name));
        return;
      }
      element[readyKey] = false;
      const pending = load(name).then((url) => {
        setter.call(element, url);
        if (element[pendingKey] === pending) element[readyKey] = true;
      });
      // 失敗原因已由網頁顯示；這裡只避免 unhandled rejection，play() 仍會拿到真正的原因
      pending.catch(() => undefined);
      element[pendingKey] = pending;
    };
    for (const Ctor of mediaTypes) {
      const descriptor = Object.getOwnPropertyDescriptor(Ctor.prototype, 'src');
      setters.set(Ctor, descriptor.set);
      Object.defineProperty(Ctor.prototype, 'src', {
        configurable: true,
        enumerable: descriptor.enumerable,
        get() { return descriptor.get.call(this); },
        set(value) {
          if (isLocalName(value)) redirect(this, value);
          else descriptor.set.call(this, value);
        },
      });
    }
    const isMediaElement = (node) => mediaTypes.some((type) => node instanceof type);
    const originalSetAttribute = Element.prototype.setAttribute;
    Element.prototype.setAttribute = function (name, value) {
      if (String(name).toLowerCase() === 'src' && isMediaElement(this) && isLocalName(value)) {
        redirect(this, value);
        return;
      }
      return originalSetAttribute.call(this, name, value);
    };
    const OriginalAudio = window.Audio;
    window.Audio = function Audio(src) {
      const audio = new OriginalAudio();
      if (src !== undefined) audio.src = src;
      return audio;
    };
    window.Audio.prototype = OriginalAudio.prototype;
    const originalPlay = HTMLMediaElement.prototype.play;
    HTMLMediaElement.prototype.play = function () {
      const pending = this[pendingKey];
      return pending && !this[readyKey] ? pending.then(() => originalPlay.call(this)) : originalPlay.call(this);
    };
    document.addEventListener('click', (event) => {
      const button = event.target instanceof Element ? event.target.closest('[data-anki-sound]') : null;
      if (!button) return;
      event.preventDefault();
      new window.Audio(button.getAttribute('data-anki-sound')).play().catch(() => undefined);
    });
    // 捲動手勢轉給網頁（彈性回饋）；不攔、不取消，原生捲動照常接給外層
    const forward = (gesture) => post({ type: 'anki-scroll-gesture', gesture });
    window.addEventListener('wheel', (event) => {
      const scale = event.deltaMode === 1 ? 16 : event.deltaMode === 2 ? window.innerHeight : 1;
      forward({ kind: 'wheel', deltaY: event.deltaY * scale });
    }, { passive: true });
    for (const [type, phase] of [['touchstart', 'start'], ['touchmove', 'move'], ['touchend', 'end'], ['touchcancel', 'end']]) {
      window.addEventListener(type, (event) => {
        const touch = event.touches[0];
        forward({ kind: 'touch', phase, y: touch ? touch.screenY : undefined });
      }, { passive: true });
    }
    // 點卡面空白處：通知網頁。播放鈕的處理在前面，已 preventDefault，這裡會略過
    const controlSelector = 'a, button, input, textarea, select, label, summary, audio, video, [onclick], [role=button], [contenteditable], [data-anki-sound]';
    const hasSelection = () => {
      const selection = window.getSelection ? window.getSelection() : null;
      return Boolean(selection && !selection.isCollapsed);
    };
    let faceClickTimer = null;
    document.addEventListener('click', (event) => {
      if (faceClickTimer !== null) {
        clearTimeout(faceClickTimer);
        faceClickTimer = null;
      }
      if (event.defaultPrevented || event.button !== 0 || event.detail > 1 || hasSelection()) return;
      let node = event.target instanceof Element ? event.target : null;
      if (node && node.closest(controlSelector)) return;
      for (; node && node !== document.body && node !== document.documentElement; node = node.parentElement) {
        if (getComputedStyle(node).cursor === 'pointer') return;
      }
      faceClickTimer = setTimeout(() => {
        faceClickTimer = null;
        if (!hasSelection()) post({ type: 'anki-face-click' });
      }, 250);
    });
    // 播放鈕的檔案先下載，第一次點擊就能同步播放
    document.addEventListener('DOMContentLoaded', () => {
      document.querySelectorAll('[data-anki-sound]').forEach((button) => {
        const name = button.getAttribute('data-anki-sound');
        if (isLocalName(name)) load(name).catch(() => undefined);
      });
    });
    const fixElement = (element) => {
      if (!isMediaElement(element)) return;
      const value = element.getAttribute('src');
      if (isLocalName(value)) redirect(element, value);
    };
    new MutationObserver((records) => {
      for (const record of records) {
        if (record.type === 'attributes') fixElement(record.target);
        for (const node of record.addedNodes) {
          if (!(node instanceof Element)) continue;
          fixElement(node);
          node.querySelectorAll('img, audio, video, source').forEach(fixElement);
        }
      }
    }).observe(document.documentElement, { subtree: true, childList: true, attributes: true, attributeFilter: ['src'] });
    const parseCall = ${parseAnkiConnectCall.toString()};
    const reported = new Set();
    // 攔下的 AnkiConnect 呼叫：guiBrowse 請網頁切換卡片，其他動作提示一次「不支援」；回傳值給模板
    const handleCall = (call) => {
      if (call.action === 'guiBrowse' && call.params && typeof call.params.query === 'string') {
        // 沒有 userActivation 的瀏覽器照樣放行
        const activation = navigator.userActivation;
        if (!activation || activation.isActive) post({ type: 'anki-browse', query: call.params.query });
        return { result: [], error: null };
      }
      const action = call.action || '（無法辨識的請求）';
      if (!reported.has(action)) {
        reported.add(action);
        post({ type: 'anki-frame-notice', message: '網頁版不支援卡面呼叫的 Anki 功能（' + action + '），用到它的按鈕在這裡沒有作用。' });
      }
      return { result: null, error: 'unsupported in the web viewer: ' + action };
    };
    const originalFetch = window.fetch;
    window.fetch = function (input, init) {
      const url = input instanceof Request ? input.url : input;
      if (parseCall(url, '') === null) return originalFetch.apply(this, arguments);
      const bodyPromise = init && init.body !== undefined
        ? Promise.resolve(init.body)
        : input instanceof Request ? input.clone().text() : Promise.resolve(null);
      return bodyPromise.then((body) => {
        const reply = handleCall(parseCall(url, typeof body === 'string' ? body : null));
        return new Response(JSON.stringify(reply), { headers: { 'Content-Type': 'application/json' } });
      });
    };
    const xhrUrlKey = Symbol('ankiConnectUrl');
    const xhrAsyncKey = Symbol('ankiConnectAsync');
    const originalOpen = XMLHttpRequest.prototype.open;
    const originalSend = XMLHttpRequest.prototype.send;
    XMLHttpRequest.prototype.open = function (method, url) {
      this[xhrUrlKey] = parseCall(url, '') === null ? null : url;
      this[xhrAsyncKey] = arguments.length < 3 || Boolean(arguments[2]);
      return originalOpen.apply(this, arguments);
    };
    XMLHttpRequest.prototype.send = function (body) {
      const url = this[xhrUrlKey];
      if (!url) return originalSend.apply(this, arguments);
      handleCall(parseCall(url, typeof body === 'string' ? body : null));
      // 不送出，當成連線失敗：非同步請求之後收到 error 與 loadend（readyState 停在 1、status 0），
      // 同步請求直接返回
      if (!this[xhrAsyncKey]) return;
      setTimeout(() => {
        this.dispatchEvent(new ProgressEvent('error'));
        this.dispatchEvent(new ProgressEvent('loadend'));
      }, 0);
    };
    if (navigator.sendBeacon) {
      const originalBeacon = navigator.sendBeacon.bind(navigator);
      navigator.sendBeacon = (url, data) => {
        if (parseCall(url, '') === null) return originalBeacon(url, data);
        handleCall(parseCall(url, typeof data === 'string' ? data : null));
        return true;
      };
    }
    // 模板常設定 html/body 的高度與 margin，兩者都量，取較大者
    const reportHeight = () => {
      const body = document.body;
      const bodyHeight = body
        ? body.scrollHeight + parseFloat(getComputedStyle(body).marginTop) + parseFloat(getComputedStyle(body).marginBottom)
        : 0;
      post({ type: 'anki-frame-height', height: Math.ceil(Math.max(document.documentElement.scrollHeight, bodyHeight)) });
    };
    const observer = new ResizeObserver(reportHeight);
    observer.observe(document.documentElement);
    document.addEventListener('DOMContentLoaded', () => observer.observe(document.body));
    window.addEventListener('load', reportHeight);
  } catch (error) {
    reportError('卡面橋接腳本初始化失敗：' + (error && error.message ? error.message : error));
  }
})();`

/**
 * 卡面的基本樣式，放在卡面之前，模板自己的樣式可以覆寫：
 * - `overflow-wrap: anywhere`：有些模板用 `word-break: keep-all`，中日文只在空白與標點換行，
 *   iframe 比 Anki 視窗窄時整行會超出邊界；這只在一行放不下時才斷，放得下時保留原本的斷行。
 * - 後端換上的播放鈕（`data-anki-sound`）的外觀。
 * Base styles for the face, overridable by the template.
 */
const FRAME_BASE_STYLE = `html{overflow-wrap:anywhere}
.replay-button[data-anki-sound]{cursor:pointer;border:1px solid currentColor;border-radius:9999px;background:transparent;color:inherit;width:2em;height:2em;margin:0 .2em;vertical-align:middle;opacity:.8}
.replay-button[data-anki-sound]:hover{opacity:1}`

/**
 * 組出 iframe 的 `srcdoc`（最前面是 {@link CARD_FRAME_CSP}）。`<body>` 帶上 Anki 審閱畫面的 `card card{n}` class，模板的 `.card`
 * 樣式才會套用（不含 `nightMode`）；`<base target="_blank">` 讓連結開新分頁，這要搭配
 * iframe 的 `allow-popups` 才有效。
 *
 * Builds the iframe document. The body gets Anki's `card card{n}` classes;
 * links open in a new tab, which needs the frame's `allow-popups`.
 *
 * @param faceHtml - Anki 渲染好的正面或背面 HTML。The rendered face.
 * @param ord - 卡片模板序號（0 起算）。Template ordinal.
 * @param nonce - 這份文件的識別碼，只含英數字。Document identifier (alphanumeric).
 * @returns 完整的 HTML 文件。The full document.
 */
export function buildCardDocument(faceHtml: string, ord: number, nonce: string): string {
  return [
    '<!doctype html>',
    `<html><head><meta charset="utf-8"><meta http-equiv="Content-Security-Policy" content="${CARD_FRAME_CSP}">`,
    '<meta name="viewport" content="width=device-width, initial-scale=1">',
    '<base target="_blank">',
    `<script>${FRAME_BRIDGE_SCRIPT.replace('__NONCE__', nonce)}</script>`,
    `<style>${FRAME_BASE_STYLE}</style>`,
    '</head>',
    `<body class="card card${ord + 1}">`,
    faceHtml,
    '</body></html>',
  ].join('\n')
}

/** {@link createFrameMessageHandler} 的相依物件。Dependencies of the frame message handler. */
export interface FrameMessageDeps {
  /** 這個 iframe 目前的視窗；只處理它送來的訊息。The frame's window. */
  frameWindow: () => Window | null | undefined
  /** 這份文件的識別碼。The current document's nonce. */
  nonce: string
  /** 下載一個媒體檔。Fetches a media file. */
  fetchMedia: (name: string) => Promise<Blob>
  /** 錯誤的可讀說明。Describes an error for display. */
  describeError: (error: unknown) => string
  onHeight: (height: number) => void
  onMediaFailed: (name: string, reason: string, error?: unknown) => void
  onFrameError: (message: string) => void
  /** 模板要打開這些筆記（「查看母卡片」等按鈕）。The template asks to open these notes. */
  onBrowse: (noteIds: number[]) => void
  /** 模板呼叫了網頁版不支援的功能。The template used something the web viewer lacks. */
  onNotice: (message: string) => void
  /** 使用者點了卡面上不是控制項的地方。A click on the face outside any control. */
  onFaceClick: () => void
  /** 卡面內的捲動手勢。A scroll gesture inside the face. */
  onScrollGesture: (gesture: ScrollGesture) => void
  /** 這份文件是否仍是目前顯示的那份（換文件後，進行中的下載不再回覆）。Still current? */
  isActive: () => boolean
}

/**
 * 網頁端處理 iframe 訊息的函式（與 React 無關，方便測試）。
 *
 * - 只處理這個 iframe、這份文件（nonce 相同）的訊息；回覆只送給送出請求的視窗。iframe 是
 *   opaque origin，postMessage 只能指定 `'*'`；iframe 內的文件由我們的 srcdoc 決定，所以
 *   限定接收的視窗即可。
 * - 不是單一檔名的請求也會回覆錯誤，iframe 端才不會一直等。
 *
 * The parent-side message handler, React-free for testing.
 *
 * @param deps - 相依物件。Dependencies.
 * @returns `message` 事件的處理函式。The event handler.
 */
export function createFrameMessageHandler(deps: FrameMessageDeps): (event: MessageEvent) => void {
  return (event: MessageEvent) => {
    const frameWindow = deps.frameWindow()
    if (!frameWindow || event.source !== frameWindow) return
    // 來自 iframe 的資料不可信：先當成鬆散的形狀，逐欄檢查
    const data = event.data as {
      type?: unknown
      nonce?: unknown
      height?: unknown
      message?: unknown
      id?: unknown
      name?: unknown
      query?: unknown
      gesture?: unknown
    } | null
    if (typeof data !== 'object' || data === null || data.nonce !== deps.nonce) return

    if (data.type === 'anki-frame-height') {
      if (typeof data.height === 'number' && Number.isFinite(data.height) && data.height >= 0) {
        deps.onHeight(Math.min(data.height, MAX_FRAME_HEIGHT))
      }
      return
    }
    if (data.type === 'anki-frame-error') {
      deps.onFrameError(String(data.message ?? ''))
      return
    }
    if (data.type === 'anki-frame-notice') {
      deps.onNotice(String(data.message ?? ''))
      return
    }
    if (data.type === 'anki-face-click') {
      deps.onFaceClick()
      return
    }
    if (data.type === 'anki-scroll-gesture') {
      const gesture = parseScrollGesture(data.gesture)
      if (gesture) deps.onScrollGesture(gesture)
      return
    }
    if (data.type === 'anki-browse') {
      const query = typeof data.query === 'string' ? data.query : ''
      const noteIds = parseNoteQuery(query)
      if (noteIds) deps.onBrowse(noteIds)
      else deps.onNotice(`網頁版只能打開指定筆記 ID 的卡片（nid:…），不支援這個搜尋：${query.slice(0, 100)}`)
      return
    }
    if (data.type !== 'anki-media-request' || typeof data.id !== 'number' || typeof data.name !== 'string') return

    const { id, name } = data
    const reply = (message: MediaResponseMessage) => {
      if (deps.isActive()) frameWindow.postMessage(message, '*')
    }
    if (!isBareMediaName(name)) {
      const reason = '不是媒體資料夾中的單一檔名（含路徑），不予載入'
      deps.onMediaFailed(name, reason)
      reply({ type: 'anki-media-response', nonce: deps.nonce, id, error: reason })
      return
    }
    deps.fetchMedia(name).then(
      (blob) => reply({ type: 'anki-media-response', nonce: deps.nonce, id, blob }),
      (error: unknown) => {
        if (!deps.isActive()) return
        const reason = deps.describeError(error)
        deps.onMediaFailed(name, reason, error)
        reply({ type: 'anki-media-response', nonce: deps.nonce, id, error: reason })
      },
    )
  }
}
