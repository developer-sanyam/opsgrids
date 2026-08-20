import type { Metadata } from 'next';
import type { ReactNode } from 'react';

import { SiteHeader } from '@/components/SiteHeader';
import { SiteFooter } from '@/components/SiteFooter';
import '@/app/globals.css';

export const metadata: Metadata = {
  metadataBase: new URL('https://atmora.com'),
  title: {
    default: 'Atmora — one platform, every business tool',
    // Product pages set their own name; this keeps the brand without repeating it by hand.
    template: '%s · Atmora',
  },
  description:
    'CRM, HRMS, project delivery, support and finance on one account. Start with what you ' +
    'need, add the rest without migrating anything.',
  openGraph: {
    type: 'website',
    siteName: 'Atmora',
    locale: 'en_IN',
  },
  robots: { index: true, follow: true },
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en">
      <body>
        {/* Keyboard users should not tab through the whole nav to reach the page. */}
        <a className="skip" href="#main">
          Skip to content
        </a>
        <SiteHeader />
        <main id="main" tabIndex={-1}>
          {children}
        </main>
        <SiteFooter />
      </body>
    </html>
  );
}
