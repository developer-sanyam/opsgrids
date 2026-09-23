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

## Owns

_Describe the tables and the domain rules this service is the authority for._

## Publishes

_Event types, with a line each on what they mean._

## Consumes

_Event types, and which projection each one maintains._
