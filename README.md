# AutoPilot Pattern MongoDB

*A robust and highly-scalable implementation of MongoDB in Docker using the Autopilot Pattern*

## Architecture

A running cluster includes the following components:
- [ContainerPilot](https://www.joyent.com/containerpilot): included in our MongoDB containers to orchestrate bootstrap behavior and coordinate replica joining using keys and checks stored in Consul in the `health`, and `onChange` handlers
- [MongoDB](https://www.mongodb.com/community): we're using MongoDB 3.2 and setting up a [replica set](https://docs.mongodb.com/manual/replication/)
- [Consul](https://www.consul.io/): used to coordinate replication and failover

## Running the cluster

Starting a new cluster is easy once you have [your `_env` file set with the configuration details](#configuration)

- for Triton, just run `docker-compose up -d`
- for non-Triton, just run `docker-compose up -f local-compose.yml -d`

In a few moments you'll have a running MongoDB ready for a replica set. Both the master and replicas are described as a single `docker-compose` service. During startup, [ContainerPilot](http://containerpilot.io) will ask Consul if an existing master has been created. If not, the node will initialize as a new MongoDB replica set and all future nodes will be added to the replica set by the current master. All master election is handled by [MongoDB itself](https://docs.mongodb.com/manual/core/replica-set-elections/) and the result is cached in Consul.

**Run `docker-compose -f local-compose.yml scale mongodb=2` to add a replica (or more than one!)**. The replicas will automatically be added to the replica set on the master and will register themselves in Consul as replicas once they're ready.

### Configuration

Pass these variables via an `_env` file.

- `LOG_LEVEL`: control the amount of logging from ContainerPilot
- when the primary node is sent a `SIGTERM` it will [step down](https://docs.mongodb.com/manual/reference/command/replSetStepDown/) as primary; the following control those timeouts
  - `MONGO_SECONDARY_CATCHUP_PERIOD`: the number of seconds that the mongod will wait for an electable secondary to catch up to the primary
  - `MONGO_STEPDOWN_TIME`: the number of seconds to step down the primary, during which time the stepdown member is ineligible for becoming primary
  - `MONGO_ELECTION_TIMEOUT`: after the primary steps down, the amount a tries to check that a new primary has been elected before the node shuts down
- `CONSUL` (optional): when using `local-compose.yml`, this will default to `consul` (and thus use the DNS provided by Docker), but for deploying on Triton via `docker-compose.yml`, this should be set to [the CNS path of the `consul` service (`consul.svc.XXX...`)](https://docs.joyent.com/public-cloud/network/cns)

Not yet implemented:
- `MANTA_URL`: the full Manta endpoint URL. (ex. `https://us-east.manta.joyent.com`)
- `MANTA_USER`: the Manta account name.
- `MANTA_SUBUSER`: the Manta subuser account name, if any.
- `MANTA_ROLE`: the Manta role name, if any.
- `MANTA_KEY_ID`: the MD5-format ssh key id for the Manta account/subuser (ex. `1a:b8:30:2e:57:ce:59:1d:16:f6:19:97:f2:60:2b:3d`); the included `setup.sh` will encode this automatically
- `MANTA_PRIVATE_KEY`: the private ssh key for the Manta account/subuser; the included `setup.sh` will encode this automatically
- `MANTA_BUCKET`: the path on Manta where backups will be stored. (ex. `/myaccount/stor/triton-mysql`); the bucket must already exist and be writeable by the `MANTA_USER`/`MANTA_PRIVATE_KEY`

### Sponsors

Initial development of this project was sponsored by [Joyent](https://www.joyent.com).
