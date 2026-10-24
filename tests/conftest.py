import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from lostcode import detect  # noqa: E402

FIX = os.path.join(os.path.dirname(__file__), "fixtures")


def load_tree(name):
    root = os.path.join(FIX, name)
    files = []
    for dp, _, fns in os.walk(root):
        for f in fns:
            files.append(os.path.relpath(os.path.join(dp, f), root).replace(os.sep, "/"))

    def reader(p):
        with open(os.path.join(root, p)) as fh:
            return fh.read()
    return detect.Tree(files, reader)


@pytest.fixture
def tree():
    return load_tree
