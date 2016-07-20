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

# consts for node state
PRIMARY = 'mongodb-replicaset'
#SECONDARY = 'mongodb-secondary'

# key where primary will be stored in consul
PRIMARY_KEY = get_environ('PRIMARY_KEY', 'mongodb-primary')

# how many time to retry connecting to mongo
# be aware that the health check may timeout before this is reached
MONGO_RETRY_TIMES=int(get_environ('MONGO_RETRY_TIMES', 10))

# ---------------------------------------------------------

#class ContainerPilot(object):
#    """
#    ContainerPilot config is where we rewrite ContainerPilot's own config
#    so that we can dynamically alter what service we advertise
#    """
#
#    def __init__(self):
#        # TODO: we should make sure we can support JSON-in-env-var
#        # the same as ContainerPilot itself
#        self.path = get_environ('CONTAINERPILOT', None).replace('file://', '')
#        with open(self.path, 'r') as f:
#            self.config = json.loads(f.read())
#
#    @debug
#    def update(self, state):
#        if state and self.config['services'][0]['name'] != state:
#            self.config['services'][0]['name'] = state
#            self.render()
#            return True
#
#    @debug
#    def render(self):
#        new_config = json.dumps(self.config)
#        with open(self.path, 'w') as f:
#            f.write(new_config)
#
#    def reload(self):
#        """ force ContainerPilot to reload its configuration """
#        log.info('Reloading ContainerPilot configuration.')
#        os.kill(1, signal.SIGHUP)

# ---------------------------------------------------------
# Top-level functions called by ContainerPilot or forked by this program

@debug
def pre_start():
    """
    MongoDB must be running in order to execute most of our setup behavior
    so we're just going to make sure the directory structures are in
    place and then let the first health check handler take it from there
    """
    # TODO is there anything that needs to be done before starting mongo?
    sys.exit(0)

@debug
def health():
    """
    Run a simple health check. Also acts as a check for whether the
    ContainerPilot configuration needs to be reloaded (if it's been
    changed externally).
    """
    # TODO periodic mongodumps to Manta

    hostname = socket.gethostname()
    ip = get_ip()
    local_mongo = MongoClient(ip, connect=False)

    #  - check that mongo is responsive
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

    # TODO reload ContainerPilot when we have more than one state
    #try:
    #    # ensure ContainerPilot knows the correct config
    #    cp = ContainerPilot()
    #    if cp.update(state):
    #        cp.reload()

    #except Exception as e:
    #    log.exception(e)
    #    sys.exit(1)

    return True

@debug
def on_change():
    '''
    called when there is a change in the list of IPs and ports for this backend
    '''
    hostname = socket.gethostname()
    ip = get_ip()
    local_mongo = MongoClient(ip, connect=False)

    try:
        is_mongo_primary = local_mongo.is_primary
    except Exception as e:
        log.error(e, 'unable to get primary status')
        sys.exit(1)

    if is_mongo_primary:
        mongo_update_replset_config(local_mongo, hostname)
    else:
        return True

# ---------------------------------------------------------

def is_mongo_up(local_mongo, timeout=MONGO_RETRY_TIMES):
    '''
    check to see if mongo is up yet, retying the given number of times
    '''
    
    while True:
        timeout -= 1
        if timeout <= 0:
            log.errors('unable to connect to mongodb after %i times' % MONGO_RETRY_TIMES)
            return False

        try:
            # check that mongo is up
            server_info = local_mongo.server_info()
            if not server_info['ok']:
                log.info('Mongo response not "ok" on %s; retrying...' % ip)
                sleep(1)
                continue
            break
        except (AutoReconnect, ServerSelectionTimeoutError) as e:
            log.info('Mongo not yet available on %s; retrying...' % ip)
            sleep(1)
            continue
        except (ConnectionFailure, NetworkTimeout, NotMasterError) as e:
            # TODO retry like AutoReconnect error above?
            log.info(e)
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
        mongos_in_consul = []
        for x in consul_services:
            host = consul_to_mongo_hostname(consul_services[x]['ID'])
            if host:
                mongos_in_consul.append(host)
        # empty list from consul means we have nothing to compare against
        if not len(mongos_in_consul):
            return
        # if the master node is not in the consul services list we need to
        # wait a little longer before configuring mongo
        if not hostname + ':27017' in mongos_in_consul:
            return

        max_id = 0
        changed = False
        new_members = []
        # loop over members in mongo replica config
        for member in repl_config['members']:
            if member['_id'] > max_id:
                # find the max `-id` for adding new members later
                max_id = member['_id']
            if member['host'] in mongos_in_consul:
                # this member is also in consul confg so keep it
                mongos_in_consul.remove(member['host'])
                new_members.append(member)
            else:
                # this member is not in consul, it will be removed from mongo config
                changed = True

        # add mongo servers that are listed in consul, but not yet in mongo config
        for host in mongos_in_consul:
            changed = True
            max_id += 1
            new_members.append({
                    '_id': max_id,
                    'host': host,
                })

        # TODO voting membership
        # https://docs.mongodb.com/manual/core/replica-set-architectures/#maximum-number-of-voting-members
        # ERROR manage.py Replica set configuration contains 10 voting members, but must be at least 1 and no more than 7
        # it should also be odd for tie breaking
        # also limit number of nodes to 50, since that is all a replica set can have

        # update mongo replica config with the new list if there are changes
        if changed:
            repl_config['members'] = new_members
            repl_config['version'] += 1
            local_mongo.admin.command('replSetReconfig', repl_config)
            log.info('updating replica config in mongo from consul info')
            return repl_config

    except Exception as e:
        log.exception(e)
        sys.exit(1)

def consul_to_mongo_hostname(name):
#    if name.startswith(SECONDARY + '-'):
#        prefix = SECONDARY + '-'
    if name.startswith(PRIMARY + '-'):
        prefix = PRIMARY + '-'
    else:
        return None

    name = name[len(prefix):] + ':27017'
    return name

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
