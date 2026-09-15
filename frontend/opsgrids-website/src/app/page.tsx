import Link from 'next/link';

import { byCategory, PRODUCTS } from '@/content/products';
import { ProductCard } from '@/components/ProductCard';

export default function HomePage() {
  return (
    <>
      <section className="hero">
        <p className="eyebrow">One account · Every product</p>
        <h1>
          Start with one tool.
          <br />
          Add the rest without starting over.
        </h1>
        <p className="lede">
          Most companies buy a CRM, then an HR system, then something for projects — and end up
          maintaining three copies of their own employee list. OpsGrids is one organization, one
          user directory, one bill. You activate the products you need, when you need them.
        </p>
        <div className="cta">
          <Link className="btn primary" href="/products">
            See the products
          </Link>
          <Link className="btn" href="/pricing">
            Pricing
          </Link>
        </div>
      </section>

      <section className="band">
        <h2>What changes when it is one platform</h2>
        <div className="three">
          <article>
            <h3>Your people are entered once</h3>
            <p>
              Someone joins, and they exist everywhere they should — with the right access, in the
              right team, on the right products. Someone leaves, and that access is gone
              everywhere at once rather than once per tool, whenever somebody remembers.
            </p>
          </article>
          <article>
            <h3>Your customers are one record</h3>
            <p>
              The company sales sold to is the company delivery works for and finance invoices. Not
              three spellings of the same name in three databases, reconciled by hand at quarter
              end.
            </p>
          </article>
          <article>
            <h3>One bill, for what you actually use</h3>
            <p>
              Products are line items on a single invoice, not separate subscriptions with separate
              renewal dates and separate procurement conversations.
            </p>
          </article>
        </div>
      </section>

      <section className="band">
        <div className="bandhead">
          <h2>The products</h2>
          <p className="lede">
            Each one stands on its own. Together they stop being separate tools.
          </p>
        </div>
        {byCategory().map(({ category, products }) => (
          <div key={category} className="catgroup">
            <p className="cat">{category}</p>
            <div className="grid">
              {products.map((p) => (
                <ProductCard key={p.slug} product={p} />
              ))}
            </div>
          </div>
        ))}
      </section>

      <section className="band closing">
        <h2>Activate a second product on a Tuesday afternoon</h2>
        <p className="lede">
          No new account. No re-uploading your team. No migration project. It is a switch on an
          organization that already exists — and the data the new product needs is already there.
        </p>
        <div className="cta">
          <Link className="btn primary" href="/pricing">
            See what it costs
          </Link>
        </div>
        <p className="fine">{PRODUCTS.length} products available on one account.</p>
      </section>
    </>
  );
}
