import type { Metadata } from 'next';

import { byCategory, PRODUCTS } from '@/content/products';
import { ProductCard } from '@/components/ProductCard';

export const metadata: Metadata = {
  title: 'Products',
  description: `All ${PRODUCTS.length} OpsGrids products — CRM, HRMS, delivery, support, marketing and finance on one account.`,
  alternates: { canonical: '/products' },
};

export default function ProductsPage() {
  return (
    <>
      <header className="phead">
        <p className="eyebrow">Products</p>
        <h1>Everything, and only what you turn on</h1>
        <p className="tagline">
          Each product works on its own. Activated together, they stop behaving like separate
          tools — one directory of people, one list of customers, one bill.
        </p>
      </header>

      {byCategory().map(({ category, products }) => (
        <section className="band" key={category}>
          <div className="catgroup">
            <p className="cat">{category}</p>
            <div className="grid">
              {products.map((p) => (
                <ProductCard key={p.slug} product={p} />
              ))}
            </div>
          </div>
        </section>
      ))}
    </>
  );
}
