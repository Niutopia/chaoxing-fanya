import React from 'react'
import { Menu } from 'lucide-react'
import { cn } from '../../lib/utils'

const Titlebar = React.forwardRef(function Titlebar(
  {
    title = '超新星 · 学习助手',
    onMenuToggle,
    sidebarOpen = false,
    className,
  },
  ref,
) {
  return (
    <header
      ref={ref}
      className={cn(
        'shell-material flex h-11 shrink-0 items-center border-b border-separator px-3',
        className,
      )}
    >
      <button
        type="button"
        className="touch-target touch-target-compact inline-flex size-8 items-center justify-center rounded-md text-label-secondary hover:bg-black/[0.06] hover:text-label-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-blue focus-visible:ring-offset-1 md:hidden"
        aria-label={sidebarOpen ? '关闭侧边栏' : '打开侧边栏'}
        aria-controls="account-sidebar"
        aria-expanded={sidebarOpen}
        onClick={onMenuToggle}
      >
        <Menu aria-hidden="true" size={17} strokeWidth={1.8} />
      </button>
      <div className="flex min-w-0 items-center gap-2 md:pl-1">
        <img
          src="/supernova.png"
          alt=""
          aria-hidden="true"
          className="size-7 shrink-0 object-contain"
        />
        <span className="truncate text-sm font-semibold text-label-primary">{title}</span>
      </div>
    </header>
  )
})

Titlebar.displayName = 'Titlebar'

export { Titlebar }
export default Titlebar
