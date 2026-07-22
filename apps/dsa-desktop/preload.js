const { contextBridge, ipcRenderer } = require('electron');

const DESKTOP_VERSION_ARG_PREFIX = '--dsa-desktop-version=';
const DESKTOP_GET_UPDATE_STATE_CHANNEL = 'desktop:get-update-state';
const DESKTOP_CHECK_FOR_UPDATES_CHANNEL = 'desktop:check-for-updates';
const DESKTOP_INSTALL_DOWNLOADED_UPDATE_CHANNEL = 'desktop:install-downloaded-update';
const DESKTOP_OPEN_RELEASE_PAGE_CHANNEL = 'desktop:open-release-page';
const DESKTOP_UPDATE_STATE_EVENT = 'desktop:update-state';

// 窗口控制 IPC 通道（PR1 新增：自绘标题栏使用）
const WINDOW_MINIMIZE_CHANNEL = 'window:minimize';
const WINDOW_TOGGLE_MAXIMIZE_CHANNEL = 'window:toggle-maximize';
const WINDOW_CLOSE_CHANNEL = 'window:close';
const WINDOW_IS_MAXIMIZED_CHANNEL = 'window:is-maximized';
const WINDOW_MAXIMIZE_CHANGED_EVENT = 'window:maximize-changed';

// 启动进度事件通道（main → renderer，loading.html 消费）
const STARTUP_PROGRESS_EVENT = 'startup-progress';

// PR2 新增：后端 API 基址同步 IPC 通道。
// 打包模式下前端通过 file:// 加载，必须在模块初始化时同步拿到后端绝对地址；
// main 进程在端口确定后写入 resolvedBackendUrl，renderer 通过 sendSync 读取。
const DESKTOP_GET_BACKEND_URL_CHANNEL = 'desktop:get-backend-url';

function readDesktopVersion(argv = process.argv) {
  const versionArg = argv.find(
    (value) => typeof value === 'string' && value.startsWith(DESKTOP_VERSION_ARG_PREFIX)
  );
  return versionArg ? versionArg.slice(DESKTOP_VERSION_ARG_PREFIX.length) : '';
}

function createDesktopBridge({
  version = readDesktopVersion(),
  renderer = ipcRenderer,
} = {}) {
  // PR2：同步读取后端 API 基址。
  // - 打包模式：main 进程在端口确定后设置 resolvedBackendUrl，此处返回形如 http://127.0.0.1:8765。
  // - dev 模式：前端由后端 HTTP 同源提供，此处返回空字符串（无需跨域）。
  // - loading 页面加载时端口可能尚未确定，同样返回空字符串，loading 页不消费此字段。
  let backendUrl = '';
  try {
    backendUrl = renderer.sendSync(DESKTOP_GET_BACKEND_URL_CHANNEL) || '';
  } catch (_error) {
    // sendSync 在某些测试环境下可能没有 main 监听器，保留空字符串即可。
    backendUrl = '';
  }

  return {
    version,
    backendUrl,
    // 更新相关 API（保留不动）
    getUpdateState() {
      return renderer.invoke(DESKTOP_GET_UPDATE_STATE_CHANNEL);
    },
    checkForUpdates() {
      return renderer.invoke(DESKTOP_CHECK_FOR_UPDATES_CHANNEL);
    },
    installDownloadedUpdate() {
      return renderer.invoke(DESKTOP_INSTALL_DOWNLOADED_UPDATE_CHANNEL);
    },
    openReleasePage(releaseUrl) {
      return renderer.invoke(DESKTOP_OPEN_RELEASE_PAGE_CHANNEL, releaseUrl);
    },
    onUpdateStateChange(listener) {
      if (typeof listener !== 'function') {
        return () => undefined;
      }

      const handler = (_event, payload) => {
        listener(payload);
      };
      renderer.on(DESKTOP_UPDATE_STATE_EVENT, handler);
      return () => {
        renderer.removeListener(DESKTOP_UPDATE_STATE_EVENT, handler);
      };
    },
    // 窗口控制 API（PR1 新增，供自绘标题栏调用）
    windowControls: {
      minimize() {
        return renderer.invoke(WINDOW_MINIMIZE_CHANNEL);
      },
      toggleMaximize() {
        return renderer.invoke(WINDOW_TOGGLE_MAXIMIZE_CHANNEL);
      },
      close() {
        return renderer.invoke(WINDOW_CLOSE_CHANNEL);
      },
      isMaximized() {
        return renderer.invoke(WINDOW_IS_MAXIMIZED_CHANNEL);
      },
      onMaximizeChange(listener) {
        if (typeof listener !== 'function') {
          return () => undefined;
        }

        const handler = (_event, isMaximized) => {
          listener(isMaximized);
        };
        renderer.on(WINDOW_MAXIMIZE_CHANGED_EVENT, handler);
        return () => {
          renderer.removeListener(WINDOW_MAXIMIZE_CHANGED_EVENT, handler);
        };
      },
    },
    // 启动进度事件（main → renderer，loading.html 显示当前阶段）
    onStartupProgress(listener) {
      if (typeof listener !== 'function') {
        return () => undefined;
      }

      const handler = (_event, payload) => {
        listener(payload);
      };
      renderer.on(STARTUP_PROGRESS_EVENT, handler);
      return () => {
        renderer.removeListener(STARTUP_PROGRESS_EVENT, handler);
      };
    },
  };
}

contextBridge.exposeInMainWorld('dsaDesktop', createDesktopBridge());

module.exports = {
  DESKTOP_CHECK_FOR_UPDATES_CHANNEL,
  DESKTOP_GET_UPDATE_STATE_CHANNEL,
  DESKTOP_INSTALL_DOWNLOADED_UPDATE_CHANNEL,
  DESKTOP_OPEN_RELEASE_PAGE_CHANNEL,
  DESKTOP_UPDATE_STATE_EVENT,
  DESKTOP_VERSION_ARG_PREFIX,
  DESKTOP_GET_BACKEND_URL_CHANNEL,
  WINDOW_MINIMIZE_CHANNEL,
  WINDOW_TOGGLE_MAXIMIZE_CHANNEL,
  WINDOW_CLOSE_CHANNEL,
  WINDOW_IS_MAXIMIZED_CHANNEL,
  WINDOW_MAXIMIZE_CHANGED_EVENT,
  STARTUP_PROGRESS_EVENT,
  createDesktopBridge,
  readDesktopVersion,
};
