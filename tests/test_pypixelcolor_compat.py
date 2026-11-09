"""Guard against drift between our schema and pypixelcolor's real command signatures."""

import inspect

import pytest

pypixelcolor_commands = pytest.importorskip("pypixelcolor.commands")

from pypixelgui.commands import COMMANDS

ALIASES = {"send_image_b64": ("send_image_hex", {"data": "hex_string", "ext": "file_extension"})}


@pytest.mark.parametrize("name", sorted(COMMANDS))
def test_params_exist_upstream(name):
    target, renames = ALIASES.get(name, (name, {}))
    func = pypixelcolor_commands.COMMANDS[target]
    upstream = set(inspect.signature(func).parameters)
    ours = {renames.get(p, p) for p in COMMANDS[name].params}
    assert ours <= upstream, f"{name}: {ours - upstream} not accepted by pypixelcolor"
