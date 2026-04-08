'use client';

import { usePathname } from 'next/navigation';
import Sidebar from '@/components/Sidebar';
import MainContent from '@/components/MainContent';
import { SidebarProvider } from '@/components/Sidebar/SidebarProvider';
import { CalendarConnectPrompt } from '@/components/CalendarConnectPrompt';
import { RecordingStateProvider } from '@/contexts/RecordingStateContext';
import { PersistentRecordingRuntime } from '@/components/PersistentRecordingRuntime';
import { TooltipProvider } from '@/components/ui/tooltip';

export default function LayoutClient({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const isAuthPage = pathname === '/login';

  if (isAuthPage) {
    return <div className="min-h-screen">{children}</div>;
  }

  return (
    <RecordingStateProvider>
      <SidebarProvider>
        <TooltipProvider>
          <div className="flex h-screen overflow-hidden">
            <Sidebar />
            <MainContent>
              <CalendarConnectPrompt />
              <PersistentRecordingRuntime />
              {children}
            </MainContent>
          </div>
        </TooltipProvider>
      </SidebarProvider>
    </RecordingStateProvider>
  );
}
