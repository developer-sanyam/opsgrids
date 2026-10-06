# Product service

Part of the OpsGrids platform. Owns the `product` schema.

```bash
make run SERVICE=product PORT=8110    # API
make worker SERVICE=product           # event consumer
make relay SERVICE=product            # outbox relay
```

## Operating

**Launch status** (`products.status`: `available`, `beta`, `soon`) is deployment data — which
products this deployment is actually selling — and is flipped with the CLI, never with a
hand-run `UPDATE`:

```bash
docker compose exec product python -m app.launch <product_key> available|soon
```

Goes through `app.services.set_launch_status`: refuses a key the catalogue does not already
know, refuses any status outside `available`/`soon` (`beta` is a real column value, but nothing
today reconciles a product into or out of it by hand), and writes an audit row
(`product.launch_status_set`, `acting_as=operator:cli`) against the platform sentinel
organisation — there is no tenant a launch decision belongs to. See `app/launch.py`.

## The hand-activation gate (ADR-0037)

`POST /products/activations` turns a product on by hand for an organization **no subscription has
fed**. A product a subscription has ever fed, in any status (live, suspended, cancelled, or
dropped from a bundle by a downgrade), answers **409 `subscription_managed`** (`errors[0].code`)
with *"This product is managed by a subscription. Manage it on the Subscriptions page or contact
OpsGrids."*, and writes nothing: a hand activation would upsert the very source row the
subscription's fold writes, undoing a cancellation or a suspension that never emits again.
`DELETE /products/activations/{key}` is refused the same way while the product's own subscription
is serving it.

The test is one property, `ActivationSource.is_subscription_managed`: `source_subscription_id IS
NOT NULL OR state_occurred_at IS NOT NULL`. The fold stamps both on every row it touches and a
hand activation never does, so a hand-only or fresh organization is never refused. It is read over
every source of the activation after the activation lock is taken, so it cannot race a fold.

Organizations that hand-activated **before** this gate, after a cancel or a suspend, are found
with the read-only audit queries in the edge RUNBOOK ("Stop events and the hand-activation gate").
Nothing repairs them automatically. `tools/demo/activate_product.py` bypasses the API (it is the
demo workspace's script); it now warns on stderr, and says `subscription_managed: true` in its
JSON, when it activates such a product.

## Owns

_Describe the tables and the domain rules this service is the authority for._

## Publishes

_Event types, with a line each on what they mean._

## Consumes

_Event types, and which projection each one maintains._
