#!/bin/bash

echo "Start MongoDB without access control."
mongod --port 27017 &
while ! nc -z 127.0.0.1 27017; do sleep 1; done


echo "Create the user administrator."
mongo admin --eval "db.createUser({ user: '${MONGO_USER}', pwd: '${MONGO_PASSWORD}', roles: [ { role: 'dbAdminAnyDatabase', db: 'admin' }, { role: 'clusterAdmin', db: 'admin' } ] });"


echo "Shutdown the MongoDB service."
mongod --shutdown


echo "Creating keyFile for replication."
echo -e ${MONGO_KEY} > /etc/mongod.key
chmod 400 /etc/mongod.key

echo "Overwrite setup_mongo.sh so that this is a one-time setup"
echo "#!/bin/bash" > ./setup_user.sh
