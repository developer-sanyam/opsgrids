# Finance service

Part of the Atmora platform. Owns the `finance` schema.

```bash
make run SERVICE=finance PORT=8110    # API
make worker SERVICE=finance           # event consumer
make relay SERVICE=finance            # outbox relay
```

## Owns

_Describe the tables and the domain rules this service is the authority for._

## Publishes

_Event types, with a line each on what they mean._

## Consumes

_Event types, and which projection each one maintains._
