/**
 * DashboardSection — 仪表盘内容区域包装器。
 * 为 HomePage 等页面提供统一的视觉分隔和标签。
 */
import type React from 'react';
import { cn } from '@/utils/cn';

export interface DashboardSectionProps {
  /** 区域标题标签 */
  label?: React.ReactNode;
  /** 区域内容 */
  children: React.ReactNode;
  /** 额外 CSS 类 */
  className?: string;
  /** 是否在标签上方显示分隔线 */
  showDivider?: boolean;
}

export const DashboardSection: React.FC<DashboardSectionProps> = ({
  label,
  children,
  className,
  showDivider = false,
}) => {
  return (
    <div className={cn('dashboard-section', className)}>
      {showDivider ? (
        <hr className="dashboard-section-divider" />
      ) : null}
      {label ? (
        <div className="dashboard-section-label">{label}</div>
      ) : null}
      <div className="dashboard-section-body">{children}</div>
    </div>
  );
};
