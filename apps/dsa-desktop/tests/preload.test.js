const assert = require('node:assert/strict');
const test = require('node:test');
const Module = require('node:module');

test('preload exposes desktop version from BrowserWindow additionalArguments', (t) => {
  const originalLoad = Module._load;
  const originalArgv = [...process.argv];
  const exposeInMainWorldCalls = [];
  const expectedVersion = '3.12.0';
  const ipcRenderer = {
    invoke: () => Promise.resolve(),
    on: () => undefined,
    removeListener: () => undefined,
  };

  Module._load = function patchedLoad(request, parent, isMain) {
    if (request === 'electron') {
      return {
        contextBridge: {
          exposeInMainWorld: (...args) => {
            exposeInMainWorldCalls.push(args);
          },
        },
        ipcRenderer,
      };
    }
    return originalLoad.call(this, request, parent, isMain);
  };

  const preloadPath = require.resolve('../preload.js');
  delete require.cache[preloadPath];
  process.argv = [...originalArgv, `--dsa-desktop-version=${expectedVersion}`];

  t.after(() => {
    Module._load = originalLoad;
    process.argv = originalArgv;
    delete require.cache[preloadPath];
  });

  const preloadModule = require('../preload.js');

  assert.equal(exposeInMainWorldCalls.length, 1);
  assert.equal(exposeInMainWorldCalls[0][0], 'dsaDesktop');
  assert.equal(exposeInMainWorldCalls[0][1].version, expectedVersion);
  assert.equal(typeof exposeInMainWorldCalls[0][1].getUpdateState, 'function');
  assert.equal(typeof exposeInMainWorldCalls[0][1].checkForUpdates, 'function');
  assert.equal(typeof exposeInMainWorldCalls[0][1].installDownloadedUpdate, 'function');
  assert.equal(typeof exposeInMainWorldCalls[0][1].openReleasePage, 'function');
  assert.equal(typeof exposeInMainWorldCalls[0][1].onUpdateStateChange, 'function');
  assert.equal(
    preloadModule.readDesktopVersion([`--dsa-desktop-version=${expectedVersion}`]),
    expectedVersion
  );
});

test('preload falls back to empty version when BrowserWindow does not pass one', (t) => {
  const originalLoad = Module._load;
  const originalArgv = [...process.argv];
  const exposeInMainWorldCalls = [];
  const ipcRenderer = {
    invoke: () => Promise.resolve(),
    on: () => undefined,
    removeListener: () => undefined,
  };

  Module._load = function patchedLoad(request, parent, isMain) {
    if (request === 'electron') {
      return {
        contextBridge: {
          exposeInMainWorld: (...args) => {
            exposeInMainWorldCalls.push(args);
          },
        },
        ipcRenderer,
      };
    }
    return originalLoad.call(this, request, parent, isMain);
  };

  const preloadPath = require.resolve('../preload.js');
  delete require.cache[preloadPath];
  process.argv = originalArgv.filter((value) => !value.startsWith('--dsa-desktop-version='));

  t.after(() => {
    Module._load = originalLoad;
    process.argv = originalArgv;
    delete require.cache[preloadPath];
  });

  const preloadModule = require('../preload.js');

  assert.equal(exposeInMainWorldCalls.length, 1);
  assert.equal(exposeInMainWorldCalls[0][0], 'dsaDesktop');
  assert.equal(exposeInMainWorldCalls[0][1].version, '');
  assert.equal(preloadModule.readDesktopVersion(['--unrelated=1']), '');
});

test('createDesktopBridge delegates update actions to ipcRenderer', async (t) => {
  const originalLoad = Module._load;
  const listeners = new Map();
  const ipcRenderer = {
    invoke: async (channel, payload) => ({ channel, payload }),
    on: (channel, listener) => {
      listeners.set(channel, listener);
    },
    removeListener: (channel, listener) => {
      const current = listeners.get(channel);
      if (current === listener) {
        listeners.delete(channel);
      }
    },
  };

  const preloadPath = require.resolve('../preload.js');
  t.after(() => {
    Module._load = originalLoad;
    delete require.cache[preloadPath];
  });

  Module._load = function patchedLoad(request, parent, isMain) {
    if (request === 'electron') {
      return {
        contextBridge: {
          exposeInMainWorld: () => undefined,
        },
        ipcRenderer,
      };
    }
    return originalLoad.call(this, request, parent, isMain);
  };

  delete require.cache[preloadPath];
  const preloadModule = require('../preload.js');
  const desktopBridge = preloadModule.createDesktopBridge({
    version: '3.12.0',
    renderer: ipcRenderer,
  });

  assert.deepEqual(await desktopBridge.getUpdateState(), {
    channel: preloadModule.DESKTOP_GET_UPDATE_STATE_CHANNEL,
    payload: undefined,
  });
  assert.deepEqual(await desktopBridge.checkForUpdates(), {
    channel: preloadModule.DESKTOP_CHECK_FOR_UPDATES_CHANNEL,
    payload: undefined,
  });
  assert.deepEqual(await desktopBridge.installDownloadedUpdate(), {
    channel: preloadModule.DESKTOP_INSTALL_DOWNLOADED_UPDATE_CHANNEL,
    payload: undefined,
  });
  assert.deepEqual(await desktopBridge.openReleasePage('https://github.com/ZhuLinsen/daily_stock_analysis/releases/tag/v3.13.0'), {
    channel: preloadModule.DESKTOP_OPEN_RELEASE_PAGE_CHANNEL,
    payload: 'https://github.com/ZhuLinsen/daily_stock_analysis/releases/tag/v3.13.0',
  });

  const receivedPayloads = [];
  const unsubscribe = desktopBridge.onUpdateStateChange((payload) => {
    receivedPayloads.push(payload);
  });
  listeners.get(preloadModule.DESKTOP_UPDATE_STATE_EVENT)(null, { status: 'update-available' });
  unsubscribe();

  assert.deepEqual(receivedPayloads, [{ status: 'update-available' }]);
  assert.equal(listeners.has(preloadModule.DESKTOP_UPDATE_STATE_EVENT), false);
});

test('createDesktopBridge delegates backend ready/error events to ipcRenderer', (t) => {
  const originalLoad = Module._load;
  const listeners = new Map();
  const ipcRenderer = {
    invoke: async () => undefined,
    on: (channel, listener) => {
      listeners.set(channel, listener);
    },
    removeListener: (channel, listener) => {
      const current = listeners.get(channel);
      if (current === listener) {
        listeners.delete(channel);
      }
    },
  };

  const preloadPath = require.resolve('../preload.js');
  t.after(() => {
    Module._load = originalLoad;
    delete require.cache[preloadPath];
  });

  Module._load = function patchedLoad(request, parent, isMain) {
    if (request === 'electron') {
      return {
        contextBridge: { exposeInMainWorld: () => undefined },
        ipcRenderer,
      };
    }
    return originalLoad.call(this, request, parent, isMain);
  };

  delete require.cache[preloadPath];
  const preloadModule = require('../preload.js');
  const desktopBridge = preloadModule.createDesktopBridge({
    version: '3.12.0',
    renderer: ipcRenderer,
  });

  // onBackendReady: 订阅 BACKEND_READY_EVENT,收到事件后 listener 被以 payload 调用
  const readyPayloads = [];
  const unsubReady = desktopBridge.onBackendReady((payload) => {
    readyPayloads.push(payload);
  });
  assert.equal(listeners.has(preloadModule.BACKEND_READY_EVENT), true);
  listeners.get(preloadModule.BACKEND_READY_EVENT)(null, { backendUrl: 'http://127.0.0.1:8765' });
  assert.deepEqual(readyPayloads, [{ backendUrl: 'http://127.0.0.1:8765' }]);
  unsubReady();
  assert.equal(listeners.has(preloadModule.BACKEND_READY_EVENT), false);

  // onBackendError: 订阅 BACKEND_ERROR_EVENT,收到事件后 listener 被以 payload 调用
  const errorPayloads = [];
  const unsubError = desktopBridge.onBackendError((payload) => {
    errorPayloads.push(payload);
  });
  assert.equal(listeners.has(preloadModule.BACKEND_ERROR_EVENT), true);
  listeners.get(preloadModule.BACKEND_ERROR_EVENT)(null, { message: 'timeout' });
  assert.deepEqual(errorPayloads, [{ message: 'timeout' }]);
  unsubError();
  assert.equal(listeners.has(preloadModule.BACKEND_ERROR_EVENT), false);

  // onBackendReady/onBackendError 对非函数 listener 返回 no-op unsubscribe,不抛错
  const noopReady = desktopBridge.onBackendReady(null);
  const noopError = desktopBridge.onBackendError(undefined);
  assert.equal(typeof noopReady, 'function');
  assert.equal(typeof noopError, 'function');
  assert.equal(noopReady(), undefined);
  assert.equal(noopError(), undefined);
});

test('createDesktopBridge.pollBackendHealth returns response.ok and tolerates fetch errors', async (t) => {
  const originalLoad = Module._load;
  const originalFetch = globalThis.fetch;
  const fetchCalls = [];
  const ipcRenderer = {
    invoke: async () => undefined,
    on: () => undefined,
    removeListener: () => undefined,
  };

  const preloadPath = require.resolve('../preload.js');
  t.after(() => {
    Module._load = originalLoad;
    globalThis.fetch = originalFetch;
    delete require.cache[preloadPath];
  });

  Module._load = function patchedLoad(request, parent, isMain) {
    if (request === 'electron') {
      return {
        contextBridge: { exposeInMainWorld: () => undefined },
        ipcRenderer,
      };
    }
    return originalLoad.call(this, request, parent, isMain);
  };

  delete require.cache[preloadPath];
  const preloadModule = require('../preload.js');
  const desktopBridge = preloadModule.createDesktopBridge({
    version: '3.12.0',
    renderer: ipcRenderer,
  });

  // Happy path:response.ok=true → pollBackendHealth 返回 true,URL 拼接正确
  globalThis.fetch = async (url, options) => {
    fetchCalls.push({ url, options });
    return { ok: true };
  };
  const ok = await desktopBridge.pollBackendHealth('http://127.0.0.1:8765');
  assert.equal(ok, true);
  assert.equal(fetchCalls.length, 1);
  assert.equal(fetchCalls[0].url, 'http://127.0.0.1:8765/api/v1/health');

  // response.ok=false → 返回 false
  globalThis.fetch = async () => ({ ok: false });
  const notOk = await desktopBridge.pollBackendHealth('http://127.0.0.1:8765');
  assert.equal(notOk, false);

  // fetch 抛错 → 返回 false,不向上抛
  globalThis.fetch = async () => {
    throw new Error('network unreachable');
  };
  const errored = await desktopBridge.pollBackendHealth('http://127.0.0.1:8765');
  assert.equal(errored, false);
});
