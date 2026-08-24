# Project service

Part of the Atmora platform. Owns the `project` schema.

```bash
make run SERVICE=project PORT=8110    # API
make worker SERVICE=project           # event consumer
make relay SERVICE=project            # outbox relay
```

## Owns

_Describe the tables and the domain rules this service is the authority for._

## Publishes

_Event types, with a line each on what they mean._

## Consumes

_Event types, and which projection each one maintains._
