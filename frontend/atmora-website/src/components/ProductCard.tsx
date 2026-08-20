import Link from 'next/link';

import type { Product } from '@/content/products';

const STATUS_LABEL = {
  available: 'Available',
  beta: 'Beta',
  soon: 'Soon',
} as const;

export function ProductCard({ product }: { product: Product }) {
  return (
    <Link className="card" href={`/products/${product.slug}`}>
      <span className="cardhead">
        <span className="name">{product.name}</span>
        {/* Status is never colour alone — the label carries the meaning too. */}
        <span className={`status ${product.status}`}>{STATUS_LABEL[product.status]}</span>
      </span>
      <span className="tag">{product.tagline}</span>
      <span className="more" aria-hidden="true">
        Read more →
      </span>
    </Link>
  );
}
