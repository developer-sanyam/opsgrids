# Activity service

Part of the OpsGrids platform. Owns the `activity` schema.

```bash
make run SERVICE=activity PORT=8110    # API
make worker SERVICE=activity           # event consumer
make relay SERVICE=activity            # outbox relay
```

## Owns

_Describe the tables and the domain rules this service is the authority for._

## Publishes

_Event types, with a line each on what they mean._

## Consumes

_Event types, and which projection each one maintains._
