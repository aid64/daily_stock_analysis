/**
 * 桌面端运行时检测工具。
 *
 * Electron 桌面端通过 preload.js 向 window.dsaDesktop 注入桥接对象，
 * 包含窗口控制 (windowControls) 与启动进度 (onStartupProgress) 等 API。
 * Web 端不存在该对象，因此可用于区分运行环境。
 */

interface DesktopWindowControls {
  minimize: () => Promise<void>;
  toggleMaximize: () => Promise<boolean>;
  close: () => Promise<void>;
  isMaximized: () => Promise<boolean>;
  onMaximizeChange: (listener: (isMaximized: boolean) => void) => () => void;
}

interface DesktopBridge {
  /** 桌面端版本号（来自 app.getVersion()） */
  version?: string;
  /**
   * 后端 API 基址（仅打包模式有效）。
   * 打包模式下前端通过 file:// 加载，需要绝对地址才能访问后端；
   * dev 模式下前端由后端 HTTP 同源提供，此字段为空字符串。
   */
  backendUrl?: string;
  windowControls?: DesktopWindowControls;
  onStartupProgress?: (listener: (payload: unknown) => void) => () => void;
  /**
   * L2.4b：后端就绪事件监听（main → renderer）。
   * main.js 在 waitForHealth 成功后发出 'dsa:backend-ready' 事件，
   * BackendGate 收到后切到 ready 状态并渲染 App 内容。
   */
  onBackendReady?: (listener: (payload: unknown) => void) => () => void;
  /**
   * L2.4b：后端失败事件监听（main → renderer）。
   * main.js 在 waitForHealth 抛错时发出 'dsa:backend-error' 事件，
   * BackendGate 收到后切到 error 状态显示错误提示。
   */
  onBackendError?: (listener: (payload: unknown) => void) => () => void;
  /**
   * L2.4b：主动轮询后端 health（兜底机制）。
   * IPC 事件丢失时 BackendGate 用此方法每 500ms 轮询 /api/v1/health，
   * 避免永久卡在 connecting 状态。
   */
  pollBackendHealth?: (backendUrl: string) => Promise<boolean>;
  [key: string]: unknown;
}

function readDesktopBridge(): DesktopBridge | undefined {
  if (typeof window === 'undefined') return undefined;
  return (window as unknown as { dsaDesktop?: DesktopBridge }).dsaDesktop;
}

const desktopBridge = readDesktopBridge();

/**
 * 桌面端 bridge 对象本身（preload 注入）。
 * L2.4c：BackendGate 需要直接访问 onBackendReady/onBackendError/pollBackendHealth，
 * 而非通过预导出的单个字段，因为后端 ready/error 事件是启动阶段一次性消费的。
 */
export const desktopBridgeInstance: DesktopBridge | undefined = desktopBridge;

/** 是否运行在 Electron 桌面端（preload 已注入 windowControls） */
export const isDesktopRuntime = Boolean(desktopBridge?.windowControls);

/** 桌面端窗口控制 API；Web 端为 undefined */
export const desktopWindowControls: DesktopWindowControls | undefined =
  desktopBridge?.windowControls;

/**
 * 桌面端后端 API 基址。
 * - 打包模式：形如 `http://127.0.0.1:8765`，供 file:// 加载的前端发起跨域请求。
 * - dev 模式：空字符串，前端走同源相对路径（由后端 FastAPI 提供）。
 * - Web 端：undefined。
 */
export const desktopBackendUrl: string | undefined = desktopBridge?.backendUrl;

/** 是否为 macOS 桌面端（保留原生交通灯，不自绘窗口控制按钮） */
export function isMacDesktopRuntime(): boolean {
  if (!isDesktopRuntime) return false;
  if (typeof navigator === 'undefined') return false;
  const platform =
    navigator.platform ||
    (navigator as unknown as { userAgentData?: { platform?: string } }).userAgentData?.platform ||
    '';
  return /mac/i.test(platform);
}