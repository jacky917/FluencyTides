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
 */

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
 * - `play()` 等檔案到了才播放；檔案載入失敗時以真正的原因 reject。
 * - 點擊 `data-anki-sound` 播放鈕時播放該檔案。
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
        cache.set(name, promise);
      }
      return promise;
    };
    const pendingKey = Symbol('ankiMediaPending');
    const mediaTypes = [HTMLImageElement, HTMLMediaElement, HTMLSourceElement];
    // 各類型原本的 src setter：拿到 blob URL 後用它設定，不會再被攔一次
    const setters = new Map();
    const redirect = (element, name) => {
      const Ctor = mediaTypes.find((type) => element instanceof type);
      const setter = setters.get(Ctor);
      const pending = load(name).then((url) => setter.call(element, url));
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
      return pending ? pending.then(() => originalPlay.call(this)) : originalPlay.call(this);
    };
    document.addEventListener('click', (event) => {
      const button = event.target instanceof Element ? event.target.closest('[data-anki-sound]') : null;
      if (!button) return;
      event.preventDefault();
      new window.Audio(button.getAttribute('data-anki-sound')).play().catch(() => undefined);
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
 * 組出 iframe 的 `srcdoc`。`<body>` 帶上 Anki 審閱畫面的 `card card{n}` class，模板的 `.card`
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
    '<html><head><meta charset="utf-8">',
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
    const data = event.data as { type?: unknown; nonce?: unknown; height?: unknown; message?: unknown; id?: unknown; name?: unknown } | null
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
