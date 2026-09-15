import type { Metadata } from 'next';
import Link from 'next/link';

import { PRODUCTS, rupees } from '@/content/products';

export const metadata: Metadata = {
  title: 'Pricing',
  description:
    'Per user, per product, on one invoice. Activate what you need and add the rest later ' +
    'without a migration.',
  alternates: { canonical: '/pricing' },
};

/** The cheapest paid tier of each product — what "adding one more" actually costs. */
const cheapestPaid = (p: (typeof PRODUCTS)[number]) =>
  p.plans.filter((t) => t.pricePerSeatMinor !== null && t.pricePerSeatMinor > 0)[0] ?? null;

export default function PricingPage() {
  const example = ['crm', 'hrms', 'pulseboard']
    .map((slug) => PRODUCTS.find((p) => p.slug === slug))
    .filter((p): p is (typeof PRODUCTS)[number] => Boolean(p));

  const perSeat = example.reduce((sum, p) => sum + (cheapestPaid(p)?.pricePerSeatMinor ?? 0), 0);
  const seats = 25;

  return (
    <>
      <header className="phead">
        <p className="eyebrow">Pricing</p>
        <h1>Pay for the products you turn on</h1>
        <p className="tagline">
          Per user, per month. Every active product is a line on one invoice — not a separate
          subscription with its own renewal date and its own procurement conversation.
        </p>
      </header>

      <section className="band">
        <h2>By product</h2>
        <div className="tw">
          <table>
            <thead>
              <tr>
                <th scope="col">Product</th>
                <th scope="col">Starts at</th>
                <th scope="col">Free tier</th>
                <th scope="col">Top tier</th>
              </tr>
            </thead>
            <tbody>
              {PRODUCTS.map((p) => {
                const paid = cheapestPaid(p);
                const free = p.plans.some((t) => t.pricePerSeatMinor === 0);
                const top = p.plans[p.plans.length - 1];
                return (
                  <tr key={p.slug}>
                    <th scope="row">
                      <Link href={`/products/${p.slug}`}>{p.name}</Link>
                      <span className="sub">{p.tagline}</span>
                    </th>
                    <td className="num">
                      {paid ? `${rupees(paid.pricePerSeatMinor as number)} /user` : '—'}
                    </td>
                    <td>{free ? 'Yes' : 'No'}</td>
                    <td>
                      {top?.pricePerSeatMinor === null ? 'Custom' : (top?.name ?? '—')}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </section>

      <section className="band">
        <h2>What a real bill looks like</h2>
        <p className="lede">
          A {seats}-person company running three products. One invoice, one payment, one renewal
          date.
        </p>
        <div className="tw">
          <table>
            <thead>
              <tr>
                <th scope="col">Line</th>
                <th scope="col">Plan</th>
                <th scope="col">Per user</th>
                <th scope="col">Users</th>
                <th scope="col">Monthly</th>
              </tr>
            </thead>
            <tbody>
              {example.map((p) => {
                const plan = cheapestPaid(p);
                const unit = plan?.pricePerSeatMinor ?? 0;
                return (
                  <tr key={p.slug}>
                    <th scope="row">{p.name}</th>
                    <td>{plan?.name ?? '—'}</td>
                    <td className="num">{rupees(unit)}</td>
                    <td className="num">{seats}</td>
                    <td className="num">{rupees(unit * seats)}</td>
                  </tr>
                );
              })}
            </tbody>
            <tfoot>
              <tr>
                <th scope="row" colSpan={4}>
                  Total, one invoice
                </th>
                <td className="num total">{rupees(perSeat * seats)}</td>
              </tr>
            </tfoot>
          </table>
        </div>
        <p className="fine">
          Indian customers are charged GST; exports are zero-rated under LUT. Annual billing is
          available on every paid plan.
        </p>
      </section>

      <section className="band closing">
        <h2>Adding the fourth product is a switch, not a project</h2>
        <p className="lede">
          Your people, teams, customers and permissions are already on the platform. Activating
          another product does not create an account, ask for a spreadsheet, or migrate anything.
        </p>
        <div className="cta">
          <a className="btn primary" href="https://app.opsgrids.com/signup">
            Get started
          </a>
          <Link className="btn" href="/products">
            See the products
          </Link>
        </div>
      </section>
    </>
  );
}
