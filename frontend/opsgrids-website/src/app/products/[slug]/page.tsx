import type { Metadata } from 'next';
import Link from 'next/link';
import { notFound } from 'next/navigation';

import { bySlug, PRODUCTS, rupees } from '@/content/products';

/**
 * Every product page is this one file.
 *
 * Adding a product means adding an entry to the catalogue — no new route, no copied
 * template that drifts from its siblings after the third edit.
 */

export function generateStaticParams() {
  return PRODUCTS.map((p) => ({ slug: p.slug }));
}

// Next 16: params is a Promise and must be awaited.
export async function generateMetadata({
  params,
}: {
  params: Promise<{ slug: string }>;
}): Promise<Metadata> {
  const { slug } = await params;
  const product = bySlug(slug);
  if (!product) return {};
  return {
    title: product.name,
    description: product.tagline,
    alternates: { canonical: `/products/${product.slug}` },
    openGraph: { title: `${product.name} · OpsGrids`, description: product.tagline },
  };
}

const STATUS_LABEL = {
  available: 'Available now',
  beta: 'In beta',
  soon: 'Coming soon',
} as const;

export default async function ProductPage({ params }: { params: Promise<{ slug: string }> }) {
  const { slug } = await params;
  const product = bySlug(slug);
  if (!product) notFound();

  return (
    <article className="product">
      <header className="phead">
        <p className="eyebrow">
          {product.category}
          <span className={`status ${product.status}`}>{STATUS_LABEL[product.status]}</span>
        </p>
        <h1>{product.name}</h1>
        <p className="tagline">{product.tagline}</p>
        <p className="problem">{product.problem}</p>
      </header>

      <section className="band">
        <h2>What it does</h2>
        <div className="features">
          {product.features.map((f) => (
            <article key={f.title}>
              <h3>{f.title}</h3>
              <p>{f.body}</p>
            </article>
          ))}
        </div>
      </section>

      <section className="band">
        <h2>What changes because it is part of OpsGrids</h2>
        <p className="lede">
          This is the part a standalone tool cannot do, however good it is.
        </p>
        <ul className="connects">
          {product.connects.map((c) => (
            <li key={c}>{c}</li>
          ))}
        </ul>
      </section>

      <section className="band">
        <h2>Plans</h2>
        <p className="lede">
          Per user, per month, billed on the same invoice as everything else you have activated.
        </p>
        <div className="plans">
          {product.plans.map((plan) => (
            <article key={plan.name} className="plan">
              <h3>{plan.name}</h3>
              <p className="price">
                {plan.pricePerSeatMinor === null ? (
                  <span className="custom">Talk to us</span>
                ) : plan.pricePerSeatMinor === 0 ? (
                  <span className="custom">Free</span>
                ) : (
                  <>
                    {rupees(plan.pricePerSeatMinor)}
                    <span className="per"> /user /month</span>
                  </>
                )}
              </p>
              <p className="blurb">{plan.blurb}</p>
              <ul>
                {plan.includes.map((i) => (
                  <li key={i}>{i}</li>
                ))}
              </ul>
            </article>
          ))}
        </div>
      </section>

      <section className="band closing">
        <h2>Add {product.name} to an account you already have</h2>
        <p className="lede">
          If you are already on OpsGrids, this is an activation — your people, teams and customers
          are already here.
        </p>
        <div className="cta">
          <Link className="btn primary" href="/pricing">
            Pricing
          </Link>
          <Link className="btn" href="/products">
            All products
          </Link>
        </div>
      </section>
    </article>
  );
}
