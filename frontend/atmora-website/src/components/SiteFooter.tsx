import Link from 'next/link';

import { byCategory } from '@/content/products';

export function SiteFooter() {
  return (
    <footer className="site">
      <div className="cols">
        {byCategory().map(({ category, products }) => (
          <div key={category}>
            <p className="cat">{category}</p>
            <ul>
              {products.map((p) => (
                <li key={p.slug}>
                  <Link href={`/products/${p.slug}`}>{p.name}</Link>
                </li>
              ))}
            </ul>
          </div>
        ))}
        <div>
          <p className="cat">Company</p>
          <ul>
            <li><Link href="/why">Why one platform</Link></li>
            <li><Link href="/pricing">Pricing</Link></li>
            <li><a href="https://app.atmora.com">Sign in</a></li>
          </ul>
        </div>
      </div>
      <p className="fine">© {new Date().getFullYear()} Atmora Tech. One account, every product.</p>
    </footer>
  );
}
