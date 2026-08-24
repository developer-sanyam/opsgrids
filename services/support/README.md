# Support service

Part of the Atmora platform. Owns the `support` schema.

```bash
make run SERVICE=support PORT=8110    # API
make worker SERVICE=support           # event consumer
make relay SERVICE=support            # outbox relay
```

## Owns

_Describe the tables and the domain rules this service is the authority for._

## Publishes

_Event types, with a line each on what they mean._

## Consumes

_Event types, and which projection each one maintains._
