'use client';

import React from 'react';
import { Menu } from 'lucide-react';
import { useSidebar } from '@/components/Sidebar/SidebarProvider';

interface MainContentProps {
  children: React.ReactNode;
}

const MainContent: React.FC<MainContentProps> = ({ children }) => {
  const { isCollapsed, setMobileOpen } = useSidebar();

  return (
    <main
      className={`flex-1 transition-all duration-300 h-screen max-w-full flex flex-col overflow-hidden ${
        // On mobile the sidebar is an off-screen drawer — no margin needed.
        // On md+ the sidebar is a fixed rail, so respect its width.
        isCollapsed ? 'md:ml-16' : 'md:ml-64'
      }`}
    >
      {/* Mobile top-bar: visible on every page below md breakpoint */}
      <div className="md:hidden flex items-center gap-3 px-4 py-3 bg-white border-b border-gray-200 shrink-0 z-10">
        <button
          onClick={() => setMobileOpen(true)}
          aria-label="Open menu"
          className="p-2 rounded-lg text-gray-600 hover:bg-gray-100 transition-colors"
        >
          <Menu className="w-5 h-5" />
        </button>
        <span className="font-semibold text-gray-900 text-sm">Pnyx</span>
      </div>

      <div className="flex-1 w-full h-full relative overflow-y-auto">
        {children}
      </div>
    </main>
  );
};

export default MainContent;
