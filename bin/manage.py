import fcntl
import inspect
import json
import logging
import os
import socket
import signal
import struct
import sys
import time

from functools import wraps
from urllib import quote_plus

import consul as pyconsul
#import manta
from pymongo import MongoClient
from pymongo.errors import *

consul = pyconsul.Consul(host=os.environ.get('CONSUL', 'consul'))
logging.basicConfig(format='%(asctime)s %(levelname)s %(name)s %(message)s',
                    stream=sys.stdout,
                    level=logging.getLevelName(
                        os.environ.get('LOG_LEVEL', 'INFO')))

log = logging.getLogger('manage.py')

def debug(fn):
    """
    Function/method decorator to trace calls via debug logging.
    Is a pass-thru if we're not at LOG_LEVEL=DEBUG. Normally this
    would have a lot of perf impact but this application doesn't
    have significant throughput.
    """
    @wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            # because we have concurrent processes running we want
            # to tag each stack with an identifier for that process
            arg = "[{}]".format(sys.argv[1])
        except IndexError:
            arg = "[pre_start]"
        name = '{}{}{}'.format(arg, (len(inspect.stack()) * " "), fn.__name__)
        log.debug('%s' % name)
        out = apply(fn, args, kwargs)
        log.debug('%s: %s', name, out)
        return out
    return wrapper

def get_environ(key, default):
    """
    Gets an environment variable and trims away comments and whitespace.
    """
    val = os.environ.get(key, default)
    try:
        val = val.split('#')[0]
        val = val.strip()
        val = os.path.expandvars(val)
    finally:
        # just swallow AttributeErrors for non-strings
        return val

# ---------------------------------------------------------

SESSION_CACHE_FILE = get_environ('SESSION_CACHE_FILE', '/tmp/mongodb-session')
SESSION_NAME = get_environ('SESSION_NAME', 'mongodb-replica-set-lock')
SESSION_TTL = int(get_environ('SESSION_TTL', 60))

MONGO_USER = get_environ('MONGO_USER', 'admin')
MONGO_PASSWORD = get_environ('MONGO_PASSWORD', 'admin')

# consts for node state
PRIMARY = 'mongodb-replicaset'
#SECONDARY = 'mongodb-secondary'

# key where primary will be stored in consul
PRIMARY_KEY = get_environ('PRIMARY_KEY', 'mongodb-primary')

# how many time to retry connecting to mongo
# be aware that the health check may timeout before this is reached
MONGO_RETRY_TIMES=int(get_environ('MONGO_RETRY_TIMES', 10))

# timeouts when the primary node gets a SIGTERM and needs to step down as primary
MONGO_STEPDOWN_TIME=int(get_environ('MONGO_STEPDOWN_TIME', 60))
MONGO_SECONDARY_CATCHUP_PERIOD=int(get_environ('MONGO_SECONDARY_CATCHUP_PERIOD', 8))
MONGO_ELECTION_TIMEOUT=int(get_environ('MONGO_ELECTION_TIMEOUT', 30))

# ---------------------------------------------------------
# Top-level functions called by ContainerPilot or forked by this program

@debug
def pre_start():
    """
    MongoDB must be running in order to execute most of our setup behavior
    """
    # TODO is there anything that needs to be done before starting mongo?
    sys.exit(0)

@debug
def pre_stop():
    """
    If we are the primary in the MongoDB replica set, we need to step down
    because we are about to be shut down
    """

    local_mongo = get_local_mongo()

    # since we are shutting down, it is ok to stop if mongo is already non-responsive
    if not is_mongo_up(local_mongo):
        return True

    try:
        repl_status = local_mongo.admin.command('replSetGetStatus')
        is_mongo_primary = repl_status['myState'] == 1
        # ref https://docs.mongodb.com/manual/reference/replica-states/
    except Exception as e:
        log.error(e, 'unable to get primary status while shuting down')
        return True

    if is_mongo_primary:
        # the primary will wait up to X seconds for a secondary member
        # to catch up and sets itself as ineligible to be primary again for 60 seconds
        # https://docs.mongodb.com/manual/reference/command/replSetStepDown/
        # this is set to 8 so that we timeout before `docker stop` would send a sigkill
        try:
            local_mongo.admin.command('replSetStepDown', MONGO_STEPDOWN_TIME, secondaryCatchUpPeriodSecs=MONGO_SECONDARY_CATCHUP_PERIOD)
        except ConnectionFailure:
            # this means mongo closed all connections and this node is no longer primary
            wait_for_election = True
        except ExecutionTimeout as e:
            # stepdown fails, ie no secondary that is caught up
            log.debug(e)
            try:
                # force
                local_mongo.admin.command('replSetStepDown', MONGO_STEPDOWN_TIME, force=True)
            except ConnectionFailure:
                # this means mongo closed all connections and this node is no longer primary
                wait_for_election = True

        if wait_for_election:
            timeout = 0
            while True:
                if timeout >= MONGO_ELECTION_TIMEOUT:
                    log.error('did not see mongodb election results of new primary after %i times' % timeout)
                    return False
                timeout += 1
                # use a replica client so that we get "primary" data
                mongo_client = MongoClient(get_local_mongo_uri(), connect=False, replicaset=repl_status['set'], serverSelectionTimeoutMS=500)
                # is_mongo_up will sleep on failure, so we don't need a "time.sleep(1)"
                if is_mongo_up(mongo_client, 1):
                    primary = mongo_client.primary
                    if primary != None:
                        log.debug('primary elected: {0!s}'.format(primary))
                        return True

    return True

@debug
def health():
    """
    Run a simple health check. Also acts as a check for whether the
    ContainerPilot configuration needs to be reloaded (if it's been
    changed externally).
    """
    # TODO periodic mongodumps to Manta

    hostname = socket.gethostname()
    local_mongo = get_local_mongo()

    # check that mongo is responsive
    if not is_mongo_up(local_mongo):
        return False

    # make sure this node has a valid consul session to work with
    get_session()

    try:
        repl_status = local_mongo.admin.command('replSetGetStatus')
        # TODO handle non-exceptional states
#        if repl_status['myState'] == 1:
#            # ref https://docs.mongodb.com/manual/reference/replica-states/
#            state = PRIMARY
#            # mongo_update_replset_config is not required in the health check
#            # but may speed up adding of new members
#            # dropping to keep consul traffic minimal
#            #mongo_update_replset_config(local_mongo, hostname)
#        elif repl_status['myState'] in (2, 3, 5):
#            # mongo states of: SECONDARY or RECOVERING or STARTUP2
#            state = SECONDARY
    except OperationFailure as e:
        # happens when replica set is not initialized
        log.debug(e)
        consul_primary = get_primary_node_from_consul()
        if not consul_primary:
            # this should only happen at the beginning when there is no replica set
            # so the first node to get the lock in consul will initialize the set
            # by setting self as primary in consul and then rs.init()
            mark_as_primary(hostname)
            #state = PRIMARY
            local_mongo.admin.command('replSetInitiate')
        else:
            # this happens when the primary node is still initializing
            # wait for it to finish so that it can add this node to the replica set
            # while waiting, we are a "healthy" node, since mongo is responsive
            # TODO maybe make this a second state of "recovering/initializing" node?
            return True

    return True

@debug
def on_change():
    '''
    called when there is a change in the list of IPs and ports for this backend
    '''
    hostname = socket.gethostname()
    ip = get_ip()
    local_mongo = get_local_mongo()

    try:
        repl_status = local_mongo.admin.command('replSetGetStatus')
        is_mongo_primary = repl_status['myState'] == 1
        # ref https://docs.mongodb.com/manual/reference/replica-states/
    except Exception as e:
        log.error(e, 'unable to get primary status')
        return False

    if is_mongo_primary:
        return mongo_update_replset_config(local_mongo, ip)
    else:
        return True

# ---------------------------------------------------------

def is_mongo_up(local_mongo, max_timeout=MONGO_RETRY_TIMES):
    '''
    check to see if mongo is up yet, retying the given number of times
    '''
    timeout = 0
    ip = get_ip()
    while True:
        if timeout >= max_timeout:
            if timeout != 1:
                log.error('unable to connect to mongodb or replica set after %i times' % timeout)
            return False
        timeout += 1

        try:
            # check that mongo is up
            server_info = local_mongo.server_info()
            if not server_info['ok']:
                log.info('Mongo response not "ok" on %s; retrying...' % ip)
                time.sleep(1)
                continue
            break
        except (AutoReconnect, ServerSelectionTimeoutError) as e:
            log.info('Mongo or specified replica set not yet available on %s; retrying...' % ip)
            time.sleep(1)
            continue
        except (ConnectionFailure, NetworkTimeout, NotMasterError) as e:
            # TODO retry like AutoReconnect error above?
            log.info(e)
            time.sleep(1)
            return False
        except Exception as e:
            # just bail on unexpected exceptions when trying to connect
            log.error(e)
            return False
    return True

@debug
def mongo_update_replset_config(local_mongo, hostname):
    '''
    called from the primary node to update the replica config in mongo
    using the current set of healthy mongo containers listed in consul
    '''
    try:
        # get current replica config from mongodb
        repl_config = local_mongo.admin.command('replSetGetConfig')
        if not repl_config['ok']:
            raise Exception('could not get replica config: %s' % repl_config['errmsg'])
        repl_config = repl_config['config']

        # TODO use consul.agent.health() instead?
        # get list of mongo servers from consul
        consul_services = consul.agent.services()

        # translate the name stored by consul to be the "host" name stored
        # in mongo config, skipping any non-mongo services
        mongos_in_consul = [consul_to_mongo_hostname(consul_services[svc]) for svc in consul_services]
        mongos_in_consul = [svc for svc in mongos_in_consul if svc]
        # empty list from consul means we have nothing to compare against
        if not mongos_in_consul:
            return
        # if the master node is not in the consul services list we need to
        # wait a little longer before configuring mongo
        if not hostname + ':27017' in mongos_in_consul:
            return

        members = repl_config['members']
        existing_hosts, ids = zip(*[(member['host'], member['_id']) for member in members])
        ids = list(ids)
        existing = set(existing_hosts)
        current = set(mongos_in_consul)

        new_mongos = current - existing
        stale_mongos = existing - current

        if not new_mongos and not stale_mongos:
            return # no change

        # don't keep mongo replica members that are not listed in consul
        for member in members:
            if member['host'] in stale_mongos:
                members.remove(member)
        for new_mongo in new_mongos:
            new_id = max(ids) + 1
            ids.append(new_id)

            members.append({'_id': new_id, 'host': new_mongo})

        # TODO voting membership
        # https://docs.mongodb.com/manual/core/replica-set-architectures/#maximum-number-of-voting-members
        # ERROR manage.py Replica set configuration contains 10 voting members, but must be at least 1 and no more than 7
        # it should also be odd for tie breaking
        # also limit number of nodes to 50, since that is all a replica set can have

        repl_config['members'] = members
        repl_config['version'] += 1
        local_mongo.admin.command('replSetReconfig', repl_config)

        log.info('updating replica config in mongo from consul info')
        return repl_config

    except Exception as e:
        log.exception(e)
        sys.exit(1)


def get_local_mongo_uri():
    ip = get_ip()
    uri = "mongodb://%s:%s@%s" % (quote_plus(MONGO_USER), quote_plus(MONGO_PASSWORD), ip)

    return uri

def get_local_mongo():
    local_mongo = MongoClient(get_local_mongo_uri(), connect=False)

    return local_mongo


def consul_to_mongo_hostname(service):
#    if name.startswith(SECONDARY + '-'):
#        prefix = SECONDARY + '-'
    if service['ID'].startswith(PRIMARY + '-'):
        return service['Address'] + ':' + str(service['Port'])
    else:
        return None

# ---------------------------------------------------------

@debug
def get_primary_node_from_consul(timeout=10):
    while timeout > 0:
        try:
            result = consul.kv.get(PRIMARY_KEY)
            if result[1]:
                if result[1].get('Session', False):
                    return result[1]['Value']
            # either there is no primary or the session has expired
            return None
        except Exception as e:
            timeout = timeout - 1
            time.sleep(1)
    raise e

@debug
def mark_as_primary(hostname):
    """ Write flag to Consul to mark this node as primary """
    session_id = get_session()
    if not mark_with_session(PRIMARY_KEY, hostname, session_id):
        log.error('Tried to mark node primary but primary exists, '
                  'exiting for retry on next check.')
        sys.exit(1)

@debug
def mark_with_session(key, val, session_id, timeout=10):
    while timeout > 0:
        try:
            return consul.kv.put(key, val, acquire=session_id)
        except Exception as e:
            log.debug(e)
            timeout = timeout - 1
            time.sleep(1)
    raise e

def get_session(no_cache=False):
    """
    Gets a Consul session ID from the on-disk cache or calls into
    `create_session` to generate and cache a new one.
    Also, renews the session TTL of on-disk key, to ensure it is valid
    in Consul
    """
    if no_cache:
        return create_session()

    try:
        with open(SESSION_CACHE_FILE, 'r') as f:
            session_id = f.read()

        # ensure the session_id is valid and refresh it
        consul.session.renew(session_id)
    except (IOError, pyconsul.base.NotFound) as e:
        # this means we have don't have a key locally, or
        # it is expired, so get a new one
        session_id = create_session()

    return session_id

def create_session(ttl=None):
    """
    We can't rely on storing Consul session IDs in memory because
    `health` and `onChange` handler calls happen in a subsequent
    process. Here we creates a session on Consul and cache the
    session ID to disk. Returns the session ID.
    """
    session_id = consul.session.create(name=SESSION_NAME,
                                       behavior='release',
                                       ttl=ttl)
    with open(SESSION_CACHE_FILE, 'w') as f:
        f.write(session_id)
    return session_id

# ---------------------------------------------------------
# utility functions

def get_ip(iface='eth0'):
    """
    Use Linux SIOCGIFADDR ioctl to get the IP for the interface.
    ref http://code.activestate.com/recipes/439094-get-the-ip-address-associated-with-a-network-inter/
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    return socket.inet_ntoa(fcntl.ioctl(
        sock.fileno(),
        0x8915, # SIOCGIFADDR
        struct.pack('256s', iface[:15])
    )[20:24])

# ---------------------------------------------------------

if __name__ == '__main__':

#    manta_config = Manta()

    if len(sys.argv) > 1:
        func = sys.argv[1]
        try:
            if not locals()[func]():
                log.info('Function failed %s' % func)
                sys.exit(1)
        except KeyError:
            log.error('Invalid command %s', func)
            sys.exit(1)
    else:
        # default behavior will be to start mysqld, running the
        # initialization if required
        pre_start()
