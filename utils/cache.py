import hashlib
import os
import pickle
from inspect import signature

from utils.constants import CACHE_DIR


def caching(function):
    if not os.path.exists(CACHE_DIR):
        os.makedirs(CACHE_DIR)
        print("creating cache dir")

    def wrapped_function(*args):

        def _obj_to_hex(o):
            if callable(o):
                return "&".join([o.__module__, o.__qualname__, str(signature(o)), str(o.__defaults__), str(o.__kwdefaults__)])
            elif type(o) == dict:
                return str(o)
            elif hasattr(o, "__iter__"):
                l = [str(type(o)), str(len(o)), str(dir(o))]
                if hasattr(o, "__len__") and len(o) > 0:
                    l.extend([str(list(o)[0]), str(list(o)[-1])])
                return "&".join(l)
            else:
                return str(o)

        hex_digest = hashlib.sha256(bytes(str([_obj_to_hex(arg) for arg in args]), "utf-8")).hexdigest()

        file_cache = os.path.join(CACHE_DIR, hex_digest)
        if not os.path.exists(file_cache):
            print("calculating result")
            result = function(*args)
            with open(file_cache, "wb") as file:
                pickle.dump(result, file)
        else:
            print("loading result from cache")
            with open(file_cache, "rb") as file:
                result = pickle.load(file)
        return result
    return wrapped_function
