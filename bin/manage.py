import logging
import os
import sys
import inspect
import json
import logging
import socket

from functools import wraps

import consul as pyconsul
#import manta
from pymongo import MongoClient

consul = pyconsul.Consul(host=os.environ.get('CONSUL', 'consul'))

log = logging.getLogger('manage.py')

# consts for node state
PRIMARY = 'mongodb-primary'
SECONDARY = 'mongodb-secondary'
REPLICA = 'mongodb'

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
    finally:
        # just swallow AttributeErrors for non-strings
        return val

# ---------------------------------------------------------

class MongoDBNode(object):
    """ MongoDBNode represents this instance of a MongoDB container. """

    def __init__(self, name='', ip=''):
        self.hostname = socket.gethostname()
        self.name = name if name else get_name()
        self.ip = ip if ip else get_ip()
        self.state = PRIMARY
        self.conn = None

    def get_state(self):
        # TODO check mongo if I am primary
        return self.state

# ---------------------------------------------------------

class ContainerPilot(object):
    """
    ContainerPilot config is where we rewrite ContainerPilot's own config
    so that we can dynamically alter what service we advertise
    """

    def __init__(self, node):
        # TODO: we should make sure we can support JSON-in-env-var
        # the same as ContainerPilot itself
        self.node = node
        self.path = get_environ('CONTAINERPILOT', None).replace('file://', '')
        with open(self.path, 'r') as f:
            self.config = json.loads(f.read())

    @debug
    def update(self):
        state = self.node.get_state()
        if state and self.config['services'][0]['name'] != state:
            self.config['services'][0]['name'] = state
            self.render()
            return True

    @debug
    def render(self):
        new_config = json.dumps(self.config)
        with open(self.path, 'w') as f:
            f.write(new_config)

    def reload(self):
        """ force ContainerPilot to reload its configuration """
        log.info('Reloading ContainerPilot configuration.')
        os.kill(1, signal.SIGHUP)

# ---------------------------------------------------------
# Top-level functions called by ContainerPilot or forked by this program

@debug
def pre_start():
    """
    MongoDB must be running in order to execute most of our setup behavior
    so we're just going to make sure the directory structures are in
    place and then let the first health check handler take it from there
    """
    #if not os.path.isdir(os.path.join('/data')):
    #    last_backup = has_snapshot()
    #    if last_backup:
    #        get_snapshot(last_backup)
    #        restore_from_snapshot(last_backup)
    #    else:
    #        if not initialize_db():
    #            log.info('Skipping database setup.')
    sys.exit(0)

@debug
def health():
    """
    Run a simple health check. Also acts as a check for whether the
    ContainerPilot configuration needs to be reloaded (if it's been
    changed externally).
    """
    
    # TODO
    #  - check for primary or replset in consul
    #  - no others, try setting self as primary and rs.init()
    #  - otherwise join the replset
    #  - just leave it alone if I am already in rs.status()
    #  - if I am primary in rs.status(), tell consul
    #  - periodic mongodumps to Manta
    
    try:
        node = MongoDBNode()
        cp = ContainerPilot(node)
        if cp.update():
            cp.reload()
            return

    except Exception as ex:
        log.exception(ex)
        sys.exit(1)

@debug
def on_change():
    # TODO when is this called?
    time.sleep(1) # avoid hammering Consul

# ---------------------------------------------------------



def get_from_consul(key):
    """
    Return the Value field for a given Consul key.
    Handles None results safely but lets all other exceptions
    just bubble up.
    """
    result = consul.kv.get(key)
    if result[1]:
        return result[1]['Value']
    return None

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

def get_name():
    return 'mysql-{}'.format(socket.gethostname())

# ---------------------------------------------------------

if __name__ == '__main__':

#    manta_config = Manta()

    if len(sys.argv) > 1:
        try:
            locals()[sys.argv[1]]()
        except KeyError:
            log.error('Invalid command %s', sys.argv[1])
            sys.exit(1)
    else:
        # default behavior will be to start mysqld, running the
        # initialization if required
        pre_start()
