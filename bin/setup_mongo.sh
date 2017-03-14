#!/bin/bash

set -e

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

echo "Overwrite setup_mongo.sh so that this is a one-time setup"
echo "#!/bin/bash" > ./setup_user.sh
