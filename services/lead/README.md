# Lead service

Part of the Atmora platform. Owns the `lead` schema.

```bash
make run SERVICE=lead PORT=8110    # API
make worker SERVICE=lead           # event consumer
make relay SERVICE=lead            # outbox relay
```

## Owns

_Describe the tables and the domain rules this service is the authority for._

## Publishes

_Event types, with a line each on what they mean._

## Consumes

_Event types, and which projection each one maintains._
