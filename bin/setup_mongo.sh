#!/bin/bash

set -e


if [ -d "/data/db/_mongosetup" ]; then
  echo "/data/db/_mongosetup exists, mongo already setup, exiting"
  exit 0
fi

echo "Start MongoDB without access control and only for local connections"
mongod --fork --bind_ip 127.0.0.1 --logpath /dev/stdout

echo "Create the user administrator."
# The createUser will error if the user already exists.
mongo admin --eval "db.createUser({ user: '${MONGO_USER}', pwd: '${MONGO_PASSWORD}', roles: [ { role: 'dbAdminAnyDatabase', db: 'admin' }, { role: 'clusterAdmin', db: 'admin' } ] });"


echo "Shutdown the MongoDB service."
mongod --shutdown


echo "Creating keyFile for replication."
echo -e ${MONGO_KEY} > /etc/mongod.key
chmod 400 /etc/mongod.key

echo "Create directory to designate that setup is complete."
mkdir -p /data/db/_mongosetup
