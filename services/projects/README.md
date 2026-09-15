# Projects service

Part of the OpsGrids platform. Owns the `projects` schema.

```bash
make run SERVICE=projects PORT=8110    # API
make worker SERVICE=projects           # event consumer
make relay SERVICE=projects            # outbox relay
```

## Owns

_Describe the tables and the domain rules this service is the authority for._

## Publishes

_Event types, with a line each on what they mean._

## Consumes

_Event types, and which projection each one maintains._
