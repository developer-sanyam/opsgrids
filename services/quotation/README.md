# Quotation service

Part of the OpsGrids platform. Owns the `quotation` schema.

```bash
make run SERVICE=quotation PORT=8110    # API
make worker SERVICE=quotation           # event consumer
make relay SERVICE=quotation            # outbox relay
```

## Owns

_Describe the tables and the domain rules this service is the authority for._

## Publishes

_Event types, with a line each on what they mean._

## Consumes

_Event types, and which projection each one maintains._
