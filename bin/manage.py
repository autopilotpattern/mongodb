import logging
import os

import consul as pyconsul
import manta
from pymongo import MongoClient

consul = pyconsul.Consul(host=os.environ.get('CONSUL', 'consul'))
local_mongo = MongoClient()

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
    finally:
        # just swallow AttributeErrors for non-strings
        return val

# ---------------------------------------------------------

if __name__ == '__main__':

    manta_config = Manta()

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
