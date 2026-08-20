import Link from 'next/link';

import { byCategory } from '@/content/products';

/**
 * The nav is generated from the catalogue, so a new product appears in it automatically.
 * A hand-maintained menu is the thing that silently stops matching the product list.
 */
export function SiteHeader() {
  const groups = byCategory();

  return (
    <header className="site">
      <div className="bar">
        <Link className="logo" href="/">
          Atmora
        </Link>

        <nav aria-label="Primary">
          {/* Progressive disclosure without JavaScript: a details/summary menu works
              before hydration and with a keyboard. */}
          <details className="menu">
            <summary>Products</summary>
            <div className="mega">
              {groups.map(({ category, products }) => (
                <div key={category}>
                  <p className="cat">{category}</p>
                  <ul>
                    {products.map((p) => (
                      <li key={p.slug}>
                        <Link href={`/products/${p.slug}`}>
                          <strong>{p.name}</strong>
                          <span>{p.tagline}</span>
                        </Link>
                      </li>
                    ))}
                  </ul>
                </div>
              ))}
            </div>
          </details>

          <Link href="/pricing">Pricing</Link>
          <Link href="/why">Why one platform</Link>
        </nav>

        <div className="actions">
          <a className="btn ghost" href="https://app.atmora.com">
            Sign in
          </a>
          <a className="btn primary" href="https://app.atmora.com/signup">
            Get started
          </a>
        </div>
      </div>
    </header>
  );
}
