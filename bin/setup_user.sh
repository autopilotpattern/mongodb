#!/bin/bash

echo "Start MongoDB without access control."
mongod --port 27017 &
while ! nc -z 127.0.0.1 27017; do sleep 1; done


echo "Create the user administrator."
mongo admin --eval "db.createUser({ user: '${MONGO_USER}', pwd: '${MONGO_PASSWORD}', roles: [ { role: 'dbAdminAnyDatabase', db: 'admin' } ] });"


echo "Shutdown the MongoDB service."
mongod --shutdown

echo "#!/bin/bash" > ./setup_user.sh
