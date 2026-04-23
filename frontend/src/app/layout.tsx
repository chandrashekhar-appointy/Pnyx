import './globals.css'
import { Source_Sans_3 } from 'next/font/google'
import { Toaster } from 'sonner'
import "sonner/dist/styles.css"
import AnalyticsProvider from '@/components/AnalyticsProvider'
import { AuthProvider } from '@/components/AuthProvider'
import { Metadata } from 'next'
import LayoutClient from './LayoutClient'

const sourceSans3 = Source_Sans_3({
  subsets: ['latin'],
  weight: ['400', '500', '600', '700'],
  variable: '--font-source-sans-3',
})

export const metadata: Metadata = {
  title: 'Pnyx',
  description: 'AI-powered collaborative meeting assistant',
  icons: {
    icon: '/favicon.png',
    apple: '/favicon.png',
  },
}

export default function RootLayout({
  children,
}: {
  children: React.ReactNode
}) {
  return (
    <html lang="en">
      <body className={`${sourceSans3.variable} font-sans`}>
        <AuthProvider>
          <AnalyticsProvider>
            <LayoutClient>{children}</LayoutClient>
          </AnalyticsProvider>
        </AuthProvider>
        <Toaster position="bottom-center" richColors closeButton />
      </body>
    </html>
  )
}
