/**
 * L2.4c：桌面端后端就绪门控组件。
 *
 * 配合 main.js L2.4a（loadFile 提前到 waitForHealth 之前）+ preload.js L2.4b
 * （onBackendReady/onBackendError/pollBackendHealth IPC 通道）使用。
 *
 * 工作机制：
 * 1. 桌面端启动时 main.js 立即 loadFile 让 UI 渲染（T+1-3s），此时后端尚未 ready
 * 2. BackendGate 默认 connecting 状态，显示 spinner + 中文文案 + 计时
 * 3. main.js waitForHealth 完成后通过 IPC 发 'dsa:backend-ready' 事件
 * 4. BackendGate 收到事件切到 ready 状态，渲染 children（AppContent）
 * 5. 兜底：若 IPC 事件丢失，BackendGate 每 500ms 主动轮询 /api/v1/health
 * 6. main.js 失败时发 'dsa:backend-error' 事件，BackendGate 切到 error 状态
 *
 * Web 端（非桌面端运行时）直接渲染 children，由 AuthProvider 自己处理网络错误。
 */

import { useEffect, useState, type ReactNode } from 'react';
import { desktopBridgeInstance, isDesktopRuntime } from '../utils/desktop';

type BackendStatus = 'connecting' | 'ready' | 'error';

interface BackendGateProps {
  children: ReactNode;
}

const POLL_INTERVAL_MS = 500;

export function BackendGate({ children }: BackendGateProps) {
  // 非桌面端（web 模式）默认 ready，由 AuthProvider 自己处理网络错误；
  // 桌面端默认 connecting，等待 IPC 事件或 polling 兜底切到 ready/error
  const [status, setStatus] = useState<BackendStatus>(
    isDesktopRuntime ? 'connecting' : 'ready'
  );
  const [elapsed, setElapsed] = useState(0);

  useEffect(() => {
    // 非桌面端无需监听 IPC，直接退出
    if (!isDesktopRuntime) {
      return;
    }

    const startTime = Date.now();
    const elapsedTimer = setInterval(() => setElapsed(Date.now() - startTime), 500);

    const bridge = desktopBridgeInstance;

    const unsubReady = bridge?.onBackendReady?.(() => {
      setStatus('ready');
    });
    const unsubError = bridge?.onBackendError?.(() => {
      setStatus('error');
    });

    // 兜底：IPC 事件丢失时主动轮询 health，避免永久卡 connecting
    let pollTimer: ReturnType<typeof setInterval> | undefined;
    if (bridge?.pollBackendHealth && bridge.backendUrl) {
      pollTimer = setInterval(async () => {
        const ok = await bridge.pollBackendHealth!(bridge.backendUrl!);
        if (ok) {
          setStatus('ready');
        }
      }, POLL_INTERVAL_MS);
    }

    return () => {
      clearInterval(elapsedTimer);
      if (pollTimer) clearInterval(pollTimer);
      unsubReady?.();
      unsubError?.();
    };
  }, []);

  if (!isDesktopRuntime || status === 'ready') {
    return <>{children}</>;
  }

  if (status === 'error') {
    return (
      <div className="backend-gate backend-gate-error">
        <div className="backend-gate-icon" aria-hidden="true">⚠</div>
        <h2>后端启动失败</h2>
        <p>请查看 logs/desktop.log 排查原因，或重启应用。</p>
      </div>
    );
  }

  return (
    <div className="backend-gate backend-gate-connecting">
      <div className="backend-gate-spinner" aria-label="loading" role="progressbar" />
      <h2>正在连接后端服务...</h2>
      <p>已等待 {(elapsed / 1000).toFixed(1)} 秒</p>
      <p className="backend-gate-tip">通常需要 15-30 秒，请耐心等待</p>
    </div>
  );
}

