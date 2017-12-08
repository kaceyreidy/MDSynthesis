"""User-level functions for manipulating Sims.

"""
import os

from datreant.core import discover as _discover
from datreant.core import Bundle
from datreant.core.names import TREANTDIR_NAME

from .treants import Sim
from .names import SIMDIR_NAME

#from .collections import Bundle

#def discover(dirpath='.', depth=None, treantdepth=None):
#    return Bundle(*_discover(dirpath=dirpath,
#                            depth=depth,
#                            treantdepth=treantdepth))

def _is_sim(treant):
    return os.path.exists(os.path.join(treant, TREANTDIR_NAME, SIMDIR_NAME))

def discover(dirpath='.', depth=None, treantdepth=None):
    treants = _discover(dirpath=dirpath,
                        depth=depth,
                        treantdepth=treantdepth)

    return Bundle([Sim(treant) for treant in treants if _is_sim(treant)])
